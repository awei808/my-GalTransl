"""Step 1 A+B 端点测试：项目初始化（init）与文件导入（import）。

覆盖核心逻辑（目录布局、去重跳过）与边界（非法项目名、路径遍历、写鉴权）。
依赖真实启动的 server（同 test_server_security_smoke 的 harness 模式）。
"""
import importlib
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from yaml import safe_load

from GalTransl import server as _server_mod
from GalTransl.server_runtime import decode_project_dir, encode_project_dir


def _start_server(workspace_root: str, token: str = ""):
    """在指定 workspace 根下启动测试服务，验证 init/import 行为。"""
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


class HelperUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self._orig = os.environ.get("GALTRANSL_WORKSPACE_ROOT")
        os.environ["GALTRANSL_WORKSPACE_ROOT"] = self.tmp

    def tearDown(self) -> None:
        if self._orig is None:
            os.environ.pop("GALTRANSL_WORKSPACE_ROOT", None)
        else:
            os.environ["GALTRANSL_WORKSPACE_ROOT"] = self._orig

    def test_resolve_rejects_separators(self) -> None:
        # 含分隔符一律拒绝（项目名应为单一路径段）
        for bad in ("a/b", "a\\b", "nested/bad", "a/b/c"):
            with self.assertRaises(ValueError):
                _server_mod._resolve_new_project_dir(bad)

    def test_resolve_rejects_traversal_and_absolute(self) -> None:
        for bad in ("..", "../escape", "/etc/foo", "sub/../x"):
            with self.assertRaises(ValueError):
                _server_mod._resolve_new_project_dir(bad)

    def test_resolve_rejects_empty(self) -> None:
        for bad in ("", "   ", ".", ".."):
            with self.assertRaises(ValueError):
                _server_mod._resolve_new_project_dir(bad)

    def test_create_layout_builds_four_cache_dirs(self) -> None:
        project_dir = os.path.join(self.tmp, "p1")
        created = _server_mod._create_project_layout(project_dir)
        for expected in (
            project_dir,
            os.path.join(project_dir, "gt_input"),
            os.path.join(project_dir, "gt_output"),
            os.path.join(project_dir, "transl_cache"),
            os.path.join(project_dir, "transl_cache", "pass0_cache"),
            os.path.join(project_dir, "transl_cache", "pass1_cache"),
            os.path.join(project_dir, "transl_cache", "pass2_cache"),
            os.path.join(project_dir, "transl_cache", "pass3_cache"),
            os.path.join(project_dir, "config.yaml"),
        ):
            self.assertIn(expected, created)
            self.assertTrue(os.path.exists(expected), f"缺失: {expected}")
        # config.yaml 应可被 yaml 解析
        with open(os.path.join(project_dir, "config.yaml"), encoding="utf-8") as _f:
            self.assertIsInstance(safe_load(_f), dict)


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


class InitEndpointTests(_Base):
    def test_init_creates_layout(self) -> None:
        status, body = self._init_project("demo")
        self.assertEqual(status, 201)
        self.assertIn("project_id", body)
        self.assertTrue(os.path.isdir(body["project_dir"]))
        for sub in ("gt_input", "gt_output", "transl_cache"):
            self.assertTrue(os.path.isdir(os.path.join(body["project_dir"], sub)))
        for pass_n in ("pass0_cache", "pass1_cache", "pass2_cache", "pass3_cache"):
            self.assertTrue(
                os.path.isdir(os.path.join(body["project_dir"], "transl_cache", pass_n)),
                f"缺 {pass_n}",
            )
        self.assertTrue(os.path.isfile(os.path.join(body["project_dir"], "config.yaml")))

    def test_init_name_with_separator_rejected(self) -> None:
        status, _ = self._init_project("nested/bad")
        self.assertEqual(status, 400)

    def test_init_conflict_returns_409(self) -> None:
        self._init_project("dup")
        status, _ = self._init_project("dup")
        self.assertEqual(status, 409)

    def test_init_rejects_empty_name(self) -> None:
        status, _ = self._req("POST", "/api/projects/init", body={"name": ""})
        self.assertEqual(status, 400)

    def test_init_rejects_absolute_name(self) -> None:
        status, _ = self._req("POST", "/api/projects/init", body={"name": "/etc/x"})
        self.assertEqual(status, 400)


class ImportEndpointTests(_Base):
    def _import(self, project_id, body=None, raw=None, content_type=None, headers=None):
        return self._req(
            "POST",
            f"/api/projects/{project_id}/import",
            body=body,
            raw=raw,
            content_type=content_type,
            headers=headers,
        )

    def test_import_writes_and_dedup(self) -> None:
        _, init = self._init_project("imp")
        pid = init["project_id"]
        pdir = init["project_dir"]

        status, body = self._import(pid, body={"filename": "a.txt", "content_b64": "aGVsbG8="})
        self.assertEqual(status, 200)
        self.assertEqual(body["imported"], ["a.txt"])
        self.assertEqual(body["skipped"], [])
        self.assertTrue(os.path.isfile(os.path.join(pdir, "gt_input", "a.txt")))

        # 同名重复导入应跳过，不覆盖
        status2, body2 = self._import(pid, body={"filename": "a.txt", "content_b64": "eHh4"})
        self.assertEqual(status2, 200)
        self.assertEqual(body2["skipped"], ["a.txt"])
        self.assertEqual(body2["imported"], [])
        with open(os.path.join(pdir, "gt_input", "a.txt"), encoding="utf-8") as _f:
            self.assertEqual(_f.read(), "hello")  # 未被覆盖

    def test_import_multipart(self) -> None:
        _, init = self._init_project("impmp")
        pid = init["project_id"]
        pdir = init["project_dir"]
        boundary = "----tb"
        raw = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="b.txt"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
            "WORLD\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        status, body = self._import(pid, raw=raw, content_type=f"multipart/form-data; boundary={boundary}")
        self.assertEqual(status, 200)
        self.assertEqual(body["imported"], ["b.txt"])
        self.assertTrue(os.path.isfile(os.path.join(pdir, "gt_input", "b.txt")))

    def test_import_traversal_filename_skipped(self) -> None:
        _, init = self._init_project("imptrav")
        pid = init["project_id"]
        pdir = init["project_dir"]
        # 路径穿越文件名既被 _is_safe_dict_filename 拒绝，也被 safe_under_project 拒绝
        status, body = self._import(
            pid, body={"filename": "../escaped.txt", "content_b64": "eHh4"}
        )
        self.assertEqual(status, 200)
        self.assertIn("../escaped.txt", body["skipped"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "escaped.txt")))
        self.assertFalse(os.path.exists(os.path.join(pdir, "escaped.txt")))

    def test_import_unknown_project_400(self) -> None:
        bad_id = encode_project_dir(os.path.join(self.tmp, "no-such-project"))
        status, _ = self._import(bad_id, body={"filename": "x.txt", "content_b64": "eA=="})
        self.assertEqual(status, 400)

    def test_import_invalid_base64_400(self) -> None:
        _, init = self._init_project("impbad")
        pid = init["project_id"]
        # 非法 base64 应被捕获并返回 400，而非 500
        status, body = self._import(pid, body={"filename": "a.txt", "content_b64": "!!!notb64!!!"})
        self.assertEqual(status, 400)
        self.assertIn("请求解析失败", body.get("error", ""))

    def test_init_overwrite_rebuilds_layout(self) -> None:
        # 覆盖模式：重建布局、还原默认 config.yaml，但保留用户已有的译文/缓存
        _, init = self._init_project("ow")
        pid = init["project_id"]
        pdir = init["project_dir"]
        user_file = os.path.join(pdir, "gt_input", "keep.txt")
        with open(user_file, "w", encoding="utf-8") as _f:
            _f.write("keep")
        # 篡改 config.yaml，覆盖应还原为合法默认配置
        with open(os.path.join(pdir, "config.yaml"), "w", encoding="utf-8") as _f:
            _f.write("garbage: true\n")
        status, body = self._req(
            "POST", "/api/projects/init", body={"name": "ow", "overwrite": True}
        )
        self.assertEqual(status, 201)
        self.assertIn("project_id", body)
        self.assertTrue(os.path.isfile(user_file), "用户译文不应被覆盖删除")
        with open(os.path.join(pdir, "config.yaml"), encoding="utf-8") as _f:
            self.assertIsInstance(safe_load(_f), dict, "config.yaml 应还原为合法默认配置")


class ImportSourcePathsTests(ImportEndpointTests):
    def test_import_source_paths_writes_files(self) -> None:
        # 后端读取本地选中的真实路径并写入 gt_input（桌面端绕开前端直接操作文件）
        _, init = self._init_project("sp")
        pid = init["project_id"]
        pdir = init["project_dir"]
        src_file = os.path.join(self.tmp, "src_a.txt")
        with open(src_file, "w", encoding="utf-8") as _f:
            _f.write("AAA")
        src_dir = os.path.join(self.tmp, "srcdir")
        os.makedirs(src_dir)
        with open(os.path.join(src_dir, "src_b.txt"), "w", encoding="utf-8") as _f:
            _f.write("BBB")
        status, body = self._import(pid, body={"source_paths": [src_file, src_dir]})
        self.assertEqual(status, 200)
        self.assertEqual(sorted(body["imported"]), ["src_a.txt", "src_b.txt"])
        self.assertTrue(os.path.isfile(os.path.join(pdir, "gt_input", "src_a.txt")))
        self.assertTrue(os.path.isfile(os.path.join(pdir, "gt_input", "src_b.txt")))

    def test_import_source_paths_skips_nonexistent(self) -> None:
        # 不存在的源路径应被忽略，其余正常导入，不报错
        _, init = self._init_project("spne")
        pid = init["project_id"]
        pdir = init["project_dir"]
        src_file = os.path.join(self.tmp, "real.txt")
        with open(src_file, "w", encoding="utf-8") as _f:
            _f.write("X")
        status, body = self._import(
            pid, body={"source_paths": [src_file, os.path.join(self.tmp, "nope.txt")]}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["imported"], ["real.txt"])
        self.assertEqual(body["skipped"], [])

    def test_import_source_paths_flatten_subdir_dedup(self) -> None:
        # 拖入含子目录的文件夹：文件以 basename 扁平写入 gt_input；
        # 不同子目录下同名文件仅保留首个（去重）
        _, init = self._init_project("spflat")
        pid = init["project_id"]
        pdir = init["project_dir"]
        src_dir = os.path.join(self.tmp, "tree")
        os.makedirs(os.path.join(src_dir, "sub1"))
        os.makedirs(os.path.join(src_dir, "sub2"))
        with open(os.path.join(src_dir, "top.txt"), "w", encoding="utf-8") as _f:
            _f.write("T")
        with open(os.path.join(src_dir, "sub1", "dup.txt"), "w", encoding="utf-8") as _f:
            _f.write("S1")
        with open(os.path.join(src_dir, "sub2", "dup.txt"), "w", encoding="utf-8") as _f:
            _f.write("S2")
        status, body = self._import(pid, body={"source_paths": [src_dir]})
        self.assertEqual(status, 200)
        self.assertEqual(sorted(body["imported"]), ["dup.txt", "top.txt"])
        with open(os.path.join(pdir, "gt_input", "dup.txt"), encoding="utf-8") as _f:
            # 去重：不同子目录下同名文件仅保留首个（内容取决于遍历顺序，断言为两者之一）
            self.assertIn(_f.read(), ("S1", "S2"))


class WriteAuthOnNewEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp()
        cls.server, cls.port = _start_server(cls.tmp, token="sectoken")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _post(self, path, body, token=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_init_requires_token(self) -> None:
        self.assertEqual(self._post("/api/projects/init", {"name": "x"}), 401)
        # 正确令牌仍走业务逻辑（项目名非法 -> 400，说明已越过鉴权门）
        self.assertEqual(self._post("/api/projects/init", {"name": ""}, token="sectoken"), 400)

    def test_import_requires_token(self) -> None:
        pid = encode_project_dir(os.path.join(self.tmp, "p"))
        self.assertEqual(
            self._post(f"/api/projects/{pid}/import", {"filename": "a", "content_b64": "eA=="}),
            401,
        )


if __name__ == "__main__":
    unittest.main()
