"""残留日文修复后端：针对译文「残留日文」问题，用 AI 修复残留的日文假名。

仅向 AI 发送带有「残留日文」问题标注的译文（同时携带对应原文 src，不带 problem
标注），AI 返回修复残留日文后的备选译文，后端解析、按 id 稀疏匹配、筛查后写入
各句 alt_dst，不破坏既有主译文。

作为完整流水线的独立修复阶段使用；也可被独立选中，对已翻译文件单独执行。
引擎标识：ForJPResidue
"""

import json
import re
from typing import List, Optional, Union

from GalTransl import LOGGER
from GalTransl.CSentense import CTransList
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.ForGalJsonMulitChat import ForGalJsonMulitChat
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_JPREPAIR_PROMPT,
    FORJP_SYSTEM,
)
from GalTransl.ConfigHelper import CProblemType
from GalTransl.Service import JobCancelledError
from GalTransl.Utils import extract_code_blocks, fix_quotes


@register_engine("ForJPResidue")
class ForJPResidue(ForGalJsonMulitChat):
    """
    残留日文修复后端：向 AI 发送「文件级元数据 + 翻译规范 + 字典 + 当前译文
    （仅含残留日文问题标注的句子，并携带对应原文 src，不带 problem 标注）」，
    让模型对照原文修复残留日文，并把备选译文写入各句 alt_dst。

    引擎标识：ForJPResidue
    """

    def __init__(
        self,
        config,
        eng_type: str,
        proxy_pool=None,
        token_pool=None,
    ) -> None:
        """
        初始化残留日文修复后端。

        Args:
            config: 项目配置对象。
            eng_type: 引擎标识（ForJPResidue）。
            proxy_pool: 代理池对象，为 None 时不使用代理。
            token_pool: API Token 池。
        """
        super().__init__(config, eng_type, proxy_pool, token_pool)
        # 覆盖基类（翻译轮）的系统提示词为残留日文修复轮专用角色声明
        self.system_prompt = FORJP_SYSTEM
        self.trans_prompt = FORGAL_JSON_JPREPAIR_PROMPT
        # 覆盖默认值后重新应用用户模板 override（基类 __init__ 已应用过一次）
        self._apply_internal_prompt_template_overrides()

    def _has_jp_residue(self, tran) -> bool:
        """判断单句是否带有「残留日文」问题且译文有效、可送审。

        与 ForBRStation._has_newline_anomaly 同构：先做译文有效性前置过滤
        （排除空译文、失败译文、用户标记跳过检查的句子），再用统一口径匹配
        CProblemType.残留日文，避免与问题检测逻辑口径不一致。

        Args:
            tran: 待判断的 CTrans 句子对象。

        Returns:
            是否应送入残留日文修复。
        """
        if not tran.problem:
            return False
        if tran.post_src == "" or tran.pre_dst == "":
            return False
        if "(Failed)" in tran.pre_dst:
            return False
        if getattr(tran, "skip_check", False):
            return False
        kept = ForGalJsonMulitChat._filter_problem_by_types(
            tran.problem, [CProblemType.残留日文]
        )
        return bool(kept)

    async def batch_translate(
        self,
        filename: str,
        cache_file_path: str,
        trans_list: CTransList,
        num_pre_request: int,
        retry_failed: bool = False,
        gpt_dic=None,
        proofread: bool = False,
        retran_key: str = "",
        translist_hit: list = [],
        translist_unhit: list = [],
    ) -> CTransList:
        """
        翻译接口：对整文件中带有「残留日文」问题的译文执行残留日文修复。

        与 ForGalJsonMulitChat.batch_translate 同名同签名（"翻译接口"），
        使 LLMTranslate 能以统一的 batch_translate 驱动本后端。
        本后端不改写 pre_dst / trans_by，仅把模型给出的修复译文写入各句 alt_dst；
        输入输出的 trans_list 保持一致。

        Args:
            filename: 原始文件名（对话分桶键，不带分块后缀）。
            cache_file_path: 缓存文件路径（本后端不读写缓存，仅兼容签名）。
            trans_list: 全文件句子（含原文与当前译文）。
            num_pre_request: 每批句子数（受 gpt.numPerRequestBetter 覆盖）。
            retry_failed: 是否重试失败句（本后端未使用）。
            gpt_dic: 术语表对象（每批按本批句子注入，供术语一致性参考）。
            proofread: 是否校对模式（本后端恒以校对模式携带 src/dst）。
            retran_key: 重翻键（本后端未使用）。
            translist_hit: 命中缓存句子（本后端未使用）。
            translist_unhit: 未命中缓存句子（本后端未使用）。

        Returns:
            CTransList: 与输入一致（alt_dst 已就地更新）。
        """
        # 仅筛选带有「残留日文」问题标注、且已有有效译文的句子。
        target_trans_list = [t for t in trans_list if self._has_jp_residue(t)]
        total = len(target_trans_list)
        if total == 0:
            LOGGER.info(f"[残留日文] {filename} 无可处理的残留日文译文，跳过")
            return trans_list

        # 用原始文件名独立分桶，从首轮重建（不混用翻译轮历史）
        self.reset_conversation(filename)
        self.conversations[filename] = [
            {"role": "system", "content": self.system_prompt}
        ]

        num_per_request = self._coerce_positive_int(
            self.pj_config.getKey("gpt.numPerRequestBetter"), num_pre_request or 100
        )
        total_batches = (total + num_per_request - 1) // num_per_request
        fix_count = 0
        LOGGER.info(
            f"[残留日文] {filename} 开始残留日文修复，共 {total} 句，"
            f"{total_batches} 批"
        )

        for batch_no, start in enumerate(range(0, total, num_per_request), start=1):
            self._check_stop_requested()
            batch = target_trans_list[start : start + num_per_request]
            idx_tip = self._build_idx_tip(batch)
            # 每批按本批句子重新生成术语表（与翻译轮一致，按需注入）；
            # h/非h 场景分流：本批处于 h 区间注入 h 字典，否则只注入非 h 字典
            batch_gptdict = ""
            if gpt_dic is not None:
                try:
                    batch_gptdict = gpt_dic.gen_prompt(
                        batch,
                        scene="h" if self._group_is_h_scene(batch, filename) else "nh",
                    )
                except Exception:
                    batch_gptdict = ""
            # 输入携带当前生效译文（proofread_zh 优先，否则 pre_dst）与原文 src，
            # 不注入 problem：残留日文需对照原文判断应译/应留，交由 AI 自行决策，
            # 不被 problem 字符串引导。
            input_list, sig_list, n_symbol, input_src = self._build_input_jsonlines(
                batch,
                proofread=True,
                filename=filename,
                include_src=True,
            )
            conv = self._ensure_conversation(filename)
            is_first_round = len(conv) <= 1
            if is_first_round:
                user_content = self._build_jp_first_round_content(
                    input_src, batch_gptdict, filename
                )
            else:
                # 续轮同样注入本批术语表，与翻译轮行为一致
                user_content = (
                    batch_gptdict + "\n以下是本批次待处理内容：\n" + input_src
                    if batch_gptdict
                    else input_src
                )
            call_messages = conv + [{"role": "user", "content": user_content}]

            # 单 worker 模式下打印输入上下文摘要，便于调试
            if self.pj_config.active_workers == 1:
                _round = "首轮" if is_first_round else "续轮"
                LOGGER.info(
                    f"-> 残留日文修复输入[{_round}] | backend={self.eng_type} | "
                    f"sentences={len(batch)}"
                )
                LOGGER.info("->输出：")

            try:
                raw_resp, _token = await self._call_llm(
                    call_messages, filename, idx_tip, None
                )
            except JobCancelledError:
                raise
            except Exception as e:
                LOGGER.warning(
                    f"[残留日文][{filename}:{idx_tip}]LLM调用失败："
                    f"{type(e).__name__}: {e}"
                )
                self._record_jp_runtime_error(
                    filename, idx_tip, f"{type(e).__name__}: {e}", None
                )
                self.reset_conversation(filename)
                continue

            # 解析结果：与改进轮一致，输出为稀疏序列（跳过无需修复的句子），
            # 必须按 id 匹配而非按输出顺序，故不复用翻译轮的顺序解析器。
            # 批内解析失败仅跳过本批，不标 (Failed)、不改写主译文。
            result_text = raw_resp or ""
            if "</think>" in result_text:
                result_text = result_text.split("</think>")[-1]
            if "```json" in result_text:
                _lang_list, code_list = extract_code_blocks(result_text)
                if code_list:
                    # 合并所有代码块而非只取第一个：模型偶尔把输出切成多段 jsonline
                    # 代码块，只取 [0] 会丢失后续句子的修复译文
                    if len(code_list) > 1:
                        LOGGER.debug(
                            f"[残留日文][{filename}] 模型输出 {len(code_list)} 个代码块，"
                            f"已合并解析"
                        )
                    result_text = "\n".join(code_list)
            sig_start = re.search(r"\b[a-z0-9]{3}\|\{\"id\"", result_text)
            if sig_start:
                result_text = result_text[sig_start.start() :]
            result_text = fix_quotes(result_text)
            success_count, found_count = self._parse_jp_jsonline_text(
                result_text, batch, n_symbol
            )

            # 模型响应非空、却未解析到任何可用 better——多为输出格式异常
            # （如漏 better 键、把输入回显、或多段代码块外残留），日志警告并上报
            # 控制台"最近错误"，便于定位模型输出格式问题。
            if result_text.strip() and found_count == 0:
                _jp_warn_msg = (
                    f"[残留日文][{filename}:{idx_tip}] 模型响应非空但未解析到任何 "
                    f"better（输出格式异常或内容问题），本次 0 句修复"
                )
                LOGGER.warning(_jp_warn_msg)
                self._record_jp_runtime_error(filename, idx_tip, _jp_warn_msg, None)

            # 追加 assistant 回复进对话，保持轮次交替（空输出也追加，确保续轮识别）
            self.conversations[filename] = self._trim_conversation(
                call_messages + [{"role": "assistant", "content": raw_resp or ""}]
            )
            fix_count += success_count
            LOGGER.debug(
                f"[残留日文] {filename} 批次 {batch_no}/{total_batches}（序号 {idx_tip}）"
                f"已评估，修复 {success_count} 句"
            )

        if fix_count > 0:
            LOGGER.info(f"[残留日文] {filename} 共修复 {fix_count} 句")
        else:
            LOGGER.info(f"[残留日文] {filename} 未发现需修复的残留日文")
        return trans_list

    def _build_jp_first_round_content(
        self, input_src: str, gptdict: str, filename: str
    ) -> str:
        """残留日文修复首轮内容：以专用提示词为模板，注入术语表、剧情元数据与输入。

        Args:
            input_src: 拼接后的待修复输入文本（含 src 与 dst，不含 problem）。
            gptdict: 本批术语表（GPT 字典）文本。
            filename: 当前文件名。

        Returns:
            首轮用户消息字符串。
        """
        prompt_req = self.trans_prompt
        prompt_req = prompt_req.replace(
            "[translation_guideline]", self.pj_config.translation_guideline
        )
        prompt_req = prompt_req.replace("[Input]", input_src)
        prompt_req = prompt_req.replace("[Glossary]", gptdict)
        metadata = self._resolve_file_metadata(filename)
        metadata_block = (
            self._format_file_metadata_block(metadata)
            if metadata is not None
            else ""
        )
        prompt_req = prompt_req.replace("[plot_metadata]", metadata_block)
        prompt_req = prompt_req.replace("[batch_metadata]", "")
        # 全局提示词（GlobalPrompt）：仅首轮注入，与翻译轮一致
        prompt_req = prompt_req.replace(
            "[global_prompt]", self._format_global_prompt_block(filename) or ""
        )
        prompt_req = prompt_req.replace("[SourceLang]", self.source_lang)
        prompt_req = prompt_req.replace("[TargetLang]", self.target_lang)
        prompt_req = self._apply_history_result(prompt_req, filename)
        return prompt_req

    def _parse_jp_jsonline_text(
        self, result_text: str, trans_list: CTransList, n_symbol: str
    ) -> tuple:
        """按 id 稀疏解析残留日文修复输出，把 better 写入对应句子的 alt_dst。

        与 ForBRStation._parse_br_jsonline_text 同构：稀疏序列、按 id 定位、
        换行防御替换、与当前主译文比对去重。

        Returns:
            (success_count, found_count)
            - success_count: 实际写入 alt_dst 的句子数（better 与当前译文不同）
            - found_count: 模型给出了可用 better 且命中本批句子的行数（含与当前
              译文相同而跳过的行）。供调用方判断「响应非空但未解析到 better」。
        """
        id_map = {t.index: t for t in trans_list}
        success_count = 0
        found_count = 0
        for line in result_text.splitlines():
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                # 带哈希锚点的行：取 | 后的 JSON 部分
                json_part = line.split("|", 1)[1].strip()
            else:
                # 容错：模型未带锚点时整行按 JSON 解析
                json_part = line
            obj = self._decode_json_part(json_part)
            if obj is None:
                continue
            line_id = obj.get("id")
            better = obj.get("better")
            if not isinstance(line_id, int) or not isinstance(better, str):
                continue
            tran = id_map.get(line_id)
            if tran is None:
                continue
            # 空译文 / 含乱码：与翻译轮解析一致地过滤，避免污染备选译文
            if tran.post_src != "" and better.strip() == "":
                continue
            if "�" in better:
                continue
            # 模型确实给出了可用 better，计入 found_count
            found_count += 1
            # 换行符防御替换与恢复：复用翻译轮的归一化（<BR>/真实换行→<br>→n_symbol），
            # 使 better 与送入模型的当前译文处于同一换行表示后再比较
            normalized = self._normalize_parsed_translation_text(
                better, tran, n_symbol
            )
            # 当前主译文：校对结果优先，否则初译
            current_dst = (
                tran.proofread_zh if tran.proofread_zh != "" else tran.pre_dst
            )
            # 未实际改进（含仅换行表示差异）：跳过，不写 alt_dst、不计修复数
            if current_dst != "" and normalized.strip() == current_dst.strip():
                LOGGER.debug(
                    f"[残留日文] 句子 {line_id} 的 better 与当前译文相同，跳过"
                )
                continue
            tran.alt_dst = normalized
            success_count += 1
        return success_count, found_count

    @staticmethod
    def _decode_json_part(json_part: str) -> Optional[dict]:
        """容错解析单行 JSON 对象。

        优先严格 `json.loads`；失败时从首个 `{` 起用 `json.JSONDecoder.raw_decode`
        解析第一个 JSON 值并忽略对象后的尾随垃圾（模型可能误加 `</br>`、`；` 等），
        从而不丢本可用的修复译文。非 dict 或无法解析返回 None。
        """
        try:
            obj = json.loads(json_part)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
        start = json_part.find("{")
        if start == -1:
            return None
        try:
            obj, _end = json.JSONDecoder().raw_decode(json_part[start:])
        except Exception:
            return None
        return obj if isinstance(obj, dict) else None

    def _record_jp_runtime_error(
        self, filename: str, idx_tip: str, message: str, model: Optional[str]
    ) -> None:
        """残留日文修复运行态错误上报（工作台"最近错误"卡片）。"""
        try:
            from GalTransl.server_runtime import record_runtime_error

            record_runtime_error(
                filename=filename,
                engine=self.eng_type,
                stage="残留日文修复",
                message=message,
                batch_index=idx_tip,
                model=model,
            )
        except Exception as e:
            LOGGER.debug(f"[残留日文] 运行态错误上报失败: {e}")
