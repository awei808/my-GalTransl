# -*- coding: utf-8 -*-
"""BaseFixRound 稀疏修复轮基类单元测试。

覆盖（ForJPResidue / ForBRStation / ForImproveTranslation 共享的基类逻辑）：
  - _has_target_problem：问题白名单 + 译文有效性 + skip_check 的统一筛选口径
  - _effective_problem_types：_inject_problem 开关决定是否注入 problem
  - _filter_target_translations：Problem 轮按问题筛选 / Improve 轮按有效译文筛选
  - swapFixToCurrent：修复结果覆盖当前译文、原译文存入 alt_dst
  - batch_translate 集成：筛选 → 独立分桶 → 首轮/续轮 → 稀疏解析 → alt_dst 写入
  - LLM 调用失败：记录错误并跳过本批，不中断整体
"""
import asyncio
import unittest
from types import MethodType, SimpleNamespace

from GalTransl.CSentense import CSentense
from GalTransl.Backend.ForJPResidue import ForJPResidue
from GalTransl.Backend.ForBRStation import ForBRStation
from GalTransl.Backend.ForImproveTranslation import ForImproveTranslation


class _FakeOpencc:
    """身份转换替身：_normalize_parsed_translation_text 在中文目标语言下调用 convert。"""

    def convert(self, text):
        return text


def make_translator(cls=ForJPResidue):
    """通过 __new__ 打桩稀疏修复轮所需属性，避免重量级初始化。"""
    t = cls.__new__(cls)
    t.pj_config = SimpleNamespace(
        active_workers=0,
        stop_event=None,
        translation_guideline="",
        getProjectDir=lambda: "",
        getKey=lambda key, default=None: default,
    )
    t.eng_type = cls.__name__
    t.system_prompt = "SYSTEM_PROMPT"
    t.trans_prompt = "[translation_guideline]\n[Glossary]\n[plot_metadata]\n[Input]"
    t.source_lang = "Japanese"
    t.target_lang = "Simplified Chinese"
    t.conversations = {}
    t._force_first_round_files = set()
    t.file_metadata_map = {}
    t._file_metadata_by_file = {}
    t._file_metadata_loaded = False
    t._global_prompt = None
    t._global_prompt_loaded = False
    t.opencc = _FakeOpencc()
    t.last_translations = {}
    t._captured_input = ""

    def _fake_first_round(self, input_src, gptdict, filename):
        self._captured_input = input_src
        return "FIRST_ROUND_CONTENT\n" + input_src

    t._build_first_round_content = MethodType(_fake_first_round, t)
    t._resolve_file_metadata = MethodType(lambda self, *a, **k: None, t)
    t._format_file_metadata_block = MethodType(lambda self, *a, **k: "", t)
    t._format_global_prompt_block = MethodType(lambda self, *a, **k: "", t)
    t._apply_history_result = MethodType(lambda self, p, *a, **k: p, t)
    t._trim_conversation = MethodType(lambda self, conv, *a, **k: conv, t)
    t._build_idx_tip = MethodType(lambda self, lst, *a, **k: "1~2", t)
    t._record_round_runtime_error = MethodType(lambda self, *a, **k: None, t)
    t.get_last_chatbot_model = MethodType(lambda self, *a, **k: "m", t)
    return t


def make_tran(index, problem, pre_dst, post_src="src", skip_check=False):
    t = CSentense(post_src, index=index)
    t.post_src = post_src
    t.pre_dst = pre_dst
    t.problem = problem
    t.skip_check = skip_check
    t.alt_dst = ""
    t.proofread_zh = ""
    return t


class HasTargetProblemTests(unittest.TestCase):
    """_has_target_problem：按问题白名单 + 译文有效性统一筛选。"""

    def test_hits_matching_problem_type(self) -> None:
        t = make_translator(ForJPResidue)
        tran = make_tran(1, "残留日文：です", "译文")
        self.assertTrue(t._has_target_problem(tran))

    def test_misses_other_problem_type(self) -> None:
        t = make_translator(ForJPResidue)
        tran = make_tran(1, "换行位置异常：第1行", "译文")
        self.assertFalse(t._has_target_problem(tran))

    def test_misses_no_problem(self) -> None:
        t = make_translator(ForJPResidue)
        tran = make_tran(1, "", "译文")
        self.assertFalse(t._has_target_problem(tran))

    def test_misses_empty_dst(self) -> None:
        t = make_translator(ForJPResidue)
        tran = make_tran(1, "残留日文：です", "")
        self.assertFalse(t._has_target_problem(tran))

    def test_misses_failed_dst(self) -> None:
        t = make_translator(ForJPResidue)
        tran = make_tran(1, "残留日文：です", "(Failed) 译文")
        self.assertFalse(t._has_target_problem(tran))

    def test_misses_skip_check(self) -> None:
        t = make_translator(ForJPResidue)
        tran = make_tran(1, "残留日文：です", "译文", skip_check=True)
        self.assertFalse(t._has_target_problem(tran))


class EffectiveProblemTypesTests(unittest.TestCase):
    """_effective_problem_types：是否注入 problem 由 _inject_problem 决定。"""

    def test_jp_residue_does_not_inject(self) -> None:
        t = make_translator(ForJPResidue)
        self.assertIsNone(t._effective_problem_types())

    def test_br_station_injects_its_problem_types(self) -> None:
        from GalTransl.ConfigHelper import CProblemType

        t = make_translator(ForBRStation)
        self.assertEqual(t._effective_problem_types(), [CProblemType.换行位置异常])

    def test_improve_injects_when_enabled(self) -> None:
        from GalTransl.ConfigHelper import CProblemType

        t = make_translator(ForImproveTranslation)
        t.pj_config.getKey = (
            lambda key, default=None: "残留日文, 用词不当"
            if key == "gpt.problemInjectTypes"
            else True
        )
        types = t._effective_problem_types()
        self.assertEqual([p.name for p in types], ["残留日文", "用词不当"])

    def test_improve_no_inject_when_disabled(self) -> None:
        t = make_translator(ForImproveTranslation)
        self.assertIsNone(t._effective_problem_types())


class FilterTargetTranslationsTests(unittest.TestCase):
    """Problem 轮按问题筛选；Improve 轮按有效译文筛选（不依赖 problem）。"""

    def test_problem_round_filters_by_problem(self) -> None:
        t = make_translator(ForJPResidue)
        trans_list = [
            make_tran(1, "残留日文：です", "译1"),
            make_tran(2, "换行位置异常：第1行", "译2"),
            make_tran(3, "残留日文：です", "译3", skip_check=True),
        ]
        kept = t._filter_target_translations(trans_list)
        self.assertEqual([tr.index for tr in kept], [1])

    def test_improve_round_filters_all_valid(self) -> None:
        t = make_translator(ForImproveTranslation)
        trans_list = [
            make_tran(1, "", "译1"),
            make_tran(2, "残留日文：です", "译2"),
            make_tran(3, "", "译3", skip_check=True),
            make_tran(4, "", ""),
            make_tran(5, "", "(Failed) 译5"),
        ]
        kept = t._filter_target_translations(trans_list)
        self.assertEqual([tr.index for tr in kept], [1, 2])


class SwapFixToCurrentTests(unittest.TestCase):
    """swapFixToCurrent 开启时：修复结果覆盖当前译文，原译文存入 alt_dst。"""

    def test_swap_overwrites_pre_dst(self) -> None:
        t = make_translator(ForJPResidue)
        t.pj_config.getKey = lambda key, default=None: True
        tran = make_tran(1, "残留日文：です", "旧译文")
        ok = t._apply_better_result(tran, "旧译文", "新译文", 1)
        self.assertTrue(ok)
        self.assertEqual(tran.pre_dst, "新译文")
        self.assertEqual(tran.alt_dst, "旧译文")

    def test_swap_prefers_proofread_zh(self) -> None:
        t = make_translator(ForJPResidue)
        t.pj_config.getKey = lambda key, default=None: True
        tran = make_tran(1, "残留日文：です", "初译")
        tran.proofread_zh = "校对"
        t._apply_better_result(tran, "校对", "新译文", 1)
        self.assertEqual(tran.proofread_zh, "新译文")
        self.assertEqual(tran.pre_dst, "初译")
        self.assertEqual(tran.alt_dst, "校对")

    def test_no_swap_writes_alt_dst_only(self) -> None:
        t = make_translator(ForJPResidue)
        tran = make_tran(1, "残留日文：です", "旧译文")
        t._apply_better_result(tran, "旧译文", "新译文", 1)
        self.assertEqual(tran.alt_dst, "新译文")
        self.assertEqual(tran.pre_dst, "旧译文")


class BatchTranslateIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """batch_translate 集成：筛选 → 独立分桶 → 首轮/续轮 → 稀疏解析 → alt_dst。"""

    async def _run(self, t, trans_list, llm_resp, num_per_request=100):
        called = []

        async def fake_call_llm(self, messages, filename, idx_tip, *_a, **_k):
            called.append(messages)
            return llm_resp, SimpleNamespace(model_name="m", domain="x")

        t._call_llm = MethodType(fake_call_llm, t)
        await t.batch_translate("f.json", "f.json", trans_list, num_per_request, gpt_dic=None)
        return called

    async def test_filters_and_writes_alt_dst(self) -> None:
        t = make_translator(ForJPResidue)
        trans_list = [
            make_tran(1, "残留日文：です", "译1"),
            make_tran(2, "词频过高", "译2"),
        ]
        called = await self._run(
            t,
            trans_list,
            'a1b|{"id": 1, "better": "修复译1"}',
        )
        self.assertEqual(len(called), 1)
        self.assertEqual(trans_list[0].alt_dst, "修复译1")
        self.assertEqual(trans_list[1].alt_dst, "")  # 非目标句不处理

    async def test_first_round_uses_dedicated_prompt(self) -> None:
        t = make_translator(ForJPResidue)
        trans_list = [make_tran(1, "残留日文：です", "译1")]
        called = await self._run(t, trans_list, "")
        user_content = called[0][-1]["content"]
        self.assertTrue(user_content.startswith("FIRST_ROUND_CONTENT"))

    async def test_second_batch_uses_plain_input(self) -> None:
        t = make_translator(ForJPResidue)
        trans_list = [
            make_tran(1, "残留日文：です", "译1"),
            make_tran(2, "残留日文：です", "译2"),
        ]
        called = await self._run(t, trans_list, "", num_per_request=1)
        self.assertEqual(len(called), 2)
        second_user = called[1][-1]["content"]
        self.assertNotIn("FIRST_ROUND_CONTENT", second_user)

    async def test_llm_failure_skips_batch_and_continues(self) -> None:
        t = make_translator(ForJPResidue)
        trans_list = [
            make_tran(1, "残留日文：です", "译1"),
            make_tran(2, "残留日文：です", "译2"),
        ]
        calls = {"count": 0}

        async def fake_call_llm(self, messages, filename, idx_tip, *_a, **_k):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("boom")
            return 'b2c|{"id": 2, "better": "修复译2"}', SimpleNamespace(
                model_name="m", domain="x"
            )

        t._call_llm = MethodType(fake_call_llm, t)
        await t.batch_translate("f.json", "f.json", trans_list, 1, gpt_dic=None)
        self.assertEqual(calls["count"], 2)  # 第一批失败后第二批继续
        self.assertEqual(trans_list[1].alt_dst, "修复译2")

    async def test_zero_targets_skips_llm(self) -> None:
        t = make_translator(ForJPResidue)
        trans_list = [make_tran(1, "词频过高", "译1")]
        calls = {"flag": False}

        async def fail_if_called(self, *a, **_k):
            calls["flag"] = True
            raise AssertionError("无可处理句子时应提前返回")

        t._call_llm = MethodType(fail_if_called, t)
        await t.batch_translate("f.json", "f.json", trans_list, 100, gpt_dic=None)
        self.assertFalse(calls["flag"])

    async def test_empty_code_block_is_not_format_error(self) -> None:
        # 提示词约定「若整批无需修复，输出空代码块即可」：空代码块 = 无命中，
        # 不得触发 _warn_on_zero_found 告警（残缺/同行空块均归一为空响应）
        t = make_translator(ForJPResidue)
        recorded = []
        t._record_round_runtime_error = MethodType(
            lambda self, *a, **k: recorded.append((a, k)), t
        )
        trans_list = [make_tran(1, "残留日文：です", "译1")]
        for resp in ('```jsonline\n\n```', '```jsonline\n```', '```jsonline ```'):
            with self.subTest(resp=resp):
                recorded.clear()
                await self._run(t, trans_list, resp)
                self.assertEqual(recorded, [])

    async def test_actual_text_with_zero_hits_still_warns(self) -> None:
        # 模型输出了实质内容但 0 命中（如纯解释文字）：格式异常，仍告警
        t = make_translator(ForJPResidue)
        recorded = []
        t._record_round_runtime_error = MethodType(
            lambda self, *a, **k: recorded.append((a, k)), t
        )
        trans_list = [make_tran(1, "残留日文：です", "译1")]
        await self._run(t, trans_list, "所有句子都无需修复，不需要输出任何内容")
        self.assertEqual(len(recorded), 1)

    async def test_echo_exact_match_before_normalization(self) -> None:
        # P1 回归：opencc 简繁转换会改写中日同形汉字（後で→后で），
        # 导致归一化后 normalized != post_src 而绕过回显过滤；
        # 归一化前的精确比较必须命中并跳过
        t = make_translator(ForJPResidue)
        _orig_opencc = t.opencc

        class _CnOpencc:
            def convert(self, text):
                return text.replace("後で", "后で").replace("時計", "时计")

        t.opencc = _CnOpencc()
        try:
            # post_src 与 better 完全相同（整批回显原文），opencc 会改写 better
            trans_list = [
                make_tran(1, "後で", "译1"),
                make_tran(2, "時計", "译2"),
            ]
            resp = (
                'a1b|{"id": 1, "better": "後で"}\n'
                'c2d|{"id": 2, "better": "時計"}'
            )
            await self._run(t, trans_list, resp)
            # 两句均被精确回显过滤拦截，alt_dst 不写入
            self.assertEqual(trans_list[0].alt_dst, "")
            self.assertEqual(trans_list[1].alt_dst, "")
        finally:
            t.opencc = _orig_opencc

    async def test_batch_echo_degradation_resets_conversation(self) -> None:
        # P2+P3：整批回显（found_count > 0 且 success_count == 0）时，
        # 应告警并 reset_conversation，回显响应不进入对话历史
        # better 含换行符，精确比较不命中（found_count 递增），
        # 但归一化后等于 post_src，被归一化后回显过滤拦截（success_count 不递增）
        t = make_translator(ForJPResidue)
        recorded = []
        t._record_round_runtime_error = MethodType(
            lambda self, *a, **k: recorded.append((a, k)), t
        )
        # post_src 含真实换行符，n_symbol 检测为 \n；
        # better 含 <br>，精确比较不命中（found_count 递增），
        # 归一化后 <br>→\n，等于 post_src，被回显过滤拦截（success_count 不递增）
        trans_list = [
            make_tran(1, "残留日文：です", "译1", post_src="src\n"),
            make_tran(2, "残留日文：です", "译2", post_src="src\n"),
        ]
        resp = (
            'a1b|{"id": 1, "better": "src<br>"}\n'
            'c2d|{"id": 2, "better": "src<br>"}'
        )
        await self._run(t, trans_list, resp)
        # 触发退化告警
        self.assertEqual(len(recorded), 1)
        self.assertIn("整批回显", recorded[0][0][2])
        # 对话已重置（key 被移除）
        self.assertNotIn("f.json", t.conversations)
        # alt_dst 未被污染
        self.assertEqual(trans_list[0].alt_dst, "")
        self.assertEqual(trans_list[1].alt_dst, "")


class EchoThresholdTests(unittest.TestCase):
    """_is_echo_response（保留供 ForSemCheck 等独立回显实现使用）阈值行为回归。"""

    def test_threshold_attribute_effective(self) -> None:
        # 回归：阈值必须读类属性（子类可覆盖），而非硬编码 0.9
        t = make_translator(ForJPResidue)
        self.assertTrue(t._is_echo_response(36, 40))  # 默认 0.9
        self.assertFalse(t._is_echo_response(35, 40))
        t._echo_hit_ratio = 1.0
        self.assertTrue(t._is_echo_response(40, 40))
        self.assertFalse(t._is_echo_response(36, 40))
        t._echo_hit_ratio = 0.5
        self.assertTrue(t._is_echo_response(20, 40))


if __name__ == "__main__":
    unittest.main()
