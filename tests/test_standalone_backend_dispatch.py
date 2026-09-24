# -*- coding: utf-8 -*-
"""独立后处理后端表驱动分发测试。

背景：原先「单独执行某个后处理后端」依赖 doLLMTranslate 里逐段硬编码的
eng_type 短路分支，ForFixRound / ForToneCheck 漏加分支后落入主翻译流程，最终
触发 postprocess_results 的 gpt.afterTranslation 循环，连带执行全部后处理后端。

覆盖点：
1. STANDALONE_BACKENDS 覆盖全部后处理后端，且不误收翻译/元数据引擎；
2. doLLMTranslate 命中独立分支后不再落入主翻译流程（不调 doLLMTranslSingleChunk）；
3. postprocess_results 兜底守卫：当前引擎为后处理后端时清空 afterTranslation 链；
4. ForFixRound 类型来源：gpt.fixRoundTypes 优先，空则回退 problemAnalyze.problemList；
   两处皆空时跳过且不实例化后端。

配置一律用真实 CProjectConfig + 临时项目，避免手搓桩与生产路径口径漂移
（历史踩坑：测试桩数据结构与生产路径不一致导致测试全绿但线上崩）。
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.Frontend.llm_standalone import (
    STANDALONE_BACKENDS,
    is_standalone_backend,
    run_standalone_backend,
    _resolve_fix_round_types,
)

POSTPROCESS_ENGINES = {
    "ForBRStation",
    "ForJPResidue",
    "ForBanWordFix",
    "ForImproveTranslation",
    "ForSemCheck",
    "ForSemCheckAgain",
    "ForToneCheck",
    "ForFixRound",
}
NON_STANDALONE_ENGINES = {
    "ForGal-json-translate",
    "ForGal-full-pipeline",
    "GenDic",
    "ForFileMetaData",
    "ForBatchMetaData",
    "ForPlotRouteMap",
    "ForGlobalPrompt",
    "rebuildr",
    "rebuilda",
}

BASE_CONFIG = """backendSpecific:
  OpenAI-Compatible:
    tokens:
      - token: sk-test
        endpoint: http://127.0.0.1:9999
        modelName: deepseek-chat

plugin:
  filePlugin: file_galtransl_json
  textPlugins:
    - text_common_normalfix

common:
  gpt.numPerRequestTranslate: 10
  workersPerProject: 1
  language: "ja2zh-cn"
  splitFile: "no"
  gpt.translation_guideline: "Basic.md"
  gpt.afterTranslation: []

internals:
  pipeline:
    enableImprove: true

dictionary:
  defaultDictFolder: Dict
  preDict: []
  gpt.dict: []
  postDict: []

problemAnalyze:
  problemList:
    - 词频过高
    - 残留日文

proxy:
  enableProxy: false
"""


def _build_mini_project(root: str, extra_common: str = "") -> str:
    """在 root 下构造含 config.inc.yaml 与单个待译文件的临时项目。"""
    proj = os.path.join(root, "mini_proj")
    os.makedirs(os.path.join(proj, "gt_input"), exist_ok=True)
    cfg_text = BASE_CONFIG
    if extra_common:
        cfg_text = cfg_text.replace(
            "  gpt.afterTranslation: []",
            f"  gpt.afterTranslation: []\n{extra_common}",
        )
    with open(os.path.join(proj, "config.inc.yaml"), "w", encoding="utf-8") as f:
        f.write(cfg_text)
    lines = [
        {"name": "爱丽丝", "message": "今日はいい天気だね。"},
        {"name": "ボブ", "message": "そうだね、散歩に行こう。"},
    ]
    with open(
        os.path.join(proj, "gt_input", "scene_01.txt.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(lines, f, ensure_ascii=False)
    return proj


async def noop_ensure_model_available(*args, **kwargs):
    """替代模型可用性网络检查（本用例只验证分派，不触网）。"""
    return None


class StandaloneBackendTableTests(unittest.TestCase):
    """引擎表必须覆盖全部后处理后端，且不误收其他引擎。"""

    def test_all_postprocess_engines_registered(self) -> None:
        missing = POSTPROCESS_ENGINES - set(STANDALONE_BACKENDS)
        self.assertEqual(missing, set(), f"后处理后端未注册独立执行表: {missing}")

    def test_non_postprocess_engines_excluded(self) -> None:
        extra = set(STANDALONE_BACKENDS) & NON_STANDALONE_ENGINES
        self.assertEqual(extra, set(), f"非后处理后端被误收进独立执行表: {extra}")

    def test_is_standalone_backend_matches_table(self) -> None:
        for name in POSTPROCESS_ENGINES:
            with self.subTest(engine=name):
                self.assertTrue(is_standalone_backend(name))
        for name in NON_STANDALONE_ENGINES:
            with self.subTest(engine=name):
                self.assertFalse(is_standalone_backend(name))

    def test_fix_round_requires_params_and_mark_engines_finalize(self) -> None:
        # 统一修复后端需要注入问题类型；标记类后端需在写盘前认领 problem
        self.assertTrue(STANDALONE_BACKENDS["ForFixRound"].needs_fix_params)
        for name in ("ForSemCheck", "ForSemCheckAgain", "ForToneCheck"):
            with self.subTest(engine=name):
                self.assertTrue(STANDALONE_BACKENDS[name].finalize_problems)
        # 纯译文生成类后端不需要 finalize
        self.assertFalse(STANDALONE_BACKENDS["ForImproveTranslation"].finalize_problems)


class FixRoundTypeResolutionTests(unittest.TestCase):
    """ForFixRound 单独执行时的问题类型来源。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fix_types_")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _cfg(self, extra_common: str = "") -> CProjectConfig:
        proj = _build_mini_project(self._tmp, extra_common)
        cfg = CProjectConfig(proj, "config.inc.yaml")
        cfg.non_interactive = True
        return cfg

    def test_explicit_key_takes_priority(self) -> None:
        cfg = self._cfg("  gpt.fixRoundTypes:\n    - 疑似错误\n")
        types = _resolve_fix_round_types(cfg)
        self.assertEqual([t.name for t in types], ["疑似错误"])

    def test_fallback_to_problem_list_when_empty(self) -> None:
        # 未配置 gpt.fixRoundTypes → 回退 problemAnalyze.problemList（config 中为 2 类）
        cfg = self._cfg()
        types = _resolve_fix_round_types(cfg)
        self.assertEqual([t.name for t in types], ["词频过高", "残留日文"])

    def test_unknown_type_ignored(self) -> None:
        cfg = self._cfg("  gpt.fixRoundTypes:\n    - 不存在的类型\n    - 疑似错误\n")
        types = _resolve_fix_round_types(cfg)
        self.assertEqual([t.name for t in types], ["疑似错误"])


class RunStandaloneBackendTests(unittest.IsolatedAsyncioTestCase):
    """执行器行为：类型为空跳过且不实例化；无文件时正常收尾。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="standalone_run_")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmp, ignore_errors=True)

    async def test_empty_fix_types_skips_without_instantiating(self) -> None:
        # problemList 写成空值（YAML 无条目 → None），fixRoundTypes 也未配置：
        # 两处皆空应跳过，且不实例化后端。同时覆盖 getProblemAnalyzeConfig 对
        # None 的防御（否则会抛 TypeError）。
        proj = _build_mini_project(self._tmp)
        cfg_path = os.path.join(proj, "config.inc.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            text = f.read()
        text = text.replace(
            "  problemList:\n    - 词频过高\n    - 残留日文\n", "  problemList:\n"
        )
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(text)
        cfg = CProjectConfig(proj, "config.inc.yaml")
        cfg.non_interactive = True

        init_calls = []

        async def fake_init_gptapi(config, *args, **kwargs):
            init_calls.append(1)
            return MagicMock()

        result = await run_standalone_backend(
            cfg,
            "ForFixRound",
            {"a.json": []},
            noop_ensure_model_available,
            fake_init_gptapi,
        )
        self.assertTrue(result)
        self.assertEqual(init_calls, [], "类型为空时不应实例化后端（避免白建客户端）")

    async def test_unknown_engine_raises(self) -> None:
        cfg = CProjectConfig(_build_mini_project(self._tmp), "config.inc.yaml")

        async def fake_init(*args, **kwargs):
            return MagicMock()

        with self.assertRaises(ValueError):
            await run_standalone_backend(
                cfg,
                "ForGal-json-translate",
                {},
                noop_ensure_model_available,
                fake_init,
            )

    async def test_no_files_returns_true_and_shuts_down(self) -> None:
        cfg = CProjectConfig(_build_mini_project(self._tmp), "config.inc.yaml")
        cfg.non_interactive = True
        api = MagicMock()
        api.shutdown = AsyncMock()

        async def fake_init(*args, **kwargs):
            return api

        result = await run_standalone_backend(
            cfg, "ForImproveTranslation", {}, noop_ensure_model_available, fake_init
        )
        self.assertTrue(result)
        api.shutdown.assert_awaited()


class DoLLMTranslateDispatchTests(unittest.IsolatedAsyncioTestCase):
    """doLLMTranslate 对后处理后端必须走独立分支，不落入主翻译流程。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="standalone_dispatch_")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _cfg(self, eng_type: str) -> CProjectConfig:
        proj = _build_mini_project(self._tmp)
        cfg = CProjectConfig(proj, "config.inc.yaml")
        cfg.non_interactive = True
        cfg.select_translator = eng_type
        return cfg

    async def _assert_goes_standalone(self, eng_type: str) -> None:
        from GalTransl.Frontend import LLMTranslate

        cfg = self._cfg(eng_type)
        single_chunk_calls = []

        async def spy_single_chunk(*args, **kwargs):
            single_chunk_calls.append(1)

        with patch.object(
            LLMTranslate, "run_standalone_backend", new=AsyncMock(return_value=True)
        ) as standalone_mock, patch.object(
            LLMTranslate, "doLLMTranslSingleChunk", new=spy_single_chunk
        ), patch.object(
            LLMTranslate, "ensure_model_available_if_needed",
            new=noop_ensure_model_available,
        ):
            result = await LLMTranslate.doLLMTranslate(cfg)

        self.assertTrue(result, "doLLMTranslate 应返回 True")
        self.assertEqual(
            single_chunk_calls, [], "后处理后端不得落入主翻译流程（chunk 翻译）"
        )
        self.assertGreaterEqual(standalone_mock.await_count, 1, "应走独立执行分支")

    async def test_fix_round_does_not_enter_main_translation(self) -> None:
        await self._assert_goes_standalone("ForFixRound")

    async def test_tonecheck_does_not_enter_main_translation(self) -> None:
        await self._assert_goes_standalone("ForToneCheck")

    async def test_improve_still_goes_standalone(self) -> None:
        # 回归：原有独立分支引擎改造后行为不变
        await self._assert_goes_standalone("ForImproveTranslation")

    async def test_brstation_still_goes_standalone(self) -> None:
        await self._assert_goes_standalone("ForBRStation")


class PostprocessGuardTests(unittest.TestCase):
    """postprocess_results 兜底守卫：后处理后端不连带执行 afterTranslation 链。"""

    def test_guard_recognizes_all_postprocess_engines(self) -> None:
        for name in POSTPROCESS_ENGINES:
            with self.subTest(engine=name):
                self.assertTrue(
                    is_standalone_backend(name),
                    f"{name} 应被兜底守卫识别为独立后端",
                )

    def test_source_contains_guard(self) -> None:
        # 静态防线：守卫代码必须真实存在于 postprocess_results 中
        import inspect

        from GalTransl.Frontend import llm_postprocess

        src = inspect.getsource(llm_postprocess.postprocess_results)
        self.assertIn("is_standalone_backend", src)
        self.assertIn("_after_order = []", src)


if __name__ == "__main__":
    unittest.main()
