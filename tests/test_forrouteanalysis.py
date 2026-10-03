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
    merge_sub_analyses,
    pack_route_chunks,
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

    def test_fullwidth_paths_matched_via_nfkc(self) -> None:
        # 路线图键为半角、真实压缩文本路径为全角时经 NFKC 兜底命中
        texts = {
            "/p/０１＿共通＿０１＿０１.json": "文本1",
            "/p/フリー拠点イベント01＿01.json": "文本2",
        }
        route_map = {
            "文件归属": {
                "01_共通_01_01.json": "主线",
                "フリー拠点イベント01_01.json": "自由拠点活动",
            }
        }
        routes, unmatched = derive_route_file_map(route_map, texts)
        self.assertEqual(unmatched, [])
        self.assertEqual(routes["主线"], ["/p/０１＿共通＿０１＿０１.json"])
        self.assertEqual(routes["自由拠点活动"], ["/p/フリー拠点イベント01＿01.json"])

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

        async def fake_analyze(data, route, files, external_info="", max_input_chars=None):
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

            async def fake_analyze(data, route, files, external_info="", max_input_chars=None):
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


class PackRouteChunksTests(unittest.TestCase):
    """pack_route_chunks 贪心装填：上限内不分块、超限切组、单文件超限独占。"""

    def test_within_limit_single_chunk(self) -> None:
        chunks, oversized = pack_route_chunks(
            ["/p/a.json"], {"/p/a.json": "短文本"}, 950000
        )
        self.assertEqual(chunks, [["/p/a.json"]])
        self.assertEqual(oversized, [])

    def test_no_limit_means_single_chunk(self) -> None:
        texts = {"/p/a.json": "A" * 300, "/p/b.json": "B" * 300}
        for limit in (None, 0, -5):
            with self.subTest(limit=limit):
                chunks, oversized = pack_route_chunks(list(texts), texts, limit)
                self.assertEqual(chunks, [list(texts)])
                self.assertEqual(oversized, [])

    def test_greedy_split_respects_limit(self) -> None:
        texts = {
            "/p/a.json": "A" * 100,
            "/p/b.json": "B" * 100,
            "/p/c.json": "C" * 100,
        }
        # 单文件段长 15+100=115；两文件 115+2+115=232<=250；三文件 349>250
        chunks, oversized = pack_route_chunks(list(texts), texts, 250)
        self.assertEqual(chunks, [["/p/a.json", "/p/b.json"], ["/p/c.json"]])
        self.assertEqual(oversized, [])

    def test_oversized_single_file_alone_not_truncated(self) -> None:
        texts = {"/p/a.json": "A" * 300, "/p/b.json": "B" * 50}
        chunks, oversized = pack_route_chunks(list(texts), texts, 100)
        self.assertEqual(chunks, [["/p/a.json"], ["/p/b.json"]])
        self.assertEqual(oversized, ["/p/a.json"])

    def test_blank_files_skipped(self) -> None:
        texts = {"/p/a.json": "  ", "/p/b.json": ""}
        chunks, oversized = pack_route_chunks(list(texts), texts, None)
        self.assertEqual(chunks, [])
        self.assertEqual(oversized, [])


def _sub_analyses(
    plot: str,
    chars: list,
    world: str = "",
    style: str = "",
    name: str = "测试游戏",
    tags: Optional[list] = None,
) -> dict:
    """构造规整后的子分析 dict（6 键齐全，模拟 normalize 产物）。"""
    return {
        "游戏名称": name,
        "剧情概述": plot,
        "角色列表": chars,
        "世界观设定": world,
        "行文风格": style,
        "题材标签": tags or [],
    }


class MergeSubAnalysesTests(unittest.TestCase):
    """merge_sub_analyses 机械合并：概述拼接、角色去重互补、全局字段首见。"""

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(merge_sub_analyses([]), {})

    def test_single_sub_returns_copy(self) -> None:
        sub = _sub_analyses("概述。", [{"名称": "爱丽丝", "形象": "女主"}])
        merged = merge_sub_analyses([sub])
        self.assertEqual(merged, sub)
        self.assertIsNot(merged, sub)

    def test_plot_concatenated_in_order_skipping_blank(self) -> None:
        subs = [
            _sub_analyses("概述一。", [{"名称": "爱丽丝", "形象": "a"}]),
            _sub_analyses("", [{"名称": "爱丽丝", "形象": "a"}]),
            _sub_analyses("概述三。", [{"名称": "爱丽丝", "形象": "a"}]),
        ]
        merged = merge_sub_analyses(subs)
        self.assertEqual(merged["剧情概述"], "概述一。\n概述三。")

    def test_characters_merged_by_name_with_field_fill(self) -> None:
        subs = [
            _sub_analyses(
                "p1",
                [{"名称": "爱丽丝", "形象": "女主", "语气": "", "说话风格": "", "关系": ""}],
            ),
            _sub_analyses(
                "p2",
                [
                    {"名称": "爱丽丝", "形象": "", "语气": "温柔", "说话风格": "爱用敬语", "关系": ""},
                    {"名称": "鲍勃", "形象": "男主", "语气": "沉稳", "说话风格": "", "关系": "爱丽丝的青梅竹马"},
                ],
            ),
        ]
        merged = merge_sub_analyses(subs)
        self.assertEqual(
            merged["角色列表"],
            [
                {"名称": "爱丽丝", "形象": "女主", "语气": "温柔", "说话风格": "爱用敬语", "关系": ""},
                {"名称": "鲍勃", "形象": "男主", "语气": "沉稳", "说话风格": "", "关系": "爱丽丝的青梅竹马"},
            ],
        )

    def test_global_fields_take_first_nonempty(self) -> None:
        subs = [
            _sub_analyses("p1", [], world="", style="轻松", name=""),
            _sub_analyses("p2", [], world="奇幻世界", style="悬疑", name="游戏A"),
        ]
        merged = merge_sub_analyses(subs)
        self.assertEqual(merged["游戏名称"], "游戏A")
        self.assertEqual(merged["世界观设定"], "奇幻世界")
        self.assertEqual(merged["行文风格"], "轻松")

    def test_tags_union_dedup_preserving_order(self) -> None:
        subs = [
            _sub_analyses("p1", [], tags=["校园", "恋爱"]),
            _sub_analyses("p2", [], tags=["恋爱", "奇幻", ""]),
        ]
        self.assertEqual(merge_sub_analyses(subs)["题材标签"], ["校园", "恋爱", "奇幻"])

    def test_non_dict_entries_ignored(self) -> None:
        self.assertEqual(merge_sub_analyses([None, "x"]), {})


class AnalyzeRouteChunkingTests(unittest.IsolatedAsyncioTestCase):
    """analyze_route 尺寸护栏：超限分块依次分析、子分析合并、失败语义。"""

    def _engine(self, cache_root: str, responses: list) -> ForRouteAnalysis:
        e = ForRouteAnalysis.__new__(ForRouteAnalysis)
        e.pj_config = _make_cfg(cache_root)
        e.system_prompt = "SYS"
        e.trans_prompt = "[Input]"
        e._inject_guideline = False
        e._build_glossary_text = lambda: ""
        e._build_prompt_request = (
            lambda input_src, gptdict="", external_info="": (
                f"PROMPT({input_src})[RouteName][RouteFiles]"
            )
        )
        prompts: List[str] = []

        async def fake_call(messages, *a, **kw):
            prompts.append(messages[1]["content"])
            return responses[len(prompts) - 1]

        e._call_llm_with_error_report = fake_call
        e._prompts = prompts
        return e

    def setUp(self) -> None:
        self.rsp1 = json.dumps(
            {
                "游戏名称": "测试游戏",
                "剧情概述": "第一块概述。",
                "角色列表": [{"名称": "爱丽丝", "形象": "女主", "语气": "", "说话风格": "", "关系": ""}],
                "世界观设定": "",
                "行文风格": "轻松",
                "题材标签": ["校园"],
            },
            ensure_ascii=False,
        )
        self.rsp2 = json.dumps(
            {
                "游戏名称": "",
                "剧情概述": "第二块概述。",
                "角色列表": [
                    {"名称": "爱丽丝", "形象": "", "语气": "温柔", "说话风格": "爱用敬语", "关系": ""},
                    {"名称": "鲍勃", "形象": "男主", "语气": "沉稳", "说话风格": "", "关系": ""},
                ],
                "世界观设定": "奇幻世界",
                "行文风格": "",
                "题材标签": ["校园", "恋爱"],
            },
            ensure_ascii=False,
        )
        self.big_texts = {
            "/p/a.json": "A" * 120,
            "/p/b.json": "B" * 120,
        }

    async def test_oversized_input_chunks_and_merges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e = self._engine(
                tmp,
                [
                    (self.rsp1, SimpleNamespace(model_name="m1")),
                    (self.rsp2, SimpleNamespace(model_name="m2")),
                ],
            )
            ok = await e.analyze_route(
                self.big_texts, "线A", ["/p/a.json", "/p/b.json"],
                max_input_chars=150,
            )
            self.assertTrue(ok)
            self.assertEqual(len(e._prompts), 2)
            # 块级文件清单标注到达提示词
            self.assertIn("第 1/2 块", e._prompts[0])
            self.assertIn("第 2/2 块", e._prompts[1])
            shard = load_route_shards(_make_cfg(tmp))["线A"]
            self.assertEqual(shard["文件列表"], ["/p/a.json", "/p/b.json"])
            self.assertEqual(shard["剧情概述"], "第一块概述。\n第二块概述。")
            self.assertEqual(shard["世界观设定"], "奇幻世界")
            self.assertEqual(shard["行文风格"], "轻松")
            self.assertEqual(shard["题材标签"], ["校园", "恋爱"])
            self.assertEqual(
                shard["角色列表"],
                [
                    {"名称": "爱丽丝", "形象": "女主", "语气": "温柔", "说话风格": "爱用敬语", "关系": ""},
                    {"名称": "鲍勃", "形象": "男主", "语气": "沉稳", "说话风格": "", "关系": ""},
                ],
            )
            # trans_by 取最后一块的 token
            self.assertEqual(shard["trans_by"], "m2")

    async def test_chunk_llm_failure_fails_whole_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e = self._engine(tmp, [(None, None)])
            ok = await e.analyze_route(
                self.big_texts, "线A", ["/p/a.json", "/p/b.json"],
                max_input_chars=150,
            )
            self.assertFalse(ok)
            self.assertEqual(load_route_shards(_make_cfg(tmp)), {})

    async def test_chunk_invalid_json_fails_whole_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e = self._engine(
                tmp,
                [(self.rsp1, SimpleNamespace(model_name="m1")), ("不是JSON", None)],
            )
            ok = await e.analyze_route(
                self.big_texts, "线A", ["/p/a.json", "/p/b.json"],
                max_input_chars=150,
            )
            self.assertFalse(ok)
            self.assertEqual(load_route_shards(_make_cfg(tmp)), {})

    async def test_within_limit_keeps_single_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e = self._engine(
                tmp, [(self.rsp1, SimpleNamespace(model_name="m1"))]
            )
            ok = await e.analyze_route(
                self.big_texts, "线A", ["/p/a.json", "/p/b.json"],
                max_input_chars=950000,
            )
            self.assertTrue(ok)
            self.assertEqual(len(e._prompts), 1)
            # 单块不附带块序标注，文件清单为整条路线
            self.assertNotIn("第 1/", e._prompts[0])
            self.assertIn("a.json、b.json", e._prompts[0])


class CoerceMaxInputCharsTests(unittest.TestCase):
    """_coerce_max_input_chars 配置规整：非法值/布尔回退默认，<=0 不限制。"""

    def test_valid_values_pass_through(self) -> None:
        from GalTransl.Backend.ForRouteAnalysis import _coerce_max_input_chars

        self.assertEqual(_coerce_max_input_chars(500), 500)
        self.assertEqual(_coerce_max_input_chars("950000"), 950000)
        self.assertEqual(_coerce_max_input_chars(0), 0)
        self.assertEqual(_coerce_max_input_chars(-1), -1)

    def test_invalid_values_fall_back_to_default(self) -> None:
        from GalTransl.Backend.ForRouteAnalysis import (
            _ROUTE_INPUT_CHARS_DEFAULT,
            _coerce_max_input_chars,
        )

        for bad in (None, "abc", True, False, [100], {"n": 1}):
            with self.subTest(bad=bad):
                self.assertEqual(_coerce_max_input_chars(bad), _ROUTE_INPUT_CHARS_DEFAULT)


class AnalyzeRouteOversizedWarningTests(unittest.IsolatedAsyncioTestCase):
    """单文件自身超限：独占成块发送前必须告警（不截断）。"""

    async def test_oversized_file_warns_before_send(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e = ForRouteAnalysis.__new__(ForRouteAnalysis)
            e.pj_config = _make_cfg(tmp)
            e.system_prompt = "SYS"
            e.trans_prompt = "[Input]"
            e._inject_guideline = False
            e._build_glossary_text = lambda: ""
            e._build_prompt_request = (
                lambda input_src, gptdict="", external_info="": (
                    f"PROMPT({input_src})[RouteName][RouteFiles]"
                )
            )
            sent: List[int] = []

            async def fake_call(messages, *a, **kw):
                sent.append(1)
                return (VALID_ROUTE_JSON, SimpleNamespace(model_name="m"))

            e._call_llm_with_error_report = fake_call
            texts = {"/p/huge.json": "H" * 300}
            with patch(
                "GalTransl.Backend.ForRouteAnalysis.LOGGER"
            ) as mock_logger:
                ok = await e.analyze_route(
                    texts, "线A", ["/p/huge.json"], max_input_chars=100
                )
            self.assertTrue(ok)
            self.assertEqual(len(sent), 1)
            warnings = [
                str(c.args[0])
                for c in mock_logger.warning.call_args_list
            ]
            self.assertTrue(
                any("huge.json" in w and "超过单次上限" in w for w in warnings),
                f"未找到单文件超限告警：{warnings}",
            )


class BatchTranslateRouteMapTests(unittest.IsolatedAsyncioTestCase):
    """batch_translate 的 route_file_map 直传口径：不依赖 PlotRouteMap.json。"""

    def _engine(self, cache_root: str) -> ForRouteAnalysis:
        e = ForRouteAnalysis.__new__(ForRouteAnalysis)
        e.pj_config = _make_cfg(cache_root)
        calls: List[tuple] = []

        async def fake_analyze(data, route, files, external_info="", max_input_chars=None):
            calls.append((route, list(files)))
            return True

        e.analyze_route = fake_analyze
        e._calls = calls
        return e

    async def test_route_file_map_works_without_plot_route_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e = self._engine(tmp)
            # tmp 下无 PlotRouteMap.json：直传映射必须照常编排（合成分片场景）
            ok = await e.batch_translate(
                FAKE_TEXTS,
                route_file_map={"线A": ["/p/a.json", "/p/c.json"], "线B": ["/p/b.json"]},
            )
            self.assertTrue(ok)
            self.assertEqual(
                dict(e._calls),
                {"线A": ["/p/a.json", "/p/c.json"], "线B": ["/p/b.json"]},
            )

    async def test_route_file_map_filters_empty_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e = self._engine(tmp)
            ok = await e.batch_translate(
                FAKE_TEXTS,
                route_file_map={"线A": ["/p/a.json"], "空路线": []},
            )
            self.assertTrue(ok)
            self.assertEqual([r for r, _ in e._calls], ["线A"])

    async def test_route_file_map_drops_blank_text_files(self) -> None:
        # 直传映射与内部 derive 口径一致：无有效压缩文本的文件被剔除告警
        with tempfile.TemporaryDirectory() as tmp:
            e = self._engine(tmp)
            ok = await e.batch_translate(
                {**FAKE_TEXTS, "/p/empty.json": "  "},
                route_file_map={
                    "线A": ["/p/a.json", "/p/empty.json"],
                    "全空路线": ["/p/empty.json"],
                },
            )
            self.assertTrue(ok)
            self.assertEqual(dict(e._calls), {"线A": ["/p/a.json"]})


if __name__ == "__main__":
    unittest.main()
