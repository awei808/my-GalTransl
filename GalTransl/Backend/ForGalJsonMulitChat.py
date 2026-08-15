from __future__ import annotations

import json
import os
import asyncio
import re
from random import choice
from typing import Any, Optional, List, Set, Tuple

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
    H_BATCH_GUIDE,
    H_BATCH_FORBIDDEN,
    NORMAL_BATCH_GUIDE,
)
from GalTransl.Backend.BaseTranslate import BaseTranslate
from GalTransl.Backend.metadata import (
    FileMetaData,
    BatchMetadata,
    load_file_metadata,
    load_file_metadata_map,
    load_batch_metadata_map,
)
from GalTransl.Backend.utils import (
    detect_line_break_symbol,
    detect_batch_line_break_symbol,
    parse_interval,
    normalize_batch_intervals,
    strip_chunk_suffix,
)
from GalTransl.server_runtime import WORKER_ID_CTX, set_live_snippets
from GalTransl.Service import JobCancelledError
from openai._types import NOT_GIVEN


"""ForGalJsonMulitChat - 基于 JSON-line 格式的多轮对话视觉小说脚本翻译后端

与单轮对话翻译后端的核心差异：
本后端采用「多轮对话（multi-round chat）」模式对接 API
每次 API 调用都会把完整的 messages 历史（system + 之前各轮 user/assistant）一并发出，由模型自行维持上下文。

数据流程（类内方法亦按此顺序排列）：
1. 输入内容处理、拼接
2. 提示词拼接
3. 传递提示词和输入内容至 API
4. 返回结果解析和处理

多轮对话的关键约定：
  - 第一轮对话：在 user 消息中写入「翻译提示词 + 剧情元数据(FileMetaData) + 本批待译句子」。
  - 后续轮次：无需再重复翻译提示词与剧情元数据，只发送「待翻译句子（带 sig 的 jsonline）」。
  - 历史译文由多轮对话本身携带，不再通过 [history_result] 注入。
"""


class ForGalJsonMulitChat(BaseTranslate):
    """
    ForGalJsonMulitChat - 基于 JSON-line 格式、采用多轮对话的视觉小说脚本翻译后端

    核心流程：
    1. 将 CTransList 中的每个 CSentense 编码为 "3位随机签名|JSON对象" 的 jsonline 行
    2. 第一轮对话把 jsonline + 翻译提示词 + 剧情元数据(FileMetaData) 写入 user 消息，后续轮次仅发送 jsonline，借助多轮上下文保持前后一致
    3. 解析 LLM 返回的 jsonline 结果，校验签名/id/字段完整性
    4. 将翻译结果写回 CSentense.pre_dst

    继承自 BaseTranslate，复用客户端构建、API 调用、缓存读写、动态句数调节等通用逻辑。
    """

    # 用于生成 jsonline 签名的字符集，每个句子分配 3 位随机签名用于防串行校验
    _SIGCHARS = "abcdefghijklmnopqrstuvwxyz0123456789"

    # 1. 输入内容处理、拼接

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
        problem_types: Optional[list] = None,
        include_src: bool = True,
    ) -> tuple:
        """
        输入内容处理与拼接（流程第 1 步）。

        遍历待译句子，完成：
          - 取说话人并清洗（去除换行/制表符，避免破坏 jsonline）
          - 整批统一判定换行符（模块级 detect_line_break_symbol，仅一次）
          - 将 \\t → [t]、换行符 → <br>（LLM 友好格式，避免与 jsonline 冲突）
          - 为每句生成唯一 3 位签名 sig（防串行校验）
          - 翻译模式构建 {id,name,src}；校对模式额外携带 dst
          - problem_types 非 None 时可选注入 problem（译文问题，供改进轮参考）
          - include_src=False 时校对模式仅注入 dst（不注入原文），供纯译文修复
            （如换行位置修复）使用，避免模型回显/依赖原文
          - 无说话人时删除 name 字段（表示旁白/独白）
        最终将所有 jsonline 行拼接为待译输入文本。

        Args:
            trans_list: 本批评句
            proofread: 是否校对模式
            filename: 文件名（预留，当前用于潜在扩展）
            problem_types: 允许注入的译文问题类型白名单（CProblemType 或其中文名）；
                None=不注入；空列表=注入全部；非空=仅注入白名单内类型。
            include_src: 校对模式下是否注入 src 原文；默认 True（保持既有行为）。
                设为 False 时只注入 dst（当前译文），供换行位置修复等纯译文任务使用。

        Returns:
            (input_list, sig_list, n_symbol, input_src)
            - input_list: 各行 "sig|{...}" 字符串列表
            - sig_list: 与 input_list 一一对应的签名列表
            - n_symbol: 整批统一检测到的换行符标记（"" 表示无）
            - input_src: 拼接后的待译输入文本
        """
        input_list: List[str] = []
        sig_list: List[str] = []

        # 整批仅判定一次换行符，逐句取首命中（不能用 join——分隔符会混入检测串）
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
                dst_text = (
                    trans.pre_dst if trans.proofread_zh == "" else trans.proofread_zh
                )
                # dst 与 src 采用同一换行表示（n_symbol -> <br>），否则模型两侧换行基准错位
                if n_symbol:
                    dst_text = dst_text.replace(n_symbol, "<br>")
                if include_src:
                    tmp_obj = {
                        "id": trans.index,
                        "name": speaker,
                        "src": src_text,
                        "dst": dst_text,
                    }
                else:
                    # 纯译文任务（如换行位置修复）：仅注入 dst，不注入原文 src，
                    # 避免模型回显原文或依赖原文做无关重译
                    tmp_obj = {
                        "id": trans.index,
                        "name": speaker,
                        "dst": dst_text,
                    }

            # 可选注入译文问题（problem）：供改进轮评估参考；翻译轮不传 problem_types 不生效
            if problem_types is not None:
                if problem_types:
                    problem_str = self._filter_problem_by_types(
                        getattr(trans, "problem", ""), problem_types
                    )
                else:
                    # 空白名单=注入全部已检测问题
                    problem_str = getattr(trans, "problem", "")
                if problem_str:
                    tmp_obj["problem"] = problem_str

            # 无说话人时删除 name 字段，表示旁白/独白
            if tmp_obj["name"] == "null":
                del tmp_obj["name"]

            input_list.append(self._encode_sig_jsonline(sig, tmp_obj))

        # 拼接所有 jsonline 行为最终输入文本
        input_src = "\n".join(input_list)
        return input_list, sig_list, n_symbol, input_src

    @staticmethod
    def _filter_problem_by_types(problem: str, problem_types: list) -> str:
        """按类型白名单过滤 problem 字符串，保留「类型名：描述」原文。

        类型匹配为精确匹配：项 == 类型名 或以「类型名：」开头，
        避免「比日文长」误匹配「比日文长严格」。对 find_problems 生成的
        文本前缀与枚举名不一致的类别（标点错漏/语言不通/字典使用）做别名兜底。

        Args:
            problem: 原始 problem 字符串（如 "残留日文：xx, 独白男他"）。
            problem_types: 允许的类型（CProblemType 成员或其 name 字符串）。

        Returns:
            str: 过滤后的问题串；无匹配返回空串。
        """
        if not problem:
            return ""
        # find_problems 生成文本与枚举名不一致的类别 -> 文本前缀别名
        type_aliases = {
            "标点错漏": ("本无", "本有"),
            "语言不通": ("语言不通-非GBK",),
            "字典使用": ("未使用",),
        }
        allowed = {
            (t.name if hasattr(t, "name") else str(t)).strip() for t in problem_types
        }
        kept = []
        for item in problem.split(","):
            item = item.strip()
            if not item:
                continue
            type_name = item.split("：", 1)[0].strip()
            if type_name in allowed:
                kept.append(item)
                continue
            if any(
                alias in type_name
                for tname in allowed
                for alias in type_aliases.get(tname, ())
            ):
                kept.append(item)
        return ", ".join(kept)

    # 2. 提示词拼接

    def _format_file_metadata_block(self, metadata: FileMetaData) -> str:
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
        batch_metadata_block: str = "",
    ) -> str:
        """
        提示词拼接（流程第 2 步）。

        第一轮对话：构建完整翻译提示词（替换 [Input]/[Glossary]/[translation_guideline]），
          剧情元数据(FileMetaData) 段通过模板中的 [plot_metadata] 占位符注入，
          批次级元数据(BatchMetadata) 段通过 [batch_metadata] 占位符注入，
          均位于 [translation_guideline]/[Glossary] 之后、[Input] 之前；
        后续轮次：返回待译 jsonline，并附上本批次按需生成的术语表(gptdict) 短块，
          复用多轮上下文的同时保证每批都能看到本批出现的专有名词/人设解释。
        多轮模式下历史由对话本身携带，[history_result] 置空。

        Args:
            conv: 该文件的对话历史（已由调用方获取，用于一致性校验）
            input_src: 拼接后的待译 jsonline 文本
            gptdict: 术语表
            filename: 文件名
            is_first_round: 是否为第一轮对话
            batch_metadata_block: 已格式化的批次级元数据段（由 translate 依本批
                全局行号区间预先解析生成；无相交区间时为空串）

        Returns:
            本轮要发送的 user 消息内容
        """
        if is_first_round:
            # 第一轮：构建完整翻译提示词（含 [Input]/[Glossary]/[translation_guideline] 替换）
            # 剧情元数据经模板 [plot_metadata] 占位符注入，位于翻译规范之后、[Input] 之前。
            metadata = self._resolve_file_metadata(filename)
            metadata_block = (
                self._format_file_metadata_block(metadata)
                if metadata is not None
                else ""
            )
            # 全局提示词（GlobalPrompt）：仅在首轮注入（路线剧情 + 带标注的全量 GlobalPrompt）
            global_prompt_block = self._format_global_prompt_block(filename)
            prompt_req = self._build_prompt_request(
                input_src,
                gptdict,
                plot_metadata=metadata_block,
                batch_metadata=batch_metadata_block,
            )
            # 注入全局提示词
            prompt_req = prompt_req.replace(
                "[global_prompt]", global_prompt_block or ""
            )
            # 多轮模式下历史由对话本身携带，[history_result] 置为 None
            prompt_req = self._apply_history_result(prompt_req, filename)
            user_content = prompt_req
        else:
            # 后续轮次：复用多轮上下文；本批注入本批涉及的剧情区间指导 + 术语表，再发待译句子
            parts = []
            if batch_metadata_block:
                parts.append(batch_metadata_block)
            if gptdict:
                parts.append(gptdict + "\n以下是本批次待翻译内容：")
            parts.append(input_src)
            user_content = "\n".join(parts)
        LOGGER.debug(
            f"[{filename}] {'首轮' if is_first_round else '续轮'}翻译 | "
            f"backend={self.eng_type} | "
            f"guideline={self.pj_config.getKey('gpt.translation_guideline') or '-'} | "
            f"global={'✓' if self._global_prompt else '✗'} | "
            f"filemeta={'✓' if filename in self._file_metadata_by_file else '✗'} | "
            f"batchmeta={'✓' if filename in self._batch_metadata_by_file else '✗'} | "
            f"sentences={len(conv) if is_first_round else input_src.count(chr(10))+1} | "
            f"gptdict={'✗' if not gptdict else f'{gptdict.count(chr(124))}条'}"
        )
        return user_content

    # 3. 调 API

    async def _call_llm(
        self,
        call_messages: list,
        filename: str,
        idx_tip: str,
        stream_line_callback: Optional[Any],
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

    # 4. 返回结果解析和处理

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
                self.get_last_chatbot_model(),
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
    ) -> tuple[bool, str]:
        # 先校验返回值非空/有效，再解析
        if not line or not isinstance(line, str):
            return False, f"待解析行为空或类型异常：{type(line).__name__}"
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
        raw_resp: str,
        token: COpenAIToken,
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

        # 快照真实翻译成功句数，用于区分「整批失败」与「部分成功」
        real_success_count = success_count

        # 部分成功时清除错误标记（仅流式，非流式由失败兜底统一处理）
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
            # 失败兜底起点：流式用 cursor 位置，非流式从最后一个成功句之后开始
            fallback_start = (
                i if is_stream else (0 if i < 0 else i + 1)
            )
            i = self._append_parse_failure_fallback_results(
                trans_list,
                fallback_start,
                result_trans_list,
                getattr(token, "model_name", ""),
                proofread=proofread,
                retain_failed=False,  # 失败句子不保留：pre_dst 保持空，由上层重新入队重试
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

    # 生命周期 / 状态管理

    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool],
        token_pool: COpenAITokenPool,
    ) -> None:
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

        # 多轮对话历史：按文件名隔离，messages[0]=system，其后 user/assistant 交替
        self.conversations: dict[str, list] = {}

        # 标记下一批次须以首轮方式构建（失败重试耗尽后设置，恢复多轮连续性）
        self._force_first_round_files: set[str] = set()

        # 文件名 -> 剧情元数据：由上层在翻译前通过 set_file_metadata 注入（显式覆盖，
        # 优先级高于从 gt_input 自动载入的 FileMetaData.json）。
        self.file_metadata_map: dict[str, FileMetaData] = {}

        # 从 FileMetaData.json 自动载入的文件名->剧情元数据映射（惰性载入，供缺显式注入时使用）
        self._file_metadata_by_file: dict[str, FileMetaData] = {}
        self._file_metadata_loaded: bool = False
        # 保存项目配置以便惰性定位 gt_input 中的 FileMetaData.json
        self.project_config = config

        # 文件名 -> 批次级元数据：由上层显式注入（优先级高于自动载入）。
        self.batch_metadata_map: dict[str, BatchMetadata] = {}
        # 从 BatchMetadata.json 自动载入的「文件名 -> 批次级元数据」映射（惰性一次）。
        self._batch_metadata_by_file: dict[str, BatchMetadata] = {}
        self._batch_metadata_loaded: bool = False

        # H 场景用词不当词库（hCheckDict）惰性加载缓存：None=未加载，[]=加载结果为空
        self._h_check_words: Optional[list] = None

        # 多轮历史最大保留轮次数（0=不裁剪）；单独解析避免 _coerce_positive_int 把 0 抬为 1
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

        # 惰性载入的全局提示词（GlobalPrompt）
        self._global_prompt: Optional[dict] = None
        self._global_prompt_loaded: bool = False
        # 惰性载入的剧情路线图（PlotRouteMap）
        self._plot_route_map: Optional[dict] = None
        self._plot_route_map_loaded: bool = False

    # ── GlobalPrompt 上下文注入 ──

    def _ensure_global_prompt_loaded(self) -> None:
        """惰性载入 GlobalPrompt.json（仅执行一次）。"""
        if self._global_prompt_loaded:
            return
        self._global_prompt_loaded = True
        # 优先从 projectConfig 读取已注入的全局提示词
        explicit = getattr(self.pj_config, "global_prompt", None)
        if isinstance(explicit, dict):
            self._global_prompt = explicit
            LOGGER.debug(
                "[ForGalJsonMulitChat] 使用已注入的 GlobalPrompt（来自流水线）"
            )
            return
        # 否则尝试从缓存文件加载
        try:
            from GalTransl.Backend.ForGlobalPrompt import load_global_prompt
            self._global_prompt = load_global_prompt(self.pj_config)
            if self._global_prompt:
                LOGGER.debug(
                    "[ForGalJsonMulitChat] 已从 pass0_cache 载入 GlobalPrompt 上下文"
                )
        except Exception as e:
            LOGGER.debug(
                f"[ForGalJsonMulitChat] 载入 GlobalPrompt 失败：{e}"
            )
            self._global_prompt = None

    def _format_global_prompt_block(self, filename: str = "") -> str:
        """格式化 GlobalPrompt 为提示词附加段落（仅首轮注入）。

        有路线图归属时注入「路线剧情 + 带标注的全量 GlobalPrompt」；无归属或
        没有路线图时仅注入全量 GlobalPrompt。标注说明全局剧情为游戏整体剧情、
        可能与当前文件不完全对应，以路线剧情和文件元数据为准。
        """
        route_block = self._format_route_context_for_file(filename)
        self._ensure_global_prompt_loaded()
        if not self._global_prompt:
            return route_block
        from GalTransl.Backend.ForGlobalPrompt import _format_global_prompt_as_context
        gp_block = _format_global_prompt_as_context(
            self._global_prompt, annotate_plot=bool(route_block)
        )
        if route_block and gp_block:
            LOGGER.debug(
                f"[ForGalJsonMulitChat] {filename} 注入路线剧情 + 带标注的全局提示词"
            )
            return f"{route_block}\n{gp_block}"
        return route_block or gp_block

    def _ensure_plot_route_map_loaded(self) -> None:
        """惰性载入 PlotRouteMap.json（仅执行一次）。"""
        if self._plot_route_map_loaded:
            return
        self._plot_route_map_loaded = True
        try:
            from GalTransl.Backend.ForPlotRouteMap import load_plot_route_map

            self._plot_route_map = load_plot_route_map(self.pj_config)
            if self._plot_route_map:
                LOGGER.debug(
                    "[ForGalJsonMulitChat] 已从 pass0_cache 载入 PlotRouteMap 路线图"
                )
        except Exception as e:
            LOGGER.debug(f"[ForGalJsonMulitChat] 载入 PlotRouteMap 失败：{e}")
            self._plot_route_map = None

    def _format_route_context_for_file(self, filename: str) -> str:
        """按当前文件所属路线返回剧情上下文块；无归属/无路线图时返回空串。"""
        from GalTransl.Backend.ForPlotRouteMap import _format_route_context

        try:
            self._ensure_plot_route_map_loaded()
            plot_route_map = self._plot_route_map
            if not plot_route_map:
                return ""
            base = strip_chunk_suffix(filename)
            ctx = _format_route_context(plot_route_map, base)
            if ctx:
                LOGGER.debug(
                    f"[ForGalJsonMulitChat] {filename} 注入路线剧情（{base}）"
                )
            return ctx
        except Exception as e:
            LOGGER.warning(
                f"[ForGalJsonMulitChat] 按路线注入剧情失败：{e}"
            )
            return ""

    def set_file_metadata(self, file_metadata: FileMetaData, filename: str = "") -> None:
        """
        设置指定文件的文件级元数据。

        应在调用 batch_translate / translate 之前、针对每个文件调用一次。
        元数据仅会在该文件的「第一轮对话」中写入，后续轮次不再重复发送。

        Args:
            file_metadata: 文件级元数据对象
            filename: 关联的文件名；为空字符串时作为默认元数据
        """
        self.file_metadata_map[filename] = file_metadata

    def _ensure_file_metadata_loaded(self) -> None:
        """惰性载入 FileMetaData.json（仅执行一次）。"""
        if self._file_metadata_loaded:
            return
        self._file_metadata_loaded = True
        if getattr(self, "project_config", None) is None:
            return
        try:
            self._file_metadata_by_file = load_file_metadata_map(self.project_config)
            LOGGER.info(
                f"[ForGalJsonMulitChat] 已载入 FileMetaData.json，"
                f"共 {len(self._file_metadata_by_file)} 个文件有文件级元数据"
            )
        except Exception as e:
            LOGGER.warning(f"[ForGalJsonMulitChat] 载入 FileMetaData.json 失败，已跳过剧情元数据：{e}")
            self._file_metadata_by_file = {}

    def _resolve_file_metadata(self, filename: str) -> Optional[FileMetaData]:
        """解析指定文件应使用的剧情元数据。

        优先级：
            1. 显式注入：上层通过 set_file_metadata 为该文件（或空串默认）设置的元数据；
            2. 自动载入：gt_input 的 FileMetaData.json 中 ``id`` 与该文件匹配的项。

        文件名可能带分批后缀（如 ``file_0``），自动载入阶段会尝试剥离末尾 ``_<数字>``
        再与 ``id`` 匹配（例如 ``02_kar_god01.txt.json_0`` -> ``02_kar_god01.txt.json``）。
        两者皆无则返回 None。
        """
        explicit = self.file_metadata_map.get(filename)
        if explicit is not None:
            return explicit
        # 显式注入的 key 可能带分批后缀（如 name_0）；翻译轮以带后缀名精确命中，
        # 改进轮等以原始名查询时按剥离后缀兜底匹配，避免分片文件丢失剧情元数据。
        stripped = strip_chunk_suffix(filename)
        for k, v in self.file_metadata_map.items():
            if strip_chunk_suffix(k) == stripped:
                return v
        self._ensure_file_metadata_loaded()
        md = self._file_metadata_by_file.get(filename)
        if md is not None:
            return md
        # 处理分批后缀：file_0 -> file
        return self._file_metadata_by_file.get(strip_chunk_suffix(filename))

    def set_batch_metadata(
        self, batch_metadata: BatchMetadata, filename: str = ""
    ) -> None:
        """设置指定文件的批次级元数据（显式注入，优先级高于自动载入）。"""
        self.batch_metadata_map[filename] = batch_metadata

    def _ensure_batch_metadata_loaded(self) -> None:
        """惰性载入 BatchMetadata.json（仅执行一次）。"""
        if self._batch_metadata_loaded:
            return
        self._batch_metadata_loaded = True
        if getattr(self, "project_config", None) is None:
            return
        try:
            self._batch_metadata_by_file = load_batch_metadata_map(self.project_config)
            LOGGER.info(
                f"[ForGalJsonMulitChat] 已载入 BatchMetadata.json，"
                f"共 {len(self._batch_metadata_by_file)} 个文件有批次元数据"
            )
        except Exception as e:
            LOGGER.warning(f"[ForGalJsonMulitChat] 载入 BatchMetadata.json 失败，已跳过批次元数据：{e}")
            self._batch_metadata_by_file = {}

    def _resolve_batch_metadata(self, filename: str) -> Optional[BatchMetadata]:
        """解析指定文件应使用的批次级元数据（显式注入 > 自动载入；剥离分批后缀）。"""
        explicit = self.batch_metadata_map.get(filename)
        if explicit is not None:
            return explicit
        self._ensure_batch_metadata_loaded()
        bm = self._batch_metadata_by_file.get(filename)
        if bm is not None:
            return bm
        # 处理分批后缀：file_0 -> file
        return self._batch_metadata_by_file.get(strip_chunk_suffix(filename))

    def _group_by_batch_metadata(
        self, translist_unhit: CTransList, filename: str
    ) -> List[CTransList]:
        """按批次级元数据语义段边界对句子分组。

        每组句子 ``runtime_index`` 同属一个语义段（边界对齐、不跨段），段外句入尾组。
        无/空元数据或零有效段时返回 ``[translist_unhit]``（调用方退化为原固定切片，待废弃）。
        有元数据时每段作单一翻译单元发送，大段不二次切割（Option A：共用文件对话）。
        """
        bm = self._resolve_batch_metadata(filename)
        if bm is None or not getattr(bm, "batches", None):
            # 待废弃：无/空 BatchMetadata 时退化为整文件一次翻译（count 切批入口），将由 BatchMetadata 取代
            return [list(translist_unhit)]

        segs = []
        for b in bm.batches:
            if not isinstance(b, dict):
                continue
            rng = parse_interval(b.get("区间") or b.get("interval"))
            if rng is None:
                continue
            segs.append((rng[0], rng[1], b))
        segs.sort(key=lambda x: (x[0], x[1]))
        if not segs:
            # 待废弃：无/空 BatchMetadata 时退化为整文件一次翻译（count 切批入口），将由 BatchMetadata 取代
            return [list(translist_unhit)]

        def _gi(t):
            gi = getattr(t, "runtime_index", None)
            if not isinstance(gi, int):
                gi = getattr(t, "index", None)
            return gi if isinstance(gi, int) else None

        seg_lists = [[] for _ in segs]
        ungrouped = []
        for t in translist_unhit:
            gi = _gi(t)
            if gi is None:
                ungrouped.append(t)
                continue
            placed = False
            for i, (s_lo, s_hi, _b) in enumerate(segs):
                if s_lo <= gi <= s_hi:
                    seg_lists[i].append(t)
                    placed = True
                    break
            if not placed:
                ungrouped.append(t)
        groups = [lst for lst in seg_lists if lst]
        if ungrouped:
            groups.append(ungrouped)
        # 待废弃：全部未分组时退化为整文件一次翻译（count 切批入口），将由 BatchMetadata 取代
        return groups if groups else [list(translist_unhit)]

    @staticmethod
    def _trans_global_range(trans_list: CTransList) -> tuple:
        """取本批句子的**全局行号**闭区间 [lo, hi]。

        优先使用与 BatchMetadata 区间同源的 runtime_index（文件内全局行号），
        缺失时回退到 index。空列表返回 (0, -1)（不与任何区间相交）。
        """
        lo = None
        hi = None
        for trans in trans_list:
            gi = getattr(trans, "runtime_index", None)
            if not isinstance(gi, int):
                gi = getattr(trans, "index", None)
            if not isinstance(gi, int):
                continue
            lo = gi if lo is None else min(lo, gi)
            hi = gi if hi is None else max(hi, gi)
        if lo is None:
            return 0, -1
        return lo, hi

    def _format_batch_metadata_block(
        self, batch_metadata: BatchMetadata, lo: int, hi: int
    ) -> str:
        """把与本批行号区间 [lo, hi] 相交的批次级元数据格式化为提示词附加段落。

        按 h 值分组渲染：H 区间段与非 H 区间段分别给出差异化翻译指导；
        H 段额外注入项目 hCheckDict 禁用词表（词数 ≤ 20 全量，超出截断）。
        仅注入相关区间，避免整份区间表膨胀提示词。无相交区间时返回空串。
        """
        segments = batch_metadata.segments_in_range(lo, hi)
        if not segments:
            return ""

        h_lines: list[str] = []
        normal_lines: list[str] = []
        for b in segments:
            rng = parse_interval(b.get("区间") or b.get("interval"))
            if rng is None:
                continue
            b_lo, b_hi = rng
            view = str(b.get("视角", "") or "").strip() or "未标注"
            atmos = str(b.get("氛围", "") or "").strip() or "未标注"
            tone = str(b.get("用词色彩", "") or "").strip() or "未标注"
            line = (
                f"- 区间[{b_lo}-{b_hi}] 视角:{view} 氛围:{atmos} "
                f"H:{'是' if b.get('h') else '否'} 用词色彩:{tone}"
            )
            if b.get("h"):
                h_lines.append(line)
            else:
                normal_lines.append(line)

        blocks: list[str] = []
        if h_lines:
            forbidden = self._format_h_forbidden_words()
            h_block = "\n".join([H_BATCH_GUIDE, *h_lines])
            if forbidden:
                h_block += f"\n{forbidden}"
            blocks.append(h_block)
        if normal_lines:
            blocks.append("\n".join([NORMAL_BATCH_GUIDE, *normal_lines]))
        if not blocks:
            return ""

        return (
            "\n<batch_metadata>\n"
            "本文件已按剧情划分为若干翻译区间，本批涉及的区间及其翻译指导如下"
            "（行号为文件内全局行号）：\n"
            f"{chr(10).join(blocks)}\n"
            "</batch_metadata>\n"
            "请依据每句所处区间，采用对应的视角、氛围与用词色彩进行翻译；"
            "每批次仅提供本批涉及的区间指导，请结合上下文保持区间内风格统一、区间之间自然过渡。\n"
        )

    def _group_is_h_scene(self, group: CTransList, filename: str) -> bool:
        """判断本批待译句子组是否处于 h 场景（供字典按场景分流注入）。

        与 _format_batch_metadata_block 的 H 判定口径同源：按全局行号区间与
        batch.json 批次相交后取任一批次 h 标记。无元数据/空组时回退非 h。
        """
        bm = self._resolve_batch_metadata(filename)
        if bm is None or not getattr(bm, "batches", None):
            return False
        lo, hi = self._trans_global_range(group)
        if hi < lo:
            return False
        return any(bool(b.get("h")) for b in bm.segments_in_range(lo, hi))

    def _format_h_forbidden_words(self) -> str:
        """把项目 hCheckDict 词库格式化为 H 区间禁用词提示段。

        词数 ≤ 20 时全量列出，超出时截断为省略提示，避免提示词过长。
        """
        words = self._resolve_h_check_words()
        if not words:
            return ""
        if len(words) <= 20:
            listed = "、".join(words)
        else:
            listed = "、".join(words[:20]) + "…………等词语"
        return H_BATCH_FORBIDDEN.format(words=listed)

    def _resolve_h_check_words(self) -> list:
        """从项目配置 hCheckDict 惰性加载 H 场景用词不当词库（仅一次）。

        复用 Problem.load_h_check_words 的解析规则；加载失败返回空列表。
        """
        if self._h_check_words is not None:
            return self._h_check_words
        self._h_check_words = []
        try:
            from GalTransl.ConfigHelper import initDictList
            from GalTransl.Problem import load_h_check_words

            cfg = getattr(self, "project_config", None)
            if cfg is None:
                return self._h_check_words
            dict_cfg = cfg.getDictCfgSection() or {}
            # h 禁用词：forbiddenDictH 优先，未配置时回退旧 hCheckDict
            h_dict_list = dict_cfg.get("forbiddenDictH", dict_cfg.get("hCheckDict", []))
            default_dir = dict_cfg.get("defaultDictFolder", "")
            project_dir = cfg.getProjectDir()
            self._h_check_words = load_h_check_words(
                initDictList(h_dict_list, default_dir, project_dir)
            )
            if self._h_check_words:
                LOGGER.info(
                    f"[ForGalJsonMulitChat] 已载入 H 场景用词不当词库，"
                    f"{len(self._h_check_words)} 词"
                )
        except Exception as e:
            LOGGER.warning(
                f"[ForGalJsonMulitChat] 加载 H 场景用词不当词库失败，跳过：{e}"
            )
            self._h_check_words = []
        return self._h_check_words

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
        当前配置项中未设置相应配置，也就是说裁剪功能实际是没有实现的。暂不考虑实现
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

    # 对外入口

    async def translate(
        self,
        trans_list: CTransList,
        gptdict: str = "",
        proofread: bool = False,
        filename: str = "",
        file_metadata: Optional[FileMetaData] = None,
    ) -> tuple[int, CTransList]:
        # 流程 1：输入处理、编码 jsonline
        idx_tip = self._build_idx_tip(trans_list)

        # 若本次调用显式传入了元数据，记录到按文件隔离的元数据表中
        if file_metadata is not None:
            self.file_metadata_map[filename] = file_metadata

        input_list, sig_list, n_symbol, input_src = self._build_input_jsonlines(
            trans_list, proofread, filename
        )

        # 按全局行号区间解析 BatchMetadata，格式化为首轮附加段（仅首轮注入）
        batch_metadata_block = ""
        _bm = self._resolve_batch_metadata(filename)
        if _bm is not None:
            _lo, _hi = self._trans_global_range(trans_list)
            batch_metadata_block = self._format_batch_metadata_block(_bm, _lo, _hi)

        # 简单重试机制：最多 3 轮，失败后重置会话为仅 [system] 并以首轮方式重建
        MAX_RETRIES = 3
        attempt = 0
        # 本批是否强制首轮构建（仅读一次，避免在重试循环中重复消费标记）
        force_first_round = filename in self._force_first_round_files

        while True:
            # 流程 2：提示词拼接
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
                conv,
                input_src,
                gptdict,
                filename,
                is_first_round,
                batch_metadata_block=batch_metadata_block,
            )

            # 实时推送当前提示词预览（前端翻译控制台左栏"当前提示词"，多 worker 时按 worker 分板块）
            _project_dir = self.pj_config.getProjectDir()
            try:
                LOGGER.debug(
                    f"[prompt-preview] translate 推送提示词: WORKER_ID_CTX={WORKER_ID_CTX.get()!r}, "
                    f"filename={filename!r}, idx_tip={idx_tip!r}"
                )
                set_live_snippets(
                    _project_dir,
                    prompt_preview=user_content,
                    filename=f"{filename}:{idx_tip}",
                )
            except Exception as e:
                LOGGER.warning(f"[prompt-preview] set_live_snippets 调用失败: {e}")

            # 组装本次调用的 messages（历史 + 本轮 user）
            call_messages = conv + [{"role": "user", "content": user_content}]

            # 增强 jailbreak 预填充：仅在「第一轮」使用，避免与多轮历史产生连续
            # 两条 assistant 消息（OpenAI 不允许）。第一轮成功后将以真实回复替换该预填充。
            prefill_used = False
            if self.enhance_jailbreak and is_first_round:
                call_messages.append({"role": "assistant", "content": "```jsonline"})
                prefill_used = True

            self._check_stop_requested()

            # 单 worker 模式下打印翻译上下文摘要，方便调试
            if self.pj_config.active_workers == 1:
                _label = '校对' if proofread else '翻译'
                _round = '首轮' if is_first_round else '续轮'
                _retry = f' 重试{attempt}' if attempt > 0 else ''
                _global = '✓' if self._global_prompt else '✗'
                _fm = '✓' if filename in self._file_metadata_by_file else '✗'
                _bm = '✓' if filename in self._batch_metadata_by_file else '✗'
                _gl = '✗' if not gptdict else f'{gptdict.count(chr(124))}条'
                _bc = self.eng_type
                _glname = self.pj_config.getKey('gpt.translation_guideline') or '-'
                LOGGER.info(
                    f"-> {_label}输入[{_round}{_retry}] | "
                    f"backend={_bc} | guideline={_glname} | "
                    f"global={_global} filemeta={_fm} batchmeta={_bm} | "
                    f"gptdict={_gl}"
                )
                LOGGER.info("->输出：")

            # 流程 3：调 API
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
            # 与 ForBatchMetaData 一致：LLM 调用异常本地兜底，记录运行时错误后
            # 走统一解析失败分支（标 (Failed)），不再直接上抛依赖上游 job runner。
            try:
                raw_resp, token = await self._call_llm(
                    call_messages, filename, idx_tip, stream_callback
                )
            except Exception as e:
                # 取消信号不上抛时不应被本地截获——让它沿调用栈继续上抛到
                # Service.run_job_async 的 except JobCancelledError 处正常结束任务，
                # 避免被误记成 LLM 调用失败导致"最近错误"面板出现冗余 cancel 报错。
                if isinstance(e, JobCancelledError):
                    raise
                LOGGER.error(
                    f"[LLM调用失败][{filename}:{idx_tip}] {type(e).__name__}: {e}",
                    exc_info=True,
                )
                try:
                    from GalTransl.server import record_runtime_error

                    record_runtime_error(
                        getattr(
                            self.pj_config,
                            "runtime_project_dir",
                            self.pj_config.getProjectDir(),
                        ),
                        kind="llm",
                        message=f"{type(e).__name__}: {e}",
                        filename=filename,
                        index_range=str(idx_tip),
                        model=self.get_last_chatbot_model(),
                        level="error",
                    )
                except Exception:
                    pass
                # 空响应交给 _handle_parse_result 走失败兜底（标 (Failed)）
                raw_resp, token = "", None

            # 流程 4：解析结果
            stream_error_msg = stream_cursor.get("error", "")
            success_count, result_trans_list, real_success = self._handle_parse_result(
                raw_resp=raw_resp,
                token=token,
                trans_list=trans_list,
                n_symbol=n_symbol,
                sig_list=sig_list,
                is_stream=self.get_last_chatbot_stream(),
                stream_error_msg=stream_error_msg,
                stream_cursor=stream_cursor,
                stream_parsed_list=parsed_result_trans_list,
                idx_tip=idx_tip,
                filename=filename,
                proofread=proofread,
                call_messages=call_messages,
                prefill_used=prefill_used,
            )

            # 实时推送译文预览（前端翻译控制台右栏"译文预览"）
            if result_trans_list:
                _assembled = "\n".join(str(t) for t in result_trans_list)
                try:
                    set_live_snippets(_project_dir, translation_preview=_assembled)
                except Exception:
                    pass

            # 必须用 real_success（而非 success_count）判断，因失败兜底会将 success_count 覆盖为正数
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

            # 重试：重置会话为仅 [system]，以首轮方式重建
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
        filename: str,
        cache_file_path: str,
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

        # 按批次级元数据语义段边界分组（无元数据时退化为单组）
        groups = self._group_by_batch_metadata(translist_unhit, filename)

        # 有元数据时每段作单一翻译单元（大段不二次切割）；无元数据走原固定切片
        # 待废弃：下述「无元数据」分支即 count-based 切批（num_pre_request），将被 BatchMetadata 取代
        bm = self._resolve_batch_metadata(filename)
        if bm is None or not getattr(bm, "batches", None):
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
                h_scene=self._group_is_h_scene(translist_unhit, filename),
            )

        # 有批次元数据：每段作为单一翻译单元，组间共用同一文件对话（Option A，不重置）
        # 以保持跨段剧情/人设连续性；该段元数据在对应单元注入（_build_round_user_content 每轮均注入）。
        merged: CTransList = []
        for group in groups:
            if not group:
                continue
            res = await self._batch_translate_common(
                filename=filename,
                cache_file_path=cache_file_path,
                translist_unhit=group,
                num_pre_request=len(group),
                gpt_dic=gpt_dic,
                proofread=proofread,
                glossary_style="gpt",
                failed_markers=("(Failed)", "(翻译失败)"),
                h_words_list=H_WORDS_LIST,
                ensure_last_translations=True,
                force_static=True,  # 有元数据：禁用动态模式，段内不沿 numPerRequestTranslate 切
                h_scene=self._group_is_h_scene(group, filename),
            )
            merged.extend(res)
        return merged

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

剧情元数据（FileMetaData）的注入有两路来源，均由「第一轮对话」写入提示词：

1. 显式注入：上层在调用 batch_translate 前，通过
   ``translator.set_file_metadata(metadata, filename)`` 为该文件设置元数据；
2. 自动载入：后端在首次需要时为该文件惰性读取 gt_input（及其上层目录）中的
   ``FileMetaData.json``（JSON 数组，每项 ``id`` 对应一个待翻译文件名），
   按文件名（含分批后缀 ``_N`` 的剥离）匹配对应条目。

显式注入优先级高于自动载入；两路皆无对应条目时该文件首轮不附带剧情元数据。
（元数据仅在第一轮对话中出现，后续轮次不再重复发送。）
"""


if __name__ == "__main__":
    pass
