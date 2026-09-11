"""HTTP 客户端构建：有界连接池（30s 空闲过期）与 pyreqwest/代理回退分支。"""
import unittest

import httpx

from GalTransl.Backend import BaseEngine as base_engine_module
from GalTransl.Backend.BaseEngine import BaseEngine


class BuildHttpClientTests(unittest.TestCase):
    def test_no_proxy_builds_pyreqwest_transport_client(self) -> None:
        if base_engine_module.HttpxTransport is None:
            self.skipTest("pyreqwest 未安装")
        client = BaseEngine._build_http_client(None)
        self.assertIsInstance(client, httpx.AsyncClient)
        self.assertIsInstance(client._transport, base_engine_module.HttpxTransport)

    def test_builds_bounded_limits_with_30s_keepalive(self) -> None:
        captured: dict = {}

        class _Recorder:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        # 强制走 DefaultAioHttpClient 分支并替换之，捕获传入的 limits
        orig_transport = base_engine_module.HttpxTransport
        orig_client = base_engine_module.DefaultAioHttpClient
        base_engine_module.HttpxTransport = None
        base_engine_module.DefaultAioHttpClient = _Recorder
        try:
            client = BaseEngine._build_http_client(None)
        finally:
            base_engine_module.HttpxTransport = orig_transport
            base_engine_module.DefaultAioHttpClient = orig_client
        self.assertIsInstance(client, _Recorder)
        limits = captured["limits"]
        self.assertEqual(limits.max_connections, 100)
        self.assertEqual(limits.max_keepalive_connections, 20)
        self.assertEqual(limits.keepalive_expiry, 30.0)

    def test_proxy_kwargs_are_forwarded(self) -> None:
        captured: dict = {}

        class _Recorder:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        orig = base_engine_module.DefaultAioHttpClient
        base_engine_module.DefaultAioHttpClient = _Recorder
        try:
            client = BaseEngine._build_http_client("http://127.0.0.1:7890")
        finally:
            base_engine_module.DefaultAioHttpClient = orig
        self.assertIsInstance(client, _Recorder)
        self.assertEqual(captured["trust_env"], False)
        # httpx>=0.28 用 proxy=，旧版用 proxies=（由 build_httpx_proxy_kwargs 决定）
        self.assertTrue({"proxy", "proxies"} & set(captured))
        self.assertEqual(captured["limits"].keepalive_expiry, 30.0)


if __name__ == "__main__":
    unittest.main()
