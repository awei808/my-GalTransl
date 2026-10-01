"""路线图读写（mcp_tools.write_route_map / read_route_map）的单测。

回归背景：0.6.0 移除内置简易 agent（server_agent.py）后，路线图读写逻辑迁入
mcp_tools.py，供 MCP 写工具与后续 agent 复用。本文件承接原
tests/test_agent_endpoint.py::RouteMapToolTests 的用例，避免迁移丢覆盖。

覆盖：读取缺失文件、写入后回读、部分写入保留旧字段、非法 mermaid 拒绝、
空内容拒绝、原子写（拒绝时不落盘）。
"""
import json
import os
import shutil
import tempfile
import unittest

from GalTransl.mcp_tools import read_route_map, route_map_path, write_route_map


class RouteMapToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project_dir = tempfile.mkdtemp(prefix="gt_route_map_")

    def tearDown(self) -> None:
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def test_path_is_under_pass0_cache(self) -> None:
        self.assertEqual(
            route_map_path(self.project_dir),
            os.path.join(self.project_dir, "transl_cache", "pass0_cache", "PlotRouteMap.json"),
        )

    def test_read_missing_route_map_returns_exists_false(self) -> None:
        result = read_route_map(self.project_dir)
        self.assertFalse(result["exists"])
        self.assertIsNone(result["entry"])

    def test_write_then_read_roundtrip(self) -> None:
        mermaid = 'flowchart TD\n  A["01_a.json"] --> B["02_b.json"]'
        result = write_route_map(
            self.project_dir,
            {"mermaid": mermaid, "文件归属": {"01_a.json": "共通线"}, "节点剧情": {"共通线": "序幕"}},
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["file_count"], 1)
        self.assertEqual(result["route_count"], 1)
        with open(route_map_path(self.project_dir), "r", encoding="utf-8") as f:
            entry = json.load(f)
        self.assertEqual(entry["mermaid"], mermaid)
        self.assertEqual(entry["文件归属"], {"01_a.json": "共通线"})
        read_back = read_route_map(self.project_dir)
        self.assertTrue(read_back["exists"])
        self.assertEqual(read_back["entry"]["节点剧情"], {"共通线": "序幕"})

    def test_partial_write_preserves_old_fields(self) -> None:
        write_route_map(
            self.project_dir,
            {
                "结构类型": "树",
                "用户大纲": "双女主线",
                "mermaid": 'flowchart TD\n  A["01_a.json"]',
                "文件归属": {"01_a.json": "共通线"},
            },
        )
        # 只改 mermaid：结构类型/用户大纲/文件归属 应保留
        write_route_map(self.project_dir, {"mermaid": 'flowchart TD\n  A["01_a.json"] --> B["02_b.json"]'})
        with open(route_map_path(self.project_dir), "r", encoding="utf-8") as f:
            entry = json.load(f)
        self.assertEqual(entry["结构类型"], "树")
        self.assertEqual(entry["用户大纲"], "双女主线")
        self.assertEqual(entry["文件归属"], {"01_a.json": "共通线"})

    def test_invalid_mermaid_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            write_route_map(self.project_dir, {"mermaid": "不是 mermaid 开头"})
        with self.assertRaises(ValueError):
            write_route_map(
                self.project_dir,
                {"mermaid": 'flowchart TD\n  subgraph 共·通线\n    A["01_a.json"]\n  end'},
            )
        # 校验失败不得落盘（原子写）
        self.assertFalse(os.path.isfile(route_map_path(self.project_dir)))

    def test_empty_mermaid_and_file_map_rejected(self) -> None:
        with self.assertRaises(ValueError):
            write_route_map(self.project_dir, {"mermaid": ""})

    def test_corrupt_existing_file_does_not_break_write(self) -> None:
        # 旧文件损坏时应视为无旧值，而不是让写入失败
        path = route_map_path(self.project_dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ 不是合法 JSON")
        result = write_route_map(self.project_dir, {"mermaid": 'flowchart TD\n  A["01_a.json"]'})
        self.assertTrue(result["success"])

    def test_read_corrupt_file_reports_exists_false_with_error(self) -> None:
        path = route_map_path(self.project_dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ 不是合法 JSON")
        result = read_route_map(self.project_dir)
        self.assertFalse(result["exists"])
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
