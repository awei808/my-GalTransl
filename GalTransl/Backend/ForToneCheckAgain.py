"""词语色彩复核后端：对 ForToneCheck 标记的「词语色彩不一致」命中句做二次复核。

第一轮 ForToneCheck 判定为色彩不一致的句子（tone_issue 非空）作为本引擎的输入；
本引擎重新注入该批句子的区间色彩标注，逐句要求 AI 给出「确认/撤销」结论，仅保留
确认仍明显偏离标注的标记，撤销属于可接受译文（表达普通、轻微语气差异、折中判定）
的误报标记。不产译文、不触碰主译文与备选译文。可独立运行，也可在色彩检查之后按
文件调用。

与第一轮 ForToneCheck 的关键差异（复核轮必须保守，绝不误删真错）：
- 不预清 tone_issue：仅显式 keep:false 才撤销，保留第一轮信号；
- 输出为全量判定（每行 keep），而非稀疏命中，不适用整批回显判定；
- 无批次级元数据（无标注基准）时**保留**既有标记并跳过（第一轮为清标记后跳过）；
- 本批句子无任何色彩标注时跳过该批并保留标记，避免「无依据撤销」；
- LLM 调用失败 / 判定行缺失 / keep 值异常一律 fail-safe 保留既有标记。

依赖 pass2_cache 批次级元数据（区间→用词色彩/视角/氛围），与第一轮同源。
引擎标识：ForToneCheckAgain
"""

from typing import Optional, Tuple

from GalTransl import LOGGER
from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.CSentense import CSentense, CTransList
from GalTransl.ConfigHelper import CProxyPool, CProjectConfig
from GalTransl.Service import JobCancelledError
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.ForToneCheck import ForToneCheck
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_FORWORDTONE_AGAIN_PROMPT,
    FORWORDTONE_AGAIN_SYSTEM,
)
from GalTransl.Backend.utils import decode_json_line_part, preprocess_jsonline_response


@register_engine("ForToneCheckAgain")
class ForToneCheckAgain(ForToneCheck):
    """词语色彩复核后端（命中句二次复核）。

    输入为第一轮 ForToneCheck 已标记 tone_issue 的句子；本引擎把「区间色彩标注 +
    原文 + 译文」重新发给 AI，并要求对输入每一行显式给出 keep: true/false 结论，
    只保留确认的标记、撤销误报。继承 ForToneCheck 以复用区间色彩标注渲染
    （_format_tone_guide）与主 profile 令牌池降级逻辑，仅重写复核语义相关部分。

    与主翻译 profile 共用令牌池（与其他后处理后端一致）：外部 OpenAI 兼容
    大模型与本地 llama.cpp 均可直接使用，取决于「后端配置」页所选端点；主池
    无可用 token 时降级跳过，不发任何请求、不清理既有标记。

    引擎标识：ForToneCheckAgain
    """

    # 日志前缀
    _log_tag = "[色彩复核]"
    # 0 命中（空代码块）是复核的常态结果（本批全部撤销），不告警
    _warn_on_zero_found = False

    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool] = None,
        token_pool: Optional[COpenAITokenPool] = None,
    ) -> None:
        """
        初始化词语色彩复核后端。

        与第一轮一致：直接复用主翻译 profile 的令牌池（token_pool），不维护独立
        端点；未传 token_pool（独立调用/测试）时由父类按主 profile 自建，构建失败
        或主池无可用 token 时降级禁用（跳过复核、不发请求）。

        Args:
            config: 项目配置对象。
            eng_type: 引擎标识（ForToneCheckAgain）。
            proxy_pool: 代理池对象，为 None 时不使用代理。
            token_pool: 主翻译令牌池；为 None 时按主 profile 自动构建。
        """
        # 父类完成令牌池降级判定与基类初始化（含第一轮提示词与 _finalize_prompts）
        super().__init__(config, eng_type, proxy_pool, token_pool)
        # 覆盖第一轮的提示词为复核轮专用角色声明与全量判定任务
        self.system_prompt = FORWORDTONE_AGAIN_SYSTEM
        self.trans_prompt = FORGAL_JSON_FORWORDTONE_AGAIN_PROMPT
        # 覆盖默认值后统一重放 change_prompt 与用户模板 override（父类 __init__ 已应用过一次）
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
        """复核轮入口：禁用/未配置/无标注依据时降级跳过；否则对命中句逐批二次复核。

        与第一轮不同：不预清 tone_issue（第一轮信号保留），仅对显式 keep:false 的
        句子撤销标记；无批次元数据或本批无色彩标注时保留标记并跳过；LLM 调用失败/
        判定缺失按 fail-safe 保留处理。
        """
        if self._disabled_reason:
            LOGGER.warning(
                f"{self._log_tag} {filename}：{self._disabled_reason}，跳过词语色彩复核"
            )
            return trans_list
        # 复核对象：上一轮已标记的命中句（tone_issue 非空），且仍有有效译文
        # （pre_dst 非空），避免对失效译文空发请求
        targets = [
            t
            for t in trans_list
            if getattr(t, "tone_issue", "") != "" and t.pre_dst != ""
        ]
        total = len(targets)
        if total == 0:
            if any(getattr(t, "tone_issue", "") != "" for t in trans_list):
                # 有标记但译文已失效（pre_dst 为空）：复核对象存在但不可用
                LOGGER.info(
                    f"{self._log_tag} {filename} 存在色彩不一致标记但均无有效译文，"
                    f"跳过（请先确认译文已生成）"
                )
            else:
                # 全文件无标记：引导先执行色彩检查，避免用户误在无标记文件上执行复核
                LOGGER.info(
                    f"{self._log_tag} {filename} 无待复核的命中句（tone_issue 为空），"
                    f"跳过；若需复核请先执行词语色彩检查（ForToneCheck）产生标记"
                )
            return trans_list
        # 色彩标注是复核的唯一判定依据：无批次级元数据时保留既有标记并跳过，
        # 与第一轮（清标记后跳过）相反——复核轮绝不因缺依据而丢弃第一轮信号
        batch_metadata = self._resolve_batch_metadata(filename)
        if batch_metadata is None or not getattr(batch_metadata, "batches", None):
            LOGGER.info(
                f"{self._log_tag} {filename} 无批次级元数据（用词色彩标注），"
                f"无复核依据，保留既有标记并跳过词语色彩复核"
            )
            return trans_list
        # 复核独立批次：与第一轮共用 gpt.numPerRequestToneCheck（默认20），未配置时
        # 回退语义检测批次、改进轮批次，再兜底调用方实参。本地小模型批次不宜过大。
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
        confirm_count = 0
        dismiss_count = 0
        skip_no_guide = 0
        # 文件级元数据：作为复核语境注入（每文件解析一次，供本文件各批复用），
        # pass1_cache 无对应条目时返回 None → 不注入，行为与第一轮一致。
        metadata = self._resolve_file_metadata(filename)
        metadata_block = (
            self._format_file_metadata_block(metadata) if metadata is not None else ""
        )
        if metadata_block:
            LOGGER.debug(f"{self._log_tag} {filename} 注入文件级元数据作为复核语境")
        LOGGER.info(
            f"{self._log_tag} {filename} 开始二次复核，共 {total} 句命中句，"
            f"{total_batches} 批"
        )
        for batch_no, start in enumerate(range(0, total, num_per_request), start=1):
            self._check_stop_requested()
            batch = targets[start : start + num_per_request]
            tone_guide = self._format_tone_guide(batch, batch_metadata)
            if not tone_guide:
                # 本批句子无任何色彩标注 → 无判定依据，保留标记、不发请求
                skip_no_guide += len(batch)
                LOGGER.debug(
                    f"{self._log_tag} {filename} 批次 {batch_no}/{total_batches} "
                    f"无相交色彩标注，保留本批 {len(batch)} 句既有标记并跳过"
                )
                continue
            idx_tip = self._build_idx_tip(batch)
            _input_list, _sig_list, n_symbol, input_src = self._build_input_jsonlines(
                batch,
                proofread=True,
                filename=filename,
                include_src=True,
            )
            user_content = self._build_tonecheck_again_user_content(
                input_src, tone_guide, metadata_block
            )
            if self.pj_config.active_workers == 1:
                LOGGER.info(
                    f"-> 色彩复核输入[{batch_no}/{total_batches}] | "
                    f"backend={self.eng_type} | sentences={len(batch)}"
                )
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
                # fail-safe：调用失败保留本批既有标记，不误删第一轮信号
                continue
            result_text = preprocess_jsonline_response(raw_resp or "")
            c, d = self._parse_confirm_response(result_text, batch)
            confirm_count += c
            dismiss_count += d
            LOGGER.debug(
                f"{self._log_tag} {filename} 批次 {batch_no}/{total_batches}"
                f"（序号 {idx_tip}）确认 {c} 句，撤销 {d} 句"
            )
        LOGGER.info(
            f"{self._log_tag} {filename} 二次复核完成：确认 {confirm_count} 句，"
            f"撤销 {dismiss_count} 句"
            + (f"，{skip_no_guide} 句因无色彩标注保留" if skip_no_guide else "")
        )
        return trans_list

    def _build_tonecheck_again_user_content(
        self, input_src: str, tone_guide: str, metadata_block: str = ""
    ) -> str:
        """拼接复核轮 user 提示词：替换 [ToneGuide]/[TargetLang]/[Input]/[plot_metadata] 占位符。

        除区间色彩标注、文件级元数据、任务说明与批次 input 外，不注入术语表/
        批次元数据/历史结果/翻译规范。固定的任务说明置于最前作为缓存头（保证
        API 前缀缓存可跨文件/批次命中），metadata_block 经模板 [plot_metadata]
        占位符注入其后，tone_guide 为空时移除标注段（防御性分支：正常流程已在
        调用前跳过该批）。
        """
        prompt_req = self.trans_prompt
        if not tone_guide:
            prompt_req = prompt_req.replace(
                "<tone_guide>\n[ToneGuide]\n</tone_guide>\n\n", ""
            )
        prompt_req = prompt_req.replace("[plot_metadata]", metadata_block)
        prompt_req = prompt_req.replace("[ToneGuide]", tone_guide)
        prompt_req = prompt_req.replace("[TargetLang]", self.target_lang)
        prompt_req = prompt_req.replace("[Input]", input_src)
        return prompt_req

    def _parse_confirm_response(
        self, result_text: str, trans_list: CTransList
    ) -> Tuple[int, int]:
        """解析复核轮全量判定（keep: true/false），应用确认/撤销。

        返回 (confirm_count, dismiss_count)。语义：
        - keep: true：确认色彩不一致，保留 tone_issue（提供干净新 reason 时覆盖）；
        - keep: false：撤销标记（清空 tone_issue）；
        - 判定行缺失 / keep 值异常 / 乱码 reason：fail-safe 保留既有标记并告警。
        """
        id_map = {t.index: t for t in trans_list}
        confirm_count = 0
        dismiss_count = 0
        missing = set(id_map)
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
            missing.discard(line_id)
            tran = id_map.get(line_id)
            if tran is None:
                continue
            keep = obj.get("keep")
            if keep is True:
                reason = obj.get("reason")
                if isinstance(reason, str) and reason.strip() and "�" not in reason:
                    tran.tone_issue = reason.strip()
                confirm_count += 1
                LOGGER.debug(
                    f"{self._log_tag} 句子 {line_id} 复核确认（reason={tran.tone_issue}）"
                )
            elif keep is False:
                tran.tone_issue = ""
                dismiss_count += 1
                LOGGER.debug(f"{self._log_tag} 句子 {line_id} 复核撤销（可接受译文）")
            else:
                LOGGER.warning(
                    f"{self._log_tag} 句子 {line_id} 的 keep 值异常（{keep!r}），按保留处理"
                )
        if missing:
            LOGGER.warning(
                f"{self._log_tag} 本批 {len(missing)} 句未获得复核判定"
                f"（模型输出缺失），按保留处理"
            )
        return confirm_count, dismiss_count

    def _apply_better_result(
        self, tran: CSentense, current_dst: str, normalized: str, line_id: int
    ) -> bool:
        """防御性死代码：本引擎重写 batch_translate 与 _parse_confirm_response，
        基类调用链不会触达本方法；保留以声明「绝不写 alt_dst / 主译文」的契约。"""
        return False
