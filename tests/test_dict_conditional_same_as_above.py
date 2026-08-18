"""条件字典「同上」（~ / (同上) / （同上））回归测试。

覆盖 H2：首个条件词条的首个条件词为「~」时 last_one_success 未初始化
导致的 UnboundLocalError；以及「同上」跨词条继承上一次命中结果的语义。
"""

import unittest

from GalTransl.CSentense import CSentense
from GalTransl.Dictionary import CBasicDicElement, CNormalDic, IfWord


def _make_conditional(search: str, replace: str, if_words: list, spl_word: str = "[and]") -> CBasicDicElement:
    """构造一个条件字典词条（手工装配，绕开行文本解析）。"""
    element = CBasicDicElement(search_word=search, replace_word=replace, special_key="pre_src")
    element.is_conditionaDic = True
    element.if_word_list = [IfWord(w) for w in if_words]
    element.spl_word = spl_word
    return element


def _make_dic(elements: list) -> CNormalDic:
    """构造内存字典：CNormalDic 的构造参数是文件路径列表，测试直接装配 dic_list。"""
    dic = CNormalDic([])
    dic.dic_list = elements
    return dic


class ConditionalDictSameAsAboveTests(unittest.TestCase):
    def test_first_condition_word_same_as_above_does_not_crash(self) -> None:
        # H2 回归：首条条件词条的首个条件词是「~」，修复前抛 UnboundLocalError
        dic = _make_dic([_make_conditional("查找", "替换", ["~", "日"])])
        tran = CSentense("日本語テスト", "", 1)
        result = dic.do_replace("日本語テスト", tran)
        # 无上一词条可参考 → 「~」判 False → [and] 提前结束，不替换
        self.assertEqual(result, "日本語テスト")

    def test_same_as_above_inherits_previous_success(self) -> None:
        # 「同上」跨词条继承：词条1命中后，词条2首词「~」应视为 True 并继续判断后续条件
        dic = _make_dic([
            _make_conditional("日", "日文", ["語"]),  # pre_src 含「語」→ 命中 → last_one_success=True
            _make_conditional("テスト", "测试", ["~", "本"]),  # 「~」→ True，第二个词「本」也命中 → 替换
        ])
        tran = CSentense("日本語テスト", "", 1)
        result = dic.do_replace("日本語テスト", tran)
        self.assertEqual(result, "日文本語测试")

    def test_same_as_above_inherits_previous_failure(self) -> None:
        # 词条1未命中 → last_one_success=False → 词条2首词「~」判 False → 不替换
        dic = _make_dic([
            _make_conditional("日", "日文", ["英"]),  # pre_src 不含「英」→ 未命中
            _make_conditional("テスト", "测试", ["~", "本"]),
        ])
        tran = CSentense("日本語テスト", "", 1)
        result = dic.do_replace("日本語テスト", tran)
        self.assertEqual(result, "日本語テスト")


if __name__ == "__main__":
    unittest.main()
