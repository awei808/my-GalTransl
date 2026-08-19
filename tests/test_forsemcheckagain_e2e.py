# -*- coding: utf-8 -*-
"""ForSemCheckAgain 端到端集成测试（mock OpenAI 兼容服务器模拟主翻译 profile 端点）。

覆盖真实链路：CProjectConfig 加载 → ForSemCheckAgain 实例化（跟随主翻译 profile
tokenPool）→ batch_translate（真实 HTTP 请求 mock 服务器）→ keep:true 确认保留
标记（reason 可更新）/ keep:false 撤销标记 / LLM 调用失败与判定缺失 fail-safe
保留标记 / 无命中句跳过 / 主池为空时降级跳过 → find_problems 重新认领。

mock 服务器监听随机端口，响应内容/状态由类属性控制（测试间切换），config.yaml
动态写入临时项目目录，测试互不冲突。
"""
import json
import os
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from GalTransl.CSentense import CSentense
from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.Problem import find_problems
from GalTransl.Backend.ForSemCheckAgain import ForSemCheckAgain


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
    """模拟 OpenAI 兼容 /v1/chat/completions。

    response_content / response_status / request_count 为类属性，
    测试间可切换响应内容与状态、统计请求次数。
    """

    protocol_version = "HTTP/1.0"  # 默认；显式声明避免 keep-alive 复用竞态

    response_content = 'abc|{"id": 2, "keep": true, "reason": "人名错译：華恋→华良"}'
    response_status = 200
    request_count = 0

    def do_POST(self):
        _MockHandler.request_count += 1
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        if self.response_status != 200:
            self.send_response(self.response_status)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return
        content = self.response_content
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


def _make_trans(index: int, pre_dst: str, suspected: str = "") -> CSentense:
    t = CSentense(f"src{index}", index=index)
    t.post_src = f"src{index}"
    t.pre_dst = pre_dst
    t.proofread_zh = ""
    t.suspected_error = suspected
    return t


class ForSemCheckAgainE2ETests(unittest.IsolatedAsyncioTestCase):
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
        cls._project_dir = _mkdtemp_writable("semcheck_again_e2e_")

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

    def _make_api(self, has_tokens: bool = True) -> ForSemCheckAgain:
        cfg = CProjectConfig(
            self._project_dir, os.path.basename(self._write_config(has_tokens))
        )
        # 不传 token_pool：验证 ForSemCheckAgain 按主 profile 自动构建令牌池（与其他后端一致）
        return ForSemCheckAgain(cfg, "ForSemCheckAgain", None, None)

    async def test_confirm_keeps_mark_dismiss_clears_mark(self) -> None:
        _MockHandler.response_content = (
            'abc|{"id": 2, "keep": true, "reason": "人名错译：華恋→华良"}\n'
            'def|{"id": 3, "keep": false}'
        )
        api = self._make_api()
        try:
            trans_list = [
                _make_trans(1, "正常译文", ""),
                _make_trans(2, "这句译文语义与原文完全不同", "疑似错误"),
                _make_trans(3, "委婉但可接受的译文", "疑似错误"),
            ]
            await api.batch_translate("demo.json", "demo.json", trans_list, 10)
            find_problems(trans_list, api.pj_config, gpt_dict=None)
        finally:
            await api.shutdown()
        # 未标记句不受影响
        self.assertEqual(trans_list[0].suspected_error, "")
        # keep:true：保留标记且 reason 被新原因覆盖
        self.assertEqual(trans_list[1].suspected_error, "人名错译：華恋→华良")
        self.assertIn("疑似错误", trans_list[1].problem)
        # keep:false：撤销标记，问题列表不再认领
        self.assertEqual(trans_list[2].suspected_error, "")
        self.assertNotIn("疑似错误", trans_list[2].problem)

    async def test_llm_failure_keeps_existing_marks_fail_safe(self) -> None:
        api = self._make_api()

        async def _failing_llm(messages, filename, idx_tip, cb):
            raise RuntimeError("mock LLM failure")

        try:
            trans_list = [
                _make_trans(1, "正常译文", ""),
                _make_trans(2, "第一轮标记的句子", "疑似错误"),
            ]
            # 直接 patch _call_llm 抛异常：真实 HTTP 500 会命中 ask_chatbot 的
            # 无限重试（max_retry_count 默认 None，指数退避直至 stop_event），
            # 测试无法等待其返回；patch 到调用层验证 fail-safe 分支本身。
            with patch.object(
                ForSemCheckAgain, "_call_llm", new=_failing_llm
            ):
                await api.batch_translate("demo.json", "demo.json", trans_list, 10)
        finally:
            await api.shutdown()
        # fail-safe：调用失败不误删第一轮信号
        self.assertEqual(trans_list[0].suspected_error, "")
        self.assertEqual(trans_list[1].suspected_error, "疑似错误")

    async def test_missing_verdict_keeps_mark_fail_safe(self) -> None:
        _MockHandler.response_content = 'abc|{"id": 2, "keep": false}'
        api = self._make_api()
        try:
            trans_list = [
                _make_trans(2, "被撤销的句子", "疑似错误"),
                _make_trans(3, "未获判定的句子", "疑似错误"),
            ]
            await api.batch_translate("demo.json", "demo.json", trans_list, 10)
        finally:
            await api.shutdown()
        # id=2 显式撤销；id=3 判定缺失 → fail-safe 保留
        self.assertEqual(trans_list[0].suspected_error, "")
        self.assertEqual(trans_list[1].suspected_error, "疑似错误")

    async def test_no_flagged_sentences_skips_without_llm_call(self) -> None:
        _MockHandler.request_count = 0
        api = self._make_api()
        try:
            trans_list = [_make_trans(1, "正常译文", ""), _make_trans(2, "正常译文二", "")]
            await api.batch_translate("demo.json", "demo.json", trans_list, 10)
        finally:
            await api.shutdown()
        self.assertTrue(all(t.suspected_error == "" for t in trans_list))
        self.assertEqual(_MockHandler.request_count, 0)

    async def test_empty_main_token_pool_degrades_without_requests(self) -> None:
        api = self._make_api(has_tokens=False)
        try:
            self.assertTrue(api._disabled_reason)  # 主池无 token → 明确禁用原因
            trans_list = [_make_trans(1, "正常译文", "疑似错误")]
            await api.batch_translate("demo.json", "demo.json", trans_list, 10)
        finally:
            await api.shutdown()
        # 降级跳过：不发请求、不动标记
        self.assertEqual(trans_list[0].suspected_error, "疑似错误")


if __name__ == "__main__":
    unittest.main()
