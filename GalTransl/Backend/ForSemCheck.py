"""语义差异检测后端：用本地/外部 AI 判断原文与译文的语义是否存在极大差异。

仅判定、不产译文：命中句由本后端置 suspected_error 标记（AI 判定原因或固定
标记 "1"），随后 find_problems 认领并输出「疑似错误」问题（problem 字段），
不触碰主译文与备选译文。作为完整流水线的后处理阶段
（gpt.afterTranslation=semcheck）使用，也可作为独立引擎对已翻译文件执行。
引擎标识：ForSemCheck
"""

from typing import Optional, Tuple

from GalTransl import LOGGER
from GalTransl.COpenAI import COpenAIToken, COpenAITokenPool
from GalTransl.CSentense import CSentense, CTransList
from GalTransl.ConfigHelper import CProxyPool, CProjectConfig
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.BaseFixRound import BaseSparseFixRound
from GalTransl.Backend.Prompts import (
    FAILED_PREFIX,
    FORGAL_JSON_SEMCHECK_PROMPT,
    FORSEMCHECK_SYSTEM,
)
from GalTransl.Backend.utils import decode_json_line_part


@register_engine("ForSemCheck")
class ForSemCheck(BaseSparseFixRound):
    """语义差异检测后端。

    向 AI 发送「原文 + 当前译文」（h 场景照常检测），AI 仅输出语义存在极大
    差异的句子 id（可选 reason），本后端为命中句置 suspected_error 标记。
    问题类型统一由 find_problems 认领为「疑似错误」。

    端点独立于主翻译 profile（gpt.semCheck.* 段）：本地 llama.cpp 与外部
    OpenAI 兼容大模型通用；未启用或未配置时降级跳过，不发任何请求。

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

        Args:
            config: 项目配置对象。
            eng_type: 引擎标识（ForSemCheck）。
            proxy_pool: 代理池对象，为 None 时不使用代理。
            token_pool: 主翻译令牌池；本引擎使用独立端点（gpt.semCheck.*），
                不使用该参数，避免校对请求误打到主翻译 profile。
        """
        self._disabled_reason = ""
        enabled = self._coerce_bool(config.getKey("gpt.semCheck.enabled", False))
        endpoint = (config.getKey("gpt.semCheck.endpoint", "") or "").strip()
        if not enabled:
            self._disabled_reason = "gpt.semCheck.enabled 未启用"
        elif not endpoint:
            self._disabled_reason = "gpt.semCheck.endpoint 未配置"

        model_name = (config.getKey("gpt.semCheck.modelName", "") or "").strip()
        api_key = (config.getKey("gpt.semCheck.apiKey", "local") or "").strip()
        stream = self._coerce_bool(config.getKey("gpt.semCheck.stream", True))
        provider = (config.getKey("gpt.semCheck.provider", "auto") or "auto").strip()

        # 独立令牌池：仅包含语义检测端点，与主翻译 profile 隔离（不误打云端）。
        # 禁用/未配置时 endpoint 为空 → token 列表为空，本引擎不发任何请求。
        sem_pool = COpenAITokenPool(config, eng_type)
        if endpoint:
            sem_token = COpenAIToken(
                token=api_key or "local",
                domain=endpoint,
                model_name=model_name or "local-model",
                stream=stream,
                isAvailable=True,
            )
            sem_pool.tokens = [(True, sem_token)]
        else:
            sem_pool.tokens = []
        super().__init__(config, eng_type, proxy_pool, sem_pool)
        # 覆盖基类（翻译轮）的系统提示词为检测轮专用角色声明
        self.system_prompt = FORSEMCHECK_SYSTEM
        self.trans_prompt = FORGAL_JSON_SEMCHECK_PROMPT
        # 思考参数路由：按 gpt.semCheck.provider 覆盖主 profile 推断（auto=按模型名推断，
        # 如外部 deepseek 可显式指定，避免误发不兼容参数）
        self.provider = provider
        # 语义检测专用超时：独立于主翻译 profile 的 apiTimeout（防御非法配置回退 120）
        try:
            self.api_timeout = int(config.getKey("gpt.semCheck.apiTimeout", 120) or 120)
        except (TypeError, ValueError):
            self.api_timeout = 120
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
        """检测轮入口：禁用/未配置时降级跳过；否则清旧标记后复用基类分桶流程。

        suspected_error 为持久化信号：进入检测即清除全部旧标记再写新结果（幂等），
        避免重复运行累积误报。
        """
        if self._disabled_reason:
            LOGGER.warning(
                f"{self._log_tag} {filename}：{self._disabled_reason}，跳过语义差异检测"
            )
            return trans_list
        for t in trans_list:
            t.suspected_error = ""
        return await super().batch_translate(
            filename,
            cache_file_path,
            trans_list,
            num_pre_request,
            retry_failed=retry_failed,
            gpt_dic=gpt_dic,
            proofread=proofread,
            retran_key=retran_key,
            translist_hit=translist_hit,
            translist_unhit=translist_unhit,
        )

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
