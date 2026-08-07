"""C端日志：GET .../logs?source= 默认 engine 向后兼容 + POST /api/log 单一 handler。"""
import importlib
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from GalTransl import server as _server_mod
from GalTransl.server_runtime import encode_project_dir


def _start_server(workspace_root: str, token: str = ""):
    os.environ["GALTRANSL_WORKSPACE_ROOT"] = workspace_root
    if token:
        os.environ["GALTRANSL_API_TOKEN"] = token
    else:
        os.environ.pop("GALTRANSL_API_TOKEN", None)
    importlib.reload(_server_mod)
    registry = _server_mod.JobRegistry()
    srv = _server_mod.ThreadingHTTPServer(
        ("127.0.0.1", 0), _server_mod.build_handler(registry)
    )
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


class _Base(unittest.TestCase):
    # 产品默认 writeFrontendLog=False；此处显式开启以测试"开启路径"，
    # 与 GalTransl.AppSettings.DEFAULT_APP_SETTINGS 的新默认解耦。
    _settings_override = {"writeFrontendLog": True}

    @classmethod
    def setUpClass(cls) -> None:
        # patch 源头 GalTransl.AppSettings.load_app_settings：_start_server 内
        # 会 importlib.reload(server)，server 模块重新执行
        # `from .AppSettings import load_app_settings`，从而绑定到此处 patch 后的函数。
        cls._patch = patch(
            "GalTransl.AppSettings.load_app_settings",
            return_value=dict(cls._settings_override),
        )
        cls._patch.start()
        cls.tmp = tempfile.mkdtemp()
        cls.server, cls.port = _start_server(cls.tmp)
        cls.root = cls.tmp

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls._patch.stop()

    def _req(self, method, path, body=None, headers=None, raw=None, content_type=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        if raw is not None:
            data = raw
        elif body is not None:
            data = json.dumps(body).encode()
            content_type = content_type or "application/json"
        else:
            data = None
        req = urllib.request.Request(url, data=data, method=method)
        if content_type:
            req.add_header("Content-Type", content_type)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode() or "{}")

    def _init_project(self, name: str) -> tuple[int, dict]:
        return self._req("POST", "/api/projects/init", body={"name": name})


class LogsEndpointTests(_Base):
    def test_logs_default_source_is_engine(self) -> None:
        _, init = self._init_project("lp")
        pid = init["project_id"]
        status, body = self._req("GET", f"/api/projects/{pid}/logs")
        self.assertEqual(status, 200)
        self.assertEqual(body["source"], "engine")
        self.assertFalse(body["exists"])
        self.assertEqual(body["lines"], [])

    def test_logs_engine_reads_galtransl_log(self) -> None:
        _, init = self._init_project("lp2")
        pid = init["project_id"]
        pdir = init["project_dir"]
        with open(os.path.join(pdir, "GalTransl.log"), "w", encoding="utf-8") as f:
            f.write("line1\nline2\n")
        status, body = self._req("GET", f"/api/projects/{pid}/logs")
        self.assertEqual(status, 200)
        self.assertEqual(body["source"], "engine")
        self.assertTrue(body["exists"])
        self.assertEqual(body["lines"], ["line1", "line2"])

    def test_logs_invalid_source_400(self) -> None:
        _, init = self._init_project("lp3")
        pid = init["project_id"]
        status, _ = self._req("GET", f"/api/projects/{pid}/logs?source=bogus")
        self.assertEqual(status, 400)

    def test_log_receive_and_read_frontend(self) -> None:
        _, init = self._init_project("lp4")
        pid = init["project_id"]
        status, body = self._req("POST", "/api/log", body={"level": "error", "message": "boom"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        status, body = self._req("GET", f"/api/projects/{pid}/logs?source=frontend")
        self.assertEqual(status, 200)
        self.assertEqual(body["source"], "frontend")
        self.assertTrue(body["exists"])
        self.assertTrue(any("boom" in line for line in body["lines"]))

    def test_log_receive_accepts_lines_array(self) -> None:
        status, _ = self._req("POST", "/api/log", body={"lines": ["a", "b"], "level": "info"})
        self.assertEqual(status, 200)

    def test_log_frontend_file_appended(self) -> None:
        # 多次发送应追加而非覆盖
        self._req("POST", "/api/log", body={"message": "first"})
        self._req("POST", "/api/log", body={"message": "second"})
        frontend_log = os.path.join(self.root, "frontend.log")
        self.assertTrue(os.path.isfile(frontend_log))
        with open(frontend_log, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("first", text)
        self.assertIn("second", text)

    def test_log_injection_sanitized(self) -> None:
        # level 含换行/控制字符时不得伪造日志行
        self._req("POST", "/api/log", body={"level": "info]\n[2099-01-01] [error] [fake", "message": "x"})
        frontend_log = os.path.join(self.root, "frontend.log")
        with open(frontend_log, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("2099-01-01", text)  # 伪造日志行未注入
        self.assertNotIn("]\n[", text)        # 换行被剥离

    def test_log_level_whitelisted(self) -> None:
        # 非法 level 应回退为 info
        self._req("POST", "/api/log", body={"level": "HACK", "message": "m"})
        frontend_log = os.path.join(self.root, "frontend.log")
        with open(frontend_log, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("[info]", text)
        self.assertNotIn("[HACK]", text)

    def test_logs_tail_invalid_returns_400(self) -> None:
        _, init = self._init_project("lp_tail1")
        pid = init["project_id"]
        status, _ = self._req("GET", f"/api/projects/{pid}/logs?tail=abc")
        self.assertEqual(status, 400)

    def test_logs_tail_negative_returns_400(self) -> None:
        _, init = self._init_project("lp_tail2")
        pid = init["project_id"]
        status, _ = self._req("GET", f"/api/projects/{pid}/logs?tail=-3")
        self.assertEqual(status, 400)

    def test_logs_tail_zero_returns_empty(self) -> None:
        _, init = self._init_project("lp_tail3")
        pid = init["project_id"]
        pdir = init["project_dir"]
        with open(os.path.join(pdir, "GalTransl.log"), "w", encoding="utf-8") as f:
            f.write("line1\nline2\n")
        status, body = self._req("GET", f"/api/projects/{pid}/logs?tail=0")
        self.assertEqual(status, 200)
        self.assertEqual(body["lines"], [])

    def test_logs_tail_positive_limits(self) -> None:
        _, init = self._init_project("lp_tail4")
        pid = init["project_id"]
        pdir = init["project_dir"]
        with open(os.path.join(pdir, "GalTransl.log"), "w", encoding="utf-8") as f:
            f.write("line1\nline2\nline3\n")
        status, body = self._req("GET", f"/api/projects/{pid}/logs?tail=2")
        self.assertEqual(status, 200)
        self.assertEqual(body["lines"], ["line2", "line3"])

    def test_log_receive_with_project_id_writes_to_project_dir(self) -> None:
        # 携带合法 project_id 时 frontend.log 归集到翻译项目目录，而非 workspace 根
        _, init = self._init_project("lp_pid")
        pid = init["project_id"]
        pdir = init["project_dir"]
        status, body = self._req(
            "POST", "/api/log", body={"level": "info", "message": "inproject", "project_id": pid}
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        proj_fe = os.path.join(pdir, "frontend.log")
        self.assertTrue(os.path.isfile(proj_fe))
        with open(proj_fe, encoding="utf-8") as f:
            self.assertIn("inproject", f.read())
        # 同一消息不应落到 workspace 根的 frontend.log
        root_fe = os.path.join(self.root, "frontend.log")
        if os.path.isfile(root_fe):
            with open(root_fe, encoding="utf-8") as f:
                self.assertNotIn("inproject", f.read())

    def test_log_receive_invalid_project_id_falls_back(self) -> None:
        # 非法 project_id 不得抛出 500，回退到 workspace 根 frontend.log
        status, body = self._req(
            "POST", "/api/log", body={"message": "fb", "project_id": "!!!not-valid"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        root_fe = os.path.join(self.root, "frontend.log")
        self.assertTrue(os.path.isfile(root_fe))
        with open(root_fe, encoding="utf-8") as f:
            self.assertIn("fb", f.read())

    def test_logs_frontend_reads_project_dir_first(self) -> None:
        # 项目目录存在专属 frontend.log 时，应优先读取它而非 workspace 根
        _, init = self._init_project("lp_read")
        pid = init["project_id"]
        pdir = init["project_dir"]
        with open(os.path.join(pdir, "frontend.log"), "w", encoding="utf-8") as f:
            f.write("[2026-01-01 00:00:00] [info] [frontend] projline\n")
        status, body = self._req("GET", f"/api/projects/{pid}/logs?source=frontend")
        self.assertEqual(status, 200)
        self.assertTrue(body["exists"])
        self.assertTrue(any("projline" in line for line in body["lines"]))


class FrontendLogDefaultOffTests(_Base):
    """覆盖产品新默认：writeFrontendLog=False 时 frontend.log 不落盘。"""

    _settings_override = {"writeFrontendLog": False}

    def test_frontend_log_not_written_by_default(self) -> None:
        # 默认关闭时，POST /api/log 不应生成 workspace 根 frontend.log
        status, body = self._req("POST", "/api/log", body={"level": "error", "message": "boom"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body.get("skipped", False))
        root_fe = os.path.join(self.root, "frontend.log")
        self.assertFalse(os.path.isfile(root_fe))

    def test_frontend_log_read_default_exists_false(self) -> None:
        # 读取路径也应反映"无 frontend.log"
        _, init = self._init_project("off_lp")
        pid = init["project_id"]
        status, body = self._req("GET", f"/api/projects/{pid}/logs?source=frontend")
        self.assertEqual(status, 200)
        self.assertEqual(body["source"], "frontend")
        self.assertFalse(body["exists"])
        self.assertEqual(body["lines"], [])

    def test_frontend_log_respects_project_id_off(self) -> None:
        # 即使携带合法 project_id，默认关闭时也不在项目目录生成 frontend.log
        _, init = self._init_project("off_pid")
        pid = init["project_id"]
        pdir = init["project_dir"]
        status, body = self._req(
            "POST", "/api/log", body={"level": "info", "message": "inproject", "project_id": pid}
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        proj_fe = os.path.join(pdir, "frontend.log")
        self.assertFalse(os.path.isfile(proj_fe))


if __name__ == "__main__":
    unittest.main()
