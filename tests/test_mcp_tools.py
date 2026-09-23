"""mcp_tools 工具层单测（0.5.1 MCP 外部 agent 接入）。

覆盖：工具定义与分发表一致性、JSON Schema 合法性、参数校验、11 个工具的行为。
"""
import json
import os
import tempfile
import unittest

from GalTransl.mcp_tools import (
    DEFAULT_PAGE_SIZE,
    MCP_TOOL_DEFS,
    READ_ONLY_ANNOTATIONS,
    SERVER_INSTRUCTIONS,
    _TOOL_HANDLERS,
    call_mcp_tool,
    tool_annotations,
)


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

    def test_tool_count_is_eleven(self) -> None:
        self.assertEqual(len(MCP_TOOL_DEFS), 11)

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

    def test_every_tool_is_read_only_kind(self) -> None:
        for item in MCP_TOOL_DEFS:
            self.assertEqual(item["kind"], "read")

    def test_unknown_tool_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            call_mcp_tool("galtransl_not_exist")


class ConstraintDeliveryTests(unittest.TestCase):
    """下发约束（instructions / annotations）：文案与协议字段口径。"""

    def test_instructions_mentions_all_six_constraints(self) -> None:
        for keyword in ("只读", "H", "project_dir", "密钥", "max_results", "index"):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, SERVER_INSTRUCTIONS)

    def test_instructions_states_no_h_filter(self) -> None:
        # 锁定「声明与实现一致」：H 门禁落地（0.6.1）前不得写成已过滤
        self.assertIn("无 H 门禁过滤", SERVER_INSTRUCTIONS)

    def test_read_only_annotations_keys_match_sdk(self) -> None:
        # 键名必须与 mcp SDK ToolAnnotations 字段一致（SDK 升级改名时立即暴露）
        self.assertEqual(
            set(READ_ONLY_ANNOTATIONS),
            {"read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint"},
        )
        for value in READ_ONLY_ANNOTATIONS.values():
            self.assertIsInstance(value, bool)

    def test_tool_annotations_derives_from_kind(self) -> None:
        for item in MCP_TOOL_DEFS:
            with self.subTest(tool=item["name"]):
                self.assertEqual(tool_annotations(item), READ_ONLY_ANNOTATIONS)
        # 非 read 类（0.6.0 作业域）与缺 kind 的桩一律返回空 dict
        self.assertEqual(tool_annotations({"name": "x", "kind": "job"}), {})
        self.assertEqual(tool_annotations({"name": "x"}), {})

    def test_tool_annotations_returns_independent_copy(self) -> None:
        # 返回副本：调用方改写不得污染模块级常量
        derived = tool_annotations(MCP_TOOL_DEFS[0])
        derived["read_only_hint"] = False
        self.assertTrue(READ_ONLY_ANNOTATIONS["read_only_hint"])

    def test_instructions_length_is_bounded(self) -> None:
        # 防膨胀：instructions 过长有被客户端截断的风险（当前实测 1175 字节）
        self.assertLessEqual(len(SERVER_INSTRUCTIONS.encode("utf-8")), 2000)

    def test_instructions_tool_count_matches_defs(self) -> None:
        # 文案里的工具数与 MCP_TOOL_DEFS 同源，数量变更（如 0.6.0 加作业域）时立即暴露
        self.assertIn(f"{len(MCP_TOOL_DEFS)} 个工具", SERVER_INSTRUCTIONS)


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

    def test_get_project_metadata_all_lists_available_files(self) -> None:
        result = self._call("galtransl_get_project_metadata", kind="all")
        self.assertEqual(result["filemeta_files"], ["a.meta.json"])
        self.assertEqual(result["batchmeta_files"], [])

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
