"""使用指南端点测试（/api/guides 与 /api/guides/:filename）。

口径：
- 列表只收 guides/ 目录下的 .md 文件（排序、忽略子目录与其他扩展名）；
- 单篇读取走文件名白名单（_read_guide 只认 _list_guides 的结果），
  不存在的名字、非 md 文件、含路径分隔符或 .. 的名字一律 404。

resolve_app_dir 被 patch 到临时目录，不依赖仓库内真实 guides/ 内容。
"""
import importlib
import json
import os
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request

from GalTransl import server as _server_mod


def _start_server():
    importlib.reload(_server_mod)
    registry = _server_mod.JobRegistry()
    srv = _server_mod.ThreadingHTTPServer(
        ("127.0.0.1", 0), _server_mod.build_handler(registry)
    )
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


class GuidesEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp()
        cls.patcher = unittest.mock.patch(
            "GalTransl.server_meta.resolve_app_dir", return_value=cls.tmp
        )
        cls.patcher.start()
        cls.server, cls.port = _start_server()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.patcher.stop()

    def _req(self, path: str) -> "tuple[int, dict]":
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(body)
            except Exception:
                return exc.code, {"raw": body}

    def _write(self, name: str, content: str) -> None:
        os.makedirs(os.path.join(self.tmp, "guides"), exist_ok=True)
        with open(os.path.join(self.tmp, "guides", name), "w", encoding="utf-8") as f:
            f.write(content)

    def test_missing_dir_returns_empty_list(self) -> None:
        # 独立空目录（无 guides/ 子目录），避免与本类其他用例写入的文件相互影响
        with tempfile.TemporaryDirectory() as empty_root:
            with unittest.mock.patch(
                "GalTransl.server_meta.resolve_app_dir", return_value=empty_root
            ):
                status, payload = self._req("/api/guides")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"guides": []})

    def test_list_only_md_files_sorted(self) -> None:
        self._write("b.md", "# B")
        self._write("a.md", "# A")
        self._write("note.txt", "not a guide")
        os.makedirs(os.path.join(self.tmp, "guides", "sub"), exist_ok=True)
        status, payload = self._req("/api/guides")
        self.assertEqual(status, 200)
        self.assertEqual(payload["guides"], ["a.md", "b.md"])

    def test_read_guide_returns_content(self) -> None:
        self._write("hello.md", "# 你好\n\n正文内容。")
        status, payload = self._req("/api/guides/hello.md")
        self.assertEqual(status, 200)
        self.assertEqual(payload["name"], "hello.md")
        self.assertIn("正文内容", payload["content"])

    def test_read_rejects_non_listed_names(self) -> None:
        self._write("real.md", "# Real")
        for name in ("missing.md", "note.txt", "..%2Freal.md", "a%2Fb.md"):
            with self.subTest(name=name):
                status, _ = self._req(f"/api/guides/{name}")
                self.assertEqual(status, 404)

    def test_read_rejects_encoded_traversal(self) -> None:
        # %2e%2e%2f = ../ —— 白名单口径下必须 404 而非读到目录外文件
        status, _ = self._req("/api/guides/%2e%2e%2fsecret.md")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
