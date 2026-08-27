"""端点级负例测试：路径穿越防护、pass3 存在性、注释符号一致性。

针对性补充 test_project_init_import / test_dictionary_parse 未覆盖的端点：
- 路径穿越：/api/dictionaries/common/{create,save,delete} 与 /api/projects/:id/cache/save
  （init / import 的穿越已在 test_project_init_import 覆盖，此处不重复）
- pass3 存在性：init 端点必须产出 pass3_cache 目录与示例缓存文件；PASS3_CACHE_DIR 变量须定义
- 注释一致性：注释符号统一为 //，仅行首 // 是注释，#、\\ 不再作为注释，
  行内 // 作为备注列保留（与引擎对齐）
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

from GalTransl import PASS3_CACHE_DIR
from GalTransl import Dictionary as _dict_mod
from GalTransl import server as _server_mod
from GalTransl.server import _is_safe_dict_filename
from GalTransl.server_runtime import encode_project_dir

# 变量存在性检查：PASS3_CACHE_DIR 必须被定义且为预期值（维修计划 A-1 的回归锚点）
assert PASS3_CACHE_DIR == "pass3_cache"


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


def _tree_has_name(root: str, name: str) -> bool:
    """在 root 子树内查找任意名为 name 的文件/目录。"""
    for cur, _dirs, files in os.walk(root):
        if name in files or name in _dirs:
            return True
    return False


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

    def _req(self, method, path, body=None, raw=None, content_type=None):
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
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode() or "{}")

    def _init_project(self, name: str) -> tuple[int, dict]:
        return self._req("POST", "/api/projects/init", body={"name": name})


class PathTraversalTests(_Base):
    """字典 common 三端点 + cache/save 必须拒绝含 .. / 分隔符的文件名。"""

    def test_dict_save_traversal_rejected(self) -> None:
        status, _ = self._req(
            "POST",
            "/api/dictionaries/common/save",
            body={"category": "pre", "filename": "../evil.txt", "content": "pwned"},
        )
        self.assertEqual(status, 400)
        # 不得逃逸到 workspace 任意位置
        self.assertFalse(_tree_has_name(self.root, "evil.txt"))

    def test_dict_save_absolute_rejected(self) -> None:
        status, _ = self._req(
            "POST",
            "/api/dictionaries/common/save",
            body={"category": "pre", "filename": "/etc/evil.txt", "content": "x"},
        )
        self.assertEqual(status, 400)
        self.assertFalse(_tree_has_name(self.root, "evil.txt"))

    def test_dict_create_traversal_rejected(self) -> None:
        status, _ = self._req(
            "POST",
            "/api/dictionaries/common/create",
            body={"category": "pre", "filename": "../../escape.txt"},
        )
        self.assertEqual(status, 400)
        self.assertFalse(_tree_has_name(self.root, "escape.txt"))

    def test_dict_delete_traversal_rejected(self) -> None:
        status, _ = self._req(
            "POST",
            "/api/dictionaries/common/delete",
            body={"filename": "../escape.txt"},
        )
        self.assertEqual(status, 400)
        self.assertFalse(_tree_has_name(self.root, "escape.txt"))

    def test_cache_save_traversal_rejected(self) -> None:
        _, init = self._init_project("trav_cache")
        pid = init["project_id"]
        status, _ = self._req(
            "POST",
            f"/api/projects/{pid}/cache/save",
            body={"filename": "../evil.json", "entries": []},
        )
        self.assertEqual(status, 400)
        self.assertFalse(_tree_has_name(self.root, "evil.json"))


class Pass3ExistenceTests(_Base):
    """pass3 文件/变量存在性：init 必须产出 pass3_cache 目录与示例缓存文件。"""

    def test_pass3_constant_defined(self) -> None:
        # 变量存在性：导入即验证；此处再确认非空且为预期字符串
        self.assertIsInstance(PASS3_CACHE_DIR, str)
        self.assertEqual(PASS3_CACHE_DIR, "pass3_cache")

    def test_init_creates_pass3_cache_and_sample(self) -> None:
        status, body = self._init_project("pass3chk")
        self.assertEqual(status, 201)
        pdir = body["project_dir"]
        pass3_dir = os.path.join(pdir, "transl_cache", PASS3_CACHE_DIR)
        self.assertTrue(os.path.isdir(pass3_dir), "pass3_cache 目录未创建")
        # 示例缓存文件须随 init 一并写入（非空目录）
        self.assertTrue(os.listdir(pass3_dir), "pass3_cache 缺少示例缓存文件")
        # init 响应的 created 列表应显式包含 pass3_cache 路径
        self.assertTrue(
            any(PASS3_CACHE_DIR in created for created in body["created"]),
            "created 列表未包含 pass3_cache",
        )


class CommentSymbolConsistencyTests(_Base):
    """注释符号统一为 //：仅行首 // 是注释，# 与 \\ 不再作为注释（纯函数 + 端点双层）。"""

    def test_only_double_slash_is_comment(self) -> None:
        # // 仍是注释
        self.assertEqual(_dict_mod.parse_dict_line("// 注释", "pre").type, "comment")
        # # 与反斜杠不再是注释，作为普通词条解析
        self.assertEqual(_dict_mod.parse_dict_line("# 注释", "pre").type, "normal")
        self.assertEqual(_dict_mod.parse_dict_line("\\注释", "pre").type, "normal")

    def test_double_slash_only_at_line_start(self) -> None:
        # // 仅行首起效；内容/字段中的 // 不是注释
        self.assertEqual(_dict_mod.parse_dict_line("// 猫|狗", "pre").type, "comment")
        self.assertEqual(_dict_mod.parse_dict_line("词|//备注", "pre").type, "normal")

    def test_parse_endpoint_comment_consistent(self) -> None:
        content = "# 注释一\n// 注释二\n\\注释三\n词|替换|//备注\n搜索|替换\n"
        status, body = self._req(
            "POST", "/api/dictionaries/parse", body={"content": content, "category": "pre"}
        )
        self.assertEqual(status, 200)
        types = [r["type"] for r in body["rows"]]
        # #、\ 开头按普通词条；// 行首是注释；行内 // 是备注列
        self.assertEqual(
            types,
            ["normal", "comment", "normal", "normal", "normal", "blank"],
        )


class SafeDictFilenameUnitTests(unittest.TestCase):
    """_is_safe_dict_filename 单测：覆盖正常 / 边界 / 异常输入。"""

    def test_normal_basename_allowed(self) -> None:
        self.assertTrue(_is_safe_dict_filename("pre.txt"))
        self.assertTrue(_is_safe_dict_filename("  my_dict.txt  "))  # 允许前后空白

    def test_separators_rejected(self) -> None:
        for bad in ("../x.txt", "a/b.txt", "a\\b.txt", "/abs.txt", "C:\\x.txt"):
            with self.subTest(bad=bad):
                self.assertFalse(_is_safe_dict_filename(bad))

    def test_dot_and_dotdot_rejected(self) -> None:
        # F6 回归：裸 . / .. 段会解析到字典目录父级，必须拒绝
        self.assertFalse(_is_safe_dict_filename("."))
        self.assertFalse(_is_safe_dict_filename(".."))
        self.assertFalse(_is_safe_dict_filename(" .. "))

    def test_empty_and_nonstring_rejected(self) -> None:
        self.assertFalse(_is_safe_dict_filename(""))
        self.assertFalse(_is_safe_dict_filename("   "))
        self.assertFalse(_is_safe_dict_filename(None))  # type: ignore[arg-type]
        self.assertFalse(_is_safe_dict_filename(123))  # type: ignore[arg-type]


class DictBareDotDotTraversalTests(_Base):
    """字典端点对裸 .. 段的拒绝（F6 集成回归）。"""

    def _assert_rejected_and_no_escape(self, body: dict) -> None:
        status, _ = self._req("POST", "/api/dictionaries/common/save", body=body)
        self.assertEqual(status, 400)
        self.assertFalse(_tree_has_name(self.root, ".."))

    def test_bare_dotdot_save_rejected(self) -> None:
        self._assert_rejected_and_no_escape(
            {"category": "pre", "filename": "..", "content": "x"}
        )

    def test_bare_dotdot_create_rejected(self) -> None:
        status, _ = self._req(
            "POST", "/api/dictionaries/common/create", body={"category": "pre", "filename": ".."}
        )
        self.assertEqual(status, 400)

    def test_bare_dotdot_delete_rejected(self) -> None:
        status, _ = self._req(
            "POST", "/api/dictionaries/common/delete", body={"filename": ".."}
        )
        self.assertEqual(status, 400)


class CrLfParseConsistencyTests(_Base):
    """parse 端点须与引擎一致地剥离 \\r（F5）。"""

    def test_crlf_values_have_no_carriage_return(self) -> None:
        content = "搜索|替换\r\nfoo|bar\r\n"
        status, body = self._req(
            "POST", "/api/dictionaries/parse", body={"content": content, "category": "pre"}
        )
        self.assertEqual(status, 200)
        for row in body["rows"]:
            for value in row["values"]:
                self.assertNotIn("\r", value, "CRLF 回车符泄漏进解析值")

    def test_crlf_comment_prefix_detected(self) -> None:
        # CRLF 下仅 // 判为注释
        content = "# 注释\r\n// 注释二\r\n\\注释三\r\n"
        status, body = self._req(
            "POST", "/api/dictionaries/parse", body={"content": content, "category": "pre"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            [r["type"] for r in body["rows"][:3]], ["normal", "comment", "normal"]
        )


class CommentPrefixSingleSourceTests(unittest.TestCase):
    """_COMMENT_PREFIXES 应为单一事实源，且仅含 //（注释符号统一）。"""

    def test_constant_only_double_slash(self) -> None:
        self.assertEqual(_dict_mod._COMMENT_PREFIXES, ("//",))

    def test_parse_dict_line_uses_prefixes(self) -> None:
        for prefix in _dict_mod._COMMENT_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertEqual(_dict_mod.parse_dict_line(f"{prefix} 注释", "pre").type, "comment")


class WriteAuthMethodLevelTests(unittest.TestCase):
    """写鉴权在 HTTP 方法级门禁（POST/PUT/DELETE 须令牌）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp()
        cls.server, cls.port = _start_server(cls.tmp, token="secret")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _req(self, method, path, body=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode() or "{}")

    def test_post_without_token_rejected(self) -> None:
        status, _ = self._req("POST", "/api/dictionaries/common/save",
                              {"category": "pre", "filename": "x.txt", "content": "a"})
        self.assertEqual(status, 401)

    def test_post_with_wrong_token_rejected(self) -> None:
        url = f"http://127.0.0.1:{self.port}/api/dictionaries/common/save"
        req = urllib.request.Request(url, data=json.dumps(
            {"category": "pre", "filename": "x.txt", "content": "a"}).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer wrong")
        try:
            urllib.request.urlopen(req)
            self.fail("应返回 401")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 401)

    def test_post_with_valid_token_passes_gate(self) -> None:
        # 令牌正确时应越过鉴权门禁，进入后续校验；
        # 用越界文件名触发 400（而非 401），证明鉴权已通过
        url = f"http://127.0.0.1:{self.port}/api/dictionaries/common/save"
        req = urllib.request.Request(url, data=json.dumps(
            {"category": "pre", "filename": "../x.txt", "content": "a"}).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer secret")
        try:
            urllib.request.urlopen(req)
            self.fail("越界文件名应返回 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)


class MetadataFilenameTraversalTests(_Base):
    """metadata 端点必须对 :filename 做穿越防护（与字典端点一致，F7）。"""

    def _pid(self) -> str:
        # 每个用例用独立项目名，避免重复 init 因项目已存在而返回非 project_id 体
        _, init = self._init_project(f"meta_trav_{uuid.uuid4().hex[:10]}")
        return init["project_id"]

    def test_filemeta_save_traversal_rejected(self) -> None:
        # ../ 经 URL 编码为单段，后端 unquote 后还原为 ../../evil，须 400
        pid = self._pid()
        status, _ = self._req(
            "POST",
            f"/api/projects/{pid}/metadata/filemeta/..%2F..%2Fevil",
            body={"entry": {"id": "x"}},
        )
        self.assertEqual(status, 400)
        self.assertFalse(_tree_has_name(self.root, "evil.meta.json"))

    def test_filemeta_save_bare_dotdot_rejected(self) -> None:
        pid = self._pid()
        status, _ = self._req(
            "POST",
            f"/api/projects/{pid}/metadata/filemeta/..",
            body={"entry": {"id": "x"}},
        )
        self.assertEqual(status, 400)

    def test_batchmeta_save_traversal_rejected(self) -> None:
        pid = self._pid()
        status, _ = self._req(
            "POST",
            f"/api/projects/{pid}/metadata/batchmeta/..%2F..%2Fevil",
            body={"entry": {"id": "x"}},
        )
        self.assertEqual(status, 400)
        self.assertFalse(_tree_has_name(self.root, "evil.batch.json"))

    def test_filemeta_get_traversal_rejected(self) -> None:
        pid = self._pid()
        status, _ = self._req(
            "GET", f"/api/projects/{pid}/metadata/filemeta/..%2F..%2Fevil"
        )
        self.assertEqual(status, 400)

    def test_normal_filename_allowed(self) -> None:
        # 合法文件名（含 .txt.json 后缀）应正常通过，证明防护不误伤
        pid = self._pid()
        status, _ = self._req(
            "POST",
            f"/api/projects/{pid}/metadata/filemeta/scenario01.txt.json",
            body={"entry": {"id": "scenario01.txt.json"}},
        )
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
