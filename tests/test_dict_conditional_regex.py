# -*- coding: utf-8 -*-
"""条件判断词 re: 正则与旧式 1^/^^ 前缀等价正则的单元测试。

验证：
  - IfWord / _parse_cond_items 的 re: 判断词解析（!re: 取反可组合；>/<> 组合回退字面量）；
  - do_replace 条件行正则判断端到端（命中/未命中/取反/与正则搜索词组合）；
  - parse_dict_line 条件项 is_regex/regex_error 结构化标记与序列化往返；
  - 旧式 1^/^^ 前缀与等价 re: 正则写法的 A/B 替换结果一致性（锁定前端转换契约，
    文件层转义：替换列 \\1 落盘为双反斜杠，经 _safe_escape 解码后才是 re.sub 模板 \\1）；
  - load_dic 对旧式前缀词条的每文件聚合弃用告警。
"""
import os
import tempfile
import unittest

from GalTransl.Dictionary import (
    CGptDict,
    CNormalDic,
    IfWord,
    _is_legacy_prefix,
    _parse_cond_items,
    _serialize_cond_item,
)


def _write_dic(tmp: str, name: str, content: str) -> str:
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _tran(**attrs):
    """do_replace 条件分支所需的最小句子桩（含对话格式字段）。"""
    base = {
        "pre_src": "", "post_src": "", "pre_dst": "", "post_dst": "",
        "is_dialogue": True, "left_symbol": "", "dia_format": "", "mono_format": "",
    }
    base.update(attrs)
    return type("T", (), base)()


class IfWordRegexTests(unittest.TestCase):
    """IfWord 的 re: 判断词解析与回退"""

    def test_regex_condition_parsed(self) -> None:
        w = IfWord("re:[あ-ん]+")
        self.assertTrue(w.is_regex)
        self.assertIsNotNone(w.regex_pattern)
        self.assertEqual(w.word, "[あ-ん]+")  # word 保留模式主体（不含 re:）
        self.assertFalse(w.without_flag)

    def test_negate_composes_with_regex(self) -> None:
        w = IfWord("!re:ねこ")
        self.assertTrue(w.is_regex)
        self.assertTrue(w.without_flag)
        self.assertEqual(w.word, "ねこ")

    def test_startswith_flag_with_regex_falls_back(self) -> None:
        with self.assertLogs("GalTransl", level="WARNING"):
            w = IfWord(">re:ねこ")
        self.assertFalse(w.is_regex)
        self.assertIsNone(w.regex_pattern)
        self.assertTrue(w.startswith_flag)  # 保留已有标志，降级为字面量 startswith
        self.assertEqual(w.word, "ねこ")

    def test_invalid_regex_falls_back(self) -> None:
        with self.assertLogs("GalTransl", level="WARNING"):
            w = IfWord("re:[")
        self.assertFalse(w.is_regex)
        self.assertEqual(w.word, "[")

    def test_zero_width_regex_falls_back(self) -> None:
        with self.assertLogs("GalTransl", level="WARNING") as logs:
            w = IfWord("re:a*")
        self.assertFalse(w.is_regex)
        self.assertEqual(w.word, "a*")
        # 告警文案与解析侧口径一致（不输出原始 zero-width token）
        self.assertTrue(any("正则可匹配空串" in o.getMessage() for o in logs.records))

    def test_literal_condition_unaffected(self) -> None:
        w = IfWord(">ねこ")
        self.assertFalse(w.is_regex)
        self.assertIsNone(w.regex_pattern)
        self.assertEqual(w.word, "ねこ")


class ParseCondRegexTests(unittest.TestCase):
    """_parse_cond_items 的 is_regex/regex_error 标记与序列化往返"""

    def test_regex_item_flagged(self) -> None:
        items, _ = _parse_cond_items("re:ねこ+")
        self.assertTrue(items[0].is_regex)
        self.assertEqual(items[0].regex_error, "")
        # word 保留 re: 前缀，保证 _serialize_cond_item 往返还原原行
        self.assertEqual(items[0].word, "re:ねこ+")

    def test_negated_regex_item(self) -> None:
        items, _ = _parse_cond_items("!re:ねこ")
        self.assertTrue(items[0].is_regex)
        self.assertTrue(items[0].negate)

    def test_flag_combo_regex_item_reports_error(self) -> None:
        items, _ = _parse_cond_items(">re:ねこ")
        self.assertTrue(items[0].is_regex)
        self.assertIn(">/<>", items[0].regex_error)

    def test_invalid_and_zero_width_report_error(self) -> None:
        self.assertNotEqual(_parse_cond_items("re:[")[0][0].regex_error, "")
        self.assertEqual(_parse_cond_items("re:a*")[0][0].regex_error, "正则可匹配空串")

    def test_literal_item_unflagged(self) -> None:
        items, _ = _parse_cond_items("!ねこ")
        self.assertFalse(items[0].is_regex)
        self.assertEqual(items[0].regex_error, "")

    def test_serialize_regex_roundtrip(self) -> None:
        for raw in ["re:ねこ+", "!re:ねこ", ">re:ねこ", "re:ねこ<"]:
            items, _ = _parse_cond_items(raw)
            self.assertEqual(_serialize_cond_item(items[0]), raw)


class ConditionalRegexReplaceTests(unittest.TestCase):
    """条件行正则判断词的 do_replace 端到端"""

    def _dic_with_cond(self, line: str) -> CNormalDic:
        tmp = tempfile.mkdtemp()
        path = _write_dic(tmp, "t.txt", line + "\n")
        return CNormalDic([path])

    def test_regex_condition_replaces_on_hit(self) -> None:
        dic = self._dic_with_cond("post_src|re:ね[こ猫]|犬|狗")
        tran = _tran(post_src="吾輩はねこである", is_dialogue=False)
        self.assertEqual(dic.do_replace("犬である", tran), "狗である")

    def test_regex_condition_skips_on_miss(self) -> None:
        dic = self._dic_with_cond("post_src|re:ね[こ猫]|犬|狗")
        tran = _tran(post_src="吾輩は鳥である", is_dialogue=False)
        self.assertEqual(dic.do_replace("犬である", tran), "犬である")

    def test_negated_regex_condition(self) -> None:
        dic = self._dic_with_cond("post_src|!re:ね[こ猫]|犬|狗")
        tran = _tran(post_src="吾輩は鳥である", is_dialogue=False)
        self.assertEqual(dic.do_replace("犬である", tran), "狗である")

    def test_regex_condition_with_regex_search(self) -> None:
        # 判断词正则与搜索词正则可组合
        dic = self._dic_with_cond("post_src|re:ね[こ猫]|re:ワ+ン|ワンX")
        tran = _tran(post_src="ねこが鳴く", is_dialogue=False)
        self.assertEqual(dic.do_replace("ワンワンだ", tran), "ワンXワンXだ")

    def test_regex_condition_flag_combo_falls_back_literal(self) -> None:
        # >re: 组合引擎侧回退字面量：按「以…开头 + 字面量模式主体」降级命中
        dic = self._dic_with_cond("post_src|>re:ねこ|犬|狗")
        tran = _tran(post_src="ねこだ", is_dialogue=False)
        self.assertEqual(dic.do_replace("犬である", tran), "狗である")
        tran2 = _tran(post_src="あねこだ", is_dialogue=False)
        self.assertEqual(dic.do_replace("犬である", tran2), "犬である")


class LegacyPrefixEquivalenceTests(unittest.TestCase):
    """旧式 1^/^^ 前缀与等价 re: 正则写法的 A/B 替换一致性（锁定前端转换契约）"""

    def _do(self, lines: list, text: str) -> str:
        tmp = tempfile.mkdtemp()
        path = _write_dic(tmp, "t.txt", "\n".join(lines) + "\n")
        return CNormalDic([path]).do_replace(text, _tran(is_dialogue=False))

    def _assert_equivalent(self, old_line: str, new_line: str, text: str) -> None:
        old = self._do([old_line], text)
        new = self._do([new_line], text)
        self.assertEqual(
            old, new,
            f"旧行 {old_line!r} 与新行 {new_line!r} 在输入 {text!r} 上结果不一致",
        )

    def test_onetime_quote_conditional_line(self) -> None:
        # 随包字典 00通用字典_符号_译后 的 1^" 条件行（首个引号替换，第二个保留）
        self._assert_equivalent(
            'post_jp|「[or]!"|1^"|「',
            'post_jp|「[or]!"|re:(?s)^(.*?)"|\\\\1「',
            'あ"い"う',
        )

    def test_onetime_normal_line(self) -> None:
        self._assert_equivalent("1^ねこ|猫", "re:(?s)^(.*?)ねこ|\\\\1猫", "あねこいねこ")

    def test_onetime_across_newline_equivalent(self) -> None:
        # (?s) 使惰性前缀可跨行：首个出现在换行之后时仍与 str.replace 首个语义一致
        self._assert_equivalent(
            "1^ねこ|猫", "re:(?s)^(.*?)ねこ|\\\\1猫", "あ\nねこいねこ",
        )

    def test_onetime_with_regex_special_chars(self) -> None:
        # C++ 含正则特殊字符，转换时需转义
        self._assert_equivalent("1^C++|加", "re:(?s)^(.*?)C\\+\\+|\\\\1加", "aC++bC++")

    def test_onetime_regex_combo_wrapped_group(self) -> None:
        # 1^re: 组合转换时模式以 (?:…) 包裹，避免 ^ 前缀/惰性前缀与选择符结合
        self._assert_equivalent("1^re:b+|X", "re:(?s)^(.*?)(?:b+)|\\\\1X", "xbbx")

    def test_startswith_line(self) -> None:
        self._assert_equivalent("^^ハロー|你好", "re:^ハロー|你好", "ハローワールドハロー")

    def test_startswith_regex_combo(self) -> None:
        self._assert_equivalent("^^re:A+|X", "re:^(?:A+)|X", "AABA")


class LegacyPrefixDeprecationWarningTests(unittest.TestCase):
    """load_dic 对旧式前缀词条的每文件聚合弃用告警"""

    def test_normal_dic_warns_once_per_file(self) -> None:
        tmp = tempfile.mkdtemp()
        path = _write_dic(tmp, "t.txt", "1^ねこ|猫\n^^いぬ|狗\n普通|词条\n")
        with self.assertLogs("GalTransl", level="WARNING") as logs:
            dic = CNormalDic([path])
        self.assertEqual(len(dic.dic_list), 3)
        warnings = [o.getMessage() for o in logs.records if "旧式" in o.getMessage()]
        self.assertEqual(len(warnings), 1)
        self.assertIn("2 条旧式 1^/^^", warnings[0])

    def test_normal_dic_no_warning_without_legacy(self) -> None:
        tmp = tempfile.mkdtemp()
        path = _write_dic(tmp, "t.txt", "re:^(.*?)ねこ|\\\\1猫\n普通|词条\n")
        with self.assertNoLogs("GalTransl", level="WARNING"):
            CNormalDic([path])

    def test_gpt_dict_warns_prefix_has_no_effect(self) -> None:
        tmp = tempfile.mkdtemp()
        path = _write_dic(tmp, "g.txt", "1^ねこ|猫\n")
        with self.assertLogs("GalTransl", level="WARNING") as logs:
            CGptDict([path])
        warnings = [o.getMessage() for o in logs.records if "GPT字典" in o.getMessage()]
        self.assertEqual(len(warnings), 1)
        self.assertIn("无位置效果", warnings[0])


class IsLegacyPrefixTests(unittest.TestCase):
    def test_is_legacy_prefix(self) -> None:
        self.assertTrue(_is_legacy_prefix("1^あ"))
        self.assertTrue(_is_legacy_prefix("^^あ"))
        self.assertFalse(_is_legacy_prefix("re:あ"))
        self.assertFalse(_is_legacy_prefix("あ"))


if __name__ == "__main__":
    unittest.main()
