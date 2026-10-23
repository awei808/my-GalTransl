"""API 请求防护测试：整请求墙钟超时、熔断。

覆盖 GalTransl/Backend/BaseEngine.py 的请求防护：
- apiWallTimeout：抛 APIWallTimeoutError（继承 TimeoutError），但不得被
  _is_transport_error 判成连接层错误，否则慢后端会被反复重建连接池。
- 熔断（apiBreakerThreshold，默认 0 关闭）：显式配置后，连续 N 次服务端级
  错误按阶梯冷却，任意一次成功清零；429 与普通业务错误不计。
"""
import asyncio
import time
import unittest
from typing import List

import httpx
from openai import RateLimitError

from GalTransl.Backend.BaseEngine import (
    APIWallTimeoutError,
    BaseEngine,
    _BREAKERS,
    _get_breaker,
)
from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.Service import JobCancelledError

try:
    from test_client_recycle import (
        DummyToken,
        _DummyResponse,
        _make_engine as _make_flow_engine,
    )
except ImportError:  # unittest 直跑（tests.test_xxx）时以包路径导入
    from tests.test_client_recycle import (
        DummyToken,
        _DummyResponse,
        _make_engine as _make_flow_engine,
    )


def _build_config(backend_specific: dict) -> CProjectConfig:
    """构造最小可用的 CProjectConfig，仅提供 getBackendConfigSection 所需字段。"""
    cfg = CProjectConfig.__new__(CProjectConfig)
    cfg.projectConfig = {
        "backendSpecific": backend_specific,
        "proxy": {},
        "dictionary": {},
    }
    cfg.keyValues = {}
    return cfg


def _make_engine(
    *,
    wall_timeout: float = 900,
    breaker_threshold: int = 0,
    breaker_cooldown: float = 30,
) -> BaseEngine:
    """绕过重型 __init__，注入请求防护所需属性。"""
    eng = BaseEngine.__new__(BaseEngine)
    eng.eng_type = "gpt35"
    eng.pj_config = _build_config({})
    eng.api_wall_timeout = wall_timeout
    eng.api_breaker_threshold = breaker_threshold
    eng.api_breaker_cooldown_sec = breaker_cooldown
    return eng


def _unique_key(tag: str) -> tuple:
    """注册表随进程存活，测试必须用唯一键避免相互污染。"""
    return (f"https://{tag}.example", f"tok-{time.monotonic_ns()}")


def _rate_limit_error() -> RateLimitError:
    """构造真实的 429 错误对象（RateLimitError 继承 APIStatusError）。"""
    request = httpx.Request("POST", "https://ratelimit.example/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return RateLimitError("rate limited", response=response, body=None)


class InitChatbotReadsNewFieldsTests(unittest.TestCase):
    def test_reads_explicit_values(self) -> None:
        cfg = _build_config({
            "OpenAI-Compatible": {
                "apiWallTimeout": 120,
                "apiBreakerThreshold": 3,
                "apiBreakerCooldownSec": 7,
            },
        })
        eng = BaseEngine.__new__(BaseEngine)
        eng.trans_prompt = None
        try:
            eng.init_chatbot("gpt35", cfg)
        except Exception:
            # 后续依赖（令牌池/客户端）缺失会抛错，但 coerce 段已赋值
            pass
        self.assertEqual(eng.api_wall_timeout, 120.0)
        self.assertEqual(eng.api_breaker_threshold, 3)
        self.assertEqual(eng.api_breaker_cooldown_sec, 7.0)

    def test_defaults_when_fields_absent(self) -> None:
        # 缺键取安全默认：墙钟 900s 生效，熔断默认关闭（profile 整段覆盖时也生效）
        cfg = _build_config({"OpenAI-Compatible": {}})
        eng = BaseEngine.__new__(BaseEngine)
        eng.trans_prompt = None
        try:
            eng.init_chatbot("gpt35", cfg)
        except Exception:
            pass
        self.assertEqual(eng.api_wall_timeout, 900.0)
        self.assertEqual(eng.api_breaker_threshold, 0)
        self.assertEqual(eng.api_breaker_cooldown_sec, 30.0)

    def test_zero_keeps_unlimited(self) -> None:
        # 0 必须保留为「不限制」，不能被抬成默认值
        cfg = _build_config({
            "OpenAI-Compatible": {
                "apiWallTimeout": 0,
                "apiBreakerThreshold": 0,
                "apiBreakerCooldownSec": 0,
            },
        })
        eng = BaseEngine.__new__(BaseEngine)
        eng.trans_prompt = None
        try:
            eng.init_chatbot("gpt35", cfg)
        except Exception:
            pass
        self.assertEqual(eng.api_wall_timeout, 0.0)
        self.assertEqual(eng.api_breaker_threshold, 0)
        self.assertEqual(eng.api_breaker_cooldown_sec, 0.0)

    def test_negative_and_overflow_fall_back_to_safe_default(self) -> None:
        # 安全类键越界必须回退默认（900/0/30），不能夹成意外值
        cfg = _build_config({
            "OpenAI-Compatible": {
                "apiWallTimeout": -5,
                "apiBreakerThreshold": 100000,
                "apiBreakerCooldownSec": -1,
            },
        })
        eng = BaseEngine.__new__(BaseEngine)
        eng.trans_prompt = None
        try:
            eng.init_chatbot("gpt35", cfg)
        except Exception:
            pass
        self.assertEqual(eng.api_wall_timeout, 900.0)
        self.assertEqual(eng.api_breaker_threshold, 0)
        self.assertEqual(eng.api_breaker_cooldown_sec, 30.0)

    def test_fractional_int_keys_round_up_to_one(self) -> None:
        # 0.4 若被 int() 截断成 0，等于静默关闭熔断；非零小数必须兜到 1
        cfg = _build_config({
            "OpenAI-Compatible": {
                "apiBreakerThreshold": 0.4,
            },
        })
        eng = BaseEngine.__new__(BaseEngine)
        eng.trans_prompt = None
        try:
            eng.init_chatbot("gpt35", cfg)
        except Exception:
            pass
        self.assertEqual(eng.api_breaker_threshold, 1)


class WallTimeoutClassificationTests(unittest.TestCase):
    def test_wall_timeout_is_not_transport_error(self) -> None:
        # 慢后端不该被当成连接池损坏——否则每次超时都重建客户端
        self.assertFalse(BaseEngine._is_transport_error(APIWallTimeoutError("x")))
        self.assertIsInstance(APIWallTimeoutError("x"), TimeoutError)

    def test_wall_timeout_does_not_trip_breaker(self) -> None:
        # 墙钟超时衡量「太慢」而非「不可用」：慢后端不该被熔断，故不计入
        self.assertFalse(BaseEngine._is_server_side_error(APIWallTimeoutError("x")))

    def test_regular_timeout_still_transport_error(self) -> None:
        # 连接层 TimeoutError 仍走客户端重建路径
        self.assertTrue(BaseEngine._is_transport_error(TimeoutError("x")))


class ServerSideClassificationTests(unittest.TestCase):
    def test_rate_limit_is_not_server_side(self) -> None:
        self.assertFalse(BaseEngine._is_server_side_error(_rate_limit_error()))

    def test_connection_errors_are_server_side(self) -> None:
        self.assertTrue(BaseEngine._is_server_side_error(ConnectionError("reset")))
        self.assertTrue(BaseEngine._is_server_side_error(OSError("reset")))
        self.assertTrue(BaseEngine._is_server_side_error(httpx.ConnectError("boom")))

    def test_business_errors_are_not_server_side(self) -> None:
        # 解析失败等业务错误不触发熔断，否则会误伤可用后端
        self.assertFalse(BaseEngine._is_server_side_error(ValueError("parse failed")))


class CircuitBreakerTests(unittest.TestCase):
    def test_disabled_when_threshold_zero(self) -> None:
        eng = _make_engine(breaker_threshold=0)
        domain, _ = _unique_key("breaker-off")
        for _ in range(20):
            eng._record_breaker_result(domain, False)
        state = _get_breaker(domain)
        self.assertEqual(state.consecutive_failures, 0)
        self.assertEqual(state.open_until_ts, 0.0)

    def test_trips_after_threshold(self) -> None:
        eng = _make_engine(breaker_threshold=3, breaker_cooldown=10)
        domain, _ = _unique_key("breaker-trip")
        eng._record_breaker_result(domain, False)
        eng._record_breaker_result(domain, False)
        state = _get_breaker(domain)
        self.assertEqual(state.open_until_ts, 0.0)  # 未达阈值不熔断
        eng._record_breaker_result(domain, False)
        self.assertGreater(state.open_until_ts, time.monotonic())
        self.assertEqual(state.trip_count, 1)
        self.assertEqual(state.consecutive_failures, 0)  # 触发后重新累计

    def test_success_resets_counter_and_ladder(self) -> None:
        eng = _make_engine(breaker_threshold=2, breaker_cooldown=10)
        domain, _ = _unique_key("breaker-reset")
        eng._record_breaker_result(domain, False)
        eng._record_breaker_result(domain, False)
        state = _get_breaker(domain)
        self.assertEqual(state.trip_count, 1)
        eng._record_breaker_result(domain, True)
        self.assertEqual(state.consecutive_failures, 0)
        self.assertEqual(state.trip_count, 0)

    def test_cooldown_grows_with_repeated_trips(self) -> None:
        eng = _make_engine(breaker_threshold=1, breaker_cooldown=0.05)
        domain, _ = _unique_key("breaker-ladder")
        state = _get_breaker(domain)
        eng._record_breaker_result(domain, False)
        first = state.open_until_ts - time.monotonic()
        time.sleep(0.06)  # 冷却结束后才算新一轮触发
        eng._record_breaker_result(domain, False)
        second = state.open_until_ts - time.monotonic()
        # 用比率断言，避免调度停顿把 0.05s 量级的绝对差打穿
        self.assertAlmostEqual(second / first, 2.0, delta=0.2)  # 第 2 次翻倍

    def test_failures_during_cooldown_do_not_escalate(self) -> None:
        # 冷却期内仍在途的旧请求会陆续报错，不能把它们算成新一轮触发把阶梯顶到 8 倍
        eng = _make_engine(breaker_threshold=1, breaker_cooldown=10)
        domain, _ = _unique_key("breaker-cooldown-fail")
        state = _get_breaker(domain)
        eng._record_breaker_result(domain, False)
        self.assertEqual(state.trip_count, 1)
        open_until = state.open_until_ts
        for _ in range(5):
            eng._record_breaker_result(domain, False)
        self.assertEqual(state.trip_count, 1)
        self.assertEqual(state.open_until_ts, open_until)
        self.assertEqual(state.consecutive_failures, 0)  # 一个字都没改

    def test_wait_returns_immediately_when_closed(self) -> None:
        eng = _make_engine(breaker_threshold=2, breaker_cooldown=10)
        domain, _ = _unique_key("breaker-closed")

        async def run() -> float:
            t0 = time.monotonic()
            await eng._wait_breaker_slot(domain)
            return time.monotonic() - t0

        self.assertLess(asyncio.run(run()), 0.05)

    def test_wait_blocks_during_cooldown(self) -> None:
        eng = _make_engine(breaker_threshold=1, breaker_cooldown=0.2)
        domain, _ = _unique_key("breaker-wait")
        eng._record_breaker_result(domain, False)

        async def run() -> List[float]:
            t0 = time.monotonic()
            await eng._wait_breaker_slot(domain)
            t1 = time.monotonic()
            await eng._wait_breaker_slot(domain)  # 冷却已过，立即返回
            return [t1 - t0, time.monotonic() - t1]

        waited, second = asyncio.run(run())
        self.assertGreaterEqual(waited, 0.15)
        self.assertLess(second, 0.05)


class _SlowCompletions:
    """永不返回的 create：模拟上游长时间不吐数据（流式/非流式均挂住）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        await asyncio.sleep(30)
        return None


class _FastCompletions:
    async def create(self, **kwargs):
        return _DummyResponse()


class _EndlessStream:
    """持续吐空 chunk 的流：模拟「有数据但永不结束」的长流。"""

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0.05)
        return type("Chunk", (), {"choices": []})()


class _StreamCompletions:
    async def create(self, **kwargs):
        return _EndlessStream()


def _flow_engine(completions=None):
    """复用 test_client_recycle 的最小引擎桩，补上请求防护属性。"""
    engine = _make_flow_engine()
    engine.api_wall_timeout = 0
    engine.api_breaker_threshold = 8
    engine.api_breaker_cooldown_sec = 0
    engine._request_count = 0
    token = DummyToken()
    completions = completions if completions is not None else _SlowCompletions()
    client = type(
        "C", (), {"chat": type("Chat", (), {"completions": completions})()}
    )()
    engine.client_list = [(client, token)]
    return engine, token


class WallTimeoutFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # 进程级注册表跨用例存活，而 DummyToken 域名固定，先清避免串味
        _BREAKERS.clear()

    async def test_wall_timeout_aborts_hung_request(self) -> None:
        # 上游永不返回时，墙钟超时必须把在途请求掐掉，而不是等 apiTimeout
        engine, _ = _flow_engine()
        engine.api_wall_timeout = 0.3
        t0 = time.monotonic()
        with self.assertRaises(RuntimeError) as ctx:
            await engine.ask_chatbot(
                messages=[{"role": "user", "content": "hi"}],
                stream=False,
                max_retry_count=1,
            )
        self.assertLess(time.monotonic() - t0, 3.0)
        self.assertIsInstance(ctx.exception.__cause__, APIWallTimeoutError)

    async def test_stream_wall_timeout_aborts_endless_stream(self) -> None:
        # 流式下 apiTimeout 只约束分片间隔，墙钟必须兜住「永远不发完」的流
        engine, _ = _flow_engine(_StreamCompletions())
        engine.api_wall_timeout = 0.3
        with self.assertRaises(RuntimeError) as ctx:
            await engine.ask_chatbot(
                messages=[{"role": "user", "content": "hi"}],
                stream=True,
                max_retry_count=1,
            )
        cause = ctx.exception.__cause__
        self.assertIsInstance(cause, APIWallTimeoutError)
        self.assertIn("流式消费阶段", str(cause))

    async def test_zero_wall_timeout_does_not_abort(self) -> None:
        # 0 表示不限制：正常请求不得被误判超时
        engine, _ = _flow_engine(_FastCompletions())
        engine.api_wall_timeout = 0
        result, _token = await engine.ask_chatbot(
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
            max_retry_count=1,
        )
        self.assertEqual(result, "ok")

    async def test_quota_exhaustion_terminates_before_request(self) -> None:
        # 配额已满必须直接终止且不再发请求；预检只读不自增
        engine, _token = _flow_engine(_FastCompletions())
        engine.api_max_requests = 1
        first, _ = await engine.ask_chatbot(
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
            max_retry_count=1,
        )
        self.assertEqual(first, "ok")
        with self.assertRaises(JobCancelledError):
            await engine.ask_chatbot(
                messages=[{"role": "user", "content": "hi"}],
                stream=False,
                max_retry_count=1,
            )
        self.assertEqual(engine._request_count, 1)  # 预检不自增

if __name__ == "__main__":
    unittest.main()
