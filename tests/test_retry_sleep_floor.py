"""ask_chatbot 重试退避：所有重试在退避值基础上额外 +1s，不再出现 0s 立即重试。"""
import asyncio
import threading
import unittest

import httpx

from GalTransl.Backend.BaseEngine import BaseEngine


class DummyToken:
    token = "sk-test"
    domain = "http://unit.test/v1"
    model_name = "test-model"
    stream = False

    def maskToken(self) -> str:
        return "sk-***"


class _FakeCompletions:
    def __init__(self, script: list):
        self.script = script  # 每次调用弹出下一个行为：Exception 实例或返回值
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


class _DummyResponse:
    def __init__(self):
        message = type("Message", (), {"content": "ok"})()
        choice = type("Choice", (), {"message": message})()
        self.choices = [choice]


def _make_engine(api_error_wait: float) -> BaseEngine:
    engine = BaseEngine.__new__(BaseEngine)
    engine.pj_config = type("Cfg", (), {"stop_event": None})()
    engine.eng_type = "test"
    engine.client_list = []
    engine.tokenStrategy = "fallback"
    engine.global_request_rpm = 0
    engine.api_max_requests = 0
    engine.api_min_interval_sec = 0.0
    engine.api_max_error_rate = 0
    engine._total_requests = 0
    engine._failed_requests = 0
    engine._rate_lock = threading.Lock()
    engine.api_timeout = 1
    engine.apiErrorWait = api_error_wait
    engine.max_api_retries = 5
    engine._shutdown_done = False
    engine._client_failure_counts = {}
    engine._retired_clients = []
    engine._client_recycle_lock = asyncio.Lock()
    engine.proxyProvider = None
    return engine


class RetrySleepFloorTests(unittest.IsolatedAsyncioTestCase):
    async def _collect_sleeps(self, api_error_wait: float) -> list[float]:
        engine = _make_engine(api_error_wait)
        token = DummyToken()
        completions = _FakeCompletions(
            script=[httpx.ConnectError("a"), httpx.ConnectError("b"), _DummyResponse()]
        )
        client = type(
            "C1", (), {"chat": type("Chat", (), {"completions": completions})()}
        )()
        engine.client_list = [(client, token)]

        sleeps: list[float] = []

        async def _fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        engine._interruptible_sleep = _fake_sleep
        result, _used = await engine.ask_chatbot(
            messages=[{"role": "user", "content": "hi"}], file_name="t.json"
        )
        self.assertEqual(result, "ok")
        return sleeps

    async def test_fixed_wait_zero_still_sleeps_at_least_one_second(self) -> None:
        # apiErrorWait=0（固定退避下限）：+1s 基础等待后每次重试睡眠 ∈ [1, 2)
        sleeps = await self._collect_sleeps(0.0)
        self.assertEqual(len(sleeps), 2)
        for seconds in sleeps:
            self.assertGreaterEqual(seconds, 1.0)
            self.assertLess(seconds, 2.0)

    async def test_auto_exponential_jitter_sleeps_at_least_one_second(self) -> None:
        # auto（-1，指数+全抖动）：抖动摇出 0 时由 +1s 兜底，不再出现 sleeping 0.000s
        sleeps = await self._collect_sleeps(-1.0)
        self.assertEqual(len(sleeps), 2)
        for seconds in sleeps:
            self.assertGreaterEqual(seconds, 1.0)


if __name__ == "__main__":
    unittest.main()
