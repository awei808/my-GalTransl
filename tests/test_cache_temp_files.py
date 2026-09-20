"""缓存快照原子替换与残留 .json.tmp 清扫。

整文件重写（post_save 快照、append 合并）先写 <缓存>.json.tmp 再替换：必须走 os.replace
原子替换（Windows 上 shutil.move 覆盖已存在文件会退化成原地截断重写，写一半崩了就是
半截 JSON）；任务启动时清扫残留 tmp；缓存列表不再把 tmp 当缓存文件展示。
"""

import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

from GalTransl.Cache import (
    CACHE_TEMP_SUFFIX,
    _compact_cache_from_append,
    cleanup_stale_cache_temp_files,
    save_transCache_to_json,
)
from GalTransl.CSentense import CSentense
from GalTransl.server import _list_dir_entries


def _tran(src: str, dst: str) -> CSentense:
    tran = CSentense(src, speaker="", index=1)
    tran.post_src = src
    tran.pre_dst = dst
    tran.post_dst = dst
    return tran


class SnapshotReplaceTests(unittest.TestCase):
    def test_post_save_snapshot_uses_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "sc_0.txt.json")
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump([{"index": 0, "pre_dst": "旧"}], f, ensure_ascii=False)
            with mock.patch("shutil.move", side_effect=AssertionError("不得使用 shutil.move")), mock.patch(
                "os.replace", wraps=os.replace
            ) as spy:
                asyncio.run(save_transCache_to_json([_tran("こんにちは", "你好")], cache_file, post_save=True))
            self.assertEqual(spy.call_count, 1)
            self.assertFalse(os.path.exists(cache_file + CACHE_TEMP_SUFFIX))
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data[0]["pre_dst"], "你好")

    def test_compact_append_uses_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "sc_1.txt.json")
            append_file = cache_file + ".append.jsonl"
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump([{"index": 1, "name": "", "pre_src": "あ", "post_src": "あ", "pre_dst": "旧"}], f, ensure_ascii=False)
            entry = {"index": 1, "name": "", "pre_src": "あ", "post_src": "あ", "pre_dst": "新", "__cache_key": "NoneあNone"}
            with open(append_file, "w", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            with mock.patch("shutil.move", side_effect=AssertionError("不得使用 shutil.move")), mock.patch(
                "os.replace", wraps=os.replace
            ) as spy:
                asyncio.run(_compact_cache_from_append(cache_file, append_file))
            self.assertEqual(spy.call_count, 1)
            self.assertFalse(os.path.exists(append_file))
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data[0]["pre_dst"], "新")


class CleanupTests(unittest.TestCase):
    def test_cleanup_removes_only_json_tmp_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            keep = os.path.join(tmp, "sc_0.txt.json")
            append = os.path.join(tmp, "sc_0.txt.json.append.jsonl")
            tmpfile = os.path.join(tmp, "sc_1.txt.json" + CACHE_TEMP_SUFFIX)
            for path in (keep, append, tmpfile):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("x")
            self.assertEqual(cleanup_stale_cache_temp_files(tmp), 1)
            self.assertTrue(os.path.exists(keep))
            self.assertTrue(os.path.exists(append))
            self.assertFalse(os.path.exists(tmpfile))

    def test_cleanup_missing_dir_returns_zero(self) -> None:
        self.assertEqual(cleanup_stale_cache_temp_files(os.path.join("不存在目录", "x")), 0)


class ListingFilterTests(unittest.TestCase):
    def test_cache_listing_skips_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.json"), "w", encoding="utf-8") as f:
                f.write("[]")
            with open(os.path.join(tmp, "b" + CACHE_TEMP_SUFFIX), "w", encoding="utf-8") as f:
                f.write("[]")
            names = [e["name"] for e in _list_dir_entries(tmp, count_json_entries=True, skip_suffixes=(CACHE_TEMP_SUFFIX,))]
            self.assertEqual(names, ["a.json"])
            names_all = [e["name"] for e in _list_dir_entries(tmp, count_json_entries=True)]
            self.assertEqual(names_all, ["a.json", "b.json.tmp"])


if __name__ == "__main__":
    unittest.main()
