"""全局分析阶段决策树（resolve_global_analysis_mode）与阶段关系测试。

锁定回退口径：路线图缺失/mermaid 为空/覆盖缺口/路线数超限 → 全文回退；
否则按路线分片汇总。宁全勿缺是硬约束。
"""

import unittest

from GalTransl.Frontend.llm_pipeline import resolve_global_analysis_mode
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

    def test_coverage_gap_falls_back(self) -> None:
        # c.json 未归入任何路线：宁可全文回退，不可静默丢失
        route_map = {"mermaid": "graph TD;", "文件归属": {"a.json": "线A"}}
        mode, routes, _ = resolve_global_analysis_mode(route_map, TEXTS, 12)
        self.assertEqual(mode, "fulltext")
        self.assertEqual(routes, {})

    def test_too_many_routes_falls_back(self) -> None:
        file_map = {f"f{i}.json": f"路线{i}" for i in range(13)}
        texts = {f"/p/f{i}.json": "文本" for i in range(13)}
        route_map = {"mermaid": "graph TD;", "文件归属": file_map}
        mode, routes, _ = resolve_global_analysis_mode(route_map, texts, max_routes=12)
        self.assertEqual(mode, "fulltext")
        # 恰好等于上限时不回退
        mode12, routes12, _ = resolve_global_analysis_mode(route_map, texts, max_routes=13)
        self.assertEqual(mode12, "routes")
        self.assertEqual(len(routes12), 13)

    def test_all_unmatched_falls_back(self) -> None:
        route_map = {"mermaid": "graph TD;", "文件归属": {"ghost.json": "线X"}}
        mode, routes, unmatched = resolve_global_analysis_mode(route_map, TEXTS, 12)
        self.assertEqual(mode, "fulltext")
        self.assertEqual(routes, {})
        self.assertEqual(unmatched, ["ghost.json"])


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
