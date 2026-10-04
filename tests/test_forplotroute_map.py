"""剧情路线图 mermaid 源码校验与路线归属查询测试（ForPlotRouteMap）。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from GalTransl.Backend.ForPlotRouteMap import (
    ForPlotRouteMap,
    _format_route_context,
    _get_route_for_file,
)


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


class GetRouteForFileTests(unittest.TestCase):
    """_get_route_for_file：精确命中优先，未命中时 NFKC 归一兜底。"""

    ROUTE_MAP = {
        "文件归属": {
            "01_共通_01_01.json": "主线",
            "０１＿共通＿０１＿０２.json": "TRUE END",
            "フリー拠点イベント01_01.json": "自由拠点活动",
        }
    }

    def test_exact_hit(self) -> None:
        self.assertEqual(
            _get_route_for_file(self.ROUTE_MAP, "01_共通_01_01.json"), "主线"
        )

    def test_fullwidth_filename_hits_halfwidth_key(self) -> None:
        self.assertEqual(
            _get_route_for_file(self.ROUTE_MAP, "０１＿共通＿０１＿０１.json"), "主线"
        )

    def test_halfwidth_filename_hits_fullwidth_key(self) -> None:
        self.assertEqual(
            _get_route_for_file(self.ROUTE_MAP, "01_共通_01_02.json"), "TRUE END"
        )

    def test_mixed_width_hits_halfwidth_key(self) -> None:
        self.assertEqual(
            _get_route_for_file(self.ROUTE_MAP, "フリー拠点イベント01＿01.json"),
            "自由拠点活动",
        )

    def test_miss_returns_none(self) -> None:
        self.assertIsNone(_get_route_for_file(self.ROUTE_MAP, "ghost.json"))
        self.assertIsNone(_get_route_for_file(self.ROUTE_MAP, ""))
        self.assertIsNone(_get_route_for_file(None, "a.json"))
        self.assertIsNone(_get_route_for_file({}, "a.json"))

    def test_blank_route_value_not_matched(self) -> None:
        rm = {"文件归属": {"a.json": "  ", "０１＿ｂ.json": "线B"}}
        self.assertIsNone(_get_route_for_file(rm, "a.json"))
        self.assertEqual(_get_route_for_file(rm, "01_b.json"), "线B")


class FormatRouteContextTests(unittest.TestCase):
    """_format_route_context：全角文件名经归一兜底后可取到路线剧情块。"""

    ROUTE_MAP = {
        "mermaid": "graph TD; A-->B;",
        "文件归属": {"01_共通_01_01.json": "主线"},
        "节点剧情": {"主线": "共同篇剧情概要。"},
    }

    def test_fullwidth_filename_gets_route_context(self) -> None:
        ctx = _format_route_context(self.ROUTE_MAP, "０１＿共通＿０１＿０１.json")
        self.assertIn("主线", ctx)
        self.assertIn("共同篇剧情概要", ctx)

    def test_unknown_file_returns_empty(self) -> None:
        self.assertEqual(_format_route_context(self.ROUTE_MAP, "ghost.json"), "")

    def test_blank_mermaid_returns_empty(self) -> None:
        rm = {**self.ROUTE_MAP, "mermaid": "  "}
        self.assertEqual(_format_route_context(rm, "０１＿共通＿０１＿０１.json"), "")


class CheckFileCoverageTests(unittest.TestCase):
    """_check_file_coverage：全角/半角写法差异不计缺失，真实缺口照报。"""

    def _engine(self) -> ForPlotRouteMap:
        eng = ForPlotRouteMap.__new__(ForPlotRouteMap)
        eng.pj_config = SimpleNamespace()
        return eng

    def _run(self, file_map: dict, fids: list) -> list:
        eng = self._engine()
        data = {"文件归属": file_map}
        fm = {fid: SimpleNamespace() for fid in fids}
        with patch(
            "GalTransl.Backend.metadata.load_file_metadata_map", return_value=fm
        ):
            with self.assertLogs(level="WARNING") as logs:
                eng._check_file_coverage(data)
            return logs.output

    def test_width_variants_not_reported_missing(self) -> None:
        # 路线图键半角、真实文件全角：不应误报缺失（无 warning 输出）
        eng = self._engine()
        data = {"文件归属": {"01_共通.json": "主线"}}
        fm = {"０１＿共通.json": SimpleNamespace()}
        with patch(
            "GalTransl.Backend.metadata.load_file_metadata_map", return_value=fm
        ):
            with self.assertNoLogs(level="WARNING"):
                eng._check_file_coverage(data)

    def test_real_gap_still_reported(self) -> None:
        logs = self._run(
            {"01_共通.json": "主线"},
            ["０１＿共通.json", "dms_init.json"],
        )
        self.assertTrue(any("dms_init.json" in m for m in logs))


class GroundToRealFilesTests(unittest.TestCase):
    """落盘前文件名规整：LLM 半角写法按 gt_input 真实名改写（NFKC 唯一命中）。"""

    def test_halfwidth_keys_and_labels_ground_to_real_names(self) -> None:
        data = {
            "mermaid": 'flowchart TD\n  A["アペンド_03.json"] --> B["01_共通.json"]',
            "文件归属": {"アペンド_03.json": "梦幻迷宫", "01_共通.json": "共通线"},
            "节点剧情": {"梦幻迷宫": "摘要"},
        }
        out = ForPlotRouteMap._ground_to_real_files(
            data, ["アペンド＿０３.json", "01_共通.json"]
        )
        self.assertEqual(list(out["文件归属"]), ["アペンド＿０３.json", "01_共通.json"])
        self.assertIn('A["アペンド＿０３.json"]', out["mermaid"])
        # 精确名保持原样
        self.assertIn('B["01_共通.json"]', out["mermaid"])
        # 「节点剧情」键为路线名，不参与规整
        self.assertEqual(out["节点剧情"], {"梦幻迷宫": "摘要"})

    def test_unquoted_label_rewrites_to_quoted(self) -> None:
        data = {
            "mermaid": "flowchart TD\n  A[アペンド_03.json]",
            "文件归属": {},
            "节点剧情": {},
        }
        out = ForPlotRouteMap._ground_to_real_files(data, ["アペンド＿０３.json"])
        self.assertIn('A["アペンド＿０３.json"]', out["mermaid"])

    def test_unknown_and_ambiguous_names_kept(self) -> None:
        data = {
            "mermaid": 'flowchart TD\n  X["不存在.json"]',
            "文件归属": {"不存在.json": "线"},
            "节点剧情": {},
        }
        # ０１.json 与 0１.json NFKC 归一后同名，视为歧义（此处标签未命中即保留）
        out = ForPlotRouteMap._ground_to_real_files(
            data, ["アペンド＿０３.json", "０１.json", "0１.json"]
        )
        self.assertIn('X["不存在.json"]', out["mermaid"])
        self.assertEqual(out["文件归属"], {"不存在.json": "线"})

    def test_no_real_files_noop_on_keys(self) -> None:
        data = {
            "mermaid": 'flowchart TD\n  A["x.json"]',
            "文件归属": {"x.json": "线"},
            "节点剧情": {},
        }
        out = ForPlotRouteMap._ground_to_real_files(data, [])
        self.assertEqual(out["文件归属"], {"x.json": "线"})


if __name__ == "__main__":
    unittest.main()
