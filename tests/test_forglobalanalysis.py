"""ForGlobalAnalysis 全局汇总后端单元测试。

覆盖：
1. 汇总输入装配（mermaid/节点剧情/分片块，缺失退化与占位说明）；
2. batch_translate 汇总链路（LLM 打桩：成功落盘/调用失败/解析失败/空分片跳过）；
3. 分片有效性过滤（非 dict、缺分析内容的分片不进入汇总）；
4. _save_global_prompt 原子写升级（产物完整且无 .tmp 残留）。
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from typing import Dict, Optional
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from GalTransl.Backend.ForGlobalAnalysis import (  # noqa: E402
    ForGlobalAnalysis,
    format_mermaid_block,
    format_node_plots,
    format_shards_block,
)
from GalTransl.Backend.ForGlobalPrompt import ForGlobalPrompt  # noqa: E402
from GalTransl.DataValidator import validate_global_prompt  # noqa: E402

MERGED_JSON = json.dumps(
    {
        "游戏名称": "测试游戏",
        "剧情概述": "融合后的全游戏剧情概述。",
        "角色列表": [{"名称": "爱丽丝", "形象": "女主角"}, {"名称": "鲍勃"}],
        "世界观设定": "奇幻世界",
        "行文风格": "轻松日常",
        "题材标签": ["校园", "恋爱"],
    },
    ensure_ascii=False,
)

SHARD_A = {
    "路线名": "线A",
    "文件列表": ["/p/a.json", "/p/c.json"],
    "生成时间": "2026-09-27T12:00:00",
    "trans_by": "m1",
    "剧情概述": "线A剧情。",
    "角色列表": [{"名称": "爱丽丝"}],
    "世界观设定": "",
    "行文风格": "轻松",
    "题材标签": ["校园"],
}
SHARD_B = {
    "路线名": "线B",
    "文件列表": ["/p/b.json"],
    "生成时间": "2026-09-27T12:01:00",
    "trans_by": "m1",
    "剧情概述": "线B剧情。",
    "角色列表": [{"名称": "鲍勃"}],
    "世界观设定": "世界观",
    "行文风格": "日常",
    "题材标签": ["恋爱"],
}


class FormatBlockTests(unittest.TestCase):
    def test_mermaid_block_missing_and_present(self) -> None:
        self.assertEqual(format_mermaid_block(None), "（无路线图结构信息）")
        self.assertEqual(format_mermaid_block({}), "（无路线图结构信息）")
        block = format_mermaid_block({"mermaid": "graph TD; A-->B;"})
        self.assertIn("```mermaid", block)
        self.assertIn("graph TD", block)

    def test_node_plots_missing_and_present(self) -> None:
        self.assertEqual(format_node_plots(None), "（无节点剧情信息）")
        plots = format_node_plots({"节点剧情": {"线A": "A摘要", "线B": ""}})
        self.assertIn("- 线A：A摘要", plots)
        self.assertNotIn("线B", plots)

    def test_shards_block_sections(self) -> None:
        block = format_shards_block({"线A": SHARD_A, "线B": SHARD_B})
        self.assertIn("### 路线「线A」（文件：/p/a.json、/p/c.json）", block)
        self.assertIn("### 路线「线B」（文件：/p/b.json）", block)
        self.assertIn("线A剧情。", block)
        self.assertEqual(format_shards_block({}), "（无任何路线分析分片）")

    def test_shards_block_strips_internal_keys(self) -> None:
        shard = dict(SHARD_A, _internal="x")
        block = format_shards_block({"线A": shard})
        self.assertNotIn("_internal", block)

    def test_shards_block_tolerates_non_dict(self) -> None:
        block = format_shards_block({"坏分片": "不是dict"})
        self.assertIn("（异常分片）", block)
        self.assertIn("不是dict", block)


class MergeEngineTests(unittest.IsolatedAsyncioTestCase):
    """batch_translate 汇总链路：__new__ 绕开初始化，打桩 LLM 与落盘。"""

    def _engine(
        self, llm_result: tuple, saved: Optional[Dict[str, dict]] = None
    ) -> ForGlobalAnalysis:
        e = ForGlobalAnalysis.__new__(ForGlobalAnalysis)
        e.pj_config = SimpleNamespace(
            getKey=lambda key, default=None: default,
            runtime_project_dir="",
        )
        e.system_prompt = "SYS"
        e.trans_prompt = "[Input]"
        e._inject_guideline = False
        e._build_glossary_text = lambda: ""

        captured = {}

        def fake_build(input_src, gptdict="", external_info=""):
            return f"EXT({external_info})[RouteMermaid][RouteNodePlots][RouteShards]"

        e._build_prompt_request = fake_build

        async def fake_call(messages, *a, **kw):
            captured["messages"] = messages
            return llm_result

        e._call_llm_with_error_report = fake_call
        saved = saved if saved is not None else {}
        e._save_global_prompt = lambda data: saved.update(data)
        e._captured = captured
        e._saved = saved
        return e

    async def test_success_merges_and_saves(self) -> None:
        saved: Dict[str, dict] = {}
        e = self._engine((MERGED_JSON, SimpleNamespace(model_name="m")), saved)
        ok = await e.batch_translate(
            {"线A": SHARD_A, "线B": SHARD_B},
            route_map={"mermaid": "graph TD;", "节点剧情": {"线A": "A摘要"}},
            external_info="游戏信息",
        )
        self.assertTrue(ok)
        self.assertEqual(e._saved["游戏名称"], "测试游戏")
        self.assertEqual(len(e._saved["角色列表"]), 2)
        val = validate_global_prompt(e._saved)
        self.assertTrue(val["valid"])
        # 提示词装配：分片/结构/外部信息到达 user 消息
        user_msg = e._captured["messages"][1]["content"]
        self.assertIn("线A剧情。", user_msg)
        self.assertIn("graph TD", user_msg)
        self.assertIn("A摘要", user_msg)
        self.assertIn("游戏信息", user_msg)

    async def test_invalid_shards_filtered_before_llm(self) -> None:
        e = self._engine((MERGED_JSON, None))
        ok = await e.batch_translate(
            {
                "线A": SHARD_A,
                "坏分片": "不是dict",
                "空分片": {"路线名": "空", "剧情概述": "", "角色列表": []},
            }
        )
        self.assertTrue(ok)
        user_msg = e._captured["messages"][1]["content"]
        self.assertIn("线A", user_msg)
        self.assertNotIn("坏分片", user_msg)
        self.assertNotIn("空分片", user_msg)

    async def test_all_shards_invalid_skips_llm(self) -> None:
        e = self._engine((MERGED_JSON, None))
        called = []

        async def spy_call(*a, **kw):
            called.append(1)
            return (MERGED_JSON, None)

        e._call_llm_with_error_report = spy_call
        ok = await e.batch_translate({"空分片": {"路线名": "空"}})
        self.assertFalse(ok)
        self.assertEqual(called, [])

    async def test_empty_shards_fails(self) -> None:
        e = self._engine((MERGED_JSON, None))
        self.assertFalse(await e.batch_translate({}))
        self.assertFalse(await e.batch_translate("oops"))

    async def test_llm_failure_returns_false(self) -> None:
        e = self._engine((None, None))
        ok = await e.batch_translate({"线A": SHARD_A})
        self.assertFalse(ok)
        self.assertEqual(e._saved, {})

    async def test_invalid_json_returns_false(self) -> None:
        e = self._engine(("不是JSON", None))
        ok = await e.batch_translate({"线A": SHARD_A})
        self.assertFalse(ok)
        self.assertEqual(e._saved, {})

    async def test_validation_failure_returns_false(self) -> None:
        # 解析成功但角色列表为空 → validate_global_prompt 不过 → False
        bad = json.dumps(
            {"游戏名称": "测试游戏", "剧情概述": "概述", "角色列表": []},
            ensure_ascii=False,
        )
        e = self._engine((bad, None))
        ok = await e.batch_translate({"线A": SHARD_A})
        self.assertFalse(ok)
        self.assertEqual(e._saved, {})


class SaveGlobalPromptAtomicTests(unittest.TestCase):
    """_save_global_prompt 原子写升级：产物完整、无 .tmp 残留。"""

    def test_atomic_save_leaves_no_tmp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e = ForGlobalPrompt.__new__(ForGlobalPrompt)
            e.pj_config = SimpleNamespace(getCachePath=lambda: tmp)
            e._gp_lock = threading.Lock()
            data = {"剧情概述": "概述", "角色列表": [{"名称": "爱丽丝"}]}
            e._save_global_prompt(data)
            path = os.path.join(tmp, "pass0_cache", "GlobalPrompt.json")
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), data)
            self.assertEqual(
                [f for f in os.listdir(os.path.dirname(path)) if f.endswith(".tmp")],
                [],
            )


if __name__ == "__main__":
    unittest.main()
