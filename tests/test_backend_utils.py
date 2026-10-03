# -*- coding: utf-8 -*-
"""Backend/utils.py 公共工具函数单元测试。"""

import unittest

from GalTransl.Backend.utils import (
    coerce_bool,
    coerce_h_value,
    decode_json_line_part,
    extract_json_object,
    is_h_value,
    preprocess_jsonline_response,
    select_paths_by_filter,
)


class CoerceBoolTests(unittest.TestCase):
    def test_bool_passthrough(self) -> None:
        self.assertIs(coerce_bool(True), True)
        self.assertIs(coerce_bool(False), False)

    def test_int_float(self) -> None:
        self.assertIs(coerce_bool(1), True)
        self.assertIs(coerce_bool(0), False)
        self.assertIs(coerce_bool(0.0), False)
        self.assertIs(coerce_bool(2), True)

    def test_truthy_strings(self) -> None:
        for s in ("true", "True", "1", "yes", "on", "是", "y"):
            self.assertIs(coerce_bool(s), True, s)

    def test_falsy_strings(self) -> None:
        for s in ("false", "0", "no", "off", "否", "n", ""):
            self.assertIs(coerce_bool(s), False, s)

    def test_unknown_string_uses_default(self) -> None:
        self.assertIs(coerce_bool("垃圾文本"), False)
        self.assertIs(coerce_bool("垃圾文本", default=True), True)

    def test_other_types_use_default(self) -> None:
        self.assertIs(coerce_bool(None), False)
        self.assertIs(coerce_bool([1], default=True), True)


class ExtractJsonObjectTests(unittest.TestCase):
    def test_plain_json(self) -> None:
        self.assertEqual(extract_json_object('{"a": 1}'), {"a": 1})

    def test_think_prefix_stripped(self) -> None:
        self.assertEqual(extract_json_object('思考过程</think>{"a": 1}'), {"a": 1})

    def test_code_block_extracted(self) -> None:
        self.assertEqual(extract_json_object('```json\n{"a": 1}\n```'), {"a": 1})

    def test_multi_code_blocks_default_takes_first(self) -> None:
        text = '```json\n{"a": 1}\n```\n```json\n{"b": 2}\n```'
        self.assertEqual(extract_json_object(text), {"a": 1})

    def test_garbage_around_json(self) -> None:
        self.assertEqual(extract_json_object('前言 {"a": 1} 后记'), {"a": 1})

    def test_not_dict_returns_none(self) -> None:
        self.assertIsNone(extract_json_object("[1, 2]"))

    def test_no_brace_returns_none(self) -> None:
        self.assertIsNone(extract_json_object("纯文本"))

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(extract_json_object(""))
        self.assertIsNone(extract_json_object("   "))

    def test_invalid_json_returns_none(self) -> None:
        self.assertIsNone(extract_json_object('{"a": }'))


class PreprocessJsonlineResponseTests(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(preprocess_jsonline_response(""), "")

    def test_think_stripped(self) -> None:
        out = preprocess_jsonline_response("思考</think>a1b|{\"id\":1}")
        self.assertNotIn("思考", out)

    def test_code_blocks_merged_by_default(self) -> None:
        out = preprocess_jsonline_response(
            '```json\na1b|{"id":1}\n```\n```json\nc2d|{"id":2}\n```'
        )
        self.assertIn('"id":1', out)
        self.assertIn('"id":2', out)

    def test_code_blocks_first_only_when_disabled(self) -> None:
        out = preprocess_jsonline_response(
            '```json\na1b|{"id":1}\n```\n```json\nc2d|{"id":2}\n```',
            merge_code_blocks=False,
        )
        self.assertIn('"id":1', out)
        self.assertNotIn('"id":2', out)

    def test_anchor_slice(self) -> None:
        out = preprocess_jsonline_response("前导文字\na1b|{\"id\":1}\n")
        self.assertTrue(out.startswith("a1b|"), out)

    def test_empty_code_blocks_normalized_to_empty(self) -> None:
        # 修复/检测轮约定「无命中输出空代码块」：无论规范还是残缺形式，
        # 空代码块都应归一到空串，避免残留围栏被误判为「有内容但 0 命中」
        F = "```"
        for raw in (
            F + "jsonline\n\n" + F,  # 规范空块（内容空行）
            F + "jsonline\n" + F,  # 无闭合换行的残缺空块
            F + "jsonline " + F,  # 同行空块
            F + "\n\n" + F,  # 无语言标签空块
            F + "\n" + F,  # 无语言标签残缺空块
            "检查完毕\n" + F + "jsonline\n\n" + F,  # 块外文字 + 规范空块
        ):
            with self.subTest(raw=raw):
                self.assertEqual(preprocess_jsonline_response(raw), "")

    def test_inline_code_block_with_content_kept(self) -> None:
        # 同行代码块内含真实内容：去除围栏后非空，仍应正常解析
        out = preprocess_jsonline_response('```jsonline a1b|{"id":1} ```')
        self.assertIn('"id":1', out)


class DecodeJsonLinePartTests(unittest.TestCase):
    def test_clean(self) -> None:
        self.assertEqual(decode_json_line_part('{"id":1}'), {"id": 1})

    def test_trailing_garbage(self) -> None:
        self.assertEqual(
            decode_json_line_part('{"id":1,"better":"x"}</br>；'),
            {"id": 1, "better": "x"},
        )

    def test_leading_garbage(self) -> None:
        self.assertEqual(decode_json_line_part('思考 {"id":1}'), {"id": 1})

    def test_not_dict_returns_none(self) -> None:
        self.assertIsNone(decode_json_line_part("[1,2]"))

    def test_invalid_returns_none(self) -> None:
        self.assertIsNone(decode_json_line_part("no json here"))
        self.assertIsNone(decode_json_line_part(""))


class CoerceHValueTests(unittest.TestCase):
    def test_bool_maps_to_extremes(self) -> None:
        self.assertEqual(coerce_h_value(True), 1.0)
        self.assertEqual(coerce_h_value(False), 0.0)

    def test_number_clamped_to_unit(self) -> None:
        self.assertEqual(coerce_h_value(0.3), 0.3)
        self.assertEqual(coerce_h_value(0.6), 0.6)
        self.assertEqual(coerce_h_value(-1), 0.0)
        self.assertEqual(coerce_h_value(2.0), 1.0)

    def test_string_forms(self) -> None:
        self.assertEqual(coerce_h_value("true"), 1.0)
        self.assertEqual(coerce_h_value("false"), 0.0)
        self.assertEqual(coerce_h_value("是"), 1.0)
        self.assertEqual(coerce_h_value("否"), 0.0)
        self.assertEqual(coerce_h_value("0.75"), 0.75)
        self.assertEqual(coerce_h_value("0.5"), 0.5)

    def test_string_overrange_clamped(self) -> None:
        self.assertEqual(coerce_h_value("1.5"), 1.0)
        self.assertEqual(coerce_h_value("-0.2"), 0.0)

    def test_unknown_uses_default(self) -> None:
        self.assertEqual(coerce_h_value(None), 0.0)
        self.assertEqual(coerce_h_value([], default=0.3), 0.3)
        self.assertEqual(coerce_h_value("垃圾", default=0.2), 0.2)

    def test_is_h_value_threshold(self) -> None:
        # 兼容口径：h >= 0.5 视为 H 场景
        self.assertTrue(is_h_value(0.5))
        self.assertTrue(is_h_value(0.75))
        self.assertTrue(is_h_value(True))
        self.assertFalse(is_h_value(0.25))
        self.assertFalse(is_h_value(0.0))
        self.assertFalse(is_h_value(False))
        self.assertFalse(is_h_value(None))


class SelectPathsByFilterTests(unittest.TestCase):
    """三级精确匹配 + NFKC 归一兜底（全角/半角键名对撞修复）。"""

    FULLWIDTH_TEXTS = {
        "/p/０１＿共通＿０１＿０１.json": "文本",
        "/p/フリー拠点イベント01＿01.json": "文本",
        "/p/マガヒメ_０１.json": "文本",
    }

    def test_no_filter_returns_all(self) -> None:
        paths = list(self.FULLWIDTH_TEXTS)
        self.assertEqual(select_paths_by_filter(paths, None), paths)
        self.assertEqual(select_paths_by_filter(paths, []), paths)

    def test_exact_hit_without_fallback(self) -> None:
        paths = list(self.FULLWIDTH_TEXTS)
        result = select_paths_by_filter(paths, ["/p/マガヒメ_０１.json"])
        self.assertEqual(result, ["/p/マガヒメ_０１.json"])

    def test_fullwidth_basename_matched_via_nfkc(self) -> None:
        # 路线图键为半角，真实文件名为全角：数字+下划线整宽度差异
        result = select_paths_by_filter(
            list(self.FULLWIDTH_TEXTS), ["01_共通_01_01.json"]
        )
        self.assertEqual(result, ["/p/０１＿共通＿０１＿０１.json"])

    def test_mixed_width_matched_via_nfkc(self) -> None:
        # 半角数字+全角下划线的混合写法（NFKC 兜底需两种都覆盖）
        result = select_paths_by_filter(
            list(self.FULLWIDTH_TEXTS), ["フリー拠点イベント01_01.json"]
        )
        self.assertEqual(result, ["/p/フリー拠点イベント01＿01.json"])

    def test_nfkc_stem_fallback(self) -> None:
        # 兜底第 4 级也支持去扩展名写法
        result = select_paths_by_filter(list(self.FULLWIDTH_TEXTS), ["01_共通_01_01"])
        self.assertEqual(result, ["/p/０１＿共通＿０１＿０１.json"])

    def test_exact_match_takes_precedence(self) -> None:
        # 全角/半角文件并存时，精确命中优先，不经 NFKC 兜底
        paths = ["/p/ａ_01.json", "/p/a_01.json"]
        result = select_paths_by_filter(paths, ["a_01.json"])
        self.assertEqual(result, ["/p/a_01.json"])

    def test_nfkc_collision_build_warns(self) -> None:
        # 两个真实文件 NFKC 归一后同名：构建兜底索引时记 warning
        paths = ["/p/０１.json", "/p/01.json"]
        with self.assertLogs(level="WARNING") as logs:
            result = select_paths_by_filter(paths, ["ghost.json"], tag="T")
        self.assertEqual(result, [])
        self.assertTrue(any("NFKC 归一后同名" in m for m in logs.output))

    def test_unmatched_key_still_reported(self) -> None:
        with self.assertLogs(level="WARNING") as logs:
            result = select_paths_by_filter(
                list(self.FULLWIDTH_TEXTS), ["ghost.json"], tag="T"
            )
        self.assertEqual(result, [])
        self.assertTrue(any("ghost.json" in m for m in logs.output))


if __name__ == "__main__":
    unittest.main()
