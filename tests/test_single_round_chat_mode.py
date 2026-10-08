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
    NO_HISTORY_PLACEHOLDER,
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
    t.restore_context_mode = "lines"
    t.contextNum = 8
    t.contextBatches = 1
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


class RestoreContextModeConfigTests(unittest.TestCase):
    """gpt.restoreContextMode 三值归一化（含旧布尔兼容）。"""

    def test_bool_compat_maps_to_lines_and_none(self) -> None:
        # 旧配置布尔写法：True→lines、False→none
        self.assertEqual(BaseTranslate._normalize_restore_context_mode(True), "lines")
        self.assertEqual(BaseTranslate._normalize_restore_context_mode(False), "none")

    def test_three_values_and_aliases(self) -> None:
        cases = [
            ("none", "none"),
            ("lines", "lines"),
            ("batches", "batches"),
            ("Batches", "batches"),
            (" batch ", "batches"),
            ("", "none"),
            ("0", "none"),
            ("1", "lines"),
            # YAML 空值（gpt.restoreContextMode: 后无值）解析为 None
            (None, "none"),
        ]
        for raw, expected in cases:
            self.assertEqual(
                BaseTranslate._normalize_restore_context_mode(raw),
                expected,
                f"raw={raw!r}",
            )

    def test_unknown_value_falls_back_to_lines(self) -> None:
        # 无法识别的值告警后回退 lines（保留上下文比静默丢弃安全）
        with patch("GalTransl.Backend.BaseTranslate.LOGGER") as mock_logger:
            self.assertEqual(
                BaseTranslate._normalize_restore_context_mode("xxx"), "lines"
            )
            mock_logger.warning.assert_called_once()


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
        t.restore_context_mode = "none"
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
        # 无历史时注入固定占位文本（模板骨架恒定），不残留占位符
        self.assertIn(NO_HISTORY_PLACEHOLDER, user_content)
        self.assertNotIn("[history_result]", user_content)

    async def test_batches_mode_injects_batch_count_times_batch_lines(self) -> None:
        # batches 模式：历史句数上限 = 批数 × 本批句数（本批 3 句 × 2 批 = 6 句）
        t = make_single_translator()
        t.restore_context_mode = "batches"
        t.contextBatches = 2
        chain = []
        for i in range(10):
            s = CSentense(f"前文原文{i}", index=i)
            s.pre_dst = f"历史{i}"
            chain.append(s)
        for i in range(1, 10):
            chain[i].prev_tran = chain[i - 1]
        cur1 = CSentense("当前原文a", index=10)
        cur1.prev_tran = chain[-1]
        cur2 = CSentense("当前原文b", index=11)
        cur2.prev_tran = cur1
        cur3 = CSentense("当前原文c", index=12)
        cur3.prev_tran = cur2
        captured = {}

        async def fake_ask(self, **kwargs):
            captured["messages"] = kwargs.get("messages")
            self._last_chatbot_was_stream = False
            return echo_translation(kwargs.get("messages")), make_token()

        t.ask_chatbot = MethodType(fake_ask, t)
        cnt, _ = await t.translate([cur1, cur2, cur3], filename="f.json")
        self.assertEqual(cnt, 3)
        user_content = captured["messages"][1]["content"]
        # 注入最近 6 句（历史4~历史9），更早的历史0~历史3 不注入
        for i in range(4, 10):
            self.assertIn(f"历史{i}", user_content)
        for i in range(0, 4):
            self.assertNotIn(f"历史{i}", user_content)

    async def test_batches_mode_skips_failed_sentences_in_history(self) -> None:
        # batches 模式下失败句（(Failed) 前缀）不占历史窗口，向前顺延收集有效句
        t = make_single_translator()
        t.restore_context_mode = "batches"
        t.contextBatches = 2
        chain = []
        for i in range(10):
            s = CSentense(f"前文原文{i}", index=i)
            s.pre_dst = f"历史{i}"
            chain.append(s)
        for i in range(1, 10):
            chain[i].prev_tran = chain[i - 1]
        # 最近一句翻译失败：不占窗口，收集应向前顺延
        chain[9].pre_dst = "(Failed)前文原文9"
        cur1 = CSentense("当前原文a", index=10)
        cur1.prev_tran = chain[-1]
        cur2 = CSentense("当前原文b", index=11)
        cur2.prev_tran = cur1
        cur3 = CSentense("当前原文c", index=12)
        cur3.prev_tran = cur2
        captured = {}

        async def fake_ask(self, **kwargs):
            captured["messages"] = kwargs.get("messages")
            self._last_chatbot_was_stream = False
            return echo_translation(kwargs.get("messages")), make_token()

        t.ask_chatbot = MethodType(fake_ask, t)
        cnt, _ = await t.translate([cur1, cur2, cur3], filename="f.json")
        self.assertEqual(cnt, 3)
        user_content = captured["messages"][1]["content"]
        # 窗口 6 句顺延为 历史3~历史8；失败句与更早的历史0~2 不注入
        for i in range(3, 9):
            self.assertIn(f"历史{i}", user_content)
        for i in range(0, 3):
            self.assertNotIn(f"历史{i}", user_content)
        self.assertNotIn("(Failed)", user_content)

    async def test_lines_mode_zero_context_num_injects_nothing(self) -> None:
        # 回归：contextNum=0 时不再因「先收集后判界」注入 1 句，应为无历史（占位文本）
        t = make_single_translator()
        t.contextNum = 0
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
        self.assertIn(NO_HISTORY_PLACEHOLDER, user_content)


if __name__ == "__main__":
    unittest.main()
