"""可用性检测容忍 provider 对 max_tokens 的下限要求。

检测请求带 max_tokens=16（不能取 1：有的家要求必须大于某个值），provider 若抱怨这个
参数（创建时或流式迭代中），摘掉该参数重试一次；与参数无关的报错照旧算不可用、只发
一次请求；TypeError 兜底（兼容实现不认识 max_tokens）保留。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    from GalTransl.COpenAI import (
        AVAILABILITY_CHECK_MAX_TOKENS,
        COpenAIToken,
        COpenAITokenPool,
        _rejects_max_tokens,
    )
except ImportError:  # 精简环境缺依赖时跳过
    raise unittest.SkipTest("GalTransl.COpenAI 依赖不可用")


class _Reply:
    """非流式响应：只需要 len(choices) 可判。"""

    def __init__(self, n_choices: int) -> None:
        self.choices = [object()] * n_choices


class _StreamReply:
    """流式响应：chunk 位置放异常实例则在该处抛错。"""

    def __init__(self, chunks: list) -> None:
        self._chunks = list(chunks)

    def __iter__(self):
        def gen():
            for chunk in self._chunks:
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk

        return gen()


class _FakeCompletions:
    """按脚本逐次返回/抛错，并记录每次调用的 kwargs。"""

    def __init__(self, script: list) -> None:
        self.script = script
        self.calls: list = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        step = self.script[len(self.calls) - 1]
        if isinstance(step, Exception):
            raise step
        return step


class _FakeClient:
    def __init__(self, script: list) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(script))


def _token(stream: bool = False) -> COpenAIToken:
    return COpenAIToken("sk-test-token", "https://example.com", "m", stream=stream)


def _run(script: list, stream: bool = False):
    """以桩替身直接调 _isTokenAvailable_sync，返回 (结果, completions桩)。"""
    completions = _FakeClient(script).chat.completions
    pool_stub = SimpleNamespace(timeout=5, _record_runtime_error=lambda **kw: None)
    with patch("GalTransl.COpenAI.OpenAI", return_value=SimpleNamespace(chat=SimpleNamespace(completions=completions))):
        result = COpenAITokenPool._isTokenAvailable_sync(pool_stub, _token(stream), None)
    return result, completions


class MaxTokensToleranceTests(unittest.TestCase):
    def test_check_cap_is_not_one(self) -> None:
        # 上限必须大于 1：有的家对 max_tokens 有下限，取 1 会误判可用后端
        _, completions = _run([_Reply(1)])
        self.assertEqual(completions.calls[0]["max_tokens"], AVAILABILITY_CHECK_MAX_TOKENS)
        self.assertGreater(AVAILABILITY_CHECK_MAX_TOKENS, 2)

    def test_create_rejecting_max_tokens_retries_without_it(self) -> None:
        err = Exception("400 max_tokens must be greater than 2")
        (ok, _), completions = _run([err, _Reply(1)])
        self.assertTrue(ok)
        self.assertEqual(len(completions.calls), 2)
        self.assertNotIn("max_tokens", completions.calls[1])

    def test_stream_iteration_error_also_retries(self) -> None:
        err = Exception("max_tokens must be greater than 2")
        (ok, _), completions = _run([_StreamReply([err]), _StreamReply([_Reply(1)])], stream=True)
        self.assertTrue(ok)
        self.assertEqual(len(completions.calls), 2)
        self.assertNotIn("max_tokens", completions.calls[1])

    def test_unrelated_error_fails_after_single_attempt(self) -> None:
        (ok, token), completions = _run([Exception("401 invalid api key")])
        self.assertFalse(ok)
        self.assertEqual(token.token, "sk-test-token")
        self.assertEqual(len(completions.calls), 1)

    def test_typeerror_fallback_still_works(self) -> None:
        # 兼容实现不认识 max_tokens 参数：TypeError 老兜底摘参重试
        (ok, _), completions = _run([TypeError("unexpected keyword argument 'max_tokens'"), _Reply(1)])
        self.assertTrue(ok)
        self.assertEqual(len(completions.calls), 2)
        self.assertNotIn("max_tokens", completions.calls[1])

    def test_non_stream_empty_choices_is_unavailable(self) -> None:
        (ok, _) = _run([_Reply(0)])[0]
        self.assertFalse(ok)

    def test_empty_stream_is_unavailable(self) -> None:
        (ok, _) = _run([_StreamReply([])], stream=True)[0]
        self.assertFalse(ok)


class RejectsMaxTokensTests(unittest.TestCase):
    def test_matches_max_tokens_complaints_only(self) -> None:
        self.assertTrue(_rejects_max_tokens(Exception("max_tokens must be greater than 2")))
        self.assertTrue(_rejects_max_tokens(Exception("Max Tokens exceeds limit")))
        self.assertFalse(_rejects_max_tokens(Exception("401 invalid api key")))
        self.assertFalse(_rejects_max_tokens(Exception("")))


if __name__ == "__main__":
    unittest.main()
