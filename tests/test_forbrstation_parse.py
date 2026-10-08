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
from GalTransl.Backend.utils import decode_json_line_part, encode_line_breaks_for_prompt


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

    def test_better_real_newline_vs_placeholder_is_same(self):
        # 比较键折叠真实换行与占位符：缓存真实 \n，模型回 <br> → 视为相同，跳过
        trans = _trans(4, "甲\n乙")
        parser = _make_parser()
        success, found = parser._parse_fix_response(
            'abc|{"id": 4, "better": "甲<br>乙"}', [trans], "\n"
        )
        self.assertEqual(found, 1)
        self.assertEqual(success, 0)
        self.assertEqual(trans.alt_dst, "")

    def test_better_real_newline_vs_literal_text_is_change(self):
        # 刻意不折叠字面 \n 文本（可能是正文，如路径 C:\new）：此处应判为真实改动，
        # 与「宽口径折叠」实现相比这是本用例的区分点（宽折叠会误判相同而不写 alt_dst）
        trans = _trans(5, "路径 C:\\new 结束")
        parser = _make_parser()
        # JSON 文本里的 \\n 解码后是真实换行，与被测的「字面 \n 正文」形成对比
        success, found = parser._parse_fix_response(
            'abc|{"id": 5, "better": "路径 C:\\new 结束"}', [trans], "\n"
        )
        self.assertEqual(found, 1)
        self.assertEqual(success, 1)
        self.assertEqual(trans.alt_dst, "路径 C:\new 结束")

    def test_echo_of_literal_newline_source_is_rejected(self):
        # 回归：字面 \n 约定项目里，src 在提示词中是 <br>，模型整句回显也就是 <br>。
        # 回显守卫必须先把 post_src 按源符号编码成 <br> 再归一后比较——若直接用
        # canonicalize_newlines（不折字面文本），守卫会永久失效，回显的日文原文
        # 会被当成有效修复写入 alt_dst（swapFixToCurrent 时还会直接覆盖译文）
        trans = _trans(6, "旧译文")
        trans.post_src = "甲\\n乙"
        parser = _make_parser()
        success, found = parser._parse_fix_response(
            'abc|{"id": 6, "better": "甲<br>乙"}', [trans], "\\n"
        )
        self.assertEqual(found, 1)
        self.assertEqual(success, 0)
        self.assertEqual(trans.alt_dst, "")

    def test_echo_of_literal_crlf_source_is_rejected(self):
        # 同上，字面 \r\n 约定（四类中的另一类）
        trans = _trans(7, "旧译文")
        trans.post_src = "甲\\r\\n乙"
        parser = _make_parser()
        success, found = parser._parse_fix_response(
            'abc|{"id": 7, "better": "甲<br>乙"}', [trans], "\\r\\n"
        )
        self.assertEqual(found, 1)
        self.assertEqual(success, 0)
        self.assertEqual(trans.alt_dst, "")

    def test_echo_rejected_when_opencc_rewrites_source_chars(self):
        # 回归：normalized 会过 opencc（説→说、時間→时间…），src 侧若拿原始日文比较
        # 则两侧永不相等 → 多行回显漏判并写进 alt_dst。守卫必须两侧都取原始文本
        # 过同一个提示词编码入口
        class _RewritingOpencc:
            @staticmethod
            def convert(text):
                return text.replace("説", "说").replace("時間", "时间")

        trans = _trans(8, "旧译文")
        trans.post_src = "彼女は説明した。\nそう言った。"
        parser = _make_parser()
        parser.opencc = _RewritingOpencc()
        # 模型回显：src 在提示词里是 <br>，回显也是 <br>
        success, found = parser._parse_fix_response(
            'abc|{"id": 8, "better": "彼女は説明した。<br>そう言った。"}', [trans], "\n"
        )
        self.assertEqual(found, 1)
        self.assertEqual(success, 0)
        self.assertEqual(trans.alt_dst, "")

    def test_echo_with_crlf_against_lf_source_is_rejected(self):
        # 回归：源为真实 LF（n_symbol="\n"），模型回显改用 CRLF 写法。编码入口必须先折
        # 真实换行再按源符号替换，否则 CRLF 会被拆成「裸 \r + <br>」，裸 \r 无法回认
        trans = _trans(10, "旧译文")
        trans.post_src = "甲\n乙"
        parser = _make_parser()
        success, found = parser._parse_fix_response(
            'abc|{"id": 10, "better": "甲\\r\\n乙"}', [trans], "\n"
        )
        self.assertEqual(found, 1)
        self.assertEqual(success, 0)
        self.assertEqual(trans.alt_dst, "")

    def test_echo_of_br_convention_source_is_rejected(self):
        # n_symbol 为空分支：源无四类换行、以字面 <br> 断行；模型回显用变体写法 <BR>，
        # 精确比较拦不住，必须由提示词编码键拦下（若入口对空符号做了 replace("", x)，
        # 会在每个字符间插 <br>，本用例会失败）
        trans = _trans(11, "旧译文")
        trans.post_src = "甲<br>乙"
        parser = _make_parser()
        success, found = parser._parse_fix_response(
            'abc|{"id": 11, "better": "甲<BR>乙"}', [trans], ""
        )
        self.assertEqual(found, 1)
        self.assertEqual(success, 0)
        self.assertEqual(trans.alt_dst, "")

    def test_empty_symbol_entry_does_not_insert_placeholders(self):
        # 直接守护 encode 的空符号分支（键比较路径的另一半）
        self.assertEqual(encode_line_breaks_for_prompt("原文单行", ""), "原文单行")
        self.assertNotIn("<br>", encode_line_breaks_for_prompt("ab", ""))
        self.assertEqual(encode_line_breaks_for_prompt("a\tb\nc", ""), "a[t]b<br>c")

    def test_kanji_only_source_rewritten_by_opencc_is_treated_as_echo(self):
        # 已知窄边界（既定语义）：源为「源文过同一归一管线的结果」时判回显而跳过。
        # 此处 準備完了（无假名）经 opencc t2s 即 准备完了，故合法的简中 better 被跳过；
        # 代价仅是少一条备选建议（写入的 alt_dst 本身也等于归一后源文，非可用译文）
        class _T2sOpencc:
            @staticmethod
            def convert(text):
                return text.replace("準備", "准备")

        trans = _trans(12, "旧译文")
        trans.post_src = "準備完了"
        parser = _make_parser()
        parser.opencc = _T2sOpencc()
        success, found = parser._parse_fix_response(
            'abc|{"id": 12, "better": "准备完了"}', [trans], ""
        )
        self.assertEqual(found, 1)
        self.assertEqual(success, 0)
        self.assertEqual(trans.alt_dst, "")


if __name__ == "__main__":
    unittest.main()
