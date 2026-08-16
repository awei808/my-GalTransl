# -*- coding: utf-8 -*-
"""Backend/utils.py 公共工具函数单元测试。"""

import unittest

from GalTransl.Backend.utils import (
    coerce_bool,
    decode_json_line_part,
    extract_json_object,
    preprocess_jsonline_response,
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


if __name__ == "__main__":
    unittest.main()
