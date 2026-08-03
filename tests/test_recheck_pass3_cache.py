"""测试停止翻译后的自动重检：recheck_pass3_cache_files 对合并后的缓存 json 刷新 problem。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import orjson

from GalTransl.server import recheck_pass3_cache_files


def _make_entry(index: int, pre_src: str, pre_dst: str) -> dict:
    return {
        "index": index,
        "name": "",
        "pre_src": pre_src,
        "post_src": pre_src,
        "pre_dst": pre_dst,
        "proofread_dst": "",
        "trans_by": "model",
        "proofread_by": "",
    }


class RecheckPass3CacheFilesTests(unittest.TestCase):
    def _write_pass3_json(self, cache_dir: str, entries: list) -> str:
        pass3_dir = os.path.join(cache_dir, "pass3_cache")
        os.makedirs(pass3_dir, exist_ok=True)
        file_path = os.path.join(pass3_dir, "demo.json")
        with open(file_path, "wb") as f:
            f.write(orjson.dumps(entries, option=orjson.OPT_INDENT_2))
        return file_path

    def _read_entries(self, file_path: str) -> list:
        with open(file_path, "rb") as f:
            return orjson.loads(f.read())

    def test_recheck_writes_back_problem_and_preview(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            entries = [_make_entry(0, "こんにちは", "你好")]
            file_path = self._write_pass3_json(cache_dir, entries)

            fake_cfg = SimpleNamespace()
            results = [
                {
                    "index": 0,
                    "problem": "长句丢失换行",
                    "post_dst_preview": "你好",
                    "skip_check": False,
                }
            ]
            with patch(
                "GalTransl.server._run_problem_detection", return_value=(results, True)
            ):
                n = recheck_pass3_cache_files(cache_dir, fake_cfg, None, None, None, [])

            self.assertEqual(n, 1)
            merged = self._read_entries(file_path)
            self.assertEqual(merged[0]["problem"], "长句丢失换行")
            self.assertEqual(merged[0]["post_dst_preview"], "你好")

    def test_recheck_skips_file_when_detection_failed(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            entries = [_make_entry(0, "こんにちは", "你好")]
            entries[0]["problem"] = "残留日文"  # 已有 problem，检测失败时不应被抹掉
            file_path = self._write_pass3_json(cache_dir, entries)

            fake_cfg = SimpleNamespace()
            results = [
                {
                    "index": 0,
                    "problem": "",
                    "post_dst_preview": "",
                    "skip_check": False,
                }
            ]
            with patch(
                "GalTransl.server._run_problem_detection", return_value=(results, False)
            ):
                n = recheck_pass3_cache_files(cache_dir, fake_cfg, None, None, None, [])

            self.assertEqual(n, 0)
            merged = self._read_entries(file_path)
            self.assertEqual(merged[0]["problem"], "残留日文")

    def test_recheck_clears_stale_problem_when_none_found(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            entries = [_make_entry(0, "こんにちは", "你好")]
            entries[0]["problem"] = "残留日文"
            file_path = self._write_pass3_json(cache_dir, entries)

            fake_cfg = SimpleNamespace()
            results = [
                {
                    "index": 0,
                    "problem": "",
                    "post_dst_preview": "你好",
                    "skip_check": False,
                }
            ]
            with patch(
                "GalTransl.server._run_problem_detection", return_value=(results, True)
            ):
                n = recheck_pass3_cache_files(cache_dir, fake_cfg, None, None, None, [])

            self.assertEqual(n, 1)
            merged = self._read_entries(file_path)
            self.assertNotIn("problem", merged[0])
            self.assertEqual(merged[0]["post_dst_preview"], "你好")

    def test_recheck_returns_zero_without_pass3_dir(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            n = recheck_pass3_cache_files(cache_dir, None, None, None, None, [])
            self.assertEqual(n, 0)

    def test_recheck_only_target_files_when_specified(self) -> None:
        # 指定 target_files 时只重检这些文件（停止翻译合并 append 后的实际路径），
        # 不重复扫描已完成文件。
        with tempfile.TemporaryDirectory() as cache_dir:
            pass3_dir = os.path.join(cache_dir, "pass3_cache")
            os.makedirs(pass3_dir)
            file_a = os.path.join(pass3_dir, "a.json")
            file_b = os.path.join(pass3_dir, "b.json")
            for path in (file_a, file_b):
                with open(path, "wb") as f:
                    f.write(
                        orjson.dumps(
                            [_make_entry(0, "こんにちは", "你好")],
                            option=orjson.OPT_INDENT_2,
                        )
                    )

            fake_cfg = SimpleNamespace()
            results = [
                {
                    "index": 0,
                    "problem": "长句丢失换行",
                    "post_dst_preview": "你好",
                    "skip_check": False,
                }
            ]
            with patch(
                "GalTransl.server._run_problem_detection", return_value=(results, True)
            ):
                n = recheck_pass3_cache_files(
                    cache_dir, fake_cfg, None, None, None, [], target_files=[file_a]
                )

            self.assertEqual(n, 1)
            self.assertEqual(self._read_entries(file_a)[0]["problem"], "长句丢失换行")
            # b 不在 target_files 中，不应被重检写回
            self.assertNotIn("problem", self._read_entries(file_b)[0])


if __name__ == "__main__":
    unittest.main()
