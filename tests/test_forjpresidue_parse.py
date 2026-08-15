# -*- coding: utf-8 -*-
"""ForJPResidue 残留日文修复解析逻辑的单元测试。

覆盖：
  - 尾随垃圾（如 </br>、；）不丢句（_decode_json_part 容错）
  - 返回 (success_count, found_count)；无 better 字段时 found_count=0
  - 多行拼接输入按 id 稀疏解析（id 不连续、顺序无关）
  - better 与当前译文相同仍跳过、不计 success 的行为
  - 输入携带 src、不携带 problem 的口径（解析仅按 id 定位，与 problem 无关）
"""
import unittest

from GalTransl.CSentense import CSentense
from GalTransl.Backend.ForJPResidue import ForJPResidue


class _FakeOpencc:
    """身份转换替身：_normalize_parsed_translation_text 在中文目标语言下调用 convert。"""

    @staticmethod
    def convert(text):
        return text


def _make_parser(target_lang: str = "Chinese (Simplified)"):
    """绕过重型 __init__，仅装配 _parse_jp_jsonline_text 所需属性。"""
    obj = object.__new__(ForJPResidue)
    obj.target_lang = target_lang
    obj.opencc = _FakeOpencc()
    return obj


def _trans(index, pre_dst, src=None):
    t = CSentense(f"src{index}", index=index)
    t.post_src = src if src is not None else f"src{index}"
    t.pre_dst = pre_dst
    t.proofread_zh = ""
    return t


class DecodeJsonPartTests(unittest.TestCase):
    def test_clean_json(self):
        self.assertEqual(
            ForJPResidue._decode_json_part('{"id": 1, "better": "好"}'),
            {"id": 1, "better": "好"},
        )

    def test_trailing_br_garbage(self):
        # 模型在 JSON 后误加 </br>，raw_decode 应忽略尾随垃圾
        self.assertEqual(
            ForJPResidue._decode_json_part('{"id": 94, "better": "abc"}</br>'),
            {"id": 94, "better": "abc"},
        )

    def test_trailing_punctuation(self):
        self.assertEqual(
            ForJPResidue._decode_json_part('{"id": 1, "better": "x"}；'),
            {"id": 1, "better": "x"},
        )

    def test_leading_garbage(self):
        # 从首个 { 开始解析，忽略前置思考文字
        self.assertEqual(
            ForJPResidue._decode_json_part('思考：{"id": 1, "better": "y"}'),
            {"id": 1, "better": "y"},
        )

    def test_brace_inside_string_kept(self):
        # better 值内含 } 时不得被错误截断
        s = '{"id": 1, "better": "说}话"}'
        self.assertEqual(
            ForJPResidue._decode_json_part(s), {"id": 1, "better": "说}话"}
        )

    def test_not_json_returns_none(self):
        self.assertIsNone(ForJPResidue._decode_json_part("纯文本没有 JSON"))
        self.assertIsNone(ForJPResidue._decode_json_part(""))


class ParseJpJsonlineTests(unittest.TestCase):
    def test_trailing_br_garbage_fix_applied(self):
        # 残留日文修复：better 尾随 </br> 仍应写入 alt_dst
        trans = _trans(94, "她像ノシ一样笑了。", src="彼女はノシと笑った")
        parser = _make_parser()
        line = 'p4c|{"id": 94, "better": "她像诺西一样笑了。"}</br>'
        success, found = parser._parse_jp_jsonline_text(line, [trans], "<br>")
        self.assertEqual(found, 1)
        self.assertEqual(success, 1)
        self.assertEqual(trans.alt_dst, "她像诺西一样笑了。")

    def test_multiple_jsonlines_sparse_by_id(self):
        # 多行合并后按 id 稀疏解析；id 不连续、顺序无关
        trans_list = [_trans(3, "a"), _trans(12, "b"), _trans(14, "c")]
        parser = _make_parser()
        text = "\n".join(
            [
                'tma|{"id": 3, "better": "a1"}',
                '1mj|{"id": 12, "better": "b1"}',
                'tc7|{"id": 14, "better": "c1"}',
            ]
        )
        success, found = parser._parse_jp_jsonline_text(text, trans_list, "<br>")
        self.assertEqual(found, 3)
        self.assertEqual(success, 3)
        self.assertEqual(trans_list[0].alt_dst, "a1")
        self.assertEqual(trans_list[1].alt_dst, "b1")
        self.assertEqual(trans_list[2].alt_dst, "c1")

    def test_missing_better_field_yields_zero_found(self):
        # 模型回显 {id, dst}（无 better）→ found_count=0，供上报判断
        trans = _trans(1, "旧译文")
        parser = _make_parser()
        text = 'nnk|{"id": 1, "dst": "旧译文"}'
        success, found = parser._parse_jp_jsonline_text(text, [trans], "<br>")
        self.assertEqual(found, 0)
        self.assertEqual(success, 0)
        self.assertEqual(trans.alt_dst, "")

    def test_better_identical_to_current_skipped_but_found(self):
        # better 与当前译文相同：found_count 仍计，success 为 0
        trans = _trans(3, "读书。", src="本を読む")
        parser = _make_parser()
        text = 'tma|{"id": 3, "better": "读书。"}'
        success, found = parser._parse_jp_jsonline_text(text, [trans], "<br>")
        self.assertEqual(found, 1)
        self.assertEqual(success, 0)
        self.assertEqual(trans.alt_dst, "")

    def test_no_problem_key_in_input_not_required(self):
        # 解析仅按 id 定位；输入虽不携带 problem，但只要 id 命中即修复
        trans = _trans(7, "魔法を使う", src="魔法を使う")
        parser = _make_parser()
        text = 'abc|{"id": 7, "better": "使用魔法"}'
        success, found = parser._parse_jp_jsonline_text(text, [trans], "<br>")
        self.assertEqual(found, 1)
        self.assertEqual(success, 1)
        self.assertEqual(trans.alt_dst, "使用魔法")


if __name__ == "__main__":
    unittest.main()
