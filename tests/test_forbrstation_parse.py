# -*- coding: utf-8 -*-
"""ForBRStation 换行修复稀疏解析（基类 BaseSparseFixRound._parse_fix_response）的单元测试。

覆盖：
  - decode_json_line_part（utils）：单行 JSON 后尾随垃圾（如 </br>、；）不丢句
  - _parse_fix_response：返回 (success_count, found_count)；无 better 字段时 found=0
  - 多行拼接输入按 id 稀疏解析（id 不连续、顺序无关）
  - better 与当前译文相同仍跳过、不计 found/success 的行为保持不变
"""
import unittest

from GalTransl.CSentense import CSentense
from GalTransl.Backend.ForBRStation import ForBRStation
from GalTransl.Backend.utils import decode_json_line_part


class _FakeOpencc:
    """身份转换替身：_normalize_parsed_translation_text 在中文目标语言下调用 convert。"""

    @staticmethod
    def convert(text):
        return text


class _FakePjConfig:
    """配置替身：_parse_fix_response 仅用 getKey 读取 swap 开关，默认关闭。"""

    def getKey(self, key, default=None):
        return default


def _make_parser(target_lang: str = "Chinese"):
    """绕过重型 __init__，仅装配 _parse_fix_response 所需属性。"""
    obj = object.__new__(ForBRStation)
    obj.target_lang = target_lang
    obj.opencc = _FakeOpencc()
    obj.pj_config = _FakePjConfig()
    return obj


def _trans(index, pre_dst):
    t = CSentense(f"src{index}", index=index)
    t.post_src = f"src{index}"
    t.pre_dst = pre_dst
    t.proofread_zh = ""
    return t


class DecodeJsonPartTests(unittest.TestCase):
    def test_clean_json(self):
        self.assertEqual(
            decode_json_line_part('{"id": 1, "better": "好"}'),
            {"id": 1, "better": "好"},
        )

    def test_trailing_br_garbage(self):
        # 修复 C：模型在 JSON 后误加 </br>，raw_decode 应忽略尾随垃圾
        self.assertEqual(
            decode_json_line_part('{"id": 94, "better": "abc"}</br>'),
            {"id": 94, "better": "abc"},
        )

    def test_trailing_punctuation(self):
        self.assertEqual(
            decode_json_line_part('{"id": 1, "better": "x"}；'),
            {"id": 1, "better": "x"},
        )

    def test_leading_garbage(self):
        # 从首个 { 开始解析，忽略前置思考文字
        self.assertEqual(
            decode_json_line_part('思考：{"id": 1, "better": "y"}'),
            {"id": 1, "better": "y"},
        )

    def test_brace_inside_string_kept(self):
        # better 值内含 } 时不得被错误截断（raw_decode 正确处理字符串内容）
        s = '{"id": 1, "better": "说}话"}'
        self.assertEqual(decode_json_line_part(s), {"id": 1, "better": "说}话"})

    def test_not_json_returns_none(self):
        self.assertIsNone(decode_json_line_part("纯文本没有 JSON"))
        self.assertIsNone(decode_json_line_part(""))


class ParseBrJsonlineTests(unittest.TestCase):
    def test_trailing_br_garbage_fix_applied(self):
        # 修复 C 端到端：00_05 回归场景，better 尾随 </br> 仍应写入 alt_dst
        trans = _trans(94, "也就是说，我得把平时笼罩在神秘面纱下的\r\n布料之下，掀开，触摸，")
        parser = _make_parser()
        line = 'p4c|{"id": 94, "better": "也就是说，我得把平时笼罩在神秘面纱下的布料之下，掀开，触摸，"}</br>'
        success, found = parser._parse_fix_response(line, [trans], "\r\n")
        self.assertEqual(found, 1)
        self.assertEqual(success, 1)
        self.assertEqual(
            trans.alt_dst,
            "也就是说，我得把平时笼罩在神秘面纱下的布料之下，掀开，触摸，",
        )

    def test_multiple_jsonlines_sparse_by_id(self):
        # 修复 A 合并多行后按 id 稀疏解析；id 不连续、顺序无关
        trans_list = [_trans(3, "a"), _trans(12, "b"), _trans(14, "c")]
        parser = _make_parser()
        text = "\n".join(
            [
                'tma|{"id": 3, "better": "a1"}',
                '1mj|{"id": 12, "better": "b1"}',
                'tc7|{"id": 14, "better": "c1"}',
            ]
        )
        success, found = parser._parse_fix_response(text, trans_list, "\r\n")
        self.assertEqual(found, 3)
        self.assertEqual(success, 3)
        self.assertEqual(trans_list[0].alt_dst, "a1")
        self.assertEqual(trans_list[1].alt_dst, "b1")
        self.assertEqual(trans_list[2].alt_dst, "c1")

    def test_missing_better_field_yields_zero_found(self):
        # 修复 B：模型回显 {id, dst}（无 better）→ found_count=0，供上报判断
        trans = _trans(1, "旧译文")
        parser = _make_parser()
        text = 'nnk|{"id": 1, "dst": "旧译文"}'
        success, found = parser._parse_fix_response(text, [trans], "\r\n")
        self.assertEqual(found, 0)
        self.assertEqual(success, 0)
        self.assertEqual(trans.alt_dst, "")  # 未写入 alt_dst，保持默认空串

    def test_better_identical_to_current_skipped_but_found(self):
        # better 与当前译文相同：found_count 仍计（模型确实给了 better），success 为 0
        trans = _trans(3, "一边给帮忙维持秩序的会场工作人员\r\n递上准备好的饮料，")
        parser = _make_parser()
        text = 'tma|{"id": 3, "better": "一边给帮忙维持秩序的会场工作人员<br>递上准备好的饮料，"}'
        success, found = parser._parse_fix_response(text, [trans], "\r\n")
        self.assertEqual(found, 1)
        self.assertEqual(success, 0)
        self.assertEqual(trans.alt_dst, "")  # 相同则跳过，不写 alt_dst


if __name__ == "__main__":
    unittest.main()
