# -*- coding: utf-8 -*-
"""afterTranslation 后处理链门控测试（postprocess_results）。

设计口径：gpt.afterTranslation 后处理链属于完整流水线阶段 8——
- 仅任务级引擎为 ForGal-full-pipeline 时执行（流水线阶段 7 在 worker 池启动前
  会把 select_translator 恢复为任务级引擎名，故 postprocess_results 可据此判定）；
- 独立翻译任务（ForGal-json-translate）只翻译，不连带执行修复/检测后端；
- rebuild / 独立后处理引擎维持原有排除行为。

postprocess_results 的调用方在 LLMTranslate/llm_postprocess 命名空间内查找名字，
patch 一律打在 GalTransl.Frontend.llm_postprocess（打 LLMTranslate 会静默打空）。
"""
import unittest
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

from GalTransl.Frontend.LLMTranslate import postprocess_results


class _FakeProjectConfig:
    """postprocess_results 所需的最小配置桩（口径同 test_postprocess_h_ranges）。"""

    def __init__(self, select_translator: str, enable_improve: bool = True) -> None:
        self.select_translator = select_translator
        self.enable_improve = enable_improve

    def getProjectDir(self) -> str:
        return "/tmp/proj"

    def getInputPath(self) -> str:
        return "/tmp/proj/gt_input"

    def getOutputPath(self) -> str:
        return "/tmp/proj/gt_output"

    def getCachePath(self) -> str:
        return "/tmp/proj/transl_cache"

    @property
    def gpt_dic(self) -> list:
        return []

    @property
    def name_replaceDict(self) -> dict:
        return {}

    @property
    def file_save_funcs(self) -> dict:
        return {}

    def getKey(self, key: str, default: Any = None) -> Any:
        if key == "internals.pipeline.enableImprove":
            return self.enable_improve
        return default


def _make_chunk(file_path: str = "/tmp/proj/gt_input/story.txt.json") -> SimpleNamespace:
    return SimpleNamespace(
        trans_list=[],
        file_path=file_path,
        chunk_index=0,
        total_chunks=1,
    )


def _run_postprocess(
    cfg: _FakeProjectConfig, after_order: list, run_after_trans: AsyncMock
) -> Optional[Exception]:
    """在公共 patch 集合下执行 postprocess_results，返回异常（无异常为 None）。"""
    chunk = _make_chunk()
    try:
        with patch(
            "GalTransl.Frontend.llm_postprocess._resolve_after_translation_order",
            return_value=after_order,
        ), patch(
            "GalTransl.Frontend.llm_postprocess._run_after_trans_single_file",
            new=run_after_trans,
        ), patch(
            "GalTransl.Frontend.llm_postprocess.ensure_model_available_if_needed",
            new=AsyncMock(return_value=None),
        ), patch(
            "GalTransl.Frontend.llm_postprocess._resolve_file_h_ranges",
            return_value=[],
        ), patch(
            "GalTransl.Frontend.llm_postprocess.find_problems",
            return_value=None,
        ), patch(
            "GalTransl.Frontend.llm_postprocess._update_runtime",
            return_value=None,
        ), patch(
            "GalTransl.Frontend.llm_postprocess.save_transCache_to_json",
            new=AsyncMock(),
        ), patch(
            "GalTransl.Frontend.llm_postprocess.DictionaryCombiner.combine",
            return_value=([], []),
        ):
            import asyncio

            asyncio.run(postprocess_results([chunk], cfg))
    except Exception as e:  # noqa: BLE001 - 测试内捕获后交由断言判别
        return e
    return None


class AfterTranslationGateTests(unittest.TestCase):
    """独立翻译不跑 afterTranslation 链；完整流水线照跑。"""

    def test_standalone_translation_skips_chain(self) -> None:
        run_after_trans = AsyncMock()
        err = _run_postprocess(
            _FakeProjectConfig("ForGal-json-translate"), ["improve"], run_after_trans
        )
        self.assertIsNone(err)
        run_after_trans.assert_not_awaited()

    def test_legacy_alias_translation_skips_chain(self) -> None:
        # 纵深防御：旧引擎名经 Runner 归一化后本不会以此形态到达门控，
        # 即便直传也应按非流水线任务跳过
        run_after_trans = AsyncMock()
        err = _run_postprocess(
            _FakeProjectConfig("ForGal-json-multi-chat"), ["fix"], run_after_trans
        )
        self.assertIsNone(err)
        run_after_trans.assert_not_awaited()

    def test_pipeline_translation_runs_chain(self) -> None:
        run_after_trans = AsyncMock()
        err = _run_postprocess(
            _FakeProjectConfig("ForGal-full-pipeline"), ["improve"], run_after_trans
        )
        self.assertIsNone(err)
        self.assertEqual(run_after_trans.await_count, 1)
        self.assertEqual(run_after_trans.await_args.args[0], "improve")

    def test_pipeline_with_enableImprove_false_skips_chain(self) -> None:
        run_after_trans = AsyncMock()
        err = _run_postprocess(
            _FakeProjectConfig("ForGal-full-pipeline", enable_improve=False),
            ["improve"],
            run_after_trans,
        )
        self.assertIsNone(err)
        run_after_trans.assert_not_awaited()

    def test_rebuild_engine_skips_chain(self) -> None:
        run_after_trans = AsyncMock()
        err = _run_postprocess(
            _FakeProjectConfig("rebuilda"), ["improve"], run_after_trans
        )
        self.assertIsNone(err)
        run_after_trans.assert_not_awaited()

    def test_standalone_fix_engine_skips_chain(self) -> None:
        # 兜底防线：单独执行修复类后端时不得连带 afterTranslation 链
        run_after_trans = AsyncMock()
        err = _run_postprocess(
            _FakeProjectConfig("ForFixRound"), ["improve"], run_after_trans
        )
        self.assertIsNone(err)
        run_after_trans.assert_not_awaited()

    def test_empty_order_never_runs_chain(self) -> None:
        run_after_trans = AsyncMock()
        err = _run_postprocess(
            _FakeProjectConfig("ForGal-full-pipeline"), [], run_after_trans
        )
        self.assertIsNone(err)
        run_after_trans.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
