"""任务日志级别隔离的回归测试（M1 修正版）。

验证 server 模式的日志装配原则：
1. logger 层必须放行 DEBUG（isEnabledFor 在 logger 层过滤，仅设 handler 级别无效）；
2. 各 job 的 handler 按本 job loggingLevel 过滤，互不干扰；
3. _ServerStatusFilter(debug_enabled) 对 DEBUG 的放行/拒绝语义。
"""

import io
import logging
import threading
import unittest

from GalTransl.Runner import _JobThreadFilter, _ServerStatusFilter


class JobLogLevelIsolationTests(unittest.TestCase):
    def test_handler_level_alone_insufficient_when_logger_is_info(self) -> None:
        # 回归防护：logger 级别 INFO 时 handler.setLevel(DEBUG) 无效（record 在 logger 层被丢弃）
        logger = logging.getLogger("m1_logger_info")
        logger.handlers.clear()
        logger.setLevel(logging.INFO)
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
        logger.addHandler(handler)

        logger.debug("should-not-appear")
        logger.info("visible-info")
        out = stream.getvalue()
        self.assertNotIn("should-not-appear", out)
        self.assertIn("visible-info", out)
        logger.handlers.clear()

    def test_debug_and_info_jobs_isolated_by_handler_level(self) -> None:
        # server 模式设置：logger 放行 DEBUG + 各 handler 按 job level 过滤
        logger = logging.getLogger("m1_isolation")
        logger.handlers.clear()
        logger.setLevel(logging.DEBUG)

        def assemble(level: int, debug_enabled: bool) -> io.StringIO:
            stream = io.StringIO()
            handler = logging.StreamHandler(stream)
            handler.setLevel(level)
            handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
            handler.addFilter(_JobThreadFilter())
            handler.addFilter(_ServerStatusFilter(debug_enabled=debug_enabled))
            logger.addHandler(handler)
            return stream

        debug_stream = assemble(logging.DEBUG, True)
        info_stream = assemble(logging.INFO, False)

        logger.debug("debug-only")
        logger.info("[job] 任务开始")  # 允许前缀，INFO 放行

        self.assertIn("debug-only", debug_stream.getvalue())
        self.assertNotIn("debug-only", info_stream.getvalue())
        self.assertIn("[job] 任务开始", debug_stream.getvalue())
        self.assertIn("[job] 任务开始", info_stream.getvalue())
        logger.handlers.clear()

    def test_server_status_filter_blocks_debug_when_not_debug_job(self) -> None:
        logger = logging.getLogger("m1_filter")
        logger.handlers.clear()
        logger.setLevel(logging.DEBUG)
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
        handler.addFilter(_ServerStatusFilter(debug_enabled=False))
        logger.addHandler(handler)

        logger.debug("blocked")
        logger.info(">>> 开始翻译")
        logger.warning("warn-visible")
        out = stream.getvalue()
        self.assertNotIn("blocked", out)
        self.assertIn(">>> 开始翻译", out)
        self.assertIn("warn-visible", out)
        logger.handlers.clear()

    def test_thread_filter_isolates_concurrent_jobs(self) -> None:
        # 并发 job：子线程的 DEBUG 不进主线程 handler
        logger = logging.getLogger("m1_thread")
        logger.handlers.clear()
        logger.setLevel(logging.DEBUG)
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
        handler.addFilter(_JobThreadFilter())
        logger.addHandler(handler)

        def emit_from_other_thread() -> None:
            logger.debug("other-thread-debug")

        t = threading.Thread(target=emit_from_other_thread)
        t.start()
        t.join()
        logger.debug("main-thread-debug")

        out = stream.getvalue()
        self.assertIn("main-thread-debug", out)
        self.assertNotIn("other-thread-debug", out)
        logger.handlers.clear()


if __name__ == "__main__":
    unittest.main()
