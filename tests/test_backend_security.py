import os
import unittest
from unittest import mock

from GalTransl.backend_security import (
    load_allowed_origins,
    load_api_token,
    origin_allowed,
    safe_under_project,
    token_ok,
)


class OriginAllowlistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.allowed = load_allowed_origins()

    def test_default_desktop_origins_allowed(self) -> None:
        for origin in (
            "tauri://localhost",
            "http://127.0.0.1:1420",
            "http://localhost:1420",
            "http://localhost:12333",
            "http://127.0.0.1:12333",
        ):
            self.assertTrue(origin_allowed(origin, self.allowed))

    def test_arbitrary_origin_denied(self) -> None:
        self.assertFalse(origin_allowed("https://evil.example.com", self.allowed))
        self.assertFalse(origin_allowed(None, self.allowed))
        self.assertFalse(origin_allowed("", self.allowed))

    def test_extra_origin_via_env(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"GALTRANSL_ALLOWED_ORIGINS": "https://app.example.com, https://b.example.com/"},
        ):
            allowed = load_allowed_origins()
        self.assertIn("https://app.example.com", allowed)
        self.assertIn("https://b.example.com", allowed)


class WriteAuthTokenTests(unittest.TestCase):
    def test_no_token_means_open(self) -> None:
        self.assertTrue(token_ok(None, ""))
        self.assertTrue(token_ok("Bearer whatever", ""))

    def test_token_required_and_matches(self) -> None:
        self.assertTrue(token_ok("Bearer secret", "secret"))

    def test_token_scheme_case_insensitive(self) -> None:
        self.assertTrue(token_ok("bearer secret", "secret"))
        self.assertTrue(token_ok("BEARER secret", "secret"))

    def test_token_malformed_header_rejected(self) -> None:
        self.assertFalse(token_ok("secret", "secret"))  # 无方案名
        self.assertFalse(token_ok("Basic secret", "secret"))  # 错误方案
        self.assertFalse(token_ok("", "secret"))

    def test_token_required_and_mismatches(self) -> None:
        self.assertFalse(token_ok(None, "secret"))
        self.assertFalse(token_ok("Bearer wrong", "secret"))
        self.assertFalse(token_ok("secret", "secret"))

    def test_load_api_token_reads_env(self) -> None:
        with mock.patch.dict(os.environ, {"GALTRANSL_API_TOKEN": "  xyz  "}):
            self.assertEqual(load_api_token(), "xyz")


class SafeUnderProjectTests(unittest.TestCase):
    def test_relative_path_resolves_inside(self) -> None:
        got = safe_under_project("/proj", "gt_input/file.txt")
        self.assertEqual(got, os.path.normpath("/proj/gt_input/file.txt"))

    def test_absolute_path_rejected(self) -> None:
        with self.assertRaises(ValueError):
            safe_under_project("/proj", "/etc/passwd")

    def test_traversal_rejected(self) -> None:
        with self.assertRaises(ValueError):
            safe_under_project("/proj", "../outside")

    def test_empty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            safe_under_project("/proj", "")

    def test_project_root_subpath_allowed(self) -> None:
        # 项目恰为文件系统根时，子路径不应被误判为越界
        self.assertEqual(safe_under_project("/", "sub"), os.path.normpath("/sub"))

    def test_dot_and_parent_inside_allowed(self) -> None:
        # 解析后仍在项目内则放行
        self.assertEqual(safe_under_project("/proj", "."), os.path.normpath("/proj"))
        self.assertEqual(
            safe_under_project("/proj", "a/.."), os.path.normpath("/proj")
        )
        self.assertEqual(
            safe_under_project("/proj", "a/../b"), os.path.normpath("/proj/b")
        )

    def test_traversal_escaping_via_dotdot_rejected(self) -> None:
        with self.assertRaises(ValueError):
            safe_under_project("/proj", "a/../../b")


if __name__ == "__main__":
    unittest.main()
