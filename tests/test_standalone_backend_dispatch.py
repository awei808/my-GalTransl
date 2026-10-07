# -*- coding: utf-8 -*-
"""独立后处理后端表驱动分发测试。

背景：原先「单独执行某个后处理后端」依赖 doLLMTranslate 里逐段硬编码的
eng_type 短路分支，ForFixRound / ForToneCheck 漏加分支后落入主翻译流程，最终
触发 postprocess_results 的 gpt.afterTranslation 循环，连带执行全部后处理后端。

覆盖点：
1. STANDALONE_BACKENDS 覆盖全部后处理后端，且不误收翻译/元数据引擎；
2. doLLMTranslate 命中独立分支后不再落入主翻译流程（不调 doLLMTranslSingleChunk）；
3. postprocess_results 兜底守卫：当前引擎为后处理后端时清空 afterTranslation 链；
4. ForFixRound 类型来源：与界面「问题类型」勾选同源（afterTranslation 的 fix 条目
   types）；无 fix 条目时回退 problemAnalyze.problemList；两处皆空时跳过且不实例化后端。

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
    "ForToneCheckAgain",
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


def _build_mini_project(root: str, after_translation: str = "[]") -> str:
    """在 root 下构造含 config.inc.yaml 与单个待译文件的临时项目。

    after_translation 为 gpt.afterTranslation 的 YAML 值文本（替换而非追加，
    避免出现重复键）。
    """
    proj = os.path.join(root, "mini_proj")
    os.makedirs(os.path.join(proj, "gt_input"), exist_ok=True)
    cfg_text = BASE_CONFIG.replace(
        "  gpt.afterTranslation: []",
        f"  gpt.afterTranslation: {after_translation}",
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

    def test_fix_round_requires_params(self) -> None:
        # 统一修复后端需要注入问题类型
        self.assertTrue(STANDALONE_BACKENDS["ForFixRound"].needs_fix_params)


class FixRoundTypeResolutionTests(unittest.TestCase):
    """ForFixRound 单独执行时的问题类型来源。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fix_types_")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _cfg(self, after_translation: str = "[]") -> CProjectConfig:
        proj = _build_mini_project(self._tmp, after_translation)
        cfg = CProjectConfig(proj, "config.inc.yaml")
        cfg.non_interactive = True
        return cfg

    def test_types_come_from_after_translation_fix_entry(self) -> None:
        # 界面「问题类型」勾选写入 afterTranslation 的 fix 条目，单独执行必须读到同一份
        cfg = self._cfg(
            "\n"
            "    - fix:\n"
            "        types:\n"
            "          - 疑似错误\n"
            "        injectProblem: true\n"
        )
        types = _resolve_fix_round_types(cfg)
        self.assertEqual([t.name for t in types], ["疑似错误"])

    def test_fallback_to_problem_list_without_fix_entry(self) -> None:
        # 无 fix 条目 → 回退 problemAnalyze.problemList（config 中为 2 类）
        cfg = self._cfg()
        types = _resolve_fix_round_types(cfg)
        self.assertEqual([t.name for t in types], ["词频过高", "残留日文"])

    def test_empty_fix_entry_types_falls_back(self) -> None:
        # fix 条目存在但 types 为空（界面全不勾）→ 回退 problemList，不静默全修
        cfg = self._cfg(
            "\n"
            "    - fix:\n"
            "        types: []\n"
            "        injectProblem: true\n"
        )
        types = _resolve_fix_round_types(cfg)
        self.assertEqual([t.name for t in types], ["词频过高", "残留日文"])

    def test_unknown_type_ignored(self) -> None:
        cfg = self._cfg(
            "\n"
            "    - fix:\n"
            "        types:\n"
            "          - 不存在的类型\n"
            "          - 疑似错误\n"
        )
        types = _resolve_fix_round_types(cfg)
        self.assertEqual([t.name for t in types], ["疑似错误"])

    def test_matches_after_translation_order_source(self) -> None:
        # 口径一致性防线：与阶段 7 走的 _resolve_after_translation_order 同源，
        # 保证「界面勾什么 → 两条路径就修什么」
        from GalTransl.Frontend.llm_postprocess import _resolve_after_translation_order

        cfg = self._cfg(
            "\n"
            "    - fix:\n"
            "        types:\n"
            "          - 词语色彩不一致\n"
        )
        order = _resolve_after_translation_order(cfg)
        fix_entry = next(e for e in order if isinstance(e, dict))
        standalone_types = [t.name for t in _resolve_fix_round_types(cfg)]
        self.assertEqual(standalone_types, fix_entry["fix"]["types"])


class RunStandaloneBackendTests(unittest.IsolatedAsyncioTestCase):
    """执行器行为：类型为空跳过且不实例化；无文件时正常收尾。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="standalone_run_")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmp, ignore_errors=True)

    async def test_empty_fix_types_skips_without_instantiating(self) -> None:
        # problemList 写成空值（YAML 无条目 → None），afterTranslation 也无 fix 条目：
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

    def test_source_chain_removed(self) -> None:
        # 静态防线：afterTranslation 链已上移为流水线阶段 8（run_improve_stage），
        # postprocess_results 源码中不得再残留链逻辑
        import inspect

        from GalTransl.Frontend import llm_postprocess

        src = inspect.getsource(llm_postprocess.postprocess_results)
        self.assertNotIn("_after_order", src)
        self.assertNotIn("_run_after_trans_single_file", src)


class StandaloneAutoRecheckTests(unittest.IsolatedAsyncioTestCase):
    """独立执行任一后处理后端后，写盘前自动重跑问题检测（find_problems）。

    回归背景：改进/修复类后端（ForImproveTranslation 等）原先不重检，跑完后
    问题列表不刷新；现统一在写盘前 postprocess_trans_list + find_problems。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="standalone_recheck_")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmp, ignore_errors=True)

    async def _assert_recheck_runs(self, eng_type: str) -> None:
        from GalTransl.Frontend import llm_standalone

        proj = _build_mini_project(self._tmp)
        cfg = CProjectConfig(proj, "config.inc.yaml")
        cfg.non_interactive = True
        # 预置 pass3 缓存占位文件（内容不参与：缓存读取已打桩，仅过存在性检查）
        pass3_dir = os.path.join(proj, "transl_cache", "pass3_cache")
        os.makedirs(pass3_dir, exist_ok=True)
        with open(os.path.join(pass3_dir, "scene_01.txt.json"), "w", encoding="utf-8") as f:
            f.write("[]")

        find_problems_calls = []

        def spy_find_problems(*args, **kwargs):
            find_problems_calls.append(1)

        api = MagicMock()
        api.batch_translate = AsyncMock()
        api.shutdown = AsyncMock()

        async def fake_init(*args, **kwargs):
            return api

        input_path = os.path.join(proj, "gt_input", "scene_01.txt.json")
        json_list = [
            {"name": "爱丽丝", "message": "今日はいい天気だね。"},
            {"name": "ボブ", "message": "そうだね、散歩に行こう。"},
        ]
        with patch(
            "GalTransl.Frontend.llm_standalone.get_transCache_from_json",
            new=AsyncMock(return_value=([], [])),
        ), patch(
            "GalTransl.Frontend.llm_standalone.save_transCache_to_json",
            new=AsyncMock(),
        ), patch(
            "GalTransl.Frontend.llm_standalone._resolve_file_h_ranges",
            return_value=[],
        ), patch(
            "GalTransl.Frontend.llm_standalone.find_problems",
            side_effect=spy_find_problems,
        ):
            result = await run_standalone_backend(
                cfg,
                eng_type,
                {input_path: json_list},
                noop_ensure_model_available,
                fake_init,
            )

        self.assertTrue(result)
        api.batch_translate.assert_awaited_once()
        self.assertEqual(
            len(find_problems_calls), 1, "独立执行后端后应自动重跑一次问题检测"
        )

    async def test_improve_triggers_recheck(self) -> None:
        # 回归：改进轮原先不重检问题列表
        await self._assert_recheck_runs("ForImproveTranslation")

    async def test_brstation_triggers_recheck(self) -> None:
        await self._assert_recheck_runs("ForBRStation")

    async def test_fix_round_triggers_recheck(self) -> None:
        await self._assert_recheck_runs("ForFixRound")

    async def test_semcheck_still_triggers_recheck(self) -> None:
        # 原有 finalize 行为保持不变
        await self._assert_recheck_runs("ForSemCheck")


if __name__ == "__main__":
    unittest.main()
