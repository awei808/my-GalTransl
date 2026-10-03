"""全局分析阶段决策树（resolve_global_analysis_mode）与阶段关系测试。

锁定口径：路线图缺失/mermaid 为空/划分不出任何可分析路线 → 全文回退；
覆盖缺口（未归属文件）合成「未归属文件」独立分片继续分片汇总，路线数
超限仅软告警仍走分片（全文回退在大项目必然超限，不可行）。每个文件都
必须被分析覆盖、不静默丢失，是硬约束。
"""

import unittest

from GalTransl.Frontend.llm_pipeline import (
    UNASSIGNED_ROUTE_NAME,
    resolve_global_analysis_mode,
)
from GalTransl.Frontend.pipeline_stages import compute_skip_reasons

TEXTS = {
    "/p/a.json": "A文本",
    "/p/b.json": "B文本",
    "/p/c.json": "C文本",
}

ROUTE_MAP_OK = {
    "mermaid": "graph TD; A-->B;",
    "文件归属": {"a.json": "线A", "b.json": "线B", "c.json": "线A"},
}


class ResolveGlobalAnalysisModeTests(unittest.TestCase):
    def test_valid_route_map_uses_routes_mode(self) -> None:
        mode, routes, unmatched = resolve_global_analysis_mode(
            ROUTE_MAP_OK, TEXTS, max_routes=12
        )
        self.assertEqual(mode, "routes")
        self.assertEqual(routes["线A"], ["/p/a.json", "/p/c.json"])
        self.assertEqual(routes["线B"], ["/p/b.json"])
        self.assertEqual(unmatched, [])
        # 全覆盖时不产生合成分片
        self.assertNotIn(UNASSIGNED_ROUTE_NAME, routes)

    def test_missing_route_map_falls_back(self) -> None:
        mode, routes, unmatched = resolve_global_analysis_mode(None, TEXTS, 12)
        self.assertEqual(mode, "fulltext")
        self.assertEqual(routes, {})
        self.assertEqual(unmatched, [])

    def test_blank_mermaid_falls_back(self) -> None:
        # mermaid 为空 = 路线图未生成成功（与 ForPlotRouteMap 下游回退口径一致）
        for bad in ({}, {"文件归属": {"a.json": "线A"}}, {"mermaid": " "}):
            with self.subTest(bad=bad):
                mode, _, _ = resolve_global_analysis_mode(bad, TEXTS, 12)
                self.assertEqual(mode, "fulltext")

    def test_coverage_gap_synthesizes_unassigned_shard(self) -> None:
        # b/c 未归入任何路线：不再整体回退全文，合成独立分片保证覆盖
        route_map = {"mermaid": "graph TD;", "文件归属": {"a.json": "线A"}}
        mode, routes, unmatched = resolve_global_analysis_mode(
            route_map, TEXTS, 12
        )
        self.assertEqual(mode, "routes")
        self.assertEqual(routes["线A"], ["/p/a.json"])
        self.assertEqual(
            routes[UNASSIGNED_ROUTE_NAME], ["/p/b.json", "/p/c.json"]
        )
        self.assertEqual(unmatched, [])

    def test_too_many_routes_still_routes_mode(self) -> None:
        # 路线数超限仅软告警（由调用方做），不再回退全文
        file_map = {f"f{i}.json": f"路线{i}" for i in range(13)}
        texts = {f"/p/f{i}.json": "文本" for i in range(13)}
        route_map = {"mermaid": "graph TD;", "文件归属": file_map}
        mode, routes, _ = resolve_global_analysis_mode(route_map, texts, max_routes=12)
        self.assertEqual(mode, "routes")
        self.assertEqual(len(routes), 13)
        # 未超限时同样走分片（护栏不改变正常路径）
        mode_ok, routes_ok, _ = resolve_global_analysis_mode(
            route_map, texts, max_routes=13
        )
        self.assertEqual(mode_ok, "routes")
        self.assertEqual(len(routes_ok), 13)

    def test_all_unmatched_falls_back(self) -> None:
        # 归属键全部悬空 = 划分不出任何可分析路线，只能全文回退
        route_map = {"mermaid": "graph TD;", "文件归属": {"ghost.json": "线X"}}
        mode, routes, unmatched = resolve_global_analysis_mode(route_map, TEXTS, 12)
        self.assertEqual(mode, "fulltext")
        self.assertEqual(routes, {})
        self.assertEqual(unmatched, ["ghost.json"])

    def test_fullwidth_texts_with_halfwidth_keys_use_routes_mode(self) -> None:
        # 路线图键半角、真实文件全角：NFKC 兜底命中后不再算覆盖缺口
        texts = {
            "/p/０１＿共通.json": "A文本",
            "/p/０２＿共通.json": "B文本",
        }
        route_map = {
            "mermaid": "graph TD; A-->B;",
            "文件归属": {"01_共通.json": "主线", "02_共通.json": "TRUE END"},
        }
        mode, routes, unmatched = resolve_global_analysis_mode(
            route_map, texts, max_routes=12
        )
        self.assertEqual(mode, "routes")
        self.assertEqual(unmatched, [])
        self.assertEqual(routes["主线"], ["/p/０１＿共通.json"])
        self.assertNotIn(UNASSIGNED_ROUTE_NAME, routes)

    def test_real_coverage_gap_synthesizes_unassigned_shard_with_nfkc(self) -> None:
        # 宽度兜底只救键名写法；真实未归属文件合成独立分片而非回退
        texts = {
            "/p/０１＿共通.json": "A文本",
            "/p/０２＿共通.json": "B文本",
        }
        route_map = {
            "mermaid": "graph TD; A-->B;",
            "文件归属": {"01_共通.json": "主线"},
        }
        mode, routes, unmatched = resolve_global_analysis_mode(
            route_map, texts, max_routes=12
        )
        self.assertEqual(mode, "routes")
        self.assertEqual(routes["主线"], ["/p/０１＿共通.json"])
        self.assertEqual(
            routes[UNASSIGNED_ROUTE_NAME], ["/p/０２＿共通.json"]
        )
        self.assertEqual(unmatched, [])

    def test_unassigned_name_collision_merges_into_existing_route(self) -> None:
        # 路线图恰好有名为「未归属文件」的路线时，未覆盖文件并入该路线
        texts = {"a.json": "A文本", "b.json": "B文本"}
        texts = {f"/p/{k}": v for k, v in texts.items()}
        route_map = {
            "mermaid": "graph TD;",
            "文件归属": {"a.json": "未归属文件"},
        }
        mode, routes, _ = resolve_global_analysis_mode(route_map, texts, 12)
        self.assertEqual(mode, "routes")
        self.assertEqual(routes[UNASSIGNED_ROUTE_NAME], ["/p/a.json", "/p/b.json"])

    def test_unmatched_keys_and_gap_coexist(self) -> None:
        # 悬空归属键与真实覆盖缺口并存：unmatched 照报，缺口照合成
        route_map = {
            "mermaid": "graph TD;",
            "文件归属": {"a.json": "线A", "ghost.json": "线X"},
        }
        mode, routes, unmatched = resolve_global_analysis_mode(
            route_map, TEXTS, 12
        )
        self.assertEqual(mode, "routes")
        self.assertEqual(unmatched, ["ghost.json"])
        self.assertEqual(routes["线A"], ["/p/a.json"])
        self.assertEqual(
            routes[UNASSIGNED_ROUTE_NAME], ["/p/b.json", "/p/c.json"]
        )


class StageRelationTests(unittest.TestCase):
    def test_global_analysis_not_skipped_when_plot_route_disabled(self) -> None:
        # 全局分析只依赖压缩：路线图被禁用时仍执行（走全文回退），不得连带跳过
        reasons = compute_skip_reasons({"plot_route": False}, compressed_texts_present=True)
        self.assertNotIn("global_analysis", reasons)
        self.assertIn("plot_route", reasons)

    def test_stage_handler_registered(self) -> None:
        # 处理函数与清单键不得脱钩
        from GalTransl.Frontend import llm_pipeline

        self.assertIn("global_analysis", llm_pipeline._STAGE_HANDLERS)

    def test_standalone_engine_stage_aliases(self) -> None:
        # 独立引擎运行态串映射到全局分析阶段（索引 5）
        from GalTransl import server_runtime

        self.assertEqual(server_runtime._compute_stage_index("路线分析"), 5)
        self.assertEqual(server_runtime._compute_stage_index("路线分析完成"), 5)
        self.assertEqual(server_runtime._compute_stage_index("全局分析汇总"), 5)
        self.assertEqual(server_runtime._compute_stage_index("全局分析汇总完成"), 5)


if __name__ == "__main__":
    unittest.main()
