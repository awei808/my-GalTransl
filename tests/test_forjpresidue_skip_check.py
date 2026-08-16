# -*- coding: utf-8 -*-
"""ForJPResidue「残留日文」筛选与送审口径的单元测试。

验证：
  - 带「残留日文」problem 的有效译文会送审（_call_llm 收到的 input_src 含该句）
  - 不带「残留日文」problem（如仅词频过高）的句子不送审
  - skip_check=True 的句子即使带残留日文也不送审（独立硬过滤）
  - 失败译文 / 空译文不送审
  - 全部跳过时提前返回，不触发任何 LLM 调用
  - 输入携带 src、不携带 problem 键的口径（与需求"不携带 problem 标注"一致）
"""
import asyncio
import unittest
from types import MethodType, SimpleNamespace

from GalTransl.CSentense import CSentense
from GalTransl.Backend.ForJPResidue import ForJPResidue


def make_translator():
    """通过 __new__ 打桩 ForJPResidue 所需属性，避免重量级初始化。"""
    t = ForJPResidue.__new__(ForJPResidue)
    t.pj_config = SimpleNamespace(
        active_workers=0,
        stop_event=None,
        translation_guideline="",
        getProjectDir=lambda: "",
        getKey=lambda key, default=None: default,
    )
    t.eng_type = "ForJPResidue"
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
    t.opencc = SimpleNamespace(convert=lambda s: s)
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


def _jp_residue_tran(index, pre_dst, problem, skip_check=False, failed=False):
    t = CSentense(f"src{index}", index=index)
    t.post_src = f"src{index}"
    t.pre_dst = pre_dst
    if failed:
        t.pre_dst = "(Failed) " + pre_dst
    t.problem = problem
    t.skip_check = skip_check
    return t


class JpResidueFilterTests(unittest.TestCase):
    async def _run_with_llm(self, t, trans_list, llm_resp):
        called = {"flag": False}

        async def fake_call_llm(self, messages, filename, idx_tip, *_a, **_k):
            called["flag"] = True
            return llm_resp, SimpleNamespace(model_name="m", domain="x")

        t._call_llm = MethodType(fake_call_llm, t)
        t._parse_fix_response = MethodType(lambda self, *a, **k: (0, 0), t)
        await t.batch_translate("f.json", "f.json", trans_list, 100, gpt_dic=None)
        return called, t._captured_input

    def test_jp_residue_sentence_sent(self) -> None:
        # 带残留日文 problem → 送审
        t = make_translator()
        trans_list = [
            _jp_residue_tran(1, "她像ノシ一样笑了。", "残留日文：ノシ", skip_check=False),
            _jp_residue_tran(2, "读书。", "词频过高", skip_check=False),
        ]
        called, input_src = asyncio.run(
            self._run_with_llm(t, trans_list, "")
        )
        self.assertTrue(called["flag"])
        self.assertIn("她像ノシ一样笑了。", input_src)  # 残留日文句送审
        # 输入携带 src（不携带 problem 键）
        self.assertIn("src1", input_src)
        self.assertNotIn("读书。", input_src)  # 非残留日文句不送审

    def test_skip_check_sentence_not_sent(self) -> None:
        # 带残留日文但 skip_check=True → 不送审
        t = make_translator()
        trans_list = [
            _jp_residue_tran(1, "译1", "残留日文：ノシ", skip_check=True),
            _jp_residue_tran(2, "译2", "残留日文：ノシ", skip_check=False),
        ]
        called, input_src = asyncio.run(
            self._run_with_llm(t, trans_list, "")
        )
        self.assertTrue(called["flag"])
        self.assertIn("译2", input_src)
        self.assertNotIn("译1", input_src)

    def test_failed_translation_not_sent(self) -> None:
        # 失败译文即使带残留日文也不送审
        t = make_translator()
        trans_list = [
            _jp_residue_tran(1, "译1", "残留日文：ノシ", failed=True),
        ]
        called = {"flag": False}

        async def fail_if_called(self, *a, **_k):
            called["flag"] = True
            raise AssertionError("失败译文不应送审")

        t._call_llm = MethodType(fail_if_called, t)
        asyncio.run(t.batch_translate("f.json", "f.json", trans_list, 100, gpt_dic=None))
        self.assertFalse(called["flag"])

    def test_all_skip_check_returns_early_no_llm(self) -> None:
        t = make_translator()
        trans_list = [
            _jp_residue_tran(1, "译1", "残留日文：ノシ", skip_check=True),
            _jp_residue_tran(2, "译2", "残留日文：ノシ", skip_check=True),
        ]
        called = {"flag": False}

        async def fail_if_called(self, *a, **_k):
            called["flag"] = True
            raise AssertionError("skip_check 全排时应提前返回，不应调用 LLM")

        t._call_llm = MethodType(fail_if_called, t)
        asyncio.run(t.batch_translate("f.json", "f.json", trans_list, 100, gpt_dic=None))
        self.assertFalse(called["flag"])


if __name__ == "__main__":
    unittest.main()
