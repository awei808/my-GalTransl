"""MultiRoundChatMixin 单元测试

覆盖：
  - _init_multi_round_chat 的 multiRoundMaxHistory 配置解析（None/0/非法值/正常值）
  - _ensure_conversation 的按文件隔离与复用
  - _trim_conversation 的保留策略（system+首轮 user 必留、按轮数裁尾、0=不裁剪）
  - reset_conversation 的全清/单文件清
"""

import unittest
from types import SimpleNamespace

from GalTransl.Backend.Conversation import MultiRoundChatMixin


def make_mixin(multi_round_max_history=None) -> MultiRoundChatMixin:
    """直接实例化 Mixin 并打桩 system_prompt / 配置读取，不依赖后端初始化链。"""
    mixin = MultiRoundChatMixin()
    mixin.system_prompt = "SYSTEM_PROMPT"

    def get_key(key: str, default=None):
        if key == "gpt.multiRoundMaxHistory":
            return multi_round_max_history
        return default

    mixin._init_multi_round_chat(SimpleNamespace(getKey=get_key))
    return mixin


class InitMultiRoundChatTests(unittest.TestCase):
    def test_max_history_defaults_to_zero_when_unconfigured(self) -> None:
        self.assertEqual(make_mixin(None).multi_round_max_history, 0)

    def test_max_history_accepts_zero_without_coercion_to_one(self) -> None:
        self.assertEqual(make_mixin(0).multi_round_max_history, 0)

    def test_max_history_accepts_positive_value(self) -> None:
        self.assertEqual(make_mixin(3).multi_round_max_history, 3)

    def test_max_history_falls_back_to_zero_on_invalid_value(self) -> None:
        self.assertEqual(make_mixin("abc").multi_round_max_history, 0)

    def test_init_creates_isolated_conversation_and_force_first_round_state(self) -> None:
        mixin = make_mixin()
        self.assertEqual(mixin.conversations, {})
        self.assertEqual(mixin._force_first_round_files, set())


class EnsureConversationTests(unittest.TestCase):
    def test_first_access_creates_system_only_history(self) -> None:
        mixin = make_mixin()
        conv = mixin._ensure_conversation("a.json")
        self.assertEqual(conv, [{"role": "system", "content": "SYSTEM_PROMPT"}])

    def test_same_file_reuses_history_and_files_are_isolated(self) -> None:
        mixin = make_mixin()
        conv_a = mixin._ensure_conversation("a.json")
        conv_a.append({"role": "user", "content": "q"})
        mixin._ensure_conversation("b.json")
        self.assertIs(mixin._ensure_conversation("a.json"), conv_a)
        self.assertEqual(len(mixin.conversations["b.json"]), 1)
        self.assertEqual(len(mixin.conversations["a.json"]), 2)


class TrimConversationTests(unittest.TestCase):
    def test_zero_max_history_returns_messages_unchanged(self) -> None:
        mixin = make_mixin(0)
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]
        self.assertIs(mixin._trim_conversation(messages), messages)

    def test_short_history_is_not_trimmed(self) -> None:
        mixin = make_mixin(2)
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
        self.assertIs(mixin._trim_conversation(messages), messages)

    def test_trim_keeps_system_and_first_user_and_recent_turns(self) -> None:
        mixin = make_mixin(2)
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u0"}]
        for i in range(1, 5):
            messages.append({"role": "assistant", "content": f"a{i}"})
            messages.append({"role": "user", "content": f"u{i}"})
        trimmed = mixin._trim_conversation(messages)
        # 保留头两条（system + 首轮 user）+ 最近 2 轮（各 user+assistant）
        self.assertEqual(len(trimmed), 6)
        self.assertEqual(trimmed[0]["content"], "s")
        self.assertEqual(trimmed[1]["content"], "u0")
        self.assertEqual([m["content"] for m in trimmed[2:]], ["a3", "u3", "a4", "u4"])


class ResetConversationTests(unittest.TestCase):
    def test_reset_single_file_keeps_others(self) -> None:
        mixin = make_mixin()
        mixin._ensure_conversation("a.json")
        mixin._ensure_conversation("b.json")
        mixin._force_first_round_files.add("a.json")
        mixin.reset_conversation("a.json")
        self.assertNotIn("a.json", mixin.conversations)
        self.assertIn("b.json", mixin.conversations)
        self.assertNotIn("a.json", mixin._force_first_round_files)

    def test_reset_all_clears_every_state(self) -> None:
        mixin = make_mixin()
        mixin._ensure_conversation("a.json")
        mixin._force_first_round_files.add("a.json")
        mixin.reset_conversation()
        self.assertEqual(mixin.conversations, {})
        self.assertEqual(mixin._force_first_round_files, set())


if __name__ == "__main__":
    unittest.main()
