"""测试"长句丢失换行"问题检测项。

测试场景：
- 译文分句过长（avg_sentence_length > threshold）应标记为"长句丢失换行"
- 译文分句正常不应误标记
- skip_check 可跳过该检测
- 未启用该检测项时不触发

设计前提：译文与原文使用同一种换行符（由 pre_src 推断的 n_symbol），
因此检测直接复用 n_symbol 对 post_dst 计数，不处理"多换行符混用"场景。
"""

import unittest

from GalTransl.Problem import find_problems
from GalTransl.ConfigHelper import CProblemType
from GalTransl.CSentense import CSentense


class FakeProblemConfig:
    """最小化的 projectConfig，用于驱动 find_problems。"""

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
    """构造一个 CSentense，原文带换行符以便触发检测。"""
    tran = CSentense(pre_src, speaker="", index=0)
    tran.post_src = pre_src
    tran.pre_dst = pre_dst
    tran.post_dst = pre_dst
    tran.skip_check = skip_check
    return tran


class LongSentenceLinebreakDetectionTests(unittest.TestCase):
    """长句丢失换行检测的单元测试。"""

    def test_long_text_no_break_flagged(self):
        """长译文且无换行符 → 平均分句长度 > 阈值 → 应标记。"""
        # 原文带 \n 以便 n_symbol 被识别
        pre_src = "日本語の長い文章です。\nもっと続きます。"
        pre_dst = "这是一句很长的中文翻译没有任何换行符来分割句子。"  # clean_len=24, breaks=0, avg=24/1=24 > 17
        tran = make_tran(pre_src, pre_dst)
        config = FakeProblemConfig()
        find_problems([tran], config)
        self.assertIn("长句丢失换行", tran.problem,
                      "长译文无换行应被标记")

    def test_short_text_no_break_not_flagged(self):
        """短译文且无换行 → 平均分句长度 ≤ 阈值 → 不应标记。"""
        pre_src = "ああ。\nそうですか。"
        pre_dst = "总之，能再试试其他的服装吗？"  # len=14, breaks=0, avg=14/1=14 ≤ 17
        tran = make_tran(pre_src, pre_dst)
        config = FakeProblemConfig()
        find_problems([tran], config)
        self.assertNotIn("长句丢失换行", tran.problem,
                         "短译文无换行不应标记")

    def test_text_with_enough_breaks_not_flagged(self):
        """译文有合理换行 → 平均分句长度正常 → 不应标记。"""
        pre_src = "文A。\n文B。\n文C。"
        pre_dst = "第一句。\n第二句。\n第三句。"  # len=14, clean=12, breaks=2, avg=12/3=4.0 ≤ 17
        tran = make_tran(pre_src, pre_dst)
        config = FakeProblemConfig()
        find_problems([tran], config)
        self.assertNotIn("长句丢失换行", tran.problem,
                         "有合理换行的译文不应标记")

    def test_custom_threshold_affects_result(self):
        """自定义更紧的阈值 → 之前不触发的场景现在触发。"""
        pre_src = "文。\n文。"
        pre_dst = "合理长度的句子。\n另一句。\n再来一句。"  # len=19, clean=17, breaks=2, avg=17/3≈5.67
        tran = make_tran(pre_src, pre_dst)
        # 阈值设为 5，avg=5.67 > 5，应标记
        config = FakeProblemConfig(threshold=5)
        find_problems([tran], config)
        self.assertIn("长句丢失换行", tran.problem,
                      "更严的阈值应触发标记")

    def test_skip_check_suppresses_detection(self):
        """skip_check=True → 跳过全部检测 → 不应标记。"""
        pre_src = "日本語の長い文章です。\nもっと続きます。"
        pre_dst = "这是一句很长的中文翻译没有任何换行符来分割句子。"
        tran = make_tran(pre_src, pre_dst, skip_check=True)
        config = FakeProblemConfig()
        find_problems([tran], config)
        self.assertEqual(tran.problem, "",
                         "skip_check 应跳过所有检测")

    def test_skip_check_clears_existing_problem(self):
        """skip_check=True → 即使之前有问题标记也应清除。"""
        pre_src = "日本語の長い文章です。\nもっと続きます。"
        pre_dst = "这是一句很长的中文翻译没有任何换行符来分割句子。"
        tran = make_tran(pre_src, pre_dst, skip_check=True)
        # 模拟之前遗留的问题标记
        tran.problem = "比日文长：1.5倍(10字符)"
        config = FakeProblemConfig(problem_list=["长句丢失换行", "比日文长"])
        find_problems([tran], config)
        self.assertEqual(tran.problem, "",
                         "skip_check 应清除已有问题标记")

    def test_disabled_in_problem_list_not_flagged(self):
        """未启用长句丢失换行检测 → 即使译文很长也不标记。"""
        pre_src = "日本語の長い文章です。\nもっと続きます。"
        pre_dst = "这是一句很长的中文翻译没有任何换行符来分割句子。"
        tran = make_tran(pre_src, pre_dst)
        # problemList 中没有"长句丢失换行"
        config = FakeProblemConfig(problem_list=["词频过高"])
        find_problems([tran], config)
        self.assertNotIn("长句丢失换行", tran.problem,
                         "未启用的检测项不应生效")

    def test_source_without_linebreak_no_detection(self):
        """原文无换行符 → n_symbol="" → 检测被跳过。"""
        pre_src = "日本語だけで改行がない。"
        pre_dst = "很长的一段中文翻译没有任何换行符分割句子即使很长也不会触发。"
        tran = make_tran(pre_src, pre_dst)
        config = FakeProblemConfig()
        find_problems([tran], config)
        self.assertNotIn("长句丢失换行", tran.problem,
                         "原文无换行时不应触发检测")

    def test_avg_exceeds_threshold_by_large_margin(self):
        """译文远超高阈值 → 应标记（边界值测试）。"""
        pre_src = "文。\n文。\n文。\n"
        # 译文有1个换行符，avg = clean / 2
        pre_dst = "这是一句非常长的中文句子有一个换行符\n但还是太长。"  # len=25, clean=24, breaks=1, avg=24/2=12 > 8
        tran = make_tran(pre_src, pre_dst)
        config = FakeProblemConfig(threshold=8)
        find_problems([tran], config)
        self.assertIn("长句丢失换行", tran.problem,
                      "avg=12 > 阈值8 应标记")


if __name__ == "__main__":
    unittest.main()
