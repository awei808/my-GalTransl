"""路线图工作台 agent 模块（server_agent）的单测。

覆盖：工具调用循环（直接回复 / 工具轮次 / 工具失败回传 / 轮数上限）、
路线图读写工具（合并保留旧值、mermaid 校验拒绝、原子写）、未知工具报错。
LLM 通过假 client 模拟，不访问网络。
"""
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace


def _text_response(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))])


def _tool_call_response(*tool_calls):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=list(tool_calls)))]
    )


def _tool_call(name: str, args: str, call_id: str = "call_1"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=args))


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=_FakeCompletions(responses))


class AgentLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        from GalTransl.server_agent import run_agent_loop

        self.run_agent_loop = run_agent_loop

    def test_direct_reply_without_tools(self) -> None:
        client = _FakeClient([_text_response("好的，已了解。")])
        calls = []
        result = self.run_agent_loop(client, "m", "sys", "你好", lambda n, a: calls.append(n) or {})
        self.assertEqual(result["reply"], "好的，已了解。")
        self.assertEqual(result["steps"], [])
        self.assertEqual(calls, [])

    def test_tool_round_then_reply(self) -> None:
        client = _FakeClient(
            [
                _tool_call_response(_tool_call("read_route_map", "{}")),
                _text_response("已读取路线图。"),
            ]
        )
        dispatch = lambda name, args: {"exists": False, "entry": None}  # noqa: E731
        result = self.run_agent_loop(client, "m", "sys", "看看路线图", dispatch)
        self.assertEqual(result["reply"], "已读取路线图。")
        self.assertEqual(len(result["steps"]), 1)
        self.assertTrue(result["steps"][0]["ok"])
        self.assertEqual(result["steps"][0]["tool"], "read_route_map")
        # 工具结果以 tool 消息回传
        messages = client.chat.completions.last_kwargs["messages"]
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertIn("exists", messages[-1]["content"])

    def test_tool_failure_feeds_error_back(self) -> None:
        client = _FakeClient(
            [
                _tool_call_response(_tool_call("write_route_map", '{"mermaid": "flowchart TD"}')),
                _text_response("已修正。"),
            ]
        )

        def dispatch(name: str, args: dict) -> dict:
            raise ValueError("mermaid 校验失败")

        result = self.run_agent_loop(client, "m", "sys", "写入", dispatch)
        self.assertEqual(result["reply"], "已修正。")
        self.assertFalse(result["steps"][0]["ok"])
        self.assertIn("mermaid 校验失败", result["steps"][0]["result"]["error"])

    def test_round_limit_returns_notice(self) -> None:
        responses = [_tool_call_response(_tool_call("read_route_map", "{}")) for _ in range(3)]
        client = _FakeClient(responses)
        result = self.run_agent_loop(client, "m", "sys", "循环", lambda n, a: {"exists": False}, max_rounds=3)
        self.assertIn("上限", result["reply"])
        self.assertEqual(len(result["steps"]), 3)

    def test_multiple_tool_calls_in_one_round(self) -> None:
        client = _FakeClient(
            [
                _tool_call_response(
                    _tool_call("read_route_map", "{}", "call_a"),
                    _tool_call("search_file_metadata", '{"query": "琴美"}', "call_b"),
                ),
                _text_response("完成。"),
            ]
        )
        seen = []
        result = self.run_agent_loop(client, "m", "sys", "查", lambda n, a: seen.append(n) or {})
        self.assertEqual(seen, ["read_route_map", "search_file_metadata"])
        self.assertEqual(len(result["steps"]), 2)


class RouteMapToolTests(unittest.TestCase):
    def setUp(self) -> None:
        from GalTransl.server_agent import (
            _make_tool_dispatch,
            _tool_read_route_map,
            _tool_write_route_map,
        )

        self.read_tool = _tool_read_route_map
        self.write_tool = _tool_write_route_map
        self.dispatch = _make_tool_dispatch
        self.project_dir = tempfile.mkdtemp(prefix="gt_agent_")

    def tearDown(self) -> None:
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def _route_path(self) -> str:
        return os.path.join(self.project_dir, "transl_cache", "pass0_cache", "PlotRouteMap.json")

    def test_read_missing_route_map_returns_exists_false(self) -> None:
        result = self.read_tool(self.project_dir, {})
        self.assertFalse(result["exists"])
        self.assertIsNone(result["entry"])

    def test_write_then_read_roundtrip(self) -> None:
        mermaid = 'flowchart TD\n  A["01_a.json"] --> B["02_b.json"]'
        result = self.write_tool(
            self.project_dir,
            {"mermaid": mermaid, "文件归属": {"01_a.json": "共通线"}, "节点剧情": {"共通线": "序幕"}},
        )
        self.assertTrue(result["success"])
        with open(self._route_path(), "r", encoding="utf-8") as f:
            entry = json.load(f)
        self.assertEqual(entry["mermaid"], mermaid)
        self.assertEqual(entry["文件归属"], {"01_a.json": "共通线"})
        read_back = self.read_tool(self.project_dir, {})
        self.assertTrue(read_back["exists"])
        self.assertEqual(read_back["entry"]["节点剧情"], {"共通线": "序幕"})

    def test_partial_write_preserves_old_fields(self) -> None:
        self.write_tool(
            self.project_dir,
            {
                "结构类型": "树",
                "用户大纲": "双女主线",
                "mermaid": 'flowchart TD\n  A["01_a.json"]',
                "文件归属": {"01_a.json": "共通线"},
            },
        )
        # 只改 mermaid：结构类型/用户大纲/文件归属 应保留
        self.write_tool(self.project_dir, {"mermaid": 'flowchart TD\n  A["01_a.json"] --> B["02_b.json"]'})
        with open(self._route_path(), "r", encoding="utf-8") as f:
            entry = json.load(f)
        self.assertEqual(entry["结构类型"], "树")
        self.assertEqual(entry["用户大纲"], "双女主线")
        self.assertEqual(entry["文件归属"], {"01_a.json": "共通线"})

    def test_invalid_mermaid_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.write_tool(self.project_dir, {"mermaid": "不是 mermaid 开头"})
        with self.assertRaises(ValueError):
            self.write_tool(
                self.project_dir,
                {"mermaid": 'flowchart TD\n  subgraph 共·通线\n    A["01_a.json"]\n  end'},
            )
        self.assertFalse(os.path.isfile(self._route_path()))

    def test_empty_mermaid_and_file_map_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.write_tool(self.project_dir, {"mermaid": ""})

    def test_unknown_tool_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.dispatch(self.project_dir)("run_translation", {})

    def test_search_tool_rejects_empty_query(self) -> None:
        with self.assertRaises(ValueError):
            self.dispatch(self.project_dir)("search_file_metadata", {"query": " "})


if __name__ == "__main__":
    unittest.main()
