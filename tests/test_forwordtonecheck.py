# -*- coding: utf-8 -*-
"""ForToneCheck 词语色彩一致性检查的单元测试。

覆盖：
  - _parse_fix_response：按 id 稀疏解析（id/可选 reason），命中句置 tone_issue
  - find_problems 认领：tone_issue 非空 → 输出「词语色彩不一致」（reason 附带展示）
  - _filter_target_translations：全量已译句，排除无译文/Failed/skip_check
  - batch_translate：禁用降级跳过（保留旧标记）；无批次元数据跳过（幂等清旧标记）
  - 分批：优先 gpt.numPerRequestToneCheck，回退 numPerRequestSemCheck / numPerRequestBetter / 实参
  - 区间色彩标注渲染与 user 提示词占位符替换（无标注时移除 tone_guide 段）
  - Cache._build_cache_obj：tone_issue 随快照落盘
  - 注册链：ENGINE_MODULE_PATHS / ENGINE_REGISTRY / NEED_OpenAITokenPool / afterTranslation 白名单
"""
import asyncio
import re
import unittest
from unittest.mock import Mock, patch

from GalTransl.CSentense import CSentense
from GalTransl.Cache import _build_cache_obj
from GalTransl.Problem import find_problems
from GalTransl.ConfigHelper import CProblemType
from GalTransl.Backend.Prompts import FORGAL_JSON_FORWORDTONE_PROMPT
from GalTransl.Backend.ForToneCheck import ForToneCheck
from GalTransl.Backend.metadata import BatchMetadata


class _FakePjConfig:
    """find_problems 配置替身：仅启用「词语色彩不一致」检测项。"""

    def getProblemAnalyzeArinashiDict(self) -> dict:
        return {}

    def getProblemAnalyzeConfig(self, key: str) -> list:
        return [CProblemType.词语色彩不一致] if key == "problemList" else []

    def hasProblemAnalyzeConfig(self, key: str) -> bool:
        return True

    def getKey(self, key: str):
        return None


def _make_parser() -> ForToneCheck:
    """绕过重型 __init__，仅装配 _parse_fix_response 所需属性。"""
    obj = object.__new__(ForToneCheck)
    obj._log_tag = "[色彩检查]"
    obj._disabled_reason = ""
    obj.pj_config = _FakePjConfig()
    return obj


def _trans(index: int, pre_dst: str = "译文", post_src: str = "原文") -> CSentense:
    t = CSentense(f"src{index}", index=index)
    t.post_src = post_src
    t.pre_dst = pre_dst
    t.proofread_zh = ""
    t.tone_issue = ""
    return t


def _batch_meta(batches: list) -> BatchMetadata:
    return BatchMetadata(id="f.json", batches=batches)


class ParseToneJsonlineTests(unittest.TestCase):
    def test_hit_sets_default_one(self) -> None:
        parser = _make_parser()
        trans = _trans(3)
        line = 'tma|{"id": 3}'
        success, found = parser._parse_fix_response(line, [trans], "\r\n")
        self.assertEqual((success, found), (1, 1))
        self.assertEqual(trans.tone_issue, "1")

    def test_hit_with_reason_keeps_reason(self) -> None:
        parser = _make_parser()
        trans = _trans(12)
        line = '1mj|{"id": 12, "reason": "标注口语活泼，译文过于书面"}'
        parser._parse_fix_response(line, [trans], "\r\n")
        self.assertEqual(trans.tone_issue, "标注口语活泼，译文过于书面")

    def test_sparse_by_id_and_unknown_skipped(self) -> None:
        parser = _make_parser()
        trans_list = [_trans(3), _trans(12)]
        text = 'aaa|{"id": 999}\nbbb|{"id": 12, "reason": "庄重典雅预期，译文粗俗"}'
        success, found = parser._parse_fix_response(text, trans_list, "\r\n")
        self.assertEqual((success, found), (1, 1))
        self.assertEqual(trans_list[0].tone_issue, "")
        self.assertEqual(trans_list[1].tone_issue, "庄重典雅预期，译文粗俗")

    def test_mojibake_reason_degrades_to_default(self) -> None:
        parser = _make_parser()
        trans = _trans(7)
        line = 'aaa|{"id": 7, "reason": "\ufffd\ufffd"}'
        parser._parse_fix_response(line, [trans], "\r\n")
        self.assertEqual(trans.tone_issue, "1")


class FindProblemsClaimTests(unittest.TestCase):
    def test_tone_issue_claimed_with_reason(self) -> None:
        trans = _trans(1)
        trans.tone_issue = "标注露骨直白，译文含蓄"
        find_problems([trans], _FakePjConfig())
        self.assertIn("词语色彩不一致：标注露骨直白，译文含蓄", trans.problem)

    def test_tone_issue_default_claimed_plain(self) -> None:
        trans = _trans(1)
        trans.tone_issue = "1"
        find_problems([trans], _FakePjConfig())
        self.assertIn("词语色彩不一致", trans.problem)
        self.assertNotIn("词语色彩不一致：", trans.problem)

    def test_no_tone_issue_no_problem(self) -> None:
        trans = _trans(1)
        trans.tone_issue = ""
        find_problems([trans], _FakePjConfig())
        self.assertEqual(trans.problem, "")

    def test_fix_spec_registered_for_claimed_type(self) -> None:
        # 统一问题修复后端必须注册该类型的修复指令（检查→修复闭环）
        from GalTransl.Backend.ForFixRound import _FIX_SPECS, build_fix_instructions

        self.assertIn(CProblemType.词语色彩不一致, _FIX_SPECS)
        self.assertIn("用词色彩", build_fix_instructions([CProblemType.词语色彩不一致]))


class FilterTargetTranslationsTests(unittest.TestCase):
    def test_all_translated_included(self) -> None:
        parser = _make_parser()
        trans_list = [
            _trans(1, "正常译文", "原文1"),
            _trans(2, "", "无译文原文"),  # pre_dst 为空 → 排除
        ]
        trans_list[0].skip_check = True  # 跳过检查 → 排除
        targets = parser._filter_target_translations(trans_list)
        self.assertEqual(targets, [])

    def test_failed_prefix_excluded(self) -> None:
        parser = _make_parser()
        targets = parser._filter_target_translations([_trans(5, "(Failed) 失败", "原文")])
        self.assertEqual(targets, [])


class _FakeBatchConfig:
    """分批配置替身：可控 getKey 返回值。"""

    def __init__(self, values: dict) -> None:
        self._values = values

    active_workers = 1

    def getKey(self, key: str):
        return self._values.get(key)


class _ToneCheckTestBase:
    """装配 batch_translate 测试替身的公共方法。"""

    def _make_obj(self, values: dict, batches: list | None, llm_resp: str = "") -> ForToneCheck:
        obj = object.__new__(ForToneCheck)
        obj._log_tag = "[色彩检查]"
        obj._disabled_reason = ""
        obj.pj_config = _FakeBatchConfig(values)
        obj.system_prompt = "system"
        obj.trans_prompt = FORGAL_JSON_FORWORDTONE_PROMPT
        obj.target_lang = "Simplified_Chinese"
        obj.eng_type = "ForToneCheck"
        obj._recorded_errors = []
        obj._llm_calls = []
        if batches is None:
            obj._resolve_batch_metadata = Mock(return_value=None)
        else:
            obj._resolve_batch_metadata = Mock(return_value=_batch_meta(batches))

        def fake_record(filename, idx_tip, message, model):
            obj._recorded_errors.append((filename, idx_tip, message, model))

        obj._record_round_runtime_error = fake_record

        async def fake_llm(messages, filename, idx_tip, cb):
            obj._llm_calls.append(messages)
            return llm_resp, None

        obj._call_llm = fake_llm
        return obj


class BatchTranslateGuardTests(unittest.IsolatedAsyncioTestCase, _ToneCheckTestBase):
    async def test_disabled_skips_and_keeps_old_mark(self) -> None:
        obj = object.__new__(ForToneCheck)
        obj._log_tag = "[色彩检查]"
        obj._disabled_reason = "主翻译令牌池无可用 token"
        trans = _trans(1)
        trans.tone_issue = "旧标记"
        trans_list = [trans]
        with patch.object(ForToneCheck, "_call_llm") as mock_llm:
            result = await obj.batch_translate("f.json", "c.json", trans_list, 100)
        mock_llm.assert_not_awaited()
        self.assertIs(result, trans_list)
        self.assertEqual(trans.tone_issue, "旧标记")  # 降级不清理旧标记

    async def test_no_batch_metadata_skips_after_clearing(self) -> None:
        # 无批次元数据（无标注基准）→ 清旧标记后跳过（幂等），不发请求
        obj = self._make_obj({}, batches=None)
        trans_list = [_trans(1), _trans(2)]
        trans_list[0].tone_issue = "旧标记"
        with patch.object(
            ForToneCheck, "_filter_target_translations", return_value=trans_list
        ):
            result = await obj.batch_translate("f.json", "c.json", trans_list, 20)
        self.assertIs(result, trans_list)
        self.assertEqual(trans_list[0].tone_issue, "")
        self.assertEqual(len(obj._llm_calls), 0)

    async def test_marks_written_and_cleared_idempotently(self) -> None:
        batches = [{"区间": [1, 10], "视角": "创", "氛围": "日常", "用词色彩": "口语活泼"}]
        obj = self._make_obj({}, batches=batches, llm_resp='ab1|{"id": 2, "reason": "过于书面"}')
        trans_list = [_trans(i) for i in range(1, 4)]
        trans_list[0].tone_issue = "旧标记"
        with patch.object(
            ForToneCheck, "_filter_target_translations", return_value=trans_list
        ):
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        self.assertEqual(trans_list[0].tone_issue, "")  # 未命中且旧标记被清
        self.assertEqual(trans_list[1].tone_issue, "过于书面")  # id 2 命中
        self.assertEqual(trans_list[2].tone_issue, "")
        # user 提示词注入了区间色彩标注
        user = obj._llm_calls[0][-1]["content"]
        self.assertIn("区间[1-10]", user)
        self.assertIn("口语活泼", user)


class ToneCheckBatchSplitTests(unittest.IsolatedAsyncioTestCase, _ToneCheckTestBase):
    """验证分批优先级：numPerRequestToneCheck > numPerRequestSemCheck > numPerRequestBetter > 实参。"""

    async def _run_split(self, values: dict, num_arg: int = 20, total: int = 5) -> list:
        batches = [{"区间": [1, 100], "用词色彩": "口语"}]
        obj = self._make_obj(values, batches=batches)
        targets = [_trans(i) for i in range(1, total + 1)]
        with patch.object(
            ForToneCheck, "_filter_target_translations", return_value=targets
        ):
            await obj.batch_translate("f.json", "c.json", targets, num_arg)
        sizes = []
        for m in obj._llm_calls:
            user = m[-1]["content"]
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

    async def test_tone_check_key_preferred(self) -> None:
        sizes = await self._run_split(
            {"gpt.numPerRequestToneCheck": 2, "gpt.numPerRequestSemCheck": 3}
        )
        self.assertEqual(sizes, [2, 2, 1])

    async def test_falls_back_to_semcheck_key(self) -> None:
        sizes = await self._run_split({"gpt.numPerRequestSemCheck": 3})
        self.assertEqual(sizes, [3, 2])

    async def test_falls_back_to_better_key(self) -> None:
        sizes = await self._run_split({"gpt.numPerRequestBetter": 4})
        self.assertEqual(sizes, [4, 1])

    async def test_falls_back_to_argument(self) -> None:
        sizes = await self._run_split({}, num_arg=2)
        self.assertEqual(sizes, [2, 2, 1])


class ToneGuideFormatTests(unittest.TestCase):
    def _make_obj(self) -> ForToneCheck:
        obj = object.__new__(ForToneCheck)
        obj.trans_prompt = FORGAL_JSON_FORWORDTONE_PROMPT
        obj.target_lang = "Simplified_Chinese"
        return obj

    def test_guide_rendered_with_fields(self) -> None:
        obj = self._make_obj()
        bm = _batch_meta(
            [
                {"区间": [1, 10], "视角": "凛音", "氛围": "情欲紧张", "用词色彩": "露骨、感官"},
                {"区间": [11, 20], "用词色彩": "克制"},
            ]
        )
        trans1, trans2 = _trans(1), _trans(12)
        trans1.runtime_index = 1
        trans2.runtime_index = 12
        guide = obj._format_tone_guide([trans1, trans2], bm)
        self.assertIn("区间[1-10]", guide)
        self.assertIn("视角:凛音", guide)
        self.assertIn("用词色彩:露骨、感官", guide)
        self.assertIn("区间[11-20]", guide)  # 批内句子各自区间都渲染

    def test_guide_only_relevant_intervals(self) -> None:
        # 批内句子仅落在 [1-10] 时，不相交的 [11-20] 不渲染
        obj = self._make_obj()
        bm = _batch_meta(
            [
                {"区间": [1, 10], "用词色彩": "口语"},
                {"区间": [11, 20], "用词色彩": "克制"},
            ]
        )
        t = _trans(1)
        t.runtime_index = 3
        guide = obj._format_tone_guide([t], bm)
        self.assertIn("区间[1-10]", guide)
        self.assertNotIn("区间[11-20]", guide)

    def test_guide_empty_when_no_intersection(self) -> None:
        obj = self._make_obj()
        bm = _batch_meta([{"区间": [50, 60], "用词色彩": "口语"}])
        t = _trans(1)
        t.runtime_index = 1
        self.assertEqual(obj._format_tone_guide([t], bm), "")

    def test_user_content_replaces_placeholders(self) -> None:
        obj = self._make_obj()
        content = obj._build_tonecheck_user_content('#ab|{"id":1}', "色彩标注内容")
        self.assertIn("<tone_guide>", content)
        self.assertIn("色彩标注内容", content)
        self.assertIn('#ab|{"id":1}', content)
        self.assertIn("Simplified_Chinese", content)
        self.assertNotIn("[ToneGuide]", content)
        self.assertNotIn("[Input]", content)
        self.assertNotIn("[TargetLang]", content)

    def test_user_content_removes_guide_when_empty(self) -> None:
        obj = self._make_obj()
        content = obj._build_tonecheck_user_content('#ab|{"id":1}', "")
        self.assertNotIn("<tone_guide>", content)
        self.assertIn("### 任务", content)  # 任务说明保留
        self.assertIn('#ab|{"id":1}', content)


class ToneCacheSerializationTests(unittest.TestCase):
    def test_tone_issue_written_to_cache_obj(self) -> None:
        trans = _trans(1)
        trans.tone_issue = "标注口语活泼，译文书面"
        cache_obj = _build_cache_obj(trans, post_save=True)
        self.assertEqual(cache_obj["tone_issue"], "标注口语活泼，译文书面")

    def test_empty_tone_issue_not_written(self) -> None:
        trans = _trans(1)
        trans.tone_issue = ""
        cache_obj = _build_cache_obj(trans, post_save=True)
        self.assertNotIn("tone_issue", cache_obj)


class ToneCheckRegistrationTests(unittest.TestCase):
    def test_engine_registered(self) -> None:
        from GalTransl.Backend.BaseEngine import ENGINE_MODULE_PATHS, ENGINE_REGISTRY

        self.assertEqual(ENGINE_MODULE_PATHS["ForToneCheck"], "GalTransl.Backend.ForToneCheck")
        self.assertIn("ForToneCheck", ENGINE_REGISTRY)

    def test_engine_in_token_pool_and_after_trans_whitelist(self) -> None:
        from GalTransl import NEED_OpenAITokenPool
        from GalTransl.Frontend.LLMTranslate import _resolve_after_translation_order

        self.assertIn("ForToneCheck", NEED_OpenAITokenPool)

        class _Cfg:
            def __init__(self, raw) -> None:
                self._raw = raw
                self.keyValues = {"gpt.afterTranslation": raw}

            def getKey(self, key: str):
                return self.keyValues.get(key)

        order = _resolve_after_translation_order(_Cfg(["improve", "tonecheck"]))
        self.assertEqual(order, ["improve", "tonecheck"])


class ToneCheckEchoTests(unittest.TestCase):
    def test_threshold_matches_semcheck(self) -> None:
        obj = object.__new__(ForToneCheck)
        obj._log_tag = "[色彩检查]"
        self.assertEqual(obj._echo_hit_ratio, 0.6)
        self.assertTrue(obj._is_echo_response(24, 40))
        self.assertFalse(obj._is_echo_response(23, 40))


if __name__ == "__main__":
    unittest.main()
