"""MCP 工具层：面向外部 agent 的能力（0.5.1 只读检索；0.6.0 增写工具与作业域查询）。

11 个检索工具为纯读磁盘的纯函数风格；2 个作业域查询（任务状态/模型探测）与 4 个写工具
（路线图/元数据/提交/停止任务）经 HTTP 回后端（见 mcp_backend_client）。
所有工具以显式 project_dir 为边界，写入范围仅限项目内指定产物，无任意路径写、无命令执行。
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
    PASS3_CACHE_DIR,
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
    """取出并校验 project_dir 参数（只读工具用：仅要求存在且是目录）。

    只读工具**不**要求「合法项目」——指向父目录批量查看、或项目尚未生成配置时
    先看缓存都是合理用法，硬拦会变成破坏性变更。合法性问题改为在返回体里以
    `project_dir_valid` 告警（见 `_attach_project_dir_warning`）。
    """
    raw = str(arguments.get("project_dir", "") or "").strip()
    if not raw:
        raise ValueError("project_dir is required")
    if not os.path.isdir(raw):
        raise ValueError(f"project_dir 不存在或不是目录: {raw}")
    return os.path.normpath(raw)


def validate_project_dir(project_dir: str) -> str:
    """校验目录是「可识别的 GalTransl 项目」，否则抛 ValueError。

    判据沿用 `_tool_list_projects` 的既有口径：`detect_config_file()` 能解析到
    真实存在的 config.inc.yaml / config.yaml。注意 `detect_config_file` 在都找不到时
    会**回退返回 "config.yaml"**，故必须再确认文件真实存在，否则任意空目录都会通过。

    这是写工具的硬边界：没有它，agent 把 project_dir 指向仓库根就能在
    `<repo>/transl_cache/` 下落盘（审查实测过）。

    判据是**文件存在性**而非内容合法性——0 字节或内容损坏的 config.yaml 仍算合法项目
    （fail-open，与 H 门禁同样的取舍）。本函数只负责挡「指向仓库根/系统目录/子目录」，
    不负责校验项目配置是否可用；后者由后续读取配置的代码各自处理，避免「配置损坏的
    项目连修复都做不了」。
    """
    config_name = detect_config_file(project_dir)
    if not os.path.isfile(os.path.join(project_dir, config_name)):
        raise ValueError(
            f"project_dir 不是可识别的 GalTransl 项目（未找到 config.inc.yaml / config.yaml）：{project_dir}。"
            "为避免误写外部目录，写工具只接受含配置文件的翻译项目目录。"
        )
    return config_name


def _require_write_project_dir(arguments: Dict[str, Any]) -> str:
    """写工具专用的 project_dir 校验：存在 + 是目录 + 是可识别项目。"""
    project_dir = _require_project_dir(arguments)
    validate_project_dir(project_dir)
    return project_dir


def _project_dir_warning(project_dir: str) -> Dict[str, Any]:
    """只读工具的合法性告警：非法项目时返回提示字段，合法时返回空 dict。

    只告警不阻断——只读不产生副作用，风险等级低于写入。
    """
    try:
        validate_project_dir(project_dir)
    except ValueError as exc:
        return {"project_dir_valid": False, "project_dir_hint": str(exc)}
    return {"project_dir_valid": True}


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
    # 文件名清单一并返回：read_translation_file / read_source_script 都需要 filename，
    # 概览是 agent 拿到清单的主要入口（count 由清单派生，避免双重遍历）
    script_names = _list_files(input_dir, ".json") if os.path.isdir(input_dir) else []
    cache_names = _list_files(cache_dir, ".json") if os.path.isdir(cache_dir) else []
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
        "script_files": len(script_names),
        "cache_files": len(cache_names),
        "script_file_names": script_names,
        "cache_file_names": cache_names,
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
    """读取元数据：globalprompt / plotroute / filemeta / batchmeta。"""
    project_dir = _require_project_dir(arguments)
    kind = str(arguments.get("kind", "globalprompt") or "globalprompt").strip()
    if kind not in ("globalprompt", "plotroute", "filemeta", "batchmeta", "all"):
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
    if kind in ("plotroute", "all"):
        # 复用 write_route_map 同源的读取（不存在返回 exists=False，不视为错误）
        result["plotroute"] = read_route_map(project_dir)
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


# ---------- 写工具（0.6.0 新增，kind=write） ----------

def _tool_write_route_map(arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_dir = _require_write_project_dir(arguments)
    return write_route_map(project_dir, arguments)


def _tool_save_metadata(arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_dir = _require_write_project_dir(arguments)
    entry = arguments.get("entry")
    if not isinstance(entry, dict):
        raise ValueError("entry 必须是 JSON 对象（要写入的完整元数据）")
    return write_metadata(
        project_dir,
        str(arguments.get("kind", "") or "").strip(),
        str(arguments.get("filename", "") or "").strip(),
        entry,
    )


def _tool_submit_job(arguments: Dict[str, Any]) -> Dict[str, Any]:
    from GalTransl.mcp_backend_client import post_json

    project_dir = _require_write_project_dir(arguments)
    payload: Dict[str, Any] = {
        "project_dir": project_dir,
        "translator": str(arguments.get("translator", "") or "").strip(),
    }
    if not payload["translator"]:
        raise ValueError("translator 必填（如 ForGal-full-pipeline）")
    for key in ("config_file_name", "backend_profile"):
        value = str(arguments.get(key, "") or "").strip()
        if value:
            payload[key] = value
    file_filter = arguments.get("file_filter")
    if file_filter:
        if not isinstance(file_filter, list) or not all(isinstance(f, str) for f in file_filter):
            raise ValueError("file_filter 必须是字符串数组")
        payload["file_filter"] = file_filter
    result = post_json("/api/jobs", payload)
    job_id = str(result.get("job_id", "") or "")
    LOGGER.info(f"[mcp] 已提交翻译任务 job_id={job_id} translator={payload['translator']}")
    return {"success": True, "job_id": job_id, "project_dir": project_dir, "translator": payload["translator"]}


def _tool_stop_job(arguments: Dict[str, Any]) -> Dict[str, Any]:
    from GalTransl.mcp_backend_client import post_json
    from GalTransl.server_runtime import encode_project_dir

    project_dir = _require_write_project_dir(arguments)
    result = post_json(f"/api/projects/{encode_project_dir(project_dir)}/stop", {})
    LOGGER.info(f"[mcp] 已请求停止任务 project={project_dir}")
    return {"success": bool(result.get("success", True)), "project_dir": project_dir, "detail": result}


# 作业状态返回体只取这些字段（to_dict 的全量字段里 file_filter/config_overrides 对 agent 有用，其余裁掉）
_JOB_SUMMARY_FIELDS = (
    "job_id",
    "status",
    "success",
    "translator",
    "config_file_name",
    "file_filter",
    "created_at",
    "started_at",
    "finished_at",
    "error",
)

# runtime 端点响应里的预览/TTFT/逐句事件对 agent 无用且体量大，只透传进度摘要
_RUNTIME_SUMMARY_FIELDS = ("stage", "stage_index", "stage_total", "current_file", "summary")


def _tool_get_job_status(arguments: Dict[str, Any]) -> Dict[str, Any]:
    from GalTransl.mcp_backend_client import get_json
    from GalTransl.server_runtime import _normalize_project_dir, encode_project_dir

    project_dir = _require_project_dir(arguments)
    target = _normalize_project_dir(project_dir)
    jobs_raw = get_json("/api/jobs").get("jobs")
    mine = [
        job
        for job in (jobs_raw if isinstance(jobs_raw, list) else [])
        if isinstance(job, dict) and _normalize_project_dir(str(job.get("project_dir", ""))) == target
    ]
    active = [job for job in mine if job.get("status") in ("pending", "running")]

    def _slim(job: Dict[str, Any]) -> Dict[str, Any]:
        return {key: job.get(key) for key in _JOB_SUMMARY_FIELDS}

    runtime_raw = get_json(f"/api/projects/{encode_project_dir(project_dir)}/runtime")
    runtime = {key: runtime_raw.get(key) for key in _RUNTIME_SUMMARY_FIELDS}
    return {
        "project_dir": project_dir,
        "active_job": _slim(active[0]) if active else None,
        "recent_jobs": [_slim(job) for job in mine[:5]],
        "runtime": runtime,
    }


def _tool_check_model(arguments: Dict[str, Any]) -> Dict[str, Any]:
    from GalTransl.mcp_backend_client import post_json
    from GalTransl.server_runtime import encode_project_dir

    project_dir = _require_project_dir(arguments)
    translator = str(arguments.get("translator", "") or "").strip()
    if not translator:
        raise ValueError("translator 必填（如 ForGal-full-pipeline）")
    config_file_name = (
        str(arguments.get("config_file_name", "") or "").strip() or detect_config_file(project_dir)
    )
    payload: Dict[str, Any] = {"translator": translator, "config_file_name": config_file_name}
    backend_profile = str(arguments.get("backend_profile", "") or "").strip()
    if backend_profile:
        payload["backend_profile"] = backend_profile
    result = post_json(f"/api/projects/{encode_project_dir(project_dir)}/check-model", payload)
    return {"project_dir": project_dir, "config_file_name": config_file_name, "check": result}


# ---------- 路线图读写（供 MCP 写工具与后续 agent 复用） ----------

def route_map_path(project_dir: str) -> str:
    """剧情路线图（PlotRouteMap.json）的绝对路径。"""
    return os.path.join(project_dir, CACHE_FOLDERNAME, PASS0_CACHE_DIR, "PlotRouteMap.json")


def read_route_map(project_dir: str) -> Dict[str, Any]:
    """读取 PlotRouteMap.json；不存在返回 exists=False（不视为错误）。"""
    path = route_map_path(project_dir)
    if not os.path.isfile(path):
        return {"exists": False, "entry": None}
    try:
        with open(path, "r", encoding="utf-8") as f:
            entry = json.load(f)
    except Exception as exc:
        return {"exists": False, "entry": None, "error": f"读取失败: {exc}"}
    return {"exists": True, "entry": entry if isinstance(entry, dict) else {}}


def write_route_map(project_dir: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """整体覆盖写入路线图：未提供字段保留旧值，mermaid 经生成端同口径校验后原子落盘。

    校验口径与 ForPlotRouteMap 生成侧一致（_normalize_result + _validate_mermaid），
    避免 AI 写入手写路线图绕过生成侧约束。空内容与非法 mermaid 一律拒绝。
    """
    from GalTransl.Backend.ForPlotRouteMap import ForPlotRouteMap

    # 自身兜底：本函数是公开入口，直接调用者不应绕过 L3 项目校验
    validate_project_dir(project_dir)
    args = args if isinstance(args, dict) else {}
    path = route_map_path(project_dir)
    old: Dict[str, Any] = {}
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
    # H 门禁：路线图的节点剧情/大纲属剧情摘要，命中 H 词库即拒绝
    # （_normalize_result 只规整 mermaid/文件归属/节点剧情，大纲须取 merged）
    enforce_h_gate(
        project_dir,
        texts=[merged["用户大纲"], *normalized["节点剧情"].values()],
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
        f"[mcp] 路线图已更新: mermaid {len(normalized['mermaid'])} 字符，"
        f"文件归属 {len(normalized['文件归属'])} 项，节点剧情 {len(normalized['节点剧情'])} 项"
    )
    return {
        "success": True,
        "mermaid_chars": len(normalized["mermaid"]),
        "file_count": len(normalized["文件归属"]),
        "route_count": len(normalized["节点剧情"]),
    }


# ---------- H 门禁（写工具硬拦截） ----------

_H_DENY_MESSAGE = (
    "写入被拒绝：目标内容被判定为 H / 成人向。本服务不下发此类内容，"
    "也无法代写。请如实告知用户「该部分因 H 门禁未执行」，由用户在 GalTransl 界面手动处理。"
)


def _entry_has_h(project_dir: str, filename: str) -> bool:
    """判断某个翻译缓存文件是否含 H 区间（复用校对界面同源判定）。

    `_resolve_cache_h_ranges` 要求「pass3_cache 下真实存在的缓存相对路径」
    （如 `pass3_cache/01.json`），裸文件名会一律判为不存在。故这里按
    pass3_cache 下的缓存命名探测：先试 `{filename}.json`，再试分片形态。
    识别不了（缓存/批次元数据缺失、命名对不上）时返回 False——不因判定不了而阻断正常写入。
    """
    candidates = [f"{PASS3_CACHE_DIR}/{filename}.json"]
    # 分片缓存可能形如 {输入名}_{N}.json；无 N 时也试一次裸名，命中由文件存在性决定
    if not filename.endswith(".json"):
        candidates.append(f"{PASS3_CACHE_DIR}/{filename}")
    try:
        from GalTransl.server_cache import _resolve_cache_h_ranges

        for cache_name in candidates:
            if _resolve_cache_h_ranges(project_dir, cache_name).get("has_h"):
                return True
    except Exception as exc:
        LOGGER.debug(f"[mcp] H 区间判定失败，按非 H 处理：{exc}")
    return False


def _h_check_words(project_dir: str) -> list:
    """加载项目「H 场景禁用词库」（配置 forbiddenDictH，回退旧 hCheckDict）。

    走与校对页问题重建完全相同的链路（_load_rebuild_deps），保证门禁口径一致。
    项目无配置/加载失败时返回空列表，此时文本维度不拦截。
    """
    try:
        from GalTransl.server_cache import _load_rebuild_deps

        config_name = "config.yaml"
        for cand in ("config.inc.yaml", "config.yaml"):
            if os.path.isfile(os.path.join(project_dir, cand)):
                config_name = cand
                break
        return _load_rebuild_deps(project_dir, config_name)[5] or []
    except Exception as exc:
        LOGGER.debug(f"[mcp] H 词库加载失败，按非 H 处理：{exc}")
        return []


def _text_has_h(project_dir: str, *texts: str) -> bool:
    """判断若干文本是否命中项目 H 词库（复用 Problem 的命中判定口径）。"""
    candidates = [t for t in texts if t and t.strip()]
    if not candidates:
        return False
    words = _h_check_words(project_dir)
    if not words:
        return False
    try:
        from GalTransl.Problem import _hit_display_words

        return bool(_hit_display_words(words, *candidates))
    except Exception as exc:
        LOGGER.debug(f"[mcp] H 命中判定失败，按非 H 处理：{exc}")
        return False


def enforce_h_gate(project_dir: str, *, cache_filename: str = "", texts: Optional[List[str]] = None) -> None:
    """写工具的 H 硬门禁：命中即抛 ValueError，不落盘。

    两条判据任一成立即拒绝：(1) 目标缓存文件落在 H 区间；(2) 待写入文本命中 H 词库。
    元数据类写入（filemeta/batchmeta）本身不含对话原文，故主要靠文件维度判定。
    """
    if cache_filename and _entry_has_h(project_dir, cache_filename):
        LOGGER.warning(f"[mcp] H 门禁拦截写入: {cache_filename}")
        raise ValueError(_H_DENY_MESSAGE)
    if texts and _text_has_h(project_dir, *texts):
        LOGGER.warning("[mcp] H 门禁拦截写入: 文本命中 H 词库")
        raise ValueError(_H_DENY_MESSAGE)


# ---------- 元数据写入（项目内原子写） ----------

_METADATA_SUBDIRS = {
    "filemeta": (PASS1_CACHE_DIR, ".meta.json"),
    "batchmeta": (PASS2_CACHE_DIR, ".batch.json"),
}


def write_metadata(project_dir: str, kind: str, filename: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    """原子写入单文件元数据；kind 仅支持 filemeta / batchmeta。

    走本模块自带的 .tmp + os.replace 原子写（HTTP 侧 filemeta/batchmeta 端点为直写，
    无原子性），并复用与 HTTP 端点同口径的文件名校验。
    plotroute 必须走 write_route_map（mermaid 同口径校验 + 字段合并），globalprompt
    由流水线生成——两者在此拒绝，避免绕过路线图校验的弱化写路径。
    """
    # 自身兜底：本函数是公开入口，直接调用者不应绕过 L3 项目校验
    validate_project_dir(project_dir)
    if not isinstance(entry, dict):
        raise ValueError("entry 必须是 JSON 对象")
    if kind not in _METADATA_SUBDIRS:
        raise ValueError(f"未知 kind: {kind}（可选 filemeta/batchmeta；剧情路线图请走 write_route_map）")
    name = str(filename or "").strip()
    if not name:
        raise ValueError(f"kind={kind} 时 filename 必填")
    sub_dir, suffix = _METADATA_SUBDIRS[kind]
    _ensure_safe_metadata_filename(name)
    enforce_h_gate(project_dir, cache_filename=name)
    path = os.path.join(project_dir, CACHE_FOLDERNAME, sub_dir, f"{name}{suffix}")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    size = os.path.getsize(path)
    LOGGER.info(f"[mcp] 元数据已写入 kind={kind} ({size} 字节)")
    return {"success": True, "kind": kind, "filename": filename or "", "bytes": size}


_WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def _ensure_safe_metadata_filename(filename: str) -> None:
    """拒绝 . / .. / 含路径分隔符 / 含冒号 / Windows 保留名的文件名。

    比 HTTP 元数据端点（`server_handlers_project` 直拼路径、无校验）更严：MCP 侧
    `filename` 来自外部 agent，属未受信输入。冒号挡 NTFS 交换数据流（`a:b`）；
    保留名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）在 Windows 上会被解析为设备而非文件，
    须在拼路径前拦掉，否则 os.makedirs 会抛非 ValueError 的 OSError 泄漏给调用方。
    """
    norm = os.path.normpath(filename.replace("\\", "/"))
    if norm in (".", "..") or norm.startswith("..") or os.path.isabs(norm):
        raise ValueError(f"非法元数据文件名：{filename}")
    if "/" in norm or "\\" in norm:
        raise ValueError(f"非法元数据文件名（不得含路径分隔符）：{filename}")
    if ":" in norm:
        raise ValueError(f"非法元数据文件名（不得含冒号）：{filename}")
    if norm.split(".")[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"非法元数据文件名（Windows 保留名）：{filename}")


# ---------- 约束下发（instructions / annotations） ----------

SERVER_INSTRUCTIONS = """GalTransl 翻译项目管理服务（17 个工具：13 个只读检索 + 4 个写操作）。所有工具都需提供翻译项目根目录的绝对路径 project_dir。

使用前必须遵守：
1. 只读工具（galtransl_search_* / lookup_name / list_* / get_* / read_* / check_model）不得引发任何写入。
   写工具仅这 4 个：write_route_map、save_metadata、submit_job、stop_job，各自只能改项目内的指定产物；
   本服务不提供任意路径读写、不提供命令执行、不改程序配置。需要其它改动请让用户在 GalTransl 界面操作。
2. submit_job 会真实启动翻译并消耗 API 额度：仅在用户明确要求时调用，调用前先与用户确认项目与引擎，
   可先用 check_model 探测可用性（同样发起真实请求，消耗极小额度）。
3. get_job_status / check_model / submit_job / stop_job 需 GalTransl 后端在运行；其余工具直接读磁盘。
4. 禁止查看 H / 成人向内容：写工具对 H 内容有硬门禁，命中即拒绝。识别到成人向内容必须立即停止该方向检索，
   不得回引原文或译文，只报告位置（文件名 + index）并请用户决定。
5. project_dir 只能是用户明确指定的翻译项目目录。禁止指向 GalTransl 程序目录（其 backend_profiles.yaml 含 API 密钥）、仓库根目录、系统目录或他人目录。
6. 禁止读取或外传任何凭据、密钥、API 端点。日志中命中疑似凭据的行不引用原文。
7. 禁止规模化拉取：搜索 max_results 默认 200 / 硬顶 2000，分页 limit 默认 100 / 硬顶 1000。不要全量拉取，也不要用宽正则做枚举式扫描。
8. 交付结论 + 定位（文件名 + index + 最短必要引文），不要堆砌原文/译文。

完整约束见随项目分发的 skills/galtransl-mcp/SKILL.md。"""

# 键名沿用 mcp SDK 的 ToolAnnotations 字段名（snake_case），由传输层构造该类型；
# 本模块不 import mcp，保持工具层的传输无关性（0.5.1 架构约定）。
READ_ONLY_ANNOTATIONS: Dict[str, Any] = {
    "read_only_hint": True,
    "destructive_hint": False,
    "idempotent_hint": True,
    "open_world_hint": False,
}

# check_model 会向外部模型端点发真实探测请求：不改用户环境，但访问开放世界——
# 若沿用 READ_ONLY_ANNOTATIONS 的 open_world_hint=False，客户端可能据此免确认放行外部调用
PROBE_ANNOTATIONS: Dict[str, Any] = {
    "read_only_hint": True,
    "destructive_hint": False,
    "idempotent_hint": True,
    "open_world_hint": True,
}


def tool_annotations(tool_def: Dict[str, Any]) -> Dict[str, Any]:
    """按工具定义派生 MCP annotations：定义自带覆盖 > kind 派生。

    只读检索工具只读本地磁盘或经 HTTP 读后端状态、不改环境、同参数重复调用无额外
    副作用，故 kind=read 统一映射为 READ_ONLY_ANNOTATIONS；例外由定义级 annotations
    覆盖（check_model 对外发探测请求 → PROBE_ANNOTATIONS）。
    写工具（kind=write）返回空 dict —— 绝不能下发 read_only_hint=True，否则客户端
    可能据此跳过用户确认直接执行写入。返回副本，调用方改写不会污染模块级常量。
    """
    override = tool_def.get("annotations")
    if isinstance(override, dict) and override:
        return dict(override)
    if str(tool_def.get("kind", "")) == "read":
        return dict(READ_ONLY_ANNOTATIONS)
    return {}


# ---------- 工具定义与分发 ----------

def _def(
    name: str,
    description: str,
    properties: Dict[str, Any],
    required: List[str],
    kind: str = "read",
    annotations: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造单条 MCP 工具定义（字段名对齐 MCP Tool：name / description / inputSchema）。

    kind 决定 tool_annotations() 的默认注解：read → READ_ONLY_ANNOTATIONS。
    写类工具必须显式传 kind="write"，否则会被误标为只读、可能让客户端跳过用户确认。
    annotations 为定义级覆盖（如 check_model 的探测注解），仅在有值时写入条目。
    """
    item = {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties, "required": required},
        "kind": kind,
    }
    if annotations:
        item["annotations"] = dict(annotations)
    return item


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
        "读取元数据：kind=globalprompt（全局分析）/ plotroute（剧情路线图）/ "
        "filemeta（文件元数据，需 filename）/ batchmeta（批次元数据，需 filename）/ all（含可用文件名清单）。",
        {
            "project_dir": _PROJECT_PROP,
            "kind": {
                "type": "string",
                "enum": ["globalprompt", "plotroute", "filemeta", "batchmeta", "all"],
                "description": "要读取的元数据种类，默认 globalprompt",
            },
            "filename": {"type": "string", "description": "kind=filemeta/batchmeta 时必填"},
        },
        ["project_dir"],
    ),
    _def(
        "galtransl_get_job_status",
        "查询某项目最近的翻译任务与实时进度摘要（当前阶段、进度百分比、worker 数、速度、ETA）。"
        "submit_job 之后可用它观察进度。需 GalTransl 后端在运行。",
        {"project_dir": _PROJECT_PROP},
        ["project_dir"],
    ),
    _def(
        "galtransl_check_model",
        "校验某项目所选后端的模型/令牌可用性。会向模型端点发起一次真实探测请求（消耗极小额度），"
        "建议在 submit_job 前调用，避免提交后才发现配置失效。需 GalTransl 后端在运行。",
        {
            "project_dir": _PROJECT_PROP,
            "translator": {"type": "string", "description": "翻译引擎 ID，如 ForGal-full-pipeline"},
            "config_file_name": {
                "type": "string",
                "description": "配置文件名，默认按项目探测（config.inc.yaml 优先）",
            },
            "backend_profile": {"type": "string", "description": "API 配置名（可选）"},
        },
        ["project_dir", "translator"],
        annotations=PROBE_ANNOTATIONS,
    ),
]

_WRITE_TOOL_DEFS: List[Dict[str, Any]] = [
    _def(
        "galtransl_write_route_map",
        "整体覆盖写入剧情路线图（PlotRouteMap.json）。未提供的字段保留旧值；"
        "mermaid 会经生成侧同口径校验（首行必须 flowchart/graph 开头）。"
        "写入前请先读取现状，未被要求修改的路线/文件必须原样带回。",
        {
            "project_dir": _PROJECT_PROP,
            "结构类型": {"type": "string", "description": "路线结构类型；不传保留旧值"},
            "用户大纲": {"type": "string", "description": "用户大纲；不传保留旧值"},
            "mermaid": {"type": "string", "description": "mermaid 源码（首行 flowchart/graph）"},
            "文件归属": {"type": "object", "description": "{文件名: 路线名}"},
            "节点剧情": {"type": "object", "description": "{路线名: 剧情摘要}"},
        },
        ["project_dir"],
        kind="write",
    ),
    _def(
        "galtransl_save_metadata",
        "原子写入单文件元数据，kind=filemeta（文件级）或 batchmeta（批次级），均需 filename（不含扩展名）。"
        "entry 为要写入的完整 JSON 对象（整体覆盖）。"
        "剧情路线图请走 galtransl_write_route_map（带 mermaid 校验）；全局分析由流水线生成，不开放写入。",
        {
            "project_dir": _PROJECT_PROP,
            "kind": {
                "type": "string",
                "enum": ["filemeta", "batchmeta"],
                "description": "元数据种类",
            },
            "filename": {"type": "string", "description": "必填，不含扩展名"},
            "entry": {"type": "object", "description": "要写入的完整元数据对象"},
        },
        ["project_dir", "kind", "entry"],
        kind="write",
    ),
    _def(
        "galtransl_submit_job",
        "提交 GalTransl 翻译任务（会真实启动翻译、消耗 API 额度）。"
        "**仅在用户明确要求开始翻译时调用**；调用前应与用户确认项目与引擎。"
        "返回 job_id，可用 galtransl_stop_job 停止。",
        {
            "project_dir": _PROJECT_PROP,
            "translator": {"type": "string", "description": "翻译引擎 ID，如 ForGal-full-pipeline"},
            "config_file_name": {"type": "string", "description": "配置文件名，默认 config.yaml"},
            "backend_profile": {"type": "string", "description": "API 配置名（可选）"},
            "file_filter": {
                "type": "array",
                "items": {"type": "string"},
                "description": "仅翻译这些文件（可选，支持完整路径/文件名/去扩展名）",
            },
        },
        ["project_dir", "translator"],
        kind="write",
    ),
    _def(
        "galtransl_stop_job",
        "请求停止某项目当前正在运行的翻译任务。无运行中任务时后端返回 409。",
        {"project_dir": _PROJECT_PROP},
        ["project_dir"],
        kind="write",
    ),
]

MCP_TOOL_DEFS.extend(_WRITE_TOOL_DEFS)

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
    "galtransl_get_job_status": _tool_get_job_status,
    "galtransl_check_model": _tool_check_model,
    "galtransl_write_route_map": _tool_write_route_map,
    "galtransl_save_metadata": _tool_save_metadata,
    "galtransl_submit_job": _tool_submit_job,
    "galtransl_stop_job": _tool_stop_job,
}


# 只读工具名集合：以 MCP_TOOL_DEFS 的 kind 为唯一真相源，避免与注册表脱节
_READ_ONLY_TOOL_NAMES = frozenset(
    item["name"] for item in MCP_TOOL_DEFS if item.get("kind") == "read"
)


def _attach_project_dir_warning(
    name: str, args: Dict[str, Any], result: Dict[str, Any]
) -> Dict[str, Any]:
    """给只读工具的成功结果补 `project_dir_valid` 告警（不阻断）。

    集中在此处而非 11 个处理器里，避免遗漏：新增只读工具会自动获得该行为。
    仅当调用确实带了 project_dir 且返回体是 dict 时补；写工具已硬校验，不再重复。
    """
    if name not in _READ_ONLY_TOOL_NAMES or "project_dir" not in args:
        return result
    raw = str(args.get("project_dir", "") or "").strip()
    if not raw or not os.path.isdir(raw):
        return result
    if not isinstance(result, dict) or "project_dir_valid" in result:
        return result
    return {**result, **_project_dir_warning(os.path.normpath(raw))}


def call_mcp_tool(name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """按工具名分发到实现；未知工具抛 KeyError，参数错误抛 ValueError。

    返回值为可直接 JSON 序列化的 dict，调用方（stdio server）负责包装成 MCP 结果。
    只读工具额外附带 `project_dir_valid` 告警（见 `_attach_project_dir_warning`）。
    """
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        raise KeyError(f"unknown tool: {name}")
    args = arguments if isinstance(arguments, dict) else {}
    started = time.time()
    result = handler(args)
    result = _attach_project_dir_warning(name, args, result)
    LOGGER.info(f"[mcp] {name} 完成（{time.time() - started:.2f}s）")
    return result
