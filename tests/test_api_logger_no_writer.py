"""ApiLogger 写入关闭时不入队的回归测试（M11）。"""

import tempfile
import unittest
from unittest import mock

from GalTransl.ApiLogger import ApiLogger


class ApiLoggerNoWriterTests(unittest.IsolatedAsyncioTestCase):
    async def test_begin_does_not_enqueue_when_writer_disabled(self) -> None:
        logger = ApiLogger()
        with mock.patch(
            "GalTransl.ApiLogger.load_app_settings",
            return_value={"writeApiCallLog": False},
        ):
            trace_id = logger.begin(
                r"C:\tmp\proj", backend="gpt", model="m", prompt_preview="x" * 2000
            )
        self.assertTrue(trace_id)
        # 写入未启用：请求不入队，避免队列无界增长
        self.assertTrue(logger._queue.empty())

    async def test_begin_enqueues_when_writer_enabled(self) -> None:
        logger = ApiLogger()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "GalTransl.ApiLogger.load_app_settings",
                return_value={"writeApiCallLog": True},
            ):
                trace_id = logger.begin(tmp, backend="gpt", model="m", prompt_preview="x")
            self.assertTrue(trace_id)
            self.assertFalse(logger._queue.empty())
            await logger.shutdown()


if __name__ == "__main__":
    unittest.main()
