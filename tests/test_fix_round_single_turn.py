# -*- coding: utf-8 -*-
"""修复/改进轮单轮对话模式（gpt.chatMode=single）测试。

覆盖 BaseSparseFixRound.batch_translate 的 single 分支（作用于
ForJPResidue / ForBRStation / ForBanWordFix / ForProblemFixRound / ForImproveTranslation）：
  - chatMode 经继承链（ForGalJsonTranslate.__init__）在修复轮实例上解析
  - 单轮模式每批独立完整请求：[system, user]、user 均为完整首轮内容
  - 单轮模式不建立/不回写会话（conversations 无该文件条目）
  - 单轮模式 LLM 失败批跳过、后续批继续（无会话可重置）
  - 单轮模式整批回显退化仅告警，不重置对话
  - 多轮模式回归：首轮/续轮内容与会话回写不变
"""
import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, patch

from GalTransl.CSentense import CSentense
from GalTransl.Backend.BaseFixRound import BaseSparseFixRound
from GalTransl.Backend.BaseTranslate import BaseTranslate
from GalTransl.Backend.ForGalJsonTranslate import ForGalJsonTranslate
from GalTransl.Backend.ForJPResidue import ForJPResidue


class _FakeOpencc:
    """身份转换替身：_normalize_parsed_translation_text 在中文目标语言下调用 convert。"""

    def convert(self, text):
        return text


class _MockConfig:
    def __init__(self, values):
        self._values = values

    def getKey(self, key, default=None):
        return self._values.get(key, default)


def make_translator(cls=ForJPResidue, chat_mode="single"):
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
    t.chat_mode = chat_mode
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
    t._first_round_calls = []
    t._first_round_gptdicts = []

    def _fake_first_round(self, input_src, gptdict, filename):
        self._captured_input = input_src
        self._first_round_calls.append(input_src)
        self._first_round_gptdicts.append(gptdict)
        return "FIRST_ROUND_CONTENT\n" + input_src

    t._build_first_round_content = MethodType(_fake_first_round, t)
    t._resolve_file_metadata = MethodType(lambda self, *a, **k: None, t)
    t._format_file_metadata_block = MethodType(lambda self, *a, **k: "", t)
    t._format_global_prompt_block = MethodType(lambda self, *a, **k: "", t)
    t._apply_history_result = MethodType(lambda self, p, *a, **k: p, t)
    t._trim_conversation = MethodType(lambda self, conv, *a, **k: conv, t)
    t._build_idx_tip = MethodType(lambda self, lst, *a, **k: "1~2", t)
    t._record_round_runtime_error = MethodType(lambda self, *a, **k: None, t)
    t._group_is_h_scene = MethodType(lambda self, group, filename: False, t)
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


def make_two_trans():
    return [
        make_tran(1, "残留日文：です", "译1"),
        make_tran(2, "残留日文：です", "译2"),
    ]


class ChatModeInheritanceTests(unittest.TestCase):
    """修复轮经继承链真实解析 gpt.chatMode（ForGalJsonTranslate.__init__）。"""

    def _make(self, config_values) -> ForJPResidue:
        token_pool = SimpleNamespace(get_available_token=lambda: [])
        with patch.object(BaseTranslate, "__init__", lambda self, *a, **k: None), \
             patch.object(BaseTranslate, "init_chatbot", lambda self, *a, **k: None), \
             patch.object(
                 ForGalJsonTranslate,
                 "_apply_internal_prompt_template_overrides",
                 lambda self: None,
             ), \
             patch.object(
                 BaseSparseFixRound, "_finalize_prompts", lambda self: None
             ):
            return ForJPResidue(config=_MockConfig(config_values), eng_type="eng",
                                proxy_pool=None, token_pool=token_pool)

    def test_fix_round_parses_single_mode(self) -> None:
        t = self._make({"gpt.chatMode": "single"})
        self.assertEqual(t.chat_mode, "single")

    def test_fix_round_defaults_to_multi(self) -> None:
        t = self._make({})
        self.assertEqual(t.chat_mode, "multi")


class SingleModeRequestShapeTests(unittest.IsolatedAsyncioTestCase):
    """单轮模式：每批独立完整请求 [system, user]，user 为完整首轮内容。"""

    async def _run(self, t, trans_list, llm_resp, num_per_request=100, gpt_dic=None):
        called = []

        async def fake_call_llm(self, messages, filename, idx_tip, *_a, **_k):
            called.append(messages)
            return llm_resp, SimpleNamespace(model_name="m", domain="x")

        t._call_llm = MethodType(fake_call_llm, t)
        await t.batch_translate(
            "f.json", "f.json", trans_list, num_per_request, gpt_dic=gpt_dic
        )
        return called

    async def test_each_batch_is_independent_full_request(self) -> None:
        t = make_translator(chat_mode="single")
        called = await self._run(t, make_two_trans(), "", num_per_request=1)
        self.assertEqual(len(called), 2)
        for messages, input_src in zip(called, t._first_round_calls):
            self.assertEqual([m["role"] for m in messages], ["system", "user"])
            self.assertEqual(messages[0]["content"], "SYSTEM_PROMPT")
            # 每批均为完整首轮内容（多轮模式仅第 1 批），并携带本批输入
            self.assertTrue(messages[1]["content"].startswith("FIRST_ROUND_CONTENT"))
            self.assertIn(input_src, messages[1]["content"])
        self.assertEqual(len(t._first_round_calls), 2)

    async def test_single_mode_builds_no_conversation_state(self) -> None:
        t = make_translator(chat_mode="single")
        await self._run(t, make_two_trans(), "", num_per_request=1)
        # 单轮模式不建立/不回写会话，无状态残留
        self.assertEqual(t.conversations, {})

    async def test_single_mode_start_log_mentions_single_round(self) -> None:
        t = make_translator(chat_mode="single")
        with self.assertLogs("GalTransl", level="INFO") as cm:
            await self._run(t, make_two_trans(), "", num_per_request=1)
        joined = "\n".join(cm.output)
        self.assertIn("（单轮模式，每批独立完整请求）", joined)

    async def test_single_mode_batch_log_label(self) -> None:
        t = make_translator(chat_mode="single")
        t.pj_config.active_workers = 1
        with self.assertLogs("GalTransl", level="INFO") as cm:
            await self._run(t, make_two_trans(), "", num_per_request=1)
        joined = "\n".join(cm.output)
        self.assertIn("-> 修复输入[单轮]", joined)
        self.assertNotIn("续轮", joined)

    async def test_single_mode_injects_gptdict_every_batch(self) -> None:
        # gpt_dic 非 None 时术语字典经 _build_first_round_content 注入 [Glossary]；
        # 多轮模式仅首轮走该路径、续轮独立拼接，single 模式每批都必须携带
        t = make_translator(chat_mode="single")
        gpt_dic = MagicMock()
        gpt_dic.gen_prompt.return_value = "GLOSSARY_BLOCK"
        await self._run(
            t, make_two_trans(), "", num_per_request=1, gpt_dic=gpt_dic
        )
        self.assertEqual(
            t._first_round_gptdicts, ["GLOSSARY_BLOCK", "GLOSSARY_BLOCK"]
        )


class SingleModeResilienceTests(unittest.IsolatedAsyncioTestCase):
    """单轮模式：失败/退化路径无会话可破坏，仅告警并继续。"""

    async def test_llm_failure_skips_batch_and_continues(self) -> None:
        t = make_translator(chat_mode="single")
        trans_list = make_two_trans()
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
        self.assertEqual(t.conversations, {})

    async def test_echo_degrade_warns_without_conversation_reset(self) -> None:
        t = make_translator(chat_mode="single")
        reset_calls = []
        t.reset_conversation = MethodType(
            lambda self, filename="": reset_calls.append(filename), t
        )
        recorded = []
        t._record_round_runtime_error = MethodType(
            lambda self, *a, **k: recorded.append(a[2]), t
        )
        trans_list = [make_tran(1, "残留日文：です", "译1")]

        async def fake_call_llm(self, messages, filename, idx_tip, *_a, **_k):
            # better 与当前译文相同：found=1、success=0，命中整批回显退化判定
            return 'a1b|{"id": 1, "better": "译1"}', SimpleNamespace(
                model_name="m", domain="x"
            )

        t._call_llm = MethodType(fake_call_llm, t)
        with self.assertLogs("GalTransl", level="WARNING") as cm:
            await t.batch_translate(
                "f.json", "f.json", trans_list, 100, gpt_dic=None
            )
        self.assertEqual(reset_calls, [])  # 单轮无会话，不做重置
        self.assertEqual(t.conversations, {})
        joined = "\n".join(cm.output)
        self.assertIn("疑似整批回显或退化输出", joined)
        self.assertNotIn("重置对话", joined)
        self.assertTrue(recorded and "疑似整批回显" in recorded[0])


class MultiModeRegressionTests(unittest.IsolatedAsyncioTestCase):
    """多轮模式回归：首轮/续轮内容与会话回写维持既有行为。"""

    async def test_multi_mode_keeps_conversation_alternation(self) -> None:
        t = make_translator(chat_mode="multi")

        async def fake_call_llm(self, messages, filename, idx_tip, *_a, **_k):
            return 'a1b|{"id": 1, "better": "修复译1"}', SimpleNamespace(
                model_name="m", domain="x"
            )

        t._call_llm = MethodType(fake_call_llm, t)
        await t.batch_translate(
            "f.json", "f.json", make_two_trans(), 1, gpt_dic=None
        )
        conv = t.conversations["f.json"]
        self.assertEqual(
            [m["role"] for m in conv],
            ["system", "user", "assistant", "user", "assistant"],
        )
        # 第 1 批为完整首轮内容，第 2 批为精简续轮内容（依赖会话记忆）
        self.assertEqual(len(t._first_round_calls), 1)
        self.assertNotIn("FIRST_ROUND_CONTENT", conv[3]["content"])


if __name__ == "__main__":
    unittest.main()
