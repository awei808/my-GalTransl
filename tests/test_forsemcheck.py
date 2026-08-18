# -*- coding: utf-8 -*-
"""ForSemCheck 语义差异检测的单元测试。

覆盖：
  - _parse_fix_response：按 id 稀疏解析（id/可选 reason），命中句置 suspected_error
  - 脏 JSON 容错（尾随垃圾 / 前置思考文字）与未知 id 跳过
  - find_problems 认领：suspected_error 非空 → 输出「疑似错误」问题
  - _filter_target_translations：全量已译句（含 h 场景），排除无译文/Failed/skip_check
  - batch_translate：主翻译令牌池无可用 token 时降级跳过（不发请求、保留旧标记）；可用时清旧标记（幂等）
  - 单轮 user 提示词：只注入任务说明与批次 input，不注入术语表/批次元数据/历史/规范等
  - Cache._build_cache_obj：suspected_error 随快照落盘
"""
import asyncio
import re
import unittest
from unittest.mock import AsyncMock, Mock, patch

from GalTransl.CSentense import CSentense
from GalTransl.Cache import _build_cache_obj
from GalTransl.Problem import find_problems
from GalTransl.ConfigHelper import CProblemType
from GalTransl.Backend.Prompts import FORGAL_JSON_SEMCHECK_PROMPT
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

    def getKey(self, key: str):
        # 测试默认不配置批次相关键，返回 None 以触发回退逻辑
        return None


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
        obj._disabled_reason = "主翻译令牌池无可用 token"
        trans = _trans(1)
        trans.suspected_error = "旧标记"
        trans_list = [trans]
        with patch.object(ForSemCheck, "_call_llm", new=AsyncMock()) as mock_llm:
            result = asyncio.run(
                obj.batch_translate("f.json", "cache.json", trans_list, 100)
            )
        mock_llm.assert_not_awaited()  # 降级不发任何请求
        self.assertIs(result, trans_list)  # 降级直接返回原列表
        self.assertEqual(trans.suspected_error, "旧标记")  # 降级不清理旧标记

    def test_enabled_clears_old_marks_and_runs_single_round(self) -> None:
        obj = object.__new__(ForSemCheck)
        obj._log_tag = "[语义检测]"
        obj._disabled_reason = ""
        obj.pj_config = _FakePjConfig()
        trans_list = [_trans(1), _trans(2)]
        trans_list[0].suspected_error = "旧标记1"
        trans_list[1].suspected_error = "旧标记2"
        with (
            patch.object(
                ForSemCheck, "_filter_target_translations", return_value=[]
            ),
            patch.object(ForSemCheck, "_call_llm", new=AsyncMock()) as mock_llm,
        ):
            result = asyncio.run(
                obj.batch_translate("f.json", "cache.json", trans_list, 100)
            )
        mock_llm.assert_not_awaited()  # 无可检测句子，不发请求
        self.assertIs(result, trans_list)
        self.assertEqual(trans_list[0].suspected_error, "")  # 幂等：清旧
        self.assertEqual(trans_list[1].suspected_error, "")


class SemcheckPromptInjectionTests(unittest.TestCase):
    """验证单轮 user 提示词：仅注入任务说明与批次 input，不注入其它内容。"""

    def _make_obj(self) -> ForSemCheck:
        obj = object.__new__(ForSemCheck)
        obj.trans_prompt = FORGAL_JSON_SEMCHECK_PROMPT
        obj.target_lang = "Simplified_Chinese"
        return obj

    def test_injects_only_task_and_input(self) -> None:
        obj = self._make_obj()
        prompt = obj._build_semcheck_user_content(input_src='#01|{"id":1}')
        self.assertIn("### 任务", prompt)  # 任务说明注入
        self.assertIn("#01|{\"id\":1}", prompt)  # input 注入
        self.assertIn("Simplified_Chinese", prompt)  # 目标语言占位符已替换

    def test_no_glossary_or_batch_or_metadata(self) -> None:
        obj = self._make_obj()
        prompt = obj._build_semcheck_user_content(input_src='#01|{"id":1}')
        self.assertNotIn("[Glossary]", prompt)
        self.assertNotIn("[translation_guideline]", prompt)
        self.assertNotIn("[global_prompt]", prompt)
        self.assertNotIn("[plot_metadata]", prompt)
        self.assertNotIn("[history_result]", prompt)
        self.assertNotIn("<batch_metadata>", prompt)
        self.assertNotIn("<translation_guidelines>", prompt)
        self.assertNotIn("<glossary>", prompt)
        self.assertNotIn("[TargetLang]", prompt)  # 已替换
        self.assertNotIn("[Input]", prompt)  # 已替换


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


class _FakeBatchConfig:
    """语义检测分批配置替身：可控 getKey 返回值，active_workers=1 触发输入日志。"""

    def __init__(self, values: dict) -> None:
        self._values = values

    active_workers = 1

    def getKey(self, key: str):
        return self._values.get(key)


class SemcheckBatchSplitTests(unittest.TestCase):
    """验证 batch_translate 按 numPerRequestSemCheck 分批发送，且优先于改进轮批次。"""

    def _run_split(self, values: dict, num_arg: int = 20, total: int = 5) -> list:
        obj = object.__new__(ForSemCheck)
        obj._log_tag = "[语义检测]"
        obj._disabled_reason = ""
        obj.pj_config = _FakeBatchConfig(values)
        obj.system_prompt = "system"
        obj.trans_prompt = FORGAL_JSON_SEMCHECK_PROMPT
        obj.target_lang = "Simplified_Chinese"
        obj.eng_type = "ForSemCheck"
        obj._resolve_batch_metadata = Mock(return_value=None)
        calls = []

        async def fake_llm(messages, filename, idx_tip, cb):
            calls.append(messages)
            return "", None

        obj._call_llm = fake_llm
        targets = [_trans(i) for i in range(1, total + 1)]
        with patch.object(
            ForSemCheck, "_filter_target_translations", return_value=targets
        ):
            asyncio.run(obj.batch_translate("f.json", "c.json", targets, num_arg))
        sizes = []
        for m in calls:
            user = m[1]["content"]
            in_input = False
            n = 0
            for line in user.splitlines():
                if line.strip().startswith("<input>"):
                    in_input = True
                    continue
                if line.strip().startswith("</input>"):
                    in_input = False
                    continue
                if in_input and re.match(r"^[A-Za-z0-9]{3}\|", line.strip()):
                    n += 1
            sizes.append(n)
        return sizes

    def test_semcheck_batch_preferred_over_better(self) -> None:
        # numPerRequestSemCheck=2 优先，5 句分成 2,2,1
        sizes = self._run_split(
            {"gpt.numPerRequestSemCheck": 2, "gpt.numPerRequestBetter": 100}
        )
        self.assertEqual(sizes, [2, 2, 1])

    def test_falls_back_to_numPerRequestBetter(self) -> None:
        # 未配置 semCheck 时回退 numPerRequestBetter=2
        sizes = self._run_split({"gpt.numPerRequestBetter": 2})
        self.assertEqual(sizes, [2, 2, 1])

    def test_falls_back_to_argument_when_any_unset(self) -> None:
        # 两者均未配置时回退实参 num_arg=2
        sizes = self._run_split({}, num_arg=2)
        self.assertEqual(sizes, [2, 2, 1])


if __name__ == "__main__":
    unittest.main()
