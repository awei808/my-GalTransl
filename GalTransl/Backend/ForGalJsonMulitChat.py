from __future__ import annotations

import json
import os
import asyncio
import re
from random import choice
from typing import Optional, List, Set

from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProxyPool, CProjectConfig
from GalTransl import LOGGER
from GalTransl.i18n import get_text
from GalTransl.CSentense import CSentense, CTransList
from GalTransl.Cache import save_transCache_to_json
from GalTransl.Dictionary import CGptDict
from GalTransl.Utils import extract_code_blocks, fix_quotes
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_SYSTEM_PROMPT,
    FORGAL_JSON_TRANS_PROMPT,
    H_WORDS_LIST,
)
from GalTransl.Backend.BaseTranslate import BaseTranslate
from openai._types import NOT_GIVEN


def detect_line_break_symbol(src_text: str) -> str:
    """
    检测原文中的换行符类型，返回用于后处理还原的换行符标记。

    将换行符判定集中到模块级函数，并在整批评句上**仅判定一次**：
    避免原来逐句判定时，因后续句子不含换行符而把已确定的 n_symbol 覆盖，
    导致解析阶段的换行符还原（<br> → 原换行符）出现错乱。

    优先级（与原逻辑一致）：
      "\\r\\n"（字面转义串，galgame 脚本中常见） > 实际 "\\r\\n" >
      "\\n"（字面转义串） > 实际 "\\n"

    Args:
        src_text: 待检测的完整原文（通常为整批评句的拼接）

    Returns:
        检测到的换行符标记；未检测到则返回 ""
    """
    if "\\r\\n" in src_text:
        return "\\r\\n"
    elif "\r\n" in src_text:
        return "\r\n"
    elif "\\n" in src_text:
        return "\\n"
    elif "\n" in src_text:
        return "\n"
    return ""


def detect_batch_line_break_symbol(post_src_list: List[str]) -> str:
    """
    对整批评句**仅判定一次**换行符类型，返回用于后处理还原的换行符标记。

    采用「逐句检测取首命中」，而**不是**把句子用 "\\n" 拼接后再检测：
    拼接分隔符 "\\n" 会混入检测串，使「字面 <br> 约定」的源（句子内容里是
    <br> 而非真实换行）被误判成 "\\n"，进而在解码阶段把 <br> 错误还原成真实
    换行，破坏源换行约定。

    逐句取首命中既保留了「整批统一单一 n_symbol」的语义，又不会引入拼接
    产生的伪换行符。

    Args:
        post_src_list: 当前批次所有句子的原文列表

    Returns:
        检测到的换行符标记；整批均无换行符则返回 ""
    """
    for src in post_src_list:
        s = detect_line_break_symbol(src)
        if s:
            return s
    return ""


class PlotMetadata:
    """剧情元数据类

    用于在多轮对话的第一轮向 LLM 提供文件级的剧情上下文，
    帮助模型在后续轮次中保持人物译名、语气与剧情基调的一致性。

    属性（与 gt_input 中的 ``PlotMetadata.json`` 顶层键一一对应；
    类内使用英文属性名，JSON 数据键保持中文）：

        id        标识：剧情元数据的字符串标识（可空）
        character 角色：角色/人物设定（字符串或字符串列表）
        costume   服装：角色服装/外观描述（字符串）
        plot      剧情：剧情梗概/背景（字符串）
        tags      标签：题材/关键词标签（字符串或字符串列表）
    """

    def __init__(
        self,
        id: object = "",
        character: object = "",
        costume: object = "",
        plot: object = "",
        tags: object = None,
    ):
        """
        初始化剧情元数据

        :param id: 剧情元数据标识（str，可空）
        :param character: 角色设定（str 或 list[str]），对应 JSON 键「角色」
        :param costume: 服装/外观描述（str），对应 JSON 键「服装」
        :param plot: 剧情梗概（str），对应 JSON 键「剧情」
        :param tags: 标签（str 或 list[str]），对应 JSON 键「标签」
        """
        self.id = id if id is not None else ""
        self.character = character
        self.costume = costume
        self.plot = plot
        self.tags = tags if tags is not None else []

    def __repr__(self):
        return (
            f"PlotMetadata(id={self.id!r}, "
            f"character={self.character!r}, "
            f"costume={self.costume!r}, "
            f"plot={self.plot!r}, "
            f"tags={self.tags!r})"
        )


def load_plot_metadata(projectConfig: "CProjectConfig"):
    """从 gt_input/PlotMetadata.json 载入剧情元数据。

    该函数与 :class:`PlotMetadata` 同文件，且本模块顶部已 ``import json``，
    因此不会在调用方（如 Frontend.LLMTranslate）额外引入 json 依赖。

    文件不存在或解析失败时返回 None；缺失字段按空值处理。
    「角色」「标签」允许是字符串或字符串列表，「服装」「剧情」「id」为字符串（可空）。
    """
    input_dir = projectConfig.getInputPath()
    path = os.path.join(input_dir, "PlotMetadata.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        LOGGER.warning(f"读取 PlotMetadata.json 失败，已忽略剧情元数据：{e}")
        return None
    if not isinstance(data, dict):
        LOGGER.warning("PlotMetadata.json 根元素不是对象，已忽略剧情元数据")
        return None

    def _to_list(value):
        if value is None:
            return []
        if isinstance(value, list):
            return [str(x) for x in value]
        return [str(value)]

    return PlotMetadata(
        id=data.get("id") or "",
        character=_to_list(data.get("角色")),
        costume=data.get("服装") or "",
        plot=data.get("剧情") or "",
        tags=_to_list(data.get("标签")),
    )


def _find_plot_metadata_file(input_dir: str) -> Optional[str]:
    """在翻译项目目录中查找 PlotMetadata.json。

    GalTransl 的「新建翻译项目」会生成一个项目根目录，其中 ``gt_input`` 是
    待翻译输入文件夹，``PlotMetadata.json`` 与该文件夹同级、位于**项目根目录**
    （即 ``gt_input`` 的父目录）。因此按以下优先级查找：

    1. ``<input_dir>/../PlotMetadata.json`` —— 项目根（标准位置，首选）
    2. ``<input_dir>/PlotMetadata.json`` —— 直接放在 gt_input 内（兼容布局）
    3. 向上再查找 2 级 —— 非标准布局的安全网（不推荐依赖）

    Args:
        input_dir: 输入目录（通常为 projectConfig.getInputPath()，即 gt_input）

    Returns:
        找到的 PlotMetadata.json 路径；均未找到则返回 None
    """
    # 1) 项目根目录（gt_input 的父目录）—— 标准位置
    project_root = os.path.dirname(input_dir)
    cand = os.path.join(project_root, "PlotMetadata.json")
    if os.path.exists(cand):
        return cand
    # 2) gt_input 自身（兼容布局）
    cand = os.path.join(input_dir, "PlotMetadata.json")
    if os.path.exists(cand):
        return cand
    # 3) 向上再查 2 级（非标准布局兜底）
    cur = project_root
    for _ in range(2):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cand = os.path.join(parent, "PlotMetadata.json")
        if os.path.exists(cand):
            return cand
        cur = parent
    return None


def load_plot_metadata_map(projectConfig: "CProjectConfig") -> dict:
    """从翻译项目的 PlotMetadata.json 载入「文件名 -> 剧情元数据」映射。

    PlotMetadata.json 位于**翻译项目根目录**（即 gt_input 的父目录，与 gt_input
    同级），由「新建翻译项目」生成。文件本身为 **JSON 数组**，每个元素形如
    ``{"id": "文件名", "角色": [...], "服装": "...", "剧情": "...", "标签": [...]}``，
    其中 ``id`` 对应 gt_input 中的一个待翻译文件名（如 ``02_kar_god01.txt.json``）。
    本函数将其解析为 ``{文件名: PlotMetadata}`` 字典，供后端按文件注入对应剧情元数据。

    兼容单对象格式（旧 schema）：此时以 ``id`` 或空串为键，得到只含一项的字典。
    文件不存在、解析失败、或根元素既非对象也非数组时返回空字典。

    注意：``id`` 可能带分批后缀干扰（如 ``file_0``），匹配时由调用方负责剥离，
    本函数仅原样以 ``id`` 为键。
    """
    path = _find_plot_metadata_file(projectConfig.getInputPath())
    if path is None:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        LOGGER.warning(f"读取 PlotMetadata.json 失败，已忽略剧情元数据：{e}")
        return {}

    def _to_list(value):
        if value is None:
            return []
        if isinstance(value, list):
            return [str(x) for x in value]
        return [str(value)]

    result: dict = {}
    if isinstance(data, dict):
        # 旧 schema：单对象，作为仅含一项的映射
        fid = data.get("id") or ""
        result[fid] = PlotMetadata(
            id=fid,
            character=_to_list(data.get("角色")),
            costume=data.get("服装") or "",
            plot=data.get("剧情") or "",
            tags=_to_list(data.get("标签")),
        )
    elif isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            fid = item.get("id") or ""
            if not fid:  # 无 id 无法与文件对应，跳过
                continue
            result[fid] = PlotMetadata(
                id=fid,
                character=_to_list(item.get("角色")),
                costume=item.get("服装") or "",
                plot=item.get("剧情") or "",
                tags=_to_list(item.get("标签")),
            )
    else:
        LOGGER.warning("PlotMetadata.json 根元素既非对象也非数组，已忽略剧情元数据")
    return result


"""
ForGalJsonMulitChat - 基于 JSON-line 格式的多轮对话视觉小说脚本翻译后端

与 ForGalJsonTranslate 的核心差异：
本后端采用「多轮对话（multi-round chat）」模式对接 API
每次 API 调用都会把完整的 messages 历史（system + 之前各轮 user/assistant）一并发出，由模型自行维持上下文。

数据流程（类内方法亦按此顺序排列）：
1. 输入内容处理、拼接
2. 提示词拼接
3. 传递提示词和输入内容至 API
4. 返回结果解析和处理

多轮对话的关键约定：
  - 第一轮对话：在 user 消息中写入「翻译提示词 + 剧情元数据(PlotMetadata) + 本批待译句子」。
  - 后续轮次：无需再重复翻译提示词与剧情元数据，只发送「待翻译句子（带 sig 的 jsonline）」。
  - 历史译文由多轮对话本身携带，不再通过 [history_result] 注入。
"""


class ForGalJsonMulitChat(BaseTranslate):
    """
    ForGalJsonMulitChat - 基于 JSON-line 格式、采用多轮对话的视觉小说脚本翻译后端

    核心流程：
    1. 将 CTransList 中的每个 CSentense 编码为 "3位随机签名|JSON对象" 的 jsonline 行
    2. 第一轮对话把 jsonline + 翻译提示词 + 剧情元数据(PlotMetadata) 写入 user 消息，后续轮次仅发送 jsonline，借助多轮上下文保持前后一致
    3. 解析 LLM 返回的 jsonline 结果，校验签名/id/字段完整性
    4. 将翻译结果写回 CSentense.pre_dst

    继承自 BaseTranslate，复用客户端构建、API 调用、缓存读写、动态句数调节等通用逻辑。
    """

    # 用于生成 jsonline 签名的字符集，每个句子分配 3 位随机签名用于防串行校验
    _SIGCHARS = "abcdefghijklmnopqrstuvwxyz0123456789"

    # ======================================================================
    # 1. 输入内容处理、拼接
    # ======================================================================

    def _encode_sig_jsonline(self, sig: str, obj: dict) -> str:
        """
        将句子对象编码为带签名的 jsonline 格式

        格式："{sig}|{json}"
        例如："a1b|{\"id\":0,\"name\":\"小明\",\"src\":\"こんにちは\"}"

        Args:
            sig: 3位随机签名，用于后续防串行校验
            obj: 待编码的句子对象，包含 id/name/src 等字段

        Returns:
            编码后的 jsonline 行
        """
        return f"{sig}|" + json.dumps(obj, ensure_ascii=False)

    def _build_input_jsonlines(
        self,
        trans_list: CTransList,
        proofread: bool,
        filename: str,
    ) -> tuple:
        """
        输入内容处理与拼接（流程第 1 步）。

        遍历待译句子，完成：
          - 取说话人并清洗（去除换行/制表符，避免破坏 jsonline）
          - 整批统一判定换行符（模块级 detect_line_break_symbol，仅一次）
          - 将 \\t → [t]、换行符 → <br>（LLM 友好格式，避免与 jsonline 冲突）
          - 为每句生成唯一 3 位签名 sig（防串行校验）
          - 翻译模式构建 {id,name,src}；校对模式额外携带 dst
          - 无说话人时删除 name 字段（表示旁白/独白）
        最终将所有 jsonline 行拼接为待译输入文本。

        Args:
            trans_list: 本批评句
            proofread: 是否校对模式
            filename: 文件名（预留，当前用于潜在扩展）

        Returns:
            (input_list, sig_list, n_symbol, input_src)
            - input_list: 各行 "sig|{...}" 字符串列表
            - sig_list: 与 input_list 一一对应的签名列表
            - n_symbol: 整批统一检测到的换行符标记（"" 表示无）
            - input_src: 拼接后的待译输入文本
        """
        input_list: List[str] = []
        sig_list: List[str] = []

        # 整批评句上仅判定一次换行符（避免逐句判定时后续句子不含换行符
        # 而覆盖已确定的标记，详见模块级 detect_*_symbol 说明）。
        # 采用「逐句检测取首命中」，不能用 "\n".join(...) 拼接后再检测——
        # join 分隔符 "\n" 会混入检测串，使「字面 <br> 约定」的源被误判成 "\n"，
        # 进而在解码阶段把 <br> 错误还原成真实换行，破坏源换行约定。
        n_symbol = detect_batch_line_break_symbol(
            [trans.post_src for trans in trans_list]
        )

        for trans in trans_list:
            # 获取说话人名称，去除换行和制表符，避免破坏 jsonline 格式
            speaker_name = trans.get_speaker_name()
            speaker = speaker_name if speaker_name else "null"
            speaker = (
                speaker.replace("\r\n", "").replace("\t", "").replace("\n", "")
            )
            src_text = trans.post_src

            # 将制表符和换行符替换为 LLM 友好格式
            src_text = src_text.replace("\t", "[t]")
            if n_symbol:
                src_text = src_text.replace(n_symbol, "<br>")

            # 生成唯一的 3 位随机签名，用于后续防串行校验
            while True:
                sig = "".join(choice(self._SIGCHARS) for _ in range(3))
                if sig not in sig_list:
                    break
            sig_list.append(sig)

            # 根据模式构建 JSON 对象
            if not proofread:
                # 翻译模式：仅包含 id/name/src
                tmp_obj = {
                    "id": trans.index,
                    "name": speaker,
                    "src": src_text,
                }
            else:
                # 校对模式：额外携带 dst（已有译文），让 LLM 在已有基础上校对
                tmp_obj = {
                    "id": trans.index,
                    "name": speaker,
                    "src": src_text,
                    "dst": (
                        trans.pre_dst if trans.proofread_zh == "" else trans.proofread_zh
                    ),
                }

            # 无说话人时删除 name 字段，表示旁白/独白
            if tmp_obj["name"] == "null":
                del tmp_obj["name"]

            input_list.append(self._encode_sig_jsonline(sig, tmp_obj))

        # 拼接所有 jsonline 行为最终输入文本
        input_src = "\n".join(input_list)
        return input_list, sig_list, n_symbol, input_src

    # ======================================================================
    # 2. 提示词拼接
    # ======================================================================

    def _format_plot_metadata_block(self, metadata: PlotMetadata) -> str:
        """
        将剧情元数据格式化为提示词附加段落（第一轮对话中使用）。

        Args:
            metadata: 剧情元数据对象（属性：id/角色/服装/剧情/标签）

        Returns:
            追加在翻译提示词之后的剧情元数据文本块
        """
        def _join(value: object) -> str:
            """把 str 或 list[str] 规范为「、」分隔串；空值返回 None。"""
            if value is None or value == "":
                return None
            if isinstance(value, list):
                items = [str(x).strip() for x in value if str(x).strip() != ""]
                return "、".join(items) if items else None
            s = str(value).strip()
            return s if s else None

        id_line = f"id: {metadata.id}\n" if metadata.id else ""
        character = _join(metadata.character) or "无"
        costume = _join(metadata.costume) or "无"
        plot = _join(metadata.plot) or "无"
        tags = _join(metadata.tags) or "无"
        return (
            "\n<plot_metadata>\n"
            f"{id_line}"
            f"角色: {character}\n"
            f"服装: {costume}\n"
            f"剧情: {plot}\n"
            f"标签: {tags}\n"
            "</plot_metadata>\n"
            "请参考上述 <plot_metadata> 中的剧情元数据：保持人物译名"
            "（与「角色」列表一致）、语气与剧情基调前后统一。"
            "后续轮次将只提供待翻译句子，无需重复翻译要求。\n"
        )

    def _build_round_user_content(
        self,
        conv: list,
        input_src: str,
        gptdict: str,
        filename: str,
        is_first_round: bool,
    ) -> str:
        """
        提示词拼接（流程第 2 步）。

        第一轮对话：构建完整翻译提示词（替换 [Input]/[Glossary]/[translation_guideline]），
          剧情元数据(PlotMetadata) 段通过模板中的 [plot_metadata] 占位符注入，
          位于 [translation_guideline]/[Glossary] 之后、[Input] 之前；
        后续轮次：返回待译 jsonline，并附上本批次按需生成的术语表(gptdict) 短块，
          复用多轮上下文的同时保证每批都能看到本批出现的专有名词/人设解释。
        多轮模式下历史由对话本身携带，[history_result] 置空。

        Args:
            conv: 该文件的对话历史（已由调用方获取，用于一致性校验）
            input_src: 拼接后的待译 jsonline 文本
            gptdict: 术语表
            filename: 文件名
            is_first_round: 是否为第一轮对话

        Returns:
            本轮要发送的 user 消息内容
        """
        if is_first_round:
            # 第一轮：构建完整翻译提示词（含 [Input]/[Glossary]/[translation_guideline] 替换）
            # 剧情元数据经模板 [plot_metadata] 占位符注入，位于翻译规范之后、[Input] 之前。
            metadata = self._resolve_plot_metadata(filename)
            metadata_block = (
                self._format_plot_metadata_block(metadata)
                if metadata is not None
                else ""
            )
            prompt_req = self._build_prompt_request(
                input_src, gptdict, plot_metadata=metadata_block
            )
            # 多轮模式下历史由对话本身携带，[history_result] 置为 None
            prompt_req = self._apply_history_result(prompt_req, filename)
            user_content = prompt_req
        else:
            # 后续轮次：主要只发送待翻译句子（带 sig 的 jsonline），复用多轮上下文。
            if gptdict:
                user_content = gptdict + "\n以下是本批次待翻译内容：\n" + input_src
            else:
                user_content = input_src
        LOGGER.debug(
            f"[{filename}] 本轮 user 提示词（{'首轮' if is_first_round else '续轮'}）:\n{user_content}"
        )
        return user_content

    # ======================================================================
    # 3. 传递提示词和输入内容至 API
    # ======================================================================

    async def _call_llm(
        self,
        call_messages: list,
        filename: str,
        idx_tip: str,
        stream_line_callback,
    ) -> tuple:
        """
        传递提示词和输入内容至 API（流程第 3 步）。

        将包含完整历史的 messages 一次性发送给 API（多轮对话模式），
        流式模式下通过 stream_line_callback 边收边解析。

        Args:
            call_messages: 完整 messages（历史 + 本轮 user，可选 assistant 预填充）
            filename: 文件名
            idx_tip: 索引提示（用于日志/错误定位）
            stream_line_callback: 流式解析回调（由 translate 绑定本次调用上下文）

        Returns:
            (raw_resp, token)
        """
        return await self.ask_chatbot(
            messages=call_messages,
            file_name=f"{filename}:{idx_tip}",
            stream_line_callback=stream_line_callback,
        )

    # ======================================================================
    # 4. 返回结果解析和处理
    # ======================================================================

    def _parse_stream_lines(
        self,
        lines: list,
        is_final_chunk: bool,
        *,
        trans_list: CTransList,
        n_symbol: str,
        sig_list: List[str],
        key_name: str,
        emit_runtime_success: bool,
        filename: str,
        parsed_result_trans_list: list,
        cursor: dict,
    ) -> bool:
        """
        流式结果解析（流程第 4 步，独立方法）。

        由 ask_chatbot 在流式输出时逐批调用，对收到的若干行做 sig 定位与
        逐行校验，成功则写入 parsed_result_trans_list。解析失败时记录错误信息
        到 cursor["error"] 并返回 False，触发流式中断。

        Args:
            lines: 本批次收到的文本行列表
            is_final_chunk: 是否为最后一批（本实现无需特殊处理）
            trans_list / n_symbol / sig_list / key_name: 与 translate 调用一致的上下文
            emit_runtime_success: 是否向服务端上报成功（翻译模式为 True）
            filename: 文件名（用于运行态成功上报）
            parsed_result_trans_list: 累积的解析结果列表（原地追加）
            cursor: 流式游标 {"i","success_count","started","error"}

        Returns:
            是否继续流式（False 表示请求中断）
        """
        del is_final_chunk  # 当前实现无需区分末批
        if cursor.get("error"):
            return False
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            # 跳过 markdown 代码块标记
            if line.startswith("```"):
                continue
            # 定位第一个有效 jsonline 行的起始位置
            if not cursor["started"]:
                sig_start = re.search(r"\b[a-z0-9]{3}\|\{\"id\"", line)
                if sig_start:
                    line = line[sig_start.start() :]
                    cursor["started"] = True
                else:
                    continue
            line = fix_quotes(line)
            parse_ok, parse_error = self._parse_jsonline_result_line(
                line,
                trans_list,
                getattr(self, "_last_chatbot_model_name", ""),
                n_symbol,
                key_name,
                cursor,
                parsed_result_trans_list,
                filename=filename,
                emit_runtime_success=emit_runtime_success,
                sig_list=sig_list,
            )
            if not parse_ok:
                cursor["error"] = parse_error
                return False
        return True

    def _parse_non_stream_text(
        self,
        result_text: str,
        trans_list: CTransList,
        token,
        n_symbol: str,
        key_name: str,
        sig_list: List[str],
        filename: str,
    ) -> tuple:
        """
        非流式结果解析（流程第 4 步，独立方法）。

        对完整响应文本逐行调用逐行校验，遇到首处失败即中断并返回错误信息。

        Args:
            result_text: 已做 </think> 截断、代码块提取、sig 定位后的响应文本
            trans_list: 本批评句
            token: API 返回 token（取其 model_name）
            n_symbol: 换行符标记
            key_name: 目标字段名（dst / newdst）
            sig_list: 签名列表（防串行校验）
            filename: 文件名（用于运行态成功上报）

        Returns:
            (result_trans_list, success_count, error_message, last_i)
            - result_trans_list: 成功解析结果列表
            - success_count: 成功句数
            - error_message: 非空表示解析失败原因
            - last_i: 最后一个成功句在 trans_list 中的下标
        """
        i = -1
        success_count = 0
        result_trans_list = []
        error_message = ""
        for line in result_text.splitlines():
            parse_ok, parse_error = self._parse_jsonline_result_line(
                line,
                trans_list,
                getattr(token, "model_name", ""),
                n_symbol,
                key_name,
                {"i": i, "success_count": success_count},
                result_trans_list,
                filename=filename,
                emit_runtime_success=False,
                sig_list=sig_list,
            )
            if not parse_ok:
                error_message = parse_error
                break
            i += 1
            success_count += 1
            if i >= len(trans_list) - 1:
                break
        return result_trans_list, success_count, error_message, i

    def _parse_jsonline_result_line(
        self,
        line: str,
        trans_list: CTransList,
        model_name: str,
        n_symbol: str,
        key_name: str,
        cursor: dict,
        result_trans_list: list,
        filename: str = "",
        emit_runtime_success: bool = False,
        emitted_success_indices: Optional[Set[int]] = None,
        sig_list: Optional[List[str]] = None,
    ):
        if "|" not in line:
            return False, f"jsonline缺少sig前缀：{line}"
        line_sig, line = line.split("|", 1)
        try:
            line_json = json.loads(line)
        except Exception:
            return False, f"json无法解析行：{line}"

        cursor["i"] += 1
        i = cursor["i"]
        if (
            isinstance(line_json, dict) == False
            or "id" not in line_json
            or type(line_json["id"]) != int
            or i > len(trans_list) - 1
        ):
            return False, f"{line}句无法解析"

        line_id = line_json["id"]
        if sig_list is not None:
            if line_sig != sig_list[i]:
                return False, f"第{trans_list[i].index}句疑似串行：期望{sig_list[i]}，实际{line_sig}"
        if line_id != trans_list[i].index:
            return False, f"{line_id}句id未对应{trans_list[i].index}"

        if key_name not in line_json or type(line_json[key_name]) != str:
            return False, f"第{trans_list[i].index}句找不到{key_name}"

        line_dst = line_json[key_name]
        if trans_list[i].post_src != "" and line_dst == "":
            return False, f"第{trans_list[i].index}句空白"
        if "�" in line_dst:
            return False, f"第{trans_list[i].index}句包含乱码：{line_dst}"

        line_dst = self._normalize_parsed_translation_text(
            line_dst, trans_list[i], n_symbol
        )

        return self._append_parsed_translation_result(
            trans_list[i],
            line_dst,
            model_name,
            cursor,
            result_trans_list,
            filename=filename,
            emit_runtime_success=emit_runtime_success,
            emitted_success_indices=emitted_success_indices,
            result_index=i,
        )

    def _handle_parse_result(
        self,
        *,
        raw_resp,
        token,
        trans_list: CTransList,
        n_symbol: str,
        sig_list: List[str],
        is_stream: bool,
        stream_error_msg: str,
        stream_cursor: dict,
        stream_parsed_list: list,
        idx_tip: str,
        filename: str,
        proofread: bool,
        call_messages: list,
        prefill_used: bool,
    ) -> tuple:
        """
        解析结果处理（流程第 4 步收尾）。

        根据流式/非流式路径统一判定是否解析成功，失败时记录运行时错误并做
        失败兜底（标 (Failed)）；成功时将本轮 assistant 回复追加进多轮对话历史。

        Args:
            raw_resp: API 原始返回文本
            token: API 返回 token
            trans_list / n_symbol / sig_list: 与 translate 一致的上下文
            is_stream: 是否为流式响应
            stream_error_msg: 流式解析错误信息（非流式为空）
            stream_cursor: 流式游标（含已处理行位置 i）
            stream_parsed_list: 流式已解析结果列表
            idx_tip / filename / proofread: 上下文
            call_messages: 本轮完整 messages
            prefill_used: 是否使用了 jailbreak 预填充

        Returns:
            (success_count, result_trans_list)
        """
        # 统一做 </think> 截断、代码块提取、sig 定位与引号修正
        result_text = raw_resp or ""
        if "</think>" in result_text:
            result_text = result_text.split("</think>")[-1]
        if "```json" in result_text:
            lang_list, code_list = extract_code_blocks(result_text)
            if len(lang_list) > 0 and len(code_list) > 0:
                result_text = code_list[0]
        sig_start = re.search(r"\b[a-z0-9]{3}\|\{\"id\"", result_text)
        if sig_start:
            result_text = result_text[sig_start.start() :]
        result_text = fix_quotes(result_text)

        key_name = "dst" if not proofread else "newdst"

        i = -1
        success_count = 0
        result_trans_list = []
        error_flag = False
        error_message = ""

        if result_text == "":
            error_message = "输出为空/被拦截"
            error_flag = True

        if is_stream:
            # 流式模式：结果由 _parse_stream_lines 边收边解析填入
            if stream_error_msg:
                error_message = stream_error_msg
                error_flag = True
            result_trans_list = stream_parsed_list
            success_count = len(stream_parsed_list)
            i = stream_cursor["i"]
        else:
            # 非流式模式：对完整响应逐行解析
            (
                result_trans_list,
                success_count,
                error_message,
                i,
            ) = self._parse_non_stream_text(
                result_text, trans_list, token, n_symbol, key_name, sig_list, filename
            )
            if error_message:
                error_flag = True

        # 真实翻译成功句数（不含失败兜底填充），用于判断是否「整批解析失败」。
        # 失败兜底路径会把 success_count 覆盖为填充的 (Failed) 句数（恒为正），
        # 此处先快照当前真实的成功句数，供重试循环区分「整批失败」与「部分成功」。
        real_success_count = success_count

        # 部分解析成功时清除错误标记（仅适用于流式模式：非流式用 error_message
        # 区分「真实失败」，若不加以区分会导致中间句解析失败时后续未处理句被
        # 静默丢弃，因此非流式不做清除，交由下方失败兜底逻辑统一处理）。
        if is_stream:
            if success_count > 0 and not stream_error_msg:
                error_flag = False

        # 无任何有效结果时标记为错误
        if not error_flag and success_count <= 0 and not result_trans_list:
            error_message = "未解析到有效句子"
            error_flag = True

        if error_flag:
            # 记录运行时错误到服务端，供桌面端展示
            try:
                from GalTransl.server import record_runtime_error

                record_runtime_error(
                    getattr(
                        self.pj_config,
                        "runtime_project_dir",
                        self.pj_config.getProjectDir(),
                    ),
                    kind="parse",
                    message=error_message,
                    filename=filename,
                    index_range=str(idx_tip),
                    model=getattr(token, "model_name", ""),
                    level="warning",
                )
            except Exception:
                pass

            LOGGER.warning(
                f"[解析错误][{filename}:{idx_tip}]解析结果出错：{error_message}"
            )
            # 不进行重试，直接将本轮标记为翻译失败并返回兜底结果
            LOGGER.error(
                f"[解析错误][{filename}:{idx_tip}]解析出错，跳过本轮翻译"
            )
            # 失败兜底起点：
            #   流式模式 i 为 cursor 已处理到的行位置（即失败句位置），直接使用；
            #   非流式模式 i 为「最后一个成功句的下标」，失败句从 i+1 开始，
            #   i<0 表示首句即失败，从 0 开始。避免把已成功的句子重复标记为失败。
            fallback_start = (
                i if is_stream else (0 if i < 0 else i + 1)
            )
            i = self._append_parse_failure_fallback_results(
                trans_list,
                fallback_start,
                result_trans_list,
                getattr(token, "model_name", ""),
                proofread=proofread,
                translate_failed_prefix="(Failed)",
                translate_problem_message="翻译失败",
                proofread_problem_message="翻译失败",
                proofread_problem_append=True,
            )
            return i, result_trans_list, real_success_count
        elif error_message:
            LOGGER.warning(
                f"[{filename}:{idx_tip}]解析了{len(trans_list)}句中的{success_count}句，"
                f"存在问题：{error_message}"
            )

        # 回写对话历史：将本轮 assistant 回复追加进多轮对话，供后续轮次复用上下文
        assistant_reply = raw_resp or ""
        if prefill_used:
            # 用真实回复替换第一轮中的 assistant 预填充，避免出现连续的 assistant 消息
            new_conv = call_messages[:-1] + [
                {"role": "assistant", "content": assistant_reply}
            ]
        else:
            new_conv = call_messages + [
                {"role": "assistant", "content": assistant_reply}
            ]
        self.conversations[filename] = self._trim_conversation(new_conv)

        # 翻译完成
        return success_count, result_trans_list, success_count

    # ======================================================================
    # 生命周期 / 状态管理
    # ======================================================================

    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool],
        token_pool: COpenAITokenPool,
    ):
        """
        初始化 ForGalJsonMulitChat 翻译器实例

        加载 jsonline 格式专用的 Prompt 模板与多轮对话相关配置，
        初始化 OpenAI 兼容客户端，并为每个文件维护独立的对话历史。

        Args:
            config: 项目配置对象，包含 gpt.enhance_jailbreak 等翻译参数
            eng_type: 翻译引擎类型标识（如 "gpt-4o"）
            proxy_pool: 代理池对象，为 None 时不使用代理
            token_pool: API Token 池，管理多个 API 密钥的轮换
        """
        super().__init__(config, eng_type, proxy_pool, token_pool)
        self.trans_prompt = FORGAL_JSON_TRANS_PROMPT
        self.system_prompt = FORGAL_JSON_SYSTEM_PROMPT
        self._apply_internal_prompt_template_overrides()
        # 读取增强 jailbreak 配置：当模型拒绝翻译时，通过在 assistant 角色
        # 预输出 ```jsonline 来引导模型输出正确格式（仅在第一轮使用）
        if val := config.getKey("gpt.enhance_jailbreak"):
            self.enhance_jailbreak = val
        else:
            self.enhance_jailbreak = False

        # 多轮对话历史：按文件名隔离，值为完整的 messages 列表
        # messages[0] 为 system 消息，messages[1] 为第一轮 user 消息
        # （含翻译提示词 + 剧情元数据 + 首批评句），其后为各轮 user/assistant 交替
        self.conversations: dict[str, list] = {}

        # 文件名 -> 标记：该文件的「下一个批次」须以首轮方式构建。
        # 当某批次解析失败并重试耗尽（或放弃）后设置，使失败批次之后的第一个
        # 批次以完整提示词 + 剧情元数据重启多轮对话，恢复被打断的连续性。
        self._force_first_round_files: set[str] = set()

        # 文件名 -> 剧情元数据：由上层在翻译前通过 set_plot_metadata 注入（显式覆盖，
        # 优先级高于从 gt_input 自动载入的 PlotMetadata.json）。
        self.plot_metadata_map: dict[str, PlotMetadata] = {}

        # 从 gt_input（及其上层目录）的 PlotMetadata.json 自动载入的「文件名 -> 剧情元数据」映射。
        # 该文件为 JSON 数组，每项 id 对应一个待翻译文件名。仅在该文件存在时填充，
        # 供 _resolve_plot_metadata 在缺少显式注入时为对应文件提供剧情元数据。
        self._plot_metadata_by_file: dict[str, PlotMetadata] = {}
        self._plot_metadata_loaded: bool = False
        # 保存项目配置以便惰性定位 gt_input 中的 PlotMetadata.json
        self.project_config = config

        # 多轮历史保留的最大轮次数（每轮 = 1 个 user + 1 个 assistant）。
        # 0 表示不裁剪（保留完整历史）；裁剪时始终保留 system 与第一轮 user（含元数据）。
        # 注意：这里不能复用 _coerce_positive_int（其下限被强制为 1），否则 0 / 缺省 /
        # 非法值都会被抬到 1，使「不裁剪」的预期行为永远无法触发。因此单独解析，
        # 仅当值为合法整数（含 0）时采用，缺省或非法值回退为 0（不裁剪）。
        raw_multi_round = config.getKey("gpt.multiRoundMaxHistory")
        if raw_multi_round is None:
            self.multi_round_max_history = 0
        else:
            try:
                self.multi_round_max_history = int(raw_multi_round)
            except (TypeError, ValueError):
                self.multi_round_max_history = 0

        self.last_file_name = ""
        self.init_chatbot(eng_type=eng_type, config=config)
        self._set_temp_type("precise")

        pass

    def set_plot_metadata(self, plot_metadata: PlotMetadata, filename: str = "") -> None:
        """
        设置指定文件的剧情元数据。

        应在调用 batch_translate / translate 之前、针对每个文件调用一次。
        元数据仅会在该文件的「第一轮对话」中写入，后续轮次不再重复发送。

        Args:
            plot_metadata: 剧情元数据对象
            filename: 关联的文件名；为空字符串时作为默认元数据
        """
        self.plot_metadata_map[filename] = plot_metadata

    def _ensure_plot_metadata_loaded(self) -> None:
        """惰性载入 gt_input 中的 PlotMetadata.json（仅执行一次）。"""
        if self._plot_metadata_loaded:
            return
        self._plot_metadata_loaded = True  # 先置位，避免后续异常导致反复重试
        if getattr(self, "project_config", None) is None:
            return
        try:
            self._plot_metadata_by_file = load_plot_metadata_map(self.project_config)
        except Exception as e:  # 载入失败不应中断翻译
            LOGGER.warning(f"载入 PlotMetadata.json 失败，已跳过剧情元数据：{e}")
            self._plot_metadata_by_file = {}

    def _resolve_plot_metadata(self, filename: str) -> Optional[PlotMetadata]:
        """解析指定文件应使用的剧情元数据。

        优先级：
            1. 显式注入：上层通过 set_plot_metadata 为该文件（或空串默认）设置的元数据；
            2. 自动载入：gt_input 的 PlotMetadata.json 中 ``id`` 与该文件匹配的项。

        文件名可能带分批后缀（如 ``file_0``），自动载入阶段会尝试剥离末尾 ``_<数字>``
        再与 ``id`` 匹配（例如 ``02_kar_god01.txt.json_0`` -> ``02_kar_god01.txt.json``）。
        两者皆无则返回 None。
        """
        explicit = self.plot_metadata_map.get(filename)
        if explicit is not None:
            return explicit
        self._ensure_plot_metadata_loaded()
        md = self._plot_metadata_by_file.get(filename)
        if md is not None:
            return md
        # 处理分批后缀：file_0 -> file
        m = re.match(r"^(.*)_\d+$", filename)
        if m:
            return self._plot_metadata_by_file.get(m.group(1))
        return None

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
        注：剧情元数据（plot_metadata_map）默认保留，避免重复注入；
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

    # ======================================================================
    # 对外入口
    # ======================================================================

    async def translate(
        self,
        trans_list: CTransList,
        gptdict: str = "",
        proofread: bool = False,
        filename: str = "",
        plot_metadata: Optional[PlotMetadata] = None,
    ):
        # ------------------------------------------------------------------
        # 流程 1：输入内容处理、拼接
        # 遍历每个待翻译句子，清洗说话人、统一换行符、生成签名，编码为 jsonline 并拼接
        # ------------------------------------------------------------------
        idx_tip = self._build_idx_tip(trans_list)

        # 若本次调用显式传入了元数据，记录到按文件隔离的元数据表中
        if plot_metadata is not None:
            self.plot_metadata_map[filename] = plot_metadata

        input_list, sig_list, n_symbol, input_src = self._build_input_jsonlines(
            trans_list, proofread, filename
        )

        # ==================================================================
        # 简单重试机制（结果处理流程）
        #   - 最多 3 轮重试；
        #   - 失败后，上下文仅保留「完整翻译提示词 + 剧情元数据 + 失败翻译的批次」：
        #     即把会话重置为仅 [system]，本轮以首轮方式重建 user 消息（含完整
        #     提示词 + 剧情元数据 + 本批 jsonline），丢弃之前各轮累积的历史；
        #   - 失败批次之后的第一个批次视为「首次对话」（由 _force_first_round_files
        #     标记），以首轮提示词 + 剧情元数据开头，恢复被失败打断的多轮连续性。
        # ==================================================================
        MAX_RETRIES = 3
        attempt = 0
        # 本批是否应强制以首轮方式构建（上一失败批次的后续首批）。
        # 仅在此处读取一次，避免在本批自身的重试循环中被重复消费，
        # 从而错误地清除标记、影响真正需要首轮重建的后续批次。
        force_first_round = filename in self._force_first_round_files

        while True:
            # ------------------------------------------------------------------
            # 流程 2：提示词拼接
            # 第一轮：翻译提示词 + 剧情元数据；后续轮次：仅待译 jsonline
            # ------------------------------------------------------------------
            # 该文件是否仍处于「第一轮」：
            # 对话历史仅含 system 消息（len<=1）说明第一轮尚未成功建立，仍需发送
            # 完整的翻译提示词 + 剧情元数据；否则视为后续轮次，只发送待译 jsonline。
            # 注：_ensure_conversation 会预先写入 [system]，故不能用
            # 「filename 是否在 conversations 中」判断。
            conv = self._ensure_conversation(filename)
            if force_first_round:
                # 失败后续首批：丢弃旧历史，从 [system] 起以首轮重建
                # （完整提示词 + 剧情元数据 + 本批 jsonline），恢复多轮连续性
                self.conversations[filename] = [
                    {"role": "system", "content": self.system_prompt}
                ]
                conv = self.conversations[filename]
                self._force_first_round_files.discard(filename)
                force_first_round = False

            is_first_round = len(conv) <= 1

            user_content = self._build_round_user_content(
                conv, input_src, gptdict, filename, is_first_round
            )

            # 组装本次调用的 messages（历史 + 本轮 user）
            call_messages = conv + [{"role": "user", "content": user_content}]

            # 增强 jailbreak 预填充：仅在「第一轮」使用，避免与多轮历史产生连续
            # 两条 assistant 消息（OpenAI 不允许）。第一轮成功后将以真实回复替换该预填充。
            prefill_used = False
            if self.enhance_jailbreak and is_first_round:
                call_messages.append({"role": "assistant", "content": "```jsonline"})
                prefill_used = True

            self._check_stop_requested()

            # 单 worker 模式下打印翻译输入输出日志，方便调试
            if self.pj_config.active_workers == 1:
                LOGGER.info(
                    f"->{'翻译输入' if not proofread else '校对输入'}"
                    f"{'[多轮-首轮]' if is_first_round else '[多轮-续轮]'}"
                    f"{f'[重试{attempt}]' if attempt > 0 else ''}：\n"
                    f"{gptdict}\n{user_content}\n"
                )
                LOGGER.info("->输出：")

            # ------------------------------------------------------------------
            # 流程 3：传递提示词和输入内容至 API
            # 将包含完整历史的 messages 一次性发给 API（多轮对话模式）
            # ------------------------------------------------------------------
            key_name = "dst" if not proofread else "newdst"
            stream_cursor = {"i": -1, "success_count": 0, "started": False}
            parsed_result_trans_list: List[str] = []

            # 用 lambda 把本次调用上下文绑定到流式解析方法，作为 stream_line_callback
            # 传入（每个 translate 调用各自持有一份闭包，避免并发互相干扰）
            stream_callback = lambda lines, is_final: self._parse_stream_lines(
                lines,
                is_final,
                trans_list=trans_list,
                n_symbol=n_symbol,
                sig_list=sig_list,
                key_name=key_name,
                emit_runtime_success=(not proofread),
                filename=filename,
                parsed_result_trans_list=parsed_result_trans_list,
                cursor=stream_cursor,
            )
            raw_resp, token = await self._call_llm(
                call_messages, filename, idx_tip, stream_callback
            )

            # ------------------------------------------------------------------
            # 流程 4：返回结果解析和处理
            # 流式边收边解析结果 + 非流式完整解析，统一判定成败并回写对话历史
            # ------------------------------------------------------------------
            stream_error_msg = stream_cursor.get("error", "")
            success_count, result_trans_list, real_success = self._handle_parse_result(
                raw_resp=raw_resp,
                token=token,
                trans_list=trans_list,
                n_symbol=n_symbol,
                sig_list=sig_list,
                is_stream=getattr(self, "_last_chatbot_was_stream", False),
                stream_error_msg=stream_error_msg,
                stream_cursor=stream_cursor,
                stream_parsed_list=parsed_result_trans_list,
                idx_tip=idx_tip,
                filename=filename,
                proofread=proofread,
                call_messages=call_messages,
                prefill_used=prefill_used,
            )

            # 解析到有效结果（含部分成功，real_success>0 表示本批确有真实译文）：
            # 直接返回，剩余失败句以 (Failed) 兜底、交由上层后续 pass 处理。
            # 注意：必须用 real_success（真实翻译句数）判断，不能用 success_count——
            # 失败兜底会把它覆盖成填充的 (Failed) 句数（恒为正），否则会误判为成功、
            # 导致下方重试逻辑永远不触发。
            if real_success > 0:
                # 本批（含内部重试）成功：清除可能由本批自身失败重试写入的强制首轮标记，
                # 避免下一个批次被错误地强制首轮、破坏刚恢复的多轮对话连续性。
                self._force_first_round_files.discard(filename)
                return success_count, result_trans_list

            # ===== 整批解析失败，进入重试 =====
            attempt += 1
            if attempt > MAX_RETRIES:
                LOGGER.error(
                    f"[重试耗尽][{filename}:{idx_tip}]已重试 {MAX_RETRIES} 次仍失败，放弃本批翻译"
                )
                # 标记后续批次以首轮重建：本次失败已破坏多轮连续性
                self._force_first_round_files.add(filename)
                return success_count, result_trans_list

            # 重试：上下文仅保留「完整翻译提示词 + 剧情元数据 + 失败翻译的批次」
            # 即把会话重置为仅 [system]，下一轮以首轮方式重建（含完整提示词+
            # 剧情元数据+本批 jsonline），丢弃之前各轮累积的历史。
            LOGGER.warning(
                f"[重试 {attempt}/{MAX_RETRIES}][{filename}:{idx_tip}]解析失败，"
                f"重置上下文为本批首轮（仅含完整提示词+剧情元数据+本批）后重试"
            )
            self.conversations[filename] = [
                {"role": "system", "content": self.system_prompt}
            ]
            # 失败批次之后的第一个批次须以首轮重建，恢复被打断的多轮连续
            self._force_first_round_files.add(filename)

    async def batch_translate(
        self,
        filename,
        cache_file_path,
        trans_list: CTransList,
        num_pre_request: int,
        retry_failed: bool = False,
        gpt_dic: CGptDict = None,
        proofread: bool = False,
        retran_key: str = "",
        translist_hit: CTransList = [],
        translist_unhit: CTransList = [],
    ) -> CTransList:
        # 新文件：重置该文件的对话历史，确保以第一轮（含元数据）开始
        if self.last_file_name != filename:
            self.reset_conversation(filename)
            self.last_file_name = filename
        return await self._batch_translate_common(
            filename=filename,
            cache_file_path=cache_file_path,
            translist_unhit=translist_unhit,
            num_pre_request=num_pre_request,
            gpt_dic=gpt_dic,
            proofread=proofread,
            glossary_style="gpt",
            failed_markers=("(Failed)", "(翻译失败)"),
            h_words_list=H_WORDS_LIST,
            ensure_last_translations=True,
        )


"""
接入说明（如需启用本后端）：
在 GalTransl/Frontend/LLMTranslate.py 的 init_gptapi() 中增加对引擎标识的映射，例如：

    match eng_type:
        ...
        case "ForGal-json-multi-chat":
            from GalTransl.Backend.ForGalJsonMulitChat import (
                ForGalJsonMulitChat,
            )
            translator = ForGalJsonMulitChat(
                cfg, param, cfg.proxyPool, cfg.tokenPool
            )
            return translator

剧情元数据（PlotMetadata）的注入有两路来源，均由「第一轮对话」写入提示词：

1. 显式注入：上层在调用 batch_translate 前，通过
   ``translator.set_plot_metadata(metadata, filename)`` 为该文件设置元数据；
2. 自动载入：后端在首次需要时为该文件惰性读取 gt_input（及其上层目录）中的
   ``PlotMetadata.json``（JSON 数组，每项 ``id`` 对应一个待翻译文件名），
   按文件名（含分批后缀 ``_N`` 的剥离）匹配对应条目。

显式注入优先级高于自动载入；两路皆无对应条目时该文件首轮不附带剧情元数据。
（元数据仅在第一轮对话中出现，后续轮次不再重复发送。）
"""


if __name__ == "__main__":
    pass
