"""GET /api/projects/:id/input-stats 与 get_dir_total_size 的回归测试。

覆盖：目录体积统计（嵌套/缺失目录）、端点阈值判定（>1MB 建议 git）、
空目录不建议、gt_input 缺失时回退旧版 json_jp 目录。
"""

import importlib
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from GalTransl import server as _server_mod
from GalTransl.Utils import GIT_SUGGEST_INPUT_SIZE_BYTES, get_dir_total_size


def _start_server(workspace_root: str):
    os.environ["GALTRANSL_WORKSPACE_ROOT"] = workspace_root
    os.environ.pop("GALTRANSL_API_TOKEN", None)
    importlib.reload(_server_mod)
    registry = _server_mod.JobRegistry()
    srv = _server_mod.ThreadingHTTPServer(
        ("127.0.0.1", 0), _server_mod.build_handler(registry)
    )
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port, registry


class GetDirTotalSizeTests(unittest.TestCase):
    def test_sums_nested_files(self) -> None:
        tmp = tempfile.mkdtemp()
        sizes = {"a.txt": 100, os.path.join("sub", "b.bin"): 2048}
        for rel, size in sizes.items():
            fp = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "wb") as f:
                f.write(b"\0" * size)
        self.assertEqual(get_dir_total_size(tmp), 100 + 2048)

    def test_missing_dir_returns_zero(self) -> None:
        self.assertEqual(get_dir_total_size(os.path.join(tempfile.mkdtemp(), "无")), 0)


class InputStatsEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp()
        cls.server, cls.port, cls.registry = _start_server(cls.tmp)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

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

    def _init_project(self, name: str):
        return self._req("POST", "/api/projects/init", body={"name": name})

    def test_empty_input_not_suggested(self) -> None:
        _, init = self._init_project("stats_empty")
        pid = init["project_id"]
        status, body = self._req("GET", f"/api/projects/{pid}/input-stats")
        self.assertEqual(status, 200)
        self.assertEqual(body["total_bytes"], 0)
        self.assertEqual(body["file_count"], 0)
        self.assertEqual(body["threshold_bytes"], GIT_SUGGEST_INPUT_SIZE_BYTES)
        self.assertFalse(body["suggest_git"])

    def test_oversize_input_suggested(self) -> None:
        _, init = self._init_project("stats_big")
        pid = init["project_id"]
        pdir = init["project_dir"]
        fp = os.path.join(pdir, "gt_input", "script.txt")
        with open(fp, "wb") as f:
            f.write(b"\0" * (GIT_SUGGEST_INPUT_SIZE_BYTES + 1024))
        status, body = self._req("GET", f"/api/projects/{pid}/input-stats")
        self.assertEqual(status, 200)
        self.assertEqual(body["total_bytes"], GIT_SUGGEST_INPUT_SIZE_BYTES + 1024)
        self.assertEqual(body["file_count"], 1)
        self.assertTrue(body["suggest_git"])

    def test_fallback_to_legacy_json_jp(self) -> None:
        _, init = self._init_project("stats_legacy")
        pid = init["project_id"]
        pdir = init["project_dir"]
        # 模拟旧项目：无 gt_input，源文件在 json_jp
        shutil.rmtree(os.path.join(pdir, "gt_input"), ignore_errors=True)
        legacy = os.path.join(pdir, "json_jp")
        os.makedirs(legacy)
        with open(os.path.join(legacy, "old.txt"), "wb") as f:
            f.write(b"\0" * 512)
        status, body = self._req("GET", f"/api/projects/{pid}/input-stats")
        self.assertEqual(status, 200)
        self.assertEqual(body["file_count"], 1)
        self.assertEqual(body["total_bytes"], 512)
        self.assertFalse(body["suggest_git"])
        self.assertEqual(os.path.basename(body["input_dir"]), "json_jp")

    def test_post_method_rejected(self) -> None:
        _, init = self._init_project("stats_method")
        pid = init["project_id"]
        status, _ = self._req("POST", f"/api/projects/{pid}/input-stats", body={})
        self.assertEqual(status, 405)


if __name__ == "__main__":
    unittest.main()
