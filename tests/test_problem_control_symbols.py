"""缺控制符检测：按「子串包含」判断控制符是否保留，而不是 token 精确相等。

extract_control_substrings 按 ASCII 连续段切词：源文 `[石浦城跡/いしうらじょうあと]`
（括号内是日文，非 ASCII）切出 ['[', '/', ']']，译文 `[石浦城迹/shipuchengji]`（括号内
是罗马字，`]` 又在允许字符集里）会把它们并成一个 token——精确比较就会误报「缺控制符」。
"""

import unittest

from GalTransl.CSentense import CSentense
from GalTransl.Problem import CProblemType, find_problems


class FakeProblemConfig:
    target_lang = "zh-cn"

    def getProblemAnalyzeArinashiDict(self) -> dict:
        return {}

    def getProblemAnalyzeConfig(self, key: str) -> list:
        if key == "problemList":
            return [CProblemType["缺控制符"]]
        return []

    def hasProblemAnalyzeConfig(self, key: str) -> bool:
        return key == "problemList"

    def getDictCfgSection(self) -> dict:
        return {}


def _tran(src: str, dst: str) -> CSentense:
    tran = CSentense(src, speaker="", index=0)
    tran.post_src = src
    tran.pre_dst = dst
    tran.post_dst = dst
    return tran


def _run(src: str, dst: str) -> str:
    tran = _tran(src, dst)
    find_problems([tran], FakeProblemConfig(), None)
    return tran.problem


class ControlSymbolTests(unittest.TestCase):
    def test_ruby_annotation_with_romaji_reading_is_not_flagged(self) -> None:
        # 括号内读音由假名改写成罗马字：不算丢控制符
        problem = _run(
            "[石浦城跡/いしうらじょうあと]のバス停まで行きたいんですけど",
            "我想去[石浦城迹/shipuchengji]的巴士站",
        )
        self.assertEqual(problem, "")

    def test_ascii_run_in_translation_absorbs_punctuation(self) -> None:
        problem = _run("三人でながれ[茶屋街/ちゃやがい]へと向かう", "三人向流[茶屋街/chayagai]走去")
        self.assertEqual(problem, "")

    def test_preserved_control_tag_is_not_flagged(self) -> None:
        problem = _run("<color=red>こんにちは</color>", "<color=red>你好</color>")
        self.assertEqual(problem, "")

    def test_dropped_control_tag_is_still_flagged(self) -> None:
        problem = _run("<color=red>こんにちは</color>", "你好")
        self.assertIn("缺控制符", problem)
        self.assertIn("<color=red>", problem)
        self.assertIn("</color>", problem)

    def test_dropped_bracket_annotation_is_still_flagged(self) -> None:
        # 括号注解整体被丢掉：仅剩控制符 [ / ] 全不在译文里
        problem = _run("[茶屋街/ちゃやがい]へ", "去茶屋街")
        self.assertIn("缺控制符", problem)


if __name__ == "__main__":
    unittest.main()
