"""D端字典解析：parse_dict_line 纯函数单测 + POST /api/dictionaries/parse 端点集成。

验证前端 dictUtils.parseDictContent 与后端 Dictionary.parse_dict_line 行为一致。
"""
import importlib
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import asdict

from GalTransl import Dictionary as _dict_mod
from GalTransl import server as _server_mod
from GalTransl.Dictionary import parse_dict_line, DictRow, CBasicDicElement


# ---- 纯函数单测 ----
class ParseDictLineTests(unittest.TestCase):
    def test_normal(self) -> None:
        r = parse_dict_line("搜索|替换", "pre")
        self.assertEqual(r.type, "normal")
        self.assertEqual(r.values, ["搜索", "替换", ""])
        self.assertEqual(r.raw, "搜索|替换")

    def test_conditional(self) -> None:
        r = parse_dict_line("pre_src|含甲|猫|猫娘", "pre")
        self.assertEqual(r.type, "conditional")
        self.assertEqual(r.values, ["pre_src", "含甲", "猫", "猫娘", ""])

    def test_situation(self) -> None:
        r = parse_dict_line("diag|台詞|独白", "pre")
        self.assertEqual(r.type, "situation")
        self.assertEqual(r.values, ["diag", "台詞", "独白"])

    def test_gpt(self) -> None:
        r = parse_dict_line("src|dst|note here", "gpt")
        self.assertEqual(r.type, "gpt")
        self.assertEqual(r.values, ["src", "dst", "note here"])

    def test_comment(self) -> None:
        self.assertEqual(parse_dict_line("// note", "pre").type, "comment")
        self.assertEqual(parse_dict_line("# note", "pre").type, "comment")
        self.assertEqual(parse_dict_line("\\\\note", "pre").type, "comment")

    def test_blank(self) -> None:
        self.assertEqual(parse_dict_line("   ", "pre").type, "blank")
        self.assertEqual(parse_dict_line("", "pre").type, "blank")

    def test_single_field_is_normal_empty_replace(self) -> None:
        # 单部分行解析为 normal，replace 为空（保留与前端一致的结构）
        r = parse_dict_line("onlysearch", "pre")
        self.assertEqual(r.type, "normal")
        self.assertEqual(r.values, ["onlysearch", "", ""])

    def test_dictrow_shape(self) -> None:
        d = asdict(parse_dict_line("a|b", "pre"))
        self.assertEqual(set(d.keys()), {"type", "values", "raw"})

    def test_comment_with_pipe_treated_as_rule(self) -> None:
        # 含 | 的 // 行引擎按规则加载，编辑器须一致，不得误判为注释
        r = parse_dict_line("// 猫|狗 备注", "pre")
        self.assertEqual(r.type, "normal")
        self.assertEqual(r.values[0], "// 猫")

    def test_escape_handling_applied(self) -> None:
        # 与引擎一致：每字段做转义处理（\n -> 真实换行）
        r = parse_dict_line("猫\\n|狗", "pre")
        self.assertEqual(r.values[0], "猫\n")
        self.assertEqual(r.values[1], "狗")

    def test_escape_lone_backslash_fallback(self) -> None:
        # 孤立反斜杠转义失败应回退原文而非抛异常
        r = parse_dict_line("a\\|b", "pre")
        self.assertEqual(r.values[0], "a\\")
        self.assertEqual(r.values[1], "b")


class LoadLineTests(unittest.TestCase):
    def test_load_line_normal(self) -> None:
        el = CBasicDicElement()
        res = el.load_line("搜索|替换", "pre")
        self.assertIs(res, el)
        self.assertEqual(el.search_word, "搜索")
        self.assertEqual(el.replace_word, "替换")

    def test_load_line_startswith_flag(self) -> None:
        el = CBasicDicElement()
        el.load_line("^^前缀|替换", "pre")
        self.assertTrue(el.startswith_flag)
        self.assertEqual(el.search_word, "前缀")

    def test_load_line_onetime_flag(self) -> None:
        el = CBasicDicElement()
        el.load_line("1^短语|替换", "pre")
        self.assertTrue(el.onetime_flag)
        self.assertEqual(el.search_word, "短语")

    def test_load_line_blank_returns_none(self) -> None:
        self.assertIsNone(CBasicDicElement().load_line("   ", "pre"))

    def test_load_line_comment_returns_none(self) -> None:
        self.assertIsNone(CBasicDicElement().load_line("// x", "pre"))

    def test_load_line_conditional(self) -> None:
        el = CBasicDicElement()
        el.load_line("pre_src|含甲|猫|猫娘", "pre")
        self.assertTrue(el.is_conditionaDic)
        self.assertEqual(el.search_word, "猫")
        self.assertEqual(el.replace_word, "猫娘")
        self.assertEqual(len(el.if_word_list), 1)

    def test_load_line_situation(self) -> None:
        el = CBasicDicElement()
        el.load_line("diag|台詞|独白", "pre")
        self.assertTrue(el.is_situationsDic)
        self.assertEqual(el.special_key, "diag")
        self.assertEqual(el.search_word, "台詞")


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


class ParseEndpointTests(_Base):
    def test_parse_returns_rows(self) -> None:
        content = "搜索|替换\npre_src|含甲|猫|猫娘\n// comment\n"
        status, body = self._req(
            "POST", "/api/dictionaries/parse", body={"content": content, "category": "pre"}
        )
        self.assertEqual(status, 200)
        rows = body["rows"]
        self.assertEqual(rows[0], {"type": "normal", "values": ["搜索", "替换", ""], "raw": "搜索|替换"})
        self.assertEqual(rows[1]["type"], "conditional")
        self.assertEqual(rows[2]["type"], "comment")

    def test_parse_matches_local_function(self) -> None:
        content = "a|b\nc|d|e\n"
        status, body = self._req(
            "POST", "/api/dictionaries/parse", body={"content": content, "category": "post"}
        )
        self.assertEqual(status, 200)
        expected = [asdict(parse_dict_line(line, "post")) for line in content.split("\n")]
        self.assertEqual(body["rows"], expected)

    def test_parse_gpt_category(self) -> None:
        status, body = self._req(
            "POST", "/api/dictionaries/parse", body={"content": "src|dst|note", "category": "gpt"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["rows"][0], {"type": "gpt", "values": ["src", "dst", "note"], "raw": "src|dst|note"})

    def test_parse_category_invalid(self) -> None:
        status, _ = self._req(
            "POST", "/api/dictionaries/parse", body={"content": "a|b", "category": "weird"}
        )
        self.assertEqual(status, 400)

    def test_parse_empty_content(self) -> None:
        status, body = self._req(
            "POST", "/api/dictionaries/parse", body={"content": "", "category": "pre"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["rows"], [{"type": "blank", "values": [], "raw": ""}])

    def test_parse_bad_json(self) -> None:
        status, _ = self._req("POST", "/api/dictionaries/parse", raw=b"{not json", content_type="application/json")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
