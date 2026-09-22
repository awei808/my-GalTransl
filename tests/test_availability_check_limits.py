"""HTTP 检测接口的探活上限（0.5.1）。

缺陷背景：`/api/projects/:id/check-model` 走 token 池探活，沿用了翻译期的
apiTimeout（默认 300s）并保留 2 次重试；慢模型下单次探活实测 39s，后端耗时
可远超前端 `apiRequest` 的超时上限，用户看到的是「请求超时」假故障。
修复：HTTP 检测路径把单请求超时封顶、重试降为 1 次，任务启动期检测不受影响。

覆盖：
1. `_availability_check_limits`：超时取 apiTimeout 与上限的较小值；
2. 检测入口确实把上限透传进 token 池；
3. `checkTokenAvailablity()` 的默认行为不变（翻译期零回归）。
"""
import unittest
from types import SimpleNamespace

try:
    from GalTransl.COpenAI import COpenAIToken, COpenAITokenPool
    from GalTransl.server_backend import (
        _AVAILABILITY_CHECK_MAX_RETRIES,
        _AVAILABILITY_CHECK_TIMEOUT_CAP,
        _availability_check_limits,
    )
except ImportError:  # 精简环境缺依赖时跳过
    raise unittest.SkipTest("GalTransl 依赖不可用")


def _make_pool(tokens: list, timeout=300) -> COpenAITokenPool:
    """构造最小 token 池桩（不读全局配置，探活走替身）。"""
    pool = COpenAITokenPool.__new__(COpenAITokenPool)
    pool.tokens = [(True, t) for t in tokens]
    pool.timeout = timeout
    pool.pj_config = SimpleNamespace(
        non_interactive=True,
        stop_event=None,
        getBackendConfigSection=lambda _name: {"checkAvailableConcurrency": 2},
    )
    pool._raise_if_stop_requested = lambda: None
    return pool


class AvailabilityCheckLimitsTests(unittest.TestCase):
    def test_caps_slow_backend_default_timeout(self) -> None:
        limits = _availability_check_limits(SimpleNamespace(timeout=300))
        self.assertEqual(limits["timeout"], _AVAILABILITY_CHECK_TIMEOUT_CAP)
        self.assertEqual(limits["max_retries"], _AVAILABILITY_CHECK_MAX_RETRIES)

    def test_keeps_smaller_configured_timeout(self) -> None:
        # 用户把 apiTimeout 调小时，检测不该反而等更久
        self.assertEqual(_availability_check_limits(SimpleNamespace(timeout=10))["timeout"], 10)

    def test_falls_back_when_timeout_missing_or_invalid(self) -> None:
        for bad in (None, "abc", object()):
            with self.subTest(value=bad):
                limits = _availability_check_limits(SimpleNamespace(timeout=bad))
                self.assertEqual(limits["timeout"], _AVAILABILITY_CHECK_TIMEOUT_CAP)
        self.assertEqual(
            _availability_check_limits(SimpleNamespace())["timeout"],
            _AVAILABILITY_CHECK_TIMEOUT_CAP,
        )

    def test_retry_is_reduced(self) -> None:
        # 必须小于历史默认 2 次，否则封顶超时也救不了总耗时
        self.assertLess(_AVAILABILITY_CHECK_MAX_RETRIES, 2)


class CheckTokenAvailabilityParamsTests(unittest.IsolatedAsyncioTestCase):
    async def _capture_kwargs(self, **call_kwargs) -> list:
        captured: list = []
        token = COpenAIToken("sk-x", "https://example.com", "m")
        pool = _make_pool([token])

        async def fake_check(tok, proxy=None, **kwargs):
            captured.append(kwargs)
            return True, tok

        pool._check_token_availability_with_retry = fake_check
        await pool.checkTokenAvailablity(**call_kwargs)
        return captured

    async def test_passes_limits_through(self) -> None:
        captured = await self._capture_kwargs(
            timeout=_AVAILABILITY_CHECK_TIMEOUT_CAP,
            max_retries=_AVAILABILITY_CHECK_MAX_RETRIES,
        )
        self.assertEqual(captured, [{"max_retries": _AVAILABILITY_CHECK_MAX_RETRIES,
                                     "timeout": _AVAILABILITY_CHECK_TIMEOUT_CAP}])

    async def test_default_behaviour_unchanged(self) -> None:
        # 任务启动期检测（llm_runtime）不传参：仍为 2 次重试 + 沿用 apiTimeout
        captured = await self._capture_kwargs()
        self.assertEqual(captured, [{"max_retries": 2, "timeout": None}])

    async def test_nonpositive_max_retries_is_clamped(self) -> None:
        captured = await self._capture_kwargs(max_retries=0)
        self.assertEqual(captured[0]["max_retries"], 1)


class TokenAvailabilityTimeoutPlumbingTests(unittest.TestCase):
    def test_is_token_available_sync_honours_override(self) -> None:
        """单请求超时覆盖必须真的落到 OpenAI 客户端调用参数上。"""
        from unittest.mock import patch

        calls: list = []

        class _Completions:
            def create(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(choices=[object()])

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=_Completions())
        )
        pool_stub = SimpleNamespace(
            timeout=300, _record_runtime_error=lambda **kw: None, bar=lambda *a, **k: None
        )
        token = COpenAIToken("sk-x", "https://example.com", "m", stream=False)
        with patch("GalTransl.COpenAI.OpenAI", return_value=fake_client):
            COpenAITokenPool._isTokenAvailable_sync(pool_stub, token, None, 30)
            COpenAITokenPool._isTokenAvailable_sync(pool_stub, token, None, None)
        self.assertEqual(calls[0]["timeout"], 30)
        self.assertEqual(calls[1]["timeout"], 300)


if __name__ == "__main__":
    unittest.main()
