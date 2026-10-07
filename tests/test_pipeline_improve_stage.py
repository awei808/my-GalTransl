# -*- coding: utf-8 -*-
"""流水线阶段 8（run_improve_stage）行为测试。

覆盖：全部文件统一执行 afterTranslation 链、断点标记命中跳过（中断重启不重复
执行）、有重译不跳过、指纹变更重跑、单项失败不写标记（下次重启重跑该文件）、
fix 对象条目透传、取消上抛。

配置用真实 CProjectConfig + 临时项目（与 test_standalone_backend_dispatch 同口径，
避免手搓桩与生产路径口径漂移）。
"""
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.Frontend.llm_postprocess import _aftertrans_fingerprint, run_improve_stage
from GalTransl.Service import JobCancelledError

BASE_CONFIG = """backendSpecific:
  OpenAI-Compatible:
    tokens:
      - token: sk-test
        endpoint: http://127.0.0.1:9999
        modelName: deepseek-chat

plugin:
  filePlugin: file_galtransl_json

common:
  gpt.numPerRequestTranslate: 10
  workersPerProject: 1
  language: "ja2zh-cn"
  splitFile: "no"
  gpt.afterTranslation: []

problemAnalyze:
  problemList:
    - 词频过高
    - 残留日文

proxy:
  enableProxy: false
"""


def _build_mini_project(root: str) -> str:
    """在 root 下构造含 config.inc.yaml 与单个待译文件的临时项目。"""
    proj = os.path.join(root, "mini_proj")
    os.makedirs(os.path.join(proj, "gt_input"), exist_ok=True)
    with open(os.path.join(proj, "config.inc.yaml"), "w", encoding="utf-8") as f:
        f.write(BASE_CONFIG)
    return proj


def _make_chunk(file_path: str, had_retranslated: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        trans_list=[],
        json_list=[],
        file_path=file_path,
        chunk_index=0,
        total_chunks=1,
        had_retranslated=had_retranslated,
    )


class RunImproveStageTests(unittest.IsolatedAsyncioTestCase):
    """run_improve_stage：统一执行 + 断点标记跳过语义。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="improve_stage_")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _cfg(self) -> CProjectConfig:
        # 每个用例独立子目录：断点标记落盘在项目缓存内，共用目录会跨用例残留
        proj_root = tempfile.mkdtemp(dir=self._tmp)
        proj = _build_mini_project(proj_root)
        cfg = CProjectConfig(proj, "config.inc.yaml")
        cfg.non_interactive = True
        return cfg

    def _marker_path(self, cfg: CProjectConfig, name: str = "scene_01.txt.json") -> str:
        return os.path.join(cfg.getCachePath(), "aftertrans_cache", name + ".json")

    def _chunks_map(self, cfg: CProjectConfig, had_retranslated: bool = False) -> dict:
        fp = os.path.join(cfg.getInputPath(), "scene_01.txt.json")
        return {fp: [_make_chunk(fp, had_retranslated)]}

    async def _run(self, cfg: CProjectConfig, order: list, run_after_trans: AsyncMock):
        postprocess_mock = AsyncMock()
        with patch(
            "GalTransl.Frontend.llm_postprocess._run_after_trans_single_file",
            new=run_after_trans,
        ), patch(
            "GalTransl.Frontend.llm_postprocess.postprocess_results",
            new=postprocess_mock,
        ), patch(
            "GalTransl.Frontend.llm_postprocess.ensure_model_available_if_needed",
            new=AsyncMock(return_value=None),
        ):
            await run_improve_stage(cfg, self._chunks_map(cfg), order)
        return postprocess_mock

    async def test_runs_chain_finalize_and_writes_marker(self) -> None:
        cfg = self._cfg()
        order = ["improve"]
        run_after_trans = AsyncMock()
        postprocess_mock = await self._run(cfg, order, run_after_trans)

        run_after_trans.assert_awaited_once()
        self.assertEqual(run_after_trans.await_args.args[0], "improve")
        # 处理过的文件必须重跑收尾（备选译文/标记落缓存、swap 模式刷新输出）
        postprocess_mock.assert_awaited_once()
        marker = json.load(open(self._marker_path(cfg), encoding="utf-8"))
        self.assertEqual(marker["fingerprint"], _aftertrans_fingerprint(order))

    async def test_skips_when_marker_hits_and_no_retrans(self) -> None:
        cfg = self._cfg()
        order = ["improve"]
        marker_path = self._marker_path(cfg)
        os.makedirs(os.path.dirname(marker_path), exist_ok=True)
        with open(marker_path, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": _aftertrans_fingerprint(order)}, f)

        run_after_trans = AsyncMock()
        postprocess_mock = await self._run(cfg, order, run_after_trans)

        run_after_trans.assert_not_awaited()
        postprocess_mock.assert_not_awaited()

    async def test_reruns_when_chunk_retranslated(self) -> None:
        cfg = self._cfg()
        order = ["improve"]
        marker_path = self._marker_path(cfg)
        os.makedirs(os.path.dirname(marker_path), exist_ok=True)
        with open(marker_path, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": _aftertrans_fingerprint(order)}, f)

        run_after_trans = AsyncMock()
        postprocess_mock = AsyncMock()
        with patch(
            "GalTransl.Frontend.llm_postprocess._run_after_trans_single_file",
            new=run_after_trans,
        ), patch(
            "GalTransl.Frontend.llm_postprocess.postprocess_results",
            new=postprocess_mock,
        ), patch(
            "GalTransl.Frontend.llm_postprocess.ensure_model_available_if_needed",
            new=AsyncMock(return_value=None),
        ):
            await run_improve_stage(
                cfg, self._chunks_map(cfg, had_retranslated=True), order
            )

        run_after_trans.assert_awaited_once()
        postprocess_mock.assert_awaited_once()

    async def test_reruns_on_fingerprint_change(self) -> None:
        cfg = self._cfg()
        order = ["improve"]
        marker_path = self._marker_path(cfg)
        os.makedirs(os.path.dirname(marker_path), exist_ok=True)
        with open(marker_path, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": "stale-fingerprint"}, f)

        run_after_trans = AsyncMock()
        postprocess_mock = await self._run(cfg, order, run_after_trans)

        run_after_trans.assert_awaited_once()
        marker = json.load(open(marker_path, encoding="utf-8"))
        self.assertEqual(marker["fingerprint"], _aftertrans_fingerprint(order))

    async def test_item_failure_skips_marker_but_finalizes(self) -> None:
        cfg = self._cfg()
        order = ["improve"]
        run_after_trans = AsyncMock(side_effect=RuntimeError("模拟失败"))
        postprocess_mock = await self._run(cfg, order, run_after_trans)

        # 单项失败隔离：收尾仍执行（保存已成功项），但断点标记不落盘
        postprocess_mock.assert_awaited_once()
        self.assertFalse(os.path.exists(self._marker_path(cfg)))

    async def test_fix_object_entry_passthrough(self) -> None:
        cfg = self._cfg()
        order = [{"fix": {"types": ["残留日文"], "injectProblem": True}}, "tonecheck"]
        run_after_trans = AsyncMock()
        await self._run(cfg, order, run_after_trans)

        self.assertEqual(run_after_trans.await_count, 2)
        self.assertEqual(run_after_trans.await_args_list[0].args[0], order[0])
        self.assertEqual(run_after_trans.await_args_list[1].args[0], "tonecheck")

    async def test_cancel_propagates_and_skips_marker(self) -> None:
        cfg = self._cfg()
        run_after_trans = AsyncMock(side_effect=JobCancelledError())
        with self.assertRaises(JobCancelledError):
            await self._run(cfg, ["improve"], run_after_trans)
        self.assertFalse(os.path.exists(self._marker_path(cfg)))


class AfterTransCacheExclusionTests(unittest.TestCase):
    """断点标记目录不得泄入构建输出收集器（dict 形状按翻译缓存解析必崩）。"""

    def test_collect_cache_files_excludes_aftertrans_markers(self) -> None:
        from GalTransl.server_cache import _collect_cache_files

        cache_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(cache_dir, "pass3_cache"))
        with open(
            os.path.join(cache_dir, "pass3_cache", "a.txt.json"), "w", encoding="utf-8"
        ) as f:
            f.write("[]")
        os.makedirs(os.path.join(cache_dir, "aftertrans_cache"))
        with open(
            os.path.join(cache_dir, "aftertrans_cache", "a.txt.json"), "w", encoding="utf-8"
        ) as f:
            f.write("{}")
        self.assertEqual(_collect_cache_files(cache_dir), ["pass3_cache/a.txt.json"])


if __name__ == "__main__":
    unittest.main()
