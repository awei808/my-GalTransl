"""尝试预算（maxApiRetries）：429 不计入预算、耗尽抛 RuntimeError、日志口径记 attempts。"""
import threading
import unittest

import httpx

from GalTransl.Backend import BaseEngine as base_engine_module
from GalTransl.Backend.BaseEngine import BaseEngine
from openai import RateLimitError

try:
    from test_client_recycle import (
        DummyToken,
        _FakeAsyncOpenAI,
        _FakeCompletions,
        _DummyResponse,
        _make_engine,
        _request,
    )
except ImportError:  # unittest 直跑（tests.test_xxx）时以包路径导入
    from tests.test_client_recycle import (
        DummyToken,
        _FakeAsyncOpenAI,
        _FakeCompletions,
        _DummyResponse,
        _make_engine,
        _request,
    )


class _StubApiLogger:
    def __init__(self):
        self.records: list[dict] = []

    def begin(self, *args, **kwargs):
        return "trace"

    def record(self, trace, **kwargs):
        self.records.append(kwargs)


def _rate_limited() -> RateLimitError:
    return RateLimitError("429", response=httpx.Response(429, request=_request()), body=None)


class RetryBudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _FakeAsyncOpenAI.instances = []
        self._orig_async_openai = base_engine_module.AsyncOpenAI
        base_engine_module.AsyncOpenAI = _FakeAsyncOpenAI

    def tearDown(self) -> None:
        base_engine_module.AsyncOpenAI = self._orig_async_openai

    async def test_budget_exhaustion_raises_after_max_attempts(self) -> None:
        engine = _make_engine()
        engine.max_api_retries = 3
        token = DummyToken()
        failing = _FakeCompletions(script=[ValueError("x")] * 3)
        client = type("C", (), {"chat": type("Chat", (), {"completions": failing})()})()
        engine.client_list = [(client, token)]

        with self.assertRaises(RuntimeError):
            await engine.ask_chatbot(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(failing.calls, 3)

    async def test_rate_limit_errors_do_not_consume_budget(self) -> None:
        engine = _make_engine()
        engine.max_api_retries = 2
        token = DummyToken()
        # 三连 429 后成功：预算未被消耗，请求最终成功
        completions = _FakeCompletions(
            script=[_rate_limited(), _rate_limited(), _rate_limited(), _DummyResponse()]
        )
        client = type("C", (), {"chat": type("Chat", (), {"completions": completions})()})()
        engine.client_list = [(client, token)]

        result, _ = await engine.ask_chatbot(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(result, "ok")
        self.assertEqual(completions.calls, 4)

    async def test_explicit_max_retry_count_overrides_instance_default(self) -> None:
        engine = _make_engine()
        engine.max_api_retries = 100
        token = DummyToken()
        failing = _FakeCompletions(script=[ValueError("x")] * 2)
        client = type("C", (), {"chat": type("Chat", (), {"completions": failing})()})()
        engine.client_list = [(client, token)]

        with self.assertRaises(RuntimeError):
            await engine.ask_chatbot(
                messages=[{"role": "user", "content": "hi"}], max_retry_count=2
            )
        self.assertEqual(failing.calls, 2)

    async def test_api_logger_records_attempts_not_rotation_offset(self) -> None:
        engine = _make_engine()
        engine.max_api_retries = 3
        engine.pj_config = type(
            "Cfg",
            (),
            {
                "stop_event": None,
                "runtime_project_dir": "proj",
                # getattr 默认参数会急切求值 getProjectDir，桩必须提供
                "getProjectDir": lambda self: "proj",
            },
        )()
        stub_logger = _StubApiLogger()
        orig_logger = base_engine_module.api_logger
        base_engine_module.api_logger = stub_logger
        try:
            token = DummyToken()
            completions = _FakeCompletions(
                script=[ValueError("x"), ValueError("y"), _DummyResponse()]
            )
            client = type(
                "C", (), {"chat": type("Chat", (), {"completions": completions})()})()
            engine.client_list = [(client, token)]
            result, _ = await engine.ask_chatbot(
                messages=[{"role": "user", "content": "hi"}]
            )
        finally:
            base_engine_module.api_logger = orig_logger

        self.assertEqual(result, "ok")
        error_records = [r for r in stub_logger.records if r.get("status") == "error"]
        # 第 1、2 次失败分别记 attempts=1、2（与轮换偏移量口径区分）
        self.assertEqual([r["retry_count"] for r in error_records], [1, 2])


if __name__ == "__main__":
    unittest.main()
