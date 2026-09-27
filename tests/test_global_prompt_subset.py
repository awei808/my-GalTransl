# -*- coding: utf-8 -*-
"""全局分析文件子集（0.5.0 批次 3）单元测试。

覆盖三块：
  1. `_select_compressed_paths`：三级宽松匹配（完整路径 / 文件名 / 去扩展名）、
     保持原顺序、去重、未命中忽略并告警、空 filter 返回全量；
  2. `ForGlobalPrompt._build_input_text_from_compressed` 的 file_filter 参数：
     只拼接选中文件、file=None 与 0.4.x 全量行为一致；
  3. `merge_global_prompt`：覆盖指定字段（策略），未列字段保留 base 值；
     首次分析（base 为空）直接返回 incoming。
"""
import os
import unittest
from types import SimpleNamespace

from GalTransl.Backend.ForGlobalPrompt import (
    ForGlobalPrompt,
    MERGE_FIELD_KEYS,
    _select_compressed_paths,
    merge_global_prompt,
)


def _data() -> dict:
    return {
        "route_a.json": "A 线文本",
        "route_b.json": "B 线文本",
        os.path.join("sub", "route_c.json"): "C 线文本",
    }


class SelectCompressedPathsTests(unittest.TestCase):
    def test_none_returns_all_in_original_order(self) -> None:
        data = _data()
        self.assertEqual(_select_compressed_paths(data, None), list(data.keys()))

    def test_empty_list_returns_all(self) -> None:
        data = _data()
        self.assertEqual(_select_compressed_paths(data, []), list(data.keys()))

    def test_exact_full_path_match(self) -> None:
        data = _data()
        target = os.path.join("sub", "route_c.json")
        self.assertEqual(_select_compressed_paths(data, [target]), [target])

    def test_basename_match_with_extension(self) -> None:
        data = _data()
        self.assertEqual(
            _select_compressed_paths(data, ["route_b.json"]), ["route_b.json"]
        )

    def test_stem_match_without_extension(self) -> None:
        data = _data()
        self.assertEqual(
            _select_compressed_paths(data, ["route_a"]), ["route_a.json"]
        )

    def test_result_keeps_compressed_data_order_not_filter_order(self) -> None:
        # filter 顺序反着给：结果仍按 compressed_data 原顺序，保证提示词稳定
        data = _data()
        self.assertEqual(
            _select_compressed_paths(data, ["route_c", "route_a"]),
            ["route_a.json", os.path.join("sub", "route_c.json")],
        )

    def test_duplicate_filter_entries_deduped(self) -> None:
        data = _data()
        self.assertEqual(
            _select_compressed_paths(data, ["route_a", "route_a.json", "route_a"]),
            ["route_a.json"],
        )

    def test_unmatched_entries_ignored(self) -> None:
        data = _data()
        self.assertEqual(
            _select_compressed_paths(data, ["ghost", "route_a"]), ["route_a.json"]
        )

    def test_all_unmatched_returns_empty(self) -> None:
        self.assertEqual(_select_compressed_paths(_data(), ["ghost"]), [])

    def test_blank_and_non_string_entries_skipped(self) -> None:
        data = _data()
        self.assertEqual(
            _select_compressed_paths(data, ["", "  ", None, "route_b"]),
            ["route_b.json"],
        )


class BuildInputTextFilterTests(unittest.TestCase):
    def test_no_filter_concatenates_all(self) -> None:
        text = ForGlobalPrompt._build_input_text_from_compressed(_data())
        for name in ("route_a.json", "route_b.json", "route_c.json"):
            self.assertIn(f"=== {name} ===", text)

    def test_filter_limits_to_selected_files(self) -> None:
        text = ForGlobalPrompt._build_input_text_from_compressed(
            _data(), file_filter=["route_b"]
        )
        self.assertIn("B 线文本", text)
        self.assertNotIn("A 线文本", text)
        self.assertNotIn("C 线文本", text)

    def test_empty_data_returns_empty_string(self) -> None:
        self.assertEqual(
            ForGlobalPrompt._build_input_text_from_compressed({}, ["route_a"]), ""
        )

    def test_blank_text_files_skipped(self) -> None:
        data = {"route_a.json": "   ", "route_b.json": "B"}
        text = ForGlobalPrompt._build_input_text_from_compressed(data)
        self.assertNotIn("route_a.json", text)
        self.assertIn("route_b.json", text)


class MergeGlobalPromptTests(unittest.TestCase):
    def _base(self) -> dict:
        return {
            "游戏名称": "旧名称",
            "剧情概述": "旧概述",
            "角色列表": [{"名称": "甲"}],
            "世界观设定": "旧世界观",
            "行文风格": "旧风格",
            "题材标签": ["旧标签"],
        }

    def test_none_base_returns_incoming_copy(self) -> None:
        incoming = {"剧情概述": "新概述"}
        merged = merge_global_prompt(None, incoming)
        self.assertEqual(merged, incoming)
        self.assertIsNot(merged, incoming)

    def test_none_fields_covers_all_merge_keys(self) -> None:
        # fields=None → 覆盖全部 MERGE_FIELD_KEYS；incoming 未提供的键保留 base 值
        base = self._base()
        merged = merge_global_prompt(base, {"剧情概述": "新概述"})
        self.assertEqual(merged["剧情概述"], "新概述")
        self.assertEqual(merged["世界观设定"], "旧世界观")
        self.assertEqual(merged["角色列表"], [{"名称": "甲"}])

    def test_selected_fields_overwritten_only(self) -> None:
        base = self._base()
        merged = merge_global_prompt(
            base, {"剧情概述": "新概述", "世界观设定": "新世界观"}, ["剧情概述"]
        )
        self.assertEqual(merged["剧情概述"], "新概述")
        # 未选择的字段保留 base
        self.assertEqual(merged["世界观设定"], "旧世界观")
        self.assertEqual(merged["行文风格"], "旧风格")

    def test_base_not_mutated(self) -> None:
        base = self._base()
        merge_global_prompt(base, {"剧情概述": "新概述"}, ["剧情概述"])
        self.assertEqual(base["剧情概述"], "旧概述")

    def test_blank_value_in_selected_field_keeps_base(self) -> None:
        # 规整会用空值补齐缺失键；子集分析的空值不得清空 base 的非空内容
        merged = merge_global_prompt(
            self._base(), {"角色列表": [], "剧情概述": ""}, ["角色列表", "剧情概述"]
        )
        self.assertEqual(merged["角色列表"], [{"名称": "甲"}])
        self.assertEqual(merged["剧情概述"], "旧概述")

    def test_non_blank_value_in_selected_field_overwrites(self) -> None:
        merged = merge_global_prompt(
            self._base(), {"角色列表": [{"名称": "乙"}]}, ["角色列表"]
        )
        self.assertEqual(merged["角色列表"], [{"名称": "乙"}])

    def test_merge_field_keys_matches_normalized_schema(self) -> None:
        self.assertEqual(
            set(MERGE_FIELD_KEYS),
            {"游戏名称", "剧情概述", "角色列表", "世界观设定", "行文风格", "题材标签"},
        )

    def test_merge_field_keys_synced_with_normalize_output(self) -> None:
        # MERGE_FIELD_KEYS 必须与 _normalize_global_prompt 的输出键同序，
        # 否则合并时会出现「补全的键莫名覆盖掉 base」的静默错位。
        normalized = ForGlobalPrompt._normalize_global_prompt({})
        self.assertEqual(list(normalized.keys()), list(MERGE_FIELD_KEYS))


class BatchTranslateSubsetTests(unittest.IsolatedAsyncioTestCase):
    """batch_translate 的子集链路：合并策略与全量零回归。

    用 `__new__` 绕开真实初始化（不连 API），只打桩 LLM 调用与落盘。
    """

    def _engine(self, existing: dict | None):
        """构造打桩引擎；返回 (engine, saved)。

        `saved` 为落盘观测点（_save_global_prompt 写入目标）。
        `existing` 非 None 时充当已有 GlobalPrompt.json 内容。
        """
        t = ForGlobalPrompt.__new__(ForGlobalPrompt)
        # runtime_project_dir 是只读 property（取自 pj_config），不能赋值
        t.pj_config = SimpleNamespace(
            getKey=lambda key, default=None: default,
            runtime_project_dir="",
        )
        t.system_prompt = "SYSTEM"
        t.trans_prompt = "[Input]"
        t.source_lang = "Japanese"
        t.target_lang = "Chinese"
        t._inject_guideline = False

        async def fake_call(messages, *a, **kw):
            return (
                '{"剧情概述":"新概述","角色列表":[{"名称":"新角色"}]}',
                None,
            )

        t._call_llm_with_error_report = fake_call
        t._build_glossary_text = lambda: ""
        t._build_prompt_request = lambda src, glossary="", external_info="": src

        saved: dict = {}
        t._save_global_prompt = lambda data: saved.update(data)
        self._existing = existing
        # 覆盖模块级 load_global_prompt（引擎内按模块全局名查找）
        self._orig_load = globals().get("load_global_prompt")
        import GalTransl.Backend.ForGlobalPrompt as mod

        self._orig_load = mod.load_global_prompt
        mod.load_global_prompt = lambda cfg: existing
        return t, saved

    def tearDown(self) -> None:
        import GalTransl.Backend.ForGlobalPrompt as mod

        if getattr(self, "_orig_load", None) is not None:
            mod.load_global_prompt = self._orig_load

    async def test_full_path_does_not_merge(self) -> None:
        # 不传 filter：全量行为，整体替换（0.4.x 零回归）——旧字段被规整为空值
        t, saved = self._engine({"剧情概述": "旧概述", "世界观设定": "旧世界观"})
        ok = await t.batch_translate({"route_a.json": "A"}, file_filter=None)
        self.assertTrue(ok)
        self.assertEqual(saved["剧情概述"], "新概述")
        self.assertEqual(saved["世界观设定"], "")

    async def test_subset_default_merges_all_fields(self) -> None:
        # 子集 + 未指定 merge_fields → 覆盖全部字段，但未在 incoming 的键保留 base 值
        t, saved = self._engine({"剧情概述": "旧概述", "世界观设定": "旧世界观"})
        ok = await t.batch_translate({"route_a.json": "A"}, file_filter=["route_a"])
        self.assertTrue(ok)
        self.assertEqual(saved["剧情概述"], "新概述")
        self.assertEqual(saved["世界观设定"], "旧世界观")

    async def test_subset_with_selected_fields_keeps_others(self) -> None:
        t, saved = self._engine(
            {"剧情概述": "旧概述", "世界观设定": "旧世界观", "行文风格": "旧风格"}
        )
        ok = await t.batch_translate(
            {"route_a.json": "A"},
            file_filter=["route_a"],
            merge_fields=["剧情概述"],
        )
        self.assertTrue(ok)
        self.assertEqual(saved["剧情概述"], "新概述")
        self.assertEqual(saved["世界观设定"], "旧世界观")
        self.assertEqual(saved["行文风格"], "旧风格")

    async def test_subset_without_existing_writes_directly(self) -> None:
        # 首次分析（无已有产物）：即使传了 filter 也直接落盘
        t, saved = self._engine(None)
        ok = await t.batch_translate({"route_a.json": "A"}, file_filter=["route_a"])
        self.assertTrue(ok)
        self.assertEqual(saved["剧情概述"], "新概述")

    async def test_filter_matching_nothing_returns_false(self) -> None:
        t, saved = self._engine(None)
        ok = await t.batch_translate({"route_a.json": "A"}, file_filter=["ghost"])
        self.assertFalse(ok)
        self.assertEqual(saved, {})

    async def test_all_blank_text_returns_false_without_calling_llm(self) -> None:
        t, saved = self._engine(None)
        called = []

        async def spy(messages, *a, **kw):
            called.append(1)
            return ("{}", None)

        t._call_llm_with_error_report = spy
        ok = await t.batch_translate({"route_a.json": "   "})
        self.assertFalse(ok)
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
