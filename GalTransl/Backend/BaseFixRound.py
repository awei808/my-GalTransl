"""稀疏修复轮 / 改进轮共享基类。

设计目标：
- 从多轮对话翻译后端 ForGalJsonTranslate 继承底层对话、术语表、元数据、API 调用能力；
- 把“筛选目标句子 → 分桶 → 构建首轮内容 → 调用 LLM → 稀疏解析 better → 错误上报”的公共流程收口；
- 按业务差异拆出两个基类：
    * BaseProblemFixRound：ForJPResidue / ForBanWordFix / ForBRStation 这类“按问题类型修复译文”的后端；
    * BaseImproveRound：ForImproveTranslation 以及未来可能新增的“全量质量改进/评估”类后端。
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from GalTransl import LOGGER
from GalTransl.CSentense import CTransList
from GalTransl.Service import JobCancelledError
from GalTransl.Backend.ForGalJsonTranslate import ForGalJsonTranslate
from GalTransl.Backend.Prompts import FAILED_PREFIX
from GalTransl.Backend.utils import (
    canonicalize_newlines,
    coerce_bool,
    decode_json_line_part,
    encode_line_breaks_for_prompt,
    preprocess_jsonline_response,
    prompt_newline_key,
)

# 问题修复/复核轮附带的问题句上下文条数：默认值与上限（前后各 N 句）
DEFAULT_FIX_CONTEXT_LINES = 4
MAX_FIX_CONTEXT_LINES = 16

# 上下文块的说明文案：置于注入块首尾，声明这些行仅供理解剧情、禁止对其输出
CONTEXT_BLOCK_HEADER = (
    "（以下为本批句子的前后上下文，仅供理解剧情衔接与说话人语气；"
    "这些行没有哈希锚点，禁止对它们输出任何判定或修复结果）"
)
CONTEXT_BLOCK_FOOTER = "（上下文结束，以下是本批待处理句子）"


class BaseSparseFixRound(ForGalJsonTranslate):
    """稀疏 better 输出修复轮的公共基类。

    子类需实现：
    - _build_first_round_content(input_src, gptdict, filename)
    - _apply_better_result(tran, current_dst, normalized, line_id)
    或直接使用 BaseProblemFixRound / BaseImproveRound。
    """

    # 日志前缀，子类覆盖
    _log_tag = "[修复]"
    # 命中的问题类型白名单；None 表示不按问题筛选（Improve 轮使用自己的筛选）
    _problem_types: Optional[list] = None
    # 是否把 problem 注入输入 JSONL
    _inject_problem = False
    # 是否在输入最前附带问题句前后上下文（问题修复轮开启；改进轮全量选句，不适用）
    _include_problem_context = False
    # 输入 JSONL 是否携带 src 原文
    _include_src = True
    # 响应非空但 0 个 better 时是否告警
    _warn_on_zero_found = True
    # 整批回显判定阈值与重试提示（修复轮 batch_translate 已移除回显检测逻辑；
    # 检测/复核轮统一由 BaseDetectRound 使用这两个阈值）
    _echo_hit_ratio = 0.9
    _echo_min_hits = 5
    _echo_retry_hint = (
        "注意：上一轮你的输出疑似把整批输入全部回显（每句都输出了 better）。"
        "请重新逐句检查，仅输出确有需要修复/改进的句子；若本批无需处理，输出空代码块。"
    )

    def _resolve_problem_context_lines(self) -> int:
        """读取问题句上下文条数配置（前后各 N 句），0=不附带。

        gpt.fixIncludeContext 默认开；gpt.fixContextLines 默认
        DEFAULT_FIX_CONTEXT_LINES，非法值回退默认，收拢到 [0, MAX]。
        getKey 统一单参调用（兼容测试替身的一参签名），缺省值在此收敛。
        """
        get_key = getattr(self.pj_config, "getKey", None)
        if not callable(get_key):
            return DEFAULT_FIX_CONTEXT_LINES
        if not coerce_bool(get_key("gpt.fixIncludeContext"), True):
            return 0
        try:
            lines = int(get_key("gpt.fixContextLines"))
        except (TypeError, ValueError):
            return DEFAULT_FIX_CONTEXT_LINES
        return min(MAX_FIX_CONTEXT_LINES, max(0, lines))

    def _build_problem_context_block(
        self, batch: list, n_symbol: str, include_src: bool, max_lines: int
    ) -> str:
        """构建问题句前后上下文块（JSONL 文本，由调用方前置到批输入最前）。

        沿 prev_tran/next_tran 链为批内每句各取前后 max_lines 句，排除批内
        句子后按行号去重排序；空文本邻居不输出也不计数。字段口径与
        _build_input_jsonlines 一致（说话人清洗、换行编码、dst 取校对优先），
        但无哈希锚点与 sig——响应解析按 id 只认批内句子，模型对上下文行的
        任何输出都会被安全丢弃。include_src=False（纯译文修复模式）时上下文
        同样不带 src，维持「不向模型展示原文」的口径。

        Returns:
            上下文块文本；无可用邻居（max_lines<=0 / 链上无句）时返回空串。
        """
        if max_lines <= 0 or not batch:
            return ""
        batch_ids = {t.index for t in batch}
        context: dict = {}
        for tran in batch:
            for direction in ("prev_tran", "next_tran"):
                neighbor = getattr(tran, direction, None)
                steps = 0
                while neighbor is not None and steps < max_lines:
                    if neighbor.post_src == "":
                        # 空文本句无上下文价值：跳过且不占窗口条数
                        neighbor = getattr(neighbor, direction, None)
                        continue
                    if neighbor.index not in batch_ids:
                        # 批内句子本身已在输入中：跳过且不占条数；
                        # 其余句入块并计数（含与其他批句窗口重叠的已收集句）
                        context[neighbor.index] = neighbor
                        steps += 1
                    neighbor = getattr(neighbor, direction, None)
        if not context:
            return ""
        lines = [CONTEXT_BLOCK_HEADER]
        for idx in sorted(context):
            tran = context[idx]
            obj: dict = {"id": idx}
            speaker = tran.get_speaker_name() or ""
            speaker = speaker.replace("\r\n", "").replace("\t", "").replace("\n", "")
            if speaker:
                obj["name"] = speaker
            if include_src:
                obj["src"] = encode_line_breaks_for_prompt(tran.post_src, n_symbol)
            dst_text = tran.proofread_zh or tran.pre_dst
            if dst_text:
                obj["dst"] = encode_line_breaks_for_prompt(dst_text, n_symbol)
            lines.append(json.dumps(obj, ensure_ascii=False))
        lines.append(CONTEXT_BLOCK_FOOTER)
        return "\n".join(lines)

    def _has_target_problem(self, tran) -> bool:
        """按问题类型白名单 + 译文有效性过滤，统一各修复轮筛选口径。"""
        if not getattr(tran, "problem", ""):
            return False
        if tran.post_src == "" or tran.pre_dst == "":
            return False
        if FAILED_PREFIX in tran.pre_dst:
            return False
        if getattr(tran, "skip_check", False):
            return False
        if not self._problem_types:
            return True
        kept = ForGalJsonTranslate._filter_problem_by_types(
            tran.problem, self._problem_types
        )
        return bool(kept)

    def _filter_target_translations(self, trans_list: CTransList) -> list:
        """默认按问题类型筛选；Improve 轮会覆盖。"""
        return [t for t in trans_list if self._has_target_problem(t)]

    def _effective_problem_types(self) -> Optional[list]:
        """返回本轮实际要注入的 problem 类型白名单；None 表示不注入。"""
        if not self._inject_problem:
            return None
        return self._problem_types

    def _build_batch_gptdict(
        self, gpt_dic, batch: CTransList, filename: str
    ) -> str:
        if gpt_dic is None:
            return ""
        try:
            scene = "h" if self._group_is_h_scene(batch, filename) else "nh"
            return gpt_dic.gen_prompt(batch, scene=scene)
        except Exception:
            return ""

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
        translist_hit: Optional[list] = None,
        translist_unhit: Optional[list] = None,
    ) -> CTransList:
        """通用稀疏修复轮入口：不覆盖主译文，只写 alt_dst（或按子类策略处理）。

        translist_hit / translist_unhit 为历史遗留签名参数（兼容旧调用方），
        本入口以 trans_list 为准，不使用这两个参数。
        对话模式（gpt.chatMode）：multi=文件级共用会话，仅首轮发完整模板；
        single=每批独立完整请求（与多轮首轮同口径），不建立/不回写会话。
        """
        target_trans_list = self._filter_target_translations(trans_list)
        total = len(target_trans_list)
        if total == 0:
            LOGGER.info(f"{self._log_tag} {filename} 无可处理的问题译文，跳过")
            return trans_list

        use_single_round = self.chat_mode == "single"
        if not use_single_round:
            # 用原始文件名独立分桶，从首轮重建，不混用翻译轮历史
            self.reset_conversation(filename)
            self.conversations[filename] = [
                {"role": "system", "content": self.system_prompt}
            ]

        num_per_request = self._coerce_positive_int(
            self.pj_config.getKey("gpt.numPerRequestBetter"),
            num_pre_request or 100,
        )
        context_lines = (
            self._resolve_problem_context_lines()
            if self._include_problem_context
            else 0
        )
        if context_lines > 0:
            LOGGER.info(
                f"{self._log_tag} {filename} 附带问题句上下文：前后各 {context_lines} 句"
            )
        total_batches = (total + num_per_request - 1) // num_per_request
        fix_count = 0
        _mode_note = "（单轮模式，每批独立完整请求）" if use_single_round else ""
        LOGGER.info(
            f"{self._log_tag} {filename} 开始处理{_mode_note}，共 {total} 句，"
            f"{total_batches} 批"
        )

        for batch_no, start in enumerate(
            range(0, total, num_per_request), start=1
        ):
            self._check_stop_requested()
            batch = target_trans_list[start : start + num_per_request]
            idx_tip = self._build_idx_tip(batch)
            batch_gptdict = self._build_batch_gptdict(gpt_dic, batch, filename)

            input_list, sig_list, n_symbol, input_src = self._build_input_jsonlines(
                batch,
                proofread=True,
                filename=filename,
                problem_types=self._effective_problem_types(),
                include_src=self._include_src,
            )
            if context_lines > 0:
                context_block = self._build_problem_context_block(
                    batch, n_symbol, self._include_src, context_lines
                )
                if context_block:
                    input_src = context_block + "\n" + input_src
            if use_single_round:
                # 单轮模式：每批独立完整请求（与多轮首轮同口径），不带会话历史
                is_first_round = True
                user_content = self._build_first_round_content(
                    input_src, batch_gptdict, filename
                )
                call_messages = [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_content},
                ]
            else:
                conv = self._ensure_conversation(filename)
                is_first_round = len(conv) <= 1
                if is_first_round:
                    user_content = self._build_first_round_content(
                        input_src, batch_gptdict, filename
                    )
                else:
                    user_content = (
                        batch_gptdict + "\n以下是本批次待处理内容：\n" + input_src
                        if batch_gptdict
                        else input_src
                    )
                call_messages = conv + [{"role": "user", "content": user_content}]

            if self.pj_config.active_workers == 1:
                _round = (
                    "单轮" if use_single_round
                    else "首轮" if is_first_round else "续轮"
                )
                LOGGER.info(
                    f"-> 修复输入[{_round}] | backend={self.eng_type} | "
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
                    f"{self._log_tag}[{filename}:{idx_tip}]LLM调用失败："
                    f"{type(e).__name__}: {e}"
                )
                self._record_round_runtime_error(
                    filename, idx_tip, f"{type(e).__name__}: {e}", None
                )
                if not use_single_round:
                    self.reset_conversation(filename)
                LOGGER.warning(
                    f"{self._log_tag}[{filename}:{idx_tip}] 该批 {len(batch)} 句因 "
                    f"LLM 调用失败跳过"
                )
                continue

            result_text = preprocess_jsonline_response(raw_resp or "")
            success_count, found_count = self._parse_fix_response(
                result_text, batch, n_symbol
            )

            if self._warn_on_zero_found and result_text.strip() and found_count == 0:
                _warn_msg = (
                    f"{self._log_tag}[{filename}:{idx_tip}] 模型响应非空但未解析到任何 "
                    f"better（输出格式异常或内容问题），本次 0 句处理"
                )
                LOGGER.warning(_warn_msg)
                self._record_round_runtime_error(filename, idx_tip, _warn_msg, None)

            # 整批回显退化：found_count > 0 但 success_count == 0，说明模型输出被逐句过滤
            # 全部拦截（疑似整批回显），告警；多轮模式再重置对话，避免回显响应污染
            # 后续批次（单轮模式无会话状态，仅需告警）
            if found_count > 0 and success_count == 0:
                _degrade_msg = (
                    f"{self._log_tag}[{filename}:{idx_tip}] 模型输出 {found_count} 条 better "
                    f"但全部被过滤（疑似整批回显或退化输出）"
                )
                if not use_single_round:
                    _degrade_msg += "，重置对话"
                    self.reset_conversation(filename)
                LOGGER.warning(_degrade_msg)
                self._record_round_runtime_error(filename, idx_tip, _degrade_msg, None)
                fix_count += success_count
                LOGGER.debug(
                    f"{self._log_tag} {filename} 批次 {batch_no}/{total_batches}"
                    f"（序号 {idx_tip}）已处理，成功 {success_count} 句"
                )
                continue

            if not use_single_round:
                # 追加 assistant 回复进对话，保持轮次交替
                self.conversations[filename] = self._trim_conversation(
                    call_messages + [{"role": "assistant", "content": raw_resp or ""}]
                )
            fix_count += success_count
            LOGGER.debug(
                f"{self._log_tag} {filename} 批次 {batch_no}/{total_batches}"
                f"（序号 {idx_tip}）已处理，成功 {success_count} 句"
            )

        if fix_count > 0:
            LOGGER.info(f"{self._log_tag} {filename} 共处理 {fix_count} 句")
        else:
            LOGGER.info(f"{self._log_tag} {filename} 未发现可处理的问题译文")
        return trans_list

    def _build_first_round_content(
        self, input_src: str, gptdict: str, filename: str
    ) -> str:
        """构建修复轮首轮 user 内容（统一占位符口径，走基类 _build_prompt_request）。

        子类可覆盖 _apply_extra_first_round_replacements 注入特有占位符
        （如 [br_issue_guide]），无需整段重写本方法。
        """
        metadata = self._resolve_file_metadata(filename)
        metadata_block = (
            self._format_file_metadata_block(metadata)
            if metadata is not None
            else ""
        )
        prompt_req = super()._build_prompt_request(
            input_src,
            gptdict,
            plot_metadata=metadata_block,
            batch_metadata="",
            global_prompt=self._format_global_prompt_block(filename) or "",
        )
        prompt_req = self._apply_extra_first_round_replacements(prompt_req)
        return self._apply_history_result(prompt_req, filename)

    def _apply_extra_first_round_replacements(self, prompt_req: str) -> str:
        """子类可在此替换特有占位符，如 [br_issue_guide]。"""
        return prompt_req

    def _finalize_prompts(self) -> None:
        """修复轮覆盖专用模板后，重新应用 change_prompt 与用户模板 override。

        基类 __init__ 链中 change_prompt 作用于翻译轮模板，随后被修复轮专用模板
        覆盖丢弃；此处对修复轮模板重新应用，使 common.gpt.change_prompt 对修复轮
        同样生效。注意优先级：本方法先应用 change_prompt 再应用用户 override
        （override 优先），与翻译轮（override 先于 init_chatbot，change_prompt 优先）
        相反，属刻意保留的既有行为。
        """
        from GalTransl.Backend.BaseEngine import _apply_change_prompt

        self.trans_prompt = _apply_change_prompt(self.pj_config, self.trans_prompt)
        self._apply_internal_prompt_template_overrides()

    def _parse_fix_response(
        self, result_text: str, trans_list: CTransList, n_symbol: str
    ) -> tuple:
        """按 id 稀疏解析 better 输出，返回 (success_count, found_count)。"""
        id_map = {t.index: t for t in trans_list}
        success_count = 0
        found_count = 0
        applied = []
        for line in result_text.splitlines():
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                json_part = line.split("|", 1)[1].strip()
            else:
                json_part = line
            obj = decode_json_line_part(json_part)
            if obj is None:
                continue
            line_id = obj.get("id")
            better = obj.get("better")
            if not isinstance(line_id, int) or not isinstance(better, str):
                continue
            tran = id_map.get(line_id)
            if tran is None:
                continue
            if tran.post_src != "" and better.strip() == "":
                continue
            if "�" in better:
                continue
            # 原文回显（精确）：归一化前直接比较，避免 opencc 简繁转换改写中日同形汉字
            # 导致 normalized != post_src 而绕过过滤
            if tran.post_src != "" and better.strip() == tran.post_src.strip():
                LOGGER.debug(
                    f"{self._log_tag} 句子 {line_id} 的 better 与原文相同（疑似原文回显），跳过"
                )
                continue
            found_count += 1
            normalized = self._normalize_parsed_translation_text(
                better, tran, n_symbol
            )
            current_dst = tran.proofread_zh or tran.pre_dst
            # 归一后比较：缓存约定真实 \n，旧缓存可能是 CRLF；刻意不折字面 \n 正文
            normalized_cmp = canonicalize_newlines(normalized).strip()
            if current_dst != "" and normalized_cmp == canonicalize_newlines(
                current_dst
            ).strip():
                LOGGER.debug(
                    f"{self._log_tag} 句子 {line_id} 的 better 与当前译文相同，跳过"
                )
                continue
            # 原文回显：键A 提示词编码口径（两侧原始文本）拦「字面换行约定」；
            # 键B 两侧同一归一管线（opencc/引号/「」/省略号）拦「回显时改写标点字形」
            if tran.post_src != "":
                echo_hit = prompt_newline_key(
                    better, n_symbol
                ).strip() == prompt_newline_key(tran.post_src, n_symbol).strip()
                if not echo_hit:
                    # 对源文跑一次归一的代价远小于一次 LLM 调用
                    echo_hit = normalized_cmp == canonicalize_newlines(
                        self._normalize_parsed_translation_text(
                            tran.post_src, tran, n_symbol
                        )
                    ).strip()
                if echo_hit:
                    LOGGER.debug(
                        f"{self._log_tag} 句子 {line_id} 的 better 与原文等价"
                        f"（疑似原文回显或字形改写回显），跳过"
                    )
                    continue
            if self._apply_better_result(tran, current_dst, normalized, line_id):
                success_count += 1
                applied.append(tran)
        if applied:
            # 实时推送结果预览（前端翻译控制台"结果预览"，覆盖 improve/brfix/jpfix/banfix/fix 各修复轮）
            self.push_result_preview(
                self.pj_config, self._format_result_preview(applied)
            )
        else:
            # 0 采纳也要推送：否则面板停留在上一批的陈旧内容，被误认为后端没跑；
            # 空响应与「有响应但全被过滤」措辞区分，便于判断是模型没输出还是全被丢弃
            note = (
                "本批响应为空，无可采纳修改"
                if not result_text.strip()
                else f"本批未采纳任何修改（解析到 {found_count} 行）"
            )
            self._push_no_hit_preview(note)
        return success_count, found_count

    def _push_no_hit_preview(self, note: str) -> None:
        """推送一条说明性预览（0 命中/0 采纳时），避免面板停留在陈旧内容。

        note_only=True：该板块已有真实结果时不覆盖，避免低频命中的真实结果被刷掉。
        """
        self.push_result_preview(
            self.pj_config, f"{self._log_tag} {note}", note_only=True
        )

    def _push_discarded_preview(self, note: str) -> None:
        """推送「本批结果已被丢弃」的说明：须覆盖真实结果（那些命中已作废）。

        仍按说明性预览记账（note_only + force）：后续批次的 0 命中说明可以继续覆盖它。
        """
        self.push_result_preview(
            self.pj_config, f"{self._log_tag} {note}", note_only=True, force=True
        )

    def _is_echo_response(self, found_count: int, batch_size: int) -> bool:
        """整批回显（复制输入全部输出 better）判定。

        以解析到的 found_count（模型实际输出的行数）为基准：命中率 ≥
        self._echo_hit_ratio 且命中数 ≥ self._echo_min_hits 时判定为模型退化
        输出；两阈值均可经类属性由子类覆盖。检测轮正常输出为稀疏命中，整批高
        命中基本可断定回显（复核轮每行必答 keep，不启用本判定）。
        """
        if batch_size <= 0 or found_count < self._echo_min_hits:
            return False
        return found_count / batch_size >= self._echo_hit_ratio

    def _apply_better_result(
        self, tran, current_dst: str, normalized: str, line_id: int
    ) -> bool:
        """默认只写 alt_dst；Problem 修复轮可覆盖为 swapFixToCurrent。"""
        tran.alt_dst = normalized
        return True

    def _format_result_preview(
        self,
        sentences: list,
        issue_attr: str = "",
        issue_label: str = "",
        dismissed: Optional[list] = None,
    ) -> str:
        """修复/检测轮结果预览文本：逐句显示原文、当前译文与处理后字段。

        格式参照 str(CSentense) 并补充 alt_dst；检测轮经 issue_attr/issue_label
        展示命中标记与原因，复核轮经 dismissed 展示已撤销句子。
        """
        lines: List[str] = []
        for t in sentences:
            lines.append(f"v--{t.index}")
            lines.append(f"> Src: {t.post_src}")
            lines.append(f"> Dst: {t.pre_dst}")
            if t.alt_dst:
                lines.append(f"> Better: {t.alt_dst}")
            if issue_attr:
                issue = getattr(t, issue_attr, "") or ""
                if issue:
                    lines.append(f"> {issue_label or issue_attr}: {issue}")
            lines.append("")
        for t in dismissed or []:
            lines.append(f"v--{t.index}（已撤销：可接受译文）")
            lines.append(f"> Src: {t.post_src}")
            lines.append(f"> Dst: {t.pre_dst}")
            lines.append("")
        return "\n".join(lines).strip()

    def _record_round_runtime_error(
        self, filename: str, idx_tip: str, message: str, model: Optional[str]
    ) -> None:
        """统一运行态错误上报，走 BaseEngine 收口的 _record_runtime_error。"""
        self._record_runtime_error(
            kind="parse",
            message=message,
            filename=filename,
            index_range=str(idx_tip),
            model=model,
            level="warning",
        )


class BaseProblemFixRound(BaseSparseFixRound):
    """按问题类型修复译文的基类：统一修复后端 ForProblemFixRound 及旧引擎薄包装子类的公共基类。"""

    _inject_problem = True
    _include_src = True
    _include_problem_context = True

    def _apply_better_result(
        self, tran, current_dst: str, normalized: str, line_id: int
    ) -> bool:
        if self.pj_config.getKey("gpt.swapFixToCurrent", False):
            tran.alt_dst = current_dst
            if tran.proofread_zh:
                tran.proofread_zh = normalized
            else:
                tran.pre_dst = normalized
            LOGGER.debug(
                f"{self._log_tag} 句子 {line_id} 已交换属性：修复结果覆盖当前译文"
            )
        else:
            tran.alt_dst = normalized
        return True


class BaseImproveRound(BaseSparseFixRound):
    """全量质量改进/评估轮基类：ForImproveTranslation 及未来同类后端。"""

    _log_tag = "[改进轮]"
    _warn_on_zero_found = False

    def _filter_target_translations(self, trans_list: CTransList) -> list:
        """改进轮筛选所有“已有有效译文”的句子，不按单一问题类型过滤。"""
        return [
            t
            for t in trans_list
            if t.post_src != ""
            and t.pre_dst != ""
            and FAILED_PREFIX not in t.pre_dst
            and not getattr(t, "skip_check", False)
        ]

    def _effective_problem_types(self) -> Optional[list]:
        if not self.pj_config.getKey("gpt.enableProblemInject"):
            return None
        return self._coerce_problem_type_list(
            self.pj_config.getKey("gpt.problemInjectTypes")
        )

    @staticmethod
    def _coerce_problem_type_list(raw_types) -> list:
        """把配置的问题类型规范为 CProblemType 列表。

        条目支持三种写法：类型名（残留日文）、枚举成员、序号（int 或十进制数字
        字符串，按 /api/problem-types 目录顺序从 1 解析，见
        ConfigHelper.PROBLEM_TYPE_CATALOG_ORDER）。
        """
        if raw_types is None:
            return []
        if isinstance(raw_types, str):
            raw_types = [t.strip() for t in raw_types.split(",") if t.strip()]
        from GalTransl.ConfigHelper import (
            CProblemType,
            PROBLEM_TYPE_CATALOG_ORDER,
        )

        max_index = len(PROBLEM_TYPE_CATALOG_ORDER)

        def _by_index(index: int, origin: str) -> Optional[CProblemType]:
            if not 1 <= index <= max_index:
                LOGGER.warning(
                    f"{origin} 不是有效的问题类型序号，已忽略（有效范围 1-{max_index}，"
                    f"序号与「问题检测类型」清单顺序一致）"
                )
                return None
            return CProblemType[PROBLEM_TYPE_CATALOG_ORDER[index - 1]]

        result = []
        for item in raw_types:
            # bool 是 int 子类，先排除以免 True/False 被当成序号 1/0
            if isinstance(item, bool):
                LOGGER.warning(f"问题类型条目 {item!r}（bool）不是有效写法，已忽略")
                continue
            if isinstance(item, int):
                coerced = _by_index(item, str(item))
                if coerced is not None:
                    result.append(coerced)
                continue
            name = item.name if hasattr(item, "name") else str(item)
            # isdecimal 限定十进制数字（isdigit 会放过上标数字等 int() 不可解析字符）
            if name.strip().isdecimal():
                coerced = _by_index(int(name.strip()), name)
                if coerced is not None:
                    result.append(coerced)
                continue
            try:
                result.append(CProblemType[name])
            except KeyError:
                LOGGER.warning(
                    f"{name} 不是已知问题类型，已忽略（若需修复请检查 _FIX_SPECS "
                    f"是否已注册该类型，未注册类型将按仅译文模式处理）"
                )
                continue
        return result
