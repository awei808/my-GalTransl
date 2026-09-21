# -*- coding: utf-8 -*-
"""提示词注入块开关（0.5.0 批次 4）单元测试。

覆盖：
  1. PROMPT_BLOCK_DEFAULTS 全 True → 默认行为与 0.5.0 之前一致（零回归）；
  2. 各开关独立生效：关闭某块时该占位符被清空，其余块不受影响；
  3. 功能性占位符（[Input]/[SourceLang]/[TargetLang]）不可被关闭；
  4. `_build_round_user_content` 续轮路径：batchMetadata / glossary 开关生效；
  5. 开关值支持字符串写法（"false"/"0"/"no"）→ 按字面解析（非 Python truthiness）。
"""
import unittest
from types import SimpleNamespace

from GalTransl.Backend.BaseEngine import BaseEngine, PROMPT_BLOCK_DEFAULTS
from GalTransl.Backend.ForGalJsonTranslate import ForGalJsonTranslate


TEMPLATE = (
    "[SourceLang]->[TargetLang]\n"
    "[translation_guideline]\n"
    "[plot_metadata]\n"
    "[batch_metadata]\n"
    "[global_prompt]\n"
    "[Glossary]\n"
    "[Input]\n"
)


def _engine(block_overrides: dict | None = None):
    """构造只打桩提示词装配所需最小状态的引擎实例。"""
    t = object.__new__(ForGalJsonTranslate)
    overrides = block_overrides or {}

    def get_key(key, default=None):
        if key.startswith("internals.promptBlocks."):
            name = key[len("internals.promptBlocks."):]
            if name in overrides:
                return overrides[name]
            return default
        return default

    t.pj_config = SimpleNamespace(
        translation_guideline="GUIDE",
        getKey=get_key,
        runtime_project_dir="",
    )
    t.trans_prompt = TEMPLATE
    t.source_lang = "Japanese"
    t.target_lang = "Chinese"
    # 续轮/单轮路径的 debug 日志会读取以下属性，缺一会抛 AttributeError
    t.eng_type = "ForGal-json-translate"
    t._global_prompt = None
    t._file_metadata_by_file = {}
    t._batch_metadata_by_file = {}
    return t


class PromptBlockDefaultsTests(unittest.TestCase):
    def test_defaults_all_true(self) -> None:
        self.assertTrue(all(PROMPT_BLOCK_DEFAULTS.values()))
        self.assertEqual(
            set(PROMPT_BLOCK_DEFAULTS),
            {
                "translationGuideline",
                "glossary",
                "plotMetadata",
                "batchMetadata",
                "globalPrompt",
            },
        )

    def test_no_config_keeps_all_blocks(self) -> None:
        # 零回归：未配置任何开关 → 五块全部注入
        t = _engine()
        out = t._build_prompt_request(
            "INPUT", "DICT", "PLOT", "BATCH", global_prompt="GLOBAL"
        )
        for token in ("GUIDE", "DICT", "PLOT", "BATCH", "GLOBAL", "INPUT"):
            self.assertIn(token, out)

    def test_no_placeholder_left_behind(self) -> None:
        t = _engine()
        out = t._build_prompt_request(
            "INPUT", "DICT", "PLOT", "BATCH", global_prompt="GLOBAL"
        )
        for ph in ("[translation_guideline]", "[plot_metadata]", "[batch_metadata]",
                   "[global_prompt]", "[Glossary]", "[Input]",
                   "[SourceLang]", "[TargetLang]"):
            self.assertNotIn(ph, out)


class PromptBlockToggleTests(unittest.TestCase):
    def _render(self, **overrides):
        t = _engine(overrides)
        return t._build_prompt_request(
            "INPUT", "DICT", "PLOT", "BATCH", global_prompt="GLOBAL"
        )

    def test_disabling_each_block_removes_only_that_content(self) -> None:
        cases = {
            "translationGuideline": "GUIDE",
            "glossary": "DICT",
            "plotMetadata": "PLOT",
            "batchMetadata": "BATCH",
            "globalPrompt": "GLOBAL",
        }
        for name, token in cases.items():
            with self.subTest(block=name):
                out = self._render(**{name: False})
                self.assertNotIn(token, out)
                # 其余块仍然注入
                for other, other_token in cases.items():
                    if other != name:
                        self.assertIn(other_token, out)
                self.assertIn("INPUT", out)

    def test_all_disabled_keeps_input_and_langs(self) -> None:
        out = self._render(
            translationGuideline=False,
            glossary=False,
            plotMetadata=False,
            batchMetadata=False,
            globalPrompt=False,
        )
        self.assertIn("INPUT", out)
        self.assertIn("Japanese->Chinese", out)
        for token in ("GUIDE", "DICT", "PLOT", "BATCH", "GLOBAL"):
            self.assertNotIn(token, out)

    def test_string_false_is_parsed_literally(self) -> None:
        # 配置可能来自 YAML/JSON 字符串：bool("false") is True，必须按字面解析
        for raw in ("false", "False", "0", "no", "off"):
            with self.subTest(raw=raw):
                self.assertNotIn("GUIDE", self._render(translationGuideline=raw))

    def test_string_true_keeps_block(self) -> None:
        for raw in ("true", "True", "1", "yes", "on"):
            with self.subTest(raw=raw):
                self.assertIn("GUIDE", self._render(translationGuideline=raw))

    def test_toggles_resolved_once_and_cached(self) -> None:
        t = _engine({"glossary": False})
        calls = []
        orig = t.pj_config.getKey

        def counting(key, default=None):
            calls.append(key)
            return orig(key, default)

        t.pj_config.getKey = counting
        t._prompt_block_toggles()
        first_round_calls = len(calls)
        t._prompt_block_toggles()
        # 第二次走缓存，不再查配置
        self.assertEqual(len(calls), first_round_calls)
        self.assertFalse(t._prompt_block_toggles()["glossary"])


class GuidelineAndSemanticsTests(unittest.TestCase):
    """promptBlocks.translationGuideline 与后端专用 inject_guideline 为 AND 关系。"""

    def test_backend_block_empty_wins(self) -> None:
        # 元数据类后端经 _build_guideline_block() 传入空串（其 inject_guideline=false），
        # 即使全局开关为 true 也不注入规范
        t = object.__new__(BaseEngine)
        t.pj_config = SimpleNamespace(
            translation_guideline="GUIDE",
            getKey=lambda key, default=None: default,
        )
        t.trans_prompt = "[translation_guideline]|[Input]"
        t.source_lang = "Japanese"
        t.target_lang = "Chinese"
        out = t._build_prompt_request("INPUT", "", translation_guideline="")
        self.assertNotIn("GUIDE", out)
        self.assertIn("|INPUT", out)

    def test_global_toggle_off_wins(self) -> None:
        t = object.__new__(BaseEngine)
        t.pj_config = SimpleNamespace(
            translation_guideline="GUIDE",
            getKey=lambda key, default=None: (
                False if key.endswith(".translationGuideline") else default
            ),
        )
        t.trans_prompt = "[translation_guideline]|[Input]"
        t.source_lang = "Japanese"
        t.target_lang = "Chinese"
        out = t._build_prompt_request("INPUT", "")
        self.assertNotIn("GUIDE", out)
        self.assertIn("|INPUT", out)


class ContinueRoundToggleTests(unittest.TestCase):
    """续轮路径（_build_round_user_content 非首轮）：batchMetadata / glossary 开关。"""

    def _engine(self, **overrides):
        t = _engine(overrides)
        t.conversations = {}
        t._history_placeholder_warned = False
        t.last_translations = {}
        return t

    def test_continue_round_injects_both_by_default(self) -> None:
        t = self._engine()
        out = t._build_round_user_content(
            [], "INPUT", "DICT", "f.json", is_first_round=False,
            batch_metadata_block="BATCH",
        )
        self.assertIn("BATCH", out)
        self.assertIn("DICT", out)
        self.assertIn("INPUT", out)

    def test_continue_round_batch_metadata_toggle(self) -> None:
        t = self._engine(batchMetadata=False)
        out = t._build_round_user_content(
            [], "INPUT", "DICT", "f.json", is_first_round=False,
            batch_metadata_block="BATCH",
        )
        self.assertNotIn("BATCH", out)
        self.assertIn("DICT", out)

    def test_continue_round_glossary_toggle(self) -> None:
        t = self._engine(glossary=False)
        out = t._build_round_user_content(
            [], "INPUT", "DICT", "f.json", is_first_round=False,
            batch_metadata_block="BATCH",
        )
        self.assertIn("BATCH", out)
        self.assertNotIn("DICT", out)
        self.assertIn("INPUT", out)

    def test_continue_round_input_always_present(self) -> None:
        t = self._engine(batchMetadata=False, glossary=False)
        out = t._build_round_user_content(
            [], "待译内容", "DICT", "f.json", is_first_round=False,
            batch_metadata_block="BATCH",
        )
        self.assertEqual(out, "待译内容")


class PlotRouteMapToggleTests(unittest.TestCase):
    """ForPlotRouteMap 自定义装配路径也需受 globalPrompt 开关控制。"""

    def _engine(self, **overrides):
        from GalTransl.Backend.ForPlotRouteMap import ForPlotRouteMap

        t = object.__new__(ForPlotRouteMap)
        overrides_ = overrides

        def get_key(key, default=None):
            if key.startswith("internals.promptBlocks."):
                name = key[len("internals.promptBlocks."):]
                if name in overrides_:
                    return overrides_[name]
            return default

        t.pj_config = SimpleNamespace(getKey=get_key, runtime_project_dir="")
        t.trans_prompt = "[structure_type]|[user_outline]|[file_summaries]|[global_prompt]"
        return t

    def _render(self, **overrides):
        t = self._engine(**overrides)
        prompt = t.trans_prompt
        prompt = prompt.replace("[structure_type]", "线性")
        prompt = prompt.replace("[user_outline]", "OUTLINE")
        prompt = prompt.replace("[file_summaries]", "SUMMARIES")
        global_prompt_block = (
            "GLOBALBLOCK" if t._prompt_block_toggles()["globalPrompt"] else ""
        )
        return prompt.replace("[global_prompt]", global_prompt_block)

    def test_global_prompt_on_by_default(self) -> None:
        self.assertIn("GLOBALBLOCK", self._render())

    def test_global_prompt_can_be_disabled(self) -> None:
        out = self._render(globalPrompt=False)
        self.assertNotIn("GLOBALBLOCK", out)
        self.assertIn("SUMMARIES", out)


class BaseEngineToggleAccessorTests(unittest.TestCase):
    """开关读取器在基类上的行为（供其它后端复用）。"""

    def test_default_true_when_config_missing(self) -> None:
        t = object.__new__(BaseEngine)
        t.pj_config = SimpleNamespace(getKey=lambda key, default=None: default)
        self.assertEqual(t._prompt_block_toggles(), PROMPT_BLOCK_DEFAULTS)

    def test_partial_override(self) -> None:
        t = object.__new__(BaseEngine)
        t.pj_config = SimpleNamespace(
            getKey=lambda key, default=None: (
                False if key.endswith(".globalPrompt") else default
            )
        )
        toggles = t._prompt_block_toggles()
        self.assertFalse(toggles["globalPrompt"])
        self.assertTrue(toggles["glossary"])

    def test_config_without_getkey_falls_back_to_defaults(self) -> None:
        # duck-typed 配置桩（无 getKey）不得抛错，且行为等价于全开（零回归）
        t = object.__new__(BaseEngine)
        t.pj_config = SimpleNamespace(translation_guideline="GUIDE")
        self.assertEqual(t._prompt_block_toggles(), PROMPT_BLOCK_DEFAULTS)
        t.trans_prompt = "[translation_guideline]\n[Input]"
        t.source_lang = "Japanese"
        t.target_lang = "Chinese"
        out = t._build_prompt_request("INPUT", "")
        self.assertIn("GUIDE", out)
        self.assertIn("INPUT", out)


if __name__ == "__main__":
    unittest.main()
