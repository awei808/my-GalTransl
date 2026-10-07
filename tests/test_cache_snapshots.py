"""缓存快照备份的回归测试。

覆盖：
- snapshot_cache_file 单元行为：创建/内容一致/子路径镜像/60s 合并窗口/滚动保留/
  源缺失/复制失败容错/同目录不同源文件前缀不互扰；
- 写/删端点接入：/cache/save、/cache/delete-file、/cache/delete-entry、
  /cache/replace、/cache/replace-entry 在真实写删前产生快照（dry_run 不产生）；
- .snapshots 目录对 GET /cache、GET /files、/cache/search 不可见。
"""

import glob
import importlib
import json
import os
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from GalTransl import server as _server_mod
from GalTransl.server_cache import (
    SNAPSHOT_DIRNAME,
    SNAPSHOT_KEEP_PER_FILE,
    snapshot_cache_file,
)

# 快照文件名中段必须是时间戳，避免 foo.json.*.bak 误配到 foo.json.meta.json.*.bak
_STAMP_ONLY_RE = re.compile(r"\d{8}-\d{6}-\d{6}")


def _snapshots_for(cache_dir: str, rel: str) -> list:
    """列出 cache_dir 下属于 rel 这一个源文件的全部快照（与实现同口径过滤）。"""
    rel_norm = rel.replace("\\", "/")
    base = os.path.basename(rel_norm)
    pattern = os.path.join(cache_dir, SNAPSHOT_DIRNAME, *rel_norm.split("/")) + ".*.bak"
    out = []
    for p in glob.glob(pattern):
        name = os.path.basename(p)
        if _STAMP_ONLY_RE.fullmatch(name[len(base) + 1 : -len(".bak")]):
            out.append(p)
    return sorted(out)


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


class SnapshotHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def _write(self, rel: str, content: str) -> str:
        fp = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(content)
        return fp

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def _no_window(self):
        """关闭 60s 合并窗口（快照排序按文件名时间戳，与 mtime 无关）。

        必须取负值：撞名递增会给最新快照打出超 now 1µs 的未来时间戳，
        时钟未走动时 diff 为负，补丁成 0 仍会命中 `< interval` 触发合并。
        """
        return mock.patch("GalTransl.server_cache.SNAPSHOT_MIN_INTERVAL_SECONDS", -1.0)

    def test_snapshot_creates_bak_with_same_content(self) -> None:
        fp = self._write("a.json", '[{"pre_dst": "旧"}]')
        dest = snapshot_cache_file(self.tmp, "a.json")
        self.assertIsNotNone(dest)
        self.assertTrue(os.path.isfile(dest))
        self.assertIn(SNAPSHOT_DIRNAME, dest)
        self.assertEqual(self._read(dest), self._read(fp))

    def test_snapshot_mirrors_subdir(self) -> None:
        self._write(os.path.join("pass3_cache", "sub.json"), "[]")
        dest = snapshot_cache_file(self.tmp, "pass3_cache/sub.json")
        self.assertIsNotNone(dest)
        self.assertTrue(os.path.basename(dest).startswith("sub.json."))
        self.assertIn("pass3_cache", dest)

    def test_snapshot_coalesces_within_interval(self) -> None:
        self._write("b.json", "v1")
        snapshot_cache_file(self.tmp, "b.json")
        self._write("b.json", "v2")
        snapshot_cache_file(self.tmp, "b.json")
        snaps = _snapshots_for(self.tmp, "b.json")
        self.assertEqual(len(snaps), 1)
        self.assertEqual(self._read(snaps[0]), "v2")

    def test_snapshot_rolls_beyond_keep(self) -> None:
        with self._no_window():
            for i in range(SNAPSHOT_KEEP_PER_FILE + 2):
                self._write("c.json", f"v{i}")
                snapshot_cache_file(self.tmp, "c.json")
        snaps = _snapshots_for(self.tmp, "c.json")
        self.assertEqual(len(snaps), SNAPSHOT_KEEP_PER_FILE)
        # 留下的是最新的几份（时间戳定宽，字典序即时序）
        self.assertEqual(self._read(snaps[-1]), f"v{SNAPSHOT_KEEP_PER_FILE + 1}")

    def test_snapshot_missing_source_returns_none(self) -> None:
        self.assertIsNone(snapshot_cache_file(self.tmp, "不存在.json"))

    def test_snapshot_copy_failure_returns_none(self) -> None:
        self._write("d.json", "x")
        with mock.patch(
            "GalTransl.server_cache.shutil.copy2", side_effect=PermissionError("占用")
        ):
            dest = snapshot_cache_file(self.tmp, "d.json", project_dir="")
        self.assertIsNone(dest)
        # 主文件未被破坏
        self.assertEqual(self._read(os.path.join(self.tmp, "d.json")), "x")

    def test_snapshot_prefix_not_confused_between_similar_names(self) -> None:
        # foo.json 与 foo.json.meta.json 同目录：前缀匹配必须用时间戳中段区分
        self._write("foo.json", "A")
        self._write("foo.json.meta.json", "B")
        s1 = snapshot_cache_file(self.tmp, "foo.json")
        s2 = snapshot_cache_file(self.tmp, "foo.json.meta.json")
        self.assertIsNotNone(s1)
        self.assertIsNotNone(s2)
        self.assertEqual(len(_snapshots_for(self.tmp, "foo.json.meta.json")), 1)
        self.assertEqual(len(_snapshots_for(self.tmp, "foo.json")), 1)


class SnapshotEndpointTests(_Base):
    def test_cache_save_snapshots_before_overwrite(self) -> None:
        _, init = self._init_project("snap_save")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "s.txt.json"
        cache_dir = os.path.join(pdir, "transl_cache")
        self._write_cache(pdir, rel, [{"index": 1, "pre_src": "A", "post_src": "A", "pre_dst": "旧译文"}])
        status, _ = self._req("POST", f"/api/projects/{pid}/cache/save", body={
            "filename": rel,
            "entries": [{"index": 1, "pre_src": "A", "post_src": "A", "pre_dst": "新译文"}],
        })
        self.assertEqual(status, 200)
        snaps = _snapshots_for(cache_dir, rel)
        self.assertEqual(len(snaps), 1)
        with open(snaps[0], encoding="utf-8") as f:
            self.assertEqual(json.load(f)[0]["pre_dst"], "旧译文")

    def test_cache_save_corrupt_old_file_still_snapshots(self) -> None:
        # 旧文件损坏导致审计解析失败（audit_ok=False）时，仍必须快照兜底
        _, init = self._init_project("snap_save_corrupt")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "corrupt.txt.json"
        cache_dir = os.path.join(pdir, "transl_cache")
        fp = os.path.join(cache_dir, rel)
        with open(fp, "wb") as f:
            f.write("{坏掉的数据不是json".encode("utf-8"))
        status, _ = self._req("POST", f"/api/projects/{pid}/cache/save", body={
            "filename": rel,
            "entries": [{"index": 1, "pre_src": "A", "post_src": "A", "pre_dst": "新译文"}],
        })
        self.assertEqual(status, 200)
        snaps = _snapshots_for(cache_dir, rel)
        self.assertEqual(len(snaps), 1)
        with open(snaps[0], "rb") as f:
            self.assertIn("坏掉的数据".encode(), f.read())

    def test_cache_save_no_change_no_snapshot(self) -> None:
        _, init = self._init_project("snap_save_same")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "same.txt.json"
        entries = [{"index": 1, "pre_src": "A", "post_src": "A", "pre_dst": "译文"}]
        self._write_cache(pdir, rel, entries)
        status, _ = self._req("POST", f"/api/projects/{pid}/cache/save", body={
            "filename": rel, "entries": entries,
        })
        self.assertEqual(status, 200)
        self.assertEqual(_snapshots_for(os.path.join(pdir, "transl_cache"), rel), [])

    def test_cache_delete_file_snapshots_before_remove(self) -> None:
        _, init = self._init_project("snap_delfile")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "del.txt.json"
        cache_dir = os.path.join(pdir, "transl_cache")
        self._write_cache(pdir, rel, [{"index": 1, "pre_dst": "译"}])
        status, body = self._req("POST", f"/api/projects/{pid}/cache/delete-file", body={
            "filenames": [rel],
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["deleted_files"], [rel])
        self.assertFalse(os.path.isfile(os.path.join(cache_dir, rel)))
        snaps = _snapshots_for(cache_dir, rel)
        self.assertEqual(len(snaps), 1)

    def test_cache_delete_entry_snapshots_before_write(self) -> None:
        _, init = self._init_project("snap_delentry")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "de.txt.json"
        cache_dir = os.path.join(pdir, "transl_cache")
        self._write_cache(pdir, rel, [
            {"index": 1, "pre_dst": "甲"}, {"index": 2, "pre_dst": "乙"},
        ])
        status, _ = self._req("POST", f"/api/projects/{pid}/cache/delete-entry", body={
            "filename": rel, "index": 0,
        })
        self.assertEqual(status, 200)
        snaps = _snapshots_for(cache_dir, rel)
        self.assertEqual(len(snaps), 1)
        with open(snaps[0], encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), 2)

    def test_cache_replace_snapshots_changed_file_only_when_real(self) -> None:
        _, init = self._init_project("snap_repl")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "r.txt.json"
        cache_dir = os.path.join(pdir, "transl_cache")
        self._write_cache(pdir, rel, [{"index": 1, "pre_dst": "旧词在"}])
        # dry_run：只预览，不快照
        status, _ = self._req("POST", f"/api/projects/{pid}/cache/replace", body={
            "query": "旧词", "replacement": "新词", "field": "dst", "dry_run": True,
        })
        self.assertEqual(status, 200)
        self.assertEqual(_snapshots_for(cache_dir, rel), [])
        # 真实替换：先快照后写
        status, _ = self._req("POST", f"/api/projects/{pid}/cache/replace", body={
            "query": "旧词", "replacement": "新词", "field": "dst", "dry_run": False,
        })
        self.assertEqual(status, 200)
        snaps = _snapshots_for(cache_dir, rel)
        self.assertEqual(len(snaps), 1)
        with open(snaps[0], encoding="utf-8") as f:
            self.assertEqual(json.load(f)[0]["pre_dst"], "旧词在")

    def test_cache_replace_no_match_no_snapshot(self) -> None:
        _, init = self._init_project("snap_repl0")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "r0.txt.json"
        self._write_cache(pdir, rel, [{"index": 1, "pre_dst": "译文"}])
        status, _ = self._req("POST", f"/api/projects/{pid}/cache/replace", body={
            "query": "不存在的词", "replacement": "x", "field": "dst", "dry_run": False,
        })
        self.assertEqual(status, 200)
        self.assertEqual(_snapshots_for(os.path.join(pdir, "transl_cache"), rel), [])

    def test_cache_replace_entry_snapshots(self) -> None:
        _, init = self._init_project("snap_replentry")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "re.txt.json"
        cache_dir = os.path.join(pdir, "transl_cache")
        self._write_cache(pdir, rel, [{"index": "1", "pre_dst": "单条旧"}])
        status, _ = self._req("POST", f"/api/projects/{pid}/cache/replace-entry", body={
            "query": "单条旧", "replacement": "单条新", "field": "dst",
            "filename": rel, "index": "1", "dry_run": False,
        })
        self.assertEqual(status, 200)
        snaps = _snapshots_for(cache_dir, rel)
        self.assertEqual(len(snaps), 1)
        with open(snaps[0], encoding="utf-8") as f:
            self.assertEqual(json.load(f)[0]["pre_dst"], "单条旧")

    def test_snapshots_invisible_to_listings_and_search(self) -> None:
        _, init = self._init_project("snap_hidden")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "h.txt.json"
        self._write_cache(pdir, rel, [{"index": 1, "pre_src": "A", "post_src": "A", "pre_dst": "现词独有标记"}])
        # 产生一份快照（保存后内容被改，快照里留着旧词）
        self._req("POST", f"/api/projects/{pid}/cache/save", body={
            "filename": rel,
            "entries": [{"index": 1, "pre_src": "A", "post_src": "A", "pre_dst": "改过的词"}],
        })
        self.assertEqual(len(_snapshots_for(os.path.join(pdir, "transl_cache"), rel)), 1)
        # GET /cache 顶层列表不含 .snapshots
        status, cache_body = self._req("GET", f"/api/projects/{pid}/cache")
        self.assertEqual(status, 200)
        self.assertNotIn(".snapshots", [f["name"] for f in cache_body["files"]])
        # GET /files 缓存树不含 .snapshots（含子级）
        status, files_body = self._req("GET", f"/api/projects/{pid}/files")
        self.assertEqual(status, 200)

        def _names(nodes):
            out = []
            for n in nodes:
                out.append(n["name"])
                out.extend(_names(n.get("children", [])))
            return out

        self.assertNotIn(".snapshots", _names(files_body["cache_files"]))
        # 搜索查不到只存在于快照里的旧值
        status, search_body = self._req("POST", f"/api/projects/{pid}/cache/search", body={
            "query": "现词独有标记", "field": "dst", "max_results": 50,
        })
        self.assertEqual(status, 200)
        results = search_body.get("results", search_body.get("matches", []))
        self.assertEqual(len(results), 0)


if __name__ == "__main__":
    unittest.main()
