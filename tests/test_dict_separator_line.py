"""字典纯符号分隔线跳过：四个加载/解析入口口径一致性测试。

`====`、`----` 等装饰分隔线此前会被 parse_dict_line 解析为 normal 词条
（src="====", dst=""）；含 Tab/四空格/-> 的分段线更会被 CNormalDic/CGptDict
加载为真实生效词条。修复后统一按注释口径跳过，与 load_h_check_words 对齐。
"""
import os
import tempfile
import unittest

from GalTransl.Dictionary import (
    CNormalDic,
    CGptDict,
    _is_separator_line,
    parse_dict_line,
)
from GalTransl.Problem import load_h_check_words


class IsSeparatorLineTests(unittest.TestCase):
    def test_common_separator_lines_match(self) -> None:
        for line in ("=====", "----", "****", "____", "~~~~", "======================="):
            self.assertTrue(_is_separator_line(line), line)

    def test_whitespace_wrapped_separator_matches(self) -> None:
        self.assertTrue(_is_separator_line("  ========  "))
        self.assertTrue(_is_separator_line("-----\n"))

    def test_short_or_mixed_lines_do_not_match(self) -> None:
        # 2 字符不判（`-- -> ——` 等真实词条不受影响）；含实义字符不判
        for line in ("--", "==", "= =", "==== 标题 ====", "re:=+", "//===="):
            self.assertFalse(_is_separator_line(line), line)


class ParseDictLineSeparatorTests(unittest.TestCase):
    def test_separator_lines_become_comment_in_all_categories(self) -> None:
        for line in ("=====", "----------", "********"):
            for category in ("pre", "post", "gpt"):
                row = parse_dict_line(line, category)
                self.assertEqual(row.type, "comment", (line, category))
                self.assertEqual(row.raw, line)

    def test_indented_separator_becomes_comment(self) -> None:
        self.assertEqual(parse_dict_line("  ====  ", "pre").type, "comment")

    def test_two_char_dash_still_normal_entry(self) -> None:
        row = parse_dict_line("--|——", "pre")
        self.assertEqual(row.type, "normal")
        self.assertEqual(row.values, ["--", "——", ""])

    def test_regexp_entry_unaffected(self) -> None:
        row = parse_dict_line("re:=+|＝", "pre")
        self.assertEqual(row.type, "normal")
        self.assertTrue(row.is_regex)

    def test_pipe_joined_separator_stays_entry(self) -> None:
        # 含 | 的分段线不在纯符号口径内（与 load_h_check_words 一致），仍按词条解析
        row = parse_dict_line("====|====", "pre")
        self.assertEqual(row.type, "normal")


class CNormalDicSeparatorTests(unittest.TestCase):
    def _write_dic(self, content: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        self.addCleanup(os.remove, path)
        return path

    def test_separator_line_not_loaded_as_entry(self) -> None:
        path = self._write_dic("あああ|啊啊啊\n=======================\nいいい|一一一\n")
        dic = CNormalDic([path])
        self.assertEqual([d.search_word for d in dic.dic_list], ["あああ", "いいい"])

    def test_spaced_separator_would_still_load(self) -> None:
        # 含四空格分隔的分段线不在本次纯符号口径内：转 | 后成多列词条（既有行为锁定）
        path = self._write_dic("====    ====\n")
        dic = CNormalDic([path])
        self.assertEqual(len(dic.dic_list), 1)
        self.assertEqual(dic.dic_list[0].search_word, "====")


class CGptDictSeparatorTests(unittest.TestCase):
    def test_separator_line_not_loaded_as_gpt_entry(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".txt")
        self.addCleanup(os.remove, path)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("あああ|啊啊啊|说明\n====\nいいい|一一一\n")
        dic = CGptDict([path])
        self.assertEqual([d.search_word for d in dic._dic_list], ["あああ", "いいい"])


class LoadHCheckWordsSeparatorTests(unittest.TestCase):
    def test_separator_skipped_but_two_char_word_kept(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".txt")
        self.addCleanup(os.remove, path)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("=====\n---\n攀上顶峰\n--\n")
        words = load_h_check_words([path])
        self.assertEqual([w.word for w in words], ["攀上顶峰", "--"])


if __name__ == "__main__":
    unittest.main()
