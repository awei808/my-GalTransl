"""语义差异检测后端：用本地/外部 AI 判断原文与译文的语义是否存在极大差异。

仅判定、不产译文：命中句由本后端置 suspected_error 标记（AI 判定原因或固定
标记 "1"），随后 find_problems 认领并输出「疑似错误」问题（problem 字段），
不触碰主译文与备选译文。作为完整流水线的后处理阶段
（gpt.afterTranslation=semcheck）使用，也可作为独立引擎对已翻译文件执行。
引擎标识：ForSemCheck
"""

from typing import Optional, Tuple

from GalTransl import LOGGER
from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.CSentense import CSentense, CTransList
from GalTransl.ConfigHelper import CProxyPool, CProjectConfig
from GalTransl.Service import JobCancelledError
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.BaseFixRound import BaseSparseFixRound
from GalTransl.Backend.Prompts import (
    FAILED_PREFIX,
    FORGAL_JSON_SEMCHECK_PROMPT,
    FORSEMCHECK_SYSTEM,
)
from GalTransl.Backend.utils import decode_json_line_part, preprocess_jsonline_response


class _EmptyTokenPool:
    """空令牌池占位：主 profile 不可用时保证基类 init_chatbot 不崩溃（不发任何请求）。"""

    def get_available_token(self) -> list:
        return []


@register_engine("ForSemCheck")
class ForSemCheck(BaseSparseFixRound):
    """语义差异检测后端。

    向 AI 发送「原文 + 当前译文」（h 场景照常检测），AI 仅输出语义存在极大
    差异的句子 id（可选 reason），本后端为命中句置 suspected_error 标记。
    问题类型统一由 find_problems 认领为「疑似错误」。

    与主翻译 profile 共用令牌池（与其他后处理后端 ForImproveTranslation /
    ForBRStation 等一致）：外部 OpenAI 兼容大模型与本地 llama.cpp 均可直接
    使用，取决于「后端配置」页所选端点；主池无可用 token 时降级跳过，不发
    任何请求。

    引擎标识：ForSemCheck
    """

    # 日志前缀
    _log_tag = "[语义检测]"
    # 输入携带原文 src（语义差异判定必需）
    _include_src = True
    # 不注入 problem（避免模型受规则问题干扰，只看语义）
    _inject_problem = False
    # 响应非空但 0 命中时告警（疑似输出格式异常）
    _warn_on_zero_found = True

    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool] = None,
        token_pool: Optional[COpenAITokenPool] = None,
    ) -> None:
        """
        初始化语义差异检测后端。

        与其他后处理后端一致，直接复用主翻译 profile 的令牌池（token_pool），
        不维护独立端点：外部 OpenAI 兼容大模型与本地 llama.cpp 均可用，
        取决于「后端配置」页所选端点。

        Args:
            config: 项目配置对象。
            eng_type: 引擎标识（ForSemCheck）。
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
        # 覆盖基类（翻译轮）的系统提示词为检测轮专用角色声明
        self.system_prompt = FORSEMCHECK_SYSTEM
        self.trans_prompt = FORGAL_JSON_SEMCHECK_PROMPT
        # 覆盖默认值后统一重放 change_prompt 与用户模板 override（基类 __init__ 已应用过一次）
        self._finalize_prompts()

    def _filter_target_translations(self, trans_list: CTransList) -> list:
        """检测全部已有有效译文的句子（含 h 场景，跳过 skip_check）。"""
        return [
            t
            for t in trans_list
            if t.post_src != ""
            and t.pre_dst != ""
            and FAILED_PREFIX not in t.pre_dst
            and not getattr(t, "skip_check", False)
        ]

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
        """检测轮入口：禁用/未配置时降级跳过；否则清旧标记后单轮逐批检测。

        suspected_error 为持久化信号：进入检测即清除全部旧标记再写新结果（幂等），
        避免重复运行累积误报。
        单轮模式：不维护多轮对话历史（不累积上下文），每批只发送
        [system] + 本批 user（仅注入任务说明与批次 input），不注入术语表、
        批次元数据、历史结果、翻译规范、全局分析、文件级元数据。
        """
        if self._disabled_reason:
            LOGGER.warning(
                f"{self._log_tag} {filename}：{self._disabled_reason}，跳过语义差异检测"
            )
            return trans_list
        for t in trans_list:
            t.suspected_error = ""
        targets = self._filter_target_translations(trans_list)
        total = len(targets)
        if total == 0:
            LOGGER.info(f"{self._log_tag} {filename} 无可检测的有效译文，跳过")
            return trans_list
        # 语义检测独立批次：优先 gpt.numPerRequestSemCheck（默认20），
        # 未配置时回退改进轮批次，再兜底调用方实参。本地小模型批次不宜过大。
        num_per_request = self._coerce_positive_int(
            self.pj_config.getKey("gpt.numPerRequestSemCheck"),
            self._coerce_positive_int(
                self.pj_config.getKey("gpt.numPerRequestBetter"),
                num_pre_request or 20,
            ),
        )
        total_batches = (total + num_per_request - 1) // num_per_request
        hit_count = 0
        LOGGER.info(
            f"{self._log_tag} {filename} 开始单轮语义差异检测，共 {total} 句，{total_batches} 批"
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
            user_content = self._build_semcheck_user_content(input_src)
            call_messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_content},
            ]
            if self.pj_config.active_workers == 1:
                LOGGER.info(
                    f"-> 语义检测输入[{batch_no}/{total_batches}] | "
                    f"backend={self.eng_type} | sentences={len(batch)}"
                )
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
                continue
            result_text = preprocess_jsonline_response(raw_resp or "")
            batch_hit, _found = self._parse_fix_response(result_text, batch, n_symbol)
            if self._warn_on_zero_found and result_text.strip() and _found == 0:
                _warn_msg = (
                    f"{self._log_tag}[{filename}:{idx_tip}] 模型响应非空但未解析到任何 "
                    f"命中（输出格式异常或内容问题），本次 0 句处理"
                )
                LOGGER.warning(_warn_msg)
                self._record_round_runtime_error(filename, idx_tip, _warn_msg, None)
            hit_count += batch_hit
            LOGGER.debug(
                f"{self._log_tag} {filename} 批次 {batch_no}/{total_batches}"
                f"（序号 {idx_tip}）命中 {batch_hit} 句"
            )
        if hit_count > 0:
            LOGGER.info(f"{self._log_tag} {filename} 语义检测完成，共命中 {hit_count} 句")
        else:
            LOGGER.info(f"{self._log_tag} {filename} 语义检测完成，未发现疑似错误")
        return trans_list

    def _build_semcheck_user_content(self, input_src: str) -> str:
        """单轮拼接语义检测 user 提示词：仅注入任务说明与批次 input。

        模板已精简为「任务说明 + input 块」，此处只做 [TargetLang]、[Input]
        两个占位符替换，不注入术语表/批次元数据/历史结果/翻译规范等其它内容。
        """
        prompt_req = self.trans_prompt
        prompt_req = prompt_req.replace("[TargetLang]", self.target_lang)
        prompt_req = prompt_req.replace("[Input]", input_src)
        return prompt_req

    def _parse_fix_response(
        self, result_text: str, trans_list: CTransList, n_symbol: str
    ) -> Tuple[int, int]:
        """按 id 稀疏解析判定结果（仅取 id 与可选 reason），命中句置 suspected_error。

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
            tran.suspected_error = (
                str(reason).strip()
                if isinstance(reason, str) and str(reason).strip()
                else "1"
            )
            hit_count += 1
            LOGGER.debug(
                f"{self._log_tag} 句子 {line_id} 判定疑似错误（reason={tran.suspected_error}）"
            )
        return hit_count, hit_count

    def _apply_better_result(
        self, tran: CSentense, current_dst: str, normalized: str, line_id: int
    ) -> bool:
        """防御：本引擎只做标记，绝不写 alt_dst / 主译文。"""
        return False
