# -*- coding: utf-8 -*-
"""ForFixRound 统一问题修复后端单元测试。

覆盖：
  - build_fix_instructions：按白名单装配指令；换行位置异常时携带 [br_issue_guide]/[allowed_break_ends] 占位符
  - 组合筛选：一句话命中白名单任一类型即入轮（多类型组合）
  - 模式差异：_include_src=False（模式 B）时输入 JSONL 不携带 src 字段
  - set_fix_params：参数注入重建提示词、切换模式
  - _apply_extra_first_round_replacements：仅白名单含换行位置异常时注入 br_issue_guide；动态填充 allowed_break_ends 与平均分句长度阈值（取 problemAnalyze 配置值）
  - __init__ 白名单回退：未指定类型时回退 problemAnalyze.problemList 全部类型
  - 模式 B 不注入术语表：_build_batch_gptdict 返回空串
"""
import asyncio
import os
import sys
import tempfile
import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from GalTransl.CSentense import CSentense
from GalTransl.ConfigHelper import CProblemType, CProjectConfig
from GalTransl.Backend.BaseEngine import BaseEngine
from GalTransl.Backend.Prompts import FORGAL_JSON_BRSTATION_PROMPT
from GalTransl.Backend.ForFixRound import (
    ForProblemFixRound,
    build_allowed_break_ends_desc,
    build_br_issue_guide,
    build_fix_instructions,
    build_sentence_len_threshold_desc,
)


class _FakeOpencc:
    @staticmethod
    def convert(text):
        return text


def make_translator(cls=ForProblemFixRound, problem_types=None):
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
    t._problem_types = list(problem_types or [])
    t._include_src = True
    t._inject_problem = True
    t._log_tag = "[问题修复]"

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


def _make_config(tmp_dir: str) -> CProjectConfig:
    cfg_path = os.path.join(tmp_dir, "config.yaml")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(
            "internals:\n"
            "  gpt:\n"
            "    numPerRequestBetter: 20\n"
            "    swapFixToCurrent: false\n"
            "externals:\n"
            "  sourceLang: ja\n"
            "  targetLang: zh\n"
            "backendSpecific:\n"
            "  OpenAI-Compatible:\n"
            "    apiTimeout: 300\n"
            "common:\n"
            "  gpt:\n"
            "    change_prompt: no\n"
        )
    return CProjectConfig(tmp_dir, "config.yaml")


def _make_config_with_thresholds(
    tmp_dir: str, avg_len: int, avg_len_h: int
) -> CProjectConfig:
    """在基础配置上注入 problemAnalyze 两档阈值，验证提示词取的是配置实际值。"""
    config = _make_config(tmp_dir)
    config.projectConfig["problemAnalyze"] = {
        "avgSentenceLengthThreshold": avg_len,
        "avgSentenceLengthThresholdH": avg_len_h,
    }
    return config


class BuildInstructionsTests(unittest.TestCase):
    def test_single_type_instructions(self) -> None:
        text = build_fix_instructions([CProblemType.残留日文])
        self.assertIn("残留日文", text)
        self.assertIn("对照 src", text)

    def test_br_type_carries_placeholder(self) -> None:
        text = build_fix_instructions([CProblemType.换行位置异常])
        self.assertIn("[br_issue_guide]", text)

    def test_non_br_type_no_placeholder(self) -> None:
        text = build_fix_instructions([CProblemType.残留日文, CProblemType.用词不当])
        self.assertNotIn("[br_issue_guide]", text)

    def test_combined_types_joined(self) -> None:
        text = build_fix_instructions([CProblemType.残留日文, CProblemType.用词不当])
        self.assertIn("残留日文", text)
        self.assertIn("用词不当", text)

    def test_empty_types_returns_fallback(self) -> None:
        self.assertEqual(
            build_fix_instructions([]),
            "（未配置具体修复指令，仅对照 problem 标注修复对应问题）",
        )

    def test_suspected_error_type_has_instruction(self) -> None:
        text = build_fix_instructions([CProblemType.疑似错误])
        self.assertIn("疑似错误", text)
        self.assertIn("保留原译文", text)


class CombinedFilterTests(unittest.TestCase):
    def test_hits_any_whitelist_type(self) -> None:
        t = make_translator(
            problem_types=[CProblemType.残留日文, CProblemType.用词不当]
        )
        trans_list = [
            make_tran(1, "残留日文：です", "译1"),
            make_tran(2, "用词不当：模型师", "译2"),
            make_tran(3, "换行位置异常：第1行", "译3"),
            make_tran(4, "", "译4"),
        ]
        kept = t._filter_target_translations(trans_list)
        self.assertEqual([tr.index for tr in kept], [1, 2])

    def test_problem_injection_filtered_by_whitelist(self) -> None:
        backend = object.__new__(ForProblemFixRound)
        backend.pj_config = _make_config(tempfile.mkdtemp(prefix="fixr_"))
        backend.gpt_dic = None
        tran = make_tran(1, "残留日文：です, 换行位置异常：第1行", "译1")
        _input_list, _sig_list, _n_symbol, input_src = backend._build_input_jsonlines(
            [tran],
            proofread=True,
            filename="dummy.txt",
            problem_types=[CProblemType.残留日文],
            include_src=True,
        )
        self.assertIn("残留日文：です", input_src)
        self.assertNotIn("换行位置异常", input_src)


class ModeTests(unittest.TestCase):
    def _backend(self, include_src: bool) -> ForProblemFixRound:
        backend = object.__new__(ForProblemFixRound)
        backend.pj_config = _make_config(tempfile.mkdtemp(prefix="fixr_"))
        backend.gpt_dic = None
        backend._include_src = include_src
        return backend

    def test_mode_a_includes_src(self) -> None:
        backend = self._backend(include_src=True)
        tran = make_tran(1, "残留日文：です", "译1")
        _l, _s, _n, input_src = backend._build_input_jsonlines(
            [tran], proofread=True, filename="dummy.txt",
            problem_types=[CProblemType.残留日文], include_src=True,
        )
        self.assertIn('"src"', input_src)
        self.assertIn('"dst"', input_src)
        self.assertIn('"problem"', input_src)

    def test_mode_b_omits_src(self) -> None:
        backend = self._backend(include_src=False)
        tran = make_tran(1, "换行位置异常：第1行", "译1")
        _l, _s, _n, input_src = backend._build_input_jsonlines(
            [tran], proofread=True, filename="dummy.txt",
            problem_types=[CProblemType.换行位置异常], include_src=False,
        )
        self.assertNotIn('"src"', input_src)
        self.assertIn('"dst"', input_src)
        self.assertIn('"problem"', input_src)


class SetFixParamsTests(unittest.TestCase):
    def _backend(self) -> ForProblemFixRound:
        t = object.__new__(ForProblemFixRound)
        t.pj_config = SimpleNamespace(getKey=lambda key, default=None: default)
        t._log_tag = "[问题修复]"
        # 绕过 change_prompt 重放（需完整 CProjectConfig），本组测试仅验证参数注入
        t._finalize_prompts = lambda: None
        return t

    def test_injects_combined_types(self) -> None:
        t = self._backend()
        t.set_fix_params([CProblemType.残留日文, CProblemType.用词不当])
        self.assertEqual(t._problem_types, [CProblemType.残留日文, CProblemType.用词不当])
        # 组合含需原文类型 → 译文+原文（自动推导）
        self.assertTrue(t._include_src)
        self.assertTrue(t._inject_problem)
        self.assertIn("残留日文", t.trans_prompt)
        self.assertIn("用词不当", t.trans_prompt)
        self.assertNotIn("换行位置异常", t.trans_prompt)

    def test_pure_dst_only_combination_omits_src(self) -> None:
        t = self._backend()
        t.set_fix_params([CProblemType.换行位置异常, CProblemType.频繁换行])
        self.assertFalse(t._include_src)
        self.assertIn("换行位置异常", t.trans_prompt)
        self.assertIn("[br_issue_guide]", t.trans_prompt)

    def test_long_line_missing_newline_is_dst_only(self) -> None:
        t = self._backend()
        t.set_fix_params([CProblemType.单句过长])
        self.assertFalse(t._include_src)

    def test_mixed_combination_with_src_required_type_includes_src(self) -> None:
        t = self._backend()
        t.set_fix_params([CProblemType.残留日文, CProblemType.换行位置异常])
        self.assertTrue(t._include_src)

    def test_attr_and_adverb_long_use_src(self) -> None:
        # 定语过长/状语过长按设计需对照原文 → 译文+原文
        t = self._backend()
        t.set_fix_params([CProblemType.定语过长, CProblemType.状语过长])
        self.assertTrue(t._include_src)

    def test_empty_types_disables_backend(self) -> None:
        t = self._backend()
        t.set_fix_params([])
        self.assertTrue(t._disabled)
        self.assertEqual(t._problem_types, [])
        self.assertIn("未配置具体修复指令", t.trans_prompt)


class BrGuideHookTests(unittest.TestCase):
    def test_br_type_injects_guide(self) -> None:
        t = make_translator(problem_types=[CProblemType.换行位置异常])
        out = t._apply_extra_first_round_replacements("x[br_issue_guide]y")
        self.assertTrue(out.startswith("x在不改变句子原意"))
        self.assertIn("自由调整<br>位置或删除<br>", out)
        self.assertIn("禁止添加<br>", out)
        # 注入的 guide 内嵌阈值占位符，同一趟装配就应被填成配置值（桩配置回退默认最大值 24）
        self.assertNotIn("[avg_sentence_len_threshold]", out)
        self.assertIn("本项目 24 字", out)

    def test_br_guide_forbids_adding_br(self) -> None:
        # 禁令防止模型为压行长而新增换行（新增会新报「多加换行」）
        self.assertIn("禁止添加<br>", build_br_issue_guide())

    def test_br_guide_uses_free_adjust_wording(self) -> None:
        # 换行修复口径已由「3 级优先级」改为「守规范前提下自由调整」
        guide = build_br_issue_guide()
        self.assertIn("不改变句子原意", guide)
        self.assertIn("自由调整<br>位置或删除<br>", guide)
        self.assertNotIn("推荐度", guide)
        self.assertNotIn("推荐度", build_fix_instructions([CProblemType.换行位置异常]))

    def test_brstation_prompt_uses_free_adjust_wording(self) -> None:
        # ForBRStation 模板（提示词页展示 + override 默认值）与运行时口径同步
        self.assertIn("不改变句子原意", FORGAL_JSON_BRSTATION_PROMPT)
        self.assertNotIn("推荐度", FORGAL_JSON_BRSTATION_PROMPT)

    def test_brstation_prompt_gets_threshold_numbers(self) -> None:
        # 静态模板经 [br_issue_guide] 注入后同样拿到配置实际阈值，无占位符残留
        t = make_translator(problem_types=[CProblemType.换行位置异常])
        out = t._apply_extra_first_round_replacements(FORGAL_JSON_BRSTATION_PROMPT)
        self.assertIn("本项目 24 字", out)
        self.assertNotIn("[avg_sentence_len_threshold]", out)

    def test_br_instruction_fills_dynamic_break_desc(self) -> None:
        # 检测侧白名单含破折号（——）时，换行合规口径必须同步，避免口径漂移
        t = make_translator(problem_types=[CProblemType.换行位置异常])
        out = t._apply_extra_first_round_replacements(
            build_fix_instructions([CProblemType.换行位置异常])
        )
        self.assertIn("破折号（——）", out)
        self.assertNotIn("[allowed_break_ends]", out)
        self.assertNotIn("[br_issue_guide]", out)
        self.assertNotIn("[avg_sentence_len_threshold]", out)

    def test_threshold_placeholder_filled_without_br_type(self) -> None:
        # 白名单不含换行位置异常、但用户模板显式写了阈值占位符时同样被填充
        t = make_translator(problem_types=[CProblemType.残留日文])
        out = t._apply_extra_first_round_replacements("x[avg_sentence_len_threshold]y")
        self.assertTrue(out.startswith("x本项目 24 字"))
        self.assertTrue(out.endswith("y"))

    def test_hook_fills_allowed_break_ends(self) -> None:
        # [allowed_break_ends] 由检测侧描述动态填充（含破折号），不残留占位符
        t = make_translator(problem_types=[CProblemType.换行位置异常])
        out = t._apply_extra_first_round_replacements("x[allowed_break_ends]y")
        self.assertEqual(out, "x" + build_allowed_break_ends_desc() + "y")
        self.assertIn("破折号（——）", out)

    def test_fix_instruction_uses_dynamic_break_desc(self) -> None:
        # 换行位置异常修复指令引用动态占位符，不再硬编码允许清单
        text = build_fix_instructions([CProblemType.换行位置异常])
        self.assertIn("[allowed_break_ends]", text)

    def test_brstation_prompt_uses_dynamic_break_desc(self) -> None:
        # ForBRStation 任务描述与质量标准均引用动态占位符，与 [br_issue_guide] 同源不矛盾
        self.assertEqual(FORGAL_JSON_BRSTATION_PROMPT.count("[allowed_break_ends]"), 2)
        self.assertNotIn("空格/Tab、emoji 或颜文字", FORGAL_JSON_BRSTATION_PROMPT)

    def test_non_br_type_removes_placeholder(self) -> None:
        t = make_translator(problem_types=[CProblemType.残留日文])
        out = t._apply_extra_first_round_replacements("x[br_issue_guide]y")
        self.assertEqual(out, "xy")


class SentenceLenThresholdTests(unittest.TestCase):
    """平均分句长度阈值取 problemAnalyze 配置实际值（非 H / H 两档较大者），不再照抄字面表述。"""

    def test_guide_carries_threshold_placeholder(self) -> None:
        # 阈值数值由运行时注入配置值，避免与检测侧配置项硬编码分叉
        self.assertIn("[avg_sentence_len_threshold]", build_br_issue_guide())

    def test_reads_problem_analyze_values(self) -> None:
        config = _make_config_with_thresholds(
            tempfile.mkdtemp(prefix="fixr_"), 15, 20
        )
        desc = build_sentence_len_threshold_desc(config)
        self.assertIn("本项目 20 字", desc)

    def test_takes_max_of_both_tiers(self) -> None:
        # 非 H 大于 H 时取非 H 值（取的是两档最大值，而非固定用 H 档）
        config = _make_config_with_thresholds(
            tempfile.mkdtemp(prefix="fixr_"), 25, 20
        )
        self.assertIn("本项目 25 字", build_sentence_len_threshold_desc(config))

    def test_falls_back_to_detection_defaults(self) -> None:
        config = _make_config(tempfile.mkdtemp(prefix="fixr_"))
        desc = build_sentence_len_threshold_desc(config)
        self.assertIn("本项目 24 字", desc)

    def test_config_without_getters_does_not_raise(self) -> None:
        # 测试桩 / 非常规调用缺这两个 getter 时回退默认值，不中断修复轮
        desc = build_sentence_len_threshold_desc(SimpleNamespace())
        self.assertIn("本项目 24 字", desc)

    def test_config_value_reaches_assembled_prompt(self) -> None:
        config = _make_config_with_thresholds(
            tempfile.mkdtemp(prefix="fixr_"), 15, 20
        )
        t = make_translator(problem_types=[CProblemType.换行位置异常])
        t.pj_config = config
        out = t._apply_extra_first_round_replacements(
            build_fix_instructions([CProblemType.换行位置异常])
        )
        self.assertIn("本项目 20 字", out)


class LazyFallbackTests(unittest.TestCase):
    """__init__ 无回退副作用；手动执行路径由 batch_translate 惰性回退。"""

    def _backend(self, problem_list) -> ForProblemFixRound:
        t = object.__new__(ForProblemFixRound)
        t.pj_config = SimpleNamespace(
            getProblemAnalyzeConfig=lambda key: problem_list,
            getKey=lambda key, default=None: default,
        )
        t._log_tag = "[问题修复]"
        t._problem_types = []
        t._finalize_prompts = lambda: None
        return t

    def test_init_has_no_fallback_side_effect(self) -> None:
        # 调度路径实例化时白名单为空属正常过程：不打印回退、不置 _disabled
        config = SimpleNamespace(
            getProblemAnalyzeConfig=lambda key: ["残留日文", "用词不当"],
            getKey=lambda key, default=None: default,
        )

        def _fake_base_init(self, config_, eng_type, proxy_pool=None, token_pool=None):
            self.pj_config = config_

        with patch.object(BaseEngine, "__init__", _fake_base_init), patch.object(
            BaseEngine, "init_chatbot", lambda self, *a, **k: None
        ), patch.object(
            BaseEngine,
            "_apply_internal_prompt_template_overrides",
            lambda self: None,
        ), patch(
            "GalTransl.ConfigHelper.CProjectConfig.getProjectConfig",
            return_value={"common": {}},
        ):
            t = ForProblemFixRound(config, "ForFixRound", None, None)
        self.assertEqual(t._problem_types, [])
        self.assertFalse(getattr(t, "_disabled", False))

    def test_lazy_fallback_fills_all_detected_types(self) -> None:
        t = self._backend(["残留日文", "用词不当"])
        self.assertTrue(t._ensure_problem_types_configured())
        self.assertEqual(
            [p.name for p in t._problem_types], ["残留日文", "用词不当"]
        )
        self.assertFalse(t._disabled)

    def test_lazy_fallback_empty_problem_list_returns_false(self) -> None:
        t = self._backend(None)
        self.assertFalse(t._ensure_problem_types_configured())
        self.assertEqual(t._problem_types, [])

    def test_fallback_to_problem_list_derives_include_src(self) -> None:
        # 手动执行（set_fix_params([]) 置 _disabled）；batch_translate 惰性回退
        # problemList 含需原文类型时，_ensure_problem_types_configured 填充后
        # _include_src 应推导为 True（译文+原文）
        t = self._backend(["残留日文", "换行位置异常"])
        t.set_fix_params([])  # 模拟手动执行：空 types -> _disabled
        self.assertTrue(t._disabled)
        self.assertTrue(t._ensure_problem_types_configured())
        self.assertFalse(t._disabled)  # 回退后恢复可执行
        self.assertEqual(
            [p.name for p in t._problem_types], ["残留日文", "换行位置异常"]
        )
        # 组合含「残留日文」(src+dst) → 整体推导为译文+原文
        self.assertTrue(t._include_src)

    def test_fallback_to_all_dst_only_problem_list_omits_src(self) -> None:
        # problemList 仅含 dst-only 类型时，回退后推导为仅译文
        t = self._backend(["换行位置异常", "频繁换行"])
        t.set_fix_params([])
        self.assertTrue(t._ensure_problem_types_configured())
        self.assertFalse(t._include_src)


class TokenPoolFallbackTests(unittest.TestCase):
    """验证 ForFixRound 复用主翻译 profile 令牌池、不回退本地端口默认值。"""

    def test_token_pool_none_without_endpoint_disables(self) -> None:
        # 无 OpenAI 兼容端点配置时，token_pool=None 兜底构建出空池，应置 _disabled_reason
        config = _make_config(tempfile.mkdtemp(prefix="fixr_"))
        backend = ForProblemFixRound(config, "ForFixRound", None, None)
        self.assertTrue(
            backend._disabled_reason,
            "无可用端点时应在 __init__ 置 _disabled_reason，而非回退本地端口",
        )

    def test_token_pool_none_with_endpoint_stays_enabled(self) -> None:
        # 传入有效令牌池时，不应置 _disabled_reason
        config = _make_config(tempfile.mkdtemp(prefix="fixr_"))
        fake_pool = SimpleNamespace(
            get_available_token=lambda: [
                SimpleNamespace(token="x", domain="https://api.openai.com", maskToken=lambda: "***")
            ]
        )
        backend = ForProblemFixRound(config, "ForFixRound", None, fake_pool)
        self.assertEqual(backend._disabled_reason, "")

    def test_batch_translate_skips_when_no_endpoint(self) -> None:
        # 无端点（_disabled_reason 已置）时 batch_translate 直接跳过，不发请求
        config = _make_config(tempfile.mkdtemp(prefix="fixr_"))
        backend = ForProblemFixRound(config, "ForFixRound", None, None)
        backend.set_fix_params([CProblemType.残留日文])
        called = {"llm": False}

        async def _fake_call_llm(*a, **k):
            called["llm"] = True
            return "", None

        backend._call_llm = _fake_call_llm
        trans_list = [make_tran(1, "残留日文：です", "译1")]

        result = asyncio.run(
            backend.batch_translate("f.json", "c.json", trans_list, 1)
        )
        self.assertFalse(called["llm"])
        self.assertIs(result, trans_list)

    def test_token_pool_build_failure_falls_back_to_empty(self) -> None:
        # COpenAITokenPool 构建抛异常（缺段或 tokens 畸形）时，应进入 _EmptyTokenPool
        # 兜底：__init__ 不崩（for token in [] 可迭代），且置 _disabled_reason 跳过
        config = _make_config(tempfile.mkdtemp(prefix="fixr_"))
        with patch(
            "GalTransl.Backend.ForFixRound.COpenAITokenPool",
            side_effect=KeyError("token 键缺失"),
        ):
            backend = ForProblemFixRound(config, "ForFixRound", None, None)
        self.assertEqual(backend._disabled_reason, "主翻译令牌池构建失败")


class ParseFixResponseTests(unittest.TestCase):
    """验证 _parse_fix_response 对退化输出的过滤（原文回显 / 当前译文相同 / 空 better）。"""

    def _backend(self):
        return make_translator()

    def test_source_echo_is_skipped(self) -> None:
        # better 等于原文(post_src) 时视为原文回显，在 found_count 前过滤，
        # 应跳过且不写入 alt_dst，found 不计入回显句
        t = self._backend()
        t1 = make_tran(1, "残留日文：です", "译1", post_src="原文AAA")
        t2 = make_tran(2, "残留日文：ます", "译2", post_src="原文BBB")
        resp = '{"id":1,"better":"原文AAA"}\n{"id":2,"better":"新译文Y2"}'
        success, found = t._parse_fix_response(resp, [t1, t2], "")
        self.assertEqual(found, 1)
        self.assertEqual(success, 1)
        self.assertEqual(t1.alt_dst, "")
        self.assertEqual(t2.alt_dst, "新译文Y2")

    def test_same_as_current_dst_is_skipped(self) -> None:
        # better 等于当前译文时跳过（既有逻辑，防止回归）
        t = self._backend()
        t1 = make_tran(1, "残留日文：です", "译1", post_src="原文AAA")
        resp = '{"id":1,"better":"译1"}'
        success, found = t._parse_fix_response(resp, [t1], "")
        self.assertEqual(success, 0)
        self.assertEqual(t1.alt_dst, "")

    def test_empty_better_is_skipped(self) -> None:
        # better 为空时跳过（既有逻辑，防止回归）
        t = self._backend()
        t1 = make_tran(1, "残留日文：です", "译1", post_src="原文AAA")
        resp = '{"id":1,"better":""}'
        success, found = t._parse_fix_response(resp, [t1], "")
        self.assertEqual(found, 0)
        self.assertEqual(success, 0)
        self.assertEqual(t1.alt_dst, "")


if __name__ == "__main__":
    unittest.main()
