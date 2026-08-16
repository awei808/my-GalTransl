"""稀疏修复轮 / 改进轮共享基类。

设计目标：
- 从多轮对话翻译后端 ForGalJsonMulitChat 继承底层对话、术语表、元数据、API 调用能力；
- 把“筛选目标句子 → 分桶 → 构建首轮内容 → 调用 LLM → 稀疏解析 better → 错误上报”的公共流程收口；
- 按业务差异拆出两个基类：
    * BaseProblemFixRound：ForJPResidue / ForBanWordFix / ForBRStation 这类“按问题类型修复译文”的后端；
    * BaseImproveRound：ForImproveTranslation 以及未来可能新增的“全量质量改进/评估”类后端。
"""

from __future__ import annotations

from typing import Any, List, Optional

from GalTransl import LOGGER
from GalTransl.CSentense import CTransList
from GalTransl.Service import JobCancelledError
from GalTransl.Backend.ForGalJsonMulitChat import ForGalJsonMulitChat
from GalTransl.Backend.Prompts import FAILED_PREFIX
from GalTransl.Backend.utils import (
    decode_json_line_part,
    preprocess_jsonline_response,
)


class BaseSparseFixRound(ForGalJsonMulitChat):
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
    # 输入 JSONL 是否携带 src 原文
    _include_src = True
    # 响应非空但 0 个 better 时是否告警
    _warn_on_zero_found = True

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
        kept = ForGalJsonMulitChat._filter_problem_by_types(
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
        translist_hit: list = [],
        translist_unhit: list = [],
    ) -> CTransList:
        """通用稀疏修复轮入口：不覆盖主译文，只写 alt_dst（或按子类策略处理）。"""
        target_trans_list = self._filter_target_translations(trans_list)
        total = len(target_trans_list)
        if total == 0:
            LOGGER.info(f"{self._log_tag} {filename} 无可处理的问题译文，跳过")
            return trans_list

        # 用原始文件名独立分桶，从首轮重建，不混用翻译轮历史
        self.reset_conversation(filename)
        self.conversations[filename] = [
            {"role": "system", "content": self.system_prompt}
        ]

        num_per_request = self._coerce_positive_int(
            self.pj_config.getKey("gpt.numPerRequestBetter"),
            num_pre_request or 100,
        )
        total_batches = (total + num_per_request - 1) // num_per_request
        fix_count = 0
        LOGGER.info(
            f"{self._log_tag} {filename} 开始处理，共 {total} 句，"
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
                _round = "首轮" if is_first_round else "续轮"
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
                self.reset_conversation(filename)
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
            found_count += 1
            normalized = self._normalize_parsed_translation_text(
                better, tran, n_symbol
            )
            current_dst = (
                tran.proofread_zh if tran.proofread_zh != "" else tran.pre_dst
            )
            if current_dst != "" and normalized.strip() == current_dst.strip():
                LOGGER.debug(
                    f"{self._log_tag} 句子 {line_id} 的 better 与当前译文相同，跳过"
                )
                continue
            if self._apply_better_result(tran, current_dst, normalized, line_id):
                success_count += 1
        return success_count, found_count

    def _apply_better_result(
        self, tran, current_dst: str, normalized: str, line_id: int
    ) -> bool:
        """默认只写 alt_dst；Problem 修复轮可覆盖为 swapFixToCurrent。"""
        tran.alt_dst = normalized
        return True

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
    """按问题类型修复译文的基类：ForJPResidue / ForBanWordFix / ForBRStation。"""

    _inject_problem = True
    _include_src = True

    def _apply_better_result(
        self, tran, current_dst: str, normalized: str, line_id: int
    ) -> bool:
        if self.pj_config.getKey("gpt.swapFixToCurrent", False):
            tran.alt_dst = current_dst
            if tran.proofread_zh != "":
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
        """把配置的问题类型规范为 CProblemType 列表。"""
        if raw_types is None:
            return []
        if isinstance(raw_types, str):
            raw_types = [t.strip() for t in raw_types.split(",") if t.strip()]
        from GalTransl.ConfigHelper import CProblemType

        result = []
        for item in raw_types:
            name = item.name if hasattr(item, "name") else str(item)
            try:
                result.append(CProblemType[name])
            except KeyError:
                continue
        return result
