"""
ForRouteAnalysis — 路线分析后端（全局分析的第 1 步：逐路线局部分析）

基于剧情路线图（PlotRouteMap.json）的「文件归属」，对每条路线包含的剧本文件
独立做一次游戏分析，产出 transl_cache/pass0_cache/route_analysis/<路线名>.json
分片；全部分片由 ForGlobalAnalysis 汇总为全量 GlobalPrompt.json。

继承 ForGlobalPrompt 的提示词装配（外部信息/人名表/术语表注入）、响应解析规整
与角色名校正，仅替换提示词与产物落盘（分片而非单文件）。

上游：TextCompressor 压缩输出 + ForPlotRouteMap 的文件归属划分
下游：ForGlobalAnalysis 汇总
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from GalTransl import LOGGER, PASS0_CACHE_DIR
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.ForGlobalPrompt import ForGlobalPrompt
from GalTransl.Backend.ForPlotRouteMap import load_plot_route_map
from GalTransl.Backend.Prompts import (
    FORROUTEANALYSIS_PROMPT,
    FORROUTEANALYSIS_SYSTEM,
)
from GalTransl.Backend.utils import select_paths_by_filter
from GalTransl.DataValidator import validate_global_prompt
from GalTransl.server_runtime import set_live_snippets


ROUTE_ANALYSIS_DIR = "route_analysis"

_MAX_ROUTE_NAME_LEN = 50
_DEFAULT_PARALLELISM = 2


# ── 分片路径与命名 ──

def shard_dir(pj_config: Any) -> str:
    """返回路线分析分片目录（pass0_cache/route_analysis）。"""
    return os.path.join(pj_config.getCachePath(), PASS0_CACHE_DIR, ROUTE_ANALYSIS_DIR)


def sanitize_route_name(name: str) -> str:
    """把路线名安全化为可作文行的文件名（不含扩展名）。

    剔除 Windows 非法字符与控制字符、折叠空白、截断至 50 字符；
    清洗后为空时回退「未命名路线」。
    """
    cleaned = "".join(
        ch for ch in str(name or "") if ch.isprintable() and ch not in '\\/:*?"<>|'
    )
    cleaned = " ".join(cleaned.split()).strip(" .")
    if not cleaned:
        return "未命名路线"
    return cleaned[:_MAX_ROUTE_NAME_LEN].strip(" .") or "未命名路线"


def load_route_shards(
    pj_config: Any, valid_routes: Optional[set] = None
) -> Dict[str, dict]:
    """载入全部分片，键为分片内的「路线名」。

    Args:
        pj_config: 项目配置对象。
        valid_routes: 非 None 时只保留路线名在集合内的分片（孤儿分片记 warning 跳过）。

    Returns:
        路线名 -> 分片 dict；目录不存在或无有效分片时返回空 dict。
    """
    out_dir = shard_dir(pj_config)
    if not os.path.isdir(out_dir):
        return {}
    shards: Dict[str, dict] = {}
    for name in sorted(os.listdir(out_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(out_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                shard = json.load(f)
        except Exception as e:
            LOGGER.warning(f"[路线分析] 分片 {name} 读取失败，跳过：{e}")
            continue
        if not isinstance(shard, dict) or not str(shard.get("路线名", "")).strip():
            LOGGER.warning(f"[路线分析] 分片 {name} 缺少有效「路线名」，跳过")
            continue
        route = str(shard["路线名"]).strip()
        if valid_routes is not None and route not in valid_routes:
            LOGGER.warning(
                f"[路线分析] 分片 {name} 的路线「{route}」不在当前路线图中，"
                f"跳过（孤儿分片，不自动删除）"
            )
            continue
        shards[route] = shard
    return shards


def shard_up_to_date(pj_config: Any, route_name: str, route_files: List[str]) -> bool:
    """判断分片是否可复用：存在、可解析且「文件列表」与当前路线归属一致。"""
    path = os.path.join(shard_dir(pj_config), sanitize_route_name(route_name) + ".json")
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            shard = json.load(f)
    except Exception:
        return False
    old_files = shard.get("文件列表")
    if not isinstance(old_files, list):
        return False
    normalize = lambda ps: sorted(os.path.normcase(str(p)) for p in ps)
    return normalize(old_files) == normalize(route_files)


# ── 路线划分推导 ──

def derive_route_file_map(
    plot_route_map: Optional[dict],
    compressed_texts: Dict[str, str],
) -> Tuple[Dict[str, List[str]], List[str]]:
    """由路线图「文件归属」反推 路线 -> 压缩文本路径列表。

    归属键按完整路径/文件名/去扩展名三级宽松匹配（与全局分析 file_filter 同口径）。

    Returns:
        (routes, unmatched_keys)
        routes: 路线名 -> 匹配到的 compressed_texts 完整路径列表（保序去重）
        unmatched_keys: 「文件归属」中无法匹配任何压缩文本的键（含空路线名）
    """
    file_map = (plot_route_map or {}).get("文件归属")
    if not isinstance(file_map, dict) or not file_map:
        return {}, []
    routes: Dict[str, List[str]] = {}
    unmatched: List[str] = []
    for raw_key, raw_route in file_map.items():
        route = str(raw_route or "").strip()
        matched = (
            select_paths_by_filter(
                compressed_texts.keys(), [str(raw_key)], tag="RouteAnalysis"
            )
            if route
            else []
        )
        if not matched:
            unmatched.append(str(raw_key))
            continue
        bucket = routes.setdefault(route, [])
        for p in matched:
            if p not in bucket:
                bucket.append(p)
    return routes, unmatched


# ── ForRouteAnalysis 后端 ──

@register_engine("ForRouteAnalysis")
class ForRouteAnalysis(ForGlobalPrompt):
    """路线分析后端：逐路线生成局部游戏分析分片。

    不翻译、不多轮；每条路线一次 LLM 调用。路线划分来自剧情路线图的
    「文件归属」，缺失路线图时 batch_translate 直接失败（回退全文分析
    由调用方决定）。
    """

    def _default_prompts(self) -> Tuple[str, str]:
        return FORROUTEANALYSIS_SYSTEM, FORROUTEANALYSIS_PROMPT

    # 1. 单路线分析（生成一个分片）
    async def analyze_route(
        self,
        compressed_data: Dict[str, str],
        route_name: str,
        route_files: List[str],
        external_info: str = "",
    ) -> bool:
        """对单条路线做游戏分析并写入分片。

        Returns:
            True 如果分片生成并写入成功，否则 False
        """
        parts: List[str] = []
        for file_path in route_files:
            text = compressed_data.get(file_path, "")
            if text and text.strip():
                parts.append(f"=== {os.path.basename(file_path)} ===\n{text.strip()}")
        input_text = "\n\n".join(parts)
        if not input_text.strip():
            LOGGER.warning(f"[路线分析] 路线「{route_name}」无有效压缩文本，跳过")
            return False

        glossary_text = self._build_glossary_text()
        route_files_text = "、".join(os.path.basename(p) for p in route_files)
        prompt = self._build_prompt_request(
            input_text, glossary_text, external_info=external_info
        )
        prompt = prompt.replace("[RouteName]", route_name)
        prompt = prompt.replace("[RouteFiles]", route_files_text)

        LOGGER.info(
            f"[路线分析] 开始分析路线「{route_name}」"
            f"（{len(route_files)} 个文件，{len(input_text)} 字符）…"
        )
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt},
        ]
        rsp, token = await self._call_llm_with_error_report(
            messages,
            f"RouteAnalysis:{route_name}",
            max_retry_count=3,
            tag="RouteAnalysis",
        )
        if rsp is None:
            LOGGER.error(f"[路线分析] 路线「{route_name}」LLM 调用失败")
            return False

        meta = self._parse_global_prompt(rsp or "")
        if not meta:
            LOGGER.warning(f"[路线分析] 路线「{route_name}」未解析到有效 JSON，跳过")
            return False
        meta = self._normalize_global_prompt(meta)

        validation = validate_global_prompt(meta)
        if not validation["valid"]:
            for err in validation["errors"]:
                LOGGER.error(f"[路线分析] 路线「{route_name}」内容校验失败：{err}")
            return False
        for warn in validation["warnings"]:
            LOGGER.warning(f"[路线分析] 路线「{route_name}」内容校验警告：{warn}")

        corrected = self._correct_character_names(meta)
        if corrected:
            LOGGER.info(
                f"[路线分析] 路线「{route_name}」已按人名对照校正 {corrected} 个角色名"
            )

        shard: Dict[str, Any] = {
            "路线名": route_name,
            "文件列表": list(route_files),
            "生成时间": datetime.now().isoformat(timespec="seconds"),
            "trans_by": getattr(token, "model_name", "") or "",
            **meta,
        }
        self._save_route_shard(route_name, shard)

        # 推送结果预览（前端翻译控制台"结果预览"；预览异常不影响主流程）
        try:
            set_live_snippets(
                self.runtime_project_dir,
                translation_preview=json.dumps(shard, ensure_ascii=False, indent=2),
            )
        except Exception:
            pass

        LOGGER.info(
            f"[路线分析] 路线「{route_name}」分析完成，"
            f"{len(meta.get('角色列表', []))} 个角色，已写入分片"
        )
        return True

    # 2. 分片落盘（先写 .tmp 再 os.replace，原子替换口径同 Cache.py）
    def _save_route_shard(self, route_name: str, shard: dict) -> None:
        out_dir = shard_dir(self.pj_config)
        os.makedirs(out_dir, exist_ok=True)
        path = self._resolve_shard_path(out_dir, route_name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(shard, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        LOGGER.debug(f"[路线分析] 已保存分片 {path}")

    def _resolve_shard_path(self, out_dir: str, route_name: str) -> str:
        """确定分片文件路径：本路线旧分片直接覆盖；与他路线安全化后同名时加序号。"""
        base = sanitize_route_name(route_name)
        candidate = base
        n = 2
        while True:
            path = os.path.join(out_dir, candidate + ".json")
            if not os.path.exists(path):
                return path
            try:
                with open(path, "r", encoding="utf-8") as f:
                    owner = json.load(f).get("路线名")
            except Exception:
                owner = None
            if owner == route_name:
                return path
            candidate = f"{base}-{n}"
            n += 1

    # 3. 入口：全路线分析（流水线与独立引擎共用）
    async def batch_translate(
        self,
        compressed_data: Dict[str, str],
        force_regen: bool = False,
        parallelism: Optional[int] = None,
        external_info: str = "",
    ) -> bool:
        """按路线图划分逐路线分析（已可复用的分片自动跳过）。

        Args:
            compressed_data: {文件路径: 压缩后文本}，来自 TextCompressor
            force_regen: True 时忽略已有分片全部重算
            parallelism: 路线级并行度；None 时读
                internals.globalanalysis.routeParallelism（默认 2）
            external_info: 外部信息；空串时读 externals.gameInfo

        Returns:
            True 当路线图存在且全部分片就绪（新生成或复用），否则 False
        """
        if not compressed_data or not isinstance(compressed_data, dict):
            LOGGER.error(
                f"[路线分析] compressed_data 类型错误，"
                f"期望 dict，实际 {type(compressed_data).__name__}，跳过"
            )
            return False
        compressed_data = {
            k: v
            for k, v in compressed_data.items()
            if v and isinstance(v, str) and v.strip()
        }
        if not compressed_data:
            LOGGER.warning("[路线分析] compressed_data 全为空，跳过")
            return False

        route_map = load_plot_route_map(self.pj_config)
        if not route_map:
            LOGGER.error(
                "[路线分析] 未找到有效的剧情路线图（PlotRouteMap.json），"
                "无法进行路线分析"
            )
            return False

        routes, unmatched = derive_route_file_map(route_map, compressed_data)
        if unmatched:
            LOGGER.warning(
                f"[路线分析] 「文件归属」中 {len(unmatched)} 个文件未匹配到"
                f"压缩文本，忽略：{unmatched}"
            )
        routes = {r: fs for r, fs in routes.items() if fs}
        if not routes:
            LOGGER.error("[路线分析] 路线图未划分出任何可分析路线，跳过")
            return False

        if not external_info:
            external_info = self.pj_config.getKey("externals.gameInfo", "") or ""
        raw_parallelism = parallelism or self.pj_config.getKey(
            "internals.globalanalysis.routeParallelism", _DEFAULT_PARALLELISM
        )
        try:
            sem_size = max(1, int(raw_parallelism))
        except (TypeError, ValueError):
            sem_size = _DEFAULT_PARALLELISM

        LOGGER.info(
            f"[路线分析] 共 {len(routes)} 条路线"
            f"（{sum(len(fs) for fs in routes.values())} 个文件），"
            f"并行度 {sem_size}，force={force_regen}"
        )
        sem = asyncio.Semaphore(sem_size)

        async def _run(route: str, files: List[str]) -> bool:
            async with sem:
                if not force_regen and shard_up_to_date(self.pj_config, route, files):
                    LOGGER.info(f"[路线分析] 路线「{route}」分片已就绪，跳过")
                    return True
                return await self.analyze_route(
                    compressed_data, route, files, external_info=external_info
                )

        tasks = [asyncio.create_task(_run(r, fs)) for r, fs in routes.items()]
        try:
            results = await asyncio.gather(*tasks)
        except Exception:
            # 任一路线异常（含取消）时取消其余任务后原样上抛，
            # JobCancelledError 不能被吞成单路线失败
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        failed = [r for r, ok in zip(routes.keys(), results) if not ok]
        if failed:
            LOGGER.error(f"[路线分析] {len(failed)} 条路线分析失败：{list(failed)}")
            return False
        LOGGER.info(f"[路线分析] 全部 {len(routes)} 条路线分片就绪")
        return True


if __name__ == "__main__":
    pass
