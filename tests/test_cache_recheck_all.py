"""全缓存重检：POST /api/projects/:id/cache/recheck-all 端点的回归测试。

覆盖：正常重检写回 problem、清除过期 problem、翻译任务运行中拒绝（409）、
配置加载失败、pass3 目录缺失返回 0。
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
from unittest import mock

from GalTransl import server as _server_mod


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
    return srv, port, registry


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp()
        cls.server, cls.port, cls.registry = _start_server(cls.tmp)
        cls.root = cls.tmp

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


class RecheckAllCacheFilesTests(_Base):
    def test_recheck_all_writes_back_problem(self) -> None:
        # 默认配置启用"残留日文"：重检后写回 problem 与 post_dst_preview
        _, init = self._init_project("ra_jp")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "pass3_cache/a.txt.json"
        entries = [
            {"index": 1, "name": "", "pre_src": "テスト", "post_src": "テスト", "pre_dst": "これはテストです"}
        ]
        self._write_cache(pdir, rel, entries)

        status, body = self._req("POST", f"/api/projects/{pid}/cache/recheck-all", body={})
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        self.assertGreaterEqual(body["rechecked"], 1)

        with open(os.path.join(pdir, "transl_cache", rel), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertIn("残留日文", saved[0].get("problem", ""))
        self.assertEqual(saved[0].get("post_dst_preview"), "これはテストです")

    def test_recheck_all_clears_stale_problem(self) -> None:
        # 用户已在缓存中修好译文但旧 problem 未清除：重检后应移除
        _, init = self._init_project("ra_clear")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "pass3_cache/b.txt.json"
        entries = [
            {"index": 1, "name": "", "pre_src": "テスト", "post_src": "テスト",
             "pre_dst": "测试", "proofread_dst": "测试", "problem": "残留日文：テスト"}
        ]
        self._write_cache(pdir, rel, entries)

        status, body = self._req("POST", f"/api/projects/{pid}/cache/recheck-all", body={})
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])

        with open(os.path.join(pdir, "transl_cache", rel), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertNotIn("problem", saved[0])

    def test_recheck_all_rejected_while_translating(self) -> None:
        # 翻译任务运行中：返回 409 且不写回任何文件
        _, init = self._init_project("ra_busy")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "pass3_cache/c.txt.json"
        entries = [
            {"index": 1, "name": "", "pre_src": "テスト", "post_src": "テスト", "pre_dst": "これはテストです"}
        ]
        self._write_cache(pdir, rel, entries)

        with mock.patch.object(
            self.registry, "get_project_job", return_value=mock.Mock(status="running")
        ):
            status, body = self._req("POST", f"/api/projects/{pid}/cache/recheck-all", body={})
        self.assertEqual(status, 409)
        self.assertFalse(body["success"])
        # 文件未被改动（problem 未写回）
        with open(os.path.join(pdir, "transl_cache", rel), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertNotIn("problem", saved[0])

    def test_recheck_all_config_missing_fails(self) -> None:
        # 配置缺失：返回 success=False 且不崩溃
        _, init = self._init_project("ra_nocfg")
        pid = init["project_id"]
        pdir = init["project_dir"]
        for name in ("config.yaml", "config.inc.yaml"):
            fp = os.path.join(pdir, name)
            if os.path.exists(fp):
                os.remove(fp)
        status, body = self._req("POST", f"/api/projects/{pid}/cache/recheck-all", body={})
        self.assertEqual(status, 200)
        self.assertFalse(body["success"])
        self.assertEqual(body["error"], "config load failed")

    def test_recheck_all_empty_pass3_returns_zero(self) -> None:
        # 无 pass3 目录/文件：返回 rechecked=0
        _, init = self._init_project("ra_empty")
        pid = init["project_id"]
        pdir = init["project_dir"]
        pass3_dir = os.path.join(pdir, "transl_cache", "pass3_cache")
        if os.path.isdir(pass3_dir):
            shutil.rmtree(pass3_dir)
        status, body = self._req("POST", f"/api/projects/{pid}/cache/recheck-all", body={})
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        self.assertEqual(body["rechecked"], 0)


if __name__ == "__main__":
    unittest.main()
