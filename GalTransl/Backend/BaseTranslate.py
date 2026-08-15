import asyncio
from typing import Any, Optional, List
from GalTransl.ConfigHelper import CProxyPool
from GalTransl import LOGGER
from GalTransl.ConfigHelper import (
    CProjectConfig,
)
from GalTransl.CSentense import CSentense, CTransList
from GalTransl.Cache import save_transCache_to_json
from GalTransl.Dictionary import CGptDict
from GalTransl.Utils import fix_quotes2
from GalTransl.Backend.BaseEngine import BaseEngine
from openai._types import NOT_GIVEN
import re
import sys
from GalTransl.TerminalOutput import should_print_translation_logs


def _print_translation_block(text: str) -> None:
    """终端打印整批译文；GBK 等终端编码失败时按 replace 降级，避免崩溃。"""
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    print(text)


class BaseTranslate(BaseEngine):
    """翻译引擎基类：在多轮对话/全量流水线等「翻译轮」后端之上提供共享的切批、
    动态句数、上下文恢复、译文归一化与缓存落盘等翻译流水线逻辑。

    继承 BaseEngine（API 客户端层），本类仅承载翻译相关流程，不包含底层 LLM 调用。
    """

    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool] = None,
        token_pool=None,
    ) -> None:
        """
        Args:
            config: 项目配置对象。
            eng_type: 引擎类型标识。
            proxy_pool: 代理池对象，为 None 时不使用代理。
            token_pool: API Token 池，管理多个 API 密钥的轮换。
        """
        super().__init__(config, eng_type, proxy_pool, token_pool)
        # 上下文恢复模式（翻译轮多轮对话用）
        self.restore_context_mode = config.getKey("gpt.restoreContextMode", True)
        # 多轮对话上下文句数
        self.contextNum: int = config.getKey("gpt.contextNum", 8)
        # 智能重试
        self.smartRetry: bool = config.getKey("smartRetry", True)
        # 动态句数（按 429/解析异常自动调节每批句数）
        self.dynamic_num_per_request = self._coerce_bool(
            config.getKey("gpt.dynamicNumPerRequestTranslate", False)
        )
        self.dynamic_num_per_request_min = self._coerce_positive_int(
            config.getKey("gpt.dynamicNumPerRequestTranslate.min", 1), 1
        )
        self.dynamic_num_per_request_max = self._coerce_positive_int(
            config.getKey("gpt.dynamicNumPerRequestTranslate.max", 16), 16
        )
        if self.dynamic_num_per_request_min > self.dynamic_num_per_request_max:
            self.dynamic_num_per_request_min, self.dynamic_num_per_request_max = (
                self.dynamic_num_per_request_max,
                self.dynamic_num_per_request_min,
            )
        self._dynamic_num_per_request_current: Optional[int] = None
        self._dynamic_num_per_request_success_streak = 0

    def _get_effective_num_per_request(
        self, configured_value: int, proofread: bool = False, force_static: bool = False
    ) -> int:
        configured = self._coerce_positive_int(configured_value, 1)
        if proofread or not self.dynamic_num_per_request or force_static:
            return configured

        if self._dynamic_num_per_request_current is None:
            self._dynamic_num_per_request_current = min(
                self.dynamic_num_per_request_max,
                max(self.dynamic_num_per_request_min, configured),
            )
        return self._dynamic_num_per_request_current

    def _update_dynamic_num_per_request(
        self,
        requested_count: int,
        completed_count: int,
        trans_result: CTransList,
        filename: str,
        proofread: bool = False,
        force_static: bool = False,
    ) -> None:
        if proofread or not self.dynamic_num_per_request or force_static:
            return

        current = self._get_effective_num_per_request(requested_count, proofread=False)
        failed_markers = ("(Failed)", "(翻译失败)")
        has_failed_result = any(
            any(marker in getattr(trans, "pre_dst", "") for marker in failed_markers)
            for trans in trans_result
        )
        has_parse_issue = completed_count < requested_count or has_failed_result

        if has_parse_issue:
            next_value = max(self.dynamic_num_per_request_min, max(1, current // 2))
            self._dynamic_num_per_request_success_streak = 0
            if next_value != current:
                LOGGER.warning(
                    f"[{filename}]动态句数调整：检测到解析异常，单次翻译句数 {current} -> {next_value}"
                )
                self._dynamic_num_per_request_current = next_value
            return

        if completed_count >= requested_count and requested_count == current:
            self._dynamic_num_per_request_success_streak += 1
            if (
                self._dynamic_num_per_request_success_streak >= 3
                and current < self.dynamic_num_per_request_max
            ):
                next_value = min(self.dynamic_num_per_request_max, current + 1)
                LOGGER.info(
                    f"[{filename}]动态句数调整：连续成功，单次翻译句数 {current} -> {next_value}"
                )
                self._dynamic_num_per_request_current = next_value
                self._dynamic_num_per_request_success_streak = 0

    @staticmethod
    def _build_idx_tip(trans_list: CTransList) -> str:
        start_idx = trans_list[0].index
        end_idx = trans_list[-1].index
        if start_idx != end_idx:
            return f"{start_idx}~{end_idx}"
        return str(start_idx)

    def _apply_history_result(self, prompt_req: str, filename: str) -> str:
        if (
            hasattr(self, "last_translations")
            and filename in self.last_translations
            and self.last_translations[filename] != ""
        ):
            history_result = self.last_translations[filename].replace("<br>", "")
            return prompt_req.replace("[history_result]", history_result)
        # 无历史记录时移除历史相关内容，避免提示词残留
        prompt_req = re.sub(
            r"\s*<history_result>\s*\[history_result\]\s*</history_result>\s*",
            "\n",
            prompt_req,
        )
        # 移除 <process_requirements> 内的历史小节（非贪婪匹配避免跨块误删翻译规范中的 ### 标题）
        prompt_req = re.sub(
            r"(<process_requirements>)(.*?)(</process_requirements>)",
            lambda m: m.group(1)
            + re.sub(
                r"###\s*.*?(?:history|历史).*$(?:\n(?:.*$))*?(?=\n### |\Z)",
                "",
                m.group(2),
                flags=re.M | re.I,
            )
            + m.group(3),
            prompt_req,
            flags=re.S,
        )
        return prompt_req

    def _record_runtime_success(self, filename: str, trans: CSentense) -> None:
        try:
            from GalTransl.server import record_runtime_success

            record_runtime_success(
                getattr(
                    self.pj_config,
                    "runtime_project_dir",
                    self.pj_config.getProjectDir(),
                ),
                filename=filename,
                index=getattr(trans, "runtime_index", getattr(trans, "index", 0)),
                speaker=getattr(trans, "speaker", None),
                source_preview=getattr(trans, "post_src", ""),
                translation_preview=getattr(trans, "pre_dst", ""),
                trans_by=getattr(trans, "trans_by", ""),
            )
        except Exception:
            pass

    def _normalize_parsed_translation_text(
        self, line_dst: str, current_tran: CSentense, n_symbol: str
    ) -> str:
        if "Chinese" in self.target_lang:
            line_dst = self.opencc.convert(line_dst)

        if "”" not in current_tran.post_src and '"' not in current_tran.post_src:
            line_dst = line_dst.replace('"', "")
        elif '"' not in current_tran.post_src and '"' in line_dst:
            line_dst = fix_quotes2(line_dst)
        elif '"' in current_tran.post_src and "”" in line_dst:
            line_dst = line_dst.replace("“", '"')
            line_dst = line_dst.replace("”", '"')

        if not line_dst.startswith("「") and current_tran.post_src.startswith("「"):
            line_dst = "「" + line_dst
        if not line_dst.endswith("」") and current_tran.post_src.endswith("」"):
            line_dst = line_dst + "」"

        line_dst = line_dst.replace("[t]", "\t")
        # 统一各类换行标记为 <br>（兼容大小写/真实换行），再整体还原为源约定
        line_dst = line_dst.replace("<BR>", "<br>")
        line_dst = line_dst.replace("\r\n", "<br>").replace("\n", "<br>")
        if n_symbol:
            # 源使用真实/字面换行约定：把 <br> 还原为对应的 n_symbol
            line_dst = line_dst.replace("<br>", n_symbol)
        # 否则（n_symbol 为空，源以 <br> 为换行约定或不含换行）：
        # 保持 <br> 不变，与源约定一致。

        if "……" in current_tran.post_src and "..." in line_dst:
            line_dst = line_dst.replace("......", "……")
            line_dst = line_dst.replace("...", "……")

        return line_dst

    def _append_parsed_translation_result(
        self,
        current_tran: CSentense,
        line_dst: str,
        model_name: str,
        cursor: dict,
        result_trans_list: list,
        filename: str = "",
        emit_runtime_success: bool = False,
        emitted_success_indices: Optional[set] = None,
        result_index: Optional[int] = None,
    ) -> tuple[bool, str]:
        current_tran.pre_dst = line_dst
        current_tran.post_dst = line_dst
        current_tran.trans_by = model_name
        if emit_runtime_success:
            if emitted_success_indices is None:
                emitted_success_indices = set()
            if result_index is not None and result_index not in emitted_success_indices:
                self._record_runtime_success(filename, current_tran)
                emitted_success_indices.add(result_index)
            current_tran._runtime_success_recorded = True
        result_trans_list.append(current_tran)
        cursor["success_count"] = cursor.get("success_count", 0) + 1
        return True, ""

    @staticmethod
    def _merge_problem_message(
        current_problem: str, message: str, append: bool = True
    ) -> str:
        current_problem = current_problem or ""
        message = message or ""
        if not message:
            return current_problem
        if not append:
            return message
        if message in current_problem:
            return current_problem
        if not current_problem:
            return message
        return f"{current_problem}, {message}"

    def _append_parse_failure_fallback_results(
        self,
        trans_list: CTransList,
        start_index: int,
        result_trans_list: list,
        model_name: str,
        proofread: bool = False,
        retain_failed: bool = True,
        translate_failed_prefix: str = "(Failed)",
        translate_problem_message: str = "翻译失败",
        proofread_problem_message: str = "翻译失败",
        proofread_problem_append: bool = True,
    ) -> int:
        if not retain_failed and not proofread:
            # 失败句子不保留：不写入 (Failed) 值、不追加进结果列表，
            # 直接返回成功句之后的游标（start_index 即成功句数），
            # 让上层按成功句数推进 i，失败句在下一轮被重新切入批次重试。
            # proofread 分支即将弃用，保持原逻辑不动。
            return max(0, start_index)

        i = max(0, start_index)
        failed_model_name = f"{model_name}(Failed)"
        while i < len(trans_list):
            current_tran = trans_list[i]
            if not proofread:
                failed_text = translate_failed_prefix + current_tran.post_src
                current_tran.pre_dst = failed_text
                current_tran.post_dst = failed_text
                current_tran.problem = self._merge_problem_message(
                    current_tran.problem, translate_problem_message, append=True
                )
                current_tran.trans_by = failed_model_name
            else:
                current_tran.proofread_zh = current_tran.pre_dst
                current_tran.post_dst = current_tran.pre_dst
                current_tran.problem = self._merge_problem_message(
                    current_tran.problem,
                    proofread_problem_message,
                    append=proofread_problem_append,
                )
                current_tran.proofread_by = failed_model_name
            result_trans_list.append(current_tran)
            i += 1
        return i

    async def _batch_translate_common(
        self,
        filename: str,
        cache_file_path: str,
        translist_unhit: CTransList,
        num_pre_request: int,
        gpt_dic: CGptDict = None,
        proofread: bool = False,
        glossary_style: str = "",
        failed_markers: tuple[str, ...] = ("(Failed)",),
        h_words_list: Optional[List[str]] = None,
        ensure_last_translations: bool = False,
        force_static: bool = False,
        h_scene: bool = False,
    ) -> CTransList:
        if len(translist_unhit) == 0:
            return []

        if self.skipH and h_words_list:
            translist_unhit = [
                tran
                for tran in translist_unhit
                if not any(word in tran.post_src for word in h_words_list)
            ]

        if ensure_last_translations and hasattr(self, "last_translations"):
            if filename not in self.last_translations:
                self.last_translations[filename] = ""

        i = 0
        trans_result_list = []
        len_trans_list = len(translist_unhit)
        transl_step_count = 0
        stall_count = 0  # 整批无进展的连续轮数，超过 3 轮则放弃本批失败句（不保留、留待下次运行）

        while i < len_trans_list:
            self._check_stop_requested()
            effective_num_pre_request = self._get_effective_num_per_request(
                num_pre_request,
                proofread=proofread,
                force_static=force_static,
            )
            trans_list_split = (
                translist_unhit[i : i + effective_num_pre_request]
                if (i + effective_num_pre_request < len_trans_list)
                else translist_unhit[i:]
            )

            if gpt_dic:
                dic_scene = "h" if h_scene else "nh"
                if glossary_style:
                    dic_prompt = gpt_dic.gen_prompt(trans_list_split, glossary_style, dic_scene)
                else:
                    dic_prompt = gpt_dic.gen_prompt(trans_list_split, scene=dic_scene)
            else:
                dic_prompt = ""

            num, trans_result = await self.translate(
                trans_list_split,
                dic_prompt,
                proofread=proofread,
                filename=filename,
            )

            if num <= 0:
                # 整批无成功句（失败句 pre_dst 保持空、不保留）：继续重试，
                # 连续 3 轮仍无进展则放弃本批失败句（不写缓存、留待下次运行），任务继续。
                stall_count += 1
                if stall_count >= 3:
                    LOGGER.warning(
                        f"[{filename}:{self._build_idx_tip(trans_list_split)}] "
                        f"连续 {stall_count} 轮翻译失败，放弃本批失败句子（不保留，留待下次运行）"
                    )
                    i += len(trans_list_split)
                    stall_count = 0
                    continue
                LOGGER.warning(
                    f"[{filename}:{self._build_idx_tip(trans_list_split)}] "
                    f"翻译无进展，第 {stall_count}/3 轮重试"
                )
                self._check_stop_requested()
                await asyncio.sleep(1)
                continue

            if num > 0:
                i += num
                stall_count = 0  # 有成功即视为进展，重置连续失败计数
            self.pj_config.bar(num)
            self._update_dynamic_num_per_request(
                requested_count=len(trans_list_split),
                completed_count=max(0, num),
                trans_result=trans_result,
                filename=filename,
                proofread=proofread,
                force_static=force_static,
            )

            result_output = ""
            for trans in trans_result:
                if (
                    not proofread
                    and trans.pre_dst
                    and not getattr(trans, "_runtime_success_recorded", False)
                    and not any(marker in trans.pre_dst for marker in failed_markers)
                ):
                    self._record_runtime_success(filename, trans)
                result_output += repr(trans)

            _print_translation_block(result_output)
            trans_result_list += trans_result
            transl_step_count += 1
            if transl_step_count >= self.save_steps:
                await save_transCache_to_json(
                    trans_result,
                    cache_file_path,
                    project_dir=getattr(
                        self.pj_config,
                        "runtime_project_dir",
                        self.pj_config.getProjectDir(),
                    ),
                )
                transl_step_count = 0

            if trans_result:
                trans_by = trans_result[0].trans_by
                LOGGER.info(
                    f"{filename}: {str(len(trans_result_list))}/{str(len_trans_list)} with {trans_by}"
                )

        return trans_result_list

    def translate(self, trans_list: CTransList, gptdict: str = "") -> None:
        pass

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
        h_scene: bool = False,
    ) -> CTransList:
        translist_unhit = list(trans_list)

        if self.skipH:
            LOGGER.warning("skipH: 将跳过含有敏感词的句子")
            h_words_list = globals().get("H_WORDS_LIST", [])
            translist_unhit = [
                tran
                for tran in translist_unhit
                if not any(word in tran.post_src for word in h_words_list)
            ]

        if len(translist_unhit) == 0:
            return []
        # 新文件重置chatbot
        if self.last_file_name != filename:
            self.reset_conversation()
            self.last_file_name = filename
        i = 0

        trans_result_list = []
        len_trans_list = len(translist_unhit)
        transl_step_count = 0
        while i < len_trans_list:
            # await asyncio.sleep(1)
            trans_list_split = (
                translist_unhit[i : i + num_pre_request]
                if (i + num_pre_request < len_trans_list)
                else translist_unhit[i:]
            )

            dic_prompt = (
                gpt_dic.gen_prompt(
                    trans_list_split, scene="h" if h_scene else "nh"
                )
                if gpt_dic
                else ""
            )

            num, trans_result = await self.translate(
                trans_list_split, dic_prompt, proofread=proofread
            )

            if num > 0:
                i += num
            result_output = ""
            for trans in trans_result:
                result_output = result_output + repr(trans)
            if should_print_translation_logs(self.pj_config):
                _print_translation_block(result_output)
            trans_result_list += trans_result
            transl_step_count += 1
            if transl_step_count >= self.save_steps:
                await save_transCache_to_json(
                    trans_result,
                    cache_file_path,
                    project_dir=getattr(
                        self.pj_config,
                        "runtime_project_dir",
                        self.pj_config.getProjectDir(),
                    ),
                )
                transl_step_count = 0
            if should_print_translation_logs(self.pj_config):
                LOGGER.info(
                    f"{filename}: {str(len(trans_result_list))}/{str(len_trans_list)}"
                )

        return trans_result_list

    def _get_restore_context_failed_markers(self) -> tuple[str, ...]:
        return ("(Failed)",)

    def _format_restore_context_line(self, current_tran: CSentense) -> str:
        raise NotImplementedError

    def _format_restore_context_payload(self, lines: List[str]) -> str:
        return "\n".join(lines)

    def _collect_restore_context_items(
        self, translist_unhit: CTransList, num_pre_request: int
    ) -> List[CSentense]:
        if translist_unhit[0].prev_tran == None:
            return []

        context_items: List[CSentense] = []
        num_count = 0
        current_tran = translist_unhit[0].prev_tran
        failed_markers = self._get_restore_context_failed_markers()

        while current_tran != None:
            if current_tran.pre_dst == "" or any(
                marker in current_tran.pre_dst for marker in failed_markers
            ):
                current_tran = current_tran.prev_tran
                continue

            context_items.append(current_tran)
            num_count += 1
            if num_count >= num_pre_request:
                break
            current_tran = current_tran.prev_tran

        context_items.reverse()
        return context_items

    def restore_context(
        self, translist_unhit: CTransList, num_pre_request: int, filename: str = ""
    ) -> None:
        if not hasattr(self, "last_translations"):
            return

        context_items = self._collect_restore_context_items(
            translist_unhit, num_pre_request
        )
        if not context_items:
            self.last_translations[filename] = ""
            return

        context_lines = [
            self._format_restore_context_line(current_tran)
            for current_tran in context_items
        ]
        self.last_translations[filename] = self._format_restore_context_payload(
            context_lines
        )

    def _set_temp_type(self, style_name: str) -> None:
        if self._current_temp_type == style_name:
            return
        self._current_temp_type = style_name
        temperature = 0.6
        frequency_penalty = NOT_GIVEN
        self.temperature = temperature
        self.frequency_penalty = frequency_penalty
