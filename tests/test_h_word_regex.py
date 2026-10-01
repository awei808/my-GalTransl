"""H 禁用词「裸字误报」修复：环视正则检测 + 提示词字面核心提取。

`禁用词_H.txt` 的「逼」「操」是常用字，裸字子串匹配会把「逼近/逼真/逼到」
「操纵/操控/操作/节操/体操」判为用词不当。改为 re: 环视正则后仅粗俗用法命中；
同时 `_h_word_text` 提取字面核心，使注入提示词仍显示「逼」「操」而非正则全文。

实测（理狂人 3390 句 + 小粥3-全量 23757 句）：逼 78->1、操 45->14，误报 0。
（「逼」排除集含「紧」，故「逼紧」按压迫义不命中；见 test_bi_jin_excluded_by_design。）
"""
import unittest

from GalTransl.Backend.ForGalJsonTranslate import (
    _h_word_text,
    _literal_core,
    _strip_leading_lookaround,
    _strip_trailing_lookaround,
)
from GalTransl.Backend.Prompts import H_BATCH_FORBIDDEN
from GalTransl.Dictionary import DictWordMatcher

# 与 Dict/禁用词_H.txt 保持一致的检测正则
_BI_PATTERN = r"(?<![催紧])逼(?![近真到得着入向我你她疯命问迫视供走])"
_CAO_PATTERN = r"(?<![节贞情曹体])操(?![作控纵线演场心办守行练课盘舵持刀琴])"


class LiteralCoreTests(unittest.TestCase):
    """_literal_core：剥离首尾环视断言取字面核心，对无环视模式恒等。"""

    def test_lookaround_patterns_yield_literal(self) -> None:
        self.assertEqual(_literal_core(_BI_PATTERN), "逼")
        self.assertEqual(_literal_core(_CAO_PATTERN), "操")
        self.assertEqual(_literal_core(r"(?<![体情节])操(?![…])"), "操")
        self.assertEqual(_literal_core(r"(?<=x)操"), "操")

    def test_patterns_without_lookaround_are_identity(self) -> None:
        # 关键回归：旧词表里的纯正则/普通词必须原样保留
        for text in ("^あ+$", "^禁止.*$", "普通词", "A|B", "[呵嘿]+", r"\d+", "逼"):
            self.assertEqual(_literal_core(text), text, text)

    def test_empty_result_for_pure_lookaround(self) -> None:
        # 全环视无字面核心：返回空串由调用方拼接，不得抛异常
        self.assertEqual(_literal_core(r"(?<!x)"), "")
        self.assertEqual(_literal_core(""), "")

    def test_nested_and_class_parentheses(self) -> None:
        # 断言内的 ')' 与字符组内的 ')' 均为字面量，不得提前截断配对
        self.assertEqual(_literal_core(r"(?<![a)b])操(?![c(d)])"), "操")
        self.assertEqual(_literal_core(r"(?<![\]])操"), "操")

    def test_strip_helpers_on_non_lookaround(self) -> None:
        self.assertEqual(_strip_leading_lookaround("操"), "操")
        self.assertEqual(_strip_trailing_lookaround("操"), "操")
        self.assertEqual(_strip_leading_lookaround(r"(?<!x)操"), "操")
        self.assertEqual(_strip_trailing_lookaround(r"操(?!x)"), "操")


class HWordTextTests(unittest.TestCase):
    """_h_word_text：正则词条取字面核心，普通词/str 口径不变。"""

    def test_regex_entry_renders_literal_core(self) -> None:
        self.assertEqual(_h_word_text(DictWordMatcher("re:" + _BI_PATTERN)), "逼")
        self.assertEqual(_h_word_text(DictWordMatcher("re:" + _CAO_PATTERN)), "操")

    def test_plain_entry_unchanged(self) -> None:
        self.assertEqual(_h_word_text(DictWordMatcher("攀上")), "攀上")
        self.assertEqual(_h_word_text("str词"), "str词")

    def test_legacy_regex_entry_unchanged(self) -> None:
        # 旧口径回归：非环视正则仍原样注入
        self.assertEqual(_h_word_text(DictWordMatcher("re:^あ+$")), "^あ+$")
        self.assertEqual(_h_word_text(DictWordMatcher("re:^禁止.*$")), "^禁止.*$")

    def test_prompt_has_no_regex_noise(self) -> None:
        words = [
            DictWordMatcher("攀上"),
            DictWordMatcher("re:" + _BI_PATTERN),
            DictWordMatcher("re:" + _CAO_PATTERN),
            DictWordMatcher("肏"),
        ]
        out = H_BATCH_FORBIDDEN.format(words="、".join(_h_word_text(w) for w in words))
        self.assertIn("逼", out)
        self.assertIn("操", out)
        self.assertNotIn("?<!", out)
        self.assertNotIn("?=", out)


class LookaroundDetectionTests(unittest.TestCase):
    """环视正则的检测精度：正常搭配不命中，粗俗用法命中。"""

    def setUp(self) -> None:
        self.bi = DictWordMatcher("re:" + _BI_PATTERN)
        self.cao = DictWordMatcher("re:" + _CAO_PATTERN)

    def test_bi_ignores_normal_collocations(self) -> None:
        for text in ("逼近极限", "不断逼近", "逼真", "被逼到绝境", "催逼着", "紧紧逼迫"):
            self.assertFalse(self.bi.hit(text), text)

    def test_bi_hits_vulgar_usage(self) -> None:
        # 语料实测的真实命中形态（逼紧/逼了过来）
        for text in ("逼紧凛音", "朝我逼了过来", "进一步逼紧"):
            self.assertTrue(self.bi.hit(text), text)

    def test_bi_excludes_coercion_with_pronoun(self) -> None:
        # 「逼你/逼她」是「迫使某人」的正常义（如 让我逼你出声），已在排除集内
        for text in ("我逼你", "别逼她", "让我逼你出声"):
            self.assertFalse(self.bi.hit(text), text)

    def test_cao_ignores_normal_collocations(self) -> None:
        for text in ("操纵", "操控", "操作", "节操", "体操", "情操", "曹操", "操线", "贞操"):
            self.assertFalse(self.cao.hit(text), text)

    def test_cao_hits_vulgar_usage(self) -> None:
        for text in ("操你的屁眼", "操到烂", "操弄", "被操个遍", "操翻", "粗暴地操"):
            self.assertTrue(self.cao.hit(text), text)

    def test_naked_substring_would_have_misfired(self) -> None:
        # 反证：裸字方案确实误报，说明本修复必要性
        naked_bi = DictWordMatcher("逼")
        naked_cao = DictWordMatcher("操")
        self.assertTrue(naked_bi.hit("逼近极限"))
        self.assertTrue(naked_cao.hit("节操"))
        self.assertFalse(self.bi.hit("逼近极限"))
        self.assertFalse(self.cao.hit("节操"))


class ExclusionSetCoverageTests(unittest.TestCase):
    """排除集逐字覆盖：漏写任一排除字都会让对应正常词误报，必须逐词断言。

    本类把 Dict/禁用词_H.txt 注释里点名的误报词清单变成可执行断言——
    这正是本次修复要防的回归类型（子串误报），故逐字覆盖而非抽样。
    """

    @classmethod
    def setUpClass(cls) -> None:
        import os

        from GalTransl.Problem import load_h_check_words

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        words = load_h_check_words([os.path.join(root, "Dict", "禁用词_H.txt")])
        cls.bi = next(w for w in words if w.is_regex and "逼" in w.word)
        cls.cao = next(w for w in words if w.is_regex and "操" in w.word)

    def test_bi_normal_collocations_not_hit(self) -> None:
        # 覆盖「逼」后顾集 近真到得着入向我你她疯命问迫视供走紧 + 前瞻集 催紧
        for text in (
            "逼近", "逼真", "被逼到绝境", "被逼得", "逼着", "逼入绝境", "逼向",
            "我逼你", "别逼她", "逼疯", "逼命", "逼问", "逼迫", "逼视", "逼供",
            "被我逼走", "逼紧", "催逼着", "紧紧逼", "紧逼。",
        ):
            self.assertFalse(self.bi.hit(text), f"「{text}」是正常词，不应命中")

    def test_cao_normal_collocations_not_hit(self) -> None:
        # 覆盖「操」后顾集 作控纵线演场心办守行练课盘舵持刀琴 + 前瞻集 节贞情曹体
        for text in (
            "操作", "操控", "操纵", "操线", "操演", "操场", "操心", "操办", "操守",
            "操行", "操练", "操课", "操盘", "操舵", "操持", "操刀", "操琴",
            "节操", "贞操", "情操", "曹操", "体操",
        ):
            self.assertFalse(self.cao.hit(text), f"「{text}」是正常词，不应命中")

    def test_bi_vulgar_still_hit(self) -> None:
        # 排除集不得把真实粗俗用法一并挡掉（防过度排除）
        for text in ("朝我逼了过来", "朝我逼过来"):
            self.assertTrue(self.bi.hit(text), f"「{text}」应命中")

    def test_bi_jin_excluded_by_design(self) -> None:
        # 「逼紧」判定为压迫义（排除集含「紧」），故不命中；此处锁定该口径防回退
        for text in ("逼紧凛音", "进一步逼紧", "逼紧"):
            self.assertFalse(self.bi.hit(text), f"「{text}」按现行口径不命中")

    def test_cao_vulgar_still_hit(self) -> None:
        for text in ("操你的屁眼", "操到烂", "操弄", "被操个遍", "操翻", "狠狠地操"):
            self.assertTrue(self.cao.hit(text), f"「{text}」应命中")


class RealDictFileTests(unittest.TestCase):
    """真实 Dict/禁用词_H.txt：加载可用且提示词干净。"""

    @classmethod
    def setUpClass(cls) -> None:
        import os

        from GalTransl.Problem import load_h_check_words

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.words = load_h_check_words([os.path.join(root, "Dict", "禁用词_H.txt")])

    def test_dict_file_loads(self) -> None:
        self.assertTrue(self.words)
        # 提示词口径（_h_word_text）取字面核心；.word 对 re: 条目是原始正则
        self.assertIn("逼", [_h_word_text(w) for w in self.words])

    def test_real_dict_bi_is_regex_not_naked(self) -> None:
        # 断言「逼」已不再是裸字条目；不写死正则条数（后续可加其它误报正则）
        self.assertFalse(any(w.word == "逼" and not w.is_regex for w in self.words))
        self.assertTrue(any(w.is_regex and "逼" in w.word for w in self.words))

    def test_real_dict_cao_is_regex_not_naked(self) -> None:
        self.assertFalse(any(w.word == "操" and not w.is_regex for w in self.words))
        self.assertTrue(any(w.is_regex and "操" in w.word for w in self.words))

    def test_real_dict_bi_and_cao_are_isolated(self) -> None:
        bi = next(w for w in self.words if w.is_regex and "逼" in w.word)
        cao = next(w for w in self.words if w.is_regex and "操" in w.word)
        self.assertTrue(bi.hit("朝我逼了过来"))
        self.assertFalse(bi.hit("操你的屁眼"))
        self.assertTrue(cao.hit("操你的屁眼"))
        self.assertFalse(cao.hit("逼了过来"))

    def test_real_dict_prompt_is_clean(self) -> None:
        listed = "、".join(_h_word_text(w) for w in self.words)
        out = H_BATCH_FORBIDDEN.format(words=listed)
        self.assertIn("逼", out)
        self.assertIn("操", out)
        self.assertNotIn("?<!", out)


if __name__ == "__main__":
    unittest.main()
