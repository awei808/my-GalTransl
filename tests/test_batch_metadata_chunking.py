"""单元测试：批次级元数据「按段划分 + 分批次注入」。

验证点（对应 ForGalJsonMulitChat 的改动）：
1. _group_by_batch_metadata：按语义段边界分组，段不跨组；空段跳过；段外句入尾组；
   无元数据时退化为单组（保持原固定切片行为）。
2. _format_batch_metadata_block：仅注入与给定行号区间相交的段，且文案已改为
   「每批次仅提供本批涉及的区间指导」（不再声称后续轮次不重复）。
3. _build_round_user_content：续轮（is_first_round=False）也会把 batch_metadata_block
   前置注入（CP3 去门控）。
4. 动态模式隔离：动态句数调整（dynamic_num_per_request）只在「无批次级元数据」的
   退化分支启用；有元数据时强制 force_static 禁用动态，大段不沿 numPerRequestTranslate
   子切，且不应污染全局动态状态。

不依赖网络/API；通过 unbound 调用真实方法 + MagicMock 提供 self 完成测试。
"""

import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock

REPO_ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, REPO_ROOT)

from GalTransl.Backend.BaseTranslate import BaseTranslate  # noqa: E402
from GalTransl.Backend.ForGalJsonMulitChat import (  # noqa: E402
    BatchMetadata,
    ForGalJsonMulitChat,
    H_WORDS_LIST,
)


class _FakeTrans:
    """极简句子替身，仅带 runtime_index（分组逻辑只依赖它）。"""

    def __init__(self, runtime_index: int):
        self.runtime_index = runtime_index

    def __repr__(self):
        return f"T({self.runtime_index})"


class _NoIdx:
    """无 runtime_index / index 属性，应落入尾组（ungrouped）。"""
    pass


def _make_inst(bm: BatchMetadata):
    """构造一个只实现了 _resolve_batch_metadata 的替身实例。"""
    inst = MagicMock(spec=ForGalJsonMulitChat)
    inst.last_file_name = None  # batch_translate 访问的动态属性，spec 不含
    inst._resolve_batch_metadata = lambda filename: bm
    return inst


class TestGroupByBatchMetadata(unittest.TestCase):
    def test_grouped_by_segment_boundaries(self) -> None:
        bm = BatchMetadata(
            id="f",
            batches=[
                {"区间": [1, 4], "视角": "创", "氛围": "x", "h": False, "用词色彩": "y"},
                {"区间": [5, 7], "视角": "创", "氛围": "x", "h": False, "用词色彩": "y"},
                {"区间": [8, 10], "视角": "创", "氛围": "x", "h": False, "用词色彩": "y"},
            ],
        )
        inst = _make_inst(bm)
        trans = [_FakeTrans(i) for i in range(1, 11)]
        groups = ForGalJsonMulitChat._group_by_batch_metadata(inst, trans, "f")
        self.assertEqual(len(groups), 3)
        self.assertEqual([t.runtime_index for t in groups[0]], [1, 2, 3, 4])
        self.assertEqual([t.runtime_index for t in groups[1]], [5, 6, 7])
        self.assertEqual([t.runtime_index for t in groups[2]], [8, 9, 10])

    def test_no_metadata_falls_back_to_single_group(self) -> None:
        inst = _make_inst(None)  # 无元数据
        trans = [_FakeTrans(i) for i in range(1, 21)]
        groups = ForGalJsonMulitChat._group_by_batch_metadata(inst, trans, "f")
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 20)

    def test_empty_segment_skipped_and_ungrouped_tail(self) -> None:
        # 段 [1,4] 有句；段 [5,7] 无句（应跳过）；句 8,9 不属于任何段（入尾组）
        bm = BatchMetadata(
            id="f",
            batches=[
                {"区间": [1, 4], "视角": "创", "氛围": "x", "h": False, "用词色彩": "y"},
                {"区间": [5, 7], "视角": "创", "氛围": "x", "h": False, "用词色彩": "y"},
            ],
        )
        inst = _make_inst(bm)
        trans = [_FakeTrans(i) for i in [1, 2, 3, 4, 8, 9]]
        groups = ForGalJsonMulitChat._group_by_batch_metadata(inst, trans, "f")
        # 段[5,7] 为空被跳过；尾组 [8,9]
        self.assertEqual(len(groups), 2)
        self.assertEqual([t.runtime_index for t in groups[0]], [1, 2, 3, 4])
        self.assertEqual([t.runtime_index for t in groups[1]], [8, 9])

    def test_large_segment_is_single_group(self) -> None:
        # 大段 [1,30]：分组阶段是一个组（子切由 _batch_translate_common 负责）
        bm = BatchMetadata(
            id="f",
            batches=[
                {"区间": [1, 30], "视角": "创", "氛围": "x", "h": False, "用词色彩": "y"},
            ],
        )
        inst = _make_inst(bm)
        trans = [_FakeTrans(i) for i in range(1, 31)]
        groups = ForGalJsonMulitChat._group_by_batch_metadata(inst, trans, "f")
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 30)


class TestEdgeCases(unittest.TestCase):
    def test_overlapping_segments_first_match_wins(self) -> None:
        # 段 [1,10] 与 [8,20] 重叠：重叠句(8-10)归入先遍历的段1（break 语义）
        bm = BatchMetadata(
            id="f",
            batches=[
                {"区间": [1, 10], "视角": "创", "h": False, "用词色彩": "y"},
                {"区间": [8, 20], "视角": "创", "h": False, "用词色彩": "y"},
            ],
        )
        inst = _make_inst(bm)
        trans = [_FakeTrans(i) for i in range(1, 21)]
        groups = ForGalJsonMulitChat._group_by_batch_metadata(inst, trans, "f")
        self.assertEqual(len(groups), 2)
        self.assertEqual([t.runtime_index for t in groups[0]], list(range(1, 11)))
        self.assertEqual([t.runtime_index for t in groups[1]], list(range(11, 21)))

    def test_reverse_interval_auto_corrected(self) -> None:
        # 段写成 [10,1]（lo>hi）→ 自动交换成 [1,10]，句 5 应归入
        bm = BatchMetadata(
            id="f",
            batches=[{"区间": [10, 1], "视角": "创", "h": False, "用词色彩": "y"}],
        )
        inst = _make_inst(bm)
        trans = [_FakeTrans(i) for i in range(1, 11)]
        groups = ForGalJsonMulitChat._group_by_batch_metadata(inst, trans, "f")
        self.assertEqual(len(groups), 1)
        self.assertEqual([t.runtime_index for t in groups[0]], list(range(1, 11)))

    def test_segment_out_of_sentence_range_all_ungrouped(self) -> None:
        # 段 [100,110] 在句集 [1,10] 之外 → 段组空被跳过，全部句入尾组
        bm = BatchMetadata(
            id="f",
            batches=[{"区间": [100, 110], "视角": "创", "h": False, "用词色彩": "y"}],
        )
        inst = _make_inst(bm)
        trans = [_FakeTrans(i) for i in range(1, 11)]
        groups = ForGalJsonMulitChat._group_by_batch_metadata(inst, trans, "f")
        self.assertEqual(len(groups), 1)
        self.assertEqual([t.runtime_index for t in groups[0]], list(range(1, 11)))

    def test_no_runtime_index_falls_to_ungrouped(self) -> None:
        # 句子无 runtime_index/index → 归入尾组（不崩溃）
        bm = BatchMetadata(
            id="f",
            batches=[{"区间": [1, 4], "视角": "创", "h": False, "用词色彩": "y"}],
        )
        inst = _make_inst(bm)
        trans = [_FakeTrans(i) for i in range(1, 5)] + [_NoIdx() for _ in range(3)]
        groups = ForGalJsonMulitChat._group_by_batch_metadata(inst, trans, "f")
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(groups[0]), 4)
        self.assertEqual(len(groups[1]), 3)

    def test_empty_translist_returns_single_empty_group(self) -> None:
        inst = _make_inst(None)
        groups = ForGalJsonMulitChat._group_by_batch_metadata(inst, [], "f")
        self.assertEqual(groups, [[]])

    def test_all_ungrouped_single_unit_no_split(self) -> None:
        # 全部句不属于任何段 → 整文件作为单一尾组单元（大段不切）
        bm = BatchMetadata(
            id="f",
            batches=[{"区间": [100, 110], "视角": "创", "h": False, "用词色彩": "y"}],
        )
        inst = _make_inst(bm)
        trans = [_FakeTrans(i) for i in range(1, 51)]  # 50 行全尾组
        groups = ForGalJsonMulitChat._group_by_batch_metadata(inst, trans, "f")
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 50)

    def test_chunk_suffix_uses_global_range(self) -> None:
        # 文件名带 _2 后缀（chunk 2），mock 返回整文件 bm；用 chunk2 的全局行号句 [17,32]
        # 段 [1,8] 不相交（空）被跳过；段 [9,38] 相交 → 句 17-32 全归入该段
        bm = BatchMetadata(
            id="f",
            batches=[
                {"区间": [1, 8], "视角": "创", "h": False, "用词色彩": "y"},
                {"区间": [9, 38], "视角": "创", "h": False, "用词色彩": "y"},
            ],
        )
        inst = _make_inst(bm)  # _resolve_batch_metadata 直接返回整文件 bm（等价剥后缀后命中）
        trans = [_FakeTrans(i) for i in range(17, 33)]
        groups = ForGalJsonMulitChat._group_by_batch_metadata(inst, trans, "f_2")
        self.assertEqual(len(groups), 1)
        self.assertEqual([t.runtime_index for t in groups[0]], list(range(17, 33)))

    def test_batches_empty_list_falls_back(self) -> None:
        # 有 BatchMetadata 但 batches=[] → 退化为单组（零回归）
        bm = BatchMetadata(id="f", batches=[])
        inst = _make_inst(bm)
        trans = [_FakeTrans(i) for i in range(1, 11)]
        groups = ForGalJsonMulitChat._group_by_batch_metadata(inst, trans, "f")
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 10)

    def test_non_integer_interval_skipped(self) -> None:
        # 段区间非整数 ["a","b"] → int() 抛错被跳过 → 无有效段 → 回退单组
        bm = BatchMetadata(
            id="f",
            batches=[{"区间": ["a", "b"], "视角": "创", "h": False, "用词色彩": "y"}],
        )
        inst = _make_inst(bm)
        trans = [_FakeTrans(i) for i in range(1, 11)]
        groups = ForGalJsonMulitChat._group_by_batch_metadata(inst, trans, "f")
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 10)


class TestFormatBatchMetadataBlock(unittest.TestCase):
    def test_only_intersecting_segments_and_new_text(self) -> None:
        bm = BatchMetadata(
            id="f",
            batches=[
                {"区间": [1, 8], "视角": "创", "氛围": "日常轻松", "h": False, "用词色彩": "口语化"},
                {"区间": [9, 38], "视角": "创", "氛围": "日常惊艳", "h": False, "用词色彩": "细腻"},
                {"区间": [39, 45], "视角": "创", "氛围": "热忱", "h": False, "用词色彩": "简洁"},
            ],
        )
        inst = _make_inst(bm)
        block = ForGalJsonMulitChat._format_batch_metadata_block(inst, bm, 1, 16)
        self.assertIn("<batch_metadata>", block)
        # 仅相交段 [1,8] 与 [9,38] 出现；[39,45] 不出现
        self.assertIn("区间[1-8]", block)
        self.assertIn("区间[9-38]", block)
        self.assertNotIn("区间[39-45]", block)
        # 文案已更新：不再声称后续轮次不重复
        self.assertIn("每批次仅提供本批涉及的区间指导", block)
        self.assertNotIn("后续轮次将只提供待翻译句子", block)


class TestBuildRoundUserContentSubseqInject(unittest.TestCase):
    def test_subseq_round_injects_batch_metadata(self) -> None:
        inst = MagicMock(spec=ForGalJsonMulitChat)
        block = "<batch_metadata>\n区间[1-8] 视角:创\n</batch_metadata>\n"
        out = ForGalJsonMulitChat._build_round_user_content(
            inst,
            conv=[{"role": "system", "content": "x"}],  # len>1 → 续轮
            input_src="SRC_LINES",
            gptdict="DIC",
            filename="f",
            is_first_round=False,
            batch_metadata_block=block,
        )
        self.assertIn(block, out)
        self.assertIn("SRC_LINES", out)
        self.assertIn("DIC", out)


class TestBatchTranslateGrouping(unittest.IsolatedAsyncioTestCase):
    async def test_no_batch_metadata_keeps_fixed_slice(self) -> None:
        # 无批次元数据 → 退化为原固定切片（num_pre_request 用原值，不按段做大段不切）
        inst = _make_inst(None)
        inst._batch_translate_common = AsyncMock(return_value=["A", "B"])

        res = await ForGalJsonMulitChat.batch_translate(
            inst,
            filename="f",
            cache_file_path="",
            trans_list=[],
            num_pre_request=16,
            translist_unhit=["a", "b", "c", "d"],
        )
        self.assertEqual(res, ["A", "B"])
        inst._batch_translate_common.assert_awaited_once()
        # 仍按原切片值传递（未改成整组长度），行为与原实现一致，零回归
        _, kwargs = inst._batch_translate_common.call_args
        self.assertEqual(kwargs["num_pre_request"], 16)
        self.assertEqual(kwargs["translist_unhit"], ["a", "b", "c", "d"])

    async def test_grouped_segments_sent_as_single_unit_no_sub_split(self) -> None:
        # 有批次元数据 → 每段作为单一翻译单元，num_pre_request=组长度（大段不二次切割）
        bm = BatchMetadata(
            id="f",
            batches=[{"区间": [1, 99], "视角": "创", "h": False, "用词色彩": "y"}],
        )
        inst = _make_inst(bm)
        groups = [["a"], ["b", "c"], ["d", "e", "f"]]  # 各段长 1/2/3 行
        inst._group_by_batch_metadata.return_value = groups
        inst._batch_translate_common = AsyncMock(
            side_effect=lambda **kw: list(kw["translist_unhit"])
        )

        res = await ForGalJsonMulitChat.batch_translate(
            inst,
            filename="f",
            cache_file_path="",
            trans_list=[],
            num_pre_request=16,
            translist_unhit=["a", "b", "c", "d", "e", "f"],
        )
        self.assertEqual(res, ["a", "b", "c", "d", "e", "f"])
        # 每组各调用一次，且 num_pre_request = 该组长度（验证不子切）
        self.assertEqual(inst._batch_translate_common.await_count, 3)
        seen = [
            c.kwargs["num_pre_request"]
            for c in inst._batch_translate_common.await_args_list
        ]
        self.assertEqual(seen, [1, 2, 3])
        # 每组传入的句子即该段本身
        groups_seen = [
            c.kwargs["translist_unhit"]
            for c in inst._batch_translate_common.await_args_list
        ]
        self.assertEqual(groups_seen, groups)

    async def test_grouped_segments_pass_force_static_true(self) -> None:
        # 有批次元数据 → 调用 _batch_translate_common 应带 force_static=True（禁用动态）
        bm = BatchMetadata(
            id="f",
            batches=[{"区间": [1, 99], "视角": "创", "h": False, "用词色彩": "y"}],
        )
        inst = _make_inst(bm)
        inst._batch_translate_common = AsyncMock(return_value=["a"])
        inst._group_by_batch_metadata.return_value = [["a", "b"]]
        await ForGalJsonMulitChat.batch_translate(
            inst,
            filename="f",
            cache_file_path="",
            trans_list=[],
            num_pre_request=16,
            translist_unhit=["a", "b"],
        )
        inst._batch_translate_common.assert_awaited()
        self.assertTrue(inst._batch_translate_common.call_args.kwargs["force_static"])

    async def test_no_metadata_passes_force_static_false(self) -> None:
        # 无批次元数据 → 退化分支应带 force_static=False（保留动态模式）
        inst = _make_inst(None)
        inst._batch_translate_common = AsyncMock(return_value=["a"])
        await ForGalJsonMulitChat.batch_translate(
            inst,
            filename="f",
            cache_file_path="",
            trans_list=[],
            num_pre_request=16,
            translist_unhit=["a", "b"],
        )
        # 未显式传 force_static 即取默认 False（退化分支仍走动态模式）
        self.assertFalse(
            inst._batch_translate_common.call_args.kwargs.get("force_static", False)
        )


class TestDynamicModeOnlyWithoutMetadata(unittest.IsolatedAsyncioTestCase):
    def _make_common_inst(self, dynamic: bool, dmax: int = 4, dmin: int = 1):
        # 构造可真实调用 _batch_translate_common 的替身（绑定基类真实动态逻辑）
        inst = MagicMock(spec=ForGalJsonMulitChat)
        inst.dynamic_num_per_request = dynamic
        inst.dynamic_num_per_request_max = dmax
        inst.dynamic_num_per_request_min = dmin
        inst._dynamic_num_per_request_current = None
        inst._dynamic_num_per_request_success_streak = 0
        inst.skipH = False
        inst.save_steps = 10 ** 9
        inst.pj_config = MagicMock()
        inst.pj_config.bar = lambda n: None
        inst._check_stop_requested = lambda: None
        inst._record_runtime_success = lambda *a, **k: None
        inst._coerce_positive_int = lambda v, d=1: max(d, int(v))

        async def _translate(trans_list_split, dic_prompt, proofread=False, filename=""):
            out = []
            for t in trans_list_split:
                t.pre_dst = ""
                t.trans_by = ""
                out.append(t)
            return len(trans_list_split), out

        inst.translate = AsyncMock(side_effect=_translate)
        # 绑定基类真实方法，验证 force_static 对动态切片的实际影响
        inst._get_effective_num_per_request = (
            BaseTranslate._get_effective_num_per_request.__get__(inst)
        )
        inst._update_dynamic_num_per_request = (
            BaseTranslate._update_dynamic_num_per_request.__get__(inst)
        )
        return inst

    async def test_force_static_keeps_large_group_unsplit(self) -> None:
        # 有元数据场景（force_static=True）：即便 dynamic 开启且 max=4，
        # 10 行大段应作为单一单元（切片=10），且全局动态状态不被污染
        inst = self._make_common_inst(dynamic=True, dmax=4)
        trans = [_FakeTrans(i) for i in range(1, 11)]
        await BaseTranslate._batch_translate_common(
            inst,
            filename="f",
            cache_file_path="",
            translist_unhit=trans,
            num_pre_request=len(trans),
            force_static=True,
        )
        self.assertEqual(inst.translate.await_count, 1)
        split = inst.translate.call_args.args[0]
        self.assertEqual(len(split), 10)
        self.assertIsNone(inst._dynamic_num_per_request_current)

    async def test_no_force_static_splits_under_dynamic(self) -> None:
        # 对照：无 force_static（即无元数据退化分支）时，dynamic 开启会按 max 子切
        inst = self._make_common_inst(dynamic=True, dmax=4)
        trans = [_FakeTrans(i) for i in range(1, 11)]
        await BaseTranslate._batch_translate_common(
            inst,
            filename="f",
            cache_file_path="",
            translist_unhit=trans,
            num_pre_request=len(trans),
        )
        self.assertGreater(inst.translate.await_count, 1)


class TestGetEffectiveNumPerRequest(unittest.TestCase):
    def _make(self, dynamic: bool, dmax: int = 4, dmin: int = 1):
        m = MagicMock()
        m._coerce_positive_int = lambda v, d=1: max(d, int(v))
        m.dynamic_num_per_request = dynamic
        m.dynamic_num_per_request_max = dmax
        m.dynamic_num_per_request_min = dmin
        m._dynamic_num_per_request_current = None
        return m

    def test_dynamic_enabled_clamps_without_force(self) -> None:
        # 无 force_static：dynamic 开启时大值被 clamp 到 max
        m = self._make(dynamic=True, dmax=4)
        self.assertEqual(BaseTranslate._get_effective_num_per_request(m, 10), 4)

    def test_force_static_overrides_dynamic(self) -> None:
        # 有 force_static：即便 dynamic 开启也返回配置值（大段不切）
        m = self._make(dynamic=True, dmax=4)
        self.assertEqual(
            BaseTranslate._get_effective_num_per_request(m, 10, force_static=True), 10
        )

    def test_dynamic_disabled_returns_configured(self) -> None:
        m = self._make(dynamic=False)
        self.assertEqual(BaseTranslate._get_effective_num_per_request(m, 10), 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
