"""H 剧情区间解析（_resolve_cache_h_ranges）测试。

数据源：transl_cache/pass2_cache/{输入名}.batch.json 的「批次」数组中 h=true 的区间。
覆盖：无批次文件 / 无 h 区间 / 单段 / 相邻合并 / 分离多段 / 分片后缀解析 /
分片偏移换算 / 原文件带 index / 路径穿越。
"""

import json
import os
import shutil
import tempfile
import unittest

from GalTransl.server import _resolve_cache_h_ranges


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class CacheHrangesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="galtransl_hranges_")
        self.input_dir = os.path.join(self.tmpdir, "gt_input")
        self.batch_dir = os.path.join(self.tmpdir, "transl_cache", "pass2_cache")
        self.pass3_dir = os.path.join(self.tmpdir, "transl_cache", "pass3_cache")
        os.makedirs(self.input_dir, exist_ok=True)
        os.makedirs(self.batch_dir, exist_ok=True)
        os.makedirs(self.pass3_dir, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _batch(self, filename: str, ranges: list) -> None:
        """写批次文件：ranges 为 [{"区间":[lo,hi], "h": bool}, ...]。"""
        _write_json(
            os.path.join(self.batch_dir, f"{filename}.batch.json"),
            {"id": filename, "批次": ranges},
        )

    def _cache(self, filename: str) -> None:
        """写一个占位缓存文件（函数只检查其存在性，不读内容）。"""
        _write_json(os.path.join(self.pass3_dir, filename), [])

    def _input(self, filename: str, items: list) -> None:
        _write_json(os.path.join(self.input_dir, filename), items)

    def test_no_batch_file(self) -> None:
        self._cache("story.txt.json")
        result = _resolve_cache_h_ranges(self.tmpdir, "pass3_cache/story.txt.json")
        self.assertFalse(result["batch_exists"])
        self.assertFalse(result["has_h"])
        self.assertEqual(result["h_ranges"], [])

    def test_cache_file_missing(self) -> None:
        result = _resolve_cache_h_ranges(self.tmpdir, "pass3_cache/nope.txt.json")
        self.assertFalse(result["batch_exists"])
        self.assertFalse(result["has_h"])

    def test_batch_exists_but_no_h(self) -> None:
        self._cache("story.txt.json")
        self._batch(
            "story.txt.json",
            [{"区间": [1, 10], "h": False}, {"区间": [11, 20], "h": False}],
        )
        result = _resolve_cache_h_ranges(self.tmpdir, "pass3_cache/story.txt.json")
        self.assertTrue(result["batch_exists"])
        self.assertFalse(result["has_h"])
        self.assertEqual(result["h_ranges"], [])

    def test_h_below_threshold_not_h_range(self) -> None:
        # 兼容口径：h < 0.5（如 0.4）不算 H 区间
        self._cache("story.txt.json")
        self._batch(
            "story.txt.json",
            [{"区间": [1, 10], "h": 0.4}, {"区间": [11, 20], "h": 0.2}],
        )
        result = _resolve_cache_h_ranges(self.tmpdir, "pass3_cache/story.txt.json")
        self.assertTrue(result["batch_exists"])
        self.assertFalse(result["has_h"])
        self.assertEqual(result["h_ranges"], [])

    def test_h_at_or_above_threshold_counts(self) -> None:
        # h = 0.5（>=0.5）视为 H 区间；0.6 与 0.8 相邻合并取峰值 0.8
        self._cache("story.txt.json")
        self._batch(
            "story.txt.json",
            [
                {"区间": [1, 5], "h": 0.5},
                {"区间": [6, 10], "h": 0.6},
                {"区间": [11, 15], "h": 0.8},
            ],
        )
        result = _resolve_cache_h_ranges(self.tmpdir, "pass3_cache/story.txt.json")
        self.assertTrue(result["has_h"])
        self.assertEqual(result["h_ranges"], [{"lo": 1, "hi": 15, "h": 0.8}])

    def test_single_h_range(self) -> None:
        self._cache("story.txt.json")
        self._batch(
            "story.txt.json",
            [{"区间": [1, 16], "h": False}, {"区间": [17, 40], "h": True}, {"区间": [41, 52], "h": False}],
        )
        result = _resolve_cache_h_ranges(self.tmpdir, "pass3_cache/story.txt.json")
        self.assertTrue(result["batch_exists"])
        self.assertTrue(result["has_h"])
        self.assertEqual(result["h_ranges"], [{"lo": 17, "hi": 40, "h": 1.0}])

    def test_adjacent_h_ranges_merged(self) -> None:
        self._cache("story.txt.json")
        self._batch(
            "story.txt.json",
            [
                {"区间": [1, 10], "h": False},
                {"区间": [11, 25], "h": True},
                {"区间": [26, 40], "h": True},
                {"区间": [41, 60], "h": False},
            ],
        )
        result = _resolve_cache_h_ranges(self.tmpdir, "pass3_cache/story.txt.json")
        self.assertEqual(result["h_ranges"], [{"lo": 11, "hi": 40, "h": 1.0}])

    def test_separated_h_ranges_stay_multiple(self) -> None:
        self._cache("story.txt.json")
        self._batch(
            "story.txt.json",
            [
                {"区间": [1, 10], "h": False},
                {"区间": [11, 20], "h": True},
                {"区间": [21, 30], "h": False},
                {"区间": [31, 50], "h": True},
            ],
        )
        result = _resolve_cache_h_ranges(self.tmpdir, "pass3_cache/story.txt.json")
        self.assertEqual(
            result["h_ranges"],
            [{"lo": 11, "hi": 20, "h": 1.0}, {"lo": 31, "hi": 50, "h": 1.0}],
        )

    def test_split_suffix_resolves_batch(self) -> None:
        """缓存 xxx_0.json 应解析到 xxx.json.batch.json（剥离 _N 分块后缀）。"""
        self._cache("story.txt.json_0.json")
        self._batch(
            "story.txt.json",
            [{"区间": [1, 10], "h": False}, {"区间": [11, 20], "h": True}],
        )
        result = _resolve_cache_h_ranges(self.tmpdir, "pass3_cache/story.txt.json_0.json")
        self.assertTrue(result["batch_exists"])
        self.assertTrue(result["has_h"])
        self.assertEqual(result["h_ranges"], [{"lo": 11, "hi": 20, "h": 1.0}])

    def test_split_offset_applied(self) -> None:
        """无 index + splitFile=Num：chunk 1 的条目 index 需加 10（1*10-0）偏移。"""
        self._input("story.txt.json", [{"message": f"m{i}"} for i in range(20)])
        self._batch(
            "story.txt.json",
            [{"区间": [1, 10], "h": False}, {"区间": [11, 15], "h": True}, {"区间": [16, 20], "h": False}],
        )
        self._cache("story.txt.json_1.json")
        _write_json(
            os.path.join(self.tmpdir, "config.yaml"),
            {"common": {"splitFile": "Num", "splitFileNum": 10, "splitFileCrossNum": 0}},
        )
        result = _resolve_cache_h_ranges(self.tmpdir, "pass3_cache/story.txt.json_1.json")
        self.assertEqual(result["h_ranges"], [{"lo": 1, "hi": 5, "h": 1.0}])

    def test_original_with_index_no_offset(self) -> None:
        """原文件带 index 时，分片缓存条目 index 即全局行号，偏移为 0。"""
        self._input(
            "story.txt.json",
            [{"index": i + 1, "message": f"m{i}"} for i in range(20)],
        )
        self._batch(
            "story.txt.json",
            [{"区间": [1, 10], "h": False}, {"区间": [11, 15], "h": True}],
        )
        self._cache("story.txt.json_1.json")
        result = _resolve_cache_h_ranges(self.tmpdir, "pass3_cache/story.txt.json_1.json")
        self.assertEqual(result["h_ranges"], [{"lo": 11, "hi": 15, "h": 1.0}])

    def test_cross_split_partial_h_lo_clamped(self) -> None:
        """H 区间跨分片边界：offset=10 时 H 段 [8,12] 在当前分片只剩 [1,2]，lo 须 clamp 到 1。"""
        self._input("story.txt.json", [{"message": f"m{i}"} for i in range(20)])
        self._batch(
            "story.txt.json",
            [{"区间": [1, 7], "h": False}, {"区间": [8, 12], "h": True}, {"区间": [13, 20], "h": False}],
        )
        self._cache("story.txt.json_1.json")
        _write_json(
            os.path.join(self.tmpdir, "config.yaml"),
            {"common": {"splitFile": "Num", "splitFileNum": 10, "splitFileCrossNum": 0}},
        )
        result = _resolve_cache_h_ranges(self.tmpdir, "pass3_cache/story.txt.json_1.json")
        self.assertEqual(result["h_ranges"], [{"lo": 1, "hi": 2, "h": 1.0}])

    def test_string_numeric_index_no_offset(self) -> None:
        """Bug1 回归：原文件 index 为数字字符串时，offset 应恒为 0（与 CSplitter 口径一致）。"""
        self._input(
            "story.txt.json",
            [{"index": str(i + 1), "message": f"m{i}"} for i in range(20)],
        )
        self._batch(
            "story.txt.json",
            [{"区间": [1, 10], "h": False}, {"区间": [11, 15], "h": True}, {"区间": [16, 20], "h": False}],
        )
        self._cache("story.txt.json_1.json")
        _write_json(
            os.path.join(self.tmpdir, "config.yaml"),
            {"common": {"splitFile": "Num", "splitFileNum": 10, "splitFileCrossNum": 0}},
        )
        result = _resolve_cache_h_ranges(self.tmpdir, "pass3_cache/story.txt.json_1.json")
        self.assertEqual(result["h_ranges"], [{"lo": 11, "hi": 15, "h": 1.0}])

    def test_corrupted_batch_marks_batch_exists(self) -> None:
        """Bug3 回归：batch 文件存在但 JSON 损坏时 batch_exists 应为 true（且 has_h=false）。"""
        self._cache("story.txt.json")
        corrupt_path = os.path.join(self.batch_dir, "story.txt.json.batch.json")
        with open(corrupt_path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        result = _resolve_cache_h_ranges(self.tmpdir, "pass3_cache/story.txt.json")
        self.assertTrue(result["batch_exists"])
        self.assertFalse(result["has_h"])
        self.assertEqual(result["h_ranges"], [])

    def test_path_traversal_rejected(self) -> None:
        result = _resolve_cache_h_ranges(self.tmpdir, "../secret.json")
        self.assertFalse(result["batch_exists"])
        self.assertFalse(result["has_h"])


if __name__ == "__main__":
    unittest.main()
