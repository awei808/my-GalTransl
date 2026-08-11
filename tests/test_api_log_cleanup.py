"""api_calls.log 日志清理功能测试"""
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from GalTransl.ApiLogger import (
    _API_LOG_MAX_BYTES,
    _API_LOG_RETAIN_HOURS,
    _API_LOG_TRIM_HOURS,
    _parse_log_timestamp,
    _trim_log_file,
    cleanup_api_log,
)


def _ts(dt: datetime) -> str:
    """按 api_calls.log 行前缀格式输出时间戳（无年份）。"""
    return dt.strftime("%m-%d %H:%M:%S")


def _make_entry(ts: str, tid: str, body: str = "prompt body\n") -> str:
    """构造一条完整日志记录：请求头 + 内容 + 响应头 + 边界。"""
    return (
        f"[{ts}][API] {tid} >>> backend model stream\n"
        f"{body}"
        f"[{ts}][API] {tid} -RESP success 100ms 10t\n"
        f"---\n"
    )


class ApiLogTimestampTests(unittest.TestCase):
    def test_parse_valid_timestamp(self) -> None:
        now = datetime.now()
        ts = _parse_log_timestamp(f"[{_ts(now)}][API] abc >>> x")
        self.assertIsNotNone(ts)
        self.assertEqual((ts.month, ts.day, ts.hour, ts.minute, ts.second),
                         (now.month, now.day, now.hour, now.minute, now.second))

    def test_parse_no_prefix_returns_none(self) -> None:
        self.assertIsNone(_parse_log_timestamp("prompt body line"))

    def test_parse_short_month_year_boundary(self) -> None:
        # 1 月上旬解析 12 月的记录，应归入上一年
        past = datetime.now() - timedelta(days=40)
        ts = _parse_log_timestamp(f"[{_ts(past)}][API] abc >>> x")
        self.assertIsNotNone(ts)
        self.assertLessEqual(ts - datetime.now(), timedelta(days=1))

    def test_parse_invalid_date_returns_none(self) -> None:
        self.assertIsNone(_parse_log_timestamp("[99-99 99:99:99][API] x"))


class ApiLogTrimTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._log_path = os.path.join(self._tmp, "api_calls.log")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))

    def _write_log(self, content: str) -> None:
        with open(self._log_path, "w", encoding="utf-8") as fh:
            fh.write(content)

    def _read_log(self) -> str:
        with open(self._log_path, encoding="utf-8") as fh:
            return fh.read()

    def test_trim_removes_old_keeps_recent(self) -> None:
        old = datetime.now() - timedelta(hours=40)
        recent = datetime.now() - timedelta(hours=1)
        self._write_log(
            _make_entry(_ts(old), "old1")
            + _make_entry(_ts(recent), "new1")
        )
        cut = datetime.now() - timedelta(hours=_API_LOG_RETAIN_HOURS)
        changed = _trim_log_file(self._log_path, cut)
        self.assertTrue(changed)
        content = self._read_log()
        self.assertNotIn("old1", content)
        self.assertIn("new1", content)

    def test_trim_all_old_empties_file(self) -> None:
        old = datetime.now() - timedelta(hours=50)
        self._write_log(_make_entry(_ts(old), "old1") + _make_entry(_ts(old), "old2"))
        cut = datetime.now() - timedelta(hours=_API_LOG_RETAIN_HOURS)
        changed = _trim_log_file(self._log_path, cut)
        self.assertTrue(changed)
        self.assertEqual(self._read_log(), "")

    def test_trim_all_recent_unchanged(self) -> None:
        recent = datetime.now() - timedelta(hours=1)
        content = _make_entry(_ts(recent), "new1")
        self._write_log(content)
        cut = datetime.now() - timedelta(hours=_API_LOG_RETAIN_HOURS)
        changed = _trim_log_file(self._log_path, cut)
        self.assertFalse(changed)
        self.assertEqual(self._read_log(), content)

    def test_trim_keeps_partial_trace_at_boundary(self) -> None:
        # 边界前有半条记录：请求头在 cutoff 前、响应头在 cutoff 后，截断后只保留响应段
        old = datetime.now() - timedelta(hours=40)
        recent = datetime.now() - timedelta(hours=1)
        self._write_log(
            f"[{_ts(old)}][API] old >>> x\n"
            f"old body\n"
            f"[{_ts(recent)}][API] old -RESP success 1ms 1t\n"
            f"---\n"
            f"[{_ts(recent)}][API] new >>> x\n"
            f"---\n"
        )
        cut = datetime.now() - timedelta(hours=_API_LOG_RETAIN_HOURS)
        _trim_log_file(self._log_path, cut)
        content = self._read_log()
        self.assertNotIn("old body", content)
        self.assertIn("new", content)


class ApiLogCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._log_path = os.path.join(self._tmp, "api_calls.log")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))

    def test_cleanup_missing_file_returns_defaults(self) -> None:
        retained, size = cleanup_api_log(os.path.join(self._tmp, "nope.log"))
        self.assertEqual(retained, _API_LOG_RETAIN_HOURS)
        self.assertEqual(size, 0)

    def test_cleanup_removes_old_records(self) -> None:
        old = datetime.now() - timedelta(hours=40)
        recent = datetime.now() - timedelta(hours=1)
        with open(self._log_path, "w", encoding="utf-8") as fh:
            fh.write(
                _make_entry(_ts(old), "old1")
                + _make_entry(_ts(recent), "new1")
            )
        retained, _size = cleanup_api_log(self._log_path)
        self.assertEqual(retained, _API_LOG_RETAIN_HOURS)
        with open(self._log_path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertNotIn("old1", content)
        self.assertIn("new1", content)

    def test_cleanup_retrims_when_oversize(self) -> None:
        # 构造：旧记录在 36h 前、中记录在 20h 前、新记录在 2h 前
        old = datetime.now() - timedelta(hours=40)
        mid = datetime.now() - timedelta(hours=20)
        fresh = datetime.now() - timedelta(hours=2)
        with open(self._log_path, "w", encoding="utf-8") as fh:
            fh.write(
                _make_entry(_ts(old), "old1")
                + _make_entry(_ts(mid), "mid1")
                + _make_entry(_ts(fresh), "fresh1")
            )
        # 把大小上限压到 150：mid+fresh 超限迫使降档，18h 清掉 mid 后 fresh 达标
        with patch("GalTransl.ApiLogger._API_LOG_MAX_BYTES", 150):
            retained, size = cleanup_api_log(self._log_path)
        self.assertEqual(retained, 18)
        with open(self._log_path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertNotIn("old1", content)
        self.assertNotIn("mid1", content)
        self.assertIn("fresh1", content)
        self.assertLessEqual(size, 150)

    def test_cleanup_retrims_until_min_hours(self) -> None:
        # 所有记录都在 4h 内且超限：降档应停在最后一个时限（4h），不死循环
        fresh = datetime.now() - timedelta(hours=1)
        with open(self._log_path, "w", encoding="utf-8") as fh:
            fh.write(_make_entry(_ts(fresh), "fresh1"))
        with patch("GalTransl.ApiLogger._API_LOG_MAX_BYTES", 1):
            retained, _size = cleanup_api_log(self._log_path)
        self.assertEqual(retained, _API_LOG_TRIM_HOURS[-1])

    def test_cleanup_unreachable_size_keeps_current_hours(self) -> None:
        # 4h 内记录仍超限时，返回当前采用的时限，而非继续循环
        fresh = datetime.now() - timedelta(hours=1)
        with open(self._log_path, "w", encoding="utf-8") as fh:
            fh.write(_make_entry(_ts(fresh), "fresh1"))
        with patch("GalTransl.ApiLogger._API_LOG_MAX_BYTES", 1):
            retained, _size = cleanup_api_log(self._log_path)
        self.assertEqual(retained, _API_LOG_TRIM_HOURS[-1])


if __name__ == "__main__":
    unittest.main()
