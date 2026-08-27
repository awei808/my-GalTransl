"""语义复核后端：对 ForSemCheck 标记的「疑似错误」命中句做二次复核。

第一轮 ForSemCheck 判定为疑似错误的句子（suspected_error 非空）作为本引擎的
输入；本引擎逐句要求 AI 给出「确认/撤销」结论，仅保留确认仍为实质错译的标记，
撤销属于可接受译文（合理意译、h 场景委婉化等）的误报标记。
不产译文、不触碰主译文与备选译文。可独立运行，也可在语义检测之后按文件调用。
引擎标识：ForSemCheckAgain
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
    FORGAL_JSON_SEMCHECK_AGAIN_PROMPT,
    FORSEMCHECK_AGAIN_SYSTEM,
)
from GalTransl.Backend.utils import decode_json_line_part, preprocess_jsonline_response


class _EmptyTokenPool:
    """空令牌池占位：主 profile 不可用时保证基类 init_chatbot 不崩溃（不发任何请求）。"""

    def get_available_token(self) -> list:
        return []


@register_engine("ForSemCheckAgain")
class ForSemCheckAgain(BaseImproveRound):
    """语义复核后端（命中句二次复核）。

    输入为第一轮 ForSemCheck 已标记 suspected_error 的句子；本引擎把「src+dst」
    重新发给 AI，并要求对输入每一行显式给出 keep: true/false 结论，只保留确认
    的标记、撤销误报。与 ForSemCheck 的差异：

    - 不预清 suspected_error：撤销才清（显式 keep:false），保留第一轮信号，
      避免「复核轮误删真错」；
    - 输出为全量判定（每行 keep），而非稀疏命中，不适用整批回显判定；
    - LLM 调用失败 / 判定行缺失 / keep 值异常一律 fail-safe 保留既有标记，
      绝不因复核故障清空第一轮信号。

    与主翻译 profile 共用令牌池（与其他后处理后端一致）：外部 OpenAI 兼容
    大模型与本地 llama.cpp 均可直接使用，取决于「后端配置」页所选端点；本引擎
    额外支持未传 token_pool 时按主 profile 自建，主池无可用 token 时降级跳过，
    不发任何请求。

    引擎标识：ForSemCheckAgain
    """

    # 日志前缀
    _log_tag = "[语义复核]"
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
        初始化语义复核后端。

        与 ForSemCheck 一致：直接复用主翻译 profile 的令牌池（token_pool），
        不维护独立端点；未传 token_pool（独立调用/测试）时按主 profile 自建，
        构建失败或主池无可用 token 时降级禁用（跳过复核、不发请求）。

        Args:
            config: 项目配置对象。
            eng_type: 引擎标识（ForSemCheckAgain）。
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
        # 覆盖基类（翻译轮）的系统提示词为复核轮专用角色声明
        self.system_prompt = FORSEMCHECK_AGAIN_SYSTEM
        self.trans_prompt = FORGAL_JSON_SEMCHECK_AGAIN_PROMPT
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
        """复核轮入口：禁用/未配置时降级跳过；否则对上一轮命中句逐批二次复核。

        与 ForSemCheck 不同：不预清 suspected_error（第一轮信号保留），仅对
        显式 keep:false 的句子撤销标记；LLM 调用失败/判定缺失按 fail-safe
        保留处理。
        """
        if self._disabled_reason:
            LOGGER.warning(
                f"{self._log_tag} {filename}：{self._disabled_reason}，跳过语义复核"
            )
            return trans_list
        # 复核对象：上一轮已标记的命中句（suspected_error 非空），
        # 且仍有有效译文（pre_dst 非空），避免对失效译文空发请求
        targets = [
            t
            for t in trans_list
            if getattr(t, "suspected_error", "") != "" and t.pre_dst != ""
        ]
        total = len(targets)
        if total == 0:
            if any(getattr(t, "suspected_error", "") != "" for t in trans_list):
                # 有标记但译文已失效（pre_dst 为空）：说明复核对象存在但不可用
                LOGGER.info(
                    f"{self._log_tag} {filename} 存在疑似错误标记但均无有效译文，"
                    f"跳过（请先确认译文已生成）"
                )
            else:
                # 全文件无标记：引导先执行语义检测，避免用户误在无标记文件上执行复核
                LOGGER.info(
                    f"{self._log_tag} {filename} 无待复核的命中句（suspected_error 为空），"
                    f"跳过；若需复核请先执行语义差异检测（ForSemCheck）产生标记"
                )
            return trans_list
        # 复核独立批次：与语义检测共用 gpt.numPerRequestSemCheck（默认20），
        # 未配置时回退改进轮批次，再兜底调用方实参。本地小模型批次不宜过大。
        num_per_request = self._coerce_positive_int(
            self.pj_config.getKey("gpt.numPerRequestSemCheck"),
            self._coerce_positive_int(
                self.pj_config.getKey("gpt.numPerRequestBetter"),
                num_pre_request or 20,
            ),
        )
        total_batches = (total + num_per_request - 1) // num_per_request
        confirm_count = 0
        dismiss_count = 0
        # 文件级元数据：作为复核语境注入（每文件解析一次，供本文件各批复用），
        # 无 FileMetaData.json 时返回 None → 不注入，行为与旧版一致。
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
            idx_tip = self._build_idx_tip(batch)
            _input_list, _sig_list, n_symbol, input_src = self._build_input_jsonlines(
                batch,
                proofread=True,
                filename=filename,
                include_src=True,
            )
            user_content = self._build_semcheck_user_content(input_src, metadata_block)
            if self.pj_config.active_workers == 1:
                LOGGER.info(
                    f"-> 语义复核输入[{batch_no}/{total_batches}] | "
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
        )
        return trans_list

    def _build_semcheck_user_content(self, input_src: str, metadata_block: str = "") -> str:
        """拼接复核轮 user 提示词：替换 [TargetLang]/[Input] 占位符，可选注入文件级元数据。

        除任务说明与批次 input 外，不注入术语表/批次元数据/历史结果/翻译规范。
        metadata_block 非空时置于任务说明之前（<plot_metadata> 作为全局语境）。
        """
        prompt_req = self.trans_prompt
        if metadata_block:
            prompt_req = metadata_block + prompt_req
        prompt_req = prompt_req.replace("[TargetLang]", self.target_lang)
        prompt_req = prompt_req.replace("[Input]", input_src)
        return prompt_req

    def _parse_confirm_response(
        self, result_text: str, trans_list: CTransList
    ) -> Tuple[int, int]:
        """解析复核轮全量判定（keep: true/false），应用确认/撤销。

        返回 (confirm_count, dismiss_count)。语义：
        - keep: true：确认错译，保留 suspected_error（提供干净新 reason 时覆盖）；
        - keep: false：撤销标记（清空 suspected_error）；
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
                    tran.suspected_error = reason.strip()
                confirm_count += 1
                LOGGER.debug(
                    f"{self._log_tag} 句子 {line_id} 复核确认（reason={tran.suspected_error}）"
                )
            elif keep is False:
                tran.suspected_error = ""
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
