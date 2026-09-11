"""token 探活乱序修复：可用性检查完成顺序随机时，token 池仍保持配置顺序。

fallback 轮换语义承诺「优先配置的第一个」，探活结果按完成顺序收集会让
该顺序漂移（本地重试包装与并发信号量进一步放大完成时间差）。
"""
import asyncio
import unittest
from types import SimpleNamespace

from GalTransl.COpenAI import COpenAIToken, COpenAITokenPool


def _make_pool(tokens: list[COpenAIToken]) -> COpenAITokenPool:
    pool = COpenAITokenPool.__new__(COpenAITokenPool)
    pool.tokens = [(True, t) for t in tokens]
    # non_interactive=True 抑制终端进度条
    pool.pj_config = SimpleNamespace(
        non_interactive=True,
        stop_event=None,
        getBackendConfigSection=lambda _name: {"checkAvailableConcurrency": 2},
    )
    pool._raise_if_stop_requested = lambda: None
    return pool


class TokenFallbackOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_availability_checks_keep_configured_order(self) -> None:
        first = COpenAIToken("first", "https://first.example", "model-a")
        second = COpenAIToken("second", "https://second.example", "model-b")
        pool = _make_pool([first, second])

        async def check_token(token, proxy=None):
            # 第二个端点先完成，结果顺序也不能变
            await asyncio.sleep(0.03 if token is first else 0.0)
            return True, token

        pool._check_token_availability_with_retry = check_token

        await pool.checkTokenAvailablity()

        self.assertEqual(
            [token.model_name for _, token in pool.tokens], ["model-a", "model-b"]
        )

    async def test_survivors_keep_order_after_unavailable_removed(self) -> None:
        # 剔除不可用 token 的场景上游未覆盖：失败的 key 先完成、
        # 幸存者后完成时，旧实现会把幸存者顺序也打乱
        first = COpenAIToken("first", "https://first.example", "model-a")
        second = COpenAIToken("second", "https://second.example", "model-b")
        third = COpenAIToken("third", "https://third.example", "model-c")
        pool = _make_pool([first, second, third])

        async def check_token(token, proxy=None):
            if token is second:
                return False, token  # 失败的先完成
            await asyncio.sleep(0.03 if token is first else 0.0)
            return True, token

        pool._check_token_availability_with_retry = check_token

        await pool.checkTokenAvailablity()

        self.assertEqual(
            [token.model_name for _, token in pool.tokens], ["model-a", "model-c"]
        )


if __name__ == "__main__":
    unittest.main()
