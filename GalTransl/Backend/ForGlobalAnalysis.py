"""
ForGlobalAnalysis — 全局分析汇总后端（全局分析的第 2 步：分片融合）

读取各路线分析分片（route_analysis/*.json，由 ForRouteAnalysis 产出），
连同剧情路线图的结构信息，要求 LLM 融合为一份覆盖整部游戏的全局分析报告，
写入 transl_cache/pass0_cache/GlobalPrompt.json（路径与 schema 与
ForGlobalPrompt 全文回退路径完全一致，下游零感知）。

继承 ForGlobalPrompt 的提示词装配（外部信息/人名表/术语表注入）、响应解析
规整与角色名校正，仅替换提示词与输入装配。

上游：ForRouteAnalysis 的分片 + ForPlotRouteMap 的路线图
下游：ForFileMetaData / ForBatchMetaData / ForGalJsonTranslate 注入消费
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from GalTransl import LOGGER
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.ForGlobalPrompt import ForGlobalPrompt
from GalTransl.Backend.Prompts import (
    FORGLOBALMERGE_PROMPT,
    FORGLOBALMERGE_SYSTEM,
)
from GalTransl.DataValidator import validate_global_prompt
from GalTransl.server_runtime import set_live_snippets


# ── 汇总输入装配（纯函数，供引擎与测试复用） ──

def format_mermaid_block(route_map: Optional[dict]) -> str:
    """路线图 mermaid 结构块；缺失或为空时给出占位说明。"""
    mermaid = ""
    if isinstance(route_map, dict):
        mermaid = str(route_map.get("mermaid", "") or "").strip()
    if not mermaid:
        return "（无路线图结构信息）"
    return f"```mermaid\n{mermaid}\n```"


def format_node_plots(route_map: Optional[dict]) -> str:
    """路线图「节点剧情」清单块；缺失时给出占位说明。"""
    nodes = route_map.get("节点剧情") if isinstance(route_map, dict) else None
    if not isinstance(nodes, dict) or not nodes:
        return "（无节点剧情信息）"
    lines: List[str] = []
    for route, plot in nodes.items():
        text = str(plot or "").strip()
        if text:
            lines.append(f"- {route}：{text}")
    return "\n".join(lines) if lines else "（无节点剧情信息）"


def format_shards_block(shards: Dict[str, dict]) -> str:
    """各路线分析分片块：每路线一节（路线名 + 文件清单 + 分片 JSON 单行）。"""
    if not shards:
        return "（无任何路线分析分片）"
    sections: List[str] = []
    for route, shard in shards.items():
        if not isinstance(shard, dict):
            sections.append(f"### 路线「{route}」（异常分片）\n{str(shard)}")
            continue
        files = shard.get("文件列表")
        files_text = (
            "、".join(str(f) for f in files) if isinstance(files, list) else ""
        )
        payload = {
            k: v for k, v in shard.items() if not str(k).startswith("_")
        }
        sections.append(
            f"### 路线「{route}」"
            f"{f'（文件：{files_text}）' if files_text else ''}\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )
    return "\n\n".join(sections)


# ── ForGlobalAnalysis 后端 ──

@register_engine("ForGlobalAnalysis")
class ForGlobalAnalysis(ForGlobalPrompt):
    """全局分析汇总后端：把各路线分片融合为全量 GlobalPrompt。

    不读压缩全文（与 ForGlobalPrompt 回退路径的本质区别），输入只有
    各路线分析分片与路线图结构，单次 LLM 调用完成融合。
    """

    def _default_prompts(self) -> Tuple[str, str]:
        return FORGLOBALMERGE_SYSTEM, FORGLOBALMERGE_PROMPT

    async def batch_translate(
        self,
        shards: Dict[str, dict],
        route_map: Optional[dict] = None,
        external_info: str = "",
    ) -> bool:
        """汇总各路线分析分片，生成全量 GlobalPrompt.json。

        Args:
            shards: 路线名 -> 分片 dict（含「路线名/文件列表」元数据与 6 个
                分析字段；孤儿分片应由调用方先行过滤）
            route_map: 剧情路线图 dict（可选，缺 mermaid/节点剧情时对应块退化）
            external_info: 外部信息；空串时读 externals.gameInfo

        Returns:
            True 如果汇总成功并写入 GlobalPrompt.json，否则 False
        """
        if not shards or not isinstance(shards, dict):
            LOGGER.error(
                f"[全局汇总] shards 类型错误或为空，"
                f"期望非空 dict，实际 {type(shards).__name__}，跳过"
            )
            return False
        valid_shards: Dict[str, dict] = {}
        for r, s in shards.items():
            if not isinstance(s, dict):
                LOGGER.warning(f"[全局汇总] 分片「{r}」不是 dict，跳过")
                continue
            has_plot = bool(str(s.get("剧情概述", "") or "").strip())
            chars = s.get("角色列表")
            has_chars = isinstance(chars, list) and len(chars) > 0
            if not has_plot and not has_chars:
                LOGGER.warning(f"[全局汇总] 分片「{r}」缺少分析内容，跳过")
                continue
            valid_shards[r] = s
        shards = valid_shards
        if not shards:
            LOGGER.warning("[全局汇总] 无有效分片（均缺少分析内容），跳过")
            return False

        if not external_info:
            external_info = self.pj_config.getKey("externals.gameInfo", "") or ""

        prompt = self._build_prompt_request(
            "", self._build_glossary_text(), external_info=external_info
        )
        prompt = prompt.replace("[RouteMermaid]", format_mermaid_block(route_map))
        prompt = prompt.replace("[RouteNodePlots]", format_node_plots(route_map))
        prompt = prompt.replace("[RouteShards]", format_shards_block(shards))

        LOGGER.info(
            f"[全局汇总] 开始融合 {len(shards)} 条路线分析分片…"
        )
        LOGGER.debug(
            f"[全局汇总] 提示词长度：{len(prompt)} 字符"
        )
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt},
        ]
        rsp, token = await self._call_llm_with_error_report(
            messages, "GlobalAnalysis", max_retry_count=3, tag="GlobalMerge"
        )
        if rsp is None:
            LOGGER.error("[全局汇总] LLM 调用失败")
            return False

        meta = self._parse_global_prompt(rsp or "")
        if not meta:
            LOGGER.warning("[全局汇总] 未解析到有效的 GlobalPrompt JSON，跳过")
            return False
        meta = self._normalize_global_prompt(meta)

        validation = validate_global_prompt(meta)
        if not validation["valid"]:
            for err in validation["errors"]:
                LOGGER.error(f"[全局汇总] 内容校验失败：{err}")
            return False
        for warn in validation["warnings"]:
            LOGGER.warning(f"[全局汇总] 内容校验警告：{warn}")

        corrected = self._correct_character_names(meta)
        if corrected:
            LOGGER.info(f"[全局汇总] 已按人名对照校正 {corrected} 个角色名")

        self._save_global_prompt(meta)

        # 推送结果预览（前端翻译控制台"结果预览"；预览异常不影响主流程）
        try:
            set_live_snippets(
                self.runtime_project_dir,
                translation_preview=json.dumps(meta, ensure_ascii=False, indent=2),
            )
        except Exception:
            pass

        LOGGER.info(
            f"[全局汇总] 融合完成：{len(meta.get('角色列表', []))} 个角色，"
            f"已写入 transl_cache/pass0_cache/GlobalPrompt.json"
        )
        return True


if __name__ == "__main__":
    pass
