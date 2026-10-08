"""全缓存重检：POST /api/projects/:id/cache/recheck-all 端点的回归测试（异步契约）。

覆盖：启动返回 202 + 轮询 status 至 finished 后写回 problem、清除过期 problem、
翻译任务运行中拒绝（409）、重检进行中重复提交拒绝（409）与状态端点进度、
配置加载失败落入状态、pass3 目录缺失 rechecked=0、progress_cb 逐文件计数、
重检进行中提交翻译任务被拒（/api/jobs 反向守卫）。
"""

import importlib
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

import orjson

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
        fp = os.path.join(project_dir, "transl_cache", rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "wb") as f:
            f.write(orjson.dumps(entries, option=orjson.OPT_INDENT_2))
        return fp

    def _start_recheck(self, pid: str):
        """启动重检并断言 202 契约。"""
        status, body = self._req("POST", f"/api/projects/{pid}/cache/recheck-all", body={})
        self.assertEqual(status, 202)
        self.assertTrue(body["success"])
        self.assertTrue(body["started"])

    def _wait_recheck_done(self, pid: str, timeout: float = 30.0) -> dict:
        """轮询 status 端点直到 finished（超时 fail）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, body = self._req("GET", f"/api/projects/{pid}/cache/recheck-all/status")
            self.assertEqual(status, 200)
            if body.get("finished"):
                return body
            time.sleep(0.1)
        self.fail("重检未在限时内完成")


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

        self._start_recheck(pid)
        final = self._wait_recheck_done(pid)
        self.assertTrue(final["success"])
        self.assertGreaterEqual(final["rechecked"], 1)

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

        self._start_recheck(pid)
        final = self._wait_recheck_done(pid)
        self.assertTrue(final["success"])

        with open(os.path.join(pdir, "transl_cache", rel), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertNotIn("problem", saved[0])

    def test_recheck_all_rejected_while_translating(self) -> None:
        # 翻译任务运行中：返回 409 且不启动重检
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

    def test_recheck_all_reentry_conflict_and_progress_status(self) -> None:
        # 重检进行中：重复提交返回 409；status 端点报告 running 与进度，完成后收敛
        _, init = self._init_project("ra_reentry")
        pid = init["project_id"]
        pdir = init["project_dir"]
        for i in range(2):
            self._write_cache(pdir, f"pass3_cache/f{i}.txt.json", [
                {"index": 1, "name": "", "pre_src": "テスト", "post_src": "テスト", "pre_dst": "これはテストです"}
            ])
        started = threading.Event()
        release = threading.Event()

        def _slow_recheck(*args, **kwargs):
            started.set()
            release.wait(timeout=10)
            cb = kwargs.get("progress_cb")
            if cb:
                cb(2, 2)
            return 2

        with mock.patch(
            "GalTransl.server_cache.recheck_pass3_cache_files", side_effect=_slow_recheck
        ):
            self._start_recheck(pid)
            self.assertTrue(started.wait(timeout=5))
            status2, body2 = self._req("POST", f"/api/projects/{pid}/cache/recheck-all", body={})
            self.assertEqual(status2, 409)
            self.assertFalse(body2["success"])
            status3, body3 = self._req("GET", f"/api/projects/{pid}/cache/recheck-all/status")
            self.assertEqual(status3, 200)
            self.assertTrue(body3["running"])
            release.set()
            final = self._wait_recheck_done(pid)
        self.assertTrue(final["success"])
        self.assertEqual(final["rechecked"], 2)
        self.assertEqual(final["checked"], 2)
        self.assertEqual(final["total"], 2)

    def test_recheck_all_config_missing_fails_in_status(self) -> None:
        # 配置缺失：启动仍 202，失败结果落入 status（error 可见）
        _, init = self._init_project("ra_nocfg")
        pid = init["project_id"]
        pdir = init["project_dir"]
        for name in ("config.yaml", "config.inc.yaml"):
            fp = os.path.join(pdir, name)
            if os.path.exists(fp):
                os.remove(fp)
        self._start_recheck(pid)
        final = self._wait_recheck_done(pid)
        self.assertFalse(final["success"])
        self.assertEqual(final["error"], "config load failed")

    def test_recheck_all_empty_pass3_reports_zero(self) -> None:
        # 无 pass3 目录/文件：完成后 rechecked=0
        _, init = self._init_project("ra_empty")
        pid = init["project_id"]
        pdir = init["project_dir"]
        pass3_dir = os.path.join(pdir, "transl_cache", "pass3_cache")
        if os.path.isdir(pass3_dir):
            shutil.rmtree(pass3_dir)
        self._start_recheck(pid)
        final = self._wait_recheck_done(pid)
        self.assertTrue(final["success"])
        self.assertEqual(final["rechecked"], 0)


class RecheckProgressCbTests(unittest.TestCase):
    """recheck_pass3_cache_files 的 progress_cb 逐文件计数（直调，不经 HTTP）"""

    def test_progress_cb_counts_every_file(self) -> None:
        from GalTransl import server_cache

        tmp = tempfile.mkdtemp()
        pass3 = os.path.join(tmp, "pass3_cache")
        os.makedirs(pass3)
        for i in range(3):
            with open(os.path.join(pass3, f"f{i}.json"), "wb") as f:
                f.write(orjson.dumps([{"index": 1, "pre_src": "あ", "post_src": "あ", "pre_dst": "い"}]))
        calls = []
        with mock.patch.object(server_cache, "_run_problem_detection", return_value=([], True)), \
             mock.patch.object(server_cache, "_resolve_cache_h_ranges", return_value={"h_ranges": []}):
            rechecked = server_cache.recheck_pass3_cache_files(
                tmp,
                proj_config=mock.Mock(),
                pre_dic=None,
                post_dic=None,
                gpt_dic=None,
                tPlugins=[],
                h_check_words=[],
                forbidden_words=[],
                progress_cb=lambda d, t: calls.append((d, t)),
            )
        self.assertEqual(rechecked, 3)
        # 初始 (0, total) + 每完成一个文件 (done, total)
        self.assertEqual(calls, [(0, 3), (1, 3), (2, 3), (3, 3)])

    def test_progress_cb_exception_does_not_break_recheck(self) -> None:
        from GalTransl import server_cache

        tmp = tempfile.mkdtemp()
        pass3 = os.path.join(tmp, "pass3_cache")
        os.makedirs(pass3)
        with open(os.path.join(pass3, "f0.json"), "wb") as f:
            f.write(orjson.dumps([{"index": 1, "pre_src": "あ", "post_src": "あ", "pre_dst": "い"}]))

        def _boom(done: int, total: int) -> None:
            raise RuntimeError("cb error")

        with mock.patch.object(server_cache, "_run_problem_detection", return_value=([], True)), \
             mock.patch.object(server_cache, "_resolve_cache_h_ranges", return_value={"h_ranges": []}):
            rechecked = server_cache.recheck_pass3_cache_files(
                tmp,
                proj_config=mock.Mock(),
                pre_dic=None,
                post_dic=None,
                gpt_dic=None,
                tPlugins=[],
                h_check_words=[],
                forbidden_words=[],
                progress_cb=_boom,
            )
        self.assertEqual(rechecked, 1)


class RecheckJobSubmitGuardTests(_Base):
    """重检与翻译互斥的反向守卫：重检进行中提交 /api/jobs 返回 409"""

    def _inject_running_state(self, project_dir: str) -> None:
        from GalTransl import server_cache

        server_cache._RECHECK_STATES[project_dir] = {
            "running": True,
            "checked": 0,
            "total": 0,
            "finished": False,
            "success": False,
            "error": "",
            "rechecked": 0,
        }
        self.addCleanup(server_cache._RECHECK_STATES.pop, project_dir, None)

    def test_job_submit_rejected_while_recheck_running(self) -> None:
        _, init = self._init_project("ra_guard")
        pdir = init["project_dir"]
        self._inject_running_state(pdir)
        payload = {
            "project_dir": pdir,
            "translator": "ForGal-json-translate",
            "config_file_name": "config.yaml",
        }
        status, body = self._req("POST", "/api/jobs", body=payload)
        self.assertEqual(status, 409)
        self.assertIn("重检", body.get("error", ""))

    def test_job_submit_reaches_registry_when_not_rechecking(self) -> None:
        # 无重检进行时守卫不拦截：非法 translator 走到 submit 参数校验（400 而非 409）
        _, init = self._init_project("ra_guard_ok")
        pdir = init["project_dir"]
        payload = {"project_dir": pdir, "translator": "no-such-translator"}
        status, body = self._req("POST", "/api/jobs", body=payload)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
