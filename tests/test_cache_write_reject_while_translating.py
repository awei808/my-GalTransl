"""翻译任务运行中写缓存端点拒绝（409）的回归测试（M4）。

覆盖：/cache/save、/cache/delete-entry、/build-output、/build-output/:filename
在 pending/running 任务存在时返回 409 且不写盘，与 /cache/recheck-all 一致。
"""

import importlib
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from GalTransl import server as _server_mod


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


class _Base(unittest.TestCase):
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

    def _write_cache(self, project_dir: str, rel: str, entries: list) -> str:
        import orjson

        fp = os.path.join(project_dir, "transl_cache", rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "wb") as f:
            f.write(orjson.dumps(entries, option=orjson.OPT_INDENT_2))
        return fp


class CacheWriteRejectedWhileTranslatingTests(_Base):
    def _mock_running(self):
        return mock.patch.object(self.registry, "get_project_job", return_value=mock.Mock(status="running"))

    def test_cache_save_rejected_while_translating(self) -> None:
        _, init = self._init_project("wr_save")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "s.txt.json"
        fp = self._write_cache(pdir, rel, [{"index": 1, "pre_src": "A", "post_src": "A", "pre_dst": "旧"}])
        with self._mock_running():
            status, body = self._req("POST", f"/api/projects/{pid}/cache/save", body={
                "filename": rel, "entries": [{"index": 1, "pre_dst": "新"}],
            })
        self.assertEqual(status, 409)
        with open(fp, encoding="utf-8") as f:
            self.assertEqual(json.load(f)[0]["pre_dst"], "旧")

    def test_cache_delete_entry_rejected_while_translating(self) -> None:
        _, init = self._init_project("wr_del")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "d.txt.json"
        fp = self._write_cache(pdir, rel, [{"index": 1, "pre_src": "A", "post_src": "A", "pre_dst": "译"}])
        with self._mock_running():
            status, body = self._req("POST", f"/api/projects/{pid}/cache/delete-entry", body={
                "filename": rel, "index": 0,
            })
        self.assertEqual(status, 409)
        with open(fp, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), 1)

    def test_build_output_rejected_while_translating(self) -> None:
        _, init = self._init_project("wr_build")
        pid = init["project_id"]
        self._write_cache(init["project_dir"], "b.txt.json", [{"index": 1, "pre_dst": "译"}])
        with self._mock_running():
            status, body = self._req("POST", f"/api/projects/{pid}/build-output", body={})
        self.assertEqual(status, 409)

    def test_build_output_single_rejected_while_translating(self) -> None:
        _, init = self._init_project("wr_build1")
        pid = init["project_id"]
        self._write_cache(init["project_dir"], "b1.txt.json", [{"index": 1, "pre_dst": "译"}])
        with self._mock_running():
            status, body = self._req("POST", f"/api/projects/{pid}/build-output/b1.txt.json", body={})
        self.assertEqual(status, 409)

    def test_replace_real_rejected_while_translating(self) -> None:
        # replace 真实替换会写缓存：运行中拒绝；dry_run 预览只读不落盘，保持可用
        _, init = self._init_project("wr_repl")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "r.txt.json"
        fp = self._write_cache(pdir, rel, [{"index": 1, "pre_dst": "旧译文"}])
        with self._mock_running():
            status, body = self._req("POST", f"/api/projects/{pid}/cache/replace", body={
                "query": "旧", "replacement": "新", "field": "dst", "dry_run": False,
            })
        self.assertEqual(status, 409)
        with open(fp, encoding="utf-8") as f:
            self.assertEqual(json.load(f)[0]["pre_dst"], "旧译文")

    def test_replace_dry_run_allowed_while_translating(self) -> None:
        _, init = self._init_project("wr_repl_dry")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "r2.txt.json"
        self._write_cache(pdir, rel, [{"index": 1, "pre_dst": "旧译文"}])
        with self._mock_running():
            status, body = self._req("POST", f"/api/projects/{pid}/cache/replace", body={
                "query": "旧", "replacement": "新", "field": "dst", "dry_run": True,
            })
        self.assertEqual(status, 200)
        self.assertEqual(body["total_matches"], 1)


if __name__ == "__main__":
    unittest.main()
