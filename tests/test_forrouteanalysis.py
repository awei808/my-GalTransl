"""ForRouteAnalysis 路线分析后端单元测试。

覆盖：
1. 路线名安全化（非法字符/空白折叠/截断/空名回退）与分片路径冲突消解；
2. 路线图「文件归属」反推 路线->文件 映射（三级匹配/未匹配键/去重）；
3. 分片读写（坏分片跳过、孤儿分片过滤、.tmp 不误读）；
4. 分片复用判断（文件列表一致才可复用）；
5. analyze_route 单路线链路（LLM 打桩：成功落盘/调用失败/空文本/解析失败）；
6. batch_translate 全路线编排（无路线图失败、跳过已就绪分片、失败路线汇总）。
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from typing import Dict, List, Optional
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from GalTransl.Backend.ForRouteAnalysis import (  # noqa: E402
    ForRouteAnalysis,
    derive_route_file_map,
    load_route_shards,
    sanitize_route_name,
    shard_dir,
    shard_up_to_date,
)

VALID_ROUTE_JSON = json.dumps(
    {
        "游戏名称": "测试游戏",
        "剧情概述": "本路线剧情概述。",
        "角色列表": [{"名称": "爱丽丝", "形象": "女主角"}],
        "世界观设定": "奇幻世界",
        "行文风格": "轻松日常",
        "题材标签": ["校园", "恋爱"],
    },
    ensure_ascii=False,
)

FAKE_ROUTE_MAP = {"文件归属": {"a.json": "线A", "b.json": "线B", "c.json": "线A"}}
FAKE_TEXTS = {
    "/p/a.json": "A文本",
    "/p/b.json": "B文本",
    "/p/c.json": "C文本",
}


def _make_cfg(cache_root: str) -> SimpleNamespace:
    return SimpleNamespace(
        getKey=lambda key, default=None: default,
        getCachePath=lambda: cache_root,
        runtime_project_dir="",
    )


def _write_shard(
    cache_root: str, route: str, files: List[str], extra: Optional[dict] = None
) -> dict:
    d = shard_dir(_make_cfg(cache_root))
    os.makedirs(d, exist_ok=True)
    shard = {"路线名": route, "文件列表": files, **(extra or {})}
    with open(os.path.join(d, sanitize_route_name(route) + ".json"), "w", encoding="utf-8") as f:
        json.dump(shard, f, ensure_ascii=False)
    return shard


class SanitizeRouteNameTests(unittest.TestCase):
    def test_strips_illegal_chars(self) -> None:
        self.assertEqual(sanitize_route_name('a/b\\c:d*e?f"g<h>i|j'), "abcdefghij")

    def test_collapses_whitespace(self) -> None:
        self.assertEqual(sanitize_route_name("  线A　 线B  "), "线A 线B")

    def test_truncates_to_max_len(self) -> None:
        long_name = "路" * 80
        self.assertLessEqual(len(sanitize_route_name(long_name)), 50)

    def test_blank_falls_back(self) -> None:
        self.assertEqual(sanitize_route_name(""), "未命名路线")
        self.assertEqual(sanitize_route_name("///"), "未命名路线")


class ResolveShardPathTests(unittest.IsolatedAsyncioTestCase):
    def _engine(self, cache_root: str) -> ForRouteAnalysis:
        e = ForRouteAnalysis.__new__(ForRouteAnalysis)
        e.pj_config = _make_cfg(cache_root)
        return e

    def test_new_route_gets_plain_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = shard_dir(_make_cfg(tmp))
            os.makedirs(out, exist_ok=True)
            path = self._engine(tmp)._resolve_shard_path(out, "线A")
            self.assertTrue(path.endswith("线A.json"))

    def test_own_shard_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_shard(tmp, "线A", ["/p/a.json"])
            out = shard_dir(_make_cfg(tmp))
            path = self._engine(tmp)._resolve_shard_path(out, "线A")
            self.assertTrue(path.endswith("线A.json"))

    def test_collision_with_other_route_gets_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # 其他路线占用了「线A」的文件名（手工拷贝场景）
            _write_shard(tmp, "线B", [], extra={"_as": "线A"})
            d = shard_dir(_make_cfg(tmp))
            os.rename(
                os.path.join(d, "线B.json"), os.path.join(d, "线A.json")
            )
            path = self._engine(tmp)._resolve_shard_path(d, "线A")
            self.assertTrue(path.endswith("线A-2.json"))


class DeriveRouteFileMapTests(unittest.TestCase):
    def test_inverts_file_map_with_basename_match(self) -> None:
        routes, unmatched = derive_route_file_map(FAKE_ROUTE_MAP, FAKE_TEXTS)
        self.assertEqual(unmatched, [])
        self.assertEqual(routes["线A"], ["/p/a.json", "/p/c.json"])
        self.assertEqual(routes["线B"], ["/p/b.json"])

    def test_stem_match(self) -> None:
        routes, _ = derive_route_file_map(
            {"文件归属": {"a": "线A"}}, FAKE_TEXTS
        )
        self.assertEqual(routes["线A"], ["/p/a.json"])

    def test_unmatched_key_reported(self) -> None:
        routes, unmatched = derive_route_file_map(
            {"文件归属": {"a.json": "线A", "ghost.json": "线X"}}, FAKE_TEXTS
        )
        self.assertEqual(unmatched, ["ghost.json"])
        self.assertNotIn("线X", routes)

    def test_blank_route_name_is_unmatched(self) -> None:
        _, unmatched = derive_route_file_map(
            {"文件归属": {"a.json": "  "}}, FAKE_TEXTS
        )
        self.assertEqual(unmatched, ["a.json"])

    def test_invalid_or_empty_map(self) -> None:
        self.assertEqual(derive_route_file_map(None, FAKE_TEXTS), ({}, []))
        self.assertEqual(derive_route_file_map({}, FAKE_TEXTS), ({}, []))
        self.assertEqual(
            derive_route_file_map({"文件归属": "oops"}, FAKE_TEXTS), ({}, [])
        )


class LoadRouteShardsTests(unittest.TestCase):
    def test_roundtrip_and_orphan_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_shard(tmp, "线A", ["/p/a.json"])
            _write_shard(tmp, "线B", ["/p/b.json"])
            cfg = _make_cfg(tmp)
            shards = load_route_shards(cfg)
            self.assertEqual(set(shards.keys()), {"线A", "线B"})
            only_a = load_route_shards(cfg, valid_routes={"线A"})
            self.assertEqual(set(only_a.keys()), {"线A"})

    def test_skips_bad_and_tmp_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = shard_dir(_make_cfg(tmp))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "broken.json"), "w", encoding="utf-8") as f:
                f.write("{oops")
            with open(os.path.join(d, "leftover.json.tmp"), "w", encoding="utf-8") as f:
                f.write("{}")
            self.assertEqual(load_route_shards(_make_cfg(tmp)), {})

    def test_missing_dir_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_route_shards(_make_cfg(tmp)), {})


class ShardUpToDateTests(unittest.TestCase):
    def test_same_files_up_to_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_shard(tmp, "线A", ["/p/a.json", "/p/c.json"])
            cfg = _make_cfg(tmp)
            self.assertTrue(shard_up_to_date(cfg, "线A", ["/p/c.json", "/p/a.json"]))

    def test_changed_files_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_shard(tmp, "线A", ["/p/a.json"])
            self.assertFalse(shard_up_to_date(_make_cfg(tmp), "线A", ["/p/b.json"]))

    def test_missing_or_broken_shard_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(shard_up_to_date(_make_cfg(tmp), "线A", ["/p/a.json"]))
            d = shard_dir(_make_cfg(tmp))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "线A.json"), "w", encoding="utf-8") as f:
                f.write("{oops")
            self.assertFalse(shard_up_to_date(_make_cfg(tmp), "线A", ["/p/a.json"]))

    @unittest.skipUnless(os.name == "nt", "normcase 大小写折叠仅 Windows 语义")
    def test_case_insensitive_files_up_to_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_shard(tmp, "线A", ["/P/A.json"])
            self.assertTrue(shard_up_to_date(_make_cfg(tmp), "线A", ["/p/a.json"]))


class AnalyzeRouteTests(unittest.IsolatedAsyncioTestCase):
    """analyze_route 单路线链路：__new__ 绕开初始化，打桩 LLM 与提示词装配。"""

    def _engine(self, cache_root: str, llm_result) -> ForRouteAnalysis:
        e = ForRouteAnalysis.__new__(ForRouteAnalysis)
        e.pj_config = _make_cfg(cache_root)
        e.system_prompt = "SYS"
        e.trans_prompt = "[Input]"
        e._inject_guideline = False
        e._build_glossary_text = lambda: ""
        captured = {}

        def fake_build(input_src, gptdict="", external_info=""):
            captured["input"] = input_src
            captured["external"] = external_info
            return f"PROMPT({input_src})[RouteName][RouteFiles]"

        e._build_prompt_request = fake_build
        calls = {}

        async def fake_call(messages, *a, **kw):
            calls["messages"] = messages
            return llm_result

        e._call_llm_with_error_report = fake_call
        e._captured = captured
        e._calls = calls
        return e

    async def test_success_writes_shard_with_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e = self._engine(
                tmp, (VALID_ROUTE_JSON, SimpleNamespace(model_name="test-model"))
            )
            ok = await e.analyze_route(
                FAKE_TEXTS, "线A", ["/p/a.json", "/p/c.json"],
                external_info="游戏信息",
            )
            self.assertTrue(ok)
            shards = load_route_shards(_make_cfg(tmp))
            self.assertIn("线A", shards)
            shard = shards["线A"]
            self.assertEqual(shard["路线名"], "线A")
            self.assertEqual(shard["文件列表"], ["/p/a.json", "/p/c.json"])
            self.assertEqual(shard["trans_by"], "test-model")
            self.assertTrue(shard["生成时间"])
            self.assertEqual(shard["剧情概述"], "本路线剧情概述。")
            # 提示词注入：路线名/文件清单/外部信息到达 user 消息
            user_msg = e._calls["messages"][1]["content"]
            self.assertIn("线A", user_msg)
            self.assertIn("a.json", user_msg)
            self.assertIn("游戏信息", e._captured["external"])

    async def test_llm_failure_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e = self._engine(tmp, (None, None))
            ok = await e.analyze_route(FAKE_TEXTS, "线A", ["/p/a.json"])
            self.assertFalse(ok)
            self.assertEqual(load_route_shards(_make_cfg(tmp)), {})

    async def test_blank_route_text_skips_llm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e = self._engine(tmp, (VALID_ROUTE_JSON, None))
            called = []

            async def spy_call(*a, **kw):
                called.append(1)
                return (VALID_ROUTE_JSON, None)

            e._call_llm_with_error_report = spy_call
            ok = await e.analyze_route(FAKE_TEXTS, "线A", ["/p/ghost.json"])
            self.assertFalse(ok)
            self.assertEqual(called, [])

    async def test_invalid_json_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e = self._engine(tmp, ("不是JSON", None))
            ok = await e.analyze_route(FAKE_TEXTS, "线A", ["/p/a.json"])
            self.assertFalse(ok)
            self.assertEqual(load_route_shards(_make_cfg(tmp)), {})


class BatchTranslateTests(unittest.IsolatedAsyncioTestCase):
    """batch_translate 全路线编排：路线图依赖、跳过与失败汇总。"""

    def _engine(
        self, cache_root: str, analyze_results: Optional[Dict[str, bool]] = None
    ) -> ForRouteAnalysis:
        e = ForRouteAnalysis.__new__(ForRouteAnalysis)
        e.pj_config = _make_cfg(cache_root)
        e._system = "SYS"
        calls = []

        async def fake_analyze(data, route, files, external_info=""):
            calls.append(route)
            if analyze_results is None:
                return True
            return analyze_results.get(route, True)

        e.analyze_route = fake_analyze
        e._calls = calls
        return e

    async def test_missing_route_map_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e = self._engine(tmp)
            with patch(
                "GalTransl.Backend.ForRouteAnalysis.load_plot_route_map",
                return_value=None,
            ):
                ok = await e.batch_translate(FAKE_TEXTS)
            self.assertFalse(ok)
            self.assertEqual(e._calls, [])

    async def test_generates_missing_and_skips_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_shard(tmp, "线B", ["/p/b.json"])
            e = self._engine(tmp)
            with patch(
                "GalTransl.Backend.ForRouteAnalysis.load_plot_route_map",
                return_value=FAKE_ROUTE_MAP,
            ), patch(
                "GalTransl.Backend.ForRouteAnalysis.shard_up_to_date",
                side_effect=lambda cfg, r, fs: r == "线B",
            ):
                ok = await e.batch_translate(FAKE_TEXTS)
            self.assertTrue(ok)
            self.assertEqual(e._calls, ["线A"])

    async def test_force_regen_regenerates_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e = self._engine(tmp)
            with patch(
                "GalTransl.Backend.ForRouteAnalysis.load_plot_route_map",
                return_value=FAKE_ROUTE_MAP,
            ), patch(
                "GalTransl.Backend.ForRouteAnalysis.shard_up_to_date",
                return_value=True,
            ):
                ok = await e.batch_translate(FAKE_TEXTS, force_regen=True)
            self.assertTrue(ok)
            self.assertEqual(len(e._calls), 2)

    async def test_failed_route_fails_whole(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e = self._engine(tmp, analyze_results={"线A": False})
            with patch(
                "GalTransl.Backend.ForRouteAnalysis.load_plot_route_map",
                return_value=FAKE_ROUTE_MAP,
            ), patch(
                "GalTransl.Backend.ForRouteAnalysis.shard_up_to_date",
                return_value=False,
            ):
                ok = await e.batch_translate(FAKE_TEXTS)
            self.assertFalse(ok)
            self.assertEqual(len(e._calls), 2)

    async def test_empty_compressed_data_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e = self._engine(tmp)
            self.assertFalse(await e.batch_translate({}))
            self.assertFalse(await e.batch_translate({"a.json": "  "}))


class BatchTranslateCancellationTests(unittest.IsolatedAsyncioTestCase):
    """取消语义：单路线 JobCancelledError 必须上抛且兄弟任务被取消。"""

    async def test_cancellation_propagates_and_cancels_siblings(self) -> None:
        from GalTransl.Service import JobCancelledError

        with tempfile.TemporaryDirectory() as tmp:
            e = ForRouteAnalysis.__new__(ForRouteAnalysis)
            e.pj_config = _make_cfg(tmp)
            started: List[str] = []

            async def fake_analyze(data, route, files, external_info=""):
                started.append(route)
                if route == "线A":
                    await asyncio.sleep(0.01)
                    raise JobCancelledError()
                await asyncio.sleep(5)
                return True

            e.analyze_route = fake_analyze
            with patch(
                "GalTransl.Backend.ForRouteAnalysis.load_plot_route_map",
                return_value=FAKE_ROUTE_MAP,
            ), patch(
                "GalTransl.Backend.ForRouteAnalysis.shard_up_to_date",
                return_value=False,
            ):
                with self.assertRaises(JobCancelledError):
                    await e.batch_translate(FAKE_TEXTS, parallelism=2)
            self.assertEqual(set(started), {"线A", "线B"})


if __name__ == "__main__":
    unittest.main()
