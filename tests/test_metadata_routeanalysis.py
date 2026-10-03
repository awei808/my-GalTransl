"""路线分析分片元数据端点测试：GET/POST /api/projects/:id/metadata/routeanalysis/:filename。

覆盖：
- GET 不存在 → 200 + exists:false（与 filemeta/batchmeta 口径一致，前端显示"没有元数据条目"）
- GET 预置分片 → 读回内容与磁盘一致（中文路线名 URL 编码）
- POST 保存 → 原子落盘（无 .tmp 残留）、回读一致、中文路线名
- 路径穿越（../ 与裸 ..）→ 400 且不逃逸 route_analysis 目录
- POST 非对象 entry → 400
"""
import importlib
import json
import os
import tempfile
import threading
import unittest
import uuid
import urllib.error
import urllib.request

from GalTransl import server as _server_mod
from GalTransl.server_runtime import encode_project_dir


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


def _tree_has_name(root: str, name: str) -> bool:
    for cur, _dirs, files in os.walk(root):
        if name in files or name in _dirs:
            return True
    return False


class RouteAnalysisMetadataTests(unittest.TestCase):
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
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode() or "{}")

    def _make_project_with_shard(self, shard_name: str, shard: dict) -> tuple[str, str]:
        """创建项目并在 pass0_cache/route_analysis/ 预置分片，返回 (pid, 分片绝对路径)。"""
        name = f"ra_{uuid.uuid4().hex[:10]}"
        _, init = self._req("POST", "/api/projects/init", body={"name": name})
        pid = init["project_id"]
        pdir = init["project_dir"]
        shard_dir = os.path.join(pdir, "transl_cache", "pass0_cache", "route_analysis")
        os.makedirs(shard_dir, exist_ok=True)
        shard_path = os.path.join(shard_dir, f"{shard_name}.json")
        with open(shard_path, "w", encoding="utf-8") as f:
            json.dump(shard, f, ensure_ascii=False, indent=2)
        return pid, shard_path

    def test_get_missing_shard_returns_exists_false(self) -> None:
        pid, _ = self._make_project_with_shard("占位", {"路线名": "占位"})
        status, body = self._req(
            "GET", f"/api/projects/{pid}/metadata/routeanalysis/%E4%B8%8D%E5%AD%98%E5%9C%A8"
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["exists"])
        self.assertIsNone(body["entry"])
        self.assertEqual(body["type"], "routeanalysis")

    def test_get_existing_shard_roundtrip(self) -> None:
        shard = {"路线名": "共通线", "文件列表": ["prologue.txt.json"], "角色列表": [{"名字": "主人公"}]}
        pid, shard_path = self._make_project_with_shard("共通线", shard)
        status, body = self._req(
            "GET", f"/api/projects/{pid}/metadata/routeanalysis/%E5%85%B1%E9%80%9A%E7%BA%BF"
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["exists"])
        self.assertEqual(body["entry"], shard)
        self.assertEqual(body["path"], shard_path)

    def test_post_save_and_atomic_no_tmp_left(self) -> None:
        pid, shard_path = self._make_project_with_shard("线A", {"路线名": "线A"})
        entry = {"路线名": "线A", "文件列表": [], "剧情概要": "手工修正后的概要"}
        status, body = self._req(
            "POST", f"/api/projects/{pid}/metadata/routeanalysis/%E7%BA%BFA",
            body={"entry": entry},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        self.assertEqual(body["type"], "routeanalysis")
        with open(shard_path, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), entry)
        self.assertFalse(os.path.exists(shard_path + ".tmp"), "原子写后不得残留 .tmp")

    def test_post_entry_must_be_object(self) -> None:
        pid, _ = self._make_project_with_shard("线B", {"路线名": "线B"})
        status, _ = self._req(
            "POST", f"/api/projects/{pid}/metadata/routeanalysis/%E7%BA%BFB",
            body={"entry": [1, 2, 3]},
        )
        self.assertEqual(status, 400)

    def test_traversal_rejected_on_get_and_post(self) -> None:
        pid, _ = self._make_project_with_shard("线C", {"路线名": "线C"})
        for method in ("GET", "POST"):
            with self.subTest(method=method):
                status, _ = self._req(
                    method, f"/api/projects/{pid}/metadata/routeanalysis/..%2F..%2Fevil",
                    body={"entry": {"x": 1}} if method == "POST" else None,
                )
                self.assertEqual(status, 400)
        # 裸 .. 段同样拒绝
        status, _ = self._req("GET", f"/api/projects/{pid}/metadata/routeanalysis/..")
        self.assertEqual(status, 400)
        self.assertFalse(_tree_has_name(self.root, "evil.json"))

    def test_missing_filename_rejected(self) -> None:
        pid, _ = self._make_project_with_shard("线D", {"路线名": "线D"})
        status, _ = self._req("GET", f"/api/projects/{pid}/metadata/routeanalysis/")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
