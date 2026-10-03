"""mcp_tools 工具层单测（0.5.1 MCP 外部 agent 接入）。

覆盖：工具定义与分发表一致性、JSON Schema 合法性、参数校验、19 个工具的行为。
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from GalTransl.mcp_tools import (
    DEFAULT_PAGE_SIZE,
    MCP_TOOL_DEFS,
    READ_ONLY_ANNOTATIONS,
    SERVER_INSTRUCTIONS,
    _TOOL_HANDLERS,
    call_mcp_tool,
    tool_annotations,
)


# 门禁设置随全局 app_settings.json 变化，模块级固定为默认值以隔离开发机真实设置
_gate_settings_patcher = None


def setUpModule() -> None:
    global _gate_settings_patcher
    _gate_settings_patcher = mock.patch(
        "GalTransl.mcp_tools.load_app_settings",
        return_value={"mcpHGateEnabled": True, "mcpDisabledTools": []},
    )
    _gate_settings_patcher.start()


def tearDownModule() -> None:
    _gate_settings_patcher.stop()


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _write_json(path: str, payload: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


class ToolDefinitionTests(unittest.TestCase):
    """工具定义（暴露给 MCP 客户端的 schema）自身的完整性。"""

    def test_defs_and_handlers_match(self) -> None:
        def_names = {item["name"] for item in MCP_TOOL_DEFS}
        self.assertEqual(def_names, set(_TOOL_HANDLERS))

    def test_tool_count_is_nineteen(self) -> None:
        # 0.5.1：11 个只读检索；0.6.0：+4 个写工具；0.6.x：+2 个作业域查询（状态/模型探测）
        # 术语表批次：+1 只读（read_glossary）+1 写（write_glossary）
        self.assertEqual(len(MCP_TOOL_DEFS), 19)
        self.assertEqual(sum(1 for d in MCP_TOOL_DEFS if d["kind"] == "read"), 14)
        self.assertEqual(sum(1 for d in MCP_TOOL_DEFS if d["kind"] == "write"), 5)

    def test_all_names_are_prefixed_and_unique(self) -> None:
        names = [item["name"] for item in MCP_TOOL_DEFS]
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            self.assertTrue(name.startswith("galtransl_"), name)

    def test_every_def_has_valid_schema(self) -> None:
        for item in MCP_TOOL_DEFS:
            with self.subTest(tool=item["name"]):
                self.assertTrue(item["description"].strip())
                schema = item["input_schema"]
                self.assertEqual(schema["type"], "object")
                self.assertIsInstance(schema["properties"], dict)
                # required 必须是 properties 的子集，否则客户端校验会失败
                for key in schema["required"]:
                    self.assertIn(key, schema["properties"])

    def test_every_tool_kind_is_read_or_write(self) -> None:
        for item in MCP_TOOL_DEFS:
            with self.subTest(tool=item["name"]):
                self.assertIn(item["kind"], ("read", "write"))

    def test_write_tools_are_never_annotated_read_only(self) -> None:
        # 安全性质：写工具若被下发 read_only_hint=True，客户端可能跳过用户确认直接写入
        for item in MCP_TOOL_DEFS:
            if item["kind"] == "write":
                with self.subTest(tool=item["name"]):
                    self.assertEqual(tool_annotations(item), {})

    def test_unknown_tool_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            call_mcp_tool("galtransl_not_exist")


class ConstraintDeliveryTests(unittest.TestCase):
    """下发约束（instructions / annotations）：文案与协议字段口径。"""

    def test_instructions_mentions_all_six_constraints(self) -> None:
        for keyword in ("只读", "H", "project_dir", "密钥", "max_results", "index"):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, SERVER_INSTRUCTIONS)

    def test_instructions_declares_h_gate_not_absence_of_one(self) -> None:
        # 反向锁定：H 门禁已落地（0.6.0），文案不得再声称「无 H 门禁过滤」
        self.assertIn("硬门禁", SERVER_INSTRUCTIONS)
        self.assertNotIn("无 H 门禁过滤", SERVER_INSTRUCTIONS)

    def test_instructions_does_not_claim_all_tools_are_read_only(self) -> None:
        # 文案必须与工具面一致：存在写工具时不得声称「全部工具只读」，
        # 否则 agent 会被诱导拒绝使用写工具
        self.assertNotIn("全部工具只读", SERVER_INSTRUCTIONS)
        self.assertIn("14 个只读检索 + 5 个写操作", SERVER_INSTRUCTIONS)

    def test_read_only_annotations_keys_match_sdk(self) -> None:
        # 键名必须与 mcp SDK ToolAnnotations 字段一致（SDK 升级改名时立即暴露）
        from GalTransl.mcp_tools import PROBE_ANNOTATIONS

        self.assertEqual(
            set(READ_ONLY_ANNOTATIONS),
            {"read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint"},
        )
        # 两个常量必须同步演进，防止某一方在 SDK 改名后漂移出白名单
        self.assertEqual(set(PROBE_ANNOTATIONS), set(READ_ONLY_ANNOTATIONS))
        for value in READ_ONLY_ANNOTATIONS.values():
            self.assertIsInstance(value, bool)
        for value in PROBE_ANNOTATIONS.values():
            self.assertIsInstance(value, bool)

    def test_tool_annotations_derives_from_kind(self) -> None:
        for item in MCP_TOOL_DEFS:
            if item["kind"] != "read":
                continue
            with self.subTest(tool=item["name"]):
                if item.get("annotations"):
                    # 定义级覆盖（check_model 的探测注解）优先于 kind 派生
                    self.assertNotEqual(item["annotations"], READ_ONLY_ANNOTATIONS)
                    continue
                self.assertEqual(tool_annotations(item), READ_ONLY_ANNOTATIONS)
        # write 与缺 kind 的桩一律返回空 dict
        self.assertEqual(tool_annotations({"name": "x", "kind": "write"}), {})
        self.assertEqual(tool_annotations({"name": "x"}), {})

    def test_tool_annotations_returns_independent_copy(self) -> None:
        # 返回副本：调用方改写不得污染模块级常量
        derived = tool_annotations(MCP_TOOL_DEFS[0])
        derived["read_only_hint"] = False
        self.assertTrue(READ_ONLY_ANNOTATIONS["read_only_hint"])

    def test_check_model_declares_open_world(self) -> None:
        # check_model 会向外部模型端点发真实探测请求：保持只读声明，但必须声明 open_world，
        # 否则客户端可能据 open_world_hint=False 免确认放行一次外部调用
        from GalTransl.mcp_tools import PROBE_ANNOTATIONS

        check_def = next(d for d in MCP_TOOL_DEFS if d["name"] == "galtransl_check_model")
        self.assertEqual(tool_annotations(check_def), PROBE_ANNOTATIONS)
        self.assertTrue(PROBE_ANNOTATIONS["read_only_hint"])
        self.assertTrue(PROBE_ANNOTATIONS["open_world_hint"])
        # 其余只读工具仍是不访问开放世界的默认注解
        for item in MCP_TOOL_DEFS:
            if item["kind"] == "read" and item["name"] != "galtransl_check_model":
                with self.subTest(tool=item["name"]):
                    self.assertFalse(tool_annotations(item)["open_world_hint"])

    def test_def_kind_defaults_to_read_and_accepts_write(self) -> None:
        # kind 默认 read 保兼容；显式传 write 后必须不再被标注为只读，
        # 否则客户端可能跳过用户确认直接执行写入。
        from GalTransl.mcp_tools import _def

        read_def = _def("t", "d", {}, [])
        self.assertEqual(read_def["kind"], "read")
        self.assertEqual(tool_annotations(read_def), READ_ONLY_ANNOTATIONS)
        write_def = _def("t", "d", {}, [], kind="write")
        self.assertEqual(write_def["kind"], "write")
        self.assertEqual(tool_annotations(write_def), {})
        # 定义级 annotations 覆盖 kind 派生（check_model 的探测注解依赖此机制）
        probe_def = _def("t", "d", {}, [], annotations={"read_only_hint": False})
        self.assertEqual(tool_annotations(probe_def), {"read_only_hint": False})

    def test_instructions_length_is_bounded(self) -> None:
        # 防膨胀：instructions 过长有被客户端截断的风险（当前实测 1847 字节）
        self.assertLessEqual(len(SERVER_INSTRUCTIONS.encode("utf-8")), 2000)

    def test_instructions_tool_count_matches_defs(self) -> None:
        # 文案里的工具数与 MCP_TOOL_DEFS 同源，数量变更（如 0.6.0 加作业域）时立即暴露
        self.assertIn(f"{len(MCP_TOOL_DEFS)} 个工具", SERVER_INSTRUCTIONS)

    def test_server_description_does_not_claim_read_only(self) -> None:
        # serverInfo.description 也是模型可见元数据。0.6.0 加写工具后一度仍写「（只读）」，
        # 会让客户端与模型低估服务能力——与 instructions 同样的口径，同样要锁。
        from run_mcp_server import SERVER_DESCRIPTION

        self.assertNotIn("（只读）", SERVER_DESCRIPTION)
        self.assertNotIn("全部只读", SERVER_DESCRIPTION)
        self.assertIn(f"{len(MCP_TOOL_DEFS)} 个工具", SERVER_DESCRIPTION)

    def test_server_description_names_both_capabilities(self) -> None:
        from run_mcp_server import SERVER_DESCRIPTION

        self.assertIn("只读", SERVER_DESCRIPTION)
        self.assertIn("写入", SERVER_DESCRIPTION)


class ArgumentValidationTests(unittest.TestCase):
    def test_missing_project_dir_raises(self) -> None:
        with self.assertRaises(ValueError):
            call_mcp_tool("galtransl_search_cache", {"query": "x"})

    def test_nonexistent_project_dir_raises(self) -> None:
        with self.assertRaises(ValueError):
            call_mcp_tool(
                "galtransl_search_cache",
                {"project_dir": os.path.join(tempfile.gettempdir(), "no_such_project_dir_xyz"), "query": "x"},
            )

    def test_empty_query_is_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as project:
            result = call_mcp_tool("galtransl_search_cache", {"project_dir": project, "query": ""})
            self.assertEqual(result["total"], 0)


class ProjectFixture(unittest.TestCase):
    """构造一个最小可用的项目目录，供各工具用例复用。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = self._tmp.name
        _write_text(
            os.path.join(self.project, "config.yaml"),
            "common:\n"
            "  language: zh-cn\n"
            "  workersPerProject: '2'\n"
            "  gpt.numPerRequestTranslate: 16\n"
            "  gpt.translation_guideline: 自制提示词.md\n"
            "internals:\n"
            "  pipeline:\n"
            "    enableGenDic: true\n"
            "dictionary:\n"
            "  preDict:\n"
            "  - (project_dir)项目字典_译前.txt\n",
        )
        _write_text(os.path.join(self.project, "项目字典_译前.txt"), "こんにちは|你好\n")
        _write_text(
            os.path.join(self.project, "name替换表.csv"),
            "SRC_Name,DST_Name\n創,创\n凛音,凛音\n",
        )
        _write_text(
            os.path.join(self.project, "GalTransl.log"),
            "[10:00:00] 开始翻译\n"
            "[10:00:01] 检测到残留日文\n"
            "[10:00:02] 完成\n",
        )
        _write_json(
            os.path.join(self.project, "transl_cache", "test.json"),
            [{"index": i, "pre_src": f"原文{i}", "pre_dst": f"译文{i}"} for i in range(5)],
        )
        _write_json(
            os.path.join(self.project, "transl_cache", "pass0_cache", "GlobalPrompt.json"),
            {"故事背景": "校园"},
        )
        _write_json(
            os.path.join(self.project, "transl_cache", "pass1_cache", "a.meta.json"),
            {"角色": ["创"]},
        )
        _write_json(
            os.path.join(self.project, "gt_input", "a.txt.json"),
            [{"message": f"脚本行{i}"} for i in range(3)],
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _call(self, tool_name: str, **kwargs: object) -> dict:
        kwargs.setdefault("project_dir", self.project)
        return call_mcp_tool(tool_name, kwargs)


class SearchToolTests(ProjectFixture):
    def test_search_cache(self) -> None:
        result = self._call("galtransl_search_cache", query="原文3")
        self.assertEqual(result["scope"], "cache")
        self.assertEqual(result["total"], 1)

    def test_search_scripts_without_cache(self) -> None:
        result = self._call("galtransl_search_scripts", query="脚本行1")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["filename"], "a.txt.json")

    def test_search_dict_uses_detected_config_name(self) -> None:
        result = self._call("galtransl_search_dict", query="こんにちは")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["dst"], "你好")

    def test_lookup_name_found_and_missing(self) -> None:
        result = self._call("galtransl_lookup_name", name=["創", "不存在的名字"])
        self.assertEqual(result["name_dict_entries"], 2)
        self.assertEqual(result["results"][0]["target"], "创")
        self.assertTrue(result["results"][0]["found"])
        self.assertFalse(result["results"][1]["found"])
        self.assertIsNone(result["results"][1]["target"])

    def test_lookup_name_requires_name(self) -> None:
        with self.assertRaises(ValueError):
            self._call("galtransl_lookup_name", name="")

    def test_search_logs_reports_absolute_line_number(self) -> None:
        result = self._call("galtransl_search_logs", keyword="残留")
        self.assertTrue(result["exists"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["line_no"], 2)

    def test_search_logs_missing_file_is_tolerated(self) -> None:
        os.remove(os.path.join(self.project, "GalTransl.log"))
        result = self._call("galtransl_search_logs", keyword="残留")
        self.assertFalse(result["exists"])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["results"], [])

    def test_search_logs_rejects_bad_source_and_tail(self) -> None:
        with self.assertRaises(ValueError):
            self._call("galtransl_search_logs", keyword="x", source="other")
        with self.assertRaises(ValueError):
            self._call("galtransl_search_logs", keyword="x", tail="not-a-number")
        with self.assertRaises(ValueError):
            self._call("galtransl_search_logs", keyword="x", tail=-1)

    def test_list_problems_returns_catalog_and_rows(self) -> None:
        _write_json(
            os.path.join(self.project, "transl_cache", "b.json"),
            [{"index": 0, "pre_src": "x", "pre_dst": "y", "problem": "残留日文"}],
        )
        result = self._call("galtransl_list_problems")
        self.assertEqual(result["total"], 1)
        self.assertTrue(result["available_problem_types"])

    def test_list_problems_with_type_filter(self) -> None:
        _write_json(
            os.path.join(self.project, "transl_cache", "b.json"),
            [
                {"index": 0, "pre_src": "x", "pre_dst": "y", "problem": "残留日文"},
                {"index": 1, "pre_src": "x", "pre_dst": "y", "problem": "词频过高"},
            ],
        )
        self.assertEqual(self._call("galtransl_list_problems", problem_type="残留")["total"], 1)


class ProjectReadToolTests(ProjectFixture):
    def test_list_projects_finds_child_with_config(self) -> None:
        # 工作区根下另建一个真项目 + 一个无配置目录
        child = os.path.join(self.project, "child_project")
        _write_text(os.path.join(child, "config.yaml"), "targetLanguage: zh-cn\n")
        os.makedirs(os.path.join(self.project, "not_a_project"), exist_ok=True)
        result = call_mcp_tool("galtransl_list_projects", {"workspace_root": self.project})
        names = [item["name"] for item in result["projects"]]
        self.assertIn("child_project", names)
        self.assertNotIn("not_a_project", names)

    def test_list_projects_missing_root_is_tolerated(self) -> None:
        result = call_mcp_tool("galtransl_list_projects", {"workspace_root": os.path.join(self.project, "nope")})
        self.assertFalse(result["exists"])
        self.assertEqual(result["projects"], [])

    def test_get_project_overview(self) -> None:
        result = self._call("galtransl_get_project_overview")
        self.assertTrue(result["config_exists"])
        self.assertEqual(result["config_file_name"], "config.yaml")
        self.assertEqual(result["target_language"], "zh-cn")
        self.assertEqual(result["workers_per_project"], "2")
        self.assertEqual(result["num_per_request_translate"], 16)
        self.assertEqual(result["translation_guideline"], "自制提示词.md")
        self.assertEqual(result["pipeline_internals"], {"enableGenDic": True})
        self.assertTrue(result["has_name_dict"])
        self.assertEqual(result["script_files"], 1)
        self.assertEqual(result["script_file_names"], ["a.txt.json"])
        # 缓存清单含 pass0/pass1 子目录产物，read_translation_file 可据此拿 filename
        self.assertIn("test.json", result["cache_file_names"])
        self.assertIn(os.path.join("pass0_cache", "GlobalPrompt.json").replace("\\", "/"), result["cache_file_names"])
        self.assertTrue(result["pipeline_stages"]["stages"])

    def test_overview_prefers_inc_config_variant(self) -> None:
        # detect_config_file 优先 config.inc.yaml，概览须跟随该口径
        _write_text(
            os.path.join(self.project, "config.inc.yaml"),
            "common:\n  language: ja\n",
        )
        result = self._call("galtransl_get_project_overview")
        self.assertEqual(result["config_file_name"], "config.inc.yaml")
        self.assertEqual(result["target_language"], "ja")

    def test_overview_missing_config_is_reported_not_raised(self) -> None:
        os.remove(os.path.join(self.project, "config.yaml"))
        result = self._call("galtransl_get_project_overview")
        self.assertFalse(result["config_exists"])
        self.assertEqual(result["target_language"], "")

    def test_read_translation_file_pagination(self) -> None:
        result = self._call("galtransl_read_translation_file", filename="test.json", offset=1, limit=2)
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["returned"], 2)
        self.assertTrue(result["has_more"])
        self.assertEqual([item["index"] for item in result["items"]], [1, 2])

    def test_read_translation_file_default_page_size(self) -> None:
        result = self._call("galtransl_read_translation_file", filename="test.json")
        self.assertEqual(result["limit"], DEFAULT_PAGE_SIZE)
        self.assertEqual(result["returned"], 5)

    def test_read_translation_file_rejects_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            self._call("galtransl_read_translation_file", filename="../config.yaml")

    def test_read_translation_file_missing_file_raises(self) -> None:
        with self.assertRaises(ValueError):
            self._call("galtransl_read_translation_file", filename="no_such.json")

    def test_read_source_script(self) -> None:
        result = self._call("galtransl_read_source_script", filename="a.txt.json")
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["items"][0]["message"], "脚本行0")

    def test_get_project_metadata_globalprompt(self) -> None:
        result = self._call("galtransl_get_project_metadata")
        self.assertEqual(result["kind"], "globalprompt")
        self.assertTrue(result["globalprompt"]["exists"])
        self.assertEqual(result["globalprompt"]["entry"], {"故事背景": "校园"})

    def test_get_project_metadata_plotroute(self) -> None:
        # plotroute 读路径与 write_route_map 配对：写之前的「先读现状」靠它
        result = self._call("galtransl_get_project_metadata", kind="plotroute")
        self.assertFalse(result["plotroute"]["exists"])
        _write_json(
            os.path.join(self.project, "transl_cache", "pass0_cache", "PlotRouteMap.json"),
            {"mermaid": "flowchart TD", "文件归属": {"a.json": "共通线"}},
        )
        result = self._call("galtransl_get_project_metadata", kind="plotroute")
        self.assertTrue(result["plotroute"]["exists"])
        self.assertEqual(result["plotroute"]["entry"]["mermaid"], "flowchart TD")

    def test_get_project_metadata_all_lists_available_files(self) -> None:
        result = self._call("galtransl_get_project_metadata", kind="all")
        self.assertEqual(result["filemeta_files"], ["a.meta.json"])
        self.assertEqual(result["batchmeta_files"], [])
        self.assertEqual(result["routeanalysis_files"], [])
        # all 档同时带回 globalprompt 与 plotroute，供 agent 一次拿全
        self.assertIn("globalprompt", result)
        self.assertIn("plotroute", result)

    def test_get_project_metadata_all_lists_routeanalysis_files(self) -> None:
        _write_json(
            os.path.join(self.project, "transl_cache", "pass0_cache", "route_analysis", "共通线.json"),
            {"路线名": "共通线"},
        )
        result = self._call("galtransl_get_project_metadata", kind="all")
        self.assertEqual(result["routeanalysis_files"], ["共通线.json"])

    def test_get_project_metadata_routeanalysis_entry(self) -> None:
        _write_json(
            os.path.join(self.project, "transl_cache", "pass0_cache", "route_analysis", "共通线.json"),
            {"路线名": "共通线", "角色列表": [{"名字": "主人公"}]},
        )
        result = self._call("galtransl_get_project_metadata", kind="routeanalysis", filename="共通线")
        self.assertTrue(result["routeanalysis"]["exists"])
        self.assertEqual(result["routeanalysis"]["entry"]["路线名"], "共通线")

    def test_get_project_metadata_routeanalysis_requires_filename(self) -> None:
        with self.assertRaises(ValueError):
            self._call("galtransl_get_project_metadata", kind="routeanalysis")

    def test_get_project_metadata_routeanalysis_rejects_traversal(self) -> None:
        with self.assertRaises(ValueError):
            self._call("galtransl_get_project_metadata", kind="routeanalysis", filename="../evil")

    def test_get_project_metadata_filemeta_requires_filename(self) -> None:
        with self.assertRaises(ValueError):
            self._call("galtransl_get_project_metadata", kind="filemeta")

    def test_get_project_metadata_filemeta_entry(self) -> None:
        result = self._call("galtransl_get_project_metadata", kind="filemeta", filename="a")
        self.assertTrue(result["filemeta"]["exists"])
        self.assertEqual(result["filemeta"]["entry"], {"角色": ["创"]})

    def test_get_project_metadata_missing_entry_reports_not_exists(self) -> None:
        result = self._call("galtransl_get_project_metadata", kind="filemeta", filename="nothing")
        self.assertFalse(result["filemeta"]["exists"])
        self.assertIsNone(result["filemeta"]["entry"])

    def test_get_project_metadata_invalid_kind_raises(self) -> None:
        with self.assertRaises(ValueError):
            self._call("galtransl_get_project_metadata", kind="whatever")


if __name__ == "__main__":
    unittest.main()
