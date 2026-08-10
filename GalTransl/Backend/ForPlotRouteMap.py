"""生成剧情路线图（PlotRouteMap）后端。

输入：各文件的文件级元数据（FileMetaData）的「角色/剧情/标签」摘要，
     以及用户给定的剧情结构类型（线性/树/有向无环图/有向有环图/混合）与剧情大纲（纯文本）。
输出：PlotRouteMap.json（mermaid 源码 + 文件→路线归属 + 路线→剧情摘要），
     存于 pass0_cache/PlotRouteMap.json，与 GlobalPrompt.json 并列。
"""

import json
import os
from typing import Any, Dict, List, Optional

from GalTransl import LOGGER, PASS0_CACHE_DIR
from GalTransl.Backend.BaseTranslate import BaseTranslate
from GalTransl.Backend.Prompts import FORPLOTROUTE_PROMPT
from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProjectConfig, CProxyPool


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


class ForPlotRouteMap(BaseTranslate):
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
        self.system_prompt = ""
        self.trans_prompt = FORPLOTROUTE_PROMPT
        self._apply_internal_prompt_template_overrides()
        self.init_chatbot(eng_type, config)
        self._global_prompt: Optional[dict] = None
        self._global_prompt_loaded: bool = False

    # 全局提示词上下文（供占位符替换）
    def _ensure_global_prompt_loaded(self) -> None:
        """惰性载入 GlobalPrompt.json（仅执行一次）。"""
        if self._global_prompt_loaded:
            return
        self._global_prompt_loaded = True
        try:
            from GalTransl.Backend.ForGlobalPrompt import load_global_prompt

            self._global_prompt = load_global_prompt(self.pj_config)
        except Exception as e:
            LOGGER.debug(f"[PlotRouteMap] 载入 GlobalPrompt 失败：{e}")
            self._global_prompt = None

    def _build_global_prompt_block(self) -> str:
        """格式化 GlobalPrompt 为提示词附加段落。"""
        self._ensure_global_prompt_loaded()
        if not self._global_prompt:
            return ""
        from GalTransl.Backend.ForGlobalPrompt import _format_global_prompt_as_context

        return _format_global_prompt_as_context(self._global_prompt)

    # 构建输入：各文件剧情摘要
    def _build_file_summaries(self) -> str:
        """从 FileMetaData 读取各文件「角色/剧情/标签」，拼成输入清单。"""
        from GalTransl.Backend.ForGalJsonMulitChat import load_file_metadata_map

        fm_map = load_file_metadata_map(self.pj_config)
        if not fm_map:
            LOGGER.warning("[PlotRouteMap] 未读取到任何文件级元数据，无法生成路线图")
            return ""
        lines: List[str] = []
        for fid, meta in sorted(fm_map.items()):
            if not hasattr(meta, "character"):
                continue
            roles = getattr(meta, "character", None) or []
            roles_str = "、".join(str(r) for r in roles) if roles else ""
            plot = str(getattr(meta, "plot", "") or "")
            tags = getattr(meta, "tags", None) or []
            tags_str = "、".join(str(t) for t in tags) if tags else ""
            lines.append(
                f"文件 {fid}：\n"
                f"- 角色：{roles_str}\n"
                f"- 剧情：{plot}\n"
                f"- 标签：{tags_str}"
            )
        return "\n".join(lines)

    # 解析与规整 LLM 返回
    @staticmethod
    def _parse_result(text: str) -> Optional[dict]:
        """提取 LLM 返回的 JSON 对象。"""
        if not text or not text.strip():
            return None
        if "</think>" in text:
            text = text.split("</think>")[-1]
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            LOGGER.warning(f"[PlotRouteMap] LLM 返回中未找到 JSON 对象：{text[:200]}")
            return None
        try:
            return json.loads(text[start : end + 1])
        except Exception as e:
            LOGGER.warning(f"[PlotRouteMap] JSON 解析失败：{e}，原文前 200 字：{text[:200]}")
            return None

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
    def _validate_mermaid(source: str) -> bool:
        """简单校验 mermaid 源码是否形如图声明（flowchart/graph）而非纯文本。"""
        if not source:
            return False
        first = next((l for l in source.splitlines() if l.strip()), "")
        head = first.strip().lower()
        return head.startswith("flowchart") or head.startswith("graph")

    def _check_file_coverage(self, data: Dict[str, Any]) -> None:
        """校验「文件归属」是否覆盖全部输入文件；缺失时输出 warning（不阻断）。"""
        from GalTransl.Backend.ForGalJsonMulitChat import load_file_metadata_map

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

    # 入口
    async def batch_translate(
        self,
        structure_type: str = "",
        user_outline: str = "",
        force_regen: bool = False,
    ) -> bool:
        """生成剧情路线图（单次 LLM 调用）。

        Args:
            structure_type: 剧情结构类型（线性/树/有向无环图/有向有环图/混合）。
            user_outline: 用户给出的剧情大纲（纯文本，可空）。
            force_regen: 是否忽略已有 PlotRouteMap.json 强制重新生成。

        Returns:
            bool: 是否成功生成。
        """
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
        prompt = prompt.replace("[global_prompt]", self._build_global_prompt_block())

        LOGGER.info("[PlotRouteMap] 正在生成剧情路线图…")
        LOGGER.debug(f"[PlotRouteMap] 提示词长度：{len(prompt)} 字符")

        # LLM 请求→解析→规整→校验，最多 2 次（首次 + 输出格式不合格重试 1 次）
        retry_hint = (
            "\n\n【格式纠正】上次输出不符合要求（JSON 不合法或 mermaid 语法错误）。"
            "请仅输出一个 JSON 对象，包含 mermaid、文件归属、节点剧情 三个字段，"
            "且 mermaid 以 flowchart 或 graph 开头。"
        )
        for attempt in (1, 2):
            try:
                messages = [{"role": "user", "content": prompt}]
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
