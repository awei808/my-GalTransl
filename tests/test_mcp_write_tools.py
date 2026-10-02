"""MCP 写工具单测（0.6.0 新增的 4 个 kind=write 工具）。

覆盖三条主线：
1. 路线图/元数据写入的正确性与原子性（含路径穿越拒绝）；
2. **H 硬门禁**：命中即拒绝且不落盘（项目 H 词库 + H 区间两条判据）；
3. 作业域工具的 HTTP 契约与后端不可达时的中文错误（mock，不发真实请求）。
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from GalTransl.ConfigHelper import detect_config_file
from GalTransl.mcp_tools import (
    MCP_TOOL_DEFS,
    _H_DENY_MESSAGE,
    _READ_ONLY_TOOL_NAMES,
    _attach_project_dir_warning,
    call_mcp_tool,
    enforce_h_gate,
    validate_project_dir,
    write_metadata,
    write_route_map,
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


def _make_project(root: str, h_words: list | None = None) -> None:
    """构造最小可识别项目：config.inc.yaml + 译前/译后字典 + 项目 H 词库。"""
    os.makedirs(os.path.join(root, "transl_cache"), exist_ok=True)
    with open(os.path.join(root, "项目禁用词_非h.txt"), "w", encoding="utf-8") as f:
        f.write("// 非 h 禁用词\n")
    with open(os.path.join(root, "项目禁用词_h.txt"), "w", encoding="utf-8") as f:
        f.write("// 项目 H 禁用词\n")
        for word in h_words or []:
            f.write(f"{word}|测试用\n")
    with open(os.path.join(root, "config.inc.yaml"), "w", encoding="utf-8") as f:
        # 字典项均指向项目内文件，避免 _load_rebuild_deps 去找程序目录下的全局 Dict
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


class RouteMapWriteToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project_dir = tempfile.mkdtemp(prefix="gt_wtool_")
        _make_project(self.project_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def _route_path(self) -> str:
        return os.path.join(self.project_dir, "transl_cache", "pass0_cache", "PlotRouteMap.json")

    def test_write_route_map_via_dispatch(self) -> None:
        result = call_mcp_tool(
            "galtransl_write_route_map",
            {
                "project_dir": self.project_dir,
                "mermaid": 'flowchart TD\n  A["01_a.json"]',
                "文件归属": {"01_a.json": "共通线"},
                "节点剧情": {"共通线": "序幕"},
            },
        )
        self.assertTrue(result["success"])
        self.assertTrue(os.path.isfile(self._route_path()))

    def test_invalid_mermaid_rejected_and_not_written(self) -> None:
        with self.assertRaises(ValueError):
            call_mcp_tool(
                "galtransl_write_route_map",
                {"project_dir": self.project_dir, "mermaid": "不是 mermaid"},
            )
        self.assertFalse(os.path.isfile(self._route_path()))


class MetadataWriteToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project_dir = tempfile.mkdtemp(prefix="gt_wtool_")
        _make_project(self.project_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def test_filemeta_written_atomically(self) -> None:
        result = call_mcp_tool(
            "galtransl_save_metadata",
            {
                "project_dir": self.project_dir,
                "kind": "filemeta",
                "filename": "01_a",
                "entry": {"角色": ["琴美"]},
            },
        )
        self.assertTrue(result["success"])
        path = os.path.join(self.project_dir, "transl_cache", "pass1_cache", "01_a.meta.json")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"角色": ["琴美"]})
        # 原子写：不得残留 .tmp
        self.assertFalse(os.path.isfile(path + ".tmp"))

    def test_filemeta_requires_filename(self) -> None:
        with self.assertRaises(ValueError):
            call_mcp_tool(
                "galtransl_save_metadata",
                {"project_dir": self.project_dir, "kind": "filemeta", "entry": {}},
            )

    def test_entry_must_be_object(self) -> None:
        with self.assertRaises(ValueError):
            call_mcp_tool(
                "galtransl_save_metadata",
                {"project_dir": self.project_dir, "kind": "filemeta", "filename": "a", "entry": [1, 2]},
            )

    def test_unknown_kind_rejected(self) -> None:
        with self.assertRaises(ValueError):
            write_metadata(self.project_dir, "whatever", "", {})

    def test_path_traversal_filename_rejected(self) -> None:
        for bad in ("../evil", "..\\evil", "sub/evil", "sub\\evil", "..", ".", "/abs/evil", "C:\\evil"):
            with self.subTest(filename=bad):
                with self.assertRaises(ValueError):
                    write_metadata(self.project_dir, "filemeta", bad, {})

    def test_ads_and_reserved_names_rejected(self) -> None:
        # 冒号挡 NTFS 交换数据流；保留名在 Windows 上会变成设备——两者都必须抛 ValueError，
        # 而不是让 os.makedirs 抛出 OSError 泄漏给调用方
        for bad in ("a:b", "aux", "NUL", "com1", "LPT9", "CON.txt"):
            with self.subTest(filename=bad):
                with self.assertRaises(ValueError):
                    write_metadata(self.project_dir, "filemeta", bad, {})

    def test_traversal_writes_nothing_outside_project(self) -> None:
        victim = os.path.join(os.path.dirname(self.project_dir), "evil.meta.json")
        with self.assertRaises(ValueError):
            write_metadata(self.project_dir, "filemeta", "../evil", {})
        self.assertFalse(os.path.isfile(victim))

    def test_plotroute_and_globalprompt_kinds_are_closed(self) -> None:
        # 收窄：plotroute 必须走 write_route_map（mermaid 校验），globalprompt 由流水线生成；
        # 旧版 save_metadata 可整包覆盖写这两个产物，绕过 mermaid 校验，已堵死
        for kind in ("plotroute", "globalprompt"):
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    write_metadata(self.project_dir, kind, "", {"mermaid": "不是 mermaid"})


class HGateTests(unittest.TestCase):
    """H 硬门禁：命中即拒绝，且不落盘。"""

    def setUp(self) -> None:
        self.project_dir = tempfile.mkdtemp(prefix="gt_hgate_")
        _make_project(self.project_dir, h_words=["攀上"])

    def tearDown(self) -> None:
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def test_project_h_word_is_detected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            enforce_h_gate(self.project_dir, texts=["他攀上了顶峰"])
        self.assertEqual(str(ctx.exception), _H_DENY_MESSAGE)

    def test_non_h_text_passes(self) -> None:
        enforce_h_gate(self.project_dir, texts=["她走进了图书馆"])

    def test_route_map_with_h_summary_rejected_and_not_written(self) -> None:
        with self.assertRaises(ValueError):
            call_mcp_tool(
                "galtransl_write_route_map",
                {
                    "project_dir": self.project_dir,
                    "mermaid": 'flowchart TD\n  A["01_a.json"]',
                    "文件归属": {"01_a.json": "共通线"},
                    "节点剧情": {"共通线": "两人攀上了顶峰"},
                },
            )
        self.assertFalse(
            os.path.isfile(os.path.join(self.project_dir, "transl_cache", "pass0_cache", "PlotRouteMap.json"))
        )

    def test_save_metadata_kind_enum_is_narrowed(self) -> None:
        # 工具 schema 的 kind 枚举必须与 write_metadata 的白名单一致（plotroute/globalprompt 不再暴露）
        save_def = next(d for d in MCP_TOOL_DEFS if d["name"] == "galtransl_save_metadata")
        self.assertEqual(save_def["input_schema"]["properties"]["kind"]["enum"], ["filemeta", "batchmeta"])
        self.assertIn("write_route_map", save_def["description"])

    def test_h_gate_message_tells_agent_to_defer_to_user(self) -> None:
        # 拒绝文案必须引导 agent 如实转告用户，而非静默重试
        self.assertIn("用户", _H_DENY_MESSAGE)

    def test_cache_file_in_h_range_rejected(self) -> None:
        # 文件维度判据：目标缓存文件落在 H 区间时拒绝
        with mock.patch("GalTransl.mcp_tools._entry_has_h", return_value=True):
            with self.assertRaises(ValueError):
                write_metadata(self.project_dir, "batchmeta", "01_a", {"视角": "第三人称"})

    def test_entry_has_h_uses_pass3_relative_path(self) -> None:
        # 回归：_resolve_cache_h_ranges 只认「pass3_cache 下真实存在的缓存相对路径」，
        # 传裸文件名会永远返回 has_h=False，让文件维度判据静默失效
        from GalTransl.mcp_tools import _entry_has_h

        with mock.patch(
            "GalTransl.server_cache._resolve_cache_h_ranges", return_value={"has_h": True}
        ) as m:
            self.assertTrue(_entry_has_h(self.project_dir, "01_a"))
        self.assertEqual(m.call_args.args[1], "pass3_cache/01_a.json")

    def test_entry_has_h_detects_real_h_batch(self) -> None:
        # 端到端：造出真实缓存 + 落在 H 档位的批次元数据，判据必须为真
        from GalTransl.mcp_tools import _entry_has_h

        os.makedirs(os.path.join(self.project_dir, "transl_cache", "pass3_cache"), exist_ok=True)
        os.makedirs(os.path.join(self.project_dir, "transl_cache", "pass2_cache"), exist_ok=True)
        with open(
            os.path.join(self.project_dir, "transl_cache", "pass3_cache", "01_a.json"), "w", encoding="utf-8"
        ) as f:
            json.dump([], f)
        with open(
            os.path.join(self.project_dir, "transl_cache", "pass2_cache", "01_a.json.batch.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump({"批次": [{"区间": [1, 5], "h": 0.95}]}, f)
        self.assertTrue(_entry_has_h(self.project_dir, "01_a"))

    def test_entry_has_h_false_when_no_cache(self) -> None:
        from GalTransl.mcp_tools import _entry_has_h

        self.assertFalse(_entry_has_h(self.project_dir, "不存在的文件"))


class JobToolTests(unittest.TestCase):
    """作业域工具：HTTP 契约与错误文案（mock，不发真实请求）。"""

    def setUp(self) -> None:
        # 作业域工具同样受 L3 校验，故需一个可识别项目（_make_project 建 config.inc.yaml）
        self.project_dir = tempfile.mkdtemp(prefix="gt_job_")
        _make_project(self.project_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def test_submit_job_posts_expected_payload(self) -> None:
        with mock.patch("GalTransl.mcp_backend_client.post_json", return_value={"job_id": "abc123"}) as m:
            result = call_mcp_tool(
                "galtransl_submit_job",
                {"project_dir": self.project_dir, "translator": "ForGal-full-pipeline"},
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["job_id"], "abc123")
        path, payload = m.call_args.args
        self.assertEqual(path, "/api/jobs")
        self.assertEqual(payload["project_dir"], self.project_dir)
        self.assertEqual(payload["translator"], "ForGal-full-pipeline")

    def test_submit_job_requires_translator(self) -> None:
        with self.assertRaises(ValueError):
            call_mcp_tool("galtransl_submit_job", {"project_dir": self.project_dir})

    def test_submit_job_passes_optional_filters(self) -> None:
        with mock.patch("GalTransl.mcp_backend_client.post_json", return_value={}) as m:
            call_mcp_tool(
                "galtransl_submit_job",
                {
                    "project_dir": self.project_dir,
                    "translator": "t",
                    "file_filter": ["01_a.json"],
                    "backend_profile": "p1",
                },
            )
        payload = m.call_args.args[1]
        self.assertEqual(payload["file_filter"], ["01_a.json"])
        self.assertEqual(payload["backend_profile"], "p1")

    def test_submit_job_rejects_non_string_file_filter(self) -> None:
        with self.assertRaises(ValueError):
            call_mcp_tool(
                "galtransl_submit_job",
                {"project_dir": self.project_dir, "translator": "t", "file_filter": [1, 2]},
            )

    def test_stop_job_posts_to_encoded_project_path(self) -> None:
        with mock.patch("GalTransl.mcp_backend_client.post_json", return_value={"success": True}) as m:
            result = call_mcp_tool("galtransl_stop_job", {"project_dir": self.project_dir})
        self.assertTrue(result["success"])
        self.assertTrue(m.call_args.args[0].startswith("/api/projects/"))
        self.assertTrue(m.call_args.args[0].endswith("/stop"))

    def test_backend_down_yields_readable_chinese_error(self) -> None:
        # 指向一个必然无监听的端口，验证错误文案对 agent 可读
        with mock.patch.dict(os.environ, {"GALTRANSL_BACKEND_URL": "http://127.0.0.1:1"}):
            with self.assertRaises(RuntimeError) as ctx:
                call_mcp_tool("galtransl_stop_job", {"project_dir": self.project_dir})
        self.assertIn("GalTransl 后端", str(ctx.exception))

    def test_get_job_status_filters_by_project_and_slims_runtime(self) -> None:
        jobs_payload = {
            "jobs": [
                {
                    "job_id": "b",
                    "project_dir": self.project_dir,
                    "status": "running",
                    "translator": "ForGal-full-pipeline",
                    "config_file_name": "config.inc.yaml",
                    "file_filter": ["01_a.json"],
                    "created_at": "2",
                    "started_at": "2",
                    "finished_at": None,
                    "success": False,
                    "error": None,
                },
                {"job_id": "other", "project_dir": "D:/somewhere_else", "status": "completed"},
            ]
        }
        runtime_payload = {
            "stage": "翻译执行",
            "stage_index": 7,
            "stage_total": 9,
            "current_file": "01_a.json",
            "summary": {"total": 10, "translated": 4, "percent": 40.0},
            # 大字段必须被白名单挡在返回体外（上下文纪律）
            "translation_previews": {"w1": "不透传"},
            "recent_successes": ["不透传"],
        }
        with mock.patch(
            "GalTransl.mcp_backend_client.get_json", side_effect=[jobs_payload, runtime_payload]
        ) as m:
            result = call_mcp_tool("galtransl_get_job_status", {"project_dir": self.project_dir})
        self.assertEqual(result["active_job"]["job_id"], "b")
        self.assertEqual(result["active_job"]["file_filter"], ["01_a.json"])
        self.assertEqual([job["job_id"] for job in result["recent_jobs"]], ["b"])
        self.assertEqual(result["runtime"]["stage"], "翻译执行")
        self.assertEqual(result["runtime"]["summary"]["percent"], 40.0)
        self.assertNotIn("translation_previews", result["runtime"])
        self.assertNotIn("recent_successes", result["runtime"])
        self.assertEqual(m.call_args_list[0].args[0], "/api/jobs")
        self.assertTrue(m.call_args_list[1].args[0].endswith("/runtime"))

    def test_get_job_status_no_jobs_yields_null_active(self) -> None:
        with mock.patch(
            "GalTransl.mcp_backend_client.get_json",
            side_effect=[{"jobs": []}, {"stage": "", "summary": {}}],
        ):
            result = call_mcp_tool("galtransl_get_job_status", {"project_dir": self.project_dir})
        self.assertIsNone(result["active_job"])
        self.assertEqual(result["recent_jobs"], [])

    def test_check_model_posts_expected_payload(self) -> None:
        with mock.patch("GalTransl.mcp_backend_client.post_json", return_value={"success": True}) as m:
            result = call_mcp_tool(
                "galtransl_check_model",
                {"project_dir": self.project_dir, "translator": "ForGal-full-pipeline"},
            )
        self.assertTrue(result["check"]["success"])
        path, payload = m.call_args.args
        self.assertTrue(path.startswith("/api/projects/"))
        self.assertTrue(path.endswith("/check-model"))
        self.assertEqual(payload["translator"], "ForGal-full-pipeline")
        # config 未传时按项目探测（本 fixture 建的是 config.inc.yaml）
        self.assertEqual(payload["config_file_name"], "config.inc.yaml")
        self.assertEqual(result["config_file_name"], "config.inc.yaml")

    def test_check_model_requires_translator(self) -> None:
        with self.assertRaises(ValueError):
            call_mcp_tool("galtransl_check_model", {"project_dir": self.project_dir})

    def test_check_model_passes_optional_fields(self) -> None:
        with mock.patch("GalTransl.mcp_backend_client.post_json", return_value={}) as m:
            call_mcp_tool(
                "galtransl_check_model",
                {
                    "project_dir": self.project_dir,
                    "translator": "t",
                    "config_file_name": "config.yaml",
                    "backend_profile": "p1",
                },
            )
        payload = m.call_args.args[1]
        self.assertEqual(payload["config_file_name"], "config.yaml")
        self.assertEqual(payload["backend_profile"], "p1")


class ProjectDirValidationTests(unittest.TestCase):
    """L3 路径白名单：写工具只接受可识别的 GalTransl 项目。"""

    def setUp(self) -> None:
        self.project_dir = tempfile.mkdtemp(prefix="gt_l3_")
        _make_project(self.project_dir)
        # 空目录：存在但不是可识别项目
        self.bare_dir = tempfile.mkdtemp(prefix="gt_l3bare_")

    def tearDown(self) -> None:
        shutil.rmtree(self.project_dir, ignore_errors=True)
        shutil.rmtree(self.bare_dir, ignore_errors=True)

    def test_valid_project_is_accepted(self) -> None:
        self.assertEqual(validate_project_dir(self.project_dir), "config.inc.yaml")

    def test_bare_directory_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_project_dir(self.bare_dir)
        self.assertIn("不是可识别的 GalTransl 项目", str(ctx.exception))

    def test_detect_config_file_fallback_does_not_whitelist_empty_dir(self) -> None:
        # detect_config_file 找不到时会回退返回 config.yaml，故必须再确认文件真实存在；
        # 否则任意空目录都会被判为合法项目（本测试锁定该性质）
        self.assertEqual(detect_config_file(self.bare_dir), "config.yaml")
        self.assertFalse(os.path.isfile(os.path.join(self.bare_dir, "config.yaml")))
        with self.assertRaises(ValueError):
            validate_project_dir(self.bare_dir)

    def test_config_yaml_also_accepted(self) -> None:
        # config.inc.yaml 优先，但只有 config.yaml 的项目同样合法
        with open(os.path.join(self.bare_dir, "config.yaml"), "w", encoding="utf-8") as f:
            f.write("common:\n  language: zh-cn\n")
        self.assertEqual(validate_project_dir(self.bare_dir), "config.yaml")

    def test_all_four_write_tools_reject_bare_directory(self) -> None:
        cases = [
            ("galtransl_write_route_map", {"mermaid": 'flowchart TD\n  A["x"]'}),
            ("galtransl_save_metadata", {"kind": "filemeta", "filename": "x", "entry": {}}),
            ("galtransl_submit_job", {"translator": "t"}),
            ("galtransl_stop_job", {}),
        ]
        for tool, extra in cases:
            with self.subTest(tool=tool):
                with self.assertRaises(ValueError):
                    call_mcp_tool(tool, {"project_dir": self.bare_dir, **extra})

    def test_repo_root_cannot_be_written(self) -> None:
        # 回归：审查实测把 project_dir 指向仓库根就能在 <repo>/transl_cache 下落盘
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with self.assertRaises(ValueError):
            call_mcp_tool(
                "galtransl_save_metadata",
                {"project_dir": repo_root, "kind": "filemeta", "filename": "a", "entry": {"a": 1}},
            )

    def test_valid_project_still_writable(self) -> None:
        result = call_mcp_tool(
            "galtransl_save_metadata",
            {"project_dir": self.project_dir, "kind": "batchmeta", "filename": "01_a", "entry": {"视角": "第一人称"}},
        )
        self.assertTrue(result["success"])

    def test_config_inc_takes_priority_over_config_yaml(self) -> None:
        # 两者并存时取 config.inc.yaml（锁定 detect_config_file 的候选顺序，
        # 将来有人改顺序会立刻暴露）
        with open(os.path.join(self.bare_dir, "config.inc.yaml"), "w", encoding="utf-8") as f:
            f.write("common: {}\n")
        with open(os.path.join(self.bare_dir, "config.yaml"), "w", encoding="utf-8") as f:
            f.write("common: {}\n")
        self.assertEqual(validate_project_dir(self.bare_dir), "config.inc.yaml")

    def test_empty_config_file_still_counts_as_project(self) -> None:
        # 判据是存在性而非内容合法性（fail-open 取舍，与 H 门禁一致）
        with open(os.path.join(self.bare_dir, "config.yaml"), "w", encoding="utf-8"):
            pass
        self.assertEqual(validate_project_dir(self.bare_dir), "config.yaml")

    def test_public_write_functions_self_validate(self) -> None:
        # write_route_map / write_metadata 是公开入口，不能只依赖处理器层的校验，
        # 否则直接调用者（未来复用方）可绕过 L3
        with self.assertRaises(ValueError):
            write_metadata(self.bare_dir, "filemeta", "a", {"a": 1})
        with self.assertRaises(ValueError):
            write_route_map(self.bare_dir, {"mermaid": 'flowchart TD\n  A["x"]'})


class ReadToolProjectDirWarningTests(unittest.TestCase):
    """只读工具对非法项目只告警不阻断。"""

    def setUp(self) -> None:
        self.project_dir = tempfile.mkdtemp(prefix="gt_warn_")
        _make_project(self.project_dir)
        self.bare_dir = tempfile.mkdtemp(prefix="gt_warnbare_")

    def tearDown(self) -> None:
        shutil.rmtree(self.project_dir, ignore_errors=True)
        shutil.rmtree(self.bare_dir, ignore_errors=True)

    def test_bare_dir_yields_warning_not_error(self) -> None:
        result = call_mcp_tool("galtransl_search_scripts", {"project_dir": self.bare_dir, "query": "x"})
        self.assertFalse(result["project_dir_valid"])
        self.assertIn("project_dir_hint", result)

    def test_valid_project_reports_true(self) -> None:
        result = call_mcp_tool("galtransl_search_scripts", {"project_dir": self.project_dir, "query": "x"})
        self.assertTrue(result["project_dir_valid"])

    def test_read_only_set_is_thirteen_and_disjoint_from_write(self) -> None:
        writes = {d["name"] for d in MCP_TOOL_DEFS if d["kind"] == "write"}
        self.assertEqual(len(_READ_ONLY_TOOL_NAMES), 13)
        self.assertEqual(_READ_ONLY_TOOL_NAMES & writes, frozenset())
        self.assertEqual(len(_READ_ONLY_TOOL_NAMES | writes), 17)

    def test_write_tool_success_is_not_annotated_with_warning(self) -> None:
        # 写工具已硬校验，成功返回体不应再被附加 project_dir_valid
        result = call_mcp_tool(
            "galtransl_save_metadata",
            {"project_dir": self.project_dir, "kind": "batchmeta", "filename": "01_a", "entry": {"视角": "第三人称"}},
        )
        self.assertTrue(result["success"])
        self.assertNotIn("project_dir_valid", result)

    def test_warning_early_returns(self) -> None:
        # (1) 工具自身已含该键 → 不覆盖（保护工具自有字段）
        self.assertEqual(
            _attach_project_dir_warning(
                "galtransl_search_scripts",
                {"project_dir": self.bare_dir},
                {"project_dir_valid": "SELF"},
            )["project_dir_valid"],
            "SELF",
        )
        # (2) 结果非 dict → 原样返回
        self.assertEqual(
            _attach_project_dir_warning("galtransl_search_scripts", {"project_dir": self.bare_dir}, "raw"),
            "raw",
        )
        # (3) 非只读工具 → 不加
        self.assertEqual(
            _attach_project_dir_warning("galtransl_save_metadata", {"project_dir": self.bare_dir}, {}),
            {},
        )
        # (4) 调用未带 project_dir → 不加
        self.assertEqual(
            _attach_project_dir_warning("galtransl_search_scripts", {"query": "x"}, {}),
            {},
        )

    def test_list_projects_has_no_project_dir_and_no_warning(self) -> None:
        result = call_mcp_tool("galtransl_list_projects", {})
        self.assertNotIn("project_dir_valid", result)


class WriteToolRegistrationTests(unittest.TestCase):
    def test_four_write_tools_registered(self) -> None:
        writes = {d["name"] for d in MCP_TOOL_DEFS if d["kind"] == "write"}
        self.assertEqual(
            writes,
            {
                "galtransl_write_route_map",
                "galtransl_save_metadata",
                "galtransl_submit_job",
                "galtransl_stop_job",
            },
        )

    def test_write_tools_require_project_dir(self) -> None:
        for d in MCP_TOOL_DEFS:
            if d["kind"] == "write":
                with self.subTest(tool=d["name"]):
                    self.assertIn("project_dir", d["input_schema"]["required"])

    def test_submit_job_description_warns_about_api_cost(self) -> None:
        # 该工具会真实消耗额度：描述必须含明确的调用前提
        desc = next(d for d in MCP_TOOL_DEFS if d["name"] == "galtransl_submit_job")["description"]
        self.assertIn("用户明确要求", desc)


if __name__ == "__main__":
    unittest.main()
