"""已知项目登记表单测与端点集成测试（GalTransl/known_projects.py）。

覆盖：
- 模块行为：登记/读取往返、config 名探测、60s 节流（窗口内不落盘、过窗刷新）、
  upsert 置顶去重、目录缺失跳过、损坏文件容错、上限裁剪、写失败静默；
- 端点接入：/api/projects/init 创建项目、项目级 API 命中（真实 HTTP server）都会登记；
- MCP 侧：项目名解析优先查登记表（项目不在工作区根下也能解析）、失效登记过滤、
  同名多候选报错、galtransl_list_projects 合并两个来源（source 字段 + 按目录去重）。
"""
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from GalTransl import known_projects
from GalTransl import server as _server_mod
from GalTransl.known_projects import load_known_projects, record_known_project
from GalTransl.mcp_tools import call_mcp_tool


def _make_project(path: str) -> None:
    """最小可识别项目：仅一个 config.yaml。"""
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "config.yaml"), "w", encoding="utf-8") as f:
        f.write("common: {}\n")


class KnownProjectsModuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gt_knownproj_")
        self.path = os.path.join(self.tmp, "known_projects.json")
        env_patch = mock.patch.dict(
            os.environ, {"GALTRANSL_KNOWN_PROJECTS_PATH": self.path}, clear=False
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        known_projects._LAST_RECORDED.clear()
        self.addCleanup(known_projects._LAST_RECORDED.clear)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_and_load_roundtrip(self) -> None:
        pdir = os.path.join(self.tmp, "projA")
        _make_project(pdir)
        self.assertTrue(record_known_project(pdir, "config.yaml"))
        projects = load_known_projects()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["name"], "projA")
        self.assertEqual(projects[0]["project_dir"], os.path.normpath(pdir))
        self.assertEqual(projects[0]["config_file_name"], "config.yaml")
        self.assertTrue(projects[0]["last_seen"])

    def test_record_infers_config_file_name(self) -> None:
        pdir = os.path.join(self.tmp, "projB")
        _make_project(pdir)
        record_known_project(pdir)
        self.assertEqual(load_known_projects()[0]["config_file_name"], "config.yaml")

    def test_throttled_repeat_record_does_not_rewrite(self) -> None:
        pdir = os.path.join(self.tmp, "projC")
        _make_project(pdir)
        self.assertTrue(record_known_project(pdir))
        # 节流窗口内：不产生 IO（返回 False，且 last_seen 不变）
        first_seen = load_known_projects()[0]["last_seen"]
        self.assertFalse(record_known_project(pdir))
        self.assertEqual(load_known_projects()[0]["last_seen"], first_seen)
        # 时钟推进越过窗口后：再次落盘刷新（_now_iso_utc 一并打桩到未来时刻，
        # 规避真实时钟秒级精度下 last_seen 相同导致断言偶发失败）
        clock = _FakeClock(_now_monotonic() + known_projects._RECORD_THROTTLE_SECONDS + 1)
        with mock.patch.object(known_projects, "time", clock), mock.patch.object(
            known_projects, "_now_iso_utc", return_value="2027-01-01T00:00:00Z"
        ):
            self.assertTrue(record_known_project(pdir))
        self.assertEqual(load_known_projects()[0]["last_seen"], "2027-01-01T00:00:00Z")

    def test_upsert_moves_to_front_and_dedups(self) -> None:
        clock = _FakeClock(_now_monotonic())
        with mock.patch.object(known_projects, "time", clock):
            for name in ("p1", "p2"):
                _make_project(os.path.join(self.tmp, name))
                record_known_project(os.path.join(self.tmp, name))
                clock.now += 1
            # 推进时钟越过节流窗口后 p1 再登记：置顶且不产生重复条目
            clock.now += known_projects._RECORD_THROTTLE_SECONDS + 1
            record_known_project(os.path.join(self.tmp, "p1"))
        names = [p["name"] for p in load_known_projects()]
        self.assertEqual(names, ["p1", "p2"])

    def test_missing_dir_not_recorded(self) -> None:
        self.assertFalse(record_known_project(os.path.join(self.tmp, "不存在")))
        self.assertEqual(load_known_projects(), [])

    def test_corrupt_or_non_dict_file_returns_empty(self) -> None:
        for broken in ("{broken", '["not", "dict"]'):
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(broken)
            self.assertEqual(load_known_projects(), [])
        self.assertFalse(os.path.isfile(self.path + ".tmp"))

    def test_max_kept_trims_to_limit(self) -> None:
        projects = [
            {
                "project_dir": os.path.join(self.tmp, f"p{i:03d}"),
                "name": f"p{i:03d}",
                "config_file_name": "config.yaml",
                "last_seen": f"2026-01-01T00:00:{i:02d}Z" if i < 60 else f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z",
            }
            for i in range(known_projects._MAX_KEPT_PROJECTS + 10)
        ]
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "projects": projects}, f, ensure_ascii=False)
        new_proj = os.path.join(self.tmp, "newproj")
        _make_project(new_proj)
        self.assertTrue(record_known_project(new_proj))
        loaded = load_known_projects()
        self.assertEqual(len(loaded), known_projects._MAX_KEPT_PROJECTS)
        self.assertEqual(loaded[0]["name"], "newproj")

    def test_write_failure_is_silent(self) -> None:
        # 登记文件路径是一个目录：写入失败但不得抛出（登记对象须是带 config 的真实项目，
        # 否则会在 config 存在性确认处提前返回，走不到写段）
        proj = os.path.join(self.tmp, "projX")
        _make_project(proj)
        dirpath = os.path.join(self.tmp, "as_dir")
        os.makedirs(dirpath)
        with mock.patch.object(
            known_projects, "known_projects_path", return_value=dirpath
        ):
            self.assertFalse(record_known_project(proj))

    def test_non_project_dir_not_recorded(self) -> None:
        # 目录存在但没有 config 文件：跳过登记（占名额但不产生无效条目）
        bare = os.path.join(self.tmp, "空目录")
        os.makedirs(bare)
        self.assertFalse(record_known_project(bare))
        self.assertEqual(load_known_projects(), [])

    def test_concurrent_records_both_persist(self) -> None:
        # 两线程同时登记不同目录（各自不节流）：写段持锁后两条都必须落盘、文件不损坏
        # （P1-1 回归：锁外读写改写时后到者会丢条目/共享 .tmp 互相截断）
        dirs = []
        for name in ("并发A", "并发B"):
            p = os.path.join(self.tmp, name)
            _make_project(p)
            dirs.append(p)
        barrier = threading.Barrier(2)
        results: list = []

        def _record(path: str) -> None:
            barrier.wait(timeout=10)
            results.append(record_known_project(path))

        threads = [
            threading.Thread(target=_record, args=(p,), name=f"record-{i}")
            for i, p in enumerate(dirs)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(sorted(results), [True, True])
        names = {p["name"] for p in load_known_projects()}
        self.assertEqual(names, {"并发A", "并发B"})


class _FakeClock:
    """known_projects.time 的替身：可编程 monotonic 时钟。"""

    def __init__(self, start: float) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now


def _now_monotonic() -> float:
    import time

    return time.monotonic()


class KnownProjectsEndpointTests(unittest.TestCase):
    """真实 HTTP server：init 创建与项目级 API 命中都登记进登记表。

    登记文件路径本类自管（setUpClass 设置、tearDownClass 恢复），摆脱对
    tests/conftest.py autouse 夹具的隐式依赖——`python -m unittest` 直跑同样隔离。
    pytest 下夹具会按测试覆盖该变量，同样隔离，互不冲突。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp(prefix="gt_knownproj_srv_")
        cls.registry_path = os.path.join(cls.tmp, "known_projects.json")
        cls._saved_registry_path = os.environ.get("GALTRANSL_KNOWN_PROJECTS_PATH")
        os.environ["GALTRANSL_WORKSPACE_ROOT"] = cls.tmp
        os.environ["GALTRANSL_KNOWN_PROJECTS_PATH"] = cls.registry_path
        os.environ.pop("GALTRANSL_API_TOKEN", None)
        known_projects._LAST_RECORDED.clear()
        cls.registry = _server_mod.JobRegistry()
        cls.server = _server_mod.ThreadingHTTPServer(
            ("127.0.0.1", 0), _server_mod.build_handler(cls.registry)
        )
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        os.environ.pop("GALTRANSL_WORKSPACE_ROOT", None)
        if cls._saved_registry_path is None:
            os.environ.pop("GALTRANSL_KNOWN_PROJECTS_PATH", None)
        else:
            os.environ["GALTRANSL_KNOWN_PROJECTS_PATH"] = cls._saved_registry_path
        known_projects._LAST_RECORDED.clear()

    def _req(self, method: str, path: str, body=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode() or "{}")

    def test_init_and_project_api_register_project(self) -> None:
        status, payload = self._req("POST", "/api/projects/init", body={"name": "登记项目"})
        self.assertEqual(status, 201)
        project_dir = payload["project_dir"]
        names = {p["name"] for p in load_known_projects()}
        self.assertIn("登记项目", names)

        # 项目级 API 命中同样登记（config-name 端点最轻）
        status, payload = self._req(
            "GET", f"/api/projects/{payload['project_id']}/config-name"
        )
        self.assertEqual(status, 200)
        entry = next(p for p in load_known_projects() if p["name"] == "登记项目")
        self.assertEqual(os.path.normpath(entry["project_dir"]), os.path.normpath(project_dir))

    def test_unknown_project_id_not_registered(self) -> None:
        before = len(load_known_projects())
        status, _ = self._req("GET", "/api/projects/!!!!!/config-name")
        self.assertEqual(status, 400)
        self.assertEqual(len(load_known_projects()), before)


class McpNameResolutionWithRegistryTests(unittest.TestCase):
    """MCP project_dir 名称解析：登记表优先、失效过滤、歧义报错、list_projects 合并。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gt_mcp_known_")
        self.registry_path = os.path.join(self.tmp, "known_projects.json")
        self.outside_dir = os.path.join(self.tmp, "外部项目")
        _make_project(self.outside_dir)
        # 登记表里有「外部项目」（不在工作区根下）与一条已失效登记（目录不存在）
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 1,
                    "projects": [
                        {
                            "project_dir": self.outside_dir,
                            "name": "外部项目",
                            "config_file_name": "config.yaml",
                            "last_seen": "2026-01-02T00:00:00Z",
                        },
                        {
                            "project_dir": os.path.join(self.tmp, "已删除"),
                            "name": "已删除",
                            "config_file_name": "config.yaml",
                            "last_seen": "2026-01-03T00:00:00Z",
                        },
                    ],
                },
                f,
                ensure_ascii=False,
            )
        self.env = {
            **os.environ,
            "GALTRANSL_KNOWN_PROJECTS_PATH": self.registry_path,
            "GALTRANSL_WORKSPACE_ROOT": self.tmp,  # 工作区根本身没有项目目录
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_registry_name_resolves_outside_workspace_root(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            result = call_mcp_tool(
                "galtransl_get_project_overview", {"project_dir": "外部项目"}
            )
        self.assertEqual(os.path.normpath(result["project_dir"]), os.path.normpath(self.outside_dir))

    def test_stale_registry_entry_is_filtered(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            with self.assertRaises(ValueError) as ctx:
                call_mcp_tool("galtransl_get_project_overview", {"project_dir": "已删除"})
        self.assertIn("未找到名为", str(ctx.exception))

    def test_ambiguous_loose_match_lists_candidates(self) -> None:
        fullwidth = os.path.join(self.tmp, "ＭＹＰＲＯＪ")
        _make_project(fullwidth)
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 1,
                    "projects": [
                        {"project_dir": self.outside_dir, "name": "外部项目", "config_file_name": "config.yaml", "last_seen": "2026-01-02T00:00:00Z"},
                        {"project_dir": fullwidth, "name": "ＭＹＰＲＯＪ", "config_file_name": "config.yaml", "last_seen": "2026-01-01T00:00:00Z"},
                    ],
                },
                f,
                ensure_ascii=False,
            )
        # NFKC 容错命中唯一 → 解析成功
        with mock.patch.dict(os.environ, self.env, clear=False):
            result = call_mcp_tool("galtransl_get_project_overview", {"project_dir": "myproj"})
        self.assertEqual(os.path.normpath(result["project_dir"]), os.path.normpath(fullwidth))
        # 制造第二个同名容错候选 → 报错列出绝对路径
        second = os.path.join(self.tmp, "ｍｙｐｒｏｊ")
        _make_project(second)
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 1,
                    "projects": [
                        {"project_dir": fullwidth, "name": "ＭＹＰＲＯＪ", "config_file_name": "config.yaml", "last_seen": "2026-01-02T00:00:00Z"},
                        {"project_dir": second, "name": "ｍｙｐｒｏｊ", "config_file_name": "config.yaml", "last_seen": "2026-01-01T00:00:00Z"},
                    ],
                },
                f,
                ensure_ascii=False,
            )
        with mock.patch.dict(os.environ, self.env, clear=False):
            with self.assertRaises(ValueError) as ctx:
                call_mcp_tool("galtransl_get_project_overview", {"project_dir": "myproj"})
        message = str(ctx.exception)
        self.assertIn("同名项目", message)
        self.assertIn(fullwidth, message)

    def test_list_projects_merges_registered_and_workspace(self) -> None:
        ws_project = os.path.join(self.tmp, "ws项目")
        _make_project(ws_project)
        # 工作区根直接放一个项目目录会被登记表扫描扫到吗——不会，工作区根=self.tmp，
        # 登记表里只有「外部项目」；工作区扫描会找到 ws项目（tmp 顶层）与 外部项目。
        with mock.patch.dict(os.environ, self.env, clear=False):
            result = call_mcp_tool("galtransl_list_projects", {})
        by_name = {p["name"]: p for p in result["projects"]}
        self.assertEqual(by_name["外部项目"]["source"], "registered")
        self.assertEqual(by_name["ws项目"]["source"], "workspace")
        self.assertNotIn("已删除", by_name)
        self.assertEqual(result["total"], len(result["projects"]))

    def test_registry_name_wins_over_same_name_workspace_dir(self) -> None:
        # 登记表与工作区根下存在同名但不同目录的项目：解析优先取登记表（最近使用）条目
        ws_same_name = os.path.join(self.tmp, "wsroot", "外部项目")
        _make_project(ws_same_name)
        env = {**self.env, "GALTRANSL_WORKSPACE_ROOT": os.path.join(self.tmp, "wsroot")}
        with mock.patch.dict(os.environ, env, clear=False):
            result = call_mcp_tool(
                "galtransl_get_project_overview", {"project_dir": "外部项目"}
            )
        self.assertEqual(os.path.normpath(result["project_dir"]), os.path.normpath(self.outside_dir))

    def test_workspace_entry_dedupes_registered_same_dir(self) -> None:
        # 工作区根下的项目同时被登记 → 只保留 registered 一条
        ws_project = os.path.join(self.tmp, "ws项目")
        _make_project(ws_project)
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 1,
                    "projects": [
                        {"project_dir": ws_project, "name": "ws项目", "config_file_name": "config.yaml", "last_seen": "2026-01-02T00:00:00Z"},
                    ],
                },
                f,
                ensure_ascii=False,
            )
        with mock.patch.dict(os.environ, self.env, clear=False):
            result = call_mcp_tool("galtransl_list_projects", {})
        ws_entries = [p for p in result["projects"] if p["name"] == "ws项目"]
        self.assertEqual(len(ws_entries), 1)
        self.assertEqual(ws_entries[0]["source"], "registered")


if __name__ == "__main__":
    unittest.main()
