"""批次级元数据提取与多轮对话翻译共享的区间/元数据解析逻辑测试。

覆盖两类共享缺陷的修复：
1. 区间解析（parse_interval / normalize_batch_intervals）对脏数据的统一、健壮处理；
2. 分批后缀剥离（strip_chunk_suffix）在两模块元数据解析中的一致性。
"""
import unittest

from GalTransl.Backend.ForGalJsonMulitChat import (
    parse_interval,
    strip_chunk_suffix,
    normalize_batch_intervals,
    BatchMetadata,
)
from GalTransl.Backend.ForBatchMetaData import ForBatchMetaData


class ParseIntervalTests(unittest.TestCase):
    """parse_interval：统一解析、自动交换、脏数据返回 None。"""

    def test_swap_when_reversed(self) -> None:
        self.assertEqual(parse_interval([10, 1]), (1, 10))

    def test_normal(self) -> None:
        self.assertEqual(parse_interval([3, 7]), (3, 7))

    def test_non_int_returns_none(self) -> None:
        self.assertIsNone(parse_interval(["1", "x"]))

    def test_too_short_returns_none(self) -> None:
        self.assertIsNone(parse_interval([1]))

    def test_non_list_returns_none(self) -> None:
        self.assertIsNone(parse_interval("1-10"))
        self.assertIsNone(parse_interval(None))

    def test_supports_interval_key_variant(self) -> None:
        # 代码里调用方用 b.get("区间") or b.get("interval")，这里直接验证端点
        self.assertEqual(parse_interval((2, 9)), (2, 9))


class StripChunkSuffixTests(unittest.TestCase):
    """strip_chunk_suffix：分批后缀剥离，与多轮模块元数据解析一致。"""

    def test_strips_numeric_suffix(self) -> None:
        self.assertEqual(strip_chunk_suffix("file.txt.json_0"), "file.txt.json")

    def test_strips_multi_digit(self) -> None:
        self.assertEqual(strip_chunk_suffix("abc_123"), "abc")

    def test_no_suffix_unchanged(self) -> None:
        self.assertEqual(strip_chunk_suffix("file.txt.json"), "file.txt.json")

    def test_trailing_underscore_no_digit_unchanged(self) -> None:
        self.assertEqual(strip_chunk_suffix("file_"), "file_")


class NormalizeBatchIntervalsTests(unittest.TestCase):
    """normalize_batch_intervals：重叠修复、裁剪、最大批次数、间隙检测、脏数据。"""

    def test_overlap_fully_inside_discarded(self) -> None:
        raw = [{"区间": [1, 10], "视角": "A"}, {"区间": [5, 8], "视角": "B"}]
        out = normalize_batch_intervals(raw, "f", max_index=10, max_batches=20)
        self.assertEqual([b["区间"] for b in out], [[1, 10]])

    def test_overlap_partial_shrinked(self) -> None:
        raw = [{"区间": [1, 10]}, {"区间": [8, 15]}]
        out = normalize_batch_intervals(raw, "f", max_index=15, max_batches=20)
        self.assertEqual([b["区间"] for b in out], [[1, 10], [11, 15]])

    def test_clip_to_max_index(self) -> None:
        raw = [{"区间": [1, 100]}]
        out = normalize_batch_intervals(raw, "f", max_index=10, max_batches=20)
        self.assertEqual(out[0]["区间"], [1, 10])

    def test_malformed_interval_dropped(self) -> None:
        raw = [{"区间": ["1", "x"]}, {"区间": [3, 5]}]
        out = normalize_batch_intervals(raw, "f", max_index=10, max_batches=20)
        self.assertEqual([b["区间"] for b in out], [[3, 5]])

    def test_bools_normalized(self) -> None:
        raw = [{"区间": [1, 5], "h": "是", "视角": "A"}]
        out = normalize_batch_intervals(raw, "f", max_index=5, max_batches=20)
        self.assertTrue(out[0]["h"])
        self.assertEqual(out[0]["视角"], "A")

    def test_max_batches_merge(self) -> None:
        raw = [
            {"区间": [1, 3], "视角": "A"},
            {"区间": [4, 6], "视角": "B"},
            {"区间": [7, 9], "视角": "C"},
        ]
        out = normalize_batch_intervals(raw, "f", max_index=9, max_batches=2)
        self.assertEqual(len(out), 2)
        # 间距最小的两个相邻区间被合并（取前者的视角），首尾区间保持
        self.assertEqual(out[0]["区间"], [1, 6])
        self.assertEqual(out[0]["视角"], "A")
        self.assertEqual(out[1]["区间"], [7, 9])

    def test_gap_no_exception(self) -> None:
        raw = [{"区间": [1, 5]}, {"区间": [10, 15]}]
        out = normalize_batch_intervals(raw, "f", max_index=15, max_batches=20)
        self.assertEqual(len(out), 2)

    def test_max_batch_size_marks_oversize(self) -> None:
        # 超过 max_batch_size 的区间不再切分，仅标注「区间过大」
        raw = [{"区间": [1, 100], "视角": "A"}]
        out = normalize_batch_intervals(
            raw, "f", max_index=100, max_batches=20, max_batch_size=40
        )
        self.assertEqual([b["区间"] for b in out], [[1, 100]])
        self.assertTrue(out[0].get("区间过大"))
        self.assertEqual(out[0]["视角"], "A")

    def test_max_batch_size_keeps_whole_interval_marked(self) -> None:
        # 模拟 00_03 的 69 行整文件一批：保留原区间并标注「区间过大」
        raw = [{"区间": [1, 69]}]
        out = normalize_batch_intervals(
            raw, "f", max_index=69, max_batches=20,
            min_batch_size=8, max_batch_size=64,
        )
        self.assertEqual([b["区间"] for b in out], [[1, 69]])
        self.assertTrue(out[0].get("区间过大"))

    def test_min_batch_size_merges_short_interval(self) -> None:
        raw = [{"区间": [1, 3], "视角": "A"}, {"区间": [4, 8], "视角": "B"}]
        out = normalize_batch_intervals(
            raw, "f", max_index=8, max_batches=20,
            min_batch_size=5, max_batch_size=100,
        )
        self.assertEqual([b["区间"] for b in out], [[1, 8]])
        # 元信息取先到者（左侧区间的视角）
        self.assertEqual(out[0]["视角"], "A")

    def test_min_batch_size_skips_merge_when_over_max(self) -> None:
        raw = [{"区间": [1, 2], "视角": "A"}, {"区间": [3, 12], "视角": "B"}]
        out = normalize_batch_intervals(
            raw, "f", max_index=12, max_batches=20,
            min_batch_size=5, max_batch_size=10,
        )
        # 合并将超过 max_batch_size(10)，应保留原区间
        self.assertEqual([b["区间"] for b in out], [[1, 2], [3, 12]])

    def test_no_length_constraint_keeps_behavior(self) -> None:
        raw = [{"区间": [1, 100]}]
        out = normalize_batch_intervals(raw, "f", max_index=100, max_batches=20)
        self.assertEqual([b["区间"] for b in out], [[1, 100]])


class ForBatchMetaDataNormalizeDelegateTests(unittest.TestCase):
    """ForBatchMetaData._normalize_meta 仅作委托，强制 id == 文件名。"""

    def test_delegates_to_shared(self) -> None:
        obj = {"批次": [{"区间": [5, 8], "视角": "A"}, {"区间": [1, 3]}]}
        meta = ForBatchMetaData._normalize_meta(obj, "file.txt.json", max_index=10, max_batches=20)
        self.assertEqual(meta["id"], "file.txt.json")
        # 排序后应为 [1,3], [5,8]
        self.assertEqual([b["区间"] for b in meta["批次"]], [[1, 3], [5, 8]])

    def test_empty_batches(self) -> None:
        meta = ForBatchMetaData._normalize_meta(
            {"batches": []}, "f", max_index=10, max_batches=20
        )
        self.assertEqual(meta["批次"], [])

    def test_delegates_length_constraints(self) -> None:
        obj = {"批次": [{"区间": [1, 100]}]}
        meta = ForBatchMetaData._normalize_meta(
            obj, "f", max_index=100, max_batches=20,
            min_batch_size=8, max_batch_size=40,
        )
        self.assertEqual([b["区间"] for b in meta["批次"]], [[1, 100]])
        self.assertTrue(meta["批次"][0].get("区间过大"))

    def test_max_natural_lines_calculation(self) -> None:
        from unittest.mock import MagicMock

        cfg = MagicMock()
        cfg.getKey.side_effect = lambda key, default=None: {
            "internals.forbatchmeta.max_batches": 20,
            "internals.forbatchmeta.min_batch_size": 8,
            "internals.forbatchmeta.max_batch_size": 64,
        }.get(key, default)
        token_pool = MagicMock()
        token_pool.get_available_token.return_value = []
        bm = ForBatchMetaData(cfg, "ForBatchMetaData", None, token_pool)
        self.assertEqual(bm.max_batch_size, 64)
        self.assertEqual(bm.max_batches, 20)
        self.assertEqual(bm.max_natural_lines, int(0.9 * 64 * 20))


class SegmentsInRangeUsesSharedParseTests(unittest.TestCase):
    """多轮模块的 segments_in_range 现通过共享 parse_interval 工作。"""

    def test_intersecting_segments(self) -> None:
        bm = BatchMetadata(
            id="f",
            batches=[
                {"区间": [1, 5], "视角": "A"},
                {"区间": [6, 10], "视角": "B"},
                {"区间": "bad", "视角": "C"},  # 畸形条目，应被共享解析逻辑跳过
                {"区间": [20, 25]},
            ],
        )
        hit = bm.segments_in_range(3, 8)
        # 仅 [1,5] 与 [6,10] 与 (3,8) 相交；畸形条目被跳过
        self.assertEqual([b["区间"] for b in hit], [[1, 5], [6, 10]])

    def test_no_hit(self) -> None:
        bm = BatchMetadata(id="f", batches=[{"区间": [1, 5]}])
        self.assertEqual(bm.segments_in_range(100, 200), [])


if __name__ == "__main__":
    unittest.main()
