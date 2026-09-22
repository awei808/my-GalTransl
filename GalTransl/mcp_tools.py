"""MCP 工具层：面向外部 agent 的只读能力（0.5.1）。

11 个工具，全部为纯函数风格：显式接收 project_dir，仅读磁盘，返回可 JSON 序列化的 dict。
本模块不依赖任何 MCP 传输实现，供独立 stdio server（run_mcp_server.py）与未来内置 agent 共用，
避免上游「工具全走 HTTP 打自家 REST」的架构绕路。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

from GalTransl import (
    CACHE_FOLDERNAME,
    INPUT_FOLDERNAME,
    LOGGER,
    PASS0_CACHE_DIR,
    PASS1_CACHE_DIR,
    PASS2_CACHE_DIR,
)
from GalTransl.ConfigHelper import detect_config_file
from GalTransl.Frontend.pipeline_stages import to_payload as _pipeline_stages_payload
from GalTransl.backend_security import safe_under_project
from GalTransl.server_config_schema import _read_yaml_file
from GalTransl.server_meta import _list_problem_types, _load_project_name_dict
from GalTransl.server_scaffold import _workspace_root
from GalTransl.server_search import (
    DEFAULT_MAX_RESULTS,
    search_cache_entries,
    search_dict_entries,
    search_source_scripts,
)

DEFAULT_PAGE_SIZE = 100
HARD_PAGE_SIZE = 1000
_LOG_SOURCES = ("engine", "frontend")


# ---------- 公共辅助 ----------

def _require_project_dir(arguments: Dict[str, Any]) -> str:
    """取出并校验 project_dir 参数。"""
    raw = str(arguments.get("project_dir", "") or "").strip()
    if not raw:
        raise ValueError("project_dir is required")
    if not os.path.isdir(raw):
        raise ValueError(f"project_dir 不存在或不是目录: {raw}")
    return os.path.normpath(raw)


def _clamp_page(value: Any, default: int, hard: int) -> int:
    """分页参数收敛：非数值回落默认，负数取 0，超上限截断。"""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 0:
        return 0
    return min(parsed, hard)


def _paginate(entries: List[Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
    """按 offset/limit 对条目列表分页。"""
    total = len(entries)
    offset = _clamp_page(arguments.get("offset", 0), 0, total)
    limit = _clamp_page(arguments.get("limit", DEFAULT_PAGE_SIZE), DEFAULT_PAGE_SIZE, HARD_PAGE_SIZE)
    page = entries[offset:offset + limit] if limit > 0 else []
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "returned": len(page),
        "has_more": offset + len(page) < total,
    }


def _read_json_file(file_path: str) -> Any:
    """读取 JSON 文件（失败抛异常，由调用方决定是否容错）。"""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_project_file(project_dir: str, sub_dir: str, filename: str) -> str:
    """把项目内相对文件名解析为绝对路径，拒绝越界与路径穿越。"""
    if not filename:
        raise ValueError("filename is required")
    base_dir = os.path.join(project_dir, sub_dir)
    # safe_under_project 拒绝绝对路径并以 commonpath 校验归属，杜绝穿越
    return safe_under_project(base_dir, filename)


# ---------- 搜索域（6） ----------

def _tool_search_cache(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return search_cache_entries(
        _require_project_dir(arguments),
        arguments.get("query", ""),
        field=str(arguments.get("field", "all") or "all").strip(),
        use_regex=bool(arguments.get("regex", False)),
        max_results=arguments.get("max_results", DEFAULT_MAX_RESULTS),
    )


def _tool_search_scripts(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return search_source_scripts(
        _require_project_dir(arguments),
        arguments.get("query", ""),
        use_regex=bool(arguments.get("regex", False)),
        max_results=arguments.get("max_results", DEFAULT_MAX_RESULTS),
    )


def _tool_search_dict(arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_dir = _require_project_dir(arguments)
    return search_dict_entries(
        project_dir,
        arguments.get("query", ""),
        direction=str(arguments.get("direction", "any") or "any").strip(),
        use_regex=bool(arguments.get("regex", False)),
        config_name=detect_config_file(project_dir),
        max_results=arguments.get("max_results", DEFAULT_MAX_RESULTS),
        include_common=bool(arguments.get("include_common", True)),
    )


def _tool_lookup_name(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """按译名表查询原文名对应的译名，未收录时 found 为 False。"""
    project_dir = _require_project_dir(arguments)
    raw = arguments.get("name", "")
    if raw in ("", None, []):
        raise ValueError("name is required")
    queries = [str(item) for item in raw] if isinstance(raw, list) else [str(raw)]
    name_dict = _load_project_name_dict(project_dir)
    results = []
    for source in queries:
        target = name_dict.get(source)
        results.append({"source": source, "target": target, "found": target is not None})
    return {
        "project_dir": project_dir,
        "name_dict_entries": len(name_dict),
        "results": results,
    }


def _tool_search_logs(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """按关键词检索项目日志（engine=GalTransl.log，frontend=frontend.log）。"""
    project_dir = _require_project_dir(arguments)
    keyword = str(arguments.get("keyword", "") or "").strip()
    if not keyword:
        raise ValueError("keyword is required")
    source = str(arguments.get("source", "engine") or "engine").strip()
    if source not in _LOG_SOURCES:
        raise ValueError(f"unsupported log source: {source}")
    try:
        tail = int(arguments.get("tail", 2000))
    except (TypeError, ValueError):
        raise ValueError(f"invalid tail: {arguments.get('tail')!r}")
    if tail < 0:
        raise ValueError("tail must be non-negative")
    max_results = _clamp_page(arguments.get("max_results", DEFAULT_MAX_RESULTS), DEFAULT_MAX_RESULTS, 2000)

    if source == "engine":
        log_path = os.path.join(project_dir, "GalTransl.log")
    else:
        # 与 GET /logs 一致：优先项目内 frontend.log，回退全局
        project_log = os.path.join(project_dir, "frontend.log")
        log_path = project_log if os.path.isfile(project_log) else os.path.join(_workspace_root(), "frontend.log")
    if not os.path.isfile(log_path):
        return {"source": source, "exists": False, "path": log_path, "results": [], "total": 0, "truncated": False}

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    window_start = max(0, len(lines) - tail) if tail > 0 else len(lines)
    window = lines[window_start:] if tail > 0 else []
    results = []
    total = 0
    lowered = keyword.lower()
    for offset, line in enumerate(window):
        if lowered not in line.lower():
            continue
        total += 1
        if len(results) < max_results:
            # 行号按文件绝对行号给出，便于直接定位日志
            results.append({"line_no": window_start + offset + 1, "text": line})
    return {
        "source": source,
        "exists": True,
        "path": log_path,
        "total_lines": len(lines),
        "scanned_lines": len(window),
        "total": total,
        "truncated": total > len(results),
        "results": results,
    }


def _tool_list_problems(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """列出带问题标记的缓存条目，可按问题类型关键词过滤。"""
    project_dir = _require_project_dir(arguments)
    problem_type = str(arguments.get("problem_type", "") or "").strip()
    # 复用缓存检索：problem 字段正则；未指定类型时用 ".+" 命中所有非空 problem
    result = search_cache_entries(
        project_dir,
        problem_type if problem_type else ".+",
        field="problem",
        use_regex=True,
        max_results=arguments.get("max_results", DEFAULT_MAX_RESULTS),
    )
    return {
        "project_dir": project_dir,
        "problem_type": problem_type,
        "available_problem_types": _list_problem_types(),
        "total": result["total"],
        "truncated": result["truncated"],
        "results": result["results"],
    }


# ---------- 只读域（5） ----------

def _tool_list_projects(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """列出工作区根下疑似 GalTransl 项目的目录（判定依据：存在配置文件）。"""
    workspace_root = os.path.normpath(str(arguments.get("workspace_root", "") or "").strip() or _workspace_root())
    if not os.path.isdir(workspace_root):
        return {"workspace_root": workspace_root, "exists": False, "projects": []}
    projects = []
    for name in sorted(os.listdir(workspace_root)):
        path = os.path.join(workspace_root, name)
        if not os.path.isdir(path):
            continue
        config_name = detect_config_file(path)
        # detect_config_file 找不到也回退 config.yaml，故必须再确认文件真实存在
        if not os.path.isfile(os.path.join(path, config_name)):
            continue
        projects.append({
            "name": name,
            "project_dir": path,
            "config_file_name": config_name,
            "has_cache": os.path.isdir(os.path.join(path, CACHE_FOLDERNAME)),
            "has_input": os.path.isdir(os.path.join(path, INPUT_FOLDERNAME)),
        })
    return {"workspace_root": workspace_root, "exists": True, "total": len(projects), "projects": projects}


def _tool_get_project_overview(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """项目概览：配置要点 + 文件统计 + 流水线阶段清单。"""
    project_dir = _require_project_dir(arguments)
    config_name = detect_config_file(project_dir)
    config_path = os.path.join(project_dir, config_name)
    config: Dict[str, Any] = {}
    config_error = ""
    if os.path.isfile(config_path):
        try:
            loaded = _read_yaml_file(config_path)
            config = loaded if isinstance(loaded, dict) else {}
        except Exception as exc:
            config_error = str(exc)

    cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
    input_dir = os.path.join(project_dir, INPUT_FOLDERNAME)
    # 配置口径以真实项目为准：common 段承载 language / workersPerProject / gpt.* 扁平键
    common = config.get("common") if isinstance(config.get("common"), dict) else {}
    internals = config.get("internals") if isinstance(config.get("internals"), dict) else {}
    stage_backends = common.get("stageBackends") if isinstance(common.get("stageBackends"), dict) else {}
    pipeline_internals = internals.get("pipeline") if isinstance(internals.get("pipeline"), dict) else {}
    return {
        "project_dir": project_dir,
        "config_file_name": config_name,
        "config_exists": os.path.isfile(config_path),
        "config_error": config_error,
        "target_language": common.get("language", ""),
        "workers_per_project": common.get("workersPerProject", 0),
        "auto_adjust_workers": common.get("autoAdjustWorkers", False),
        "num_per_request_translate": common.get("gpt.numPerRequestTranslate", 0),
        "context_num": common.get("gpt.contextNum", 0),
        "skip_h": common.get("skipH", False),
        "translation_guideline": common.get("gpt.translation_guideline", ""),
        "stage_backends": stage_backends,
        "pipeline_internals": pipeline_internals,
        "script_files": len(_list_files(input_dir, ".json")) if os.path.isdir(input_dir) else 0,
        "cache_files": len(_list_files(cache_dir, ".json")) if os.path.isdir(cache_dir) else 0,
        "has_name_dict": os.path.isfile(os.path.join(project_dir, "name替换表.csv"))
        or os.path.isfile(os.path.join(project_dir, "name替换表.xlsx")),
        "pipeline_stages": _pipeline_stages_payload(),
    }


def _list_files(base_dir: str, suffix: str) -> List[str]:
    """递归列出目录下指定后缀的文件（相对路径，'/' 分隔）。"""
    found: List[str] = []
    for root, _dirs, names in os.walk(base_dir):
        for name in sorted(names):
            if name.endswith(suffix):
                found.append(os.path.relpath(os.path.join(root, name), base_dir).replace("\\", "/"))
    return found


def _read_entries_tool(arguments: Dict[str, Any], sub_dir: str) -> Dict[str, Any]:
    """读取项目内某 JSON 条目文件并分页（译缓存 / 原始脚本共用）。"""
    project_dir = _require_project_dir(arguments)
    filename = str(arguments.get("filename", "") or "").strip()
    file_path = _resolve_project_file(project_dir, sub_dir, filename)
    if not os.path.isfile(file_path):
        raise ValueError(f"文件不存在: {sub_dir}/{filename}")
    data = _read_json_file(file_path)
    entries = data if isinstance(data, list) else []
    page_info = _paginate(entries, arguments)
    offset, limit = page_info["offset"], page_info["limit"]
    return {
        "project_dir": project_dir,
        "filename": filename,
        "sub_dir": sub_dir,
        **page_info,
        "items": entries[offset:offset + limit] if limit > 0 else [],
    }


def _tool_read_translation_file(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return _read_entries_tool(arguments, CACHE_FOLDERNAME)


def _tool_read_source_script(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return _read_entries_tool(arguments, INPUT_FOLDERNAME)


def _tool_get_project_metadata(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """读取三档元数据：globalprompt / filemeta / batchmeta。"""
    project_dir = _require_project_dir(arguments)
    kind = str(arguments.get("kind", "globalprompt") or "globalprompt").strip()
    if kind not in ("globalprompt", "filemeta", "batchmeta", "all"):
        raise ValueError(f"unsupported metadata kind: {kind}")

    def _load(path: str) -> Dict[str, Any]:
        if not os.path.isfile(path):
            return {"exists": False, "path": path, "entry": None}
        try:
            return {"exists": True, "path": path, "entry": _read_json_file(path)}
        except Exception as exc:
            return {"exists": False, "path": path, "entry": None, "error": str(exc)}

    result: Dict[str, Any] = {"project_dir": project_dir, "kind": kind}
    if kind in ("globalprompt", "all"):
        result["globalprompt"] = _load(
            os.path.join(project_dir, CACHE_FOLDERNAME, PASS0_CACHE_DIR, "GlobalPrompt.json")
        )
    if kind == "all":
        pass1_dir = os.path.join(project_dir, CACHE_FOLDERNAME, PASS1_CACHE_DIR)
        pass2_dir = os.path.join(project_dir, CACHE_FOLDERNAME, PASS2_CACHE_DIR)
        result["filemeta_files"] = _list_files(pass1_dir, ".meta.json") if os.path.isdir(pass1_dir) else []
        result["batchmeta_files"] = _list_files(pass2_dir, ".batch.json") if os.path.isdir(pass2_dir) else []
    if kind == "filemeta":
        filename = str(arguments.get("filename", "") or "").strip()
        if not filename:
            raise ValueError("filename is required for kind=filemeta")
        result["filemeta"] = _load(
            os.path.join(project_dir, CACHE_FOLDERNAME, PASS1_CACHE_DIR, f"{filename}.meta.json")
        )
    if kind == "batchmeta":
        filename = str(arguments.get("filename", "") or "").strip()
        if not filename:
            raise ValueError("filename is required for kind=batchmeta")
        result["batchmeta"] = _load(
            os.path.join(project_dir, CACHE_FOLDERNAME, PASS2_CACHE_DIR, f"{filename}.batch.json")
        )
    return result


# ---------- 工具定义与分发 ----------

def _def(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    """构造单条 MCP 工具定义（字段名对齐 MCP Tool：name / description / inputSchema）。"""
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties, "required": required},
        "kind": "read",
    }


_PROJECT_PROP = {"type": "string", "description": "项目根目录绝对路径"}
_QUERY_PROP = {"type": "string", "description": "检索词"}
_REGEX_PROP = {"type": "boolean", "description": "是否按正则解释检索词，默认 false"}
_MAX_RESULTS_PROP = {"type": "integer", "description": f"返回条数上限，默认 {DEFAULT_MAX_RESULTS}"}
_PAGE_PROPS = {
    "offset": {"type": "integer", "description": "起始下标，默认 0"},
    "limit": {"type": "integer", "description": f"返回条数，默认 {DEFAULT_PAGE_SIZE}"},
}

MCP_TOOL_DEFS: List[Dict[str, Any]] = [
    _def(
        "galtransl_search_cache",
        "检索项目翻译缓存（已入库的原文/译文/问题/说话人）。用于核查某个术语在现有译文中的用法是否一致。",
        {
            "project_dir": _PROJECT_PROP,
            "query": _QUERY_PROP,
            "field": {"type": "string", "enum": ["all", "src", "dst", "problem"], "description": "检索字段，默认 all"},
            "regex": _REGEX_PROP,
            "max_results": _MAX_RESULTS_PROP,
        },
        ["project_dir", "query"],
    ),
    _def(
        "galtransl_search_scripts",
        "检索原始脚本文本（gt_input 下的 name-message JSON），不依赖是否已生成翻译缓存。",
        {
            "project_dir": _PROJECT_PROP,
            "query": _QUERY_PROP,
            "regex": _REGEX_PROP,
            "max_results": _MAX_RESULTS_PROP,
        },
        ["project_dir", "query"],
    ),
    _def(
        "galtransl_search_dict",
        "检索字典词条（项目字典 + 公共 Dict），返回原文与译名。用于确认术语是否已条目化、译法是否统一。",
        {
            "project_dir": _PROJECT_PROP,
            "query": _QUERY_PROP,
            "direction": {
                "type": "string",
                "enum": ["any", "jp2zh", "zh2jp"],
                "description": "any=双向，jp2zh=只匹配原文列，zh2jp=只匹配译文列",
            },
            "regex": _REGEX_PROP,
            "include_common": {"type": "boolean", "description": "是否一并检索公共 Dict，默认 true"},
            "max_results": _MAX_RESULTS_PROP,
        },
        ["project_dir", "query"],
    ),
    _def(
        "galtransl_lookup_name",
        "查询角色名译名表（name替换表.csv/xlsx）：给出原文名，返回对应译名与是否收录。",
        {
            "project_dir": _PROJECT_PROP,
            "name": {"type": "string", "description": "原文名，可传单个字符串或字符串数组"},
        },
        ["project_dir", "name"],
    ),
    _def(
        "galtransl_search_logs",
        "按关键词检索项目日志，返回绝对行号便于定位。",
        {
            "project_dir": _PROJECT_PROP,
            "keyword": {"type": "string", "description": "检索关键词"},
            "source": {"type": "string", "enum": ["engine", "frontend"], "description": "engine=GalTransl.log，frontend=frontend.log"},
            "tail": {"type": "integer", "description": "只扫描最后 N 行，默认 2000"},
            "max_results": _MAX_RESULTS_PROP,
        },
        ["project_dir", "keyword"],
    ),
    _def(
        "galtransl_list_problems",
        "列出带问题标记的缓存条目（可指定问题类型），用于定位需要修复的译文。",
        {
            "project_dir": _PROJECT_PROP,
            "problem_type": {"type": "string", "description": "问题类型关键词，留空则列出全部有问题的条目"},
            "max_results": _MAX_RESULTS_PROP,
        },
        ["project_dir"],
    ),
    _def(
        "galtransl_list_projects",
        "列出工作区根目录下可识别的 GalTransl 项目（含项目绝对路径，供其它工具使用）。",
        {
            "workspace_root": {"type": "string", "description": "工作区根目录，默认取服务端配置"},
        },
        [],
    ),
    _def(
        "galtransl_get_project_overview",
        "获取项目概览：配置要点、脚本/缓存文件数、流水线阶段清单。建议在检索前先调用以了解项目结构。",
        {"project_dir": _PROJECT_PROP},
        ["project_dir"],
    ),
    _def(
        "galtransl_read_translation_file",
        "读取某个翻译缓存文件的条目（分页），用于查看上下文与译文细节。",
        {
            "project_dir": _PROJECT_PROP,
            "filename": {"type": "string", "description": "相对 transl_cache 的缓存文件名"},
            **_PAGE_PROPS,
        },
        ["project_dir", "filename"],
    ),
    _def(
        "galtransl_read_source_script",
        "读取某个原始脚本文件的条目（分页），用于查看原文上下文。",
        {
            "project_dir": _PROJECT_PROP,
            "filename": {"type": "string", "description": "相对 gt_input 的脚本文件名"},
            **_PAGE_PROPS,
        },
        ["project_dir", "filename"],
    ),
    _def(
        "galtransl_get_project_metadata",
        "读取元数据：kind=globalprompt（全局分析）/ filemeta（文件元数据，需 filename）/ "
        "batchmeta（批次元数据，需 filename）/ all（含可用文件名清单）。",
        {
            "project_dir": _PROJECT_PROP,
            "kind": {
                "type": "string",
                "enum": ["globalprompt", "filemeta", "batchmeta", "all"],
                "description": "要读取的元数据种类，默认 globalprompt",
            },
            "filename": {"type": "string", "description": "kind=filemeta/batchmeta 时必填"},
        },
        ["project_dir"],
    ),
]

_TOOL_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "galtransl_search_cache": _tool_search_cache,
    "galtransl_search_scripts": _tool_search_scripts,
    "galtransl_search_dict": _tool_search_dict,
    "galtransl_lookup_name": _tool_lookup_name,
    "galtransl_search_logs": _tool_search_logs,
    "galtransl_list_problems": _tool_list_problems,
    "galtransl_list_projects": _tool_list_projects,
    "galtransl_get_project_overview": _tool_get_project_overview,
    "galtransl_read_translation_file": _tool_read_translation_file,
    "galtransl_read_source_script": _tool_read_source_script,
    "galtransl_get_project_metadata": _tool_get_project_metadata,
}


def call_mcp_tool(name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """按工具名分发到实现；未知工具抛 KeyError，参数错误抛 ValueError。

    返回值为可直接 JSON 序列化的 dict，调用方（stdio server）负责包装成 MCP 结果。
    """
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        raise KeyError(f"unknown tool: {name}")
    args = arguments if isinstance(arguments, dict) else {}
    started = time.time()
    result = handler(args)
    LOGGER.info(f"[mcp] {name} 完成（{time.time() - started:.2f}s）")
    return result
