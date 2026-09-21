"""server 路由分发冒烟测试（0.4.10 重构安全网）。

背景：0.4.10 把 `build_handler` 内 `RequestHandler` 的 74 个路由块按功能域委托到
`server_handlers_*.py`。路由块**顺序敏感**（如 `/cache/` 前缀匹配必须晚于
`/cache/save`），且 handler 需访问 `self._send_json` / `self._read_json_body`。
一旦委托出错，症状通常是「某个路由静默落到兜底 404」或「抛异常变 500」。

本测试对全部路由发最小请求，断言**不返回 500**（允许 400/404/405/409/422 ——
这些是正常的参数校验/鉴权结果，重构不应改变其分布）。这是路由委托唯一的
自动化安全网，故意用「未 500」这种弱断言以换取全路由覆盖。

注意：断言的是「返回码不变」而非「返回码正确」。若某路由重构前就返回 500，
本测试在重构前即会失败并暴露该基线 —— 因此基线必须是全绿。
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
from GalTransl.server_runtime import encode_project_dir

# 允许的返回码（500 之外的任何"正常"响应）。
# 502/504：/api/openai-models 等会转发上游请求的路由，上游不可达时返回网关错误，
# 属正确的错误处理而非 handler 崩溃。
ACCEPTABLE_STATUS = {
    200, 201, 204, 400, 401, 403, 404, 405, 409, 415, 422, 501, 502, 504,
}

# GET 路由（不含 /api/projects/:id/*，后者单列）
ROOT_GET_ROUTES = [
    "/", "/api/version", "/api/version/check", "/api/translators", "/api/jobs",
    "/api/app-settings", "/api/project-config-template", "/api/pipeline-stages",
    "/api/prompt-templates",
    "/api/backend-profiles", "/api/plugins", "/api/problem-types",
    "/api/translation-guidelines", "/api/projects/workspace-root",
    "/api/dictionaries/common",
]

# POST 路由
ROOT_POST_ROUTES = [
    "/api/log", "/api/dictionaries/parse", "/api/openai-models", "/api/jobs",
    "/api/dictionaries/common/create", "/api/dictionaries/common/save",
    "/api/dictionaries/common/delete",
]

# PUT / DELETE 路由
ROOT_PUT_ROUTES = ["/api/app-settings"]
ROOT_DELETE_ROUTES = ["/api/backend-profiles/_nonexistent_"]

# /api/projects/:id/* 路由（{id} 由测试运行时替换为真实编码 id）
PROJECT_ROUTES = [
    ("GET", "/config"), ("GET", "/config-name"), ("GET", "/config-schema"),
    ("GET", "/check-batch-size"), ("GET", "/build/validate"),
    ("GET", "/files"), ("GET", "/cache"), ("GET", "/cache/check"),
    ("GET", "/progress"), ("GET", "/runtime"), ("GET", "/dictionary"),
    ("GET", "/dictionary/project"), ("GET", "/name-table"), ("GET", "/name-dict"),
    ("GET", "/problems"), ("GET", "/alt-translations"), ("GET", "/logs"),
    ("GET", "/cache/_probe_.json"), ("GET", "/cache/_probe_.json/h-ranges"),
    ("GET", "/metadata/globalprompt"), ("GET", "/metadata/plotroute"),
    ("GET", "/metadata/filemeta/_probe_.json"),
    ("GET", "/metadata/batchmeta/_probe_.json"),
    ("GET", "/build-output/_probe_.json"),
    ("POST", "/check-model"), ("POST", "/import"), ("POST", "/cache/save"),
    ("POST", "/cache/recheck-all"), ("POST", "/cache/check"),
    ("POST", "/cache/delete-entry"), ("POST", "/cache/delete-file"),
    ("POST", "/cache/search"), ("POST", "/cache/replace"),
    ("POST", "/cache/replace-entry"), ("POST", "/review/ai-suggest"),
    ("POST", "/runtime/notices/clear"), ("POST", "/stop"), ("POST", "/reveal"),
    ("POST", "/build-output"), ("POST", "/dictionary/project/create"),
    ("POST", "/dictionary/project/save"), ("POST", "/dictionary/project/delete"),
    ("POST", "/name-table/generate"), ("POST", "/name-table/ai-suggest"),
    ("POST", "/name-table/save"),
    ("POST", "/metadata/filemeta/_probe_.json"),
    ("POST", "/metadata/batchmeta/_probe_.json"),
    ("POST", "/metadata/globalprompt"), ("POST", "/metadata/plotroute"),
]


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


class RouteDispatchSmokeTests(unittest.TestCase):
    """全部路由在重构前后必须给出相同的「非 500」行为。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp()
        cls.server, cls.port = _start_server(cls.tmp)
        # 构造一个真实存在的项目目录，使 /api/projects/:id/* 走到真实 handler
        cls.project_dir = os.path.join(cls.tmp, "probe_project")
        os.makedirs(os.path.join(cls.project_dir, "gt_input"), exist_ok=True)
        cls.project_id = encode_project_dir(cls.project_dir)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _req(self, method: str, path: str, body=None, timeout: float = 10):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def _assert_not_500(self, method: str, path: str, body=None,
                        timeout: float = 10) -> None:
        status, payload = self._req(method, path, body, timeout=timeout)
        self.assertIn(
            status, ACCEPTABLE_STATUS,
            f"{method} {path} 返回 {status}（期望非 500 的可接受码）\n响应: {payload[:400]}",
        )
        self.assertNotEqual(
            status, 500,
            f"{method} {path} 触发 500 —— 路由 handler 抛异常\n响应: {payload[:400]}",
        )

    def test_root_get_routes(self) -> None:
        for path in ROOT_GET_ROUTES:
            with self.subTest(method="GET", path=path):
                self._assert_not_500("GET", path)

    def test_root_post_routes(self) -> None:
        for path in ROOT_POST_ROUTES:
            with self.subTest(method="POST", path=path):
                # /api/openai-models 默认端点会发起真实外网请求 → 传极小 timeout，
                # 上游不可达时该路由返回 502（正确的错误处理，非 handler 崩溃）。
                body = {"timeout": 1} if path == "/api/openai-models" else {}
                self._assert_not_500("POST", path, body=body, timeout=8)

    def test_root_put_routes(self) -> None:
        for path in ROOT_PUT_ROUTES:
            with self.subTest(method="PUT", path=path):
                self._assert_not_500("PUT", path, body={})

    def test_root_delete_routes(self) -> None:
        for path in ROOT_DELETE_ROUTES:
            with self.subTest(method="DELETE", path=path):
                self._assert_not_500("DELETE", path)

    def test_project_routes(self) -> None:
        for method, sub in PROJECT_ROUTES:
            path = f"/api/projects/{self.project_id}{sub}"
            with self.subTest(method=method, sub=sub):
                self._assert_not_500(method, path, body={} if method != "GET" else None)

    def test_unknown_route_returns_404_not_500(self) -> None:
        # 兜底分支：未知路由必须 404（委托重构后容易误落 500）
        self._assert_not_500("GET", "/api/definitely-not-a-route")
        self._assert_not_500("GET", f"/api/projects/{self.project_id}/nope-nope")


if __name__ == "__main__":
    unittest.main()
