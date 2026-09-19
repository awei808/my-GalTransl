"""校对页单句 AI 建议端点（/review/ai-suggest）与 ReviewAssist 纯函数的行为验证。

覆盖：
- ReviewAssist 纯函数：提示词装配、建议提取（剥围栏/前缀/包裹引号）、
  端点规范化、真实 token 挑选；
- HTTP 端点：正常建议（含提取）、条目不存在 404、非法路径 400、
  空建议 502、单飞锁 409、翻译任务运行中 409。
"""

import base64
import importlib
import json
import os
import shutil
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from GalTransl import ReviewAssist
from GalTransl import server as _server_mod

_ENTRIES = [
    {"index": 1, "name": "創", "pre_src": "……そんな目で見るなよ", "pre_dst": "……别用那种眼神看我啊"},
    {"index": 2, "name": "", "pre_src": "おはよう", "pre_dst": "早上好", "problem": "残留日文：おはよう"},
    {"index": 3, "name": "", "pre_src": "行くぞ", "pre_dst": "走吧"},
]

_PROFILE_DATA = {
    "OpenAI-Compatible": {
        "tokens": [
            {"token": "sk-test", "endpoint": "https://api.example.com", "modelName": "test-model"},
            {"token": "-example-", "endpoint": "https://api.example.com", "modelName": "example-model"},
        ]
    }
}


class ReviewAssistPureTests(unittest.TestCase):
    def test_build_suggest_messages_contains_context_sections(self) -> None:
        messages = ReviewAssist.build_suggest_messages(
            "原文", "当前译文",
            prev_dst="上句", next_dst="下句",
            problem="残留日文", doub_content="存疑", instruction="更口语化",
        )
        self.assertEqual(messages[0]["role"], "system")
        body = messages[1]["content"]
        for section in ("<原文>", "当前译文", "上句", "下句", "残留日文", "存疑", "更口语化"):
            self.assertIn(section, body)

    def test_build_suggest_messages_empty_dst_marks_untranslated(self) -> None:
        messages = ReviewAssist.build_suggest_messages("原文", "")
        self.assertIn("尚未翻译", messages[1]["content"])

    def test_extract_suggestion_strips_fences_prefix_and_quotes(self) -> None:
        self.assertEqual(ReviewAssist.extract_suggestion('译文："更好的译文"'), "更好的译文")
        self.assertEqual(ReviewAssist.extract_suggestion("```\n围栏里的译文\n```"), "围栏里的译文")
        self.assertEqual(ReviewAssist.extract_suggestion("保持原样"), "保持原样")
        # 对话引号「」是合法内容，不剥
        self.assertEqual(ReviewAssist.extract_suggestion("「你好」"), "「你好」")

    def test_normalize_base_url(self) -> None:
        self.assertEqual(ReviewAssist.normalize_base_url("https://a.com"), "https://a.com/v1")
        self.assertEqual(ReviewAssist.normalize_base_url("https://a.com/v1"), "https://a.com/v1")
        self.assertEqual(
            ReviewAssist.normalize_base_url("https://a.com/v1/chat/completions"), "https://a.com/v1"
        )

    def test_pick_real_token_skips_example(self) -> None:
        token = ReviewAssist.pick_real_token(_PROFILE_DATA["OpenAI-Compatible"]["tokens"])
        assert token is not None
        self.assertEqual(token["modelName"], "test-model")
        self.assertIsNone(ReviewAssist.pick_real_token([{"token": "-example-"}]))


class ReviewAiSuggestHttpTests(unittest.TestCase):
    """HTTP 端点行为：以临时项目 + mock LLM 调用验证（不发起真实 API 请求）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.mkdtemp(prefix="gt_ai_suggest_")
        pass3 = os.path.join(cls.tmpdir, "transl_cache", "pass3_cache")
        os.makedirs(pass3)
        with open(os.path.join(pass3, "t01.txt.json"), "wb") as f:
            f.write(json.dumps(_ENTRIES, ensure_ascii=False).encode("utf-8"))
        # 隔离安全冒烟测试可能遗留的鉴权环境变量（否则写端点全部 401）
        cls._saved_token = os.environ.pop("GALTRANSL_API_TOKEN", None)
        importlib.reload(_server_mod)
        cls.registry = _server_mod.JobRegistry()
        cls.server = _server_mod.ThreadingHTTPServer(
            ("127.0.0.1", 0), _server_mod.build_handler(cls.registry)
        )
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        if cls._saved_token is not None:
            os.environ["GALTRANSL_API_TOKEN"] = cls._saved_token
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _pid(self) -> str:
        return base64.urlsafe_b64encode(self.tmpdir.encode("utf-8")).decode("ascii")

    def _post(self, body: dict) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{self.port}/api/projects/{self._pid()}/review/ai-suggest"
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    def _suggest_body(self, **overrides) -> dict:
        body = {
            "file": "pass3_cache/t01.txt.json",
            "index": 2,
            "backend_profile_data": _PROFILE_DATA,
        }
        body.update(overrides)
        return body

    def test_suggest_normal_returns_extracted_suggestion(self) -> None:
        with patch.object(
            ReviewAssist, "request_suggestion", return_value='译文："更好的译文"'
        ) as mock_call:
            status, data = self._post(self._suggest_body())
        self.assertEqual(status, 200)
        self.assertEqual(data["suggestion"], "更好的译文")
        self.assertEqual(data["model"], "test-model")
        # 消息中携带原文与问题上下文
        sent_messages = mock_call.call_args.kwargs.get("messages") or mock_call.call_args[0][0]
        self.assertIn("おはよう", sent_messages[1]["content"])
        self.assertIn("残留日文", sent_messages[1]["content"])

    def test_missing_entry_returns_404(self) -> None:
        status, _ = self._post(self._suggest_body(index=999))
        self.assertEqual(status, 404)

    def test_invalid_path_returns_400(self) -> None:
        status, _ = self._post(self._suggest_body(file="../escape.json"))
        self.assertEqual(status, 400)

    def test_no_backend_profile_returns_400(self) -> None:
        body = self._suggest_body()
        body.pop("backend_profile_data")
        # 隔离机器上的真实全局 profiles：置空后兜底链无任何可用后端 → 400
        with patch.object(_server_mod, "_read_backend_profiles", return_value={"profiles": {}}):
            status, data = self._post(body)
        self.assertEqual(status, 400)
        self.assertTrue(data.get("error", ""))

    def test_empty_suggestion_returns_502(self) -> None:
        with patch.object(ReviewAssist, "request_suggestion", return_value=""):
            status, _ = self._post(self._suggest_body())
        self.assertEqual(status, 502)

    def test_busy_lock_returns_409(self) -> None:
        with _server_mod._REVIEW_SUGGEST_LOCK:
            status, data = self._post(self._suggest_body())
        self.assertEqual(status, 409)

    def test_running_job_returns_409(self) -> None:
        self.registry._jobs["fake-job"] = types.SimpleNamespace(
            project_dir=self.tmpdir, status="running"
        )
        try:
            status, _ = self._post(self._suggest_body())
            self.assertEqual(status, 409)
        finally:
            del self.registry._jobs["fake-job"]


if __name__ == "__main__":
    unittest.main()
