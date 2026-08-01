"""构建输出换行统一：POST /api/projects/:id/build-output 时译文换行按原文换行符类型转换。"""
import importlib
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from GalTransl import server as _server_mod


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

    def _write_json(self, path: str, data) -> None:
        import orjson

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))


class BuildOutputNewlineTests(_Base):
    def _run(self, name: str, input_data: list, cache_entries: list):
        _, init = self._init_project(name)
        pid = init["project_id"]
        pdir = init["project_dir"]
        cache_name = "book.txt.json"
        self._write_json(os.path.join(pdir, "gt_input", cache_name), input_data)
        self._write_json(
            os.path.join(pdir, "transl_cache", cache_name), cache_entries
        )
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/build-output",
            body={"filenames": [cache_name]},
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(body["success"], body)
        with open(
            os.path.join(pdir, "gt_output", cache_name), encoding="utf-8"
        ) as f:
            return json.load(f)

    def test_newline_normalized_to_crlf(self) -> None:
        # 原文用 \r\n、译文用 \n：输出 message 换行应统一为 \r\n
        out = self._run(
            "bo_crlf",
            [{"message": "実在するモデルを使って、\r\nより魅力的に見せることが出来るのか。"}],
            [
                {
                    "index": 1,
                    "name": "",
                    "pre_src": "実在するモデルを使って、\r\nより魅力的に見せることが出来るのか。",
                    "post_src": "実在するモデルを使って、\r\nより魅力的に見せることが出来るのか。",
                    "pre_dst": "怎样让真实的模特摆出姿势，\n才能看起来更有魅力。",
                }
            ],
        )
        self.assertEqual(out[0]["message"], "怎样让真实的模特摆出姿势，\r\n才能看起来更有魅力。")

    def test_no_newline_in_src_keeps_dst(self) -> None:
        # 原文无换行：译文换行保持不变（不强行转换）
        out = self._run(
            "bo_none",
            [{"message": "こんにちは"}],
            [
                {
                    "index": 1,
                    "name": "",
                    "pre_src": "こんにちは",
                    "post_src": "こんにちは",
                    "pre_dst": "你好\n世界",
                }
            ],
        )
        self.assertEqual(out[0]["message"], "你好\n世界")

    def test_literal_newline_src_converts_to_literal(self) -> None:
        # 原文存字面 "\\n"（反斜杠+n 文本）：译文真实换行应转成字面形式
        out = self._run(
            "bo_lit",
            [{"message": "A\\nB"}],
            [
                {
                    "index": 1,
                    "name": "",
                    "pre_src": "A\\nB",
                    "post_src": "A\\nB",
                    "pre_dst": "甲\n乙",
                }
            ],
        )
        self.assertEqual(out[0]["message"], "甲\\n乙")

    def test_unmatched_message_keeps_src(self) -> None:
        # 缓存无对应 pre_src：message 保持原文
        out = self._run(
            "bo_unmatch",
            [{"message": "未翻译的句子"}],
            [
                {
                    "index": 1,
                    "name": "",
                    "pre_src": "别的原文",
                    "post_src": "别的原文",
                    "pre_dst": "别的译文",
                }
            ],
        )
        self.assertEqual(out[0]["message"], "未翻译的句子")

    def test_full_build_recurses_nested_cache(self) -> None:
        # 全量构建（无 filenames）：递归找到 pass3_cache 下的翻译缓存并正确输出
        _, init = self._init_project("bo_nested")
        pid = init["project_id"]
        pdir = init["project_dir"]
        self._write_json(
            os.path.join(pdir, "gt_input", "book.txt.json"),
            [{"message": "実在するモデルを使って、\r\nより魅力的に見せることが出来るのか。"}],
        )
        self._write_json(
            os.path.join(pdir, "transl_cache", "pass3_cache", "book.txt.json"),
            [
                {
                    "index": 1,
                    "name": "",
                    "pre_src": "実在するモデルを使って、\r\nより魅力的に見せることが出来るのか。",
                    "post_src": "実在するモデルを使って、\r\nより魅力的に見せることが出来るのか。",
                    "pre_dst": "怎样让真实的模特摆出姿势，\n才能看起来更有魅力。",
                }
            ],
        )
        status, body = self._req("POST", f"/api/projects/{pid}/build-output", body={})
        self.assertEqual(status, 200, body)
        self.assertIn("book.txt.json", body["built_files"], body)
        with open(os.path.join(pdir, "gt_output", "book.txt.json"), encoding="utf-8") as f:
            out = json.load(f)
        self.assertEqual(out[0]["message"], "怎样让真实的模特摆出姿势，\r\n才能看起来更有魅力。")

    def test_full_build_skips_metadata(self) -> None:
        # 全量构建跳过 *.meta.json / *.batch.json / GlobalPrompt.json，不构建也不报错
        _, init = self._init_project("bo_meta")
        pid = init["project_id"]
        pdir = init["project_dir"]
        self._write_json(os.path.join(pdir, "gt_input", "book.txt.json"), [{"message": "甲"}])
        self._write_json(
            os.path.join(pdir, "transl_cache", "pass3_cache", "book.txt.json"),
            [{"index": 1, "name": "", "pre_src": "甲", "post_src": "甲", "pre_dst": "乙"}],
        )
        self._write_json(
            os.path.join(pdir, "transl_cache", "pass1_cache", "book.meta.json"), {"id": "x"}
        )
        self._write_json(
            os.path.join(pdir, "transl_cache", "pass2_cache", "book.batch.json"), [{"id": "x"}]
        )
        self._write_json(
            os.path.join(pdir, "transl_cache", "pass0_cache", "GlobalPrompt.json"), {"id": "x"}
        )
        status, body = self._req("POST", f"/api/projects/{pid}/build-output", body={})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["built_files"], ["book.txt.json"], body)
        self.assertEqual(body["errors"], [], body)


class BuildValidateTests(_Base):
    def _validate(self, name: str, input_files: dict, cache_files: dict):
        _, init = self._init_project(name)
        pid = init["project_id"]
        pdir = init["project_dir"]
        for rel, data in input_files.items():
            self._write_json(os.path.join(pdir, "gt_input", rel), data)
        for rel, data in cache_files.items():
            self._write_json(os.path.join(pdir, "transl_cache", rel), data)
        status, body = self._req("POST", f"/api/projects/{pid}/build/validate", body={})
        self.assertEqual(status, 200, body)
        return body

    def test_validate_lists_missing_cache_files(self) -> None:
        # 有 2 个输入、仅 1 个缓存：缺失文件应列出
        body = self._validate(
            "bv_miss",
            {"a.txt.json": [{"message": "甲"}], "b.txt.json": [{"message": "乙"}]},
            {
                "pass3_cache/a.txt.json": [
                    {"index": 1, "name": "", "pre_src": "甲", "post_src": "甲", "pre_dst": "译甲"}
                ]
            },
        )
        self.assertFalse(body["ok"])
        self.assertEqual(body["missing_files"], ["b.txt.json"])
        self.assertEqual(body["input_total"], 2)

    def test_validate_reports_content_issues(self) -> None:
        # index 缺失与不连续应报告内容异常
        body = self._validate(
            "bv_cont",
            {"a.txt.json": [{"message": "甲"}]},
            {
                "pass3_cache/a.txt.json": [
                    {"index": 1, "name": "", "pre_src": "甲", "post_src": "甲", "pre_dst": "译1"},
                    {"name": "", "pre_src": "缺", "post_src": "缺", "pre_dst": "译2"},
                    {"index": 4, "name": "", "pre_src": "丙", "post_src": "丙", "pre_dst": "译3"},
                ]
            },
        )
        self.assertFalse(body["ok"])
        issues = " ".join(i["issue"] for i in body["content_issues"])
        self.assertIn("缺少 index", issues)
        self.assertIn("索引不连续", issues)

    def test_validate_chunked_cache_not_missing(self) -> None:
        # 分块缓存（xx-1.json）视为已覆盖对应输入，不误报缺失
        body = self._validate(
            "bv_chunk",
            {"book.txt.json": [{"message": "甲"}]},
            {
                "pass3_cache/book.txt-1.json": [
                    {"index": 1, "name": "", "pre_src": "甲", "post_src": "甲", "pre_dst": "译"}
                ]
            },
        )
        self.assertEqual(body["missing_files"], [])

    def test_validate_ok_when_all_good(self) -> None:
        body = self._validate(
            "bv_ok",
            {"a.txt.json": [{"message": "甲"}]},
            {
                "pass3_cache/a.txt.json": [
                    {"index": 1, "name": "", "pre_src": "甲", "post_src": "甲", "pre_dst": "译"}
                ]
            },
        )
        self.assertTrue(body["ok"])
        self.assertEqual(body["missing_files"], [])
        self.assertEqual(body["content_issues"], [])

    def test_validate_reports_missing_starting_index(self) -> None:
        # index 从 35 开始（1-34 全部缺失）：应报告起始缺失，而非只报相邻缺口
        body = self._validate(
            "bv_start",
            {"a.txt.json": [{"message": "甲"}]},
            {
                "pass3_cache/a.txt.json": [
                    {"index": idx, "name": "", "pre_src": "甲", "post_src": "甲", "pre_dst": "译"}
                    for idx in (35, 36, 37, 38, 39, 41)
                ]
            },
        )
        self.assertFalse(body["ok"])
        issues = " ".join(i["issue"] for i in body["content_issues"])
        self.assertIn("起始缺失 1→34", issues)
        self.assertIn("39→41", issues)

    def test_validate_no_false_positive_when_start_at_one(self) -> None:
        # index 从 1 开始且连续：不应误报起始缺失
        body = self._validate(
            "bv_st1",
            {"a.txt.json": [{"message": "甲"}]},
            {
                "pass3_cache/a.txt.json": [
                    {"index": 1, "name": "", "pre_src": "甲", "post_src": "甲", "pre_dst": "译"},
                    {"index": 2, "name": "", "pre_src": "乙", "post_src": "乙", "pre_dst": "译2"},
                ]
            },
        )
        issues = " ".join(i["issue"] for i in body["content_issues"])
        self.assertNotIn("起始缺失", issues)


if __name__ == "__main__":
    unittest.main()
