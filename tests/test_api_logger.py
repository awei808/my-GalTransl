"""ApiLogger 模块功能测试"""
import asyncio
import os
import shutil
import tempfile
import unittest


class ApiLoggerTests(unittest.IsolatedAsyncioTestCase):
    async def test_basic_logging(self) -> None:
        from GalTransl.ApiLogger import api_logger

        tmp = tempfile.mkdtemp()

        t1 = api_logger.begin(tmp, backend="gpt4", model="deepseek-chat",
                              endpoint="https://api.deepseek.com",
                              file="test.txt", prompt_preview="hello\nworld")
        api_logger.record(t1, status="success", latency_ms=1234,
                          prompt_tokens=100, completion_tokens=50,
                          response_preview="你好\n世界")

        t2 = api_logger.begin(tmp, backend="gpt4", model="deepseek-chat",
                              endpoint="https://api.deepseek.com")
        api_logger.record(t2, status="error", latency_ms=567, error="timeout\nretry later")

        await api_logger.shutdown()

        logfile = os.path.join(tmp, "api_calls.log")
        self.assertTrue(os.path.exists(logfile))
        content = open(logfile, encoding="utf-8").read()

        # 验证关键标记
        self.assertIn(">>>", content)
        self.assertIn("-REQ", content)
        self.assertIn("-RESP success", content)
        self.assertIn("---", content)          # 分隔线

        # 验证内容真实换行
        self.assertIn("hello\nworld", content)
        self.assertIn("你好\n世界", content)

        # 错误记录
        self.assertIn("timeout\nretry later", content)
        self.assertIn("<<< error", content)

        shutil.rmtree(tmp, ignore_errors=True)

    async def test_unpaired_begin_flushed_on_shutdown(self) -> None:
        from GalTransl.ApiLogger import api_logger

        tmp = tempfile.mkdtemp()
        api_logger.begin(tmp, backend="orphan", model="x", prompt_preview="orphan prompt")
        await api_logger.shutdown()

        logfile = os.path.join(tmp, "api_calls.log")
        content = open(logfile, encoding="utf-8").read()
        self.assertIn("orphan prompt", content)
        self.assertNotIn("RESP", content)  # 未配对，无响应

        shutil.rmtree(tmp, ignore_errors=True)

    async def test_retry_entries_are_separate(self) -> None:
        from GalTransl.ApiLogger import api_logger

        tmp = tempfile.mkdtemp()
        t1 = api_logger.begin(tmp, backend="test", model="x")
        api_logger.record(t1, status="error", latency_ms=100, retry_count=1, error="timed out")
        t2 = api_logger.begin(tmp, backend="test", model="x")
        api_logger.record(t2, status="success", latency_ms=200, retry_count=2)
        await api_logger.shutdown()

        content = open(os.path.join(tmp, "api_calls.log"), encoding="utf-8").read()
        self.assertEqual(content.count(">>>"), 2)
        self.assertIn("<<< error", content)
        self.assertIn("-RESP success", content)

        shutil.rmtree(tmp, ignore_errors=True)

    async def test_writer_restart(self) -> None:
        from GalTransl.ApiLogger import api_logger

        tmp = tempfile.mkdtemp()
        t1 = api_logger.begin(tmp, backend="a", model="m")
        api_logger.record(t1, status="success")
        await api_logger.shutdown()

        t2 = api_logger.begin(tmp, backend="b", model="m")
        api_logger.record(t2, status="success")
        await api_logger.shutdown()

        content = open(os.path.join(tmp, "api_calls.log"), encoding="utf-8").read()
        self.assertEqual(content.count(">>>"), 2)
        self.assertIn(">>> a m", content)
        self.assertIn(">>> b m", content)

        shutil.rmtree(tmp, ignore_errors=True)

    def test_writer_survives_multiple_event_loops(self) -> None:
        # 复现单例队列跨多次 asyncio.run（每次新 loop）复用导致的
        # "bound to a different event loop"：模拟 server 模式连续多次任务。
        from GalTransl.ApiLogger import api_logger

        tmp = tempfile.mkdtemp()

        async def _job(label: str) -> None:
            tid = api_logger.begin(tmp, backend="b", model="m", file=f"{label}.txt")
            api_logger.record(tid, status="success", latency_ms=10)
            await api_logger.shutdown()

        asyncio.run(_job("first"))
        asyncio.run(_job("second"))  # 旧代码第二次会令 writer 崩溃、日志缺失

        content = open(os.path.join(tmp, "api_calls.log"), encoding="utf-8").read()
        self.assertEqual(content.count(">>>"), 2)
        shutil.rmtree(tmp, ignore_errors=True)

    async def test_record_without_writer_ignored(self) -> None:
        from GalTransl.ApiLogger import api_logger

        await api_logger.shutdown()
        api_logger.record("fake-id", status="success")
        # should not raise

    async def test_trace_id_uniqueness(self) -> None:
        from GalTransl.ApiLogger import _new_trace_id

        ids = {_new_trace_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)

    async def test_concurrent_logging(self) -> None:
        from GalTransl.ApiLogger import api_logger

        tmp = tempfile.mkdtemp()
        traces = [api_logger.begin(tmp, backend=f"b{i}", model=f"m{i}") for i in range(10)]
        for tid in traces:
            api_logger.record(tid, status="success", latency_ms=50,
                              prompt_tokens=10, completion_tokens=5)
        await api_logger.shutdown()

        content = open(os.path.join(tmp, "api_calls.log"), encoding="utf-8").read()
        self.assertEqual(content.count(">>>"), 10)
        self.assertEqual(content.count("RESP success"), 10)
        self.assertEqual(content.count("---"), 10)

        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
