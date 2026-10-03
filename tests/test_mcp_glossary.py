"""MCP 术语表（GPT 字典）读写工具单测（galtransl_read_glossary / galtransl_write_glossary）。

覆盖两条主线：
1. 读取：多文件汇总、单文件限定、筛选（子串/正则/非法正则）、分页、配置缺失、非术语表文件拒绝、
   只读无副作用；
2. 写入：upsert/delete 计数与内容、原地更新保注释与顺序、note 省略/清空语义、竖线转义往返、
   原子写、CRLF 保持、新建文件并自动登记 gpt.dict、字段与文件名校验、H 门禁拒绝且不落盘、
   L3 裸目录拒绝。
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from GalTransl.mcp_tools import (
    _GLOSSARY_NEW_FILE_HEADER,
    _H_DENY_MESSAGE,
    call_mcp_tool,
    read_glossary,
    tool_annotations,
    write_glossary,
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


def _make_project(
    root: str,
    h_words: list | None = None,
    glossary: str = "//格式说明\n\n大家さん|房东\n",
    extra_gpt_files: list | None = None,
) -> None:
    """构造最小可识别项目：config.inc.yaml + 项目术语表 +（可选）H 词库与第二个术语表。"""
    os.makedirs(os.path.join(root, "transl_cache"), exist_ok=True)
    with open(os.path.join(root, "项目禁用词_非h.txt"), "w", encoding="utf-8", newline="") as f:
        f.write("// 非 h 禁用词\n")
    with open(os.path.join(root, "项目禁用词_h.txt"), "w", encoding="utf-8", newline="") as f:
        f.write("// 项目 H 禁用词\n")
        for word in h_words or []:
            f.write(f"{word}|测试用\n")
    with open(os.path.join(root, "项目GPT字典.txt"), "w", encoding="utf-8", newline="") as f:
        f.write(glossary)
    gpt_lines = ["  gpt.dict:", "    - (project_dir)项目GPT字典.txt"]
    for name in extra_gpt_files or []:
        gpt_lines.append(f"    - (project_dir){name}")
    with open(os.path.join(root, "config.inc.yaml"), "w", encoding="utf-8") as f:
        # 字典项均指向项目内文件，避免 _load_rebuild_deps 去找程序目录下的全局 Dict
        f.write(
            "dictionary:\n"
            "  defaultDictFolder: ''\n"
            + "\n".join(gpt_lines) + "\n"
            "  forbiddenDictH:\n"
            "    - (project_dir)项目禁用词_h.txt\n"
            "  forbiddenDictNonH:\n"
            "    - (project_dir)项目禁用词_非h.txt\n"
            "  preDict:\n"
            "    - (project_dir)项目字典_译前.txt\n"
            "  postDict: []\n"
        )
    with open(os.path.join(root, "项目字典_译前.txt"), "w", encoding="utf-8", newline="") as f:
        f.write("我慢汁|がまん汁\n")


class GlossaryProjectFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.project_dir = tempfile.mkdtemp(prefix="gt_gloss_")

    def tearDown(self) -> None:
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def _glossary_path(self, name: str = "项目GPT字典.txt") -> str:
        return os.path.join(self.project_dir, name)

    def _read_glossary_text(self, name: str = "项目GPT字典.txt") -> str:
        with open(self._glossary_path(name), "r", encoding="utf-8", newline="") as f:
            return f.read()

    def _config_text(self) -> str:
        with open(os.path.join(self.project_dir, "config.inc.yaml"), "r", encoding="utf-8") as f:
            return f.read()


class ReadGlossaryTests(GlossaryProjectFixture):
    def test_reads_entries_and_skips_comments_blanks_separators(self) -> None:
        _make_project(
            self.project_dir,
            glossary=(
                "//整行注释\n"
                "====\n"
                "\n"
                "大家さん|房东\n"
                "re:あ\\|い|啊|x|y\n"
            ),
        )
        result = read_glossary(self.project_dir)
        self.assertEqual(result["files"], ["项目GPT字典.txt"])
        self.assertEqual(result["missing_files"], [])
        self.assertEqual(result["total"], 2)
        first, second = result["entries"]
        self.assertEqual((first["file"], first["line_no"]), ("项目GPT字典.txt", 4))
        self.assertEqual((first["src"], first["dst"], first["note"]), ("大家さん", "房东", ""))
        self.assertFalse(first["is_regex"])
        self.assertEqual(second["src"], "re:あ|い")
        self.assertEqual(second["dst"], "啊")
        self.assertEqual(second["note"], "x|y")
        self.assertTrue(second["is_regex"])
        self.assertEqual(second["regex_error"], "")

    def test_reads_all_project_glossary_files_and_lists_missing(self) -> None:
        _make_project(
            self.project_dir,
            extra_gpt_files=["项目术语表_补.txt", "项目术语表_缺.txt"],
        )
        with open(self._glossary_path("项目术语表_补.txt"), "w", encoding="utf-8", newline="") as f:
            f.write("追加词|追加译\n")
        result = read_glossary(self.project_dir)
        self.assertEqual(result["files"], ["项目GPT字典.txt", "项目术语表_补.txt", "项目术语表_缺.txt"])
        self.assertEqual(result["missing_files"], ["项目术语表_缺.txt"])
        self.assertEqual(
            [(e["file"], e["src"]) for e in result["entries"]],
            [("项目GPT字典.txt", "大家さん"), ("项目术语表_补.txt", "追加词")],
        )

    def test_single_file_scope_and_non_glossary_rejection(self) -> None:
        _make_project(self.project_dir, extra_gpt_files=["项目术语表_补.txt"])
        with open(self._glossary_path("项目术语表_补.txt"), "w", encoding="utf-8", newline="") as f:
            f.write("追加词|追加译\n")
        scoped = read_glossary(self.project_dir, filename="项目术语表_补.txt")
        self.assertEqual(scoped["files"], ["项目术语表_补.txt"])
        self.assertEqual([e["src"] for e in scoped["entries"]], ["追加词"])
        with self.assertRaises(ValueError):
            read_glossary(self.project_dir, filename="项目字典_译前.txt")
        with self.assertRaises(ValueError):
            read_glossary(self.project_dir, filename="未登记文件.txt")
        with self.assertRaises(ValueError):
            read_glossary(self.project_dir, filename="../config.inc.yaml")

    def test_query_filter_substring_regex_and_invalid_regex(self) -> None:
        _make_project(
            self.project_dir,
            glossary="大家さん|房东|敬称\n学校|学校\n",
        )
        by_note = read_glossary(self.project_dir, query="敬称")
        self.assertEqual([e["src"] for e in by_note["entries"]], ["大家さん"])
        by_dst = read_glossary(self.project_dir, query="学校")
        self.assertEqual([e["src"] for e in by_dst["entries"]], ["学校"])
        by_regex = read_glossary(self.project_dir, query="^大家", use_regex=True)
        self.assertEqual([e["src"] for e in by_regex["entries"]], ["大家さん"])
        with self.assertRaises(ValueError):
            read_glossary(self.project_dir, query="([", use_regex=True)

    def test_pagination_fields(self) -> None:
        _make_project(
            self.project_dir,
            glossary="".join(f"词{i}|译{i}\n" for i in range(5)),
        )
        page = read_glossary(self.project_dir, offset=1, limit=2)
        self.assertEqual((page["total"], page["offset"], page["limit"], page["returned"]), (5, 1, 2, 2))
        self.assertTrue(page["has_more"])
        self.assertEqual([e["src"] for e in page["entries"]], ["词1", "词2"])
        last = read_glossary(self.project_dir, offset=4, limit=2)
        self.assertFalse(last["has_more"])

    def test_missing_config_returns_empty_without_error(self) -> None:
        result = read_glossary(self.project_dir)
        self.assertFalse(result["config_exists"])
        self.assertEqual((result["total"], result["entries"]), (0, []))
        self.assertIn("hint", result)

    def test_read_has_no_side_effects(self) -> None:
        _make_project(self.project_dir)
        config_before = self._config_text()
        glossary_before = self._read_glossary_text()
        before_files = sorted(os.listdir(self.project_dir))
        read_glossary(self.project_dir)
        self.assertEqual(self._config_text(), config_before)
        self.assertEqual(self._read_glossary_text(), glossary_before)
        self.assertEqual(sorted(os.listdir(self.project_dir)), before_files)

    def test_dispatch_adds_project_dir_warning(self) -> None:
        _make_project(self.project_dir)
        ok = call_mcp_tool("galtransl_read_glossary", {"project_dir": self.project_dir})
        self.assertTrue(ok["project_dir_valid"])
        bare = tempfile.mkdtemp(prefix="gt_gloss_bare_")
        try:
            warned = call_mcp_tool("galtransl_read_glossary", {"project_dir": bare})
            self.assertFalse(warned["project_dir_valid"])
            self.assertFalse(warned["config_exists"])
        finally:
            shutil.rmtree(bare, ignore_errors=True)


class WriteGlossaryTests(GlossaryProjectFixture):
    def test_upsert_updates_in_place_and_appends(self) -> None:
        _make_project(self.project_dir, glossary="//头注释\n大家さん|房东\n\n学校|学校\n")
        result = write_glossary(
            self.project_dir,
            "项目GPT字典.txt",
            "upsert",
            [
                {"src": "学校", "dst": "学校（机构）"},
                {"src": "新词", "dst": "新译", "note": "备注"},
            ],
        )
        self.assertTrue(result["success"])
        self.assertEqual((result["added"], result["updated"], result["unchanged"]), (1, 1, 0))
        self.assertFalse(result["registered_in_config"])
        self.assertEqual(
            self._read_glossary_text(),
            "//头注释\n大家さん|房东\n\n学校|学校（机构）\n新词|新译|备注\n",
        )
        self.assertFalse(os.path.isfile(self._glossary_path() + ".tmp"))

    def test_upsert_note_omitted_preserves_and_empty_clears(self) -> None:
        _make_project(self.project_dir, glossary="大家さん|房东|敬称\n")
        write_glossary(self.project_dir, "项目GPT字典.txt", "upsert", [{"src": "大家さん", "dst": "房东先生"}])
        self.assertIn("大家さん|房东先生|敬称\n", self._read_glossary_text())
        write_glossary(
            self.project_dir,
            "项目GPT字典.txt",
            "upsert",
            [{"src": "大家さん", "dst": "房东先生", "note": ""}],
        )
        self.assertIn("大家さん|房东先生\n", self._read_glossary_text())

    def test_upsert_is_idempotent(self) -> None:
        _make_project(self.project_dir)
        first = write_glossary(self.project_dir, "项目GPT字典.txt", "upsert", [{"src": "新词", "dst": "新译"}])
        text_after_first = self._read_glossary_text()
        second = write_glossary(self.project_dir, "项目GPT字典.txt", "upsert", [{"src": "新词", "dst": "新译"}])
        self.assertEqual((first["added"], first["updated"]), (1, 0))
        self.assertEqual((second["added"], second["updated"], second["unchanged"]), (0, 0, 1))
        self.assertEqual(self._read_glossary_text(), text_after_first)

    def test_duplicate_src_lines_are_all_updated(self) -> None:
        _make_project(self.project_dir, glossary="词|旧1\n别的|保持\n词|旧2\n")
        result = write_glossary(self.project_dir, "项目GPT字典.txt", "upsert", [{"src": "词", "dst": "新"}])
        self.assertEqual(result["updated"], 2)
        self.assertEqual(self._read_glossary_text(), "词|新\n别的|保持\n词|新\n")

    def test_duplicate_src_within_one_call_appends_then_matches(self) -> None:
        _make_project(self.project_dir, glossary="")
        result = write_glossary(
            self.project_dir,
            "项目GPT字典.txt",
            "upsert",
            [{"src": "词", "dst": "译"}, {"src": "词", "dst": "译"}],
        )
        self.assertEqual((result["added"], result["updated"], result["unchanged"]), (1, 0, 1))
        self.assertEqual(self._read_glossary_text(), f"{_GLOSSARY_NEW_FILE_HEADER}\n词|译\n")

    def test_delete_removes_lines_and_reports_not_found(self) -> None:
        _make_project(self.project_dir, glossary="//注释\n词|译\n别的|保持\n")
        result = write_glossary(
            self.project_dir,
            "项目GPT字典.txt",
            "delete",
            [{"src": "词"}, {"src": "缺词"}],
        )
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["not_found"], ["缺词"])
        self.assertEqual(self._read_glossary_text(), "//注释\n别的|保持\n")

    def test_delete_without_any_match_does_not_rewrite(self) -> None:
        _make_project(self.project_dir)
        before = self._read_glossary_text()
        result = write_glossary(self.project_dir, "项目GPT字典.txt", "delete", [{"src": "缺词"}])
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(self._read_glossary_text(), before)

    def test_pipe_escape_roundtrip_via_read(self) -> None:
        _make_project(self.project_dir, glossary="")
        write_glossary(
            self.project_dir,
            "项目GPT字典.txt",
            "upsert",
            [{"src": "re:あ|い", "dst": "啊|呀", "note": "选择符"}],
        )
        self.assertIn("re:あ\\|い|啊\\|呀|选择符\n", self._read_glossary_text())
        entries = read_glossary(self.project_dir)["entries"]
        self.assertEqual(
            [(e["src"], e["dst"], e["note"]) for e in entries],
            [("re:あ|い", "啊|呀", "选择符")],
        )

    def test_crlf_and_lf_preserved(self) -> None:
        _make_project(self.project_dir)
        with open(self._glossary_path(), "w", encoding="utf-8", newline="") as f:
            f.write("//注释\r\n大家さん|房东\r\n")
        write_glossary(self.project_dir, "项目GPT字典.txt", "upsert", [{"src": "新词", "dst": "新译"}])
        self.assertEqual(self._read_glossary_text(), "//注释\r\n大家さん|房东\r\n新词|新译\r\n")

        with open(self._glossary_path(), "w", encoding="utf-8", newline="") as f:
            f.write("大家さん|房东\n")
        write_glossary(self.project_dir, "项目GPT字典.txt", "upsert", [{"src": "新词2", "dst": "新译2"}])
        self.assertEqual(self._read_glossary_text(), "大家さん|房东\n新词2|新译2\n")

    def test_missing_trailing_newline_completed_on_first_change(self) -> None:
        _make_project(self.project_dir, glossary="旧词|旧译")
        write_glossary(self.project_dir, "项目GPT字典.txt", "upsert", [{"src": "新词", "dst": "新译"}])
        self.assertEqual(self._read_glossary_text(), "旧词|旧译\n新词|新译\n")

    def test_existing_empty_file_gets_header(self) -> None:
        _make_project(self.project_dir, extra_gpt_files=["项目术语表_空.txt"])
        open(self._glossary_path("项目术语表_空.txt"), "w", encoding="utf-8", newline="").close()
        result = write_glossary(
            self.project_dir,
            "项目术语表_空.txt",
            "upsert",
            [{"src": "术语", "dst": "Term"}],
        )
        self.assertFalse(result["registered_in_config"])
        self.assertEqual(
            self._read_glossary_text("项目术语表_空.txt"),
            f"{_GLOSSARY_NEW_FILE_HEADER}\n术语|Term\n",
        )

    def test_new_file_created_with_comment_header_and_registered(self) -> None:
        _make_project(self.project_dir)
        result = write_glossary(
            self.project_dir,
            "项目新术语表.txt",
            "upsert",
            [{"src": "术语", "dst": "Term", "note": "注"}],
        )
        self.assertTrue(result["registered_in_config"])
        self.assertEqual(
            self._read_glossary_text("项目新术语表.txt"),
            f"{_GLOSSARY_NEW_FILE_HEADER}\n术语|Term|注\n",
        )
        self.assertIn("(project_dir)项目新术语表.txt", self._config_text())
        # 新文件同样可被读工具读到（已在 gpt.dict 中登记）
        self.assertIn("项目新术语表.txt", read_glossary(self.project_dir)["files"])

    def test_unregistered_delete_is_rejected(self) -> None:
        _make_project(self.project_dir)
        with self.assertRaises(ValueError):
            write_glossary(self.project_dir, "项目新术语表.txt", "delete", [{"src": "词"}])
        self.assertFalse(os.path.isfile(self._glossary_path("项目新术语表.txt")))

    def test_wrong_category_file_is_rejected(self) -> None:
        _make_project(self.project_dir)
        with self.assertRaises(ValueError) as ctx:
            write_glossary(self.project_dir, "项目字典_译前.txt", "upsert", [{"src": "词", "dst": "译"}])
        self.assertIn("译前字典", str(ctx.exception))

    def test_field_and_param_validation(self) -> None:
        _make_project(self.project_dir)
        cases = [
            ("entries-empty", "upsert", []),
            ("entries-not-list", "upsert", {"src": "词"}),
            ("item-not-dict", "upsert", ["词"]),
            ("src-empty", "upsert", [{"src": "  ", "dst": "译"}]),
            ("src-not-str", "upsert", [{"src": 1, "dst": "译"}]),
            ("dst-missing", "upsert", [{"src": "词"}]),
            ("dst-empty", "upsert", [{"src": "词", "dst": ""}]),
            ("note-not-str", "upsert", [{"src": "词", "dst": "译", "note": 1}]),
            ("tab-in-field", "upsert", [{"src": "词", "dst": "译\t注"}]),
            ("newline-in-field", "upsert", [{"src": "词", "dst": "译\n注"}]),
            ("comment-prefix", "upsert", [{"src": "//词", "dst": "译"}]),
            ("separator-src", "upsert", [{"src": "===", "dst": "译"}]),
            ("escape-drift", "upsert", [{"src": "词", "dst": "译" + chr(92) + "n"}]),
            ("backslash-eol-src", "upsert", [{"src": "词" + chr(92), "dst": "译"}]),
            ("four-spaces-src", "upsert", [{"src": "词" + " " * 4 + "语", "dst": "译"}]),
            ("four-spaces-dst", "upsert", [{"src": "词", "dst": "译" + " " * 4 + "文"}]),
            ("line-sep-char", "upsert", [{"src": "词", "dst": "译", "note": "注" + chr(0x2028)}]),
        ]
        for label, mode, entries in cases:
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    write_glossary(self.project_dir, "项目GPT字典.txt", mode, entries)
        with self.assertRaises(ValueError):
            write_glossary(self.project_dir, "项目GPT字典.txt", "replace", [{"src": "词", "dst": "译"}])
        # delete 的 src 同样要能作为匹配键被原样读回
        with self.assertRaises(ValueError):
            write_glossary(self.project_dir, "项目GPT字典.txt", "delete", [{"src": "词" + chr(92)}])
        with self.assertRaises(ValueError):
            write_glossary(self.project_dir, "", "upsert", [{"src": "词", "dst": "译"}])
        with self.assertRaises(ValueError):
            write_glossary(self.project_dir, "../evil.txt", "upsert", [{"src": "词", "dst": "译"}])
        with self.assertRaises(ValueError):
            write_glossary(self.project_dir, "a:b.txt", "upsert", [{"src": "词", "dst": "译"}])
        with self.assertRaises(ValueError):
            write_glossary(self.project_dir, "con.txt", "upsert", [{"src": "词", "dst": "译"}])
        # 全部拒绝路径都不得留下 .tmp 或改写文件
        self.assertFalse(os.path.isfile(self._glossary_path() + ".tmp"))
        self.assertEqual(self._read_glossary_text(), "//格式说明\n\n大家さん|房东\n")

    def test_also_found_in_reports_other_glossary_files(self) -> None:
        _make_project(self.project_dir, extra_gpt_files=["项目术语表_补.txt"])
        with open(self._glossary_path("项目术语表_补.txt"), "w", encoding="utf-8", newline="") as f:
            f.write("大家さん|房东先生\n")
        result = write_glossary(
            self.project_dir,
            "项目GPT字典.txt",
            "upsert",
            [{"src": "大家さん", "dst": "房东先生"}],
        )
        self.assertEqual(
            result["also_found_in"],
            [{"file": "项目术语表_补.txt", "line_no": 1, "dst": "房东先生"}],
        )

    def test_h_gate_rejects_and_leaves_no_side_effects(self) -> None:
        _make_project(self.project_dir, h_words=["攀上"])
        before = self._read_glossary_text()
        with self.assertRaises(ValueError) as ctx:
            write_glossary(self.project_dir, "项目GPT字典.txt", "upsert", [{"src": "攀上", "dst": "爬上"}])
        self.assertEqual(str(ctx.exception), _H_DENY_MESSAGE)
        self.assertEqual(self._read_glossary_text(), before)
        # 未登记文件被门禁拒绝时：不得创建文件、不得登记进 config
        with self.assertRaises(ValueError):
            write_glossary(self.project_dir, "项目新术语表.txt", "upsert", [{"src": "攀上", "dst": "爬上"}])
        self.assertFalse(os.path.isfile(self._glossary_path("项目新术语表.txt")))
        self.assertNotIn("项目新术语表.txt", self._config_text())

    def test_gate_off_allows_h_terms(self) -> None:
        _make_project(self.project_dir, h_words=["攀上"])
        with mock.patch(
            "GalTransl.mcp_tools.load_app_settings",
            return_value={"mcpHGateEnabled": False, "mcpDisabledTools": []},
        ):
            result = write_glossary(self.project_dir, "项目GPT字典.txt", "upsert", [{"src": "攀上", "dst": "爬上"}])
        self.assertEqual(result["added"], 1)

    def test_bare_directory_rejected_by_l3(self) -> None:
        bare = tempfile.mkdtemp(prefix="gt_gloss_bare_")
        try:
            with self.assertRaises(ValueError):
                write_glossary(bare, "项目GPT字典.txt", "upsert", [{"src": "词", "dst": "译"}])
        finally:
            shutil.rmtree(bare, ignore_errors=True)

    def test_dispatch_roundtrip(self) -> None:
        _make_project(self.project_dir, glossary="")
        written = call_mcp_tool(
            "galtransl_write_glossary",
            {
                "project_dir": self.project_dir,
                "file": "项目GPT字典.txt",
                "entries": [{"src": "术语", "dst": "Term"}],
            },
        )
        self.assertEqual(written["added"], 1)
        read = call_mcp_tool("galtransl_read_glossary", {"project_dir": self.project_dir})
        self.assertEqual([e["src"] for e in read["entries"]], ["术语"])


class GlossaryToolDefinitionTests(unittest.TestCase):
    def _find(self, name: str):
        from GalTransl.mcp_tools import MCP_TOOL_DEFS

        return next(item for item in MCP_TOOL_DEFS if item["name"] == name)

    def test_kinds_and_annotations(self) -> None:
        read_def = self._find("galtransl_read_glossary")
        write_def = self._find("galtransl_write_glossary")
        self.assertEqual(read_def["kind"], "read")
        self.assertTrue(tool_annotations(read_def)["read_only_hint"])
        self.assertEqual(write_def["kind"], "write")
        self.assertEqual(tool_annotations(write_def), {})

    def test_schemas(self) -> None:
        read_def = self._find("galtransl_read_glossary")
        write_def = self._find("galtransl_write_glossary")
        self.assertEqual(read_def["input_schema"]["required"], ["project_dir"])
        self.assertEqual(write_def["input_schema"]["required"], ["project_dir", "file", "entries"])
        self.assertEqual(write_def["input_schema"]["properties"]["mode"]["enum"], ["upsert", "delete"])


if __name__ == "__main__":
    unittest.main()
