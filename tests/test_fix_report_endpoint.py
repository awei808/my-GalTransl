"""端点级测试：GET /api/projects/:id/fix-report。

验证"修复报告"标签页的数据来源：
- RuntimeRegistry 有本次任务的报告时优先返回（exists=true，含 label/started_at/finished_at）
- Registry 无状态（如后端重启）时回退读 transl_cache/fix_report/latest.json
- 两者皆无返回 exists=false + total=0（非错误）
"""

import importlib
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import orjson

from GalTransl import server as _server_mod
from GalTransl.server_runtime import (
    RUNTIME_REGISTRY,
    _normalize_project_dir,
    finish_project_fix_report,
    record_fix_change,
)


def _start_server(workspace_root: str) -> tuple:
    os.environ["GALTRANSL_WORKSPACE_ROOT"] = workspace_root
    os.environ.pop("GALTRANSL_API_TOKEN", None)
    importlib.reload(_server_mod)
    registry = _server_mod.JobRegistry()
    srv = _server_mod.ThreadingHTTPServer(
        ("127.0.0.1", 0), _server_mod.build_handler(registry)
    )
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def _entry(filename="a.json", index=1):
    return {
        "filename": filename,
        "index": index,
        "speaker": "爱丽丝",
        "post_src": "こんにちは",
        "old_dst": "旧译",
        "new_dst": "新译",
        "problems": "残留日文",
        "backend": "ForImproveTranslation",
        "writes_main": False,
    }


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp()
        cls.server, cls.port = _start_server(cls.tmp)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _req(self, method: str, path: str, body=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = orjson.dumps(body) if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, orjson.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            return exc.code, orjson.loads(exc.read().decode() or "{}")

    def _init_project(self, name: str) -> dict:
        _, init = self._req("POST", "/api/projects/init", body={"name": name})
        return init

    def _drop_state(self, project_dir: str) -> None:
        # 测试专用清理：移除项目运行态，模拟后端重启后 Registry 无状态
        RUNTIME_REGISTRY._states.pop(_normalize_project_dir(project_dir), None)


class FixReportEndpointTests(_Base):
    def test_serves_registry_snapshot_when_running(self) -> None:
        init = self._init_project(f"fxrep_{os.urandom(4).hex()}")
        pdir = init["project_dir"]
        RUNTIME_REGISTRY.reset_project(pdir)  # 模拟任务已启动（报告打开）
        try:
            record_fix_change(type("Cfg", (), {"runtime_project_dir": pdir})(), _entry())
            status, body = self._req(
                "GET", f"/api/projects/{init['project_id']}/fix-report"
            )
            self.assertEqual(status, 200)
            self.assertTrue(body["exists"])
            self.assertEqual(body["total"], 1)
            self.assertEqual(body["finished_at"], "")
            self.assertEqual(len(body["files"]), 1)
            group = body["files"][0]
            self.assertEqual(group["filename"], "a.json")
            self.assertEqual(group["count"], 1)
            self.assertEqual(group["entries"][0]["new_dst"], "新译")
            self.assertEqual(group["entries"][0]["writes_main"], False)
        finally:
            self._drop_state(pdir)

    def test_file_fallback_after_registry_cleared(self) -> None:
        init = self._init_project(f"fxfile_{os.urandom(4).hex()}")
        pdir = init["project_dir"]
        RUNTIME_REGISTRY.reset_project(pdir)
        try:
            record_fix_change(type("Cfg", (), {"runtime_project_dir": pdir})(), _entry())
            finish_project_fix_report(pdir, label="ForImproveTranslation")
            self._drop_state(pdir)  # 模拟后端重启
            status, body = self._req(
                "GET", f"/api/projects/{init['project_id']}/fix-report"
            )
            self.assertEqual(status, 200)
            self.assertTrue(body["exists"])
            self.assertEqual(body["label"], "ForImproveTranslation")
            self.assertNotEqual(body["finished_at"], "")
            self.assertEqual(body["files"][0]["entries"][0]["old_dst"], "旧译")
        finally:
            self._drop_state(pdir)

    def test_missing_report_returns_exists_false(self) -> None:
        init = self._init_project(f"fxnone_{os.urandom(4).hex()}")
        status, body = self._req(
            "GET", f"/api/projects/{init['project_id']}/fix-report"
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["exists"])
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["files"], [])


if __name__ == "__main__":
    unittest.main()
