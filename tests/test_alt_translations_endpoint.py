"""端点级测试：GET /api/projects/:id/alt-translations。

验证"查看备选"侧边栏的数据来源：
- 只返回 alt_dst（备选译文）非空的缓存条目，其余跳过
- 递归遍历 transl_cache（含 pass3_cache 子目录），元数据 json（非 list）自然跳过
- ?file= 仅限单个文件，且做路径穿越防护（与 /problems 一致）
- 字段对齐 ProblemEntry 风格：filename/index/speaker/post_src/pre_dst/alt_dst/trans_by
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


def _start_server(workspace_root: str, token: str = "") -> tuple:
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

    def _write_cache(self, project_dir: str, rel_path: str, entries: list) -> None:
        cache_dir = os.path.join(project_dir, "transl_cache")
        full = os.path.join(cache_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(orjson.dumps(entries, option=orjson.OPT_INDENT_2))


class AltTranslationsEndpointTests(_Base):
    def test_returns_only_entries_with_alt_dst(self) -> None:
        init = self._init_project(f"alt_{os.urandom(4).hex()}")
        pdir = init["project_dir"]
        # 同一文件内：一条有备选，一条没有
        self._write_cache(
            pdir,
            "pass3_cache/demo.json",
            [
                {
                    "index": 0,
                    "name": "爱丽丝",
                    "pre_src": "こんにちは",
                    "post_src": "こんにちは",
                    "pre_dst": "你好",
                    "alt_dst": "日安",
                    "proofread_dst": "",
                    "trans_by": "model",
                    "proofread_by": "",
                },
                {
                    "index": 1,
                    "name": "鲍勃",
                    "pre_src": "さようなら",
                    "post_src": "さようなら",
                    "pre_dst": "再见",
                    "alt_dst": "",
                    "proofread_dst": "",
                    "trans_by": "model",
                    "proofread_by": "",
                },
            ],
        )
        status, body = self._req("GET", f"/api/projects/{init['project_id']}/alt-translations")
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 1)
        self.assertEqual(len(body["alts"]), 1)
        alt = body["alts"][0]
        # 路径须以 transl_cache 为根的 relpath
        self.assertEqual(alt["filename"], "pass3_cache/demo.json")
        self.assertEqual(alt["index"], 0)
        self.assertEqual(alt["speaker"], "爱丽丝")
        self.assertEqual(alt["post_src"], "こんにちは")
        self.assertEqual(alt["pre_dst"], "你好")
        self.assertEqual(alt["alt_dst"], "日安")
        self.assertEqual(alt["trans_by"], "model")

    def test_aggregates_across_files(self) -> None:
        init = self._init_project(f"alt_agg_{os.urandom(4).hex()}")
        pdir = init["project_dir"]
        self._write_cache(
            pdir,
            "pass3_cache/a.json",
            [{"index": 0, "name": "", "pre_src": "x", "post_src": "x",
              "pre_dst": "y", "alt_dst": "z", "trans_by": "model"}],
        )
        self._write_cache(
            pdir,
            "pass3_cache/b.json",
            [
                {"index": 0, "name": "", "pre_src": "x", "post_src": "x",
                 "pre_dst": "y", "alt_dst": "z", "trans_by": "model"},
                {"index": 1, "name": "", "pre_src": "x", "post_src": "x",
                 "pre_dst": "y", "alt_dst": "z", "trans_by": "model"},
            ],
        )
        status, body = self._req("GET", f"/api/projects/{init['project_id']}/alt-translations")
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 3)
        # 各文件计数正确（用于侧栏分组）
        by_file = {}
        for alt in body["alts"]:
            by_file[alt["filename"]] = by_file.get(alt["filename"], 0) + 1
        self.assertEqual(by_file.get("pass3_cache/a.json"), 1)
        self.assertEqual(by_file.get("pass3_cache/b.json"), 2)

    def test_file_filter_scopes_to_single_file(self) -> None:
        init = self._init_project(f"alt_flt_{os.urandom(4).hex()}")
        pdir = init["project_dir"]
        self._write_cache(
            pdir,
            "pass3_cache/a.json",
            [{"index": 0, "name": "", "pre_src": "x", "post_src": "x",
              "pre_dst": "y", "alt_dst": "z", "trans_by": "model"}],
        )
        self._write_cache(
            pdir,
            "pass3_cache/b.json",
            [{"index": 0, "name": "", "pre_src": "x", "post_src": "x",
              "pre_dst": "y", "alt_dst": "z", "trans_by": "model"}],
        )
        status, body = self._req(
            "GET",
            f"/api/projects/{init['project_id']}/alt-translations?file=pass3_cache/a.json",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["alts"][0]["filename"], "pass3_cache/a.json")

    def test_path_traversal_not_leaked(self) -> None:
        # 与 /problems 端点一致：穿越路径不会命中缓存内任何条目，返回空列表（200），不会越权读取
        init = self._init_project(f"alt_trav_{os.urandom(4).hex()}")
        status, body = self._req(
            "GET",
            f"/api/projects/{init['project_id']}/alt-translations?file=..%2F..%2Fevil",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["alts"], [])

    def test_metadata_json_skipped(self) -> None:
        # 非 list 的 json（如引擎元数据）不得被当作条目解析或报错
        init = self._init_project(f"alt_meta_{os.urandom(4).hex()}")
        pdir = init["project_dir"]
        self._write_cache(
            pdir,
            "pass0_cache/meta.json",
            {"version": 1, "note": "这不是条目列表"},  # dict 而非 list
        )
        self._write_cache(
            pdir,
            "pass3_cache/demo.json",
            [{"index": 0, "name": "", "pre_src": "x", "post_src": "x",
              "pre_dst": "y", "alt_dst": "z", "trans_by": "model"}],
        )
        status, body = self._req("GET", f"/api/projects/{init['project_id']}/alt-translations")
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 1)

    def test_empty_project_returns_zero(self) -> None:
        init = self._init_project(f"alt_empty_{os.urandom(4).hex()}")
        status, body = self._req("GET", f"/api/projects/{init['project_id']}/alt-translations")
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["alts"], [])


if __name__ == "__main__":
    unittest.main()
