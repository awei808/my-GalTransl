# -*- coding: utf-8 -*-
"""ForSemCheck 端到端集成测试（mock OpenAI 兼容服务器模拟主翻译 profile 端点）。

覆盖真实链路：CProjectConfig 加载 → ForSemCheck 实例化（跟随主翻译 profile
tokenPool，即「后端配置」页的 OpenAI-Compatible 端点）→ batch_translate
（真实 HTTP 请求 mock 服务器）→ suspected_error 置位 → find_problems 认领
「疑似错误」→ 重跑幂等 → 主池为空时降级跳过。

mock 服务器监听随机端口，config.yaml 动态写入临时项目目录，测试互不冲突。
"""
import json
import os
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from GalTransl.CSentense import CSentense
from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.Problem import find_problems
from GalTransl.Backend.ForSemCheck import ForSemCheck


def _mkdtemp_writable(prefix: str) -> str:
    """创建可写临时目录。

    tempfile.mkdtemp 默认以 0o700 模式建目录，在受限沙箱环境下其内部文件
    不可写；改用无 mode 的 os.makedirs + uuid 保证唯一，正常环境行为一致。
    """
    base = tempfile.gettempdir()
    for _ in range(100):
        path = os.path.join(base, f"{prefix}{uuid.uuid4().hex[:10]}")
        try:
            os.makedirs(path)
            return path
        except FileExistsError:
            continue
    raise RuntimeError(f"无法在 {base} 下创建唯一临时目录")

CONFIG_TEMPLATE = """\
common:
  language: ja2zh-cn
  gpt.translation_guideline: Basic.md
  gpt.numPerRequestSemCheck: 20
problemAnalyze:
  problemList:
    - 疑似错误
backendSpecific:
  OpenAI-Compatible:
    tokens:{tokens}
proxy:
  enableProxy: false
  proxies: []
"""


def _tokens_yaml(port: int, empty: bool = False) -> str:
    """构造 OpenAI-Compatible 段 tokens 的 YAML 文本。

    empty=True 时返回空列表（模拟主翻译 profile 未配置任何 token）。
    """
    if empty:
        return " []"
    return (
        "\n"
        "      - token: mock-key\n"
        f"        endpoint: http://127.0.0.1:{port}\n"
        "        modelName: mock-model\n"
        "        stream: false\n"
    )


class _MockHandler(BaseHTTPRequestHandler):
    """模拟 OpenAI 兼容 /v1/chat/completions：固定判定 index=2 命中。"""

    protocol_version = "HTTP/1.0"  # 默认；显式声明避免 keep-alive 复用竞态

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        content = 'abc|{"id": 2, "reason": "译文串行"}'
        resp = {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": 0,
            "model": "mock-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        data = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")  # 关闭连接，避免 httpx keep-alive 复用问题
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class _NoopApiLogger:
    """测试环境不写 api_calls.log：no-op 替身。"""

    def begin(self, *args, **kwargs):
        return ""

    def record(self, *args, **kwargs):
        return None


def _make_trans(index: int, pre_dst: str) -> CSentense:
    t = CSentense(f"src{index}", index=index)
    t.post_src = f"src{index}"
    t.pre_dst = pre_dst
    t.proofread_zh = ""
    t.suspected_error = ""
    return t


class ForSemCheckE2ETests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # 测试环境不写 api_calls.log
        import GalTransl.Backend.BaseEngine as base_engine_mod

        cls._api_logger_patcher_owner = base_engine_mod
        cls._old_api_logger = base_engine_mod.api_logger
        base_engine_mod.api_logger = _NoopApiLogger()

        cls._server = ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
        cls._port = cls._server.server_address[1]
        cls._thread = threading.Thread(
            target=cls._server.serve_forever, daemon=True
        )
        cls._thread.start()
        cls._project_dir = _mkdtemp_writable("semcheck_e2e_")

    @classmethod
    def tearDownClass(cls) -> None:
        # setUpClass 中途失败时相关属性可能未初始化，逐项防御避免掩盖原始错误
        server = getattr(cls, "_server", None)
        if server is not None:
            server.shutdown()
            server.server_close()
        owner = getattr(cls, "_api_logger_patcher_owner", None)
        old_logger = getattr(cls, "_old_api_logger", None)
        if owner is not None and old_logger is not None:
            owner.api_logger = old_logger

    def _write_config(self, has_tokens: bool) -> str:
        cfg_path = os.path.join(
            self._project_dir,
            "config_with_tokens.yaml" if has_tokens else "config_no_tokens.yaml",
        )
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(
                CONFIG_TEMPLATE.format(
                    tokens=_tokens_yaml(self._port, empty=not has_tokens)
                )
            )
        return cfg_path

    def _make_api(self, has_tokens: bool) -> ForSemCheck:
        cfg = CProjectConfig(
            self._project_dir, os.path.basename(self._write_config(has_tokens))
        )
        # 不传 token_pool：验证 ForSemCheck 按主 profile 自动构建令牌池（与其他后端一致）
        return ForSemCheck(cfg, "ForSemCheck", None, None)

    async def test_follows_main_profile_marks_suspected_error_and_problem(self) -> None:
        api = self._make_api(has_tokens=True)
        try:
            trans_list = [
                _make_trans(1, "正确译文一"),
                _make_trans(2, "这句译文语义与原文完全不同"),
                _make_trans(3, "正确译文三"),
            ]
            await api.batch_translate("demo.json", "demo.json", trans_list, 10)
            find_problems(trans_list, api.pj_config, gpt_dict=None)
        finally:
            await api.shutdown()
        self.assertEqual(trans_list[0].suspected_error, "")
        self.assertEqual(trans_list[1].suspected_error, "译文串行")
        self.assertEqual(trans_list[2].suspected_error, "")
        self.assertIn("疑似错误", trans_list[1].problem)
        self.assertNotIn("疑似错误", trans_list[0].problem)

    async def test_rerun_is_idempotent(self) -> None:
        api = self._make_api(has_tokens=True)
        try:
            trans_list = [
                _make_trans(1, "正确译文一"),
                _make_trans(2, "这句译文语义与原文完全不同"),
            ]
            await api.batch_translate("demo.json", "demo.json", trans_list, 10)
            # 先人为塞入旧标记，再重跑：应先清旧再写新
            trans_list[0].suspected_error = "旧标记"
            await api.batch_translate("demo.json", "demo.json", trans_list, 10)
        finally:
            await api.shutdown()
        self.assertEqual(trans_list[0].suspected_error, "")  # 旧标记被清，未命中
        self.assertEqual(trans_list[1].suspected_error, "译文串行")

    async def test_empty_main_token_pool_degrades_without_requests(self) -> None:
        api = self._make_api(has_tokens=False)
        try:
            self.assertTrue(api._disabled_reason)  # 主池无 token → 明确禁用原因
            trans_list = [_make_trans(1, "正确译文一"), _make_trans(2, "语义不同")]
            await api.batch_translate("demo.json", "demo.json", trans_list, 10)
        finally:
            await api.shutdown()
        self.assertTrue(all(t.suspected_error == "" for t in trans_list))


if __name__ == "__main__":
    unittest.main()
