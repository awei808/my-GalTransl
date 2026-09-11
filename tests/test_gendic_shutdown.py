"""GenDic 字典生成流程收尾：batch_translate 成功/失败/取消/中止时都必须关闭 gptapi 客户端。"""
import unittest

from GalTransl.Frontend import LLMTranslate
from GalTransl.Service import JobCancelledError


class _FakeGptApi:
    def __init__(self, batch_result: bool = True, batch_exc: Exception | None = None):
        self.batch_result = batch_result
        self.batch_exc = batch_exc
        self.batch_called_with = None
        self.shutdown_calls = 0

    async def batch_translate(self, all_jsons):
        self.batch_called_with = all_jsons
        if self.batch_exc is not None:
            raise self.batch_exc
        return self.batch_result

    async def shutdown(self):
        self.shutdown_calls += 1


class _FakeConfig:
    def __init__(self, abort_on_dic_failure: bool = False):
        self.abort_on_dic_failure = abort_on_dic_failure

    def getKey(self, key, default=None):
        if key == "internals.pipeline.abortOnDicFailure":
            return self.abort_on_dic_failure
        return default


class GenDicShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_called_on_success(self) -> None:
        gptapi = _FakeGptApi(batch_result=True)
        ok = await LLMTranslate._run_gendic_flow(_FakeConfig(), ["a", "b"], gptapi)
        self.assertTrue(ok)
        self.assertEqual(gptapi.batch_called_with, ["a", "b"])
        self.assertEqual(gptapi.shutdown_calls, 1)

    async def test_shutdown_called_when_batch_translate_raises(self) -> None:
        gptapi = _FakeGptApi(batch_exc=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            await LLMTranslate._run_gendic_flow(_FakeConfig(), ["a"], gptapi)
        self.assertEqual(gptapi.shutdown_calls, 1)

    async def test_shutdown_called_when_cancelled(self) -> None:
        # 取消信号穿透，但客户端仍需关闭（这是本地此前泄漏的路径）
        gptapi = _FakeGptApi(batch_exc=JobCancelledError())
        with self.assertRaises(JobCancelledError):
            await LLMTranslate._run_gendic_flow(_FakeConfig(), ["a"], gptapi)
        self.assertEqual(gptapi.shutdown_calls, 1)

    async def test_shutdown_called_when_abort_on_dic_failure_raises(self) -> None:
        gptapi = _FakeGptApi(batch_result=False)
        with self.assertRaises(RuntimeError):
            await LLMTranslate._run_gendic_flow(
                _FakeConfig(abort_on_dic_failure=True), ["a"], gptapi
            )
        self.assertEqual(gptapi.shutdown_calls, 1)

    async def test_missing_shutdown_attribute_is_tolerated(self) -> None:
        class _BareApi:
            def __init__(self):
                self.batch_called = False

            async def batch_translate(self, all_jsons):
                self.batch_called = True
                return True

        api = _BareApi()
        ok = await LLMTranslate._run_gendic_flow(_FakeConfig(), ["a"], api)
        self.assertTrue(ok)
        self.assertTrue(api.batch_called)


if __name__ == "__main__":
    unittest.main()
