import unittest

from GalTransl.Problem import find_problems
from GalTransl.ConfigHelper import CProblemType
from GalTransl.CSentense import CSentense


class FakeProblemConfig:
    target_lang = "zh-cn"

    def __init__(self, problem_list=None, threshold=17):
        self._problem_list = problem_list or ["长句丢失换行"]
        self._threshold = threshold

    def getProblemAnalyzeArinashiDict(self):
        return {}

    def getProblemAnalyzeConfig(self, key):
        if key == "problemList":
            return [CProblemType[name] for name in self._problem_list]
        return []

    def getlbSymbol(self):
        return "auto"

    def getAvgSentenceLengthThreshold(self):
        return self._threshold


def make_tran(pre_src: str, pre_dst: str, skip_check: bool = False) -> CSentense:
    tran = CSentense(pre_src, speaker="", index=0)
    tran.post_src = pre_src
    tran.pre_dst = pre_dst
    tran.post_dst = pre_dst
    tran.skip_check = skip_check
    return tran


class LongSentenceLinebreakDetectionTests(unittest.TestCase):

    def test_long_text_no_break_flagged(self) -> None:
        pre_src = "日本語の長い文章です。\nもっと続きます。"
        pre_dst = "这是一句很长的中文翻译没有任何换行符来分割句子。"
        tran = make_tran(pre_src, pre_dst)
        config = FakeProblemConfig()
        find_problems([tran], config)
        self.assertIn("长句丢失换行", tran.problem)

    def test_short_text_no_break_not_flagged(self) -> None:
        pre_src = "ああ。\nそうですか。"
        pre_dst = "总之，能再试试其他的服装吗？"
        tran = make_tran(pre_src, pre_dst)
        config = FakeProblemConfig()
        find_problems([tran], config)
        self.assertNotIn("长句丢失换行", tran.problem)

    def test_text_with_enough_breaks_not_flagged(self) -> None:
        pre_src = "文A。\n文B。\n文C。"
        pre_dst = "第一句。\n第二句。\n第三句。"
        tran = make_tran(pre_src, pre_dst)
        config = FakeProblemConfig()
        find_problems([tran], config)
        self.assertNotIn("长句丢失换行", tran.problem)

    def test_custom_threshold_affects_result(self) -> None:
        pre_src = "文。\n文。"
        pre_dst = "合理长度的句子。\n另一句。\n再来一句。"
        tran = make_tran(pre_src, pre_dst)
        config = FakeProblemConfig(threshold=5)
        find_problems([tran], config)
        self.assertIn("长句丢失换行", tran.problem)

    def test_skip_check_suppresses_detection(self) -> None:
        pre_src = "日本語の長い文章です。\nもっと続きます。"
        pre_dst = "这是一句很长的中文翻译没有任何换行符来分割句子。"
        tran = make_tran(pre_src, pre_dst, skip_check=True)
        config = FakeProblemConfig()
        find_problems([tran], config)
        self.assertEqual(tran.problem, "")

    def test_skip_check_clears_existing_problem(self) -> None:
        pre_src = "日本語の長い文章です。\nもっと続きます。"
        pre_dst = "这是一句很长的中文翻译没有任何换行符来分割句子。"
        tran = make_tran(pre_src, pre_dst, skip_check=True)
        tran.problem = "比日文长：1.5倍(10字符)"
        config = FakeProblemConfig(problem_list=["长句丢失换行", "比日文长"])
        find_problems([tran], config)
        self.assertEqual(tran.problem, "")

    def test_disabled_in_problem_list_not_flagged(self) -> None:
        pre_src = "日本語の長い文章です。\nもっと続きます。"
        pre_dst = "这是一句很长的中文翻译没有任何换行符来分割句子。"
        tran = make_tran(pre_src, pre_dst)
        config = FakeProblemConfig(problem_list=["词频过高"])
        find_problems([tran], config)
        self.assertNotIn("长句丢失换行", tran.problem)

    def test_source_without_linebreak_no_detection(self) -> None:
        pre_src = "日本語だけで改行がない。"
        pre_dst = "很长的一段中文翻译没有任何换行符分割句子即使很长也不会触发。"
        tran = make_tran(pre_src, pre_dst)
        config = FakeProblemConfig()
        find_problems([tran], config)
        self.assertNotIn("长句丢失换行", tran.problem)

    def test_avg_exceeds_threshold_by_large_margin(self) -> None:
        pre_src = "文。\n文。\n文。\n"
        pre_dst = "这是一句非常长的中文句子有一个换行符\n但还是太长。"
        tran = make_tran(pre_src, pre_dst)
        config = FakeProblemConfig(threshold=8)
        find_problems([tran], config)
        self.assertIn("长句丢失换行", tran.problem)


if __name__ == "__main__":
    unittest.main()
