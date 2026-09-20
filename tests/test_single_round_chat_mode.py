"""翻译后端单轮对话模式（gpt.chatMode=single）测试

覆盖：
  - 单轮提示词模板与多轮模板不同（历史上下文语义改写，其余骨架一致）
  - chatMode 配置解析（默认 multi / 显式 single / 非法值回退 multi）与模板选择
  - 单轮 translate：请求为独立 [system, user]，已译上下文经 restore_context
    注入 [history_result]，且不回写多轮会话
  - jailbreak 预填充按请求生效
  - 解析失败重试不触碰多轮会话状态
"""

import re
import unittest
from types import SimpleNamespace, MethodType
from unittest.mock import patch

from GalTransl.Backend.BaseTranslate import BaseTranslate
from GalTransl.Backend.ForGalJsonTranslate import ForGalJsonTranslate
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_TRANS_PROMPT,
    FORGAL_JSON_TRANS_PROMPT_SINGLE,
)
from GalTransl.CSentense import CSentense


def make_token(model_name="test-model"):
    return SimpleNamespace(model_name=model_name, domain="https://example.com")


class _MockConfig:
    def __init__(self, values):
        self._values = values

    def getKey(self, key, default=None):
        return self._values.get(key, default)


def make_instance(config_values=None) -> ForGalJsonTranslate:
    """真实构造实例（patch 掉重初始化），验证 __init__ 的 chatMode 解析与模板选择。"""
    config = _MockConfig(config_values or {})
    token_pool = SimpleNamespace(get_available_token=lambda: [])
    with patch.object(BaseTranslate, "__init__", lambda self, *a, **k: None), \
         patch.object(BaseTranslate, "init_chatbot", lambda self, *a, **k: None), \
         patch.object(
             ForGalJsonTranslate,
             "_apply_internal_prompt_template_overrides",
             lambda self: None,
         ):
        return ForGalJsonTranslate(config, "eng", None, token_pool)


def echo_translation(messages) -> str:
    """从 user 内容提取 sig/id 对，按单轮格式回显 dst（与既有集成测试同口径）。

    仅取最后一个 jsonline 代码块（[Input] 块），避免把 <history_result>
    注入的历史上下文行（sig 固定为 old）也回显进输出。
    """
    content = ""
    for m in messages or []:
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content", "")
    target = content.split("```jsonline")[-1]
    pairs = re.findall(r'([a-z0-9]{3})\|\{"id":\s*(\d+)', target)
    return "\n".join(
        sig + '|{"id": ' + idx + ', "dst": "当前译文"}' for sig, idx in pairs
    )


def make_single_translator() -> ForGalJsonTranslate:
    """__new__ 打桩：单轮 translate 流程所需全部属性。"""
    t = ForGalJsonTranslate.__new__(ForGalJsonTranslate)
    t.pj_config = SimpleNamespace(
        active_workers=0,
        stop_event=None,
        translation_guideline="",
        getProjectDir=lambda: "",
        getKey=lambda key, default=None: default,
    )
    t.eng_type = "ForGal-json-translate"
    t.chat_mode = "single"
    t.enhance_jailbreak = False
    t.system_prompt = "SYSTEM_PROMPT"
    t.trans_prompt = (
        "<history_result>\n[history_result]\n</history_result>\n\n"
        "<translation_guidelines>\n[translation_guideline]\n</translation_guidelines>\n\n"
        "<glossary>\n[Glossary]\n</glossary>\n\n"
        "[global_prompt]\n\n[plot_metadata]\n\n"
        "<input>\n```jsonline\n[Input]\n```\n</input>"
    )
    t.source_lang = "Japanese"
    t.target_lang = "English"  # 非中文，跳过 opencc
    t.restore_context_mode = True
    t.contextNum = 8
    t.last_translations = {}
    t._history_placeholder_warned = False
    t.plot_metadata_map = {}
    t.file_metadata_map = {}
    t._file_metadata_by_file = {}
    t._file_metadata_loaded = False
    t.project_config = None
    t.conversations = {}
    t._force_first_round_files = set()
    t.multi_round_max_history = 0
    t.last_file_name = ""
    t._last_chatbot_was_stream = False
    t._last_chatbot_model_name = ""
    t.batch_metadata_map = {}
    t._batch_metadata_by_file = {}
    t._batch_metadata_loaded = False
    t._global_prompt = None
    t._global_prompt_loaded = False
    t._plot_route_map = None
    t._plot_route_map_loaded = False
    return t


class SinglePromptTemplateTests(unittest.TestCase):
    def test_single_prompt_differs_from_multi(self) -> None:
        self.assertNotEqual(FORGAL_JSON_TRANS_PROMPT, FORGAL_JSON_TRANS_PROMPT_SINGLE)

    def test_single_prompt_keeps_skeleton_and_rewrites_history_semantics(self) -> None:
        for placeholder in (
            "[Input]",
            "[Glossary]",
            "[history_result]",
            "[translation_guideline]",
            "[plot_metadata]",
            "[batch_metadata]",
            "[global_prompt]",
        ):
            self.assertIn(placeholder, FORGAL_JSON_TRANS_PROMPT_SINGLE)
        self.assertIn("每次请求独立完整", FORGAL_JSON_TRANS_PROMPT_SINGLE)
        # 多轮模板不应出现单轮专属表述
        self.assertNotIn("每次请求独立完整", FORGAL_JSON_TRANS_PROMPT)


class ChatModeConfigTests(unittest.TestCase):
    def test_default_mode_is_multi(self) -> None:
        t = make_instance()
        self.assertEqual(t.chat_mode, "multi")
        self.assertIs(t.trans_prompt, FORGAL_JSON_TRANS_PROMPT)

    def test_single_mode_selects_single_prompt(self) -> None:
        t = make_instance({"gpt.chatMode": "single"})
        self.assertEqual(t.chat_mode, "single")
        self.assertIs(t.trans_prompt, FORGAL_JSON_TRANS_PROMPT_SINGLE)

    def test_invalid_mode_falls_back_to_multi(self) -> None:
        t = make_instance({"gpt.chatMode": "both"})
        self.assertEqual(t.chat_mode, "multi")
        self.assertIs(t.trans_prompt, FORGAL_JSON_TRANS_PROMPT)


class SingleRoundTranslateTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_mode_builds_independent_messages_with_history(self) -> None:
        t = make_single_translator()
        # 前一句已翻译（prev_tran 链），应经 restore_context 注入 [history_result]
        prev = CSentense("前文原文", index=0)
        prev.pre_dst = "前文译文"
        cur = CSentense("当前原文", index=1)
        cur.prev_tran = prev
        captured = {}

        async def fake_ask(self, **kwargs):
            captured["messages"] = kwargs.get("messages")
            self._last_chatbot_was_stream = False
            return echo_translation(kwargs.get("messages")), make_token()

        t.ask_chatbot = MethodType(fake_ask, t)
        cnt, res = await t.translate([cur], filename="f.json")
        self.assertEqual(cnt, 1)
        msgs = captured["messages"]
        # 单轮请求独立：system + user 两条，无历史轮次
        self.assertEqual([m["role"] for m in msgs], ["system", "user"])
        self.assertEqual(msgs[0]["content"], "SYSTEM_PROMPT")
        # 历史上下文注入
        self.assertIn("前文译文", msgs[1]["content"])
        self.assertIn("```jsonline", msgs[1]["content"])
        # 本批待译内容与规范占位符替换
        self.assertIn("当前原文", msgs[1]["content"])
        # 单轮不回写多轮会话
        self.assertEqual(t.conversations, {})

    async def test_jailbreak_prefill_applies_per_request(self) -> None:
        t = make_single_translator()
        t.enhance_jailbreak = True
        captured = {}

        async def fake_ask(self, **kwargs):
            captured["messages"] = kwargs.get("messages")
            self._last_chatbot_was_stream = False
            return echo_translation(kwargs.get("messages")), make_token()

        t.ask_chatbot = MethodType(fake_ask, t)
        cnt, _ = await t.translate([CSentense("当前原文", index=1)], filename="f.json")
        self.assertEqual(cnt, 1)
        self.assertEqual(
            [m["role"] for m in captured["messages"]],
            ["system", "user", "assistant"],
        )
        self.assertEqual(captured["messages"][-1]["content"], "```jsonline")

    async def test_retry_does_not_touch_conversation_state(self) -> None:
        t = make_single_translator()
        calls = {"n": 0}

        async def fake_ask(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                # 空响应：触发「输出为空/被拦截」解析失败 → 重试
                return "", make_token()
            self._last_chatbot_was_stream = False
            return echo_translation(kwargs.get("messages")), make_token()

        t.ask_chatbot = MethodType(fake_ask, t)
        cnt, res = await t.translate([CSentense("当前原文", index=1)], filename="f.json")
        self.assertEqual(cnt, 1)
        self.assertEqual(calls["n"], 2)
        # 重试全程未触碰多轮会话状态
        self.assertEqual(t.conversations, {})
        self.assertEqual(t._force_first_round_files, set())

    async def test_restore_context_disabled_yields_no_history(self) -> None:
        t = make_single_translator()
        t.restore_context_mode = False
        prev = CSentense("前文原文", index=0)
        prev.pre_dst = "前文译文"
        cur = CSentense("当前原文", index=1)
        cur.prev_tran = prev
        captured = {}

        async def fake_ask(self, **kwargs):
            captured["messages"] = kwargs.get("messages")
            self._last_chatbot_was_stream = False
            return echo_translation(kwargs.get("messages")), make_token()

        t.ask_chatbot = MethodType(fake_ask, t)
        cnt, _ = await t.translate([cur], filename="f.json")
        self.assertEqual(cnt, 1)
        user_content = captured["messages"][1]["content"]
        self.assertNotIn("前文译文", user_content)
        # 无历史时 <history_result> 段被剥除，不残留占位符
        self.assertNotIn("[history_result]", user_content)


if __name__ == "__main__":
    unittest.main()
