"""缓存重建引擎（rebuildr / rebuilda）：不翻译，只用现有缓存重刷译文与结果。

- rebuildr：重建结果 —— 经缓存重刷 gt_output，不修改缓存；
- rebuilda：重建缓存和结果 —— 同上，并把重刷后的缓存（problem / post_dst_preview
  等派生字段一并刷新）写回。

任何一句未命中缓存都整体失败：重建不调用模型，只能基于现有缓存。未命中明细由
Cache 层以 [cache]ERROR 日志逐句给出（eng_type 含 "rebuild" 时自动升级日志级别）。
"""
from __future__ import annotations

from typing import Optional

from GalTransl import LOGGER
from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProjectConfig, CProxyPool
from GalTransl.CSentense import CTransList
from GalTransl.Dictionary import CGptDict
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.BaseTranslate import BaseTranslate

# 重建引擎标识：LLMTranslate 的阶段7后处理与缓存刷新门控使用
REBUILD_ENGINES = ("rebuildr", "rebuilda")

# 未命中示例最多列举条数与例句截断长度：全部列出会淹没日志，给前几条帮助定位即可
_MISS_EXAMPLE_MAX = 5
_MISS_EXAMPLE_SOURCE_MAX = 24


@register_engine("rebuildr")
@register_engine("rebuilda")
class CRebuildTranslate(BaseTranslate):
    """重建引擎：缓存全命中时为空操作，任何未命中即报错终止。"""

    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool] = None,
        token_pool: COpenAITokenPool = None,
    ) -> None:
        # 重建引擎不持有模型客户端：整体覆写 __init__，不跑基类的客户端初始化
        self.pj_config = config
        self.eng_type = eng_type
        mode_text = "重建结果（不修改缓存）" if eng_type == "rebuildr" else "重建缓存与结果"
        LOGGER.info(f"[rebuild]重建模式：{mode_text}")

    async def shutdown(self) -> None:
        """空操作：不持有模型客户端，基类 shutdown 引用的客户端属性不存在。"""
        return None

    def _incomplete_message(self, filename: str, translist_unhit: CTransList) -> str:
        """把「缓存不完整」讲清楚：几句未命中、例句与后续处理提示。"""
        examples = []
        for tran in translist_unhit[:_MISS_EXAMPLE_MAX]:
            source = (tran.pre_src or "").replace("\n", " ").strip()
            if len(source) > _MISS_EXAMPLE_SOURCE_MAX:
                source = source[:_MISS_EXAMPLE_SOURCE_MAX] + "…"
            examples.append(f"#{tran.index}「{source}」")
        more = (
            f" 等 {len(translist_unhit)} 句"
            if len(translist_unhit) > _MISS_EXAMPLE_MAX
            else ""
        )
        return (
            f"{filename} 缓存不完整：重建不调用模型翻译，只能基于现有缓存重刷，"
            f"但 {len(translist_unhit)} 句未命中缓存（{'、'.join(examples)}{more}），已终止。"
            f"未命中原因见上方 [cache]ERROR 日志（pre_src 变更 / 译文为空 / 标记失败等），"
            f"请修正或补齐缓存后重试。"
        )

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
        translist_hit: Optional[CTransList] = None,
        translist_unhit: Optional[CTransList] = None,
    ) -> CTransList:
        """主流程仅在存在未命中句时调用本方法：此时按重建约定整体失败。"""
        unhit = translist_unhit or []
        if unhit:
            raise RuntimeError(self._incomplete_message(filename, unhit))
        return trans_list
