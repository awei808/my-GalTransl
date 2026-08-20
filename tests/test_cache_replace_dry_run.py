"""cache/replace 端点 dry_run 响应携带替换前原值 entries 的回归测试。

覆盖 H5 后端：dry_run=true 时响应携带未修改的原值 entries（供前端构造撤销
before 快照）且不落盘；真实替换后 entries 为替换后值并落盘。
"""

import importlib
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

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
    return srv, port


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

    def _read_cache(self, project_dir: str, rel: str) -> list:
        with open(os.path.join(project_dir, "transl_cache", rel), encoding="utf-8") as f:
            return json.load(f)


class CacheReplaceDryRunTests(_Base):
    def test_dry_run_returns_original_entries_and_does_not_write(self) -> None:
        _, init = self._init_project("cr_dry")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "d.txt.json"
        self._write_cache(pdir, rel, [
            {"index": 1, "name": "", "pre_src": "テスト", "post_src": "テスト",
             "pre_dst": "旧译文A", "proofread_dst": "旧译文B"},
        ])

        status, body = self._req("POST", f"/api/projects/{pid}/cache/replace", body={
            "query": "旧", "replacement": "新", "field": "dst", "dry_run": True,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["total_matches"], 2)
        self.assertEqual(len(body["file_details"]), 1)
        fd = body["file_details"][0]
        self.assertEqual(fd["filename"], rel)
        self.assertEqual(fd["matches"], 2)
        # H5：dry_run 响应携带未修改的原值 entries（撤销 before 快照）
        self.assertIsNotNone(fd.get("entries"))
        self.assertEqual(fd["entries"][0]["pre_dst"], "旧译文A")
        self.assertEqual(fd["entries"][0]["proofread_dst"], "旧译文B")
        # dry_run 不落盘
        saved = self._read_cache(pdir, rel)
        self.assertEqual(saved[0]["pre_dst"], "旧译文A")
        self.assertEqual(saved[0]["proofread_dst"], "旧译文B")

    def test_real_replace_returns_replaced_entries_and_persists(self) -> None:
        _, init = self._init_project("cr_real")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "e.txt.json"
        self._write_cache(pdir, rel, [
            {"index": 1, "name": "", "pre_src": "テスト", "post_src": "テスト", "pre_dst": "旧译文"},
        ])

        status, body = self._req("POST", f"/api/projects/{pid}/cache/replace", body={
            "query": "旧", "replacement": "新", "field": "dst", "dry_run": False,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["dry_run"], False)
        fd = body["file_details"][0]
        self.assertEqual(fd["entries"][0]["pre_dst"], "新译文")
        # 真实替换落盘
        saved = self._read_cache(pdir, rel)
        self.assertEqual(saved[0]["pre_dst"], "新译文")

    def test_replace_finds_entries_in_nested_cache_subdir(self) -> None:
        # 回归：真实项目的缓存位于 pass3_cache/ 子目录，replace 必须递归命中
        # （此前 /cache/replace 只遍历 cache_dir 顶层，而 /cache/search 递归，导致
        # 查找有结果、替换 0 命中）
        _, init = self._init_project("cr_nested")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "pass3_cache/f.txt.json"
        self._write_cache(pdir, rel, [
            {"index": 1, "name": "", "pre_src": "テスト", "post_src": "テスト",
             "pre_dst": "旧译文"},
        ])

        # dry_run 预览命中
        status, body = self._req("POST", f"/api/projects/{pid}/cache/replace", body={
            "query": "旧", "replacement": "新", "field": "dst", "dry_run": True,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["total_matches"], 1)
        self.assertEqual(len(body["file_details"]), 1)
        self.assertEqual(body["file_details"][0]["filename"], rel)
        self.assertEqual(body["file_details"][0]["entries"][0]["pre_dst"], "旧译文")

        # 真实替换命中并落盘
        status, body = self._req("POST", f"/api/projects/{pid}/cache/replace", body={
            "query": "旧", "replacement": "新", "field": "dst", "dry_run": False,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["total_matches"], 1)
        saved = self._read_cache(pdir, rel)
        self.assertEqual(saved[0]["pre_dst"], "新译文")

    def test_replace_skips_metadata_files_in_subdirs(self) -> None:
        # 递归遍历后必须沿用 search 的过滤规则：pass1/pass2 元数据不参与替换
        _, init = self._init_project("cr_meta")
        pid = init["project_id"]
        pdir = init["project_dir"]
        self._write_cache(pdir, "pass1_cache/x.meta.json", [
            {"index": 1, "pre_src": "テスト", "post_src": "テスト", "pre_dst": "旧译文"},
        ])
        self._write_cache(pdir, "pass2_cache/x.batch.json", [
            {"index": 1, "pre_src": "テスト", "post_src": "テスト", "pre_dst": "旧译文"},
        ])

        status, body = self._req("POST", f"/api/projects/{pid}/cache/replace", body={
            "query": "旧", "replacement": "新", "field": "all", "dry_run": True,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["total_matches"], 0)


class CacheReplaceEntryTests(_Base):
    """cache/replace-entry 单条替换端点（查找替换侧边栏「替换单个」）。"""

    def test_replace_entry_single_match_in_nested_subdir(self) -> None:
        # 指定文件+index 单条替换：dry_run 预览、真实替换落盘，响应结构与批量 replace 一致
        _, init = self._init_project("cre_single")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "pass3_cache/single.txt.json"
        self._write_cache(pdir, rel, [
            {"index": 1, "name": "", "pre_src": "テスト", "post_src": "テスト",
             "pre_dst": "她的手以惊人的速度伸向那根棒状物。"},
            {"index": 2, "name": "", "pre_src": "テスト", "post_src": "テスト",
             "pre_dst": "另一处棒状物不应被改动。"},
        ])

        # dry_run 预览只命中 index=1
        status, body = self._req("POST", f"/api/projects/{pid}/cache/replace-entry", body={
            "query": "棒状物", "replacement": "假阳具", "field": "dst",
            "filename": rel, "index": 1, "dry_run": True,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["total_matches"], 1)
        self.assertEqual(body["total_files"], 1)
        self.assertEqual(body["file_details"][0]["filename"], rel)
        self.assertEqual(body["file_details"][0]["matches"], 1)
        # dry_run 返回未修改原值（撤销 before 快照），且不落盘
        self.assertEqual(body["file_details"][0]["entries"][0]["pre_dst"], "她的手以惊人的速度伸向那根棒状物。")
        saved = self._read_cache(pdir, rel)
        self.assertEqual(saved[0]["pre_dst"], "她的手以惊人的速度伸向那根棒状物。")
        self.assertEqual(saved[1]["pre_dst"], "另一处棒状物不应被改动。")

        # 真实替换只改 index=1，index=2 不受影响并落盘
        status, body = self._req("POST", f"/api/projects/{pid}/cache/replace-entry", body={
            "query": "棒状物", "replacement": "假阳具", "field": "dst",
            "filename": rel, "index": 1, "dry_run": False,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["total_matches"], 1)
        saved = self._read_cache(pdir, rel)
        self.assertEqual(saved[0]["pre_dst"], "她的手以惊人的速度伸向那根假阳具。")
        self.assertEqual(saved[1]["pre_dst"], "另一处棒状物不应被改动。")

    def test_replace_entry_non_matching_index_returns_zero(self) -> None:
        # index 不匹配（或该 index 文本不含 query）→ 0 命中且不落盘
        _, init = self._init_project("cre_nomatch")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "pass3_cache/nomatch.txt.json"
        self._write_cache(pdir, rel, [
            {"index": 7, "name": "", "pre_src": "テスト", "post_src": "テスト", "pre_dst": "旧译文"},
        ])

        status, body = self._req("POST", f"/api/projects/{pid}/cache/replace-entry", body={
            "query": "旧", "replacement": "新", "field": "dst",
            "filename": rel, "index": 999, "dry_run": True,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["total_matches"], 0)
        self.assertEqual(body["file_details"], [])
        # 落盘不变
        saved = self._read_cache(pdir, rel)
        self.assertEqual(saved[0]["pre_dst"], "旧译文")

    def test_replace_entry_rejects_path_traversal(self) -> None:
        # filename 路径穿越防护（与 /cache/:filename 一致）
        _, init = self._init_project("cre_trav")
        pid = init["project_id"]
        pdir = init["project_dir"]
        self._write_cache(pdir, "pass3_cache/trav.txt.json", [
            {"index": 1, "name": "", "pre_src": "テスト", "post_src": "テスト", "pre_dst": "旧译文"},
        ])

        for evil in ("..", "../secret.json", "..\\secret.json"):
            status, _ = self._req("POST", f"/api/projects/{pid}/cache/replace-entry", body={
                "query": "旧", "replacement": "新", "field": "dst",
                "filename": evil, "index": 1, "dry_run": True,
            })
            self.assertEqual(status, 400, f"filename={evil!r} 应被拒绝")

    def test_replace_entry_missing_file_returns_404(self) -> None:
        _, init = self._init_project("cre_missing")
        pid = init["project_id"]
        status, _ = self._req("POST", f"/api/projects/{pid}/cache/replace-entry", body={
            "query": "旧", "replacement": "新", "field": "dst",
            "filename": "pass3_cache/不存在.json", "index": 1, "dry_run": True,
        })
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
