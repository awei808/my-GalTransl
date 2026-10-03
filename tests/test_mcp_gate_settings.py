"""MCP 门禁设置（设置界面新增）：H 门禁开关与 MCP 工具禁用黑名单。

覆盖：
1. AppSettings 新键的默认值/规整/读写回环（临时目录，不碰真实 app_settings.json）；
2. enforce_h_gate 随 mcpHGateEnabled 开关放行/拦截（含真实项目夹具的端到端写入）；
3. call_mcp_tool 对禁用工具的拦截与未知工具口径；
4. enabled_tool_defs / build_server_instructions 按启用集合联动；
5. /api/mcp-tools 端点与 run_mcp_server 的 tools/list 过滤、禁用错误包装。
"""
import asyncio
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from GalTransl import AppSettings
from GalTransl.mcp_tools import (
    MCP_TOOL_DEFS,
    MCPToolDisabledError,
    SERVER_INSTRUCTIONS,
    _H_DENY_MESSAGE,
    build_server_instructions,
    call_mcp_tool,
    enabled_tool_defs,
    enforce_h_gate,
    route_map_path,
    write_metadata,
    write_route_map,
)
import run_mcp_server


def _settings_patch(**overrides) -> mock._patch:
    """把 mcp_tools 的设置读取替换为指定快照（默认：门禁开 + 全启用）。"""
    payload = {"mcpHGateEnabled": True, "mcpDisabledTools": []}
    payload.update(overrides)
    return mock.patch("GalTransl.mcp_tools.load_app_settings", return_value=payload)


def _make_h_project(root: str) -> None:
    """构造最小可识别项目（带项目 H 词库，词「攀上」），与 test_mcp_write_tools 同口径。"""
    os.makedirs(os.path.join(root, "transl_cache"), exist_ok=True)
    with open(os.path.join(root, "项目禁用词_非h.txt"), "w", encoding="utf-8") as f:
        f.write("// 非 h 禁用词\n")
    with open(os.path.join(root, "项目禁用词_h.txt"), "w", encoding="utf-8") as f:
        f.write("// 项目 H 禁用词\n攀上|测试用\n")
    with open(os.path.join(root, "config.inc.yaml"), "w", encoding="utf-8") as f:
        f.write(
            "common:\n"
            "  gpt:\n"
            "    dict: []\n"
            "dictionary:\n"
            "  defaultDictFolder: ''\n"
            "  gpt:\n"
            "    dict: []\n"
            "  forbiddenDictH:\n"
            "    - (project_dir)项目禁用词_h.txt\n"
            "  forbiddenDictNonH:\n"
            "    - (project_dir)项目禁用词_非h.txt\n"
            "  preDict: []\n"
            "  postDict: []\n"
        )


class AppSettingsGateKeysTests(unittest.TestCase):
    """AppSettings 新键：mcpHGateEnabled / mcpDisabledTools。"""

    def test_defaults_include_gate_keys(self) -> None:
        settings = AppSettings._normalize_settings({})
        self.assertTrue(settings["mcpHGateEnabled"])
        self.assertEqual(settings["mcpDisabledTools"], [])

    def test_disabled_tools_normalization(self) -> None:
        settings = AppSettings._normalize_settings(
            {"mcpDisabledTools": [" a ", "a", "b", 3, None, ""]}
        )
        # 仅保留非空字符串、去空白、去重
        self.assertEqual(settings["mcpDisabledTools"], ["a", "b"])

    def test_disabled_tools_non_list_falls_back_to_empty(self) -> None:
        for bad in ("x", 3, {"a": 1}, None):
            with self.subTest(bad=bad):
                settings = AppSettings._normalize_settings({"mcpDisabledTools": bad})
                self.assertEqual(settings["mcpDisabledTools"], [])

    def test_h_gate_flag_coerced_to_bool(self) -> None:
        self.assertFalse(AppSettings._normalize_settings({"mcpHGateEnabled": 0})["mcpHGateEnabled"])
        self.assertTrue(AppSettings._normalize_settings({"mcpHGateEnabled": "yes"})["mcpHGateEnabled"])

    def test_h_gate_explicit_null_falls_back_to_default(self) -> None:
        # bool(None)=False 会静默关门禁，显式 null 必须回退默认开启
        self.assertTrue(AppSettings._normalize_settings({"mcpHGateEnabled": None})["mcpHGateEnabled"])

    def test_save_load_roundtrip_in_temp_dir(self) -> None:
        tmp = tempfile.mkdtemp(prefix="gt_appset_")
        try:
            target = os.path.join(tmp, "app_settings.json")
            with mock.patch.object(AppSettings, "_SETTINGS_PATH", target):
                saved = AppSettings.save_app_settings(
                    {"mcpHGateEnabled": False, "mcpDisabledTools": ["galtransl_submit_job", "galtransl_submit_job"]}
                )
                self.assertFalse(saved["mcpHGateEnabled"])
                self.assertEqual(saved["mcpDisabledTools"], ["galtransl_submit_job"])
                loaded = AppSettings.load_app_settings()
                self.assertFalse(loaded["mcpHGateEnabled"])
                self.assertEqual(loaded["mcpDisabledTools"], ["galtransl_submit_job"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class HGateToggleTests(unittest.TestCase):
    """mcpHGateEnabled 开关对 enforce_h_gate 两条判据的总闸作用。"""

    def test_gate_off_bypasses_both_dimensions(self) -> None:
        with _settings_patch(mcpHGateEnabled=False), mock.patch(
            "GalTransl.mcp_tools._entry_has_h", return_value=True
        ), mock.patch("GalTransl.mcp_tools._text_has_h", return_value=True):
            enforce_h_gate("X:/fake_project", cache_filename="01.json", texts=["攀上"])

    def test_gate_on_file_dimension_still_blocks(self) -> None:
        with _settings_patch(), mock.patch("GalTransl.mcp_tools._entry_has_h", return_value=True):
            with self.assertRaises(ValueError) as ctx:
                enforce_h_gate("X:/fake_project", cache_filename="01.json")
            self.assertEqual(str(ctx.exception), _H_DENY_MESSAGE)

    def test_gate_on_text_dimension_still_blocks(self) -> None:
        with _settings_patch(), mock.patch("GalTransl.mcp_tools._text_has_h", return_value=True):
            with self.assertRaises(ValueError):
                enforce_h_gate("X:/fake_project", texts=["攀上"])


class GateOffWriteIntegrationTests(unittest.TestCase):
    """真实项目夹具下：门禁开拦写且不落盘，关闭后同一写入放行。"""

    def setUp(self) -> None:
        self.project_dir = tempfile.mkdtemp(prefix="gt_gateoff_")
        _make_h_project(self.project_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def test_route_map_h_write_blocked_then_allowed_after_gate_off(self) -> None:
        args = {
            "project_dir": self.project_dir,
            "mermaid": 'flowchart TD\n  A["01_a.json"]',
            "文件归属": {"01_a.json": "共通线"},
            "节点剧情": {"共通线": "两人攀上了顶峰"},
        }
        # 门禁开需显式 mock：不依赖本机真实 app_settings.json（用户可能已关闭门禁）
        with _settings_patch():
            with self.assertRaises(ValueError):
                write_route_map(self.project_dir, args)
        self.assertFalse(os.path.isfile(route_map_path(self.project_dir)))

        with _settings_patch(mcpHGateEnabled=False):
            result = write_route_map(self.project_dir, args)
        self.assertTrue(result["success"])
        self.assertTrue(os.path.isfile(route_map_path(self.project_dir)))

    def test_write_metadata_allowed_after_gate_off(self) -> None:
        with _settings_patch(mcpHGateEnabled=False):
            result = write_metadata(self.project_dir, "filemeta", "01", {"角色": "测试"})
        self.assertTrue(result["success"])
        path = os.path.join(self.project_dir, "transl_cache", "pass1_cache", "01.meta.json")
        self.assertTrue(os.path.isfile(path))


class DisabledToolDispatchTests(unittest.TestCase):
    """call_mcp_tool 的禁用拦截（在未知工具判断之后、执行之前）。"""

    def test_disabled_tool_raises_dedicated_error(self) -> None:
        with _settings_patch(mcpDisabledTools=["galtransl_search_cache"]):
            with self.assertRaises(MCPToolDisabledError) as ctx:
                call_mcp_tool("galtransl_search_cache", {"project_dir": "X:/p", "query": "x"})
            self.assertIn("禁用", str(ctx.exception))

    def test_unknown_tool_still_raises_key_error(self) -> None:
        with _settings_patch(mcpDisabledTools=["galtransl_search_cache"]):
            with self.assertRaises(KeyError):
                call_mcp_tool("galtransl_not_exist")

    def test_enabled_tool_still_runs(self) -> None:
        with _settings_patch(mcpDisabledTools=["galtransl_search_cache"]):
            result = call_mcp_tool("galtransl_list_projects", {})
        self.assertIsInstance(result, dict)


class EnabledToolDefsTests(unittest.TestCase):
    """enabled_tool_defs：tools/list 与心跳共用的启用集合。"""

    def test_no_disabled_returns_all(self) -> None:
        with _settings_patch():
            self.assertEqual(len(enabled_tool_defs()), len(MCP_TOOL_DEFS))

    def test_disabled_are_filtered(self) -> None:
        with _settings_patch(mcpDisabledTools=["galtransl_search_cache", "galtransl_submit_job"]):
            defs = enabled_tool_defs()
        names = {item["name"] for item in defs}
        self.assertNotIn("galtransl_search_cache", names)
        self.assertNotIn("galtransl_submit_job", names)
        self.assertEqual(len(defs), len(MCP_TOOL_DEFS) - 2)


class BuildServerInstructionsTests(unittest.TestCase):
    """下发说明随门禁开关与禁用集合联动（默认口径被既有测试锁定）。"""

    def test_default_matches_constant(self) -> None:
        self.assertEqual(build_server_instructions(), SERVER_INSTRUCTIONS)

    def test_default_matches_golden_text(self) -> None:
        # 金样本：0.6.0 静态文本原文（术语表批次只改动工具计数与写工具清单两行）。
        # build_server_instructions 重构自它，默认参数输出必须逐字一致，
        # 防止未来编辑 f-string 拼接时静默漂移。
        golden = (
            "GalTransl 翻译项目管理服务（19 个工具：14 个只读检索 + 5 个写操作）。"
            "所有工具都需提供翻译项目根目录的绝对路径 project_dir。\n"
            "\n"
            "使用前必须遵守：\n"
            "1. 只读工具（galtransl_search_* / lookup_name / list_* / get_* / read_* / check_model）不得引发任何写入。\n"
            "   写工具仅这 5 个：write_route_map、save_metadata、write_glossary、submit_job、stop_job，各自只能改项目内的指定产物；\n"
            "   本服务不提供任意路径读写、不提供命令执行、不改程序配置。需要其它改动请让用户在 GalTransl 界面操作。\n"
            "2. submit_job 会真实启动翻译并消耗 API 额度：仅在用户明确要求时调用，调用前先与用户确认项目与引擎，\n"
            "   可先用 check_model 探测可用性（同样发起真实请求，消耗极小额度）。\n"
            "3. get_job_status / check_model / submit_job / stop_job 需 GalTransl 后端在运行；其余工具直接读磁盘。\n"
            "4. 禁止查看 H / 成人向内容：写工具对 H 内容有硬门禁，命中即拒绝。识别到成人向内容必须立即停止该方向检索，\n"
            "   不得回引原文或译文，只报告位置（文件名 + index）并请用户决定。\n"
            "5. project_dir 只能是用户明确指定的翻译项目目录。禁止指向 GalTransl 程序目录（其 backend_profiles.yaml 含 API 密钥）、仓库根目录、系统目录或他人目录。\n"
            "6. 禁止读取或外传任何凭据、密钥、API 端点。日志中命中疑似凭据的行不引用原文。\n"
            "7. 禁止规模化拉取：搜索 max_results 默认 200 / 硬顶 2000，分页 limit 默认 100 / 硬顶 1000。不要全量拉取，也不要用宽正则做枚举式扫描。\n"
            "8. 交付结论 + 定位（文件名 + index + 最短必要引文），不要堆砌原文/译文。\n"
            "\n"
            "完整约束见随项目分发的 skills/galtransl-mcp/SKILL.md。"
        )
        self.assertEqual(SERVER_INSTRUCTIONS, golden)

    def test_default_counts_match_defs(self) -> None:
        n_read = sum(1 for item in MCP_TOOL_DEFS if item["kind"] == "read")
        n_write = len(MCP_TOOL_DEFS) - n_read
        self.assertIn(f"（{len(MCP_TOOL_DEFS)} 个工具：{n_read} 个只读检索 + {n_write} 个写操作）", SERVER_INSTRUCTIONS)

    def test_disabled_subset_updates_counts_and_write_list(self) -> None:
        text = build_server_instructions(
            disabled_tools={"galtransl_submit_job", "galtransl_search_cache"}
        )
        self.assertIn(f"（{len(MCP_TOOL_DEFS) - 2} 个工具", text)
        self.assertIn("write_route_map", text)
        self.assertNotIn("submit_job、stop_job", text)

    def test_all_write_tools_disabled_reports_no_write_tools(self) -> None:
        write_names = {item["name"] for item in MCP_TOOL_DEFS if item["kind"] == "write"}
        text = build_server_instructions(disabled_tools=write_names)
        self.assertIn("本服务当前未开放任何写工具", text)

    def test_gate_off_rewrites_h_clause(self) -> None:
        text = build_server_instructions(h_gate_enabled=False)
        self.assertIn("关闭 H 门禁", text)
        self.assertNotIn("硬门禁", text)

    def test_variants_length_bounded(self) -> None:
        for text in (
            SERVER_INSTRUCTIONS,
            build_server_instructions(h_gate_enabled=False),
            build_server_instructions(disabled_tools={"galtransl_submit_job"}),
        ):
            self.assertLessEqual(len(text.encode("utf-8")), 2000)


class McpToolsEndpointTests(unittest.TestCase):
    """/api/mcp-tools：设置页工具开关的唯一真源。"""

    def test_mcp_tools_endpoint_payload(self) -> None:
        from GalTransl.server_handlers_root import do_get

        class _Stub:
            def __init__(self) -> None:
                self.path = "/api/mcp-tools"
                self.payload = None

            def _send_json(self, payload, status=200) -> None:
                self.payload = payload

        stub = _Stub()
        do_get(stub, mock.Mock())
        tools = stub.payload["tools"]
        self.assertEqual([t["name"] for t in tools], [d["name"] for d in MCP_TOOL_DEFS])
        for tool in tools:
            self.assertIn(tool["kind"], ("read", "write"))
            self.assertTrue(tool["description"])


class RunMcpServerGateTests(unittest.TestCase):
    """stdio server 侧：tools/list 过滤、禁用调用的错误包装、启动说明快照。"""

    def test_handle_list_tools_filters_disabled(self) -> None:
        with _settings_patch(mcpDisabledTools=["galtransl_search_cache"]):
            result = asyncio.run(run_mcp_server.handle_list_tools(None))
        names = {tool.name for tool in result.tools}
        self.assertNotIn("galtransl_search_cache", names)
        self.assertEqual(len(names), len(MCP_TOOL_DEFS) - 1)

    def test_handle_call_tool_disabled_returns_error_result(self) -> None:
        with _settings_patch(mcpDisabledTools=["galtransl_search_cache"]):
            result = asyncio.run(
                run_mcp_server.handle_call_tool(
                    None,
                    SimpleNamespace(
                        name="galtransl_search_cache",
                        arguments={"project_dir": "X:/p", "query": "x"},
                    ),
                )
            )
        self.assertTrue(result.is_error)
        self.assertIn("禁用", result.content[0].text)

    def test_startup_instructions_reflect_gate_off(self) -> None:
        with mock.patch(
            "run_mcp_server.load_app_settings",
            return_value={"mcpHGateEnabled": False, "mcpDisabledTools": []},
        ):
            text = run_mcp_server._build_startup_instructions()
        self.assertIn("关闭 H 门禁", text)

    def test_startup_description_counts_follow_enabled_set(self) -> None:
        defs = [item for item in MCP_TOOL_DEFS if item["name"] != "galtransl_submit_job"]
        with mock.patch("run_mcp_server.enabled_tool_defs", return_value=defs):
            text = run_mcp_server._build_startup_description()
        n_read = sum(1 for item in defs if item["kind"] == "read")
        self.assertIn(f"（{len(defs)} 个工具：{n_read} 只读检索 + {len(defs) - n_read} 受限写入）", text)

    def test_startup_description_default_matches_full_count(self) -> None:
        with _settings_patch():
            text = run_mcp_server._build_startup_description()
        self.assertIn(f"{len(MCP_TOOL_DEFS)} 个工具", text)


if __name__ == "__main__":
    unittest.main()
