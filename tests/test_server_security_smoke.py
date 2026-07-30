import importlib
import json
import os
import threading
import unittest
import urllib.error
import urllib.request

from GalTransl import server as _server_mod


def _start_server_with_token(token: str):
    """按指定令牌重载模块并启动测试服务，验证不同配置下的行为。"""
    if token == "":
        os.environ.pop("GALTRANSL_API_TOKEN", None)
    else:
        os.environ["GALTRANSL_API_TOKEN"] = token
    importlib.reload(_server_mod)
    registry = _server_mod.JobRegistry()
    srv = _server_mod.ThreadingHTTPServer(
        ("127.0.0.1", 0), _server_mod.build_handler(registry)
    )
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


class _Base(unittest.TestCase):
    TOKEN = ""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server, cls.port = _start_server_with_token(cls.TOKEN)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _req(self, method, path, origin=None, token=None, body=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if origin:
            req.add_header("Origin", origin)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers)


class DefaultOpenTests(_Base):
    """向后兼容：未配置令牌时行为与原先一致（写操作不鉴权）。"""

    TOKEN = ""

    def test_write_allowed_without_token(self) -> None:
        status, _ = self._req("POST", "/api/openai-models", body={"endpoint": "http://127.0.0.1:9"})
        self.assertNotEqual(status, 401)

    def test_cors_allows_desktop_origin(self) -> None:
        status, headers = self._req("GET", "/api/version", origin="tauri://localhost")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "tauri://localhost")

    def test_cors_denies_arbitrary_origin(self) -> None:
        status, headers = self._req("GET", "/api/version", origin="https://evil.example.com")
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_preflight_options_no_auth(self) -> None:
        # 预检请求不得被写鉴权拦截，否则浏览器将拒绝实际跨域写请求
        status, headers = self._req("OPTIONS", "/api/openai-models", origin="tauri://localhost")
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "tauri://localhost")


class TokenRequiredTests(_Base):
    """SEC-1 写端点鉴权：配置令牌后，POST/PUT/DELETE 必须携带正确 Bearer。"""

    TOKEN = "testtoken"

    def test_post_requires_token(self) -> None:
        status, _ = self._req("POST", "/api/openai-models", body={"endpoint": "http://127.0.0.1:9"})
        self.assertEqual(status, 401)

    def test_post_with_valid_token(self) -> None:
        status, _ = self._req(
            "POST",
            "/api/openai-models",
            token="testtoken",
            body={"endpoint": "http://127.0.0.1:9"},
        )
        self.assertNotEqual(status, 401)

    def test_post_with_wrong_token(self) -> None:
        status, _ = self._req(
            "POST",
            "/api/openai-models",
            token="wrong",
            body={"endpoint": "http://127.0.0.1:9"},
        )
        self.assertEqual(status, 401)

    def test_put_requires_token(self) -> None:
        status, _ = self._req("PUT", "/api/app-settings", body={"theme": "dark"})
        self.assertEqual(status, 401)

    def test_delete_requires_token(self) -> None:
        status, _ = self._req("DELETE", "/api/backend-profiles/nonexistent")
        self.assertEqual(status, 401)

    def test_get_not_gated_by_token(self) -> None:
        # 读操作即使令牌已配置也不应被拦截
        status, _ = self._req("GET", "/api/version")
        self.assertEqual(status, 200)

    def test_unknown_post_requires_token_first(self) -> None:
        # 鉴权门应先于路由，未知路径的写请求同样需令牌
        status, _ = self._req("POST", "/api/does-not-exist")
        self.assertEqual(status, 401)

    def test_preflight_options_no_auth_with_token(self) -> None:
        status, headers = self._req("OPTIONS", "/api/openai-models", origin="tauri://localhost")
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "tauri://localhost")


if __name__ == "__main__":
    unittest.main()
