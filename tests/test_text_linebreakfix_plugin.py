"""换行修复插件 text_common_lineBreakFix 的 intersperse_mode 回归测试。

覆盖 H3：译文换行数多于原文时，原实现 join(slices[:target_breaks+1]) 截断丢文本；
修复后合并多余换行且不丢失任何内容，并回归正常切分路径。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plugins.text_common_lineBreakFix.text_common_lineBreakFix import LineBreakFix


class _FakeTokenizer:
    """最小替身：按 2 字符切分，规避 budoux 外部依赖。"""

    def parse(self, text: str) -> list:
        if not text:
            return []
        return [text[i : i + 2] for i in range(0, len(text), 2)]


def _make_plugin() -> LineBreakFix:
    plugin = object.__new__(LineBreakFix)
    plugin.pname = "test-linebreakfix"
    plugin.linebreak = "[r]"
    plugin.tokenizer_module = "budoux"
    plugin.tokenizer = _FakeTokenizer()
    return plugin


class LineBreakFixIntersperseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = _make_plugin()

    def test_merges_excess_breaks_without_data_loss(self) -> None:
        # H3 回归：译文 2 个换行 > 目标 1 个，修复前输出 "C[r]D" 丢 "E"
        result = self.plugin.intersperse_mode("C[r]D[r]E", 1)
        self.assertEqual(result.count("[r]"), 1)
        for ch in ("C", "D", "E"):
            self.assertIn(ch, result)

    def test_merges_all_breaks_when_target_zero(self) -> None:
        result = self.plugin.intersperse_mode("A[r]B[r]C", 0)
        self.assertEqual(result, "ABC")

    def test_splits_when_fewer_breaks_unchanged(self) -> None:
        # 正常切分路径回归：译文换行不足时仍按最长片段切分，无截断
        result = self.plugin.intersperse_mode("ABCDEF", 1)
        self.assertEqual(result.count("[r]"), 1)
        self.assertEqual(result.replace("[r]", ""), "ABCDEF")

    def test_unchanged_when_breaks_equal(self) -> None:
        self.assertEqual(self.plugin.intersperse_mode("A[r]B", 1), "A[r]B")


if __name__ == "__main__":
    unittest.main()
