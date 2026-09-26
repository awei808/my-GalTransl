"""路线图工作台简易 agent：工具调用循环与 HTTP 处理体。

AI 只持 3 个工具：读路线图 / 写路线图（整体覆盖、校验后原子落盘）/ 查文件元数据。
翻译后端的执行不经 AI——前端直接提交 /api/jobs，本模块不提供任何执行类工具。

LLM 解析复用 server_backend._resolve_suggest_backend（profile_data → profile 名 →
项目 backendSpecific → 首个全局 profile）；互斥口径与 /review/ai-suggest 一致：
翻译任务运行中 409，同项目 agent 会话单飞。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List

from openai import OpenAI

from GalTransl import CACHE_FOLDERNAME, LOGGER, PASS0_CACHE_DIR
from GalTransl.Backend.Prompts import AGENT_SYSTEM_PROMPT
from GalTransl.server_backend import _SuggestConfigError, _resolve_suggest_backend

AGENT_MAX_ROUNDS = 6
AGENT_REQUEST_TIMEOUT = 120.0
# 工具结果回传给模型的大小上限（超出截断，防上下文膨胀）
_AGENT_RESULT_MAX_CHARS = 12000

# 同项目 agent 会话单飞（进程内，归一口径与 JobRegistry 一致：Path.resolve）
_AGENT_BUSY: set = set()
_AGENT_BUSY_LOCK = threading.Lock()


def is_agent_busy(project_dir: str) -> bool:
    """判断该项目是否正有 agent 会话进行中（供 JobRegistry.submit 反向互斥）。"""
    norm = str(Path(project_dir).resolve())
    with _AGENT_BUSY_LOCK:
        return norm in _AGENT_BUSY

AGENT_TOOLS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_route_map",
            "description": "读取当前项目的剧情路线图完整内容（mermaid 源码、文件归属、节点剧情、结构类型、用户大纲）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_route_map",
            "description": (
                "整体覆盖写入剧情路线图。必须先 read_route_map 获取现状，再在现状基础上修改；"
                "未被要求修改的字段（结构类型/用户大纲/文件归属/节点剧情）必须原样带回。"
                "mermaid 节点的显示文本必须是文件名。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "结构类型": {"type": "string", "description": "结构类型（如 线性/树/有向无环图/混合），不改则保持原值"},
                    "用户大纲": {"type": "string", "description": "用户大纲原文，不改则保持原值"},
                    "mermaid": {"type": "string", "description": "完整 mermaid 源码，首行须以 flowchart 或 graph 开头"},
                    "文件归属": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "文件名 -> 路线名",
                    },
                    "节点剧情": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "路线名 -> 剧情摘要",
                    },
                },
                "required": ["mermaid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_file_metadata",
            "description": "按关键词检索各文件的元数据（角色/剧情/标签/视角/氛围/用词色彩等），返回命中的文件名与摘要。用于找出涉及某段剧情/角色的文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词（字面子串，不区分大小写）"},
                    "scope": {
                        "type": "string",
                        "enum": ["filemeta", "batchmeta", "all"],
                        "description": "检索范围：filemeta=文件级，batchmeta=批次级，all=全部（默认）",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


def _route_map_path(project_dir: str) -> str:
    return os.path.join(project_dir, CACHE_FOLDERNAME, PASS0_CACHE_DIR, "PlotRouteMap.json")


def _tool_read_route_map(project_dir: str, args: dict) -> dict:
    """读取 PlotRouteMap.json；不存在返回 exists=False（不视为错误）。"""
    path = _route_map_path(project_dir)
    if not os.path.isfile(path):
        return {"exists": False, "entry": None}
    try:
        with open(path, "r", encoding="utf-8") as f:
            entry = json.load(f)
    except Exception as exc:
        return {"exists": False, "entry": None, "error": f"读取失败: {exc}"}
    return {"exists": True, "entry": entry if isinstance(entry, dict) else {}}


def _tool_write_route_map(project_dir: str, args: dict) -> dict:
    """整体覆盖写入路线图：未提供字段保留旧值，mermaid 经生成端同口径校验后原子落盘。"""
    from GalTransl.Backend.ForPlotRouteMap import ForPlotRouteMap

    args = args if isinstance(args, dict) else {}
    path = _route_map_path(project_dir)
    old: dict = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                old = loaded
        except Exception:
            old = {}

    outline = args.get("用户大纲")
    merged = {
        "结构类型": str(args.get("结构类型") or old.get("结构类型") or ""),
        "用户大纲": str(outline if outline is not None else old.get("用户大纲") or ""),
        "mermaid": str(args.get("mermaid") or "").strip(),
        "文件归属": args["文件归属"] if isinstance(args.get("文件归属"), dict) else old.get("文件归属") or {},
        "节点剧情": args["节点剧情"] if isinstance(args.get("节点剧情"), dict) else old.get("节点剧情") or {},
    }
    normalized = ForPlotRouteMap._normalize_result(merged)
    if not normalized["mermaid"] and not normalized["文件归属"]:
        raise ValueError("写入被拒绝：mermaid 与 文件归属 不能同时为空")
    if normalized["mermaid"] and not ForPlotRouteMap._validate_mermaid(normalized["mermaid"]):
        raise ValueError(
            "写入被拒绝：mermaid 校验失败。首行必须以 flowchart 或 graph 开头；"
            "subgraph id 只能包含字母/数字/下划线/连字符（可含中文），禁止空格等其它字符。"
        )
    final = {
        "结构类型": merged["结构类型"],
        "用户大纲": merged["用户大纲"],
        "mermaid": normalized["mermaid"],
        "文件归属": normalized["文件归属"],
        "节点剧情": normalized["节点剧情"],
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    LOGGER.info(
        f"[agent] 路线图已更新: mermaid {len(normalized['mermaid'])} 字符，"
        f"文件归属 {len(normalized['文件归属'])} 项，节点剧情 {len(normalized['节点剧情'])} 项"
    )
    return {
        "success": True,
        "mermaid_chars": len(normalized["mermaid"]),
        "file_count": len(normalized["文件归属"]),
        "route_count": len(normalized["节点剧情"]),
    }


def _tool_search_file_metadata(project_dir: str, args: dict) -> dict:
    from GalTransl.server_search import search_metadata

    args = args if isinstance(args, dict) else {}
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query 不能为空")
    return search_metadata(project_dir, query, scope=str(args.get("scope") or "all"), max_results=30)


def _make_tool_dispatch(project_dir: str) -> Callable[[str, dict], dict]:
    def dispatch(name: str, args: dict) -> dict:
        if name == "read_route_map":
            return _tool_read_route_map(project_dir, args)
        if name == "write_route_map":
            return _tool_write_route_map(project_dir, args)
        if name == "search_file_metadata":
            return _tool_search_file_metadata(project_dir, args)
        raise ValueError(f"未知工具: {name}")

    return dispatch


def _truncate_result(result: dict) -> str:
    text = json.dumps(result, ensure_ascii=False)
    if len(text) > _AGENT_RESULT_MAX_CHARS:
        text = text[:_AGENT_RESULT_MAX_CHARS] + "…(结果过长已截断)"
    return text


def run_agent_loop(
    client: Any,
    model: str,
    system_prompt: str,
    user_message: str,
    dispatch: Callable[[str, dict], dict],
    max_rounds: int = AGENT_MAX_ROUNDS,
) -> dict:
    """执行一次 agent 会话的工具调用循环，返回 {"reply", "steps"}。

    模型发起工具调用时执行 dispatch 并把结果以 tool 消息回传，直至给出纯文本
    回复；轮数耗尽仍未回复时以提示文案收尾。工具执行失败会把错误信息回传给
    模型自行修正（不中断会话）。
    """
    messages: List[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    steps: List[dict] = []
    for _ in range(max_rounds):
        resp = client.chat.completions.create(model=model, messages=messages, tools=AGENT_TOOLS)
        choice = resp.choices[0] if getattr(resp, "choices", None) else None
        message = getattr(choice, "message", None) if choice else None
        if message is None:
            break
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            return {"reply": message.content or "", "steps": steps}
        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            }
        )
        for tc in tool_calls:
            name = str(getattr(tc.function, "name", "") or "")
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            try:
                result = dispatch(name, args)
                ok = True
                LOGGER.info(f"[agent] 工具调用 {name} 成功")
            except Exception as exc:
                result = {"error": str(exc)}
                ok = False
                LOGGER.warning(f"[agent] 工具调用 {name} 失败: {exc}")
            steps.append({"tool": name, "args": args, "ok": ok, "result": result})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": _truncate_result(result)})
    return {"reply": "（工具调用轮数达到上限，已停止本次会话；请缩小修改范围后重试）", "steps": steps}


def handle_agent_chat(handler: Any, registry: Any, project_dir: str, payload: dict) -> None:
    """POST /api/projects/:id/agent/chat 的处理体（同步执行，阻塞至会话结束）。"""
    message = str(payload.get("message", "") or "").strip()
    if not message:
        handler._send_json({"error": "message required"}, status=400)
        return
    if registry is not None and registry._has_running_job_for_project(project_dir):
        handler._send_json({"error": "翻译任务运行中，请先停止任务再使用路线图 Agent"}, status=409)
        return
    norm_dir = str(Path(project_dir).resolve())
    with _AGENT_BUSY_LOCK:
        if norm_dir in _AGENT_BUSY:
            handler._send_json({"error": "已有一个路线图 Agent 会话进行中，请稍候"}, status=409)
            return
        _AGENT_BUSY.add(norm_dir)
    try:
        api_key, base_url, model = _resolve_suggest_backend(payload, project_dir)
        # 允许请求显式指定 agent 模型名（与该后端配置的默认模型不同时）
        model_override = str(payload.get("model", "") or "").strip()
        if model_override:
            model = model_override
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=AGENT_REQUEST_TIMEOUT)
        LOGGER.info(f"[agent] 会话开始 model={model} 消息长度={len(message)}")
        result = run_agent_loop(client, model, AGENT_SYSTEM_PROMPT, message, _make_tool_dispatch(project_dir))
        LOGGER.info(f"[agent] 会话结束 工具调用 {len(result['steps'])} 次 回复 {len(result['reply'])} 字")
        handler._send_json({"reply": result["reply"], "steps": result["steps"], "model": model})
    except _SuggestConfigError as exc:
        handler._send_json({"error": str(exc)}, status=400)
    except Exception as exc:
        LOGGER.warning(f"[agent] 会话失败: {exc}")
        handler._send_json({"error": f"路线图 Agent 调用失败: {exc}"}, status=502)
    finally:
        with _AGENT_BUSY_LOCK:
            _AGENT_BUSY.discard(norm_dir)
