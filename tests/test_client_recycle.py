"""客户端韧性：传输错误分类、连续失败自动回收、retired 客户端 shutdown 清理。"""
import asyncio
import threading
import unittest

import httpx

from GalTransl.Backend.BaseEngine import BaseEngine
from GalTransl.Backend import BaseEngine as base_engine_module
from openai import APIConnectionError, APITimeoutError, RateLimitError


def _request() -> httpx.Request:
    return httpx.Request("POST", "http://unit.test/v1")


class DummyToken:
    token = "sk-test"
    domain = "http://unit.test/v1"
    model_name = "test-model"
    stream = False

    def maskToken(self) -> str:
        return "sk-***"


class _FakeAsyncOpenAI:
    """替身 AsyncOpenAI：记录构造参数，供回收逻辑断言。"""

    instances: list = []

    def __init__(self, api_key=None, base_url=None, max_retries=0, http_client=None):
        self.api_key = api_key
        self.base_url = base_url
        self.http_client = http_client
        _FakeAsyncOpenAI.instances.append(self)


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


def _make_engine(strategy: str = "fallback") -> BaseEngine:
    engine = BaseEngine.__new__(BaseEngine)
    engine.pj_config = type("Cfg", (), {"stop_event": None})()
    engine.eng_type = "test"
    engine.client_list = []
    engine.tokenStrategy = strategy
    engine.global_request_rpm = 0
    engine.api_max_requests = 0
    engine.api_min_interval_sec = 0.0
    engine.api_max_error_rate = 0
    engine._total_requests = 0
    engine._failed_requests = 0
    engine._rate_lock = threading.Lock()
    engine.api_timeout = 1
    engine.apiErrorWait = 0.0
    engine._shutdown_done = False
    engine._client_failure_counts = {}
    engine._retired_clients = []
    engine._client_recycle_lock = asyncio.Lock()
    engine.proxyProvider = None
    return engine


class TransportErrorClassificationTests(unittest.TestCase):
    def test_transport_errors_are_recognized(self) -> None:
        req = _request()
        for exc in [
            ConnectionError("reset"),
            TimeoutError("timeout"),
            httpx.ConnectError("refused"),
            httpx.ReadError("read"),
            httpx.RemoteProtocolError("peer closed"),
            APIConnectionError(request=req),
            APITimeoutError(req),
        ]:
            self.assertTrue(BaseEngine._is_transport_error(exc), f"{type(exc).__name__}")

    def test_api_status_and_unknown_errors_are_not_transport(self) -> None:
        rate_limited = RateLimitError(
            "429", response=httpx.Response(429, request=_request()), body=None
        )
        for exc in [rate_limited, ValueError("bad"), RuntimeError("x")]:
            self.assertFalse(BaseEngine._is_transport_error(exc), f"{type(exc).__name__}")


class ClientRecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _FakeAsyncOpenAI.instances = []
        self._orig_async_openai = base_engine_module.AsyncOpenAI
        base_engine_module.AsyncOpenAI = _FakeAsyncOpenAI

    def tearDown(self) -> None:
        base_engine_module.AsyncOpenAI = self._orig_async_openai

    async def test_recycle_after_three_transport_failures_then_success(self) -> None:
        engine = _make_engine()
        token = DummyToken()
        failing = _FakeCompletions(script=[httpx.ConnectError("a"), httpx.ConnectError("b"), httpx.ConnectError("c")])
        healthy = _FakeCompletions(script=[_DummyResponse()])
        failing_client = type("C1", (), {"chat": type("Chat", (), {"completions": failing})()})()
        healthy_client = type("C2", (), {"chat": type("Chat", (), {"completions": healthy})()})()
        engine.client_list = [(failing_client, token)]

        # 回收替换语义由 test_recycle_replaces_pair_and_rejects_stale_client 覆盖，
        # 此处聚焦 ask_chatbot 的计数/触发/换用新客户端逻辑。
        async def _fake_recycle(failed_client, tok):
            engine.client_list = [(healthy_client, tok)]
            engine._retired_clients.append(failed_client)
            return healthy_client

        engine._recycle_failed_client = _fake_recycle

        result, used_token = await engine.ask_chatbot(
            messages=[{"role": "user", "content": "hi"}], file_name="t.json"
        )

        self.assertEqual(result, "ok")
        self.assertIs(used_token, token)
        self.assertEqual(failing.calls, 3)
        self.assertEqual(healthy.calls, 1)
        self.assertIs(engine.client_list[0][0], healthy_client)
        self.assertEqual(engine._retired_clients, [failing_client])
        self.assertEqual(engine._client_failure_counts, {})

    async def test_non_transport_error_does_not_recycle(self) -> None:
        engine = _make_engine()
        token = DummyToken()
        failing = _FakeCompletions(script=[ValueError("x"), ValueError("y")])
        client = type("C1", (), {"chat": type("Chat", (), {"completions": failing})()})()
        engine.client_list = [(client, token)]
        recycle_calls: list = []

        async def _no_recycle(failed_client, tok):
            recycle_calls.append(failed_client)
            return None

        engine._recycle_failed_client = _no_recycle

        with self.assertRaises(RuntimeError):
            await engine.ask_chatbot(
                messages=[{"role": "user", "content": "hi"}],
                file_name="t.json",
                max_retry_count=2,
            )
        self.assertEqual(recycle_calls, [])
        self.assertEqual(engine._client_failure_counts, {})

    async def test_recycle_replaces_pair_and_rejects_stale_client(self) -> None:
        engine = _make_engine()
        token_a, token_b = DummyToken(), DummyToken()
        client_a, client_b = object(), object()
        engine.client_list = [(client_a, token_a), (client_b, token_b)]
        engine._build_http_client = lambda proxy_addr: "http-client-sentinel"

        replacement = await engine._recycle_failed_client(client_a, token_a)

        self.assertIsInstance(replacement, _FakeAsyncOpenAI)
        self.assertEqual(replacement.api_key, token_a.token)
        self.assertIs(engine.client_list[0][0], replacement)
        self.assertIs(engine.client_list[1][0], client_b)
        self.assertEqual(engine._retired_clients, [client_a])
        # 已被替换的客户端再次回收：配对不存在，返回 None
        self.assertIsNone(await engine._recycle_failed_client(client_a, token_a))

    async def test_recycle_aborts_when_shutdown_wins_the_race(self) -> None:
        # 确定性复现窄竞态：外层检查通过后、加锁前 shutdown 完成置位，
        # 锁内复查必须中止回收，否则新建客户端不在 shutdown 快照里、无人关闭
        engine = _make_engine()
        token = DummyToken()
        client = object()
        engine.client_list = [(client, token)]

        class _ShutdownWinsLock(asyncio.Lock):
            async def acquire(self):
                await super().acquire()
                engine._shutdown_done = True
                return True

        engine._client_recycle_lock = _ShutdownWinsLock()

        replacement = await engine._recycle_failed_client(client, token)

        self.assertIsNone(replacement)
        self.assertEqual(_FakeAsyncOpenAI.instances, [])
        self.assertIs(engine.client_list[0][0], client)
        self.assertEqual(engine._retired_clients, [])


class RetiredClientShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_closes_active_and_retired_clients(self) -> None:
        engine = _make_engine()
        closed: list[str] = []

        class _FakeClient:
            def __init__(self, name: str):
                self.name = name

            async def close(self):
                closed.append(self.name)

        active, retired = _FakeClient("active"), _FakeClient("retired")
        engine.client_list = [(active, DummyToken())]
        engine._retired_clients = [retired]

        await engine.shutdown()
        await engine.shutdown()  # 幂等

        self.assertEqual(sorted(closed), ["active", "retired"])
        self.assertEqual(engine._retired_clients, [])


if __name__ == "__main__":
    unittest.main()
