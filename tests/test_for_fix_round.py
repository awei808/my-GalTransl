# -*- coding: utf-8 -*-
"""ForFixRound 统一问题修复后端单元测试。

覆盖：
  - build_fix_instructions：按白名单装配指令；换行位置异常时携带 [br_issue_guide] 占位符
  - 组合筛选：一句话命中白名单任一类型即入轮（多类型组合）
  - 模式差异：_include_src=False（模式 B）时输入 JSONL 不携带 src 字段
  - set_fix_params：参数注入重建提示词、切换模式
  - _apply_extra_first_round_replacements：仅白名单含换行位置异常时注入 br_issue_guide
  - __init__ 白名单回退：未指定类型时回退 problemAnalyze.problemList 全部类型
  - 模式 B 不注入术语表：_build_batch_gptdict 返回空串
"""
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
from GalTransl.Backend.ForFixRound import (
    ForProblemFixRound,
    build_br_issue_guide,
    build_fix_instructions,
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
        t.set_fix_params([CProblemType.长句丢失换行])
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
        self.assertEqual(out, "x" + build_br_issue_guide() + "y")

    def test_non_br_type_removes_placeholder(self) -> None:
        t = make_translator(problem_types=[CProblemType.残留日文])
        out = t._apply_extra_first_round_replacements("x[br_issue_guide]y")
        self.assertEqual(out, "xy")


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


if __name__ == "__main__":
    unittest.main()
