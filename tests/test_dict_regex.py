# -*- coding: utf-8 -*-
"""字典正则支持与重叠词检查去重单元测试。

验证：
  - `re:` 前缀正则词条：解析（normal/gpt/conditional）、零宽丢弃、编译失败回退字面量、
    与 1^/^^ 组合；
  - CNormalDic.do_replace 正则替换（全量/一次/锚定开头/full_match）；
  - `\|` 转义竖线（引擎加载与检测词表）；
  - CGptDict.gen_prompt 正则命中与消费、内部长度排序（不依赖调用方 sort_dic）；
  - CGptDict.check_dic_use 消费式重叠去重（skip_overlap 开关）；
  - load_h_check_words 正则检测词与 find_problems 集成（含旧口径 str 词表兼容）。
"""
import os
import tempfile
import unittest

from GalTransl.CSentense import CSentense
from GalTransl.ConfigHelper import CProblemType
from GalTransl.Dictionary import (
    CBasicDicElement,
    CGptDict,
    CNormalDic,
    DictWordMatcher,
    IfWord,
    parse_dict_line,
)
from GalTransl.Problem import find_problems, load_h_check_words


def _write_dic(tmp: str, name: str, content: str) -> str:
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class RegexElementTests(unittest.TestCase):
    """CBasicDicElement 的 re: 前缀解析"""

    def test_regex_prefix_compiled(self) -> None:
        el = CBasicDicElement("re:魔[法剣]+", "魔X")
        self.assertTrue(el.is_regex)
        self.assertIsNotNone(el.regex_pattern)
        self.assertEqual(el.search_word, "魔[法剣]+")

    def test_zero_width_regex_marked_for_drop(self) -> None:
        el = CBasicDicElement("re:a*", "X")
        self.assertTrue(el.is_regex)
        self.assertIsNone(el.regex_pattern)
        self.assertEqual(el.regex_error, "zero-width")

    def test_invalid_regex_falls_back_to_literal(self) -> None:
        el = CBasicDicElement("re:[", "X")
        self.assertFalse(el.is_regex)
        self.assertEqual(el.search_word, "[")
        self.assertNotEqual(el.regex_error, "")

    def test_onetime_and_regex_compose(self) -> None:
        el = CBasicDicElement("1^re:b+", "X")
        self.assertTrue(el.onetime_flag)
        self.assertTrue(el.is_regex)
        self.assertEqual(el.search_word, "b+")

    def test_startswith_and_regex_compose(self) -> None:
        el = CBasicDicElement("^^re:c+", "X")
        self.assertTrue(el.startswith_flag)
        self.assertTrue(el.is_regex)

    def test_literal_entry_unaffected(self) -> None:
        el = CBasicDicElement("魔法", "魔")
        self.assertFalse(el.is_regex)
        self.assertIsNone(el.regex_pattern)
        self.assertEqual(el.search_word, "魔法")


class RegexDoReplaceTests(unittest.TestCase):
    """CNormalDic.do_replace 的正则替换分支"""

    def _dic(self, *elements: CBasicDicElement) -> CNormalDic:
        dic = CNormalDic([])
        dic.dic_list = list(elements)
        return dic

    def test_regex_replaces_all_occurrences(self) -> None:
        dic = self._dic(CBasicDicElement("re:[AB]+", "X"))
        self.assertEqual(dic.do_replace("12AB34A", None), "12X34X")

    def test_regex_onetime_replaces_first_only(self) -> None:
        # 贪婪匹配吞掉 AA 后仅替换首个匹配
        dic = self._dic(CBasicDicElement("1^re:A+", "X"))
        self.assertEqual(dic.do_replace("AAB", None), "XB")
        self.assertEqual(dic.do_replace("ABA", None), "XBA")

    def test_regex_startswith_anchors_at_beginning(self) -> None:
        dic = self._dic(CBasicDicElement("^^re:AB", "X"))
        self.assertEqual(dic.do_replace("ABCD", None), "XCD")
        self.assertEqual(dic.do_replace("xAB", None), "xAB")

    def test_regex_full_match(self) -> None:
        dic = self._dic(CBasicDicElement("re:AB", "Y"))
        self.assertEqual(dic.do_replace("AB", None, full_match=True), "Y")
        self.assertEqual(dic.do_replace("xAB", None, full_match=True), "xAB")

    def test_conditional_dict_regex_search(self) -> None:
        dic = self._dic(CBasicDicElement("re:[AB]+", "X", "pre_src"))
        dic.dic_list[0].is_conditionaDic = True
        dic.dic_list[0].if_word_list = [IfWord("あ")]
        dic.dic_list[0].spl_word = "[or]"
        tran = type(
            "T", (),
            {"pre_src": "あBB", "post_src": "", "pre_dst": "", "post_dst": "",
             "is_dialogue": True, "left_symbol": "", "dia_format": "", "mono_format": ""},
        )()
        self.assertEqual(dic.do_replace("あBBた", tran), "あXた")


class EscapedPipeTests(unittest.TestCase):
    """\\| 转义竖线：字段内还原为普通 |（| 本身是字段分隔符，无法直书）"""

    def test_engine_load_escaped_pipe_alternation(self) -> None:
        # 正则侧：\| 写出选择符（字段内为普通 |）
        tmp = tempfile.mkdtemp()
        path = _write_dic(tmp, "t.txt", "re:あ\\|い|译X\n")
        dic = CNormalDic([path])
        self.assertEqual(len(dic.dic_list), 1)
        el = dic.dic_list[0]
        self.assertTrue(el.is_regex)
        self.assertEqual(el.search_word, "あ|い")
        self.assertEqual(dic.do_replace("あだ", None), "译Xだ")
        self.assertEqual(dic.do_replace("いだ", None), "译Xだ")

    def test_regex_literal_pipe_via_char_class(self) -> None:
        # 模式内匹配字面竖线：字符类 [|]（源文本中的 | 写作 \|）
        tmp = tempfile.mkdtemp()
        path = _write_dic(tmp, "t.txt", "re:a[\\|]b|译X\n")
        dic = CNormalDic([path])
        self.assertEqual(dic.dic_list[0].search_word, "a[|]b")
        self.assertEqual(dic.do_replace("xxa|byy", None), "xx译Xyy")

    def test_h_check_words_escaped_pipe(self) -> None:
        tmp = tempfile.mkdtemp()
        path = _write_dic(tmp, "h.txt", "re:あ\\|い\n")
        words = load_h_check_words([path])
        self.assertEqual(len(words), 1)
        self.assertTrue(words[0].is_regex)
        self.assertEqual(words[0].word, "あ|い")  # \| 还原为普通 |（选择符）
        self.assertTrue(words[0].hit("あ"))
        self.assertTrue(words[0].hit("い"))


class GenPromptRegexTests(unittest.TestCase):
    """CGptDict.gen_prompt 的正则命中/消费与内部排序"""

    def _make_gd(self, tmp: str) -> CGptDict:
        path = _write_dic(
            tmp, "gpt.txt", "re:魔[法剣]+|魔X\n剣|圣剑\n"
        )
        return CGptDict([path])

    def _tran(self, src: str) -> CSentense:
        tran = CSentense(pre_src=src)
        tran.post_src = src
        return tran

    def test_regex_hit_and_consumption(self) -> None:
        gd = self._make_gd(tempfile.mkdtemp())
        prompt = gd.gen_prompt([self._tran("魔法剣と剣")])
        # 长词（正则）命中注入；消费后独立出现的短词仍注入
        self.assertIn("魔X", prompt)
        self.assertIn("圣剑", prompt)

    def test_short_word_covered_by_long_regex_not_injected(self) -> None:
        gd = self._make_gd(tempfile.mkdtemp())
        prompt = gd.gen_prompt([self._tran("魔法剣")])
        self.assertIn("魔X", prompt)
        self.assertNotIn("圣剑", prompt)  # 剣仅作为长词一部分出现，不重复注入

    def test_internal_sorting_without_sort_dic(self) -> None:
        # 未调用 sort_dic（文件序短词在前）时，内部排序仍保证长词优先消费
        tmp = tempfile.mkdtemp()
        path = _write_dic(tmp, "unsorted.txt", "剣|圣剑\n魔剣|魔剑\n")
        gd = CGptDict([path])
        prompt = gd.gen_prompt([self._tran("魔剣")])
        self.assertIn("魔剑", prompt)
        self.assertNotIn("圣剑", prompt)


class CheckDicUseOverlapTests(unittest.TestCase):
    """check_dic_use 消费式重叠去重（skip_overlap 开关）"""

    def _make_gd(self, tmp: str) -> CGptDict:
        path = _write_dic(tmp, "gpt.txt", "魔剣|魔剑\n剣|圣剑\n")
        return CGptDict([path])

    def _tran(self, src: str, dst: str) -> CSentense:
        tran = CSentense(pre_src=src)
        tran.post_src = src
        tran.pre_dst = dst
        tran.post_dst = dst
        return tran

    def test_overlap_no_false_positive(self) -> None:
        # 源词「剣」仅作为「魔剣」一部分出现：旧口径会误报「剣---圣剑」未使用，新口径不报
        gd = self._make_gd(tempfile.mkdtemp())
        tran = self._tran("魔剣", "魔剑")
        self.assertEqual(gd.check_dic_use("魔剑", tran), "")  # 长词条替换已使用，短词条不再误报

    def test_long_word_reported_once(self) -> None:
        # 长词条替换未使用时如实报告，且其覆盖范围不再由短词条重复报
        gd = self._make_gd(tempfile.mkdtemp())
        tran = self._tran("魔剣", "别的")
        out = gd.check_dic_use("别的", tran)
        self.assertIn("魔剣---魔剑", out)
        self.assertNotIn("剣---圣剑", out)

    def test_skip_overlap_false_keeps_legacy_behavior(self) -> None:
        gd = self._make_gd(tempfile.mkdtemp())
        tran = self._tran("魔剣", "魔剑")
        out = gd.check_dic_use("魔剑", tran, skip_overlap=False)
        self.assertNotIn("魔剣---魔剑", out)  # 长词条替换已使用
        self.assertIn("剣---圣剑", out)  # 旧口径：重叠词重复检查导致误报

    def test_regex_entry_in_check(self) -> None:
        tmp = tempfile.mkdtemp()
        path = _write_dic(tmp, "gpt.txt", "re:魔[法剣]+|魔X\n")
        gd = CGptDict([path])
        tran = self._tran("魔剣", "魔剑")
        out = gd.check_dic_use("魔剑", tran)
        self.assertIn("魔[法剣]+---魔X", out)

    def test_h_scene_priority_with_consumption(self) -> None:
        # scene='h'：h 短词先命中并消费，非 h 长词（其一部分为 h 词）不再检查
        tmp = tempfile.mkdtemp()
        h_path = _write_dic(tmp, "GPT字典_h.txt", "剣|圣剑\n")
        nh_path = _write_dic(tmp, "GPT字典_非h.txt", "魔剣|魔剑\n")
        gd = CGptDict([h_path, nh_path])
        tran = self._tran("魔剣", "魔剑")
        out = gd.check_dic_use("魔剑", tran, scene="h")
        self.assertIn("剣---圣剑", out)  # h 优先：覆盖消费后按 h 词条报告
        self.assertNotIn("魔剣---魔剑", out)


class _StubProblemConfig:
    """find_problems 所需的最小配置桩（可指定启用的检测类型与 skipOverlapCheck）"""

    def __init__(self, skip_overlap=None, problems=None) -> None:
        self._skip_overlap = skip_overlap
        self._problems = problems if problems is not None else [
            CProblemType.字典使用, CProblemType.用词不当,
        ]

    def getProblemAnalyzeArinashiDict(self) -> dict:
        return {}

    def getProblemAnalyzeConfig(self, key: str) -> list:
        return self._problems

    def hasProblemAnalyzeConfig(self, key: str) -> bool:
        return True

    def getDictCfgSection(self) -> dict:
        return {} if self._skip_overlap is None else {"skipOverlapCheck": self._skip_overlap}


class FindProblemsDictTests(unittest.TestCase):
    """find_problems 集成：字典使用/用词不当的正则与重叠去重"""

    def _trans(self, src: str, dst: str) -> list:
        tran = CSentense(pre_src=src)
        tran.post_src = src
        tran.pre_dst = dst
        tran.post_dst = dst
        tran.index = 1
        return [tran]

    def test_dict_use_skips_overlap_by_default(self) -> None:
        tmp = tempfile.mkdtemp()
        path = _write_dic(tmp, "gpt.txt", "魔剣|魔剑\n剣|圣剑\n")
        gd = CGptDict([path])
        trans = self._trans("魔剣", "魔剑")
        find_problems(trans, _StubProblemConfig(), gpt_dict=gd)
        self.assertNotIn("剣---圣剑", trans[0].problem)
        self.assertNotIn("魔剣---魔剑", trans[0].problem)  # 长词条替换已使用

    def test_dict_use_config_disables_overlap_skip(self) -> None:
        tmp = tempfile.mkdtemp()
        path = _write_dic(tmp, "gpt.txt", "魔剣|魔剑\n剣|圣剑\n")
        gd = CGptDict([path])
        trans = self._trans("魔剣", "魔剑")
        find_problems(
            trans,
            _StubProblemConfig(skip_overlap=False),
            gpt_dict=gd,
        )
        self.assertNotIn("魔剣---魔剑", trans[0].problem)  # 长词条替换已使用
        self.assertIn("剣---圣剑", trans[0].problem)  # 配置关闭去重 → 旧口径重复检查

    def test_forbidden_word_regex_hits(self) -> None:
        tmp = tempfile.mkdtemp()
        path = _write_dic(tmp, "forbidden.txt", "好棒\nre:棒[棒哒]+\n")
        words = load_h_check_words([path])
        trans = self._trans("ああ", "真的好棒棒")
        find_problems(
            trans,
            _StubProblemConfig(problems=[CProblemType.用词不当]),
            forbidden_words=words,
        )
        self.assertIn("用词不当：好棒、棒[棒哒]+", trans[0].problem)

    def test_forbidden_words_plain_strings_still_supported(self) -> None:
        # 兼容直接传 str 词表（老调用方）
        trans = self._trans("ああ", "真的好棒")
        find_problems(
            trans,
            _StubProblemConfig(problems=[CProblemType.用词不当]),
            forbidden_words=["好棒"],
        )
        self.assertIn("用词不当：好棒", trans[0].problem)


class ParseRegexFlagTests(unittest.TestCase):
    """parse_dict_line 的 is_regex/regex_error 结构化标记"""

    def test_normal_regex_row(self) -> None:
        r = parse_dict_line("re:\\d+|数字", "pre")
        self.assertTrue(r.is_regex)
        self.assertEqual(r.regex_error, "")
        self.assertEqual(r.values[0], "re:\\d+")

    def test_invalid_regex_row_reports_error(self) -> None:
        r = parse_dict_line("re:[|X", "pre")
        self.assertTrue(r.is_regex)
        self.assertNotEqual(r.regex_error, "")

    def test_zero_width_regex_row_reports_error(self) -> None:
        r = parse_dict_line("re:a*|X", "pre")
        self.assertTrue(r.is_regex)
        self.assertEqual(r.regex_error, "正则可匹配空串")

    def test_gpt_and_conditional_rows_flagged(self) -> None:
        self.assertTrue(parse_dict_line("re:a|b", "gpt").is_regex)
        r = parse_dict_line("pre_src|含甲|re:猫+|猫娘", "pre")
        self.assertTrue(r.is_regex)
        self.assertFalse(parse_dict_line("猫|猫娘", "pre").is_regex)


class EmptySearchWordGuardTests(unittest.TestCase):
    """空搜索词与零宽正则词条的入库守卫"""

    def test_empty_search_word_dropped(self) -> None:
        tmp = tempfile.mkdtemp()
        path = _write_dic(tmp, "t.txt", "|替换\n好|好X\n")
        dic = CNormalDic([path])
        self.assertEqual([e.search_word for e in dic.dic_list], ["好"])

    def test_zero_width_regex_dropped(self) -> None:
        tmp = tempfile.mkdtemp()
        path = _write_dic(tmp, "t.txt", "re:a*|X\n好|好X\n")
        dic = CNormalDic([path])
        self.assertEqual([e.search_word for e in dic.dic_list], ["好"])


if __name__ == "__main__":
    unittest.main()
