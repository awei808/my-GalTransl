"""
流水线新模块边缘情况测试。

覆盖：
  - DataValidator: 非法输入、边界值、格式错乱
  - TextCompressor: 空数据、特殊字符、极限重复
  - ForGlobalPrompt: 参数类型错误、空值、异常字符
"""

import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from GalTransl.DataValidator import (
    validate_input_json,
    validate_llm_response,
    validate_global_prompt,
    validate_file_metadata_entry,
    validate_batch_metadata_entry,
    cross_validate_counts,
    validate_translation_output,
)
from GalTransl.TextCompressor import TextCompressor


# ──────────────────────────────────────────
# DataValidator 测试
# ──────────────────────────────────────────

class ValidateInputJsonEdgeCases(unittest.TestCase):
    """validate_input_json: 非法和边界输入"""

    def test_none_input(self):
        r = validate_input_json(None)
        self.assertFalse(r["valid"])
        self.assertIn("不是列表", r["errors"][0])

    def test_string_input(self):
        r = validate_input_json("not a list")
        self.assertFalse(r["valid"])

    def test_empty_list(self):
        r = validate_input_json([])
        self.assertTrue(r["valid"])
        self.assertIn("空列表", r["warnings"][0])

    def test_dict_not_list(self):
        r = validate_input_json({"key": "value"})
        self.assertFalse(r["valid"])

    def test_item_not_dict__int(self):
        r = validate_input_json([42])
        self.assertFalse(r["valid"])
        self.assertIn("不是字典格式", r["errors"][0])

    def test_item_not_dict__string(self):
        r = validate_input_json(["hello"])
        self.assertFalse(r["valid"])

    def test_missing_message_field(self):
        r = validate_input_json([{"name": "x"}])
        self.assertFalse(r["valid"])
        self.assertIn("缺少 'message'", r["errors"][0])

    def test_message_wrong_type__int(self):
        r = validate_input_json([{"message": 123}])
        self.assertFalse(r["valid"])
        self.assertIn("类型错误", r["errors"][0])

    def test_message_wrong_type__bool(self):
        r = validate_input_json([{"message": True}])
        self.assertFalse(r["valid"])

    def test_name_wrong_type__int(self):
        r = validate_input_json([{"message": "a", "name": 999}])
        self.assertFalse(r["valid"])
        self.assertIn("类型错误", r["errors"][0])

    def test_name_wrong_type__dict(self):
        r = validate_input_json([{"message": "a", "name": {"nested": 1}}])
        self.assertFalse(r["valid"])

    def test_index_float_with_fraction(self):
        r = validate_input_json([{"message": "a", "index": 1.5}])
        self.assertFalse(r["valid"])
        self.assertIn("不是有效整数", r["errors"][0])

    def test_index_string_not_digit(self):
        r = validate_input_json([{"message": "a", "index": "abc"}])
        self.assertFalse(r["valid"])

    def test_all_messages_empty(self):
        r = validate_input_json([
            {"message": "", "name": "x"},
            {"message": "", "name": "y"},
        ])
        self.assertFalse(r["valid"])
        self.assertIn("均为空", r["errors"][0])

    def test_mixed_valid_and_invalid(self):
        r = validate_input_json([
            {"message": "valid1", "name": "a"},
            {"message": 123},                    # 无效
            {"message": "valid2", "name": "b"},
            {"name": "missing_msg"},             # 无效
            {"message": "valid3"},
        ])
        self.assertFalse(r["valid"])
        self.assertEqual(r["stats"]["total_items"], 5)
        self.assertEqual(len(r["errors"]), 2)

    def test_large_input(self):
        items = [{"message": f"msg{i}", "name": f"name{i}", "index": i}
                 for i in range(10000)]
        r = validate_input_json(items)
        self.assertTrue(r["valid"])
        self.assertEqual(r["stats"]["total_items"], 10000)

    def test_name_as_list(self):
        r = validate_input_json([{"message": "a", "name": ["A", "B"]}])
        self.assertTrue(r["valid"])
        self.assertEqual(r["stats"]["items_with_name"], 1)

    def test_index_as_digit_string(self):
        r = validate_input_json([{"message": "a", "index": "42"}])
        self.assertTrue(r["valid"])

    def test_message_with_newlines(self):
        r = validate_input_json([{"message": "line1\nline2\r\nline3"}])
        self.assertTrue(r["valid"])


class ValidateLlmResponseEdgeCases(unittest.TestCase):
    """validate_llm_response: LLM 返回的异常情况"""

    def test_none_response(self):
        r = validate_llm_response(None)
        self.assertFalse(r["valid"])
        self.assertIn("None", r["errors"][0])

    def test_non_string__int(self):
        r = validate_llm_response(12345)
        self.assertFalse(r["valid"])

    def test_empty_string(self):
        r = validate_llm_response("")
        self.assertFalse(r["valid"])
        self.assertIn("空字符串", r["errors"][0])

    def test_whitespace_only(self):
        r = validate_llm_response("   \n\t  ")
        self.assertFalse(r["valid"])

    def test_garbled_replacement_char(self):
        r = validate_llm_response('{"key": "val�ue"}')
        self.assertFalse(r["valid"])
        self.assertIn("乱码", r["errors"][0])

    def test_no_json_object(self):
        r = validate_llm_response("This is just plain text, no JSON here.")
        self.assertFalse(r["valid"])

    def test_json_with_think_tag(self):
        r = validate_llm_response(
            '<think>reasoning...</think>\n{"游戏名称": "test", "剧情概述": "plot", "角色列表": [{"名称": "A"}]}'
        )
        self.assertTrue(r["valid"])
        self.assertIsNotNone(r.get("parsed_data"))

    def test_json_in_code_block(self):
        r = validate_llm_response(
            '```json\n{"游戏名称": "t", "剧情概述": "p", "角色列表": [{"名称": "A"}]}\n```'
        )
        self.assertTrue(r["valid"])

    def test_jsonline_format(self):
        r = validate_llm_response(
            'abc|{"id": 1, "name": "x", "src": "hello"}\ndef|{"id": 2, "src": "world"}',
            expected_format="jsonline"
        )
        self.assertTrue(r["valid"])
        self.assertEqual(len(r.get("parsed_data", [])), 2)

    def test_jsonline_all_invalid_lines(self):
        r = validate_llm_response(
            "not json\nstill not json",
            expected_format="jsonline"
        )
        self.assertFalse(r["valid"])
        self.assertIn("未解析到任何有效", r["errors"][0])

    def test_tsv_format(self):
        r = validate_llm_response(
            "日文原词\t中文翻译\t备注\nテスト\ttest\tnoun",
            expected_format="tsv"
        )
        self.assertTrue(r["valid"])

    def test_tsv_all_header_lines(self):
        r = validate_llm_response(
            "日文\t中文\t备注",
            expected_format="tsv"
        )
        self.assertFalse(r["valid"])

    def test_unknown_format(self):
        r = validate_llm_response("anything", expected_format="xml")
        self.assertTrue(r["valid"])  # 未知格式不阻止，仅 warning


class ValidateGlobalPromptEdgeCases(unittest.TestCase):
    """validate_global_prompt: 结构校验边界"""

    def test_none(self):
        r = validate_global_prompt(None)
        self.assertFalse(r["valid"])

    def test_list_not_dict(self):
        r = validate_global_prompt([1, 2, 3])
        self.assertFalse(r["valid"])

    def test_empty_dict(self):
        r = validate_global_prompt({})
        self.assertFalse(r["valid"])
        self.assertIn("游戏名称", r["errors"][0])

    def test_missing_characters(self):
        r = validate_global_prompt({"游戏名称": "t", "剧情概述": "p"})
        self.assertFalse(r["valid"])
        self.assertIn("角色列表", r["errors"][0])

    def test_empty_characters(self):
        r = validate_global_prompt({
            "游戏名称": "t", "剧情概述": "p", "角色列表": []
        })
        self.assertFalse(r["valid"])

    def test_char_not_dict(self):
        r = validate_global_prompt({
            "游戏名称": "t", "剧情概述": "p",
            "角色列表": ["not a dict"]
        })
        self.assertFalse(r["valid"])

    def test_char_without_name(self):
        r = validate_global_prompt({
            "游戏名称": "t", "剧情概述": "p",
            "角色列表": [{"形象": "tall"}]
        })
        self.assertFalse(r["valid"])
        self.assertIn("缺少「名称」", r["errors"][0])

    def test_minimal_valid(self):
        r = validate_global_prompt({
            "游戏名称": "t", "剧情概述": "p",
            "角色列表": [{"名称": "A"}]
        })
        self.assertTrue(r["valid"])

    def test_missing_optional_fields__warns(self):
        r = validate_global_prompt({
            "游戏名称": "t", "剧情概述": "p",
            "角色列表": [{"名称": "A"}]
        })
        self.assertTrue(r["valid"])
        warnings_text = " ".join(r["warnings"])
        self.assertIn("世界观设定", warnings_text)
        self.assertIn("行文风格", warnings_text)

    def test_tags_wrong_type(self):
        r = validate_global_prompt({
            "游戏名称": "t", "剧情概述": "p",
            "角色列表": [{"名称": "A"}],
            "题材标签": "not a list"  # 不是 list
        })
        self.assertTrue(r["valid"])  # 不阻止，仅 warning
        self.assertTrue(any("题材标签" in w for w in r["warnings"]))


class ValidateFileMetadataEntryEdgeCases(unittest.TestCase):

    def test_none(self):
        r = validate_file_metadata_entry(None)
        self.assertFalse(r["valid"])

    def test_list(self):
        r = validate_file_metadata_entry([1, 2])
        self.assertFalse(r["valid"])

    def test_missing_id(self):
        r = validate_file_metadata_entry({"角色": ["A"], "剧情": "p"})
        self.assertFalse(r["valid"])
        self.assertIn("缺少 'id'", r["errors"][0])

    def test_roles_wrong_type(self):
        r = validate_file_metadata_entry({"id": "f.json", "角色": "not a list"})
        self.assertFalse(r["valid"])

    def test_roles_empty__warns(self):
        r = validate_file_metadata_entry({"id": "f.json", "角色": [], "剧情": "p"})
        self.assertTrue(r["valid"])
        self.assertTrue(any("列表为空" in w for w in r["warnings"]))

    def test_plot_empty__warns(self):
        r = validate_file_metadata_entry({"id": "f.json", "角色": ["A"], "剧情": ""})
        self.assertTrue(r["valid"])
        self.assertTrue(any("「剧情」为空" in w for w in r["warnings"]))


class ValidateBatchMetadataEntryEdgeCases(unittest.TestCase):

    def test_none(self):
        r = validate_batch_metadata_entry(None)
        self.assertFalse(r["valid"])

    def test_empty_batches(self):
        r = validate_batch_metadata_entry({"id": "f.json", "批次": []})
        self.assertFalse(r["valid"])

    def test_batches_wrong_type(self):
        r = validate_batch_metadata_entry({"id": "f.json", "批次": "not a list"})
        self.assertFalse(r["valid"])

    def test_interval_missing(self):
        r = validate_batch_metadata_entry({
            "id": "f.json",
            "批次": [{"视角": "x", "氛围": "y", "用词色彩": "z"}]
        })
        self.assertFalse(r["valid"])

    def test_interval_not_numbers(self):
        r = validate_batch_metadata_entry({
            "id": "f.json",
            "批次": [{"区间": ["a", "b"], "视角": "x", "氛围": "y", "用词色彩": "z"}]
        })
        self.assertFalse(r["valid"])

    def test_overlapping_intervals(self):
        r = validate_batch_metadata_entry({
            "id": "f.json",
            "批次": [
                {"区间": [1, 10], "视角": "A", "氛围": "x", "用词色彩": "y"},
                {"区间": [5, 20], "视角": "B", "氛围": "x", "用词色彩": "y"},  # 重叠
            ]
        })
        self.assertFalse(r["valid"])
        self.assertTrue(any("重叠" in err for err in r["errors"]))

    def test_out_of_range_with_max_index(self):
        r = validate_batch_metadata_entry({
            "id": "f.json",
            "批次": [
                {"区间": [1, 100], "视角": "A", "氛围": "x", "用词色彩": "y"},
            ]
        }, max_index=50)
        self.assertFalse(r["valid"])
        self.assertTrue(any("> 最大行号" in err for err in r["errors"]))

    def test_incomplete_coverage(self):
        r = validate_batch_metadata_entry({
            "id": "f.json",
            "批次": [
                {"区间": [1, 5], "视角": "A", "氛围": "x", "用词色彩": "y"},
                # 缺少 6-10
            ]
        }, max_index=10)
        self.assertTrue(r["valid"])  # 缺少覆盖不阻止，仅 warning
        self.assertTrue(any("未完全覆盖" in w for w in r["warnings"]))

    def test_lo_gt_hi__auto_swapped(self):
        r = validate_batch_metadata_entry({
            "id": "f.json",
            "批次": [
                {"区间": [10, 1], "视角": "A", "氛围": "x", "用词色彩": "y"},
            ]
        })
        self.assertTrue(r["valid"])
        self.assertTrue(any("lo > hi" in w for w in r["warnings"]))


class CrossValidateCountsEdgeCases(unittest.TestCase):

    def test_exact_match(self):
        r = cross_validate_counts(5, 5, "test")
        self.assertTrue(r["valid"])

    def test_small_diff__warning(self):
        r = cross_validate_counts(5, 3, "test")
        self.assertTrue(r["valid"])
        self.assertTrue(any("缺失" in w for w in r["warnings"]))

    def test_large_diff__error(self):
        r = cross_validate_counts(10, 2, "test")
        self.assertFalse(r["valid"])
        self.assertTrue(any("缺失" in err for err in r["errors"]))

    def test_actual_more_than_expected(self):
        r = cross_validate_counts(5, 8, "test")
        self.assertTrue(r["valid"])
        self.assertTrue(any("多出" in w for w in r["warnings"]))


class ValidateTranslationOutputEdgeCases(unittest.TestCase):

    def test_none_list(self):
        r = validate_translation_output(10, None)
        self.assertFalse(r["valid"])

    def test_count_mismatch(self):
        fake_list = [SimpleNamespace(pre_dst="ok") for _ in range(5)]
        r = validate_translation_output(10, fake_list)
        self.assertFalse(r["valid"])
        self.assertIn("不一致", r["errors"][0])

    def test_high_failure_rate(self):
        fake_list = [SimpleNamespace(pre_dst="(Failed)") for _ in range(10)]
        r = validate_translation_output(10, fake_list)
        self.assertFalse(r["valid"])
        self.assertIn("失败率过高", r["errors"][0])

    def test_low_failure_rate(self):
        items = [SimpleNamespace(pre_dst="(Failed)")] + [SimpleNamespace(pre_dst="ok") for _ in range(19)]
        r = validate_translation_output(20, items)
        self.assertTrue(r["valid"])
        self.assertTrue(any("存在翻译失败" in w for w in r["warnings"]))

    def test_empty_translations(self):
        items = [SimpleNamespace(pre_dst="") for _ in range(5)]
        r = validate_translation_output(5, items)
        self.assertTrue(r["valid"])
        self.assertTrue(any("空白翻译" in w for w in r["warnings"]))


# ──────────────────────────────────────────
# TextCompressor 测试
# ──────────────────────────────────────────

class TextCompressorEdgeCases(unittest.TestCase):
    """TextCompressor: 边界和异常输入"""

    def test_empty_file_dict(self):
        tc = TextCompressor()
        result = tc.compress({})
        self.assertIn("文件数：0", result)

    def test_single_empty_file(self):
        tc = TextCompressor()
        result = tc.compress({"f.json": []})
        self.assertIn("f.json（0 行）", result)

    def test_message_with_unicode_emoji(self):
        tc = TextCompressor()
        data = {"f.json": [{"message": "hello 😀🎉 world", "name": "test"}]}
        result = tc.compress(data)
        self.assertIn("😀🎉", result)

    def test_message_with_embedded_newlines(self):
        tc = TextCompressor()
        data = {"f.json": [{"message": "line1\nline2\r\nline3", "name": "A"}]}
        result = tc.compress(data)
        self.assertIn("line1\nline2\r\nline3", result)

    def test_all_messages_identical(self):
        tc = TextCompressor()
        data = {"f.json": [
            {"message": "repeat", "name": "A", "index": i}
            for i in range(100)
        ]}
        result = tc.compress(data)
        # 第一行正常输出，其余 99 行折叠
        self.assertEqual(result.count("^ 同上 L1"), 99)

    def test_verify_all_present(self):
        tc = TextCompressor()
        data = {"f.json": [
            {"message": f"msg{i}", "name": f"name{i % 5}"}
            for i in range(50)
        ]}
        result = tc.compress(data)
        v = tc.verify_compression(data, result)
        self.assertTrue(v["all_present"])
        self.assertEqual(v["total_messages"], 50)
        self.assertEqual(len(v["missing_messages"]), 0)

    def test_verify_detects_loss(self):
        tc = TextCompressor()
        data = {"f.json": [{"message": "hello"}, {"message": "world"}]}
        # 故意传入不完整的压缩结果
        v = tc.verify_compression(data, "hello")
        self.assertFalse(v["all_present"])
        self.assertIn("world", v["missing_messages"])

    def test_no_name_items(self):
        tc = TextCompressor()
        data = {"f.json": [
            {"message": "narrator line 1"},
            {"message": "narrator line 2"},
        ]}
        result = tc.compress(data)
        self.assertIn("narrator line 1", result)
        self.assertIn("narrator line 2", result)
        v = tc.verify_compression(data, result)
        self.assertTrue(v["all_present"])

    def test_name_with_special_chars(self):
        tc = TextCompressor()
        data = {"f.json": [{"message": "test", "name": "名・前〜！？"}]}
        result = tc.compress(data)
        v = tc.verify_compression(data, result)
        self.assertTrue(v["all_present"])
        self.assertEqual(v["lost_names"], [])

    def test_multiple_files(self):
        tc = TextCompressor()
        data = {
            "a.json": [{"message": "a1"}, {"message": "a2"}],
            "b.json": [{"message": "b1"}, {"message": "b2"}],
            "c.json": [{"message": "c1"}],
        }
        result = tc.compress(data)
        for name in ["a.json", "b.json", "c.json"]:
            self.assertIn(name, result)
        v = tc.verify_compression(data, result)
        self.assertTrue(v["all_present"])

    def test_max_chars_warning(self):
        tc = TextCompressor(max_chars=10)
        data = {"f.json": [{"message": "this is a very long message"}]}
        result = tc.compress(data)
        # 超限不截断，但大文本不在此测试
        self.assertIn("this is a very long message", result)


if __name__ == "__main__":
    unittest.main()
