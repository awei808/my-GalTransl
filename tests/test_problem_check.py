"""问题检测：POST /api/projects/:id/cache/check（只检测不落盘）与 cache/save rebuild 的 problem 保护。"""
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
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp()
        cls.server, cls.port = _start_server(cls.tmp)
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


class CacheCheckTests(_Base):
    def test_cache_check_detects_residual_japanese(self) -> None:
        # 默认配置启用"残留日文"：译文含假名时返回对应 problem；skip_check 条目不参与检测
        _, init = self._init_project("cc_jp")
        pid = init["project_id"]
        entries = [
            {"index": 1, "name": "", "pre_src": "测试", "post_src": "测试", "pre_dst": "これはテストです"},
            {"index": 2, "name": "", "pre_src": "你好", "post_src": "你好", "pre_dst": "你好", "skip_check": True},
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/cc.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        results = {r["index"]: r for r in body["results"]}
        self.assertIn("残留日文", results[1]["problem"])
        self.assertEqual(results[2]["problem"], "")

    def test_cache_check_invalid_path_rejected(self) -> None:
        _, init = self._init_project("cc_bad")
        pid = init["project_id"]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "../evil.json", "entries": []},
        )
        self.assertEqual(status, 400)

    def test_cache_check_config_missing_returns_failure(self) -> None:
        # 配置缺失时返回 success=False 且不崩溃
        _, init = self._init_project("cc_nocfg2")
        pid = init["project_id"]
        pdir = init["project_dir"]
        os.remove(os.path.join(pdir, "config.yaml"))
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/x.json", "entries": []},
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["success"])
        self.assertEqual(body["results"], [])


class CacheSaveProblemProtectionTests(_Base):
    def test_cache_save_preserves_problem_when_config_missing(self) -> None:
        # 配置缺失 → rebuild 跳过，已有 problem 不得被删除
        _, init = self._init_project("cc_nocfg")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "pass3_cache/cc.txt.json"
        entries = [
            {"index": 1, "name": "", "pre_src": "测试", "post_src": "测试", "pre_dst": "これはテストです", "problem": "残留日文"}
        ]
        self._write_cache(pdir, rel, entries)
        os.remove(os.path.join(pdir, "config.yaml"))
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/save",
            body={"filename": rel, "entries": entries},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        with open(os.path.join(pdir, "transl_cache", rel), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved[0]["problem"], "残留日文")

    def test_cache_save_preserves_problem_when_detection_fails(self) -> None:
        # find_problems 抛异常 → 保留已有 problem，不误删
        _, init = self._init_project("cc_fail")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "pass3_cache/cc.txt.json"
        entries = [
            {"index": 1, "name": "", "pre_src": "测试", "post_src": "测试", "pre_dst": "これはテストです", "problem": "残留日文"}
        ]
        self._write_cache(pdir, rel, entries)
        with mock.patch("GalTransl.Problem.find_problems", side_effect=RuntimeError("boom")):
            status, body = self._req(
                "POST",
                f"/api/projects/{pid}/cache/save",
                body={"filename": rel, "entries": entries},
            )
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        with open(os.path.join(pdir, "transl_cache", rel), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved[0]["problem"], "残留日文")


class NewlineDetectionTests(_Base):
    def test_newline_count_mixed(self) -> None:
        # 真实 \r\n/\n/\r 与字面转义均计 1 次，CRLF 不重复计
        from GalTransl.Problem import _newline_count

        self.assertEqual(_newline_count("a\r\nb"), 1)
        self.assertEqual(_newline_count("a\nb\nc"), 2)
        self.assertEqual(_newline_count("a\\r\\nb"), 1)
        self.assertEqual(_newline_count("a\\nb"), 1)
        self.assertEqual(_newline_count("a\rb"), 1)
        self.assertEqual(_newline_count("a\r\nb\nc\\n"), 3)

    def test_cache_check_detects_extra_newlines_crlf_src_lf_dst(self) -> None:
        # 原文 CRLF、译文 LF 且换行更多：应报"多加换行"（修复前因换行符类型不同而漏报）
        _, init = self._init_project("nl_extra")
        pid = init["project_id"]
        entries = [
            {
                "index": 1,
                "name": "",
                "pre_src": "実在するモデルを使って、どうポーズを取らせれば、\r\nより魅力的に見せることが出来るのか。",
                "post_src": "実在するモデルを使って、どうポーズを取らせれば、\r\nより魅力的に見せることが出来るのか。",
                "pre_dst": "怎样让真实的模特摆出姿势，\n\n\n才能看起来更有魅力。\n",
            }
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/nl.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        results = {r["index"]: r for r in body["results"]}
        self.assertIn("多加换行", results[1]["problem"])

    def test_cache_check_no_newline_false_positive_same_count(self) -> None:
        # 原文与译文换行数量一致（符号类型不同）：不应报换行问题
        _, init = self._init_project("nl_same")
        pid = init["project_id"]
        entries = [
            {"index": 1, "name": "", "pre_src": "A\r\nB", "post_src": "A\r\nB", "pre_dst": "甲\n乙"}
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/nl.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        results = {r["index"]: r for r in body["results"]}
        self.assertNotIn("多加换行", results[1]["problem"])
        self.assertNotIn("丢失换行", results[1]["problem"])


if __name__ == "__main__":
    unittest.main()
