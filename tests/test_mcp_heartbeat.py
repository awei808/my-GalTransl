"""mcp_heartbeat 心跳模块与 /api/mcp-status 端点单测（0.5.1）。"""
import os
import tempfile
import time
import unittest
from unittest import mock

from GalTransl import mcp_heartbeat
from GalTransl.server_handlers_root import do_get


class HeartbeatFileTests(unittest.TestCase):
    """心跳读写放在临时"程序目录"里，避免污染真实程序目录。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.app_dir = self._tmp.name
        self._patch = mock.patch.object(mcp_heartbeat, "resolve_app_dir", return_value=self.app_dir)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_path_is_under_app_dir(self) -> None:
        self.assertEqual(
            mcp_heartbeat.heartbeat_path(),
            os.path.join(self.app_dir, mcp_heartbeat.HEARTBEAT_FILENAME),
        )

    def test_write_then_read_marks_available(self) -> None:
        self.assertTrue(mcp_heartbeat.write_heartbeat(tools_count=11))
        status = mcp_heartbeat.read_heartbeat()
        self.assertTrue(status["available"])
        self.assertEqual(status["tools"], 11)
        self.assertEqual(status["pid"], os.getpid())
        self.assertEqual(status["stale_after_seconds"], mcp_heartbeat.HEARTBEAT_STALE_SECONDS)
        self.assertIsNotNone(status["age_seconds"])

    def test_missing_file_is_unavailable(self) -> None:
        status = mcp_heartbeat.read_heartbeat()
        self.assertFalse(status["available"])
        self.assertIsNone(status["age_seconds"])
        self.assertEqual(status["tools"], 0)
        self.assertEqual(status["pid"], None)

    def test_stale_heartbeat_is_unavailable(self) -> None:
        mcp_heartbeat.write_heartbeat(tools_count=11)
        stale_now = time.time() + mcp_heartbeat.HEARTBEAT_STALE_SECONDS + 5
        status = mcp_heartbeat.read_heartbeat(now=stale_now)
        self.assertFalse(status["available"])
        self.assertGreater(status["age_seconds"], mcp_heartbeat.HEARTBEAT_STALE_SECONDS)

    def test_refresh_keeps_started_at(self) -> None:
        mcp_heartbeat.write_heartbeat(tools_count=1)
        first_started = mcp_heartbeat.read_heartbeat()["started_at"]
        mcp_heartbeat.write_heartbeat(tools_count=2)
        status = mcp_heartbeat.read_heartbeat()
        self.assertEqual(status["started_at"], first_started)
        self.assertTrue(first_started)
        self.assertEqual(status["tools"], 2)

    def test_remove_deletes_file_and_is_idempotent(self) -> None:
        mcp_heartbeat.write_heartbeat()
        path = mcp_heartbeat.heartbeat_path()
        self.assertTrue(os.path.isfile(path))
        mcp_heartbeat.remove_heartbeat()
        self.assertFalse(os.path.isfile(path))
        mcp_heartbeat.remove_heartbeat()

    def test_corrupt_content_is_unavailable(self) -> None:
        with open(mcp_heartbeat.heartbeat_path(), "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.assertFalse(mcp_heartbeat.read_heartbeat()["available"])

    def test_non_dict_payload_is_unavailable(self) -> None:
        with open(mcp_heartbeat.heartbeat_path(), "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")
        self.assertFalse(mcp_heartbeat.read_heartbeat()["available"])

    def test_write_failure_returns_false_without_raising(self) -> None:
        broken = os.path.join(self.app_dir, "missing_dir", mcp_heartbeat.HEARTBEAT_FILENAME)
        with mock.patch.object(mcp_heartbeat, "heartbeat_path", return_value=broken):
            self.assertFalse(mcp_heartbeat.write_heartbeat())


class McpStatusEndpointTests(unittest.TestCase):
    """端点只做转发：拿到心跳状态对象即算通过。"""

    def test_endpoint_returns_heartbeat_payload(self) -> None:
        handler = mock.MagicMock()
        handler.path = "/api/mcp-status"
        with mock.patch(
            "GalTransl.server_handlers_root.read_heartbeat",
            return_value={"available": True, "tools": 11},
        ) as fake:
            do_get(handler, mock.MagicMock())
        fake.assert_called_once()
        handler._send_json.assert_called_once_with({"available": True, "tools": 11})


if __name__ == "__main__":
    unittest.main()
