"""全局后端配置（backend profile）读写回归测试。

历史上 _BACKEND_PROFILES_PATH 常量曾被误删，导致 _read_backend_profiles /
_write_backend_profiles 调用即抛 NameError（后端配置页、按名提交任务、check-model
全部不可用）。本测试锁定：两个函数可调用且读写一致。

0.4.10 起这两个函数与 _BACKEND_PROFILES_PATH 位于 GalTransl.server_backend，
故 patch 目标随之改到该模块（打 `GalTransl.server` 会静默打空 —— 函数在其自身
模块内按模块全局名解析常量，patch 到 server 命名空间不影响它）。
"""

import os
import tempfile
import unittest
from unittest import mock

from GalTransl import server  # noqa: F401  （保留：验证 re-export 面仍可导入）
from GalTransl import server_backend


class BackendProfilesStoreTests(unittest.TestCase):
    def test_read_backend_profiles_returns_empty_profiles_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_path = os.path.join(tmp_dir, "backend_profiles.yaml")
            with mock.patch.object(
                server_backend, "_BACKEND_PROFILES_PATH", missing_path
            ):
                data = server_backend._read_backend_profiles()
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
            with mock.patch.object(server_backend, "_BACKEND_PROFILES_PATH", target):
                server_backend._write_backend_profiles(payload)
                self.assertTrue(os.path.isfile(target))
                data = server_backend._read_backend_profiles()
        self.assertEqual(payload, data)

    def test_server_reexports_same_objects(self) -> None:
        # server.py 必须 re-export 同一函数对象（不是转发壳），
        # 否则外部 `from GalTransl.server import _read_backend_profiles` 拿到的是
        # 另一份实现，patch/行为会分叉。
        self.assertIs(server._read_backend_profiles, server_backend._read_backend_profiles)
        self.assertIs(server._write_backend_profiles, server_backend._write_backend_profiles)
        self.assertIs(server._BACKEND_PROFILES_PATH, server_backend._BACKEND_PROFILES_PATH)


if __name__ == "__main__":
    unittest.main()
