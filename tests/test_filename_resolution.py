"""文件名 NFKC 归一解析器单测（全角/半角写法差异的统一兜底口径）。

覆盖：精确命中优先、NFKC 归一兜底、同名碰撞读路径 first-wins、
写路径歧义拒绝、相对子路径解析与目录段校验。
"""
import os
import shutil
import tempfile
import unittest

from GalTransl.Utils import (
    nfkc_fold,
    resolve_filename,
    resolve_filename_rel,
    resolve_filename_strict,
)


class NfkcFoldTests(unittest.TestCase):
    def test_fullwidth_digits_underscore_fold_to_halfwidth(self) -> None:
        self.assertEqual(nfkc_fold("アペンド＿０３.json"), "アペンド_03.json")

    def test_halfwidth_unchanged(self) -> None:
        self.assertEqual(nfkc_fold("01_共通.json"), "01_共通.json")

    def test_none_like_input_returns_empty(self) -> None:
        self.assertEqual(nfkc_fold(""), "")


class ResolveFilenameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="gt_nfkc_")
        for name in ("アペンド＿０３.json", "01_共通.json"):
            with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
                f.write("{}")

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_exact_hit_wins(self) -> None:
        self.assertEqual(resolve_filename(self.dir, "01_共通.json"), "01_共通.json")

    def test_halfwidth_query_resolves_fullwidth_file(self) -> None:
        self.assertEqual(resolve_filename(self.dir, "アペンド_03.json"), "アペンド＿０３.json")

    def test_fullwidth_query_returns_itself(self) -> None:
        self.assertEqual(resolve_filename(self.dir, "アペンド＿０３.json"), "アペンド＿０３.json")

    def test_no_hit_returns_none(self) -> None:
        self.assertIsNone(resolve_filename(self.dir, "存在しない.json"))
        self.assertIsNone(resolve_filename(self.dir, ""))

    def test_missing_dir_returns_none(self) -> None:
        self.assertIsNone(resolve_filename(os.path.join(self.dir, "无此目录"), "アペンド_03.json"))


class ResolveFilenameAmbiguityTests(unittest.TestCase):
    """读路径 first-wins + 告警；写路径（strict）歧义即拒绝。"""

    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="gt_nfkc_amb_")
        # 两种混写形态，NFKC 归一后同为 "01.json"
        for name in ("０１.json", "0１.json"):
            with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
                f.write("{}")

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_read_path_first_wins(self) -> None:
        hit = resolve_filename(self.dir, "０1.json")
        self.assertIn(hit, ("０１.json", "0１.json"))

    def test_write_path_rejects_ambiguity(self) -> None:
        with self.assertRaises(ValueError):
            resolve_filename_strict(self.dir, "０1.json")

    def test_write_path_exact_hit_ok(self) -> None:
        self.assertEqual(resolve_filename_strict(self.dir, "０１.json"), "０１.json")


class ResolveFilenameRelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="gt_nfkc_rel_")
        os.makedirs(os.path.join(self.dir, "pass3_cache"), exist_ok=True)
        for name in (
            os.path.join("pass3_cache", "アペンド＿０３.json"),
            os.path.join("01.json"),
        ):
            with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
                f.write("{}")

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_bare_name_resolution(self) -> None:
        self.assertEqual(resolve_filename_rel(self.dir, "０1.json"), "01.json")

    def test_subdir_resolution(self) -> None:
        self.assertEqual(
            resolve_filename_rel(self.dir, "pass3_cache/アペンド_03.json"),
            "pass3_cache/アペンド＿０３.json",
        )

    def test_missing_subdir_returns_none(self) -> None:
        self.assertIsNone(resolve_filename_rel(self.dir, "pass2_cache/01.json"))

    def test_traversal_like_input_returns_none(self) -> None:
        self.assertIsNone(resolve_filename_rel(self.dir, "../outside.json"))

    def test_no_hit_returns_none(self) -> None:
        self.assertIsNone(resolve_filename_rel(self.dir, "pass3_cache/存在しない.json"))


if __name__ == "__main__":
    unittest.main()
