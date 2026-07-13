"""
文本无损压缩模块（TextCompressor）

在发送全文给 LLM 做全局分析前，对 JSON 结构进行无损压缩以减少 token 消耗。

压缩原则（红线，不可违反）：
  - 绝不删除任何一行文本
  - 绝不修改任何 message 内容（包括其中的换行符、标点、语气词）
  - 绝不修改任何 name 字段
  - 绝不合并或改写对话内容

允许的压缩操作（仅结构性）：
  1. JSON 结构剥离：将 {"name": "x", "message": "y"} 转为 "x：y" 纯文本格式
  2. 精确重复行折叠：完全相同的 message 只保留首次出现，后续用占位符引用
  3. index 字段去除：索引号仅在需要时保留为行号标记
"""

from __future__ import annotations

import os
from typing import Dict, List, Set, Tuple

from GalTransl import LOGGER


class TextCompressor:
    """
    文本无损压缩器。

    只做结构性压缩（去除 JSON 开销 + 精确重复折叠），
    绝不动语义内容——不删行、不改 message、不改 name、不改换行符。
    """

    def __init__(self, max_chars: int = 80000):
        """
        Args:
            max_chars: 压缩后目标最大字符数。0 表示不限制。
                       如果结构性压缩后仍超限，会记录 WARNING 但不强制截断。
        """
        self.max_chars = max_chars

    # ── 公开接口 ──

    def compress(
        self,
        file_json_lists: Dict[str, list],
        game_name: str = "",
    ) -> str:
        """
        将 JSON 格式的待翻译文件无损压缩为纯文本。

        压缩策略（仅结构性，不碰语义）：
          1. JSON 结构剥离：去除 "name"/"message"/"index" 等键名和 JSON 语法字符
          2. 精确重复行折叠：message 完全相同的行，后续出现时仅输出 [同上 L{n}]
          3. 文件级分段：按文件组织，附文件名和行数统计

        Args:
            file_json_lists: {文件路径: [{name, message, index}, ...]}
            game_name: 游戏名称（可选）

        Returns:
            压缩后的纯文本
        """
        total_lines = sum(len(v) for v in file_json_lists.values())
        LOGGER.info(
            f"[TextCompressor] 开始压缩 {len(file_json_lists)} 个文件，"
            f"共 {total_lines} 行"
        )

        # Step 1: 收集角色名（用于头部统计）
        all_names = self._collect_all_names(file_json_lists)

        # Step 2: 逐文件压缩
        parts: List[str] = []
        # 头部
        if game_name:
            parts.append(f"# 游戏名称：{game_name}")
        parts.append(f"# 文件数：{len(file_json_lists)}")
        parts.append(
            f"# 角色列表：{'、'.join(sorted(all_names)) if all_names else '(未检测到)'}"
        )
        parts.append("")

        total_dup_lines = 0

        for file_path, json_list in file_json_lists.items():
            short_name = os.path.basename(file_path)
            compressed_block, dup_count = self._compress_single_file(
                json_list, short_name
            )
            parts.append(compressed_block)
            total_dup_lines += dup_count

        output = "\n".join(parts)

        LOGGER.info(
            f"[TextCompressor] 压缩完成：{total_lines} 行 → "
            f"{len(output)} 字符，折叠重复行 {total_dup_lines} 处"
        )

        # 如果超限，记录警告但不强制截断（截断=丢失信息=违反无损原则）
        if self.max_chars > 0 and len(output) > self.max_chars:
            LOGGER.warning(
                f"[TextCompressor] 压缩后 {len(output)} 字符仍超过目标 "
                f"{self.max_chars}（超出 {len(output) - self.max_chars} 字符）。"
                f"文本未截断，建议调大 maxInputChars 或分文件发送。"
            )

        return output

    def verify_compression(
        self,
        original: Dict[str, list],
        compressed: str,
    ) -> dict:
        """
        校验压缩完整性：确保所有 message 和 name 完整保留。

        检查项：
          - 每条原始 message 的完整文本在压缩结果中至少出现一次
          - 所有角色名完整保留

        Returns:
            {
                "total_messages": int,       # 原始 message 总数
                "unique_messages": int,      # 去重后数量
                "missing_messages": list,    # 丢失的 message（前 20 条）
                "total_names": int,          # 原始角色名总数
                "lost_names": list,          # 丢失的角色名
                "all_present": bool,         # 全部保留？
            }
        """
        # Step 1: 收集所有原始 message
        all_messages: List[str] = []
        for json_list in original.values():
            for item in json_list:
                if not isinstance(item, dict):
                    continue
                msg = str(item.get("message", ""))
                all_messages.append(msg)

        # Step 2: 检查每条 message 是否在压缩文本中出现
        missing: List[str] = []
        for msg in all_messages:
            if not msg.strip():
                continue  # 空 message 不检查
            if msg not in compressed:
                missing.append(msg)

        # Step 3: 检查角色名
        all_names = self._collect_all_names(original)
        lost_names = [
            n for n in all_names
            if n and n.strip() and n.strip() not in compressed
        ]

        all_present = len(missing) == 0 and len(lost_names) == 0

        result = {
            "total_messages": len(all_messages),
            "unique_messages": len(set(all_messages)),
            "missing_messages": missing[:20],
            "total_names": len(all_names),
            "lost_names": lost_names,
            "all_present": all_present,
        }

        if not all_present:
            if missing:
                LOGGER.error(
                    f"[TextCompressor] 校验失败：{len(missing)} 条 message "
                    f"在压缩结果中丢失！（示例：{missing[0][:80]}）"
                )
            if lost_names:
                LOGGER.error(
                    f"[TextCompressor] 校验失败：丢失角色名 "
                    f"{', '.join(lost_names[:10])}"
                )
        else:
            LOGGER.info(
                f"[TextCompressor] 校验通过：{len(all_messages)} 条 message、"
                f"{len(all_names)} 个角色名全部保留"
            )

        return result

    # ── 内部方法 ──

    @staticmethod
    def _collect_all_names(file_json_lists: Dict[str, list]) -> Set[str]:
        """收集所有文件中出现的角色名（不修改，只读取）。"""
        names: Set[str] = set()
        for json_list in file_json_lists.values():
            for item in json_list:
                if not isinstance(item, dict):
                    continue
                name = item.get("name", "")
                if name and isinstance(name, str) and name.strip():
                    names.add(name.strip())
                # AINIEe 格式兼容
                names_list = item.get("names", [])
                if isinstance(names_list, list):
                    for n in names_list:
                        if n and isinstance(n, str) and n.strip():
                            names.add(n.strip())
        return names

    @staticmethod
    def _compress_single_file(
        json_list: list,
        short_name: str,
    ) -> Tuple[str, int]:
        """
        对单个文件进行结构性压缩。

        操作：
          1. JSON → 纯文本（name：message 或仅 message）
          2. 精确重复检测：message 完全相同 → 用引用标记

        Returns:
            (压缩后的文本块, 折叠的重复行数)
        """
        lines: List[str] = []
        # 记录每条唯一 message 首次出现的行号
        msg_first_seen: Dict[str, int] = {}
        dup_count = 0

        lines.append(f"\n## {short_name}（{len(json_list)} 行）")

        for i, item in enumerate(json_list):
            if not isinstance(item, dict):
                continue

            msg = str(item.get("message", ""))
            name = str(item.get("name", "")).strip()
            line_no = i + 1  # 1-based 文件内行号

            # 精确重复折叠：message 文本完全相同
            if msg and msg in msg_first_seen:
                ref_line = msg_first_seen[msg]
                lines.append(f"[{line_no}] ^ 同上 L{ref_line}")
                dup_count += 1
                continue

            # 记录首次出现
            if msg:
                msg_first_seen[msg] = line_no

            # 输出：保留原始换行符（message 中的 \n 直接输出为真实换行）
            if name:
                lines.append(f"[{line_no}] {name}：{msg}")
            else:
                lines.append(f"[{line_no}] {msg}")

        return "\n".join(lines), dup_count
