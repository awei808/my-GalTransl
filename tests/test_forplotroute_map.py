"""剧情路线图 mermaid 源码校验（ForPlotRouteMap._validate_mermaid）。"""
import unittest

from GalTransl.Backend.ForPlotRouteMap import ForPlotRouteMap


class PlotRouteMermaidValidationTests(unittest.TestCase):
    """_validate_mermaid：拦截 mermaid 词法无法解析的 subgraph id。"""

    def test_valid_english_subgraph_id(self) -> None:
        src = 'flowchart TD\nsubgraph prologue["序章"]\n A[xx]\nend'
        self.assertTrue(ForPlotRouteMap._validate_mermaid(src))

    def test_valid_chinese_subgraph_id(self) -> None:
        src = "flowchart TD\nsubgraph 序章[序章]\n A[xx]\nend"
        self.assertTrue(ForPlotRouteMap._validate_mermaid(src))

    def test_valid_flowchart_without_subgraph(self) -> None:
        self.assertTrue(ForPlotRouteMap._validate_mermaid("flowchart TD\nA --> B"))

    def test_invalid_subgraph_id_with_interpunct(self) -> None:
        # U+00B7 中间点会导致 mermaid 词法解析失败（Syntax error in text）
        src = "flowchart TD\nsubgraph 华恋·魅魔支线[华恋·魅魔支线]\n A[xx]\nend"
        self.assertFalse(ForPlotRouteMap._validate_mermaid(src))

    def test_invalid_non_flowchart_head(self) -> None:
        self.assertFalse(ForPlotRouteMap._validate_mermaid("hello world"))

    def test_invalid_empty_source(self) -> None:
        self.assertFalse(ForPlotRouteMap._validate_mermaid(""))
        self.assertFalse(ForPlotRouteMap._validate_mermaid("   \n  "))


if __name__ == "__main__":
    unittest.main()
