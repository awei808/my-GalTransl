# -*- coding: utf-8 -*-
"""检测类后端「按批次级元数据区间划分批次」的单元测试。

覆盖 BaseDetectRound._split_into_batches 的生效与全部退化路径：
  - 开关关闭 / 复核轮 → 固定句数切片（与改动前一致）
  - 有元数据 → 每区间一批，句子不跨区间、空区间跳过
  - 无元数据 / 句子无行号 / 行号与区间不匹配 → 回退固定句数
  - 单批句数上限 > 0 时对超长区间与区间外尾句子切
  - 文件名口径兜底：分批后缀（f_2）、子目录扁平化名（sub-}a.json）
"""

import unittest
from unittest.mock import Mock

from GalTransl.CSentense import CSentense
from GalTransl.Backend.BaseDetectRound import BaseDetectRound
from GalTransl.Backend.ForSemCheck import ForSemCheck
from GalTransl.Backend.ForSemCheckAgain import ForSemCheckAgain
from GalTransl.Backend.metadata import BatchMetadata


class _FakeConfig:
    active_workers = 1

    def getKey(self, key: str):
        return None


def _engine(
    cls=ForSemCheck,
    enabled: bool = True,
    cap: int = 0,
    metadata: dict | None = None,
):
    """构造只实现分批所需最小状态的替身（不跑 __init__，避免真实令牌池）。"""
    obj = object.__new__(cls)
    obj.pj_config = _FakeConfig()
    obj.batch_metadata_map = {}
    obj._batch_metadata_by_file = dict(metadata or {})
    obj._batch_metadata_loaded = True
    obj.batch_from_metadata = enabled
    obj.batch_metadata_max_sentences = cap
    return obj


def _trans(index: int) -> CSentense:
    tran = CSentense(f"src{index}", index=index)
    tran.post_src = f"原文{index}"
    tran.pre_dst = f"译文{index}"
    tran.runtime_index = index
    return tran


class _NoIdxTrans:
    """无 runtime_index / index：模拟替换实现中缺少行号的句子。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.runtime_index = None
        self.index = None


def _bm(*ranges) -> BatchMetadata:
    return BatchMetadata(
        id="f",
        batches=[{"区间": list(rng), "视角": "创", "h": 0.0, "用词色彩": "x"} for rng in ranges],
    )


def _indices(batches) -> list:
    return [[t.index for t in batch] for batch in batches]


class FixedSliceFallbackTests(unittest.TestCase):
    def test_disabled_keeps_fixed_slice(self) -> None:
        engine = _engine(enabled=False, metadata={"f.json": _bm((1, 4), (5, 8))})
        targets = [_trans(i) for i in range(1, 9)]
        batches, grouped = engine._split_into_batches("f.json", targets, 3)
        self.assertFalse(grouped)
        self.assertEqual(_indices(batches), [[1, 2, 3], [4, 5, 6], [7, 8]])

    def test_review_round_keeps_fixed_slice(self) -> None:
        engine = _engine(cls=ForSemCheckAgain, metadata={"f.json": _bm((1, 4), (5, 8))})
        targets = [_trans(i) for i in range(1, 9)]
        batches, grouped = engine._split_into_batches("f.json", targets, 4)
        self.assertFalse(grouped)
        self.assertEqual(_indices(batches), [[1, 2, 3, 4], [5, 6, 7, 8]])

    def test_no_metadata_falls_back(self) -> None:
        engine = _engine()
        batches, grouped = engine._split_into_batches("f.json", [_trans(i) for i in range(1, 6)], 2)
        self.assertFalse(grouped)
        self.assertEqual(_indices(batches), [[1, 2], [3, 4], [5]])

    def test_empty_batches_falls_back(self) -> None:
        engine = _engine(metadata={"f.json": BatchMetadata(id="f", batches=[])})
        batches, grouped = engine._split_into_batches("f.json", [_trans(i) for i in range(1, 4)], 2)
        self.assertFalse(grouped)
        self.assertEqual(_indices(batches), [[1, 2], [3]])

    def test_no_index_falls_back(self) -> None:
        engine = _engine(metadata={"f.json": _bm((1, 4))})
        targets = [_NoIdxTrans("a"), _NoIdxTrans("b")]
        batches, grouped = engine._split_into_batches("f.json", targets, 2)
        self.assertFalse(grouped)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 2)

    def test_out_of_range_falls_back(self) -> None:
        # 行号与所有区间都不相交：照搬分组会退化成整文件一批，必须兜回固定切片
        engine = _engine(metadata={"f.json": _bm((100, 110))})
        targets = [_trans(i) for i in range(1, 6)]
        batches, grouped = engine._split_into_batches("f.json", targets, 2)
        self.assertFalse(grouped)
        self.assertEqual(_indices(batches), [[1, 2], [3, 4], [5]])


class MetadataGroupingTests(unittest.TestCase):
    def test_each_segment_is_one_batch(self) -> None:
        engine = _engine(metadata={"f.json": _bm((1, 4), (5, 7), (8, 10))})
        targets = [_trans(i) for i in range(1, 11)]
        batches, grouped = engine._split_into_batches("f.json", targets, 16)
        self.assertTrue(grouped)
        self.assertEqual(_indices(batches), [[1, 2, 3, 4], [5, 6, 7], [8, 9, 10]])

    def test_empty_segment_skipped(self) -> None:
        engine = _engine(metadata={"f.json": _bm((1, 4), (5, 7))})
        targets = [_trans(i) for i in [1, 2, 3, 4, 8, 9]]
        batches, grouped = engine._split_into_batches("f.json", targets, 16)
        self.assertTrue(grouped)
        self.assertEqual(_indices(batches), [[1, 2, 3, 4], [8, 9]])

    def test_tail_group_uses_fixed_slice(self) -> None:
        # 区间 [1,4] 之外无语义边界：按固定句数切；设了单批上限 4 时取两者较小值
        engine = _engine(cap=4, metadata={"f.json": _bm((1, 4))})
        targets = [_trans(i) for i in range(1, 11)]
        batches, grouped = engine._split_into_batches("f.json", targets, 16)
        self.assertTrue(grouped)
        self.assertEqual(_indices(batches), [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10]])

    def test_tail_group_without_cap_uses_num_per_request(self) -> None:
        # 未设上限（0）：尾组按调用方每批句数切，不退化成整文件一批
        engine = _engine(cap=0, metadata={"f.json": _bm((1, 4))})
        targets = [_trans(i) for i in range(1, 11)]
        batches, grouped = engine._split_into_batches("f.json", targets, 3)
        self.assertTrue(grouped)
        self.assertEqual(_indices(batches), [[1, 2, 3, 4], [5, 6, 7], [8, 9, 10]])

    def test_scattered_tail_sentences_join_one_group(self) -> None:
        # 句 2 与 4 落在区间外（分散）：合并为尾组后按固定句数 1 切成独立批
        engine = _engine(metadata={"f.json": _bm((1, 1), (3, 3))})
        targets = [_trans(i) for i in range(1, 5)]
        batches, grouped = engine._split_into_batches("f.json", targets, 1)
        self.assertTrue(grouped)
        self.assertEqual(_indices(batches), [[1], [3], [2], [4]])

    def test_no_cap_keeps_large_segment_whole(self) -> None:
        engine = _engine(cap=0, metadata={"f.json": _bm((1, 30))})
        targets = [_trans(i) for i in range(1, 31)]
        batches, grouped = engine._split_into_batches("f.json", targets, 16)
        self.assertTrue(grouped)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 30)

    def test_segment_split_by_cap(self) -> None:
        engine = _engine(cap=4, metadata={"f.json": _bm((1, 10))})
        targets = [_trans(i) for i in range(1, 11)]
        batches, grouped = engine._split_into_batches("f.json", targets, 16)
        self.assertTrue(grouped)
        self.assertEqual(_indices(batches), [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10]])


class FilenameLookupTests(unittest.TestCase):
    def test_chunk_suffix_uses_whole_file_metadata(self) -> None:
        # f_2 分片：区间按整文件全局行号，剥后缀后命中
        engine = _engine(metadata={"f": _bm((1, 8), (9, 38))})
        targets = [_trans(i) for i in range(17, 33)]
        batches, grouped = engine._split_into_batches("f_2", targets, 16)
        self.assertTrue(grouped)
        self.assertEqual(len(batches), 1)
        self.assertEqual(_indices(batches), [list(range(17, 33))])

    def test_flattened_subdir_name_falls_back_to_basename(self) -> None:
        # 阶段 8 传入 sub-}a.json，pass2 产物按 basename 存
        engine = _engine(metadata={"a.json": _bm((1, 3), (4, 6))})
        targets = [_trans(i) for i in range(1, 7)]
        batches, grouped = engine._split_into_batches("sub-}a.json", targets, 16)
        self.assertTrue(grouped)
        self.assertEqual(_indices(batches), [[1, 2, 3], [4, 5, 6]])
        # 兜底命中登记为显式注入，供子类钩子（色彩基准）按原文件名解析
        self.assertIn("sub-}a.json", engine.batch_metadata_map)

    def test_plain_basename_falls_back(self) -> None:
        engine = _engine(metadata={"a.json": _bm((1, 2))})
        targets = [_trans(i) for i in range(1, 3)]
        batches, grouped = engine._split_into_batches("sub/a.json", targets, 16)
        self.assertTrue(grouped)
        self.assertEqual(_indices(batches), [[1, 2]])

    def test_chinese_subdir_flat_name_falls_back(self) -> None:
        # 中文子目录 + 中文文件名：-} 还原必须在各平台都给出一致的 basename
        engine = _engine(metadata={"第一章.json": _bm((1, 2), (3, 4))})
        targets = [_trans(i) for i in range(1, 5)]
        batches, grouped = engine._split_into_batches("剧情-}第一章.json", targets, 16)
        self.assertTrue(grouped)
        self.assertEqual(_indices(batches), [[1, 2], [3, 4]])

    def test_overlapping_segments_first_match_wins(self) -> None:
        # 重叠区间沿用翻译端口径：先遍历（按起始行升序）的区间优先
        engine = _engine(metadata={"f.json": _bm((1, 4), (4, 6))})
        targets = [_trans(i) for i in range(1, 7)]
        batches, grouped = engine._split_into_batches("f.json", targets, 16)
        self.assertTrue(grouped)
        self.assertEqual(_indices(batches), [[1, 2, 3, 4], [5, 6]])


class PartialInstanceTests(unittest.TestCase):
    def test_missing_metadata_tables_falls_back(self) -> None:
        # 半构造实例（无 batch_metadata_map）：不能崩，直接走固定句数
        obj = object.__new__(ForSemCheck)
        obj.batch_from_metadata = True
        obj.batch_metadata_max_sentences = 0
        targets = [_trans(i) for i in range(1, 4)]
        batches, grouped = obj._split_into_batches("f.json", targets, 2)
        self.assertFalse(grouped)
        self.assertEqual(_indices(batches), [[1, 2], [3]])

    def test_grouping_attribute_error_falls_back(self) -> None:
        # 分组内部解析不可用（替身没有 batch_metadata_map）：退化固定句数而不中断检测
        obj = object.__new__(ForSemCheck)
        obj.batch_from_metadata = True
        obj.batch_metadata_max_sentences = 0
        obj.batch_metadata_map = {}
        obj._batch_metadata_by_file = {"f.json": _bm((1, 2))}
        obj._batch_metadata_loaded = True
        obj._resolve_batch_metadata = Mock(side_effect=AttributeError("no map"))
        targets = [_trans(i) for i in range(1, 4)]
        batches, grouped = obj._split_into_batches("f.json", targets, 2)
        self.assertFalse(grouped)
        self.assertEqual(_indices(batches), [[1, 2], [3]])


class DefaultAttributeTests(unittest.TestCase):
    def test_missing_switch_attribute_defaults_to_enabled(self) -> None:
        # 属性缺失时按默认开启处理（与 internals.detect.batch_from_metadata 模板默认一致）
        obj = object.__new__(ForSemCheck)
        obj.batch_metadata_map = {}
        obj._batch_metadata_by_file = {"f.json": _bm((1, 2))}
        obj._batch_metadata_loaded = True
        targets = [_trans(i) for i in range(1, 3)]
        batches, grouped = obj._split_into_batches("f.json", targets, 16)
        self.assertTrue(grouped)
        self.assertEqual(_indices(batches), [[1, 2]])

    def test_replace_override_keeps_base_hook(self) -> None:
        # 分批逻辑挂在基类上，六个检测引擎共享同一实现
        for cls in (ForSemCheck, ForSemCheckAgain):
            self.assertIs(getattr(cls, "_split_into_batches"), BaseDetectRound._split_into_batches)


if __name__ == "__main__":
    unittest.main(verbosity=2)
