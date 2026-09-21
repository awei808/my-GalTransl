"""生成剧情路线图（PlotRouteMap）后端。

输入：各文件的文件级元数据（FileMetaData）的「角色/剧情/标签」摘要，
     以及用户给定的剧情结构类型（线性/树/有向无环图/有向有环图/混合）与剧情大纲（纯文本）。
输出：PlotRouteMap.json（mermaid 源码 + 文件→路线归属 + 路线→剧情摘要），
     存于 pass0_cache/PlotRouteMap.json，与 GlobalPrompt.json 并列。
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

from GalTransl import LOGGER, PASS0_CACHE_DIR
from GalTransl.Backend.BaseEngine import BaseEngine, register_engine
from GalTransl.Backend.Prompts import (
    FORPLOTROUTE_PROMPT,
    FORPLOTROUTE_ROUTES_ONLY_PROMPT,
    FORPLOTROUTE_SYSTEM,
    FORPLOTROUTE_SYSTEM_ROUTES_ONLY,
)
from GalTransl.Backend.utils import extract_json_object
from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProjectConfig, CProxyPool

# 路线图生成模式（internals.plotroute.routeMode）
ROUTE_MODE_FULL = "full"  # 重新归纳路线、生成 mermaid 与文件归属（旧行为）
ROUTE_MODE_ROUTES_ONLY = "routesOnly"  # 保留既有「文件归属」划分，仅逐路线梳理「节点剧情」


def load_plot_route_map(pj_config: CProjectConfig) -> Optional[dict]:
    """从 pass0_cache 读取 PlotRouteMap.json；不存在或非法时返回 None。

    Args:
        pj_config: 项目配置对象。

    Returns:
        dict: 路线图数据；失败返回 None。
    """
    path = os.path.join(pj_config.getCachePath(), PASS0_CACHE_DIR, "PlotRouteMap.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            LOGGER.warning(f"[PlotRouteMap] {path} 内容不是 JSON 对象，忽略")
            return None
        return data
    except Exception as e:
        LOGGER.warning(f"[PlotRouteMap] 读取 {path} 失败：{e}")
        return None


def _get_route_for_file(plot_route_map: Optional[dict], filename: str) -> Optional[str]:
    """查询文件所属路线；未找到返回 None。"""
    if not plot_route_map:
        return None
    file_map = plot_route_map.get("文件归属")
    if not isinstance(file_map, dict):
        return None
    route = file_map.get(filename)
    return route if isinstance(route, str) and route else None


def _format_route_context(plot_route_map: Optional[dict], filename: str) -> str:
    """按当前文件所属路线，返回「只含该路线剧情」的上下文块。

    找不到归属、节点剧情缺失或 mermaid 为空时返回空串（调用方应回退全量剧情）。
    """
    if not plot_route_map:
        return ""
    # mermaid 为空说明路线图未生成成功，下游应回退全量注入
    if (
        not isinstance(plot_route_map.get("mermaid"), str)
        or not plot_route_map["mermaid"].strip()
    ):
        return ""
    route = _get_route_for_file(plot_route_map, filename)
    if not route:
        return ""
    nodes = plot_route_map.get("节点剧情")
    if not isinstance(nodes, dict):
        return ""
    summary = nodes.get(route)
    if not summary:
        return ""
    return f"# 当前文件所属路线「{route}」的剧情\n{summary}\n"


@register_engine("ForPlotRouteMap")
class ForPlotRouteMap(BaseEngine):
    """剧情路线图后端：基于各文件剧情摘要与用户大纲，生成 mermaid 路线图。"""

    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool] = None,
        token_pool: Optional[COpenAITokenPool] = None,
    ) -> None:
        super().__init__(config, eng_type, proxy_pool, token_pool)
        self.pj_config = config
        self.system_prompt = FORPLOTROUTE_SYSTEM
        self.trans_prompt = FORPLOTROUTE_PROMPT
        self._setup_prompts(eng_type, config)
        self._global_prompt: Optional[dict] = None
        self._global_prompt_loaded: bool = False

    # 全局提示词上下文（实现见 GalTransl.Backend.context）
    def _ensure_global_prompt_loaded(self) -> None:
        from GalTransl.Backend.context import ensure_global_prompt_loaded
        ensure_global_prompt_loaded(self, "PlotRouteMap")

    def _build_global_prompt_block(self) -> str:
        from GalTransl.Backend.context import format_global_prompt_only
        return format_global_prompt_only(self, "PlotRouteMap")

    # 构建输入：各文件剧情摘要
    def _iter_file_summaries(self) -> List[tuple]:
        """逐文件返回 (文件名, 摘要文本)；无文件级元数据时返回空列表。"""
        from GalTransl.Backend.metadata import load_file_metadata_map

        fm_map = load_file_metadata_map(self.pj_config)
        if not fm_map:
            return []
        items: List[tuple] = []
        for fid, meta in sorted(fm_map.items()):
            if not hasattr(meta, "character"):
                continue
            roles = getattr(meta, "character", None) or []
            roles_str = "、".join(str(r) for r in roles) if roles else ""
            plot = str(getattr(meta, "plot", "") or "")
            tags = getattr(meta, "tags", None) or []
            tags_str = "、".join(str(t) for t in tags) if tags else ""
            items.append(
                (
                    str(fid),
                    f"文件 {fid}：\n"
                    f"- 角色：{roles_str}\n"
                    f"- 剧情：{plot}\n"
                    f"- 标签：{tags_str}",
                )
            )
        return items

    def _build_file_summaries(self) -> str:
        """从 FileMetaData 读取各文件「角色/剧情/标签」，拼成输入清单。"""
        items = self._iter_file_summaries()
        if not items:
            LOGGER.warning("[PlotRouteMap] 未读取到任何文件级元数据，无法生成路线图")
            return ""
        return "\n".join(text for _, text in items)


    # 解析与规整 LLM 返回
    @staticmethod
    def _parse_result(text: str) -> Optional[dict]:
        """提取 LLM 返回的 JSON 对象（统一走 extract_json_object）。"""
        if not text or not text.strip():
            return None
        obj = extract_json_object(text, tag="PlotRouteMap")
        if obj is None:
            LOGGER.warning(f"[PlotRouteMap] LLM 返回中未找到 JSON 对象：{text[:200]}")
        return obj

    @staticmethod
    def _normalize_result(obj: Dict[str, Any]) -> Dict[str, Any]:
        """规整输出字段：mermaid/文件归属/节点剧情 均为合法类型。"""
        mermaid = str(obj.get("mermaid", "") or "").strip()
        file_map_raw = obj.get("文件归属") or {}
        file_map = {}
        if isinstance(file_map_raw, dict):
            for k, v in file_map_raw.items():
                if isinstance(k, str) and isinstance(v, str) and v.strip():
                    file_map[k] = v.strip()
        nodes_raw = obj.get("节点剧情") or {}
        nodes = {}
        if isinstance(nodes_raw, dict):
            for k, v in nodes_raw.items():
                if isinstance(k, str) and isinstance(v, str) and v.strip():
                    nodes[k] = v.strip()
        return {"mermaid": mermaid, "文件归属": file_map, "节点剧情": nodes}

    @staticmethod
    def _extract_node_plots(obj: Optional[dict]) -> Dict[str, str]:
        """从 LLM 返回对象中提取合法的「节点剧情」映射。

        复用 _normalize_result 的清洗口径（键值均须为非空字符串）。
        """
        if not obj:
            return {}
        raw = obj.get("节点剧情")
        if not isinstance(raw, dict):
            return {}
        return ForPlotRouteMap._normalize_result({"节点剧情": raw})["节点剧情"]

    @staticmethod
    def _validate_mermaid(source: str) -> bool:
        """校验 mermaid 源码：以 flowchart/graph 开头，且 subgraph id 不含非法字符。

        subgraph id 只允许字母/数字/下划线/连字符（含中文），
        禁止 `·`、空白等字符（否则 mermaid 词法解析失败，前端渲染报 Syntax error）。
        """
        if not source:
            return False
        first = next((l for l in source.splitlines() if l.strip()), "")
        head = first.strip().lower()
        if not (head.startswith("flowchart") or head.startswith("graph")):
            return False
        for line in source.splitlines():
            m = re.match(r"^\s*subgraph\s+([^\[\s]+)", line)
            if m and re.search(r"[^\w\-]", m.group(1)):
                LOGGER.warning(
                    f"[PlotRouteMap] subgraph id 含非法字符（将被拒绝）：{m.group(1)!r}"
                )
                return False
        return True

    def _check_file_coverage(self, data: Dict[str, Any]) -> None:
        """校验「文件归属」是否覆盖全部输入文件；缺失时输出 warning（不阻断）。"""
        from GalTransl.Backend.metadata import load_file_metadata_map

        fm_map = load_file_metadata_map(self.pj_config)
        if not fm_map:
            return
        covered = {
            k for k, v in data.get("文件归属", {}).items() if isinstance(v, str) and v
        }
        missing = [fid for fid in sorted(fm_map) if fid not in covered]
        if missing:
            LOGGER.warning(
                f"[PlotRouteMap] 有 {len(missing)} 个文件未纳入路线图，缺失：{missing}"
            )

    @staticmethod
    def _format_route_assignments(file_map: Dict[str, str]) -> str:
        """把既有「文件归属」整理为「路线 -> 文件名清单」的固定约束文本。

        保持 JSON 中的路线出现顺序；空路线名归入「（未归属）」以示区分。
        """
        grouped: Dict[str, List[str]] = {}
        for fid, route in file_map.items():
            key = str(route or "").strip() or "（未归属）"
            grouped.setdefault(key, []).append(str(fid))
        lines: List[str] = []
        for route, files in grouped.items():
            lines.append(f"- 路线「{route}」（{len(files)} 个文件）：{'、'.join(files)}")
        return "\n".join(lines)

    def _check_route_coverage(
        self, nodes: Dict[str, str], file_map: Dict[str, str]
    ) -> None:
        """校验「节点剧情」是否覆盖既有全部路线名；缺失时 warning（不阻断）。"""
        routes = {str(v or "").strip() for v in file_map.values()}
        missing = sorted(r for r in routes if r and r not in nodes)
        if missing:
            LOGGER.warning(
                f"[PlotRouteMap] 有 {len(missing)} 条既有路线未生成节点剧情，"
                f"缺失：{missing}"
            )

    # 写入 PlotRouteMap.json
    def _save_plot_route_map(
        self, data: Dict[str, Any], structure_type: str, user_outline: str
    ) -> bool:
        out_dir = os.path.join(self.pj_config.getCachePath(), PASS0_CACHE_DIR)
        os.makedirs(out_dir, exist_ok=True)
        payload = {
            "结构类型": structure_type,
            "用户大纲": user_outline,
            "mermaid": data.get("mermaid", ""),
            "文件归属": data.get("文件归属", {}),
            "节点剧情": data.get("节点剧情", {}),
        }
        path = os.path.join(out_dir, "PlotRouteMap.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            LOGGER.info(f"[PlotRouteMap] 已保存 {path}")
            return True
        except Exception as e:
            LOGGER.error(f"[PlotRouteMap] 写入 {path} 失败：{e}")
            return False

    # routesOnly 模式：保留用户拓扑，仅逐路线重新梳理「节点剧情」
    async def _refresh_node_plots(
        self, structure_type: str, user_outline: str
    ) -> bool:
        """保留既有「文件归属」与 mermaid，仅逐路线重新梳理「节点剧情」。

        无既有路线图 / 无「文件归属」/ 既有 mermaid 不合法时告警并返回 False
        —— 绝不退化为重新划分结构，以免抹掉用户已划好的路线拓扑。
        """
        existing = load_plot_route_map(self.pj_config)
        file_map = (existing or {}).get("文件归属")
        if not isinstance(file_map, dict) or not file_map:
            LOGGER.warning(
                "[PlotRouteMap] routesOnly 模式需要已有 PlotRouteMap.json 的"
                "「文件归属」，未找到，跳过（不重新划分结构）"
            )
            return False
        mermaid = str((existing or {}).get("mermaid", "") or "").strip()
        if not self._validate_mermaid(mermaid):
            LOGGER.warning(
                "[PlotRouteMap] 既有 mermaid 未通过格式校验，routesOnly 模式跳过"
            )
            return False
        summaries = self._build_file_summaries()
        if not summaries:
            return False

        prompt = FORPLOTROUTE_ROUTES_ONLY_PROMPT
        prompt = prompt.replace(
            "[route_assignments]", self._format_route_assignments(file_map)
        )
        prompt = prompt.replace("[file_summaries]", summaries)
        # 全局分析块可经 internals.promptBlocks.globalPrompt 关闭（默认为 True）
        global_prompt_block = (
            self._build_global_prompt_block()
            if self._prompt_block_toggles()["globalPrompt"]
            else ""
        )
        prompt = prompt.replace("[global_prompt]", global_prompt_block)

        LOGGER.info(
            f"[PlotRouteMap] routesOnly：按既有 {len(file_map)} 个文件归属"
            "重新梳理节点剧情…"
        )
        retry_hint = (
            "\n\n【格式纠正】上次输出不符合要求（JSON 不合法或缺少「节点剧情」）。"
            "请仅输出一个 JSON 对象，只含 节点剧情 字段，"
            "且键必须与给定的路线名完全一致。"
        )
        for attempt in (1, 2):
            try:
                messages = [
                    {"role": "system", "content": FORPLOTROUTE_SYSTEM_ROUTES_ONLY},
                    {"role": "user", "content": prompt},
                ]
                rsp, token = await self.ask_chatbot(
                    messages=messages,
                    file_name="PlotRouteMap",
                    max_retry_count=3,
                )
            except Exception as e:
                LOGGER.error(
                    f"[PlotRouteMap] routesOnly LLM 请求失败：{type(e).__name__}: {e}",
                    exc_info=True,
                )
                self._record_runtime_error(
                    kind="llm",
                    message=f"{type(e).__name__}: {e}",
                    filename="PlotRouteMap",
                    index_range="-",
                    level="error",
                )
                return False

            nodes = self._extract_node_plots(self._parse_result(rsp or ""))
            if nodes:
                self._check_route_coverage(nodes, file_map)
                # mermaid 与 文件归属 原样保留（用户拓扑权威），只替换 节点剧情
                merged = {
                    "mermaid": mermaid,
                    "文件归属": dict(file_map),
                    "节点剧情": nodes,
                }
                return self._save_plot_route_map(merged, structure_type, user_outline)

            if attempt == 1:
                LOGGER.warning(
                    "[PlotRouteMap] routesOnly 输出不含有效「节点剧情」，"
                    "追加纠正提示后重试 1 次"
                )
                prompt += retry_hint
                continue
            LOGGER.warning("[PlotRouteMap] routesOnly 输出仍不合格，不保存路线图")
            return False
        return False

    # 入口
    async def batch_translate(
        self,
        structure_type: str = "",
        user_outline: str = "",
        force_regen: bool = False,
        route_mode: str = ROUTE_MODE_FULL,
    ) -> bool:
        """生成剧情路线图（单次 LLM 调用）。

        Args:
            structure_type: 剧情结构类型（线性/树/有向无环图/有向有环图/混合）。
            user_outline: 用户给出的剧情大纲（纯文本，可空）。
            force_regen: 是否忽略已有 PlotRouteMap.json 强制重新生成（仅 full 模式）。
            route_mode: ROUTE_MODE_FULL（默认）重新归纳路线并生成 mermaid；
                ROUTE_MODE_ROUTES_ONLY 保留既有「文件归属」与 mermaid，
                仅逐路线重新梳理「节点剧情」（增量，不重新划分结构）。

        Returns:
            bool: 是否成功生成。
        """
        if route_mode == ROUTE_MODE_ROUTES_ONLY:
            return await self._refresh_node_plots(structure_type, user_outline)

        # 已有产物则跳过（除非 force_regen）
        if not force_regen and load_plot_route_map(self.pj_config):
            LOGGER.info("[PlotRouteMap] 已存在，跳过生成")
            return True

        summaries = self._build_file_summaries()
        if not summaries:
            LOGGER.warning("[PlotRouteMap] 无文件级元数据输入，跳过")
            return False

        prompt = self.trans_prompt
        prompt = prompt.replace("[structure_type]", structure_type or "混合")
        prompt = prompt.replace("[user_outline]", user_outline or "（未提供，请根据各文件剧情自行归纳整体结构）")
        prompt = prompt.replace("[file_summaries]", summaries)
        # 全局分析块可经 internals.promptBlocks.globalPrompt 关闭（默认为 True）
        global_prompt_block = (
            self._build_global_prompt_block()
            if self._prompt_block_toggles()["globalPrompt"]
            else ""
        )
        prompt = prompt.replace("[global_prompt]", global_prompt_block)

        LOGGER.info("[PlotRouteMap] 正在生成剧情路线图…")
        LOGGER.debug(f"[PlotRouteMap] 提示词长度：{len(prompt)} 字符")

        # LLM 请求→解析→规整→校验，最多 2 次（首次 + 输出格式不合格重试 1 次）
        retry_hint = (
            "\n\n【格式纠正】上次输出不符合要求（JSON 不合法、mermaid 语法错误或 subgraph id 非法）。"
            "请仅输出一个 JSON 对象，包含 mermaid、文件归属、节点剧情 三个字段，"
            "且 mermaid 以 flowchart 或 graph 开头。"
            "subgraph 的 id 必须使用英文/数字/下划线，显示名放在方括号内，"
            "例如 subgraph prologue[\"序章\"]；节点 id 同样使用英文。"
        )
        for attempt in (1, 2):
            try:
                messages = [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt},
                ]
                rsp, token = await self.ask_chatbot(
                    messages=messages,
                    file_name="PlotRouteMap",
                    max_retry_count=3,
                )
            except Exception as e:
                LOGGER.error(
                    f"[PlotRouteMap] LLM 请求失败：{type(e).__name__}: {e}",
                    exc_info=True,
                )
                self._record_runtime_error(
                    kind="llm",
                    message=f"{type(e).__name__}: {e}",
                    filename="PlotRouteMap",
                    index_range="-",
                    level="error",
                )
                return False

            obj = self._parse_result(rsp or "")
            if not obj:
                LOGGER.warning("[PlotRouteMap] 未解析到有效 JSON")
            else:
                data = self._normalize_result(obj)
                self._check_file_coverage(data)
                if self._validate_mermaid(data["mermaid"]):
                    return self._save_plot_route_map(data, structure_type, user_outline)
                LOGGER.warning("[PlotRouteMap] mermaid 格式校验失败")

            if attempt == 1:
                LOGGER.warning("[PlotRouteMap] 输出格式不合格，追加纠正提示后重试 1 次")
                prompt += retry_hint
                continue
            LOGGER.warning(
                "[PlotRouteMap] 输出格式仍不合格，不保存路线图（下游将回退全量 GlobalPrompt）"
            )
            return False
        return False
