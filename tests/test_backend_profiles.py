"""全局后端配置（backend profile）读写回归测试。

历史上 _BACKEND_PROFILES_PATH 常量曾被误删，导致 _read_backend_profiles /
_write_backend_profiles 调用即抛 NameError（后端配置页、按名提交任务、check-model
全部不可用）。本测试锁定：两个函数可调用且读写一致。
"""

import os
import tempfile
import unittest
from unittest import mock

from GalTransl import server


class BackendProfilesStoreTests(unittest.TestCase):
    def test_read_backend_profiles_returns_empty_profiles_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_path = os.path.join(tmp_dir, "backend_profiles.yaml")
            with mock.patch.object(server, "_BACKEND_PROFILES_PATH", missing_path):
                data = server._read_backend_profiles()
        self.assertEqual({"profiles": {}}, data)

    def test_write_then_read_backend_profiles_roundtrip(self) -> None:
        payload = {
            "profiles": {
                "p1": {
                    "OpenAI-Compatible": {
                        "tokens": [{"token": "sk-test", "endpoint": "https://example.com"}]
                    }
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = os.path.join(tmp_dir, "backend_profiles.yaml")
            with mock.patch.object(server, "_BACKEND_PROFILES_PATH", target):
                server._write_backend_profiles(payload)
                self.assertTrue(os.path.isfile(target))
                data = server._read_backend_profiles()
        self.assertEqual(payload, data)


if __name__ == "__main__":
    unittest.main()
