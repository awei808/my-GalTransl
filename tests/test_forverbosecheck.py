# -*- coding: utf-8 -*-
"""ForVerboseCheck 节奏冗长检测的单元测试。

覆盖：
  - _parse_fix_response：按 id 稀疏解析（id/可选 reason），命中句置 verbose_issue（占位 reason 归一为 "1"）
  - find_problems 认领：verbose_issue 非空 → 输出「节奏冗长」（reason 附带展示；半角逗号归一）
  - batch_translate：禁用降级跳过（保留旧标记）；启用时清旧标记（幂等）并写新标记
  - 分批：优先 gpt.numPerRequestVerboseCheck，回退 numPerRequestSemCheck / numPerRequestBetter / 实参
  - 整批回显：追加纠正提示重试一次，重试仍回显则丢弃本批标记并记运行态错误
  - user 提示词：仅注入任务说明、文件级元数据与批次 input，不注入术语表/历史/规范
  - Cache._build_cache_obj：verbose_issue 随快照落盘
  - 注册链：ENGINE_MODULE_PATHS / ENGINE_REGISTRY / NEED_OpenAITokenPool / afterTranslation 白名单 / 独立执行规格
"""
import asyncio
import unittest
from unittest.mock import Mock, patch

from GalTransl.CSentense import CSentense
from GalTransl.Cache import _build_cache_obj
from GalTransl.Problem import find_problems
from GalTransl.ConfigHelper import CProblemType
from GalTransl.Backend.Prompts import FORGAL_JSON_VERBOSECHECK_PROMPT
from GalTransl.Backend.ForVerboseCheck import ForVerboseCheck
from GalTransl.Backend.ForVerboseCheckAgain import ForVerboseCheckAgain


class _VerboseOnlyPjConfig:
    """find_problems 配置替身：仅启用「节奏冗长」检测项。"""

    def getProblemAnalyzeArinashiDict(self) -> dict:
        return {}

    def getProblemAnalyzeConfig(self, key: str) -> list:
        return [CProblemType.节奏冗长] if key == "problemList" else []

    def hasProblemAnalyzeConfig(self, key: str) -> bool:
        return True

    def getKey(self, key: str):
        return None


def _make_parser() -> ForVerboseCheck:
    """绕过重型 __init__，仅装配解析与筛选所需属性。"""
    obj = object.__new__(ForVerboseCheck)
    obj._log_tag = "[冗长检测]"
    obj._disabled_reason = ""
    obj.pj_config = _VerboseOnlyPjConfig()
    return obj


def _trans(index: int, pre_dst: str = "译文", post_src: str = "原文") -> CSentense:
    t = CSentense(f"src{index}", index=index)
    t.post_src = post_src
    t.pre_dst = pre_dst
    t.proofread_zh = ""
    t.verbose_issue = ""
    return t


def problem_types_of(problem: str) -> list:
    """problem 的类型名列表：与前端 problemTypesOf 同一切分口径。"""
    return [
        seg.strip().split("：")[0].split(":")[0]
        for seg in problem.split(",")
        if seg.strip()
    ]


class ParseVerboseJsonlineTests(unittest.TestCase):
    def test_hit_sets_verbose_issue_default_one(self) -> None:
        parser = _make_parser()
        trans = _trans(3)
        parser._parse_fix_response('tma|{"id": 3}', [trans], "\r\n")
        self.assertEqual(trans.verbose_issue, "1")

    def test_hit_keeps_specific_reason(self) -> None:
        parser = _make_parser()
        trans = _trans(7)
        parser._parse_fix_response(
            'ab1|{"id": 7, "reason": "同义堆叠：非常非常"}', [trans], "\r\n"
        )
        self.assertEqual(trans.verbose_issue, "同义堆叠：非常非常")

    def test_placeholder_reason_normalized_to_one(self) -> None:
        # 提示词禁止只写类型名，模型仍可能照抄 → 归一为哨兵 "1"
        parser = _make_parser()
        trans = _trans(4)
        parser._parse_fix_response('1mj|{"id": 4, "reason": "节奏冗长"}', [trans], "\r\n")
        self.assertEqual(trans.verbose_issue, "1")

    def test_reason_halfwidth_commas_normalized(self) -> None:
        parser = _make_parser()
        trans = _trans(9)
        parser._parse_fix_response(
            'cd2|{"id": 9, "reason": "垫词过多：的话呢,也就是说"}', [trans], "\r\n"
        )
        self.assertEqual(trans.verbose_issue, "垫词过多：的话呢，也就是说")

    def test_unknown_id_and_garbage_tolerated(self) -> None:
        parser = _make_parser()
        trans = _trans(5)
        parser._parse_fix_response(
            '先思考一下…\nab1|{"id": 99}\nzz9|{"id": 5} 尾巴乱码', [trans], "\r\n"
        )
        self.assertEqual(trans.verbose_issue, "1")


class VerboseClaimTests(unittest.TestCase):
    def test_reason_appended_in_problem(self) -> None:
        trans = _trans(1)
        trans.verbose_issue = "同义堆叠：非常非常"
        find_problems([trans], _VerboseOnlyPjConfig())
        self.assertEqual(trans.problem, "节奏冗长：同义堆叠：非常非常")
        self.assertEqual(problem_types_of(trans.problem), ["节奏冗长"])

    def test_sentinel_claimed_plain(self) -> None:
        trans = _trans(2)
        trans.verbose_issue = "1"
        find_problems([trans], _VerboseOnlyPjConfig())
        self.assertEqual(trans.problem, "节奏冗长")

    def test_type_disabled_does_not_claim(self) -> None:
        class _NoTypes(_VerboseOnlyPjConfig):
            def getProblemAnalyzeConfig(self, key: str) -> list:
                return []

        trans = _trans(3)
        trans.verbose_issue = "重复陈述同一句"
        find_problems([trans], _NoTypes())
        self.assertEqual(trans.problem, "")

    def test_non_string_marker_claimed_plain(self) -> None:
        # 手改缓存写入 null/数字：不拼出「节奏冗长：None」，只标类型名
        trans = _trans(4)
        trans.verbose_issue = None
        find_problems([trans], _VerboseOnlyPjConfig())
        self.assertEqual(trans.problem, "节奏冗长")


class FixSpecTests(unittest.TestCase):
    def test_claimed_type_has_fix_spec(self) -> None:
        # 认领即必须注册修复指令：否则统一修复勾选后既无指令也拿不到原文
        from GalTransl.Backend.ForFixRound import _FIX_SPECS, MODE_SRC_DST

        spec = _FIX_SPECS.get(CProblemType.节奏冗长)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.mode, MODE_SRC_DST)
        self.assertIn("同义重复", spec.instruction)


class CacheRoundTripTests(unittest.TestCase):
    def test_verbose_issue_persisted(self) -> None:
        trans = _trans(1)
        trans.verbose_issue = "同义堆叠"
        cache_obj = _build_cache_obj(trans)
        self.assertEqual(cache_obj["verbose_issue"], "同义堆叠")

    def test_empty_marker_not_persisted(self) -> None:
        cache_obj = _build_cache_obj(_trans(2))
        self.assertNotIn("verbose_issue", cache_obj)


class _FakeBatchConfig:
    """分批配置替身：可控 getKey 返回值。"""

    active_workers = 1

    def __init__(self, values: dict) -> None:
        self._values = values

    def getKey(self, key: str):
        return self._values.get(key)


class _VerboseCheckTestBase:
    """装配 batch_translate 测试替身的公共方法。"""

    def _make_obj(self, values: dict, llm_resp: str = "", metadata_block: str = ""):
        obj = object.__new__(ForVerboseCheck)
        obj._log_tag = "[冗长检测]"
        obj._disabled_reason = ""
        obj.pj_config = _FakeBatchConfig(values)
        obj.system_prompt = "system"
        obj.trans_prompt = FORGAL_JSON_VERBOSECHECK_PROMPT
        obj.target_lang = "Simplified_Chinese"
        obj.eng_type = "ForVerboseCheck"
        obj._recorded_errors = []
        obj._llm_calls = []
        obj._resolve_file_metadata = Mock(
            return_value=object() if metadata_block else None
        )
        obj._format_file_metadata_block = Mock(return_value=metadata_block)
        obj._record_round_runtime_error = (
            lambda filename, idx_tip, message, model: obj._recorded_errors.append(
                (filename, idx_tip, message, model)
            )
        )

        async def fake_llm(messages, filename, idx_tip, cb):
            obj._llm_calls.append(messages)
            return llm_resp, None

        obj._call_llm = fake_llm
        return obj


class BatchTranslateTests(unittest.IsolatedAsyncioTestCase, _VerboseCheckTestBase):
    async def test_disabled_skips_and_keeps_old_mark(self) -> None:
        obj = object.__new__(ForVerboseCheck)
        obj._log_tag = "[冗长检测]"
        obj._disabled_reason = "主翻译令牌池无可用 token"
        trans = _trans(1)
        trans.verbose_issue = "旧标记"
        trans_list = [trans]
        with patch.object(ForVerboseCheck, "_call_llm") as mock_llm:
            result = await obj.batch_translate("f.json", "c.json", trans_list, 20)
        mock_llm.assert_not_awaited()
        self.assertIs(result, trans_list)
        self.assertEqual(trans.verbose_issue, "旧标记")

    async def test_marks_written_and_cleared_idempotently(self) -> None:
        obj = self._make_obj({}, llm_resp='ab1|{"id": 2, "reason": "重复陈述同一句"}')
        trans_list = [_trans(i) for i in range(1, 4)]
        trans_list[0].verbose_issue = "旧标记"
        with patch.object(
            ForVerboseCheck, "_filter_target_translations", return_value=trans_list
        ):
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        self.assertEqual(trans_list[0].verbose_issue, "")
        self.assertEqual(trans_list[1].verbose_issue, "重复陈述同一句")
        self.assertEqual(trans_list[2].verbose_issue, "")

    async def test_prompt_injects_task_metadata_and_input_only(self) -> None:
        obj = self._make_obj(
            {},
            llm_resp='ab1|{"id": 1, "reason": "垫词过多"}',
            metadata_block="\n<plot_metadata>\n角色: 創\n</plot_metadata>\n",
        )
        trans_list = [_trans(1)]
        with patch.object(
            ForVerboseCheck, "_filter_target_translations", return_value=trans_list
        ):
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        user = obj._llm_calls[0][-1]["content"]
        self.assertIn("### 任务", user)
        self.assertIn("角色: 創", user)
        self.assertIn('"id": 1', user)
        self.assertIn('"src": "原文"', user)
        self.assertIn("Simplified_Chinese", user)
        self.assertNotIn("[plot_metadata]", user)
        self.assertNotIn("[TargetLang]", user)
        self.assertNotIn("[Input]", user)
        self.assertNotIn("[Glossary]", user)
        self.assertNotIn("<batch_metadata>", user)
        self.assertNotIn("[history_result]", user)

    async def test_echo_response_retried_once_then_discarded(self) -> None:
        # 6 句整批回显（命中数达 _echo_min_hits）：首轮丢弃并追加纠正提示重试，
        # 重试仍回显 → 标记清空 + 记错误
        resp = "\n".join(f'ab{i}|{{"id": {i}, "reason": "冗长"}}' for i in range(1, 7))
        obj = self._make_obj({}, llm_resp=resp)
        trans_list = [_trans(i) for i in range(1, 7)]
        with patch.object(
            ForVerboseCheck, "_filter_target_translations", return_value=trans_list
        ):
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        self.assertEqual(len(obj._llm_calls), 2)
        self.assertIn("整批回显", obj._llm_calls[1][-1]["content"])
        self.assertTrue(all(t.verbose_issue == "" for t in trans_list))
        self.assertIn("重试仍疑似整批回显", obj._recorded_errors[0][2])

    async def test_llm_failure_records_error_and_leaves_batch_unmarked(self) -> None:
        # 首轮进入即清旧标记，失败批次必然为空；「失败保留标记」的 fail-safe 语义属复核轮
        obj = self._make_obj({})
        trans_list = [_trans(1)]

        async def boom(messages, filename, idx_tip, cb):
            obj._llm_calls.append(messages)
            raise RuntimeError("boom")

        obj._call_llm = boom
        with patch.object(
            ForVerboseCheck, "_filter_target_translations", return_value=trans_list
        ):
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        self.assertIn("boom", obj._recorded_errors[0][2])
        self.assertEqual(trans_list[0].verbose_issue, "")


class BatchSplitTests(unittest.IsolatedAsyncioTestCase, _VerboseCheckTestBase):
    """验证分批优先级：numPerRequestVerboseCheck > numPerRequestSemCheck > numPerRequestBetter > 实参。"""

    async def _run_split(self, values: dict, num_arg: int = 20, total: int = 5) -> list:
        obj = self._make_obj(values, llm_resp="")
        targets = [_trans(i) for i in range(1, total + 1)]
        with patch.object(
            ForVerboseCheck, "_filter_target_translations", return_value=targets
        ):
            await obj.batch_translate("f.json", "c.json", targets, num_arg)
        return [len(call[-1]["content"]) for call in obj._llm_calls]

    async def test_uses_verbose_check_key_first(self) -> None:
        sizes = await self._run_split(
            {
                "gpt.numPerRequestVerboseCheck": 2,
                "gpt.numPerRequestSemCheck": 3,
                "gpt.numPerRequestBetter": 4,
            }
        )
        self.assertEqual(len(sizes), 3)  # 5 句 / 2 = 3 批

    async def test_falls_back_to_sem_check_key(self) -> None:
        sizes = await self._run_split(
            {"gpt.numPerRequestSemCheck": 3, "gpt.numPerRequestBetter": 4}
        )
        self.assertEqual(len(sizes), 2)  # 5 句 / 3 = 2 批

    async def test_falls_back_to_better_key(self) -> None:
        sizes = await self._run_split({"gpt.numPerRequestBetter": 5})
        self.assertEqual(len(sizes), 1)  # 5 句 / 5 = 1 批

    async def test_falls_back_to_argument(self) -> None:
        sizes = await self._run_split({}, num_arg=5)
        self.assertEqual(len(sizes), 1)


class RegistrationTests(unittest.TestCase):
    def test_engine_module_and_registry(self) -> None:
        from GalTransl.Backend.BaseEngine import ENGINE_MODULE_PATHS, ENGINE_REGISTRY

        self.assertEqual(
            ENGINE_MODULE_PATHS["ForVerboseCheck"], "GalTransl.Backend.ForVerboseCheck"
        )
        self.assertEqual(
            ENGINE_MODULE_PATHS["ForVerboseCheckAgain"],
            "GalTransl.Backend.ForVerboseCheckAgain",
        )
        # 注册值为惰性构造工厂（非类本身），此处只锁登记存在与可调用
        for name in ("ForVerboseCheck", "ForVerboseCheckAgain"):
            self.assertIn(name, ENGINE_REGISTRY)
            self.assertTrue(callable(ENGINE_REGISTRY[name]))

    def test_token_pool_and_standalone_and_after_translation(self) -> None:
        from GalTransl import NEED_OpenAITokenPool, TRANSLATOR_SUPPORTED
        from GalTransl.Frontend.llm_standalone import STANDALONE_BACKENDS
        from GalTransl.Frontend.llm_postprocess import _resolve_after_translation_order

        self.assertIn("ForVerboseCheck", NEED_OpenAITokenPool)
        self.assertIn("ForVerboseCheckAgain", NEED_OpenAITokenPool)
        self.assertIn("ForVerboseCheck", TRANSLATOR_SUPPORTED)
        self.assertEqual(STANDALONE_BACKENDS["ForVerboseCheck"].log_tag, "[冗长检测]")
        self.assertEqual(STANDALONE_BACKENDS["ForVerboseCheckAgain"].log_tag, "[冗长复核]")

        class _Cfg:
            def getKey(self, key: str):
                if key == "gpt.afterTranslation":
                    return ["verbosecheck", "verbosecheckagain"]
                return None

        self.assertEqual(
            _resolve_after_translation_order(_Cfg()),
            ["verbosecheck", "verbosecheckagain"],
        )


if __name__ == "__main__":
    unittest.main()
