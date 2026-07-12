import json, time, asyncio, os, traceback, re
from opencc import OpenCC
from typing import Optional, List, Set

from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProxyPool
from GalTransl import LOGGER, LANG_SUPPORTED, TRANSLATOR_DEFAULT_ENGINE
from GalTransl.i18n import get_text, GT_LANG
from sys import exit, stdout
from GalTransl.ConfigHelper import (
    CProjectConfig,
)
from random import choice
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


"""
ForGalJsonTranslate - 基于 JSON-line 格式的视觉小说脚本翻译后端

该翻译器将待翻译的句子列表编码为 "sig|JSON" 格式的 jsonline 文本，
通过 LLM（大语言模型）进行批量翻译，再将响应解析回 CSentense 对象。

核心流程：
  1. 将 CTransList 中的每个 CSentense 编码为 "3位随机签名|JSON对象" 的 jsonline 行
  2. 将 jsonline 嵌入 Prompt 模板，发送给 OpenAI 兼容 API
  3. 解析 LLM 返回的 jsonline 结果，校验签名/id/字段完整性
  4. 将翻译结果写回 CSentense.pre_dst，支持多次重试与容错

继承自 BaseTranslate，复用上下文管理、缓存读写、动态句数调节等通用逻辑。
"""
class ForGalJsonTranslate(BaseTranslate):
    """
    ForGalJsonTranslate - 基于 JSON-line 格式的视觉小说脚本翻译后端

    核心流程：
      1. 将 CTransList 中的每个 CSentense 编码为 "3位随机签名|JSON对象" 的 jsonline 行
      2. 将 jsonline 嵌入 Prompt 模板，发送给 OpenAI 兼容 API
      3. 解析 LLM 返回的 jsonline 结果，校验签名/id/字段完整性
      4. 将翻译结果写回 CSentense.pre_dst，支持多次重试与容错

    继承自 BaseTranslate，复用上下文管理、缓存读写、动态句数调节等通用逻辑。
    """

    # 用于生成 jsonline 签名的字符集，每个句子分配 3 位随机签名用于防串行校验
    _SIGCHARS = "abcdefghijklmnopqrstuvwxyz0123456789"

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

    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool],
        token_pool: COpenAITokenPool,
    ):
        """
        初始化 ForGalJsonTranslate 翻译器实例

        加载 jsonline 格式专用的 Prompt 模板，初始化 OpenAI 兼容客户端，
        并设置增强 jailbreak 等配置选项。

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
        # 预输出 ```jsonline 来引导模型输出正确格式
        if val := config.getKey("gpt.enhance_jailbreak"):
            self.enhance_jailbreak = val
        else:
            self.enhance_jailbreak = False

        # 按文件名存储最近一次翻译结果，用于上下文恢复
        self.last_translations = {}
        self.init_chatbot(eng_type=eng_type, config=config)
        self._set_temp_type("precise")

        pass

    async def translate(
        self, trans_list: CTransList, gptdict="", proofread=False, filename=""
    ):
        input_list = []
        sig_list = []
        tmp_enhance_jailbreak = False
        n_symbol = ""
        idx_tip = self._build_idx_tip(trans_list)

        # 遍历每个待翻译句子，将其编码为 jsonline 行
        for i, trans in enumerate(trans_list):
            # 获取说话人名称，去除换行和制表符，避免破坏 jsonline 格式
            speaker_name = trans.get_speaker_name()
            speaker = speaker_name if speaker_name else "null"
            speaker = speaker.replace("\r\n", "").replace("\t", "").replace("\n", "")
            src_text = trans.post_src

            # 检测原文中的换行符类型，统一记录为 n_symbol（用于后处理还原）
            if "\\r\\n" in src_text:
                n_symbol = "\\r\\n"
            elif "\r\n" in src_text:
                n_symbol = "\r\n"
            elif "\\n" in src_text:
                n_symbol = "\\n"
            elif "\n" in src_text:
                n_symbol = "\n"

            # 将制表符和换行符替换为 LLM 友好格式：
            # \t → [t]（避免与 jsonline 格式冲突）
            # 换行符 → <br>（LLM 更容易理解）
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
        # 将所有 jsonline 行拼接为最终输入文本
        input_src = "\n".join(input_list)

        # 恢复历史翻译上下文，让 LLM 了解前文的翻译结果
        self.restore_context(trans_list, self.contextNum, filename)

        # 将输入和字典填充到 Prompt 模板中
        prompt_template = self._build_prompt_request(input_src, gptdict)

        retry_count = 0
        emitted_success_indices = set()
        while True:  # 一直循环，直到成功解析或超过重试上限
            self._check_stop_requested()
            # 增强 jailbreak：在 assistant 角色预输出 ```jsonline，
            # 引导模型输出正确的 jsonline 代码块格式
            if self.enhance_jailbreak or tmp_enhance_jailbreak:
                assistant_prompt = "```jsonline"
            else:
                assistant_prompt = ""

            # 构建发给 LLM 的消息列表
            messages = []
            messages.append({"role": "system", "content": self.system_prompt})
            prompt_req = self._apply_history_result(prompt_template, filename)
            messages.append({"role": "user", "content": prompt_req})
            if assistant_prompt:
                messages.append({"role": "assistant", "content": assistant_prompt})

            # 单 worker 模式下打印翻译输入输出日志，方便调试
            if self.pj_config.active_workers == 1:
                LOGGER.info(
                    f"->{'翻译输入' if not proofread else '校对输入'}：\n{gptdict}\n{input_src}\n"
                )
                LOGGER.info("->输出：")
            parsed_result_trans_list = []
            stream_parse_error_message = ""
            stream_cursor = {"i": -1, "success_count": 0, "started": False}

            def _parse_stream_lines(lines, is_final_chunk):
                nonlocal stream_parse_error_message, parsed_result_trans_list
                if stream_parse_error_message:
                    return False
                key_name = "dst" if not proofread else "newdst"
                for raw_line in lines:
                    line = raw_line.strip()
                    if not line:
                        continue
                    # 跳过 markdown 代码块标记
                    if line.startswith("```"):
                        continue
                    # 定位第一个有效 jsonline 行的起始位置
                    if not stream_cursor["started"]:
                        sig_start = re.search(r"\b[a-z0-9]{3}\|\{\"id\"", line)
                        if sig_start:
                            line = line[sig_start.start() :]
                            stream_cursor["started"] = True
                        else:
                            continue
                    line = fix_quotes(line)
                    parse_ok, parse_error = self._parse_jsonline_result_line(
                        line,
                        trans_list,
                        getattr(self, "_last_chatbot_model_name", ""),
                        n_symbol,
                        key_name,
                        stream_cursor,
                        parsed_result_trans_list,
                        filename=filename,
                        emit_runtime_success=(not proofread),
                        emitted_success_indices=emitted_success_indices,
                        sig_list=sig_list,
                    )
                    if not parse_ok:
                        stream_parse_error_message = parse_error
                        return False
                return True

            resp = None
            # 调用 LLM API，支持流式和非流式两种模式
            resp, token = await self.ask_chatbot(
                messages=messages,
                file_name=f"{filename}:{idx_tip}",
                base_try_count=retry_count,
                stream_line_callback=_parse_stream_lines,
            )

            result_text = resp or ""

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

            i = -1
            success_count = 0
            result_trans_list = []
            result_lines = result_text.splitlines()
            error_flag = False
            error_message = ""
            key_name = "dst" if not proofread else "newdst"

            if result_text == "":
                error_message = "输出为空/被拦截"
                error_flag = True

            if getattr(self, "_last_chatbot_was_stream", False):
                if stream_parse_error_message:
                    error_message = stream_parse_error_message
                    error_flag = True
                result_trans_list = parsed_result_trans_list
                success_count = len(parsed_result_trans_list)
                i = stream_cursor["i"]
            else:
                # 非流式模式：对完整响应逐行解析
                for line in result_lines:
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
                        emitted_success_indices=emitted_success_indices,
                        sig_list=sig_list,
                    )
                    if not parse_ok:
                        error_message = parse_error
                        error_flag = True
                        break
                    i += 1
                    success_count += 1
                    if i >= len(trans_list) - 1:
                        break

            # 部分解析成功时清除错误标记（允许部分结果）
            if success_count > 0 and not stream_parse_error_message:
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
                        getattr(self.pj_config, "runtime_project_dir", self.pj_config.getProjectDir()),
                        kind="parse",
                        message=error_message,
                        filename=filename,
                        index_range=str(idx_tip),
                        retry_count=retry_count + 1,
                        model=getattr(token, "model_name", ""),
                        level="warning",
                    )
                except Exception:
                    pass

                LOGGER.warning(
                    f"[解析错误][{filename}:{idx_tip}]解析结果出错：{error_message}"
                )
                retry_count += 1
                self._check_stop_requested()
                await asyncio.sleep(1)

                tmp_enhance_jailbreak = not tmp_enhance_jailbreak

                # 重试策略1：第2次重试时，将句子列表拆分为 1/3 大小递归重试
                if retry_count == 2 and len(trans_list) > 1 and self.smartRetry:
                    retry_count -= 1
                    LOGGER.warning(
                        f"[解析错误][{filename}:{idx_tip}]连续2次出错，尝试拆分重试"
                    )
                    return await self.translate(
                        trans_list[: max(len(trans_list) // 3,1)],
                        gptdict,
                        proofread=proofread,
                        filename=filename,
                    )
                # 重试策略2：第3次重试时，清空历史翻译上下文，重置会话
                if retry_count == 3 and self.smartRetry:
                    self.last_translations[filename] = ""
                    LOGGER.warning(
                        f"[解析错误][{filename}:{idx_tip}]连续3次出错，尝试清空上文"
                    )
                # 重试策略3：超过4次重试，放弃本轮翻译，标记为失败
                if retry_count >= 4:
                    self.last_translations[filename] = ""
                    LOGGER.error(
                        f"[解析错误][{filename}:{idx_tip}]解析反复出错，跳过本轮翻译"
                    )
                    i = self._append_parse_failure_fallback_results(
                        trans_list,
                        0 if i < 0 else i,
                        result_trans_list,
                        getattr(token, "model_name", ""),
                        proofread=proofread,
                        translate_failed_prefix="(Failed)",
                        translate_problem_message="翻译失败",
                        proofread_problem_message="翻译失败",
                        proofread_problem_append=True,
                    )
                    return i, result_trans_list
                continue
            elif error_flag == False and error_message:
                LOGGER.warning(
                    f"[{filename}:{idx_tip}]解析了{len(trans_list)}句中的{success_count}句，存在问题：{error_message}"
                )

            # 翻译完成，收尾
            break
        return success_count, result_trans_list

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

    def reset_conversation(self, filename=""):
        """
        重置会话上下文，清空指定文件的历史翻译记录

        Args:
            filename: 要重置的文件名，为空时重置所有
        """
        self.last_translations[filename] = ""

    def _format_restore_context_line(self, current_tran: CSentense) -> str:
        """
        将单个 CSentense 格式化为历史上下文行（jsonline 格式）

        签名固定为 "old"，JSON 包含 id/name/dst 字段。

        Args:
            current_tran: 当前句子对象

        Returns:
            编码后的 jsonline 行
        """
        speaker_name = current_tran.get_speaker_name()
        speaker = speaker_name if speaker_name else "null"
        tmp_obj = {
            "id": current_tran.index,
            "name": speaker,
            "dst": current_tran.pre_dst,
        }
        if speaker == "null":
            del tmp_obj["name"]
        return self._encode_sig_jsonline("old", tmp_obj)

    def _format_restore_context_payload(self, lines: List[str]) -> str:
        """
        将多行历史上下文包装为 markdown 代码块

        Args:
            lines: 历史上下文行列表

        Returns:
            包装后的代码块字符串
        """
        return "```jsonline\n" + "\n".join(lines) + "\n```"


if __name__ == "__main__":
    pass