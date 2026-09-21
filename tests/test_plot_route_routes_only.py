"""ForPlotRouteMap routesOnly 模式（0.5.0 补进 §3.4.3-3）测试。

覆盖：
  - routesOnly 保留既有 mermaid 与「文件归属」，仅替换「节点剧情」
  - 无既有产物 / 无「文件归属」/ 既有 mermaid 不合法 → 返回 False 且不写盘
  - 输出缺少「节点剧情」→ 追加纠正提示重试 1 次，仍失败则不写盘
  - 首次失败、重试成功 → 保存成功
  - 节点剧情未覆盖全部既有路线 → warning 但不阻断保存
  - _format_route_assignments / _extract_node_plots 的文本口径
  - full 模式行为零回归（已存在且非 force_regen → 跳过并返回 True）
  - _iter_file_summaries 重构后 _build_file_summaries 输出不变
"""

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from GalTransl import PASS0_CACHE_DIR
from GalTransl.Backend.ForPlotRouteMap import (
    ROUTE_MODE_FULL,
    ROUTE_MODE_ROUTES_ONLY,
    ForPlotRouteMap,
)

_VALID_MERMAID = 'flowchart TD\n  subgraph prologue["序章"]\n    A["a.txt"]\n  end'
_META = {
    "a.txt": SimpleNamespace(character=["甲"], plot="开局剧情", tags=["日常"]),
    "b.txt": SimpleNamespace(character=["乙"], plot="支线剧情", tags=["冲突"]),
}


class _FakeConfig:
    """最小项目配置桩：仅提供缓存路径。"""

    def __init__(self, project_dir: str) -> None:
        self.project_dir = project_dir

    def getCachePath(self) -> str:
        return os.path.join(self.project_dir, "transl_cache")

    def getProjectDir(self) -> str:
        return self.project_dir


def _write_existing(project_dir, file_map, mermaid=_VALID_MERMAID):
    out_dir = os.path.join(project_dir, "transl_cache", PASS0_CACHE_DIR)
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "结构类型": "树",
        "用户大纲": "",
        "mermaid": mermaid,
        "文件归属": dict(file_map),
        "节点剧情": {r: "旧摘要" for r in set(file_map.values())},
    }
    path = os.path.join(out_dir, "PlotRouteMap.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def _read_saved(project_dir):
    path = os.path.join(project_dir, "transl_cache", PASS0_CACHE_DIR, "PlotRouteMap.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def make_engine(project_dir: str) -> ForPlotRouteMap:
    """用 __new__ 打桩，避免触发 BaseEngine.__init__（需真实 token pool）。"""
    t = ForPlotRouteMap.__new__(ForPlotRouteMap)
    t.pj_config = _FakeConfig(project_dir)
    t._prompt_block_toggles = lambda: {"globalPrompt": False}
    t._build_global_prompt_block = lambda: ""
    t._record_runtime_error = lambda **kw: None
    return t


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.engine = make_engine(self.dir)
        patcher = patch(
            "GalTransl.Backend.metadata.load_file_metadata_map", return_value=_META
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _reply(self, payload: dict) -> str:
        return json.dumps(payload, ensure_ascii=False)


class RefreshNodePlotsTests(_Base):
    async def test_keeps_mermaid_and_routes_replaces_node_plots(self) -> None:
        _write_existing(self.dir, {"a.txt": "序章", "b.txt": "支线"})
        self.engine.ask_chatbot = AsyncMock(
            return_value=(self._reply({"节点剧情": {"序章": "新序章", "支线": "新支线"}}), 0)
        )
        ok = await self.engine.batch_translate(route_mode=ROUTE_MODE_ROUTES_ONLY)
        self.assertTrue(ok)
        saved = _read_saved(self.dir)
        self.assertEqual(saved["mermaid"], _VALID_MERMAID)  # 用户拓扑原样保留
        self.assertEqual(saved["文件归属"], {"a.txt": "序章", "b.txt": "支线"})
        self.assertEqual(saved["节点剧情"], {"序章": "新序章", "支线": "新支线"})

    async def test_no_existing_map_returns_false_and_writes_nothing(self) -> None:
        self.engine.ask_chatbot = AsyncMock(
            return_value=(self._reply({"节点剧情": {"序章": "x"}}), 0)
        )
        ok = await self.engine.batch_translate(route_mode=ROUTE_MODE_ROUTES_ONLY)
        self.assertFalse(ok)
        self.engine.ask_chatbot.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(self.dir, "transl_cache")))

    async def test_empty_file_map_returns_false(self) -> None:
        _write_existing(self.dir, {})
        self.engine.ask_chatbot = AsyncMock(
            return_value=(self._reply({"节点剧情": {"序章": "x"}}), 0)
        )
        self.assertFalse(
            await self.engine.batch_translate(route_mode=ROUTE_MODE_ROUTES_ONLY)
        )
        self.engine.ask_chatbot.assert_not_called()

    async def test_invalid_existing_mermaid_returns_false(self) -> None:
        _write_existing(self.dir, {"a.txt": "序章"}, mermaid="hello world")
        self.engine.ask_chatbot = AsyncMock(
            return_value=(self._reply({"节点剧情": {"序章": "x"}}), 0)
        )
        self.assertFalse(
            await self.engine.batch_translate(route_mode=ROUTE_MODE_ROUTES_ONLY)
        )
        self.engine.ask_chatbot.assert_not_called()

    async def test_missing_node_plots_retries_then_fails(self) -> None:
        _write_existing(self.dir, {"a.txt": "序章"})
        self.engine.ask_chatbot = AsyncMock(
            return_value=(self._reply({"mermaid": _VALID_MERMAID}), 0)
        )
        ok = await self.engine.batch_translate(route_mode=ROUTE_MODE_ROUTES_ONLY)
        self.assertFalse(ok)
        self.assertEqual(self.engine.ask_chatbot.await_count, 2)  # 首次 + 重试 1 次
        self.assertEqual(_read_saved(self.dir)["节点剧情"], {"序章": "旧摘要"})

    async def test_retry_succeeds(self) -> None:
        _write_existing(self.dir, {"a.txt": "序章"})
        self.engine.ask_chatbot = AsyncMock(side_effect=[
            ("不是 JSON", 0),
            (self._reply({"节点剧情": {"序章": "重试后"}}), 0),
        ])
        ok = await self.engine.batch_translate(route_mode=ROUTE_MODE_ROUTES_ONLY)
        self.assertTrue(ok)
        self.assertEqual(_read_saved(self.dir)["节点剧情"], {"序章": "重试后"})

    async def test_partial_route_coverage_still_saves(self) -> None:
        _write_existing(self.dir, {"a.txt": "序章", "b.txt": "支线"})
        self.engine.ask_chatbot = AsyncMock(
            return_value=(self._reply({"节点剧情": {"序章": "只给一条"}}), 0)
        )
        ok = await self.engine.batch_translate(route_mode=ROUTE_MODE_ROUTES_ONLY)
        self.assertTrue(ok)
        self.assertEqual(_read_saved(self.dir)["节点剧情"], {"序章": "只给一条"})

    async def test_prompt_contains_route_assignments(self) -> None:
        _write_existing(self.dir, {"a.txt": "序章"})
        self.engine.ask_chatbot = AsyncMock(
            return_value=(self._reply({"节点剧情": {"序章": "x"}}), 0)
        )
        await self.engine.batch_translate(route_mode=ROUTE_MODE_ROUTES_ONLY)
        prompt = self.engine.ask_chatbot.await_args.kwargs["messages"][1]["content"]
        self.assertIn("路线「序章」", prompt)
        self.assertIn("a.txt", prompt)
        self.assertNotIn("[route_assignments]", prompt)  # 占位符已替换
        self.assertNotIn("[file_summaries]", prompt)

    async def test_llm_failure_returns_false(self) -> None:
        _write_existing(self.dir, {"a.txt": "序章"})
        self.engine.ask_chatbot = AsyncMock(side_effect=RuntimeError("boom"))
        self.assertFalse(
            await self.engine.batch_translate(route_mode=ROUTE_MODE_ROUTES_ONLY)
        )


class FullModeRegressionTests(_Base):
    async def test_full_mode_skips_when_exists(self) -> None:
        _write_existing(self.dir, {"a.txt": "序章"})
        self.engine.ask_chatbot = AsyncMock()
        ok = await self.engine.batch_translate(route_mode=ROUTE_MODE_FULL)
        self.assertTrue(ok)
        self.engine.ask_chatbot.assert_not_called()

    async def test_default_route_mode_is_full(self) -> None:
        _write_existing(self.dir, {"a.txt": "序章"})
        self.engine.ask_chatbot = AsyncMock()
        ok = await self.engine.batch_translate()
        self.assertTrue(ok)
        self.engine.ask_chatbot.assert_not_called()


class FormatRouteAssignmentsTests(unittest.TestCase):
    def test_groups_files_by_route(self) -> None:
        out = ForPlotRouteMap._format_route_assignments(
            {"a.txt": "序章", "b.txt": "序章", "c.txt": "支线"}
        )
        self.assertIn("路线「序章」（2 个文件）：a.txt、b.txt", out)
        self.assertIn("路线「支线」（1 个文件）：c.txt", out)

    def test_blank_route_name_is_kept_as_unassigned(self) -> None:
        out = ForPlotRouteMap._format_route_assignments({"a.txt": "  "})
        self.assertIn("（未归属）", out)
        self.assertIn("a.txt", out)

    def test_empty_map_returns_empty(self) -> None:
        self.assertEqual(ForPlotRouteMap._format_route_assignments({}), "")


class ExtractNodePlotsTests(unittest.TestCase):
    def test_cleans_non_string_and_blank_values(self) -> None:
        got = ForPlotRouteMap._extract_node_plots(
            {"节点剧情": {"甲": "摘要", "乙": "", "丙": None, "丁": 3}}
        )
        self.assertEqual(got, {"甲": "摘要"})

    def test_missing_field_returns_empty(self) -> None:
        self.assertEqual(ForPlotRouteMap._extract_node_plots({}), {})
        self.assertEqual(ForPlotRouteMap._extract_node_plots(None), {})
        self.assertEqual(ForPlotRouteMap._extract_node_plots({"节点剧情": []}), {})

    def test_strips_whitespace(self) -> None:
        got = ForPlotRouteMap._extract_node_plots({"节点剧情": {"甲": "  摘要  "}})
        self.assertEqual(got, {"甲": "摘要"})


class FileSummariesRefactorTests(unittest.TestCase):
    """_build_file_summaries 抽出 _iter_file_summaries 后输出必须不变。"""

    def test_joined_text_matches_iter_items(self) -> None:
        t = ForPlotRouteMap.__new__(ForPlotRouteMap)
        t.pj_config = _FakeConfig(tempfile.gettempdir())
        with patch(
            "GalTransl.Backend.metadata.load_file_metadata_map", return_value=_META
        ):
            items = t._iter_file_summaries()
            joined = t._build_file_summaries()
        self.assertEqual(len(items), 2)
        self.assertEqual([fid for fid, _ in items], ["a.txt", "b.txt"])
        self.assertEqual(joined, "\n".join(text for _, text in items))
        self.assertIn("文件 a.txt：", joined)
        self.assertIn("- 角色：甲", joined)
        self.assertIn("- 剧情：开局剧情", joined)
        self.assertIn("- 标签：日常", joined)

    def test_no_metadata_returns_empty(self) -> None:
        t = ForPlotRouteMap.__new__(ForPlotRouteMap)
        t.pj_config = _FakeConfig(tempfile.gettempdir())
        with patch(
            "GalTransl.Backend.metadata.load_file_metadata_map", return_value={}
        ):
            self.assertEqual(t._iter_file_summaries(), [])
            self.assertEqual(t._build_file_summaries(), "")


if __name__ == "__main__":
    unittest.main()
