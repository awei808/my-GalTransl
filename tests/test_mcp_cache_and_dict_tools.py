"""MCP 缓存条目写工具与项目字典类别扩展单测（0.6.x 缓存条目批次）。

覆盖四条主线：
1. project_dir 项目名解析：工作区根下按名称解析（含 NFKC/大小写容错）、未知名称报可用清单、
   绝对路径行为不变、只读工具不因名称误报 project_dir_valid；
2. galtransl_mark_skip_check：标记清除 problem、取消恢复、幂等 no-op、元数据文件拒绝、
   index 兜底（数字字符串/越界）、翻译任务运行中拒绝、后端不可达放行并注明；
3. galtransl_swap_alt_translation：pre_dst/alt_dst 交换、无备选拒绝；
4. write_glossary / read_glossary 的 category=gpt/pre/post：译前/译后字典 upsert+自动登记、
   条件行保留、mono/diag 保留字拒绝、跨类别文件拒绝、H 门禁同样生效、read 返回 row_type。
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from GalTransl.mcp_tools import (
    _H_DENY_MESSAGE,
    call_mcp_tool,
    mark_skip_check,
    read_glossary,
    swap_alt_translation,
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


def _make_project(root: str, h_words: list | None = None) -> None:
    """构造最小可识别项目：config.inc.yaml + 项目禁用词（与 test_mcp_write_tools 同口径）。"""
    os.makedirs(os.path.join(root, "transl_cache"), exist_ok=True)
    with open(os.path.join(root, "项目禁用词_非h.txt"), "w", encoding="utf-8") as f:
        f.write("// 非 h 禁用词\n")
    with open(os.path.join(root, "项目禁用词_h.txt"), "w", encoding="utf-8") as f:
        f.write("// 项目 H 禁用词\n")
        for word in h_words or []:
            f.write(f"{word}|测试用\n")
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


def _write_cache(project_dir: str, entries: list, rel: str = "pass3_cache/01.json") -> str:
    path = os.path.join(project_dir, "transl_cache", *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    return rel


def _read_cache(project_dir: str, rel: str = "pass3_cache/01.json") -> list:
    path = os.path.join(project_dir, "transl_cache", *rel.split("/"))
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _no_jobs(_path: str) -> dict:
    return {"jobs": []}


_ENTRIES = [
    {
        "index": 1,
        "name": "alice",
        "pre_src": "こんにちは",
        "post_src": "こんにちは",
        "pre_dst": "你好",
        "problem": "残留日文",
    },
    {
        "index": 2,
        "name": "alice",
        "pre_src": "さようなら",
        "post_src": "さようなら",
        "pre_dst": "再见",
        "alt_dst": "再会了",
    },
]


class ProjectNameResolutionTests(unittest.TestCase):
    """project_dir 项目名解析：工作区根扫描、容错匹配与错误提示。"""

    def setUp(self) -> None:
        self.ws_root = tempfile.mkdtemp(prefix="gt_ws_")
        self.project_dir = os.path.join(self.ws_root, "我的项目")
        _make_project(self.project_dir)
        # 第二个项目：全角名，用于 NFKC/大小写容错匹配
        self.fullwidth_dir = os.path.join(self.ws_root, "ＴｅｓｔＰｒｏｊ")
        _make_project(self.fullwidth_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.ws_root, ignore_errors=True)

    def _env(self) -> dict:
        # 登记表重定向到工作区内空路径：本组测试专测工作区根扫描回退，
        # 与真实程序目录的 known_projects.json 隔离（登记表优先路径另测）
        return {
            **os.environ,
            "GALTRANSL_WORKSPACE_ROOT": self.ws_root,
            "GALTRANSL_KNOWN_PROJECTS_PATH": os.path.join(self.ws_root, "known_projects.json"),
        }

    def test_read_tool_resolves_project_name(self) -> None:
        rel = _write_cache(self.project_dir, _ENTRIES)
        with mock.patch.dict(os.environ, self._env(), clear=False):
            result = call_mcp_tool(
                "galtransl_read_translation_file",
                {"project_dir": "我的项目", "filename": rel},
            )
        self.assertEqual(result["total"], len(_ENTRIES))
        self.assertEqual(os.path.normpath(result["project_dir"]), os.path.normpath(self.project_dir))
        # 名称不是目录，_attach_project_dir_warning 不应据此误报
        self.assertNotIn("project_dir_valid", result)

    def test_name_matching_is_nfkc_and_casefold_tolerant(self) -> None:
        rel = _write_cache(self.fullwidth_dir, _ENTRIES)
        with mock.patch.dict(os.environ, self._env(), clear=False):
            result = call_mcp_tool(
                "galtransl_get_project_overview",
                {"project_dir": "testproj"},
            )
        self.assertEqual(os.path.normpath(result["project_dir"]), os.path.normpath(self.fullwidth_dir))
        self.assertIn(rel, result["cache_file_names"])

    def test_unknown_name_lists_available_projects(self) -> None:
        with mock.patch.dict(os.environ, self._env(), clear=False):
            with self.assertRaises(ValueError) as ctx:
                call_mcp_tool("galtransl_get_project_overview", {"project_dir": "不存在的项目"})
        message = str(ctx.exception)
        self.assertIn("未找到名为", message)
        self.assertIn("我的项目", message)
        self.assertIn("ＴｅｓｔＰｒｏｊ", message)

    def test_absolute_path_still_works(self) -> None:
        rel = _write_cache(self.project_dir, _ENTRIES)
        with mock.patch.dict(os.environ, self._env(), clear=False):
            result = call_mcp_tool(
                "galtransl_read_translation_file",
                {"project_dir": self.project_dir, "filename": rel},
            )
        self.assertEqual(result["total"], len(_ENTRIES))

    def test_list_projects_reports_names(self) -> None:
        with mock.patch.dict(os.environ, self._env(), clear=False):
            result = call_mcp_tool("galtransl_list_projects", {})
        names = {p["name"] for p in result["projects"]}
        self.assertEqual(names, {"我的项目", "ＴｅｓｔＰｒｏｊ"})


class CacheWriteToolTestsBase(unittest.TestCase):
    """缓存条目写工具共用夹具：项目 + 缓存文件 + 后端无任务。"""

    def setUp(self) -> None:
        self.project_dir = tempfile.mkdtemp(prefix="gt_cachetool_")
        _make_project(self.project_dir)
        self.rel = _write_cache(self.project_dir, _ENTRIES)

    def tearDown(self) -> None:
        shutil.rmtree(self.project_dir, ignore_errors=True)


class MarkSkipCheckTests(CacheWriteToolTestsBase):
    def test_mark_sets_flag_and_clears_problem(self) -> None:
        with mock.patch("GalTransl.mcp_backend_client.get_json", side_effect=_no_jobs):
            result = mark_skip_check(self.project_dir, self.rel, 1, True)
        self.assertTrue(result["success"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["problem_before"], "残留日文")
        self.assertEqual(result["problem_after"], "")
        self.assertEqual(result["job_check"], "ok")
        entry = _read_cache(self.project_dir)[0]
        self.assertTrue(entry.get("skip_check"))
        self.assertNotIn("problem", entry)
        # 写前有快照
        snap_dir = os.path.join(self.project_dir, "transl_cache", ".snapshots", "pass3_cache")
        self.assertTrue(any(name.startswith("01.json.") for name in os.listdir(snap_dir)))

    def test_unmark_removes_flag(self) -> None:
        mark_skip_check(self.project_dir, self.rel, 1, True)
        result = mark_skip_check(self.project_dir, self.rel, 1, False)
        self.assertTrue(result["changed"])
        entry = _read_cache(self.project_dir)[0]
        self.assertNotIn("skip_check", entry)

    def test_mark_is_noop_when_already_marked(self) -> None:
        mark_skip_check(self.project_dir, self.rel, 1, True)
        before = _read_cache(self.project_dir)
        with mock.patch("GalTransl.mcp_backend_client.get_json", side_effect=_no_jobs):
            result = mark_skip_check(self.project_dir, self.rel, 1, True)
        self.assertFalse(result["changed"])
        self.assertIn("无变化", result["detail"])
        self.assertEqual(result["problem_after"], result["problem_before"])
        self.assertEqual(_read_cache(self.project_dir), before)

    def test_running_job_of_other_project_does_not_block(self) -> None:
        other_dir = os.path.join(tempfile.gettempdir(), "gt_other_proj_不在工作区")
        jobs = {"jobs": [{"project_dir": other_dir, "status": "running", "job_id": "j9", "translator": "x"}]}
        with mock.patch("GalTransl.mcp_backend_client.get_json", return_value=jobs):
            result = mark_skip_check(self.project_dir, self.rel, 1, True)
        self.assertTrue(result["changed"])

    def test_recently_cancelled_job_rejects_write(self) -> None:
        from datetime import datetime, timezone

        finished = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        jobs = {"jobs": [{"project_dir": self.project_dir, "status": "cancelled", "job_id": "j2", "translator": "x", "finished_at": finished}]}
        with mock.patch("GalTransl.mcp_backend_client.get_json", return_value=jobs):
            with self.assertRaises(ValueError) as ctx:
                mark_skip_check(self.project_dir, self.rel, 1, True)
        self.assertIn("合并增量缓存", str(ctx.exception))
        self.assertFalse(_read_cache(self.project_dir)[0].get("skip_check"))

    def test_long_finished_job_does_not_block(self) -> None:
        from datetime import datetime, timedelta, timezone

        finished = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        jobs = {"jobs": [{"project_dir": self.project_dir, "status": "cancelled", "job_id": "j2", "translator": "x", "finished_at": finished}]}
        with mock.patch("GalTransl.mcp_backend_client.get_json", return_value=jobs):
            result = mark_skip_check(self.project_dir, self.rel, 1, True)
        self.assertTrue(result["changed"])

    def test_meta_file_rejected(self) -> None:
        _write_cache(self.project_dir, [{"index": 1}], rel="pass1_cache/01.txt.json.meta.json")
        with self.assertRaises(ValueError) as ctx:
            mark_skip_check(self.project_dir, "pass1_cache/01.txt.json.meta.json", 1, True)
        self.assertIn("不是翻译缓存文件", str(ctx.exception))

    def test_index_accepts_numeric_string_and_reports_range(self) -> None:
        result = mark_skip_check(self.project_dir, self.rel, "2", True)
        self.assertEqual(result["index"], 2)
        self.assertTrue(_read_cache(self.project_dir)[1].get("skip_check"))
        with self.assertRaises(ValueError) as ctx:
            mark_skip_check(self.project_dir, self.rel, 99, True)
        self.assertIn("index 范围 1~2", str(ctx.exception))

    def test_running_job_rejects_write(self) -> None:
        jobs = {"jobs": [{"project_dir": self.project_dir, "status": "running", "job_id": "j1", "translator": "x"}]}
        with mock.patch("GalTransl.mcp_backend_client.get_json", return_value=jobs):
            with self.assertRaises(ValueError) as ctx:
                mark_skip_check(self.project_dir, self.rel, 1, True)
        self.assertIn("翻译任务进行中", str(ctx.exception))
        # 拒绝时不得落盘
        self.assertFalse(_read_cache(self.project_dir)[0].get("skip_check"))

    def test_backend_unreachable_proceeds_with_note(self) -> None:
        with mock.patch(
            "GalTransl.mcp_backend_client.get_json", side_effect=RuntimeError("无法连接 GalTransl 后端")
        ):
            result = mark_skip_check(self.project_dir, self.rel, 1, True)
        self.assertTrue(result["changed"])
        self.assertTrue(result["job_check"].startswith("backend_unreachable"))

    def test_call_mcp_tool_dispatches(self) -> None:
        with mock.patch("GalTransl.mcp_backend_client.get_json", side_effect=_no_jobs):
            result = call_mcp_tool(
                "galtransl_mark_skip_check",
                {"project_dir": self.project_dir, "filename": self.rel, "index": 1},
            )
        self.assertTrue(result["success"])


class SwapAltTranslationTests(CacheWriteToolTestsBase):
    def test_swap_exchanges_and_rechecks(self) -> None:
        # 给条目 2 一个假 problem：交换后按新译文重检应将其清除，证明重检确实生效
        _write_cache(
            self.project_dir,
            [{**_ENTRIES[0]}, {**_ENTRIES[1], "problem": "残留日文"}],
        )
        with mock.patch("GalTransl.mcp_backend_client.get_json", side_effect=_no_jobs):
            result = swap_alt_translation(self.project_dir, self.rel, 2)
        self.assertTrue(result["success"])
        entries = _read_cache(self.project_dir)
        self.assertEqual(entries[1]["pre_dst"], "再会了")
        self.assertEqual(entries[1]["alt_dst"], "再见")
        self.assertEqual(result["problem_before"], "残留日文")
        self.assertEqual(result["problem_after"], "")
        self.assertEqual(result["recheck_note"], "")
        self.assertTrue(result["changed"])
        # 交换属于译文变化，审计快照应存在
        snap_dir = os.path.join(self.project_dir, "transl_cache", ".snapshots", "pass3_cache")
        self.assertTrue(any(name.startswith("01.json.") for name in os.listdir(snap_dir)))

    def test_swap_with_pre_zh_only_entry(self) -> None:
        # 旧格式条目仅有 pre_zh：交换后 pre_dst 取得备选，alt_dst 落旧 pre_zh 值
        _write_cache(
            self.project_dir,
            [_ENTRIES[0], {**_ENTRIES[1], "pre_dst": "", "pre_zh": "再见"}],
        )
        with mock.patch("GalTransl.mcp_backend_client.get_json", side_effect=_no_jobs):
            swap_alt_translation(self.project_dir, self.rel, 2)
        entries = _read_cache(self.project_dir)
        self.assertEqual(entries[1]["pre_dst"], "再会了")
        self.assertEqual(entries[1]["alt_dst"], "再见")

    def test_no_alt_rejected(self) -> None:
        with mock.patch("GalTransl.mcp_backend_client.get_json", side_effect=_no_jobs):
            with self.assertRaises(ValueError) as ctx:
                swap_alt_translation(self.project_dir, self.rel, 1)
        self.assertIn("没有备选译文", str(ctx.exception))
        self.assertEqual(_read_cache(self.project_dir)[0]["pre_dst"], "你好")

    def test_missing_file_rejected(self) -> None:
        with mock.patch("GalTransl.mcp_backend_client.get_json", side_effect=_no_jobs):
            with self.assertRaises(ValueError) as ctx:
                swap_alt_translation(self.project_dir, "pass3_cache/不存在.json", 1)
        self.assertIn("文件不存在", str(ctx.exception))


class WriteGlossaryPrePostTests(unittest.TestCase):
    """write_glossary / read_glossary 的 pre/post 类别（增删项目字典）。"""

    def setUp(self) -> None:
        self.project_dir = tempfile.mkdtemp(prefix="gt_dictool_")
        _make_project(self.project_dir, h_words=["禁词"])

    def tearDown(self) -> None:
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def _config_text(self) -> str:
        with open(os.path.join(self.project_dir, "config.inc.yaml"), "r", encoding="utf-8") as f:
            return f.read()

    def _dict_text(self, name: str) -> str:
        with open(os.path.join(self.project_dir, name), "r", encoding="utf-8") as f:
            return f.read()

    def test_pre_upsert_creates_and_registers(self) -> None:
        result = write_glossary(
            self.project_dir,
            "项目译前字典.txt",
            "upsert",
            [{"src": "お腹すいた", "dst": "肚子饿了"}],
            category="pre",
        )
        self.assertTrue(result["registered_in_config"])
        self.assertEqual(result["category"], "pre")
        self.assertIn("(project_dir)项目译前字典.txt", self._config_text())
        self.assertIn("preDict", self._config_text())
        self.assertTrue(
            self._dict_text("项目译前字典.txt").startswith("//译前字典")
        )
        self.assertIn("お腹すいた|肚子饿了", self._dict_text("项目译前字典.txt"))

    def test_post_upsert_then_delete(self) -> None:
        write_glossary(
            self.project_dir,
            "项目译后字典.txt",
            "upsert",
            [{"src": "早饭", "dst": "早餐"}],
            category="post",
        )
        result = write_glossary(
            self.project_dir,
            "项目译后字典.txt",
            "delete",
            [{"src": "早饭", "dst": ""}],
            category="post",
        )
        self.assertEqual(result["deleted"], 1)
        self.assertNotIn("早饭|早餐", self._dict_text("项目译后字典.txt"))

    def test_conditional_lines_preserved_and_not_matched(self) -> None:
        # 先登记文件（delete 不做自动登记），再写入条件行
        write_glossary(
            self.project_dir,
            "项目译后字典.txt",
            "upsert",
            [{"src": "普通词", "dst": "替换词"}],
            category="post",
        )
        with open(os.path.join(self.project_dir, "项目译后字典.txt"), "w", encoding="utf-8") as f:
            f.write("pre_dst|さん$|山田|山下\n普通词|替换词\n")
        result = write_glossary(
            self.project_dir,
            "项目译后字典.txt",
            "delete",
            [{"src": "普通词", "dst": ""}],
            category="post",
        )
        self.assertEqual(result["deleted"], 1)
        text = self._dict_text("项目译后字典.txt")
        self.assertIn("pre_dst|さん$|山田|山下", text)
        self.assertNotIn("普通词|替换词", text)

    def test_situation_key_rejected_for_pre_post(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            write_glossary(
                self.project_dir,
                "项目译后字典.txt",
                "upsert",
                [{"src": "mono", "dst": "x", "note": "n"}],
                category="post",
            )
        self.assertIn("mono", str(ctx.exception))

    def test_cross_category_file_rejected(self) -> None:
        write_glossary(
            self.project_dir,
            "项目译前字典.txt",
            "upsert",
            [{"src": "a", "dst": "b"}],
            category="pre",
        )
        with self.assertRaises(ValueError) as ctx:
            write_glossary(
                self.project_dir,
                "项目译前字典.txt",
                "upsert",
                [{"src": "c", "dst": "d"}],
                category="gpt",
            )
        self.assertIn("译前字典", str(ctx.exception))

    def test_h_gate_applies_to_post_dict(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            write_glossary(
                self.project_dir,
                "项目译后字典.txt",
                "upsert",
                [{"src": "普通词", "dst": "含禁词的译文"}],
                category="post",
            )
        self.assertEqual(str(ctx.exception), _H_DENY_MESSAGE)
        self.assertFalse(os.path.isfile(os.path.join(self.project_dir, "项目译后字典.txt")))

    def test_read_glossary_pre_returns_row_types(self) -> None:
        # 先登记文件（read 只认 config 各清单中带 (project_dir) 的项目字典）
        write_glossary(
            self.project_dir,
            "项目译前字典.txt",
            "upsert",
            [{"src": "占位", "dst": "占位"}],
            category="pre",
        )
        with open(os.path.join(self.project_dir, "项目译前字典.txt"), "w", encoding="utf-8") as f:
            f.write("//注释\n\n查找词|替换词|备注\npre_src|さん$|山田|山下\n")
        result = read_glossary(self.project_dir, category="pre")
        self.assertEqual(result["category"], "pre")
        self.assertEqual(result["files"], ["项目译前字典.txt"])
        rows = {e["src"]: e for e in result["entries"]}
        self.assertEqual(rows["查找词"]["row_type"], "normal")
        self.assertEqual(rows["查找词"]["dst"], "替换词")
        self.assertEqual(rows["查找词"]["note"], "备注")
        self.assertEqual(rows["山田"]["row_type"], "conditional")
        self.assertEqual(rows["山田"]["dst"], "山下")
        self.assertEqual(rows["山田"]["target"], "pre_src")
        self.assertEqual(rows["山田"]["cond"], "さん$")
        self.assertNotIn("注释", rows)

    def test_read_glossary_category_mismatch_rejected(self) -> None:
        write_glossary(
            self.project_dir,
            "项目译前字典.txt",
            "upsert",
            [{"src": "a", "dst": "b"}],
            category="pre",
        )
        with self.assertRaises(ValueError) as ctx:
            read_glossary(self.project_dir, filename="项目译前字典.txt")
        self.assertIn("译前字典", str(ctx.exception))

    def test_unknown_category_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            read_glossary(self.project_dir, category="forbidden")
        self.assertIn("未知 category", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            write_glossary(
                self.project_dir, "x.txt", "upsert", [{"src": "a", "dst": "b"}], category="forbidden"
            )
        self.assertIn("未知 category", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
