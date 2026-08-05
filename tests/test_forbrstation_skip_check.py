# -*- coding: utf-8 -*-
"""ForBRStation「跳过检查(skip_check)」独立硬过滤的单元测试。

背景：ForBRStation 的 target_trans_list 筛选新增显式排除 skip_check，
使「用户标记跳过检查的句子不再送审」成为独立硬过滤，不再依赖「重检清除
problem」这一间接副作用。本测试验证：

  - skip_check=True 且带「换行位置异常」problem 的句子不会被送入 AI
    （_call_llm 收到的批内不包含该句）
  - skip_check=False 的正常句子照常送审
  - 全部 skip_check 时提前返回，不触发任何 LLM 调用
"""
import asyncio
import unittest
from types import MethodType, SimpleNamespace

from GalTransl.CSentense import CSentense
from GalTransl.Backend.ForBRStation import ForBRStation


def make_translator():
    """通过 __new__ 打桩 ForBRStation 所需属性，避免重量级初始化。"""
    t = ForBRStation.__new__(ForBRStation)
    t.pj_config = SimpleNamespace(
        active_workers=0,
        stop_event=None,
        translation_guideline="",
        getProjectDir=lambda: "",
        getKey=lambda key, default=None: default,
    )
    t.eng_type = "ForBRStation"
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
    # 首轮构建相关：记录 input_src 供断言，不触发真实元数据/全局提示词读取
    t._captured_input = ""

    def _fake_first_round(self, input_src, gptdict, filename):
        self._captured_input = input_src
        return "FIRST_ROUND_CONTENT\n" + input_src

    t._build_br_first_round_content = MethodType(_fake_first_round, t)
    t._resolve_file_metadata = MethodType(lambda self, *a, **k: None, t)
    t._format_file_metadata_block = MethodType(lambda self, *a, **k: "", t)
    t._format_global_prompt_block = MethodType(lambda self, *a, **k: "", t)
    t._apply_history_result = MethodType(lambda self, p, *a, **k: p, t)
    t._trim_conversation = MethodType(lambda self, conv, *a, **k: conv, t)
    t._build_idx_tip = MethodType(lambda self, lst, *a, **k: "1~2", t)
    t._record_br_runtime_error = MethodType(lambda self, *a, **k: None, t)
    t.get_last_chatbot_model = MethodType(lambda self, *a, **k: "m", t)
    return t


def _newline_anomaly_tran(index, pre_dst, skip_check):
    t = CSentense(f"src{index}", index=index)
    t.post_src = f"src{index}"
    t.pre_dst = pre_dst
    t.problem = "换行位置异常：第1行"
    t.skip_check = skip_check
    return t


class SkipCheckFilterTests(unittest.TestCase):
    async def _run_with_llm(self, t, trans_list, llm_resp):
        """把 _call_llm 替换为记录调用并返回指定响应，暴露 input_src。"""
        called = {"flag": False}

        async def fake_call_llm(self, messages, filename, idx_tip, *_a, **_k):
            called["flag"] = True
            return llm_resp, SimpleNamespace(model_name="m", domain="x")

        t._call_llm = MethodType(fake_call_llm, t)
        # 解析返回 0，避免写 alt_dst 干扰断言
        t._parse_br_jsonline_text = MethodType(lambda self, *a, **k: (0, 0), t)
        await t.batch_translate("f.json", "f.json", trans_list, 100, gpt_dic=None)
        return called, t._captured_input

    def test_skip_check_sentence_not_sent(self) -> None:
        # 带换行异常 problem，但 skip_check=True → 不送审
        t = make_translator()
        trans_list = [
            _newline_anomaly_tran(1, "译1", skip_check=True),
            _newline_anomaly_tran(2, "译2", skip_check=False),
        ]
        called, input_src = asyncio.run(self._run_with_llm(t, trans_list, ""))
        self.assertTrue(called["flag"])
        self.assertIn("译2", input_src)   # 正常句仍送审
        self.assertNotIn("译1", input_src)  # skip_check 句不送审

    def test_all_skip_check_returns_early_no_llm(self) -> None:
        # 全部 skip_check → 提前返回，不触发任何 LLM 调用
        t = make_translator()
        trans_list = [
            _newline_anomaly_tran(1, "译1", skip_check=True),
            _newline_anomaly_tran(2, "译2", skip_check=True),
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
