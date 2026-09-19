"""词语色彩一致性检查后端：对照批次级元数据的「用词色彩」标注检查译文用词。

仅判定、不产译文：命中句由本后端置 tone_issue 标记（AI 判定说明或固定 "1"），
随后 find_problems 认领并输出「词语色彩不一致」问题（problem 字段），不触碰
主译文与备选译文。依赖 pass2_cache 批次级元数据（区间→用词色彩/视角/氛围）：
文件无批次元数据时跳过（无标注基准即无判定，进入时仍清旧标记保证幂等）。
作为完整流水线的后处理阶段（gpt.afterTranslation=tonecheck）使用，也可作为
独立引擎对已翻译文件执行。
引擎标识：ForToneCheck
"""

from typing import Optional, Tuple

from GalTransl import LOGGER
from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.CSentense import CSentense, CTransList
from GalTransl.ConfigHelper import CProxyPool, CProjectConfig
from GalTransl.Service import JobCancelledError
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.BaseFixRound import BaseImproveRound
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_FORWORDTONE_PROMPT,
    FORWORDTONE_SYSTEM,
)
from GalTransl.Backend.utils import decode_json_line_part, preprocess_jsonline_response


class _EmptyTokenPool:
    """空令牌池占位：主 profile 不可用时保证基类 init_chatbot 不崩溃（不发任何请求）。"""

    def get_available_token(self) -> list:
        return []


@register_engine("ForToneCheck")
class ForToneCheck(BaseImproveRound):
    """词语色彩一致性检查后端。

    向 AI 发送「区间色彩标注 + 原文 + 当前译文」（h 场景照常检查），AI 仅输出
    用词色彩与所属区间标注明显不一致的句子 id（可选 reason），本后端为命中句
    置 tone_issue 标记。问题类型统一由 find_problems 认领为「词语色彩不一致」。

    与主翻译 profile 共用令牌池（与其他后处理后端一致），本引擎额外支持未传
    token_pool 时按主 profile 自建，主池无可用 token 时降级跳过，不发任何请求。

    引擎标识：ForToneCheck
    """

    # 日志前缀
    _log_tag = "[色彩检查]"
    # 0 命中（空代码块）是色彩检查的常态结果（绝大多数句子色彩相符），
    # 不告警也不计入最近错误；与 ForSemCheck 对齐
    _warn_on_zero_found = False
    # 整批回显（复制输入全部标错）重试时追加的纠正提示（覆盖基类文案）
    _echo_retry_hint = (
        "注意：上一轮你的输出疑似把整批输入全部判定为色彩不一致（整批回显）。"
        "请重新逐句对照色彩标注与 dst，仅输出明显不一致的句子；"
        "若均无明显不一致，输出空代码块。"
    )
    # 回显判定阈值：与 ForSemCheck 同口径（本引擎只做标记不产译文，误杀成本低）
    _echo_hit_ratio = 0.6

    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool] = None,
        token_pool: Optional[COpenAITokenPool] = None,
    ) -> None:
        """初始化词语色彩一致性检查后端。

        Args:
            config: 项目配置对象。
            eng_type: 引擎标识（ForToneCheck）。
            proxy_pool: 代理池对象，为 None 时不使用代理。
            token_pool: 主翻译令牌池；为 None 时按主 profile 自动构建。
        """
        self._disabled_reason = ""
        if token_pool is None:
            # 直接实例化（测试/独立调用）未传池：按主 profile 自建，与其他调用路径一致
            try:
                token_pool = COpenAITokenPool(config, eng_type)
            except Exception as e:
                # 老项目缺 OpenAI-Compatible 段等极端场景：降级禁用，不中断后处理
                LOGGER.warning(
                    f"{self._log_tag} 主翻译令牌池构建失败：{type(e).__name__}: {e}"
                )
                self._disabled_reason = "主翻译令牌池构建失败"
                token_pool = _EmptyTokenPool()
        if not getattr(token_pool, "get_available_token", lambda: [])():
            if not self._disabled_reason:
                self._disabled_reason = (
                    "主翻译令牌池无可用 token（请在「后端配置」页配置 OpenAI 兼容端点）"
                )
        super().__init__(config, eng_type, proxy_pool, token_pool)
        # 覆盖基类（翻译轮）的系统提示词为色彩检查轮专用角色声明
        self.system_prompt = FORWORDTONE_SYSTEM
        self.trans_prompt = FORGAL_JSON_FORWORDTONE_PROMPT
        # 覆盖默认值后统一重放 change_prompt 与用户模板 override（基类 __init__ 已应用过一次）
        self._finalize_prompts()

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
        """色彩检查轮入口：禁用/未配置时降级跳过；否则清旧标记后单轮逐批检查。

        tone_issue 为持久化信号：进入检查即清除全部旧标记再写新结果（幂等），
        避免重复运行累积误报。单轮模式：每批只发送 [system] + 本批 user
        （区间色彩标注、任务说明与批次 input），不注入术语表、历史结果、翻译规范。
        """
        if self._disabled_reason:
            LOGGER.warning(
                f"{self._log_tag} {filename}：{self._disabled_reason}，跳过词语色彩一致性检查"
            )
            return trans_list
        for t in trans_list:
            t.tone_issue = ""
        batch_metadata = self._resolve_batch_metadata(filename)
        if batch_metadata is None or not getattr(batch_metadata, "batches", None):
            LOGGER.info(
                f"{self._log_tag} {filename} 无批次级元数据（用词色彩标注），跳过词语色彩一致性检查"
            )
            return trans_list
        targets = self._filter_target_translations(trans_list)
        total = len(targets)
        if total == 0:
            LOGGER.info(f"{self._log_tag} {filename} 无可检查的有效译文，跳过")
            return trans_list
        # 色彩检查独立批次：优先 gpt.numPerRequestToneCheck（默认20），
        # 未配置时回退语义检测批次，再兜底改进轮批次/调用方实参。本地小模型批次不宜过大。
        num_per_request = self._coerce_positive_int(
            self.pj_config.getKey("gpt.numPerRequestToneCheck"),
            self._coerce_positive_int(
                self.pj_config.getKey("gpt.numPerRequestSemCheck"),
                self._coerce_positive_int(
                    self.pj_config.getKey("gpt.numPerRequestBetter"),
                    num_pre_request or 20,
                ),
            ),
        )
        total_batches = (total + num_per_request - 1) // num_per_request
        hit_count = 0
        LOGGER.info(
            f"{self._log_tag} {filename} 开始单轮词语色彩一致性检查，共 {total} 句，{total_batches} 批"
        )
        for batch_no, start in enumerate(range(0, total, num_per_request), start=1):
            self._check_stop_requested()
            batch = targets[start : start + num_per_request]
            idx_tip = self._build_idx_tip(batch)
            _input_list, _sig_list, n_symbol, input_src = self._build_input_jsonlines(
                batch,
                proofread=True,
                filename=filename,
                include_src=True,
            )
            tone_guide = self._format_tone_guide(batch, batch_metadata)
            user_content = self._build_tonecheck_user_content(input_src, tone_guide)
            if self.pj_config.active_workers == 1:
                LOGGER.info(
                    f"-> 色彩检查输入[{batch_no}/{total_batches}] | "
                    f"backend={self.eng_type} | sentences={len(batch)}"
                )
            echo_retried = False
            while True:
                call_messages = [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_content},
                ]
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
                    break
                result_text = preprocess_jsonline_response(raw_resp or "")
                batch_hit, _ = self._parse_fix_response(result_text, batch, n_symbol)
                if self._is_echo_response(batch_hit, len(batch)):
                    # 模型整批回显（复制输入全部标错）：丢弃本批标记，重试一次
                    for t in batch:
                        t.tone_issue = ""
                    if not echo_retried:
                        LOGGER.warning(
                            f"{self._log_tag}[{filename}:{idx_tip}] 单批命中 "
                            f"{batch_hit}/{len(batch)} 句（疑似模型整批回显输入），"
                            f"追加纠正提示后重试一次"
                        )
                        user_content = user_content + "\n\n" + self._echo_retry_hint
                        echo_retried = True
                        continue
                    _echo_msg = (
                        f"{self._log_tag}[{filename}:{idx_tip}] 重试仍疑似整批回显"
                        f"（命中 {batch_hit}/{len(batch)} 句），已丢弃本批全部色彩不一致标记"
                    )
                    LOGGER.warning(_echo_msg)
                    self._record_round_runtime_error(filename, idx_tip, _echo_msg, None)
                    break
                hit_count += batch_hit
                LOGGER.debug(
                    f"{self._log_tag} {filename} 批次 {batch_no}/{total_batches}"
                    f"（序号 {idx_tip}）命中 {batch_hit} 句"
                )
                break
        if hit_count > 0:
            LOGGER.info(f"{self._log_tag} {filename} 色彩检查完成，共命中 {hit_count} 句")
        else:
            LOGGER.info(f"{self._log_tag} {filename} 色彩检查完成，未发现色彩不一致")
        return trans_list

    def _format_tone_guide(self, batch: list, batch_metadata) -> str:
        """把本批句子覆盖到的区间色彩标注渲染为提示词段（与翻译轮区间口径一致）。"""
        lo = None
        hi = None
        for t in batch:
            gi = getattr(t, "runtime_index", None)
            if not isinstance(gi, int):
                gi = getattr(t, "index", None)
            if not isinstance(gi, int):
                continue
            lo = gi if lo is None else min(lo, gi)
            hi = gi if hi is None else max(hi, gi)
        if lo is None:
            return ""
        segments = batch_metadata.segments_in_range(lo, hi)
        if not segments:
            LOGGER.debug(
                f"{self._log_tag} 本批区间 [{lo}-{hi}] 无相交色彩标注，按未标注处理"
            )
            return ""
        lines = []
        for b in segments:
            if not isinstance(b, dict):
                continue
            rng = b.get("区间") or b.get("interval")
            if not isinstance(rng, (list, tuple)) or len(rng) < 2:
                continue
            view = str(b.get("视角", "") or "").strip() or "未标注"
            atmos = str(b.get("氛围", "") or "").strip() or "未标注"
            tone = str(b.get("用词色彩", "") or "").strip() or "未标注"
            lines.append(
                f"- 区间[{rng[0]}-{rng[1]}] 视角:{view} 氛围:{atmos} 用词色彩:{tone}"
            )
        if not lines:
            return ""
        return (
            "本文件已按剧情划分为若干翻译区间，本批句子涉及的区间色彩标注如下"
            "（行号为文件内全局行号）：\n" + "\n".join(lines)
        )

    def _build_tonecheck_user_content(self, input_src: str, tone_guide: str) -> str:
        """单轮拼接色彩检查 user 提示词：替换 [ToneGuide]/[TargetLang]/[Input] 占位符。

        除区间色彩标注与任务说明外，不注入术语表/文件级元数据/历史结果/翻译规范。
        tone_guide 为空时移除标注段（区间无标注，仅按「不判为不一致」口径输出空块）。
        """
        prompt_req = self.trans_prompt
        if not tone_guide:
            prompt_req = prompt_req.replace("<tone_guide>\n[ToneGuide]\n</tone_guide>\n\n", "")
        prompt_req = prompt_req.replace("[ToneGuide]", tone_guide)
        prompt_req = prompt_req.replace("[TargetLang]", self.target_lang)
        prompt_req = prompt_req.replace("[Input]", input_src)
        return prompt_req

    def _parse_fix_response(
        self, result_text: str, trans_list: CTransList, n_symbol: str
    ) -> Tuple[int, int]:
        """按 id 稀疏解析判定结果（仅取 id 与可选 reason），命中句置 tone_issue。

        返回 (hit_count, hit_count) 使基类进度计数一致；0 命中告警沿用基类逻辑。
        """
        id_map = {t.index: t for t in trans_list}
        hit_count = 0
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
            if not isinstance(line_id, int):
                continue
            tran = id_map.get(line_id)
            if tran is None:
                continue
            reason = obj.get("reason")
            if isinstance(reason, str) and "�" in reason:
                # 乱码 reason 视为输出异常：降级为默认标记，避免污染 tone_issue
                reason = ""
            tran.tone_issue = (
                str(reason).strip()
                if isinstance(reason, str) and str(reason).strip()
                else "1"
            )
            hit_count += 1
            LOGGER.debug(
                f"{self._log_tag} 句子 {line_id} 判定色彩不一致（reason={tran.tone_issue}）"
            )
        return hit_count, hit_count

    def _apply_better_result(
        self, tran: CSentense, current_dst: str, normalized: str, line_id: int
    ) -> bool:
        """防御性死代码：本引擎重写 batch_translate 与 _parse_fix_response，
        基类调用链不会触达本方法；保留以声明「绝不写 alt_dst / 主译文」的契约。"""
        return False
