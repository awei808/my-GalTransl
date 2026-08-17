# -*- coding: utf-8 -*-
"""ForSemCheck 语义差异检测的单元测试。

覆盖：
  - _parse_fix_response：按 id 稀疏解析（id/可选 reason），命中句置 suspected_error
  - 脏 JSON 容错（尾随垃圾 / 前置思考文字）与未知 id 跳过
  - find_problems 认领：suspected_error 非空 → 输出「疑似错误」问题
  - _filter_target_translations：全量已译句（含 h 场景），排除无译文/Failed/skip_check
  - batch_translate：未启用/未配置时降级跳过（不发请求、保留旧标记）；启用时清旧标记（幂等）
  - Cache._build_cache_obj：suspected_error 随快照落盘
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from GalTransl.CSentense import CSentense
from GalTransl.Cache import _build_cache_obj
from GalTransl.Problem import find_problems
from GalTransl.ConfigHelper import CProblemType
from GalTransl.Backend.BaseFixRound import BaseSparseFixRound
from GalTransl.Backend.ForSemCheck import ForSemCheck


class _FakePjConfig:
    """find_problems 配置替身：仅启用「疑似错误」检测项。

    getProblemAnalyzeConfig 与真实 CProjectConfig 一致返回 CProblemType 枚举列表。
    """

    def getProblemAnalyzeArinashiDict(self) -> dict:
        return {}

    def getProblemAnalyzeConfig(self, key: str) -> list:
        return [CProblemType.疑似错误] if key == "problemList" else []

    def hasProblemAnalyzeConfig(self, key: str) -> bool:
        return True


def _make_parser() -> ForSemCheck:
    """绕过重型 __init__，仅装配 _parse_fix_response / 筛选所需属性。"""
    obj = object.__new__(ForSemCheck)
    obj._log_tag = "[语义检测]"
    obj._disabled_reason = ""
    obj.pj_config = _FakePjConfig()
    return obj


def _trans(index: int, pre_dst: str = "译文", post_src: str = "原文") -> CSentense:
    t = CSentense(f"src{index}", index=index)
    t.post_src = post_src
    t.pre_dst = pre_dst
    t.proofread_zh = ""
    t.suspected_error = ""
    return t


class ParseSemcheckJsonlineTests(unittest.TestCase):
    def test_hit_sets_suspected_error_default_one(self) -> None:
        parser = _make_parser()
        trans = _trans(3)
        line = 'tma|{"id": 3}'
        success, found = parser._parse_fix_response(line, [trans], "\r\n")
        self.assertEqual(found, 1)
        self.assertEqual(success, 1)
        self.assertEqual(trans.suspected_error, "1")

    def test_hit_with_reason_keeps_reason(self) -> None:
        parser = _make_parser()
        trans = _trans(12)
        line = '1mj|{"id": 12, "reason": "译文串行"}'
        success, found = parser._parse_fix_response(line, [trans], "\r\n")
        self.assertEqual(found, 1)
        self.assertEqual(trans.suspected_error, "译文串行")

    def test_sparse_by_id_order_irrelevant(self) -> None:
        parser = _make_parser()
        trans_list = [_trans(3), _trans(12), _trans(14)]
        text = "\n".join(
            [
                'tc7|{"id": 14}',
                'tma|{"id": 3, "reason": "漏译"}',
                '1mj|{"id": 12}',
            ]
        )
        success, found = parser._parse_fix_response(text, trans_list, "\r\n")
        self.assertEqual(found, 3)
        self.assertEqual(success, 3)
        self.assertEqual(trans_list[0].suspected_error, "漏译")
        self.assertEqual(trans_list[1].suspected_error, "1")
        self.assertEqual(trans_list[2].suspected_error, "1")

    def test_trailing_garbage_and_leading_text_tolerated(self) -> None:
        parser = _make_parser()
        trans = _trans(94)
        text = (
            '思考一下：p4c|{"id": 94, "reason": "含义相反"}</br>；\n'
        )
        success, found = parser._parse_fix_response(text, [trans], "\r\n")
        self.assertEqual(found, 1)
        self.assertEqual(trans.suspected_error, "含义相反")

    def test_unknown_id_skipped(self) -> None:
        parser = _make_parser()
        trans = _trans(1)
        line = 'nnk|{"id": 999}'
        success, found = parser._parse_fix_response(line, [trans], "\r\n")
        self.assertEqual(found, 0)
        self.assertEqual(trans.suspected_error, "")

    def test_non_json_line_skipped(self) -> None:
        parser = _make_parser()
        trans = _trans(1)
        line = "纯文本没有 JSON"
        success, found = parser._parse_fix_response(line, [trans], "\r\n")
        self.assertEqual(found, 0)
        self.assertEqual(trans.suspected_error, "")


class FilterTargetTranslationsTests(unittest.TestCase):
    def test_all_translated_included_including_h_scene(self) -> None:
        parser = _make_parser()
        # index=50 处于 H 区间（1-100）内，语义检测不区分 h 场景，照常筛选
        trans_list = [
            _trans(1, "正常译文", "原文1"),
            _trans(50, "h场景译文", "h原文"),
            _trans(2, "", "无译文原文"),  # pre_dst 为空 → 排除
        ]
        trans_list[0].skip_check = True  # 跳过检查 → 排除
        targets = parser._filter_target_translations(trans_list)
        self.assertEqual([t.index for t in targets], [50])

    def test_failed_prefix_excluded(self) -> None:
        parser = _make_parser()
        trans = _trans(5, "(Failed) 翻译失败", "原文")
        targets = parser._filter_target_translations([trans])
        self.assertEqual(targets, [])


class FindProblemsClaimTests(unittest.TestCase):
    def test_suspected_error_claimed_as_problem(self) -> None:
        trans = _trans(1)
        trans.suspected_error = "译文串行"
        find_problems([trans], _FakePjConfig())
        self.assertIn("疑似错误", trans.problem)

    def test_no_suspected_error_no_problem(self) -> None:
        trans = _trans(1)
        trans.suspected_error = ""
        find_problems([trans], _FakePjConfig())
        self.assertEqual(trans.problem, "")


class BatchTranslateGuardTests(unittest.TestCase):
    def test_disabled_skips_and_keeps_old_mark(self) -> None:
        obj = object.__new__(ForSemCheck)
        obj._log_tag = "[语义检测]"
        obj._disabled_reason = "gpt.semCheck.enabled 未启用"
        trans = _trans(1)
        trans.suspected_error = "旧标记"
        trans_list = [trans]
        with patch.object(
            BaseSparseFixRound, "batch_translate", new=AsyncMock(return_value=None)
        ) as mock_super:
            result = asyncio.run(
                obj.batch_translate("f.json", "cache.json", trans_list, 100)
            )
        mock_super.assert_not_awaited()
        self.assertIs(result, trans_list)  # 降级直接返回原列表
        self.assertEqual(trans.suspected_error, "旧标记")  # 降级不清理旧标记

    def test_enabled_clears_old_marks_before_run(self) -> None:
        obj = object.__new__(ForSemCheck)
        obj._log_tag = "[语义检测]"
        obj._disabled_reason = ""
        trans_list = [_trans(1), _trans(2)]
        trans_list[0].suspected_error = "旧标记1"
        trans_list[1].suspected_error = "旧标记2"
        with patch.object(
            BaseSparseFixRound,
            "batch_translate",
            new=AsyncMock(return_value=trans_list),
        ) as mock_super:
            result = asyncio.run(
                obj.batch_translate("f.json", "cache.json", trans_list, 100)
            )
        mock_super.assert_awaited_once()
        self.assertIs(result, trans_list)
        self.assertEqual(trans_list[0].suspected_error, "")  # 幂等：清旧
        self.assertEqual(trans_list[1].suspected_error, "")


class CacheSerializationTests(unittest.TestCase):
    def test_suspected_error_written_to_cache_obj(self) -> None:
        trans = _trans(1)
        trans.suspected_error = "译文串行"
        cache_obj = _build_cache_obj(trans, post_save=True)
        self.assertEqual(cache_obj["suspected_error"], "译文串行")

    def test_empty_suspected_error_not_written(self) -> None:
        trans = _trans(1)
        trans.suspected_error = ""
        cache_obj = _build_cache_obj(trans, post_save=True)
        self.assertNotIn("suspected_error", cache_obj)


if __name__ == "__main__":
    unittest.main()
