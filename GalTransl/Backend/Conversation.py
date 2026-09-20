"""多轮对话管理

从翻译后端抽出的多轮对话状态与生命周期：按文件隔离的 messages 历史、
历史裁剪、失败后的「强制首轮重建」标记。翻译后端与修复轮经 Mixin 复用，
使多轮对话能力与翻译流程解耦——后端只在多轮对话模式下消费这些状态。
"""

from typing import List

from GalTransl.ConfigHelper import CProjectConfig


class MultiRoundChatMixin:
    """多轮对话状态管理 Mixin。

    职责：
    - 按文件名隔离的对话历史（messages[0]=system，其后 user/assistant 交替）
    - 历史裁剪（gpt.multiRoundMaxHistory，0=不裁剪）
    - 失败重试后的强制首轮重建标记

    使用方需在 __init__ 中调用 ``_init_multi_round_chat`` 完成状态初始化，
    并提供 ``self.system_prompt`` 属性（_ensure_conversation 初始化首条消息用）。
    """

    def _init_multi_round_chat(self, config: CProjectConfig) -> None:
        """初始化多轮对话状态。

        Args:
            config: 项目配置对象，读取 gpt.multiRoundMaxHistory。
        """
        # 多轮对话历史：按文件名隔离，messages[0]=system，其后 user/assistant 交替
        self.conversations: dict[str, list] = {}

        # 标记下一批次须以首轮方式构建（失败重试耗尽后设置，恢复多轮连续性）
        self._force_first_round_files: set[str] = set()

        # 多轮历史最大保留轮次数（0=不裁剪）；单独解析避免 _coerce_positive_int 把 0 抬为 1
        raw_multi_round = config.getKey("gpt.multiRoundMaxHistory")
        if raw_multi_round is None:
            self.multi_round_max_history = 0
        else:
            try:
                self.multi_round_max_history = int(raw_multi_round)
            except (TypeError, ValueError):
                self.multi_round_max_history = 0

    def _ensure_conversation(self, filename: str) -> list:
        """
        获取（或初始化）指定文件的对话历史。

        初始化时仅包含 system 消息；真正的第一轮 user 消息在 translate 中构建。

        Args:
            filename: 文件名

        Returns:
            该文件对应的 messages 列表（会被原地修改/替换）
        """
        if filename not in self.conversations:
            self.conversations[filename] = [
                {"role": "system", "content": self.system_prompt}
            ]
        return self.conversations[filename]

    def _trim_conversation(self, messages: List[dict]) -> List[dict]:
        """
        裁剪过长的对话历史以控制 token 消耗。

        始终保留 system 消息（index 0）与第一轮 user 消息（index 1，含剧情元数据），
        仅裁剪中间的历史轮次，保留最近的若干轮。
        裁剪轮数由配置 gpt.multiRoundMaxHistory 控制（0=不裁剪，默认 0）。

        Args:
            messages: 完整 messages 列表

        Returns:
            裁剪后的 messages 列表
        """
        max_turns = self.multi_round_max_history
        if max_turns <= 0:
            return messages
        # system + 第一轮 user 必须保留
        if len(messages) <= 3:
            return messages
        head = messages[:2]
        tail = messages[2:]
        keep = max_turns * 2  # 每轮 = user + assistant
        if len(tail) > keep:
            tail = tail[-keep:]
        return head + tail

    def reset_conversation(self, filename: str = "") -> None:
        """
        重置会话上下文。

        清空指定文件的多轮对话历史；filename 为空时清空全部。
        注：剧情元数据（file_metadata_map）默认保留，避免重复注入；
        若需一并清除可手动 del。

        Args:
            filename: 要重置的文件名，为空时重置所有
        """
        if filename == "":
            self.conversations = {}
            self._force_first_round_files = set()
        else:
            self.conversations.pop(filename, None)
            self._force_first_round_files.discard(filename)
