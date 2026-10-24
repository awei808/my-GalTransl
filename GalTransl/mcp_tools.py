"""MCP 工具层：面向外部 agent 的能力（0.5.1 只读检索；0.6.0 起增写工具与作业域查询）。

检索类与字典读取为纯读磁盘的纯函数风格；作业域工具（任务状态/模型探测、提交/停止任务）
经 HTTP 回后端（JobRegistry 只存在于后端进程内，见 mcp_backend_client），
路线图/元数据/项目字典写工具与缓存条目写工具（跳过检查标记、备选译文交换、译文字段改写）在本地落盘。
所有工具以显式 project_dir 为边界（绝对路径，或已登记/工作区根下的项目名称），写入范围仅限项目内
指定产物，无任意路径写、无命令执行。
本模块不依赖任何 MCP 传输实现，供独立 stdio server（run_mcp_server.py）与未来内置 agent 共用，
避免上游「工具全走 HTTP 打自家 REST」的架构绕路。
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from typing import Any, Callable, Collection, Dict, List, Optional, Set, Tuple

from GalTransl import (
    CACHE_FOLDERNAME,
    INPUT_FOLDERNAME,
    LOGGER,
    PASS0_CACHE_DIR,
    PASS1_CACHE_DIR,
    PASS2_CACHE_DIR,
    PASS3_CACHE_DIR,
)
from GalTransl.AppSettings import load_app_settings
from GalTransl.Backend.utils import (
    coerce_context_batches,
    normalize_restore_context_mode,
)
from GalTransl.ConfigHelper import detect_config_file
from GalTransl.Dictionary import (
    DictRow,
    _COMMENT_PREFIXES,
    _SITUATION_KEYS,
    _is_separator_line,
    _safe_escape,
    parse_dict_line,
)
from GalTransl.Frontend.pipeline_stages import to_payload as _pipeline_stages_payload
from GalTransl.backend_security import safe_under_project
from GalTransl.server_config_schema import _read_yaml_file
from GalTransl.server_dict import (
    DICT_PROJECT_MARKER,
    _collect_project_dict_payload,
    _ensure_project_dict_file_configured,
    _is_safe_dict_filename,
)
from GalTransl.server_meta import _list_problem_types, _load_project_name_dict
from GalTransl.server_scaffold import _workspace_root
from GalTransl.server_search import (
    DEFAULT_MAX_RESULTS,
    _make_matcher,
    search_cache_entries,
    search_dict_entries,
    search_source_scripts,
)
from GalTransl.Utils import resolve_filename, resolve_filename_rel, resolve_filename_strict

DEFAULT_PAGE_SIZE = 100
HARD_PAGE_SIZE = 1000
_LOG_SOURCES = ("engine", "frontend")


# ---------- 公共辅助 ----------

def _require_project_dir(arguments: Dict[str, Any]) -> str:
    """取出并解析 project_dir 参数：可以是项目根目录绝对路径，或工作区根下的项目名称。

    解析顺序：已存在的目录按原样（normpath，含相对路径的既有行为）；否则对不含
    路径分隔符的取值按项目名解析——优先查已登记项目（GalTransl 打开过/创建过/
    跑过任务的项目，见 known_projects.py），再回退工作区根扫描。
    找不到时给出可用项目名清单，便于 agent 自行纠正。

    只读工具**不**要求「合法项目」——指向父目录批量查看、或项目尚未生成配置时
    先看缓存都是合理用法，硬拦会变成破坏性变更。合法性问题改为在返回体里以
    `project_dir_valid` 告警（见 `_attach_project_dir_warning`）。
    """
    raw = str(arguments.get("project_dir", "") or "").strip()
    if not raw:
        raise ValueError("project_dir is required")
    if os.path.isdir(raw):
        return os.path.normpath(raw)
    if not any(sep in raw for sep in ("/", "\\")) and not os.path.isabs(raw):
        return _resolve_project_name(raw)
    raise ValueError(f"project_dir 不存在或不是目录: {raw}")


def _workspace_projects(workspace_root: str) -> List[Dict[str, Any]]:
    """扫描工作区根下的可识别项目（与 _tool_list_projects 同口径，供名称解析复用）。"""
    projects = []
    for name in sorted(os.listdir(workspace_root)):
        path = os.path.join(workspace_root, name)
        if not os.path.isdir(path):
            continue
        config_name = detect_config_file(path)
        # detect_config_file 找不到也回退 config.yaml，故必须再确认文件真实存在
        if not os.path.isfile(os.path.join(path, config_name)):
            continue
        projects.append({"name": name, "project_dir": path, "config_file_name": config_name})
    return projects


def _resolve_project_name(name: str) -> str:
    """把项目名解析为绝对路径：先查已登记项目，再回退工作区根扫描。"""
    resolved = _resolve_from_known_projects(name)
    if resolved:
        return resolved
    return _resolve_in_workspace(name)


def _valid_known_projects() -> List[Dict[str, Any]]:
    """有效已登记项目清单（目录仍存在且是可识别项目），按 last_seen 新→旧。

    供 list_projects 合并输出与错误消息的可用项目名单复用，统一「失效登记过滤」口径。
    """
    from GalTransl.known_projects import load_known_projects

    valid: List[Dict[str, Any]] = []
    for item in load_known_projects():
        path = os.path.normpath(item["project_dir"])
        if not os.path.isdir(path):
            continue
        try:
            config_name = validate_project_dir(path)
        except ValueError:
            continue
        valid.append({**item, "project_dir": path, "config_file_name": config_name})
    return valid


def _resolve_from_known_projects(name: str) -> str:
    """按已登记项目（GalTransl 打开过/创建过/跑过任务的项目，见 known_projects.py）解析名称。

    登记可能过期（项目被移动/删除），命中后须仍存在且是可识别项目才算有效。
    精确命中取最近使用的那个（与首页「最近项目」同语义）；NFKC 容错命中要求唯一，
    多个同名候选报错列出绝对路径，由 agent 改用绝对路径指定。未命中返回空串。
    先按名字匹配、命中后再 validate（失效过滤只影响能否返回，不影响匹配本身）。
    """
    from GalTransl.known_projects import load_known_projects

    target = unicodedata.normalize("NFKC", name).casefold()
    loose: List[str] = []
    for item in load_known_projects():
        path = os.path.normpath(item["project_dir"])
        if not os.path.isdir(path):
            continue
        if item["name"] == name or unicodedata.normalize("NFKC", item["name"]).casefold() == target:
            # 候选须仍是可识别项目（与写工具同口径）
            try:
                validate_project_dir(path)
            except ValueError:
                continue
            if item["name"] == name:
                return path
            loose.append(path)
    if len(loose) == 1:
        return loose[0]
    if len(loose) > 1:
        shown = "\n".join(loose[:10])
        raise ValueError(f"存在 {len(loose)} 个同名项目「{name}」，请改用绝对路径指定：\n{shown}")
    return ""


def _resolve_in_workspace(name: str) -> str:
    """工作区根扫描回退：与 galtransl_list_projects 的 workspace 来源同口径。"""
    workspace_root = os.path.normpath(_workspace_root())
    if not os.path.isdir(workspace_root):
        raise ValueError(
            f"project_dir 不是已存在的目录，也无法按项目名解析（工作区根不可用: {workspace_root}）：{name}。"
            "请直接传翻译项目根目录的绝对路径。"
        )
    projects = _workspace_projects(workspace_root)
    target = unicodedata.normalize("NFKC", name).casefold()
    exact = [p for p in projects if p["name"] == name]
    loose = [
        p for p in projects
        if p["name"] != name
        and unicodedata.normalize("NFKC", p["name"]).casefold() == target
    ]
    if len(exact) == 1:
        return os.path.normpath(exact[0]["project_dir"])
    if len(loose) == 1:
        return os.path.normpath(loose[0]["project_dir"])
    # 两路来源合并去重后给出可用项目名，便于 agent 自行纠正
    known_names = [item["name"] for item in _valid_known_projects()]
    all_names = list(dict.fromkeys(known_names + [p["name"] for p in projects]))
    shown = "、".join(all_names[:10]) + (f" 等共 {len(all_names)} 个" if len(all_names) > 10 else "")
    available = f"可用项目：{shown}。" if all_names else "暂无已登记项目，工作区根下也没有可识别项目。"
    raise ValueError(
        f"未找到名为「{name}」的项目。{available}"
        "项目在 GalTransl 中打开过/创建过即可按名称解析；也可直接传翻译项目根目录的绝对路径。"
    )


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
    """按 offset/limit 对条目列表分页；offset 为负时从文件末尾倒数（-1=最后一条）。"""
    total = len(entries)
    try:
        raw_offset = int(arguments.get("offset", 0))
    except (TypeError, ValueError):
        raw_offset = 0
    if raw_offset < 0:
        # 负数 offset 从末尾倒数，换算为正值返回，便于调用方继续向后翻页
        offset = max(total + raw_offset, 0)
    else:
        offset = min(raw_offset, total)
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
    """把项目内相对文件名解析为绝对路径，拒绝越界与路径穿越。

    精确文件不存在时按 NFKC 归一解析（目录列表兜底，兼容 agent 传入的
    半角写法）；解析不到返回原拼接路径，由调用方按不存在处理。
    """
    if not filename:
        raise ValueError("filename is required")
    base_dir = os.path.join(project_dir, sub_dir)
    # safe_under_project 拒绝绝对路径并以 commonpath 校验归属，杜绝穿越
    path = safe_under_project(base_dir, filename)
    if os.path.isfile(path):
        return path
    resolved = resolve_filename_rel(base_dir, filename.replace("\\", "/"))
    if resolved:
        # 解析值来自目录列表，天然不越界；仍走 safe_under_project 保持口径统一
        return safe_under_project(base_dir, resolved)
    return path


# ---------- 搜索域（6） ----------

def _tool_search_cache(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return search_cache_entries(
        _require_project_dir(arguments),
        arguments.get("query", ""),
        field=str(arguments.get("field", "all") or "all").strip(),
        use_regex=bool(arguments.get("regex", False)),
        max_results=arguments.get("max_results", DEFAULT_MAX_RESULTS),
        context=arguments.get("context", 0),
    )


def _tool_search_scripts(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return search_source_scripts(
        _require_project_dir(arguments),
        arguments.get("query", ""),
        use_regex=bool(arguments.get("regex", False)),
        max_results=arguments.get("max_results", DEFAULT_MAX_RESULTS),
        context=arguments.get("context", 0),
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
    """列出可用项目：已登记（GalTransl 打开过/创建过/跑过任务）+ 工作区根下可识别的目录。

    同一目录两个来源都会命中时只保留一条（registered 优先）。
    """
    workspace_root = os.path.normpath(str(arguments.get("workspace_root", "") or "").strip() or _workspace_root())
    projects: List[Dict[str, Any]] = []
    seen_dirs: Set[str] = set()

    def _entry(path: str, name: str, config_name: str, source: str) -> Dict[str, Any]:
        return {
            "name": name,
            "project_dir": path,
            "config_file_name": config_name,
            "has_cache": os.path.isdir(os.path.join(path, CACHE_FOLDERNAME)),
            "has_input": os.path.isdir(os.path.join(path, INPUT_FOLDERNAME)),
            "source": source,
        }

    for item in _valid_known_projects():
        path = item["project_dir"]
        if path in seen_dirs:
            continue
        seen_dirs.add(path)
        projects.append(_entry(path, item["name"], item["config_file_name"], "registered"))

    if os.path.isdir(workspace_root):
        for found in _workspace_projects(workspace_root):
            path = os.path.normpath(found["project_dir"])
            if path in seen_dirs:
                continue
            seen_dirs.add(path)
            projects.append(_entry(path, found["name"], found["config_file_name"], "workspace"))

    return {"workspace_root": workspace_root, "exists": os.path.isdir(workspace_root), "total": len(projects), "projects": projects}


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
    # aftertrans_cache（阶段 8 断点标记）与 gendic_cache（阶段 2 术语表分片）均为 dict 形状的
    # 断点产物，不是翻译缓存，不进 agent 文件清单（与 _is_translation_cache_rel 的排除口径一致）
    cache_names = (
        [
            n
            for n in _list_files(cache_dir, ".json")
            if not n.startswith(("aftertrans_cache/", "gendic_cache/"))
        ]
        if os.path.isdir(cache_dir)
        else []
    )
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
        "restore_context_mode": normalize_restore_context_mode(
            common.get("gpt.restoreContextMode", "lines")
        ),
        "context_batches": coerce_context_batches(common.get("gpt.contextBatches", 1)),
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
    """读取元数据：globalprompt / plotroute / filemeta / batchmeta / routeanalysis。"""
    project_dir = _require_project_dir(arguments)
    kind = str(arguments.get("kind", "globalprompt") or "globalprompt").strip()
    if kind not in ("globalprompt", "plotroute", "filemeta", "batchmeta", "routeanalysis", "all"):
        raise ValueError(f"unsupported metadata kind: {kind}")

    def _load(path: str) -> Dict[str, Any]:
        if not os.path.isfile(path):
            return {"exists": False, "path": path, "entry": None}
        try:
            return {"exists": True, "path": path, "entry": _read_json_file(path)}
        except Exception as exc:
            return {"exists": False, "path": path, "entry": None, "error": str(exc)}

    result: Dict[str, Any] = {"project_dir": project_dir, "kind": kind}
    route_analysis_dir = os.path.join(project_dir, CACHE_FOLDERNAME, PASS0_CACHE_DIR, "route_analysis")
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
        result["routeanalysis_files"] = _list_files(route_analysis_dir, ".json") if os.path.isdir(route_analysis_dir) else []
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
    if kind == "routeanalysis":
        filename = str(arguments.get("filename", "") or "").strip()
        if not filename:
            raise ValueError("filename is required for kind=routeanalysis")
        _ensure_safe_metadata_filename(filename)
        result["routeanalysis"] = _load(os.path.join(route_analysis_dir, f"{filename}.json"))
    return result


def _tool_read_glossary(arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_dir = _require_project_dir(arguments)
    return read_glossary(
        project_dir,
        filename=str(arguments.get("filename", "") or "").strip(),
        query=arguments.get("query", ""),
        use_regex=bool(arguments.get("regex", False)),
        offset=arguments.get("offset", 0),
        limit=arguments.get("limit", DEFAULT_PAGE_SIZE),
        category=str(arguments.get("category", "gpt") or "gpt").strip(),
    )


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


def _tool_write_glossary(arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_dir = _require_write_project_dir(arguments)
    return write_glossary(
        project_dir,
        str(arguments.get("file", "") or "").strip(),
        str(arguments.get("mode", "") or "").strip(),
        arguments.get("entries"),
        category=str(arguments.get("category", "gpt") or "gpt").strip(),
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


# ---------- 缓存条目写工具（跳过检查 / 交换备选译文） ----------

# 任务刚结束（cancelled/failed）后，后端 finally 仍会合并 .append.jsonl 增量并重检，
# 此窗口内改写缓存会丢合并增量；翻译已停止但合并未落盘时同样拒绝写入
_JOB_RECENT_TERMINAL_SECONDS = 30.0


def _finished_within_seconds(finished_at: Any, seconds: float) -> bool:
    """finished_at（UTC ISO，Z 后缀）是否落在最近 seconds 秒内；解析失败按不新处理。"""
    from datetime import datetime, timedelta, timezone

    try:
        ts = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    delta = datetime.now(timezone.utc) - ts
    return timedelta(0) <= delta <= timedelta(seconds=seconds)


def _active_job_of(project_dir: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    """查询后端该项目是否有进行中/刚结束的翻译任务。返回 (任务摘要, 后端是否可达)。

    缓存文件在翻译期间被 worker 增量写盘、停止后还有一次增量合并+重检，外部并发
    改写会互相覆盖或丢数据（与 /cache/save 的 409 拒绝同一风险）。MCP 是独立进程，
    只能经 HTTP 查询后端；后端不可达时放行并在结果里注明（此时大概率没有翻译在跑，
    不因查询失败而阻断离线编辑）。
    """
    try:
        from GalTransl.mcp_backend_client import get_json
        from GalTransl.server_runtime import _normalize_project_dir

        jobs_raw = get_json("/api/jobs").get("jobs")
    except Exception as exc:
        LOGGER.debug(f"[mcp] 查询后端任务状态失败（按后端不可达处理）：{exc}")
        return None, False
    target = _normalize_project_dir(project_dir)
    recent_terminal: Optional[Dict[str, Any]] = None
    for job in jobs_raw if isinstance(jobs_raw, list) else []:
        if not isinstance(job, dict):
            continue
        if _normalize_project_dir(str(job.get("project_dir", ""))) != target:
            continue
        status = job.get("status")
        if status in ("pending", "running"):
            return {
                "phase": "running",
                "job_id": job.get("job_id"),
                "status": status,
                "translator": job.get("translator"),
            }, True
        if (
            status in ("cancelled", "failed")
            and recent_terminal is None
            and _finished_within_seconds(job.get("finished_at"), _JOB_RECENT_TERMINAL_SECONDS)
        ):
            recent_terminal = {
                "phase": "recent",
                "job_id": job.get("job_id"),
                "status": status,
                "translator": job.get("translator"),
            }
    return recent_terminal, True


def _modify_cache_entry(
    project_dir: str,
    filename: str,
    index: Any,
    mutate: Callable[[Dict[str, Any]], Optional[str]],
    action_label: str,
    on_change: Optional[Callable[[Dict[str, Any], str], None]] = None,
) -> Dict[str, Any]:
    """定位缓存条目 → 原地修改 → 快照 → 落盘 → 按 /cache/save 同口径重建问题标注。

    mutate(entry) 返回操作描述文本；业务校验不过（如无备选译文）时在 mutate 内抛
    ValueError。重建复用 server_cache 的检测链路（依赖加载失败时保留原 problem，
    检测异常时不覆写已有 problem，与 /cache/save 的降级口径一致）。

    on_change(target, rel) 在确认有实际变化后、快照/落盘前调用（用于 H 门禁等
    写前校验）；抛异常即中止本次写入，磁盘保持原样。no-op 时不会调用。
    """
    # 自身兜底：本函数是公开入口，直接调用者不应绕过 L3 项目校验
    validate_project_dir(project_dir)
    job, backend_reachable = _active_job_of(project_dir)
    if job:
        if job.get("phase") == "recent":
            raise ValueError(
                f"翻译任务刚结束（job_id={job.get('job_id', '')}），后端正在合并增量缓存并重检，"
                f"请等待约 {_JOB_RECENT_TERMINAL_SECONDS:.0f} 秒后再修改缓存。"
            )
        raise ValueError(
            f"翻译任务进行中（job_id={job.get('job_id', '')}），请先停止翻译再修改缓存"
            "（翻译期间 worker 会增量写盘，并发改写会互相覆盖）。"
        )

    cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
    file_path = _resolve_project_file(project_dir, CACHE_FOLDERNAME, filename)
    if not os.path.isfile(file_path):
        raise ValueError(f"文件不存在: {CACHE_FOLDERNAME}/{filename}")
    rel = os.path.relpath(file_path, cache_dir).replace("\\", "/")
    # 只允许翻译缓存：pass1/pass2 元数据等没有 skip_check/alt_dst 语义
    from GalTransl.server_cache import _is_translation_cache_rel

    if not _is_translation_cache_rel(rel):
        raise ValueError(f"不是翻译缓存文件（元数据/示例文件不支持该操作）: {rel}")

    import orjson

    with open(file_path, "rb") as f:
        old_bytes = f.read()
    entries = orjson.loads(old_bytes)
    if not isinstance(entries, list):
        raise ValueError(f"缓存文件内容不是 JSON 数组: {rel}")

    try:
        idx_int = int(index)
    except (TypeError, ValueError):
        raise ValueError(f"index 必须是整数: {index!r}")
    target = next(
        (e for e in entries if isinstance(e, dict) and e.get("index") == idx_int), None
    )
    if target is None:
        # 兼容 index 落盘为数字字符串的旧缓存
        target = next(
            (
                e
                for e in entries
                if isinstance(e, dict)
                and isinstance(e.get("index"), str)
                and e["index"].isdigit()
                and int(e["index"]) == idx_int
            ),
            None,
        )
    if target is None:
        indexes = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            val = e.get("index")
            if isinstance(val, int):
                indexes.append(val)
            elif isinstance(val, str) and val.isdigit():
                indexes.append(int(val))
        scope = f"{min(indexes)}~{max(indexes)}" if indexes else "（文件为空）"
        raise ValueError(f"未找到 index={idx_int} 的条目（{rel} 现有 index 范围 {scope}）")
    problem_before = target.get("problem", "")
    detail = mutate(target)
    if detail is None:
        # 本操作为 no-op：不动文件、不重检——否则整文件重检可能刷新其它条目，
        # 造成「detail 说没改但 changed=true」的误导（审计日志同样会自相矛盾）
        LOGGER.info(f"[mcp] {action_label} {rel} index={idx_int}（no-op）")
        return {
            "success": True,
            "project_dir": project_dir,
            "filename": rel,
            "index": idx_int,
            "changed": False,
            "detail": "条目已处于目标状态，无变化",
            "problem_before": problem_before,
            "problem_after": problem_before,
            "recheck_note": "",
            "job_check": "ok" if backend_reachable else "backend_unreachable（无法确认是否有翻译任务在运行，请自行确认）",
        }

    if on_change is not None:
        on_change(target, rel)

    # 重建 problem / post_dst_preview / skip_check（整文件，与 /cache/save 保存后重建一致）
    recheck_note = ""
    results: Optional[List[Dict[str, Any]]] = None
    detection_ok = False
    try:
        from GalTransl.server_cache import _load_rebuild_deps, _resolve_cache_h_ranges, _run_problem_detection

        deps = _load_rebuild_deps(project_dir, detect_config_file(project_dir))
        if deps[0] is None:
            recheck_note = "项目配置加载失败，已保留原 problem 字段"
        else:
            (
                proj_config,
                pre_dic,
                post_dic,
                gpt_dic,
                t_plugins,
                h_check_words,
                forbidden_words,
            ) = deps
            h_ranges = [
                (r["lo"], r["hi"])
                for r in _resolve_cache_h_ranges(project_dir, rel).get("h_ranges", [])
            ]
            results, detection_ok = _run_problem_detection(
                entries,
                proj_config,
                pre_dic,
                post_dic,
                gpt_dic,
                t_plugins,
                h_ranges,
                h_check_words,
                forbidden_words,
            )
    except Exception as exc:
        LOGGER.warning(f"[mcp] 缓存条目修改后重检失败（保留原标注）：{rel}: {exc}")
        recheck_note = f"重检失败，已保留原 problem 字段：{exc}"
    if results is not None:
        for e, r in zip(entries, results):
            if (e.get("post_src", "") or e.get("post_jp", "")) == "":
                continue
            if detection_ok:
                if r["problem"]:
                    e["problem"] = r["problem"]
                elif "problem" in e:
                    del e["problem"]
            e["post_dst_preview"] = r["post_dst_preview"]
            if r["skip_check"]:
                e["skip_check"] = True
            elif "skip_check" in e:
                del e["skip_check"]

    new_bytes = orjson.dumps(entries, option=orjson.OPT_INDENT_2)
    changed = new_bytes != old_bytes
    if changed:
        from GalTransl.server_cache import _append_engine_log, snapshot_cache_file

        snapshot_cache_file(cache_dir, rel, project_dir)
        tmp_path = file_path + ".tmp"
        with open(tmp_path, "wb") as f:
            f.write(new_bytes)
        os.replace(tmp_path, file_path)
        _append_engine_log(project_dir, f"[mcp] {action_label} {rel} index={idx_int}：{detail}")
    LOGGER.info(f"[mcp] {action_label} {rel} index={idx_int}（changed={changed}）")
    return {
        "success": True,
        "project_dir": project_dir,
        "filename": rel,
        "index": idx_int,
        "changed": changed,
        "detail": detail,
        "problem_before": problem_before,
        "problem_after": target.get("problem", ""),
        "recheck_note": recheck_note,
        "job_check": "ok" if backend_reachable else "backend_unreachable（无法确认是否有翻译任务在运行，请自行确认）",
    }


def mark_skip_check(project_dir: str, filename: str, index: Any, skip: bool = True) -> Dict[str, Any]:
    """标记 / 取消某条翻译缓存条目的「跳过问题检查」（skip_check）。

    标记后该条不再参与问题检测，已有 problem 标记经重建清除；取消后重检恢复检测。
    """
    return _modify_cache_entry(
        project_dir,
        filename,
        index,
        lambda entry: _mutate_skip_check(entry, bool(skip)),
        "标记跳过检查" if skip else "取消跳过检查",
    )


def _mutate_skip_check(entry: Dict[str, Any], skip: bool) -> Optional[str]:
    """返回操作描述；返回 None 表示条目已处于目标状态（no-op，不重检不落盘）。"""
    if skip:
        if bool(entry.get("skip_check", False)):
            return None
        entry["skip_check"] = True
        return "已标记 skip_check=true（问题检测跳过该条，已有 problem 标记一并清除）"
    if "skip_check" not in entry:
        return None
    del entry["skip_check"]
    return "已取消 skip_check 标记（问题检测恢复覆盖该条）"


def swap_alt_translation(project_dir: str, filename: str, index: Any) -> Dict[str, Any]:
    """交换某条翻译缓存条目的备选译文（alt_dst）与当前译文（pre_dst），语义与校对页一致。

    旧缓存条目仅有 pre_zh（无 pre_dst）时，交换会把旧 pre_zh 值写入 alt_dst——
    读侧 pre_dst 优先，行为与校对页对该类条目的处理存在历史格式差异，但不破坏数据。
    """
    return _modify_cache_entry(
        project_dir,
        filename,
        index,
        _mutate_swap_alt,
        "交换备选译文",
    )


def _mutate_swap_alt(entry: Dict[str, Any]) -> Optional[str]:
    """返回操作描述；返回 None 表示 alt 与当前译文相同（交换后无变化，no-op）。"""
    alt = entry.get("alt_dst", "")
    if not alt:
        raise ValueError(
            "该条目没有备选译文（alt_dst 由后处理轮 improve/brfix/jpfix/banfix/fix 生成）；"
            "可先用 galtransl_read_translation_file 查看哪些条目带 alt_dst"
        )
    pre = entry.get("pre_dst", "") or entry.get("pre_zh", "")
    if alt == pre:
        return None
    entry["pre_dst"], entry["alt_dst"] = alt, pre
    return "已交换 pre_dst 与 alt_dst（交换后按新译文重检问题标注）"


def _tool_mark_skip_check(arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_dir = _require_write_project_dir(arguments)
    return mark_skip_check(
        project_dir,
        str(arguments.get("filename", "") or "").strip(),
        arguments.get("index"),
        bool(arguments.get("skip", True)),
    )


def _tool_swap_alt_translation(arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_dir = _require_write_project_dir(arguments)
    return swap_alt_translation(
        project_dir,
        str(arguments.get("filename", "") or "").strip(),
        arguments.get("index"),
    )


# 缓存条目可写译文字段：与校对页 EDITABLE_FIELD_KEYS 同口径（其余字段只读）
_CACHE_ENTRY_WRITABLE_FIELDS = ("pre_dst", "proofread_dst", "alt_dst")
# 旧缓存兼容键：canonical 键缺失/None 时回退（与 Cache.py 的 _cache_get 对齐）；
# server_cache/检索侧用 `or` 回退，故清空 canonical 时仍需同步删旧键
_CACHE_ENTRY_LEGACY_FIELDS = {"pre_dst": "pre_zh", "proofread_dst": "proofread_zh"}


def _enforce_cache_entry_h_gate(project_dir: str, rel: str, texts: List[str]) -> None:
    """缓存条目改写前的 H 门禁：文件维度（rel 所在 H 区间）+ 本次新写入文本。"""
    enforce_h_gate(project_dir, cache_filename=rel, texts=texts)


def update_translation_entry(
    project_dir: str,
    filename: str,
    index: Any,
    fields: Dict[str, Any],
) -> Dict[str, Any]:
    """直接改写某条翻译缓存条目的可写译文字段。

    可写字段与校对页一致：pre_dst（初译/主译文）、proofread_dst（校对译文）、
    alt_dst（备选译文）。新值按真实换行（LF）归一；proofread_dst / alt_dst 传空串
    表示移除该字段，pre_dst 传空串表示置为未翻译。改写后按 /cache/save 同口径
    整文件重检 problem/post_dst_preview，并在写前做 H 硬门禁校验。

    Raises:
        ValueError: 字段不合法、条目不存在、翻译任务运行中/刚结束、命中 H 门禁。
    """
    if not isinstance(fields, dict) or not fields:
        raise ValueError(
            "fields 必须是非空对象，至少包含 "
            f"{'/'.join(_CACHE_ENTRY_WRITABLE_FIELDS)} 之一"
        )
    unknown = sorted(set(fields) - set(_CACHE_ENTRY_WRITABLE_FIELDS))
    if unknown:
        raise ValueError(
            f"不支持的字段: {', '.join(unknown)}；可写字段仅 "
            f"{'/'.join(_CACHE_ENTRY_WRITABLE_FIELDS)}"
        )
    normalized: Dict[str, str] = {}
    for key, value in fields.items():
        if not isinstance(value, str):
            raise ValueError(f"字段 {key} 的值必须是字符串（清空请传空串）")
        normalized[key] = value.replace("\r\n", "\n").replace("\r", "\n")

    def _current(entry: Dict[str, Any], key: str) -> str:
        # canonical 键存在即以其为准（含空串，与 Cache.py 的 _cache_get 一致）；
        # 仅在键缺失/为 None 时回退旧键
        value = entry.get(key)
        if value is None:
            legacy = _CACHE_ENTRY_LEGACY_FIELDS.get(key)
            value = entry.get(legacy) if legacy else None
        return "" if value is None else str(value)

    def _has_stale_legacy(entry: Dict[str, Any], key: str, value: str) -> bool:
        """旧键仍在且值与目标不同：即使 canonical 已是目标值也要落盘清理，避免歧义。"""
        legacy = _CACHE_ENTRY_LEGACY_FIELDS.get(key)
        if not legacy:
            return False
        old = entry.get(legacy)
        return old is not None and str(old) != value

    changed_values: List[str] = []

    def _mutate(entry: Dict[str, Any]) -> Optional[str]:
        changed_keys = [
            key
            for key, value in normalized.items()
            if _current(entry, key) != value or _has_stale_legacy(entry, key, value)
        ]
        if not changed_keys:
            return None
        value_changed = []
        for key in changed_keys:
            # 只有 canonical/有效值真的变化时才纳入 H 文本校验；
            # 仅清理旧键不引入新文本，不必过文本维度
            if _current(entry, key) != normalized[key]:
                value_changed.append(key)
                changed_values.append(normalized[key])
            # 同步移除旧键别名（pre_zh/proofread_zh）：重检/检索侧会在新键为空时
            # 用 `or` 回退旧键，不清掉会把刚清空的字段又读回来
            legacy = _CACHE_ENTRY_LEGACY_FIELDS.get(key)
            if legacy:
                entry.pop(legacy, None)
            # 可选译文清空时移除字段（校对页写空串，读取侧语义等价）；
            # pre_dst 是主译文，空串表示未翻译，保留字段本身
            if key != "pre_dst" and normalized[key] == "":
                entry.pop(key, None)
            else:
                entry[key] = normalized[key]
        cleanup_only = [key for key in changed_keys if key not in value_changed]
        parts = []
        if value_changed:
            parts.append("已改写 " + "、".join(value_changed))
        if cleanup_only:
            parts.append("已清理旧键 " + "、".join(cleanup_only))
        return "；".join(parts)

    return _modify_cache_entry(
        project_dir,
        filename,
        index,
        _mutate,
        "改写译文",
        # 只校验本次新写入的文本；文件维度覆盖 target 所在的 H 区间
        on_change=lambda _entry, rel: _enforce_cache_entry_h_gate(
            project_dir, rel, changed_values
        ),
    )


def _tool_update_translation_entry(arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_dir = _require_write_project_dir(arguments)
    return update_translation_entry(
        project_dir,
        str(arguments.get("filename", "") or "").strip(),
        arguments.get("index"),
        arguments.get("fields"),
    )


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
    # agent 写入的文件名可能被归一成半角，落盘前按 gt_input 真实名规整（与生成端同口径）
    normalized = ForPlotRouteMap._ground_to_real_files(
        normalized,
        ForPlotRouteMap.list_real_input_files(os.path.join(project_dir, INPUT_FOLDERNAME)),
    )
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


# ---------- 设置联动（app_settings.json，与后端 UI 同一份文件） ----------

class MCPToolDisabledError(Exception):
    """工具被用户在 GalTransl 设置中禁用（call_mcp_tool 分发前抛出）。"""


def mcp_h_gate_enabled() -> bool:
    """H 门禁开关（实时读设置；读失败回退默认开启，保持既有安全行为）。"""
    return bool(load_app_settings().get("mcpHGateEnabled", True))


def disabled_mcp_tool_names() -> frozenset:
    """用户禁用的工具名集合（黑名单口径：未列入者默认启用）。"""
    return frozenset(load_app_settings().get("mcpDisabledTools", []))


def enabled_tool_defs() -> List[Dict[str, Any]]:
    """按禁用黑名单过滤后的工具定义（供 tools/list 与心跳统计共用）。"""
    disabled = disabled_mcp_tool_names()
    if not disabled:
        return list(MCP_TOOL_DEFS)
    return [item for item in MCP_TOOL_DEFS if item["name"] not in disabled]


# ---------- H 门禁（内容写入类写工具的硬拦截） ----------

_H_DENY_MESSAGE = (
    "写入被拒绝：目标内容被判定为 H / 成人向。本服务不下发此类内容，"
    "也无法代写。请如实告知用户「该部分因 H 门禁未执行」，由用户在 GalTransl 界面手动处理。"
)


def _entry_has_h(project_dir: str, filename: str) -> bool:
    """判断某个翻译缓存文件是否含 H 区间（复用校对界面同源判定）。

    `_resolve_cache_h_ranges` 要求真实存在的缓存相对路径。缓存工具传入的
    `pass3_cache/01.json` 等相对路径优先按原样判定；元数据工具传入的是输入名
    （不带扩展名或含 .json），故再按 pass3_cache 下的缓存命名探测 `{filename}.json`
    与分片形态，并按 NFKC 归一追加候选。识别不了时返回 False——不因判定不了而阻断正常写入。
    """
    # 缓存工具传入的是相对 transl_cache 的路径（如 pass3_cache/01.json 或顶层 01.json）：
    # 能按该路径直接命中文件时优先按缓存路径判定，避免被下面的「输入名」探测漏掉
    norm = str(filename or "").replace("\\", "/").strip("/")
    try:
        from GalTransl.server_cache import _resolve_cache_h_ranges

        parts = norm.split("/") if norm and not os.path.isabs(norm) else []
        if parts and ".." not in parts and os.path.isfile(
            os.path.join(project_dir, CACHE_FOLDERNAME, *parts)
        ):
            return bool(_resolve_cache_h_ranges(project_dir, norm).get("has_h"))
    except Exception as exc:
        LOGGER.debug(f"[mcp] H 区间判定失败，按非 H 处理：{exc}")

    # 两种候选：{输入名}.json（输入名不带扩展名的历史简写）与输入名本身
    # （meta 文件名 = 输入名 + .meta.json，输入名含 .json 时即缓存名），命中由文件存在性决定
    candidates = [f"{filename}.json", filename]
    probe_names = list(candidates)
    pass3_dir = os.path.join(project_dir, CACHE_FOLDERNAME, PASS3_CACHE_DIR)
    for cand in candidates:
        hit = resolve_filename(pass3_dir, cand)
        if hit and hit != cand and hit not in probe_names:
            probe_names.append(hit)
    try:
        for cand in probe_names:
            if _resolve_cache_h_ranges(project_dir, f"{PASS3_CACHE_DIR}/{cand}").get("has_h"):
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
    """H 硬门禁：命中即抛 ValueError，不落盘。

    只由 4 个内容写入类写工具调用（路线图 / 元数据 / 项目字典 / 缓存条目译文字段改写）；
    缓存条目标记与备选交换不引入新文本，不调用本函数（对调 H 区间条目的既有译文属允许操作）。
    两条判据任一成立即拒绝：(1) 目标缓存文件落在 H 区间；(2) 待写入文本命中 H 词库。
    元数据类写入（filemeta/batchmeta）本身不含对话原文，故主要靠文件维度判定。
    用户可在设置中关闭门禁（mcpHGateEnabled），关闭时直接放行。
    """
    if not mcp_h_gate_enabled():
        LOGGER.debug("[mcp] H 门禁已被用户关闭，跳过 H 校验")
        return
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

    走本模块自带的 .tmp + os.replace 原子写（HTTP 侧元数据端点同为该原子写口径），
    并复用与 HTTP 端点同口径的文件名校验。
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
    # 文件名 NFKC 兜底（写路径口径：归一歧义即拒绝），解析到真实 meta 文件，避免半角名重复落盘
    meta_dir = os.path.join(project_dir, CACHE_FOLDERNAME, sub_dir)
    resolved = resolve_filename_strict(meta_dir, f"{name}{suffix}")
    if resolved:
        real_name = resolved[: -len(suffix)] if resolved.endswith(suffix) else name
    else:
        # meta 尚不存在：输入名按 gt_input 真实名做 NFKC 规整，避免半角名新建孤儿 meta
        from GalTransl.Backend.ForPlotRouteMap import ForPlotRouteMap

        real_name = ForPlotRouteMap.ground_single_filename(
            os.path.join(project_dir, INPUT_FOLDERNAME), name
        )
    # H 门禁用解析后的真实输入名，保证全角文件名在半角输入下同样能判定 H 区间
    enforce_h_gate(project_dir, cache_filename=real_name)
    path = os.path.join(meta_dir, f"{real_name}{suffix}")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    size = os.path.getsize(path)
    LOGGER.info(f"[mcp] 元数据已写入 kind={kind} ({size} 字节)")
    return {"success": True, "kind": kind, "filename": real_name, "bytes": size}


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


# ---------- 术语表 / 项目字典（GPT / 译前 / 译后）读写 ----------

# 术语表与项目字典 = config 的 dictionary 各清单中带 (project_dir) 前缀的项目字典文件；
# 公共 Dict/ 下的字典不在此工具的写入范围内（由 galtransl_search_dict 覆盖读取）。
_GLOSSARY_MODES = ("upsert", "delete")
_GLOSSARY_CATEGORIES = ("gpt", "pre", "post")
_GLOSSARY_CATEGORY_LABELS = {"gpt": "术语表（GPT）", "pre": "译前字典", "post": "译后字典"}
# category -> _collect_project_dict_payload 里的文件清单键
_GLOSSARY_CATEGORY_LIST_KEYS = {
    "gpt": "gpt_dict_files",
    "pre": "pre_dict_files",
    "post": "post_dict_files",
}
_GLOSSARY_NEW_FILE_HEADERS = {
    "gpt": "//术语表（GPT 字典）：格式为 日文|中文|解释（解释可不写）；本行为注释，不参与翻译",
    "pre": "//译前字典：格式为 查找词|替换词|备注（备注可不写）；本行为注释，不参与翻译",
    "post": "//译后字典：格式为 查找词|替换词|备注（备注可不写）；本行为注释，不参与翻译",
}
# 制表符/换行会破坏行结构；其余为 str.splitlines() 的行界字符（\v \f \x1c-\x1e \x85 \u2028 \u2029），
# 落盘后下次按行读取会把一行拆成两行，一并拒绝。
_GLOSSARY_FORBIDDEN_CHARS = ("\t", "\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")
# 全部字典类别（含本工具不写的禁用词类）：upsert 到非目标类别文件会静默失效，按类别报错
_GLOSSARY_ALL_CATEGORY_LABELS = (
    ("gpt_dict_files", "术语表（GPT）"),
    ("pre_dict_files", "译前字典"),
    ("post_dict_files", "译后字典"),
    ("forbidden_dict_files_h", "禁用词（H）"),
    ("forbidden_dict_files_nh", "禁用词（非 H）"),
)
# 与 _ensure_safe_metadata_filename 同口径：挡 NTFS 交换数据流（冒号）与 Windows 保留名
_GLOSSARY_WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def _ensure_safe_glossary_filename(filename: str) -> None:
    """术语表文件名硬校验：在 _is_safe_dict_filename 之上补冒号（NTFS ADS）与 Windows 保留名。"""
    if not _is_safe_dict_filename(filename):
        raise ValueError(f"非法术语表文件名: {filename}")
    if ":" in filename:
        raise ValueError(f"非法术语表文件名（不得含冒号）：{filename}")
    if filename.split(".")[0].upper() in _GLOSSARY_WINDOWS_RESERVED:
        raise ValueError(f"非法术语表文件名（Windows 保留名）：{filename}")


def _glossary_files(payload: Dict[str, Any], category: str) -> List[str]:
    """从字典载荷取出指定类别的项目字典文件名（去 (project_dir) 前缀，保持配置顺序）。"""
    return [
        str(key).replace(DICT_PROJECT_MARKER, "").strip()
        for key in payload.get(_GLOSSARY_CATEGORY_LIST_KEYS[category], []) or []
    ]


def _glossary_other_category(payload: Dict[str, Any], filename: str, category: str) -> str:
    """filename 若被配置为**其它**类别的项目字典，返回该类别中文名（否则空串）。

    把 gpt 行写进译后字典、把译前行写进 gpt 文件都会静默失效（对方解析口径不同），
    upsert 到非目标类别文件时按此报错。
    """
    current_key = _GLOSSARY_CATEGORY_LIST_KEYS[category]
    for list_key, label in _GLOSSARY_ALL_CATEGORY_LABELS:
        if list_key == current_key:
            continue
        keys = [
            str(key).replace(DICT_PROJECT_MARKER, "").strip()
            for key in payload.get(list_key, []) or []
        ]
        if filename in keys:
            return label
    return ""


def read_glossary(
    project_dir: str,
    filename: str = "",
    query: str = "",
    use_regex: bool = False,
    offset: Any = 0,
    limit: Any = DEFAULT_PAGE_SIZE,
    category: str = "gpt",
) -> Dict[str, Any]:
    """读取项目字典词条，供整表浏览与筛选。category=gpt（术语表）/ pre（译前字典）/ post（译后字典）。

    与 `search_dict_entries` 的分工：后者跨全部字典类别且必须给 query；本函数限定单一类别，
    允许整表分页浏览。注释行 / 空行 / 分隔线不产条目；line_no 为文件内绝对行号，
    用于向用户报告词条位置（`write_glossary` 按 src 匹配，不按行号）。

    pre/post 类别下除普通词条（查找词|替换词）外，条件/场景行也一并返回（带 row_type，
    MCP 只读不改写它们）；src/dst 统一取生效的查找/替换对。

    Args:
        filename: 只读某个字典文件（项目根目录下的文件名）；空串读全部该类别项目字典。
        query: 筛选词，匹配原文 / 译名 / 解释任一列；空串返回全部。
        use_regex: True 时 query 按正则解释（非法正则抛 ValueError）。
        offset / limit: 分页参数，收敛口径与其它分页读工具一致。
    """
    if category not in _GLOSSARY_CATEGORIES:
        raise ValueError(f"未知 category: {category}（可选 gpt/pre/post）")
    config_name = detect_config_file(project_dir)
    config_path = os.path.join(project_dir, config_name)
    if not os.path.isfile(config_path):
        # 只读工具不硬拦非项目目录：返回空结果 + 提示，由调用方决定
        return {
            "project_dir": project_dir,
            "config_file_name": config_name,
            "config_exists": False,
            "category": category,
            "files": [],
            "missing_files": [],
            "total": 0,
            "offset": 0,
            "limit": DEFAULT_PAGE_SIZE,
            "returned": 0,
            "has_more": False,
            "entries": [],
            "hint": "项目未找到 config.inc.yaml / config.yaml，无法确定字典文件清单；"
                    "请确认 project_dir 指向项目根目录。",
        }

    target = str(filename or "").strip()
    if target:
        _ensure_safe_glossary_filename(target)
    payload = _collect_project_dict_payload(project_dir, config_name)
    files = _glossary_files(payload, category)
    if target:
        other = _glossary_other_category(payload, target, category)
        if other:
            raise ValueError(f"不是{_GLOSSARY_CATEGORY_LABELS[category]}文件: {target}（它是{other}）")
        if target not in files:
            raise ValueError(
                f"不是{category}类项目字典文件: {target}"
                "（项目字典 = config 的 dictionary 各清单中带 (project_dir) 前缀的文件）"
            )
        files = [target]

    query_text = str(query or "").strip()
    matcher: Optional[Callable[[str], bool]] = None
    if query_text:
        try:
            matcher = _make_matcher(query_text, use_regex)
        except re.error as exc:
            raise ValueError(f"invalid regex: {query_text}（{exc}）")

    entries: List[Dict[str, Any]] = []
    missing_files: List[str] = []
    for name in files:
        # 文件名 NFKC 兜底：config 登记名与磁盘名全/半角宽度不一致时同样可读
        hit = resolve_filename(project_dir, name)
        path = os.path.join(project_dir, hit or name)
        if not os.path.isfile(path):
            missing_files.append(name)
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        for line_no, line in enumerate(lines, start=1):
            row = parse_dict_line(line, category)
            entry = _glossary_entry_of_row(name, line_no, row, category)
            if entry is None:
                continue
            if matcher and not (matcher(entry["src"]) or matcher(entry["dst"]) or matcher(entry["note"])):
                continue
            entries.append(entry)
    page_info = _paginate(entries, {"offset": offset, "limit": limit})
    offset_val, limit_val = page_info["offset"], page_info["limit"]
    return {
        "project_dir": project_dir,
        "config_file_name": config_name,
        "config_exists": True,
        "category": category,
        "files": files,
        "missing_files": missing_files,
        **page_info,
        "entries": entries[offset_val:offset_val + limit_val] if limit_val > 0 else [],
    }


def _glossary_entry_of_row(
    name: str, line_no: int, row: DictRow, category: str
) -> Optional[Dict[str, Any]]:
    """把解析后的字典行转成浏览条目；该类别不展示的行类型返回 None。

    gpt 只展示 gpt 行；pre/post 展示 normal/conditional/situation 三种生效行，
    src/dst 统一取生效的查找/替换对（条件行另带 target 与 cond 列）。
    """
    if category == "gpt":
        if row.type != "gpt":
            return None
        src = row.values[0] if len(row.values) > 0 else ""
        dst = row.values[1] if len(row.values) > 1 else ""
        return {
            "file": name,
            "line_no": line_no,
            "row_type": "gpt",
            "src": src,
            "dst": dst,
            "note": row.note,
            "is_regex": row.is_regex,
            "regex_error": row.regex_error,
        }
    if row.type == "normal":
        src = row.values[0] if len(row.values) > 0 else ""
        dst = row.values[1] if len(row.values) > 1 else ""
    elif row.type == "conditional":
        # values = [target, cond, search, replace, note]
        src = row.values[2] if len(row.values) > 2 else ""
        dst = row.values[3] if len(row.values) > 3 else ""
    elif row.type == "situation":
        # values = [scene, search, replace]
        src = row.values[1] if len(row.values) > 1 else ""
        dst = row.values[2] if len(row.values) > 2 else ""
    else:
        return None
    entry = {
        "file": name,
        "line_no": line_no,
        "row_type": row.type,
        "src": src,
        "dst": dst,
        "note": row.note,
        "is_regex": row.is_regex,
        "regex_error": row.regex_error,
    }
    if row.type == "conditional":
        entry["target"] = row.target
        entry["cond"] = row.values[1] if len(row.values) > 1 else ""
    return entry


def _validate_glossary_field(value: Any, label: str) -> str:
    """校验术语表字段可安全落盘（单字段维度），配合 `_ensure_glossary_roundtrip` 做整行自检。

    拒绝控制字符（含 ``str.splitlines()`` 的行界字符，落盘后会把一行拆成两行）与转义漂移
    （如字面 ``\\n`` 会被解析端还原成换行）。
    """
    if not isinstance(value, str):
        raise ValueError(f"{label} 必须是字符串")
    if any(ch in value for ch in _GLOSSARY_FORBIDDEN_CHARS):
        raise ValueError(f"{label} 不能包含制表符、换行或其它行界字符")
    if _safe_escape(value) != value:
        raise ValueError(f"{label} 含转义序列（如 \\n），与读回值不一致，请改用字面文本")
    return value


def _ensure_glossary_roundtrip(expected: List[str], label: str, category: str = "gpt") -> None:
    """把字段按落盘口径序列化成一行后按解析口径自检：必须能被原样读回。

    挡住单字段校验发现不了的拼接类漂移：非末位字段以反斜杠结尾会把 ``\\|`` 还原成
    字面竖线（读回 dst 丢失）、字段含 4 连续空格会被解析端当列分隔——这些都会导致
    写入值与读回值不一致、后续按 src 匹配永远落空。pre/post 类别还要求该行解析为
    普通词条行（而非条件/场景行），否则语义会漂移。
    """
    line = "|".join(_encode_glossary_field(field) for field in expected)
    row = parse_dict_line(line, category)
    expected_type = "gpt" if category == "gpt" else "normal"
    values = list(row.values) + [""] * (len(expected) - len(row.values))
    if row.type != expected_type or values[: len(expected)] != list(expected):
        raise ValueError(f"{label} 无法按字典格式原样写回并读回（请检查反斜杠结尾、连续空格等字符）")


def _encode_glossary_field(value: str) -> str:
    """字段内字面竖线写作 ``\\|``（与解析端 _split_dict_line 互逆）。"""
    return value.replace("|", "\\|")


def _serialize_glossary_line(src: str, dst: str, note: str) -> str:
    """把词条序列化为一行术语表文本（note 为空时只写两列）。"""
    fields = [src, dst] + ([note] if note else [])
    return "|".join(_encode_glossary_field(field) for field in fields)


def _read_glossary_file(project_dir: str, filename: str) -> Tuple[List[str], str, bool]:
    """读取术语表原始行与行尾风格；返回 (lines, eol, exists)。

    以 newline="" 读取以保留原始行尾，写入时按同一风格回写，避免整文件行尾漂移。
    文件名 NFKC 兜底：agent 传入半角写法时解析到项目根下真实文件。
    """
    path = os.path.join(project_dir, resolve_filename(project_dir, filename) or filename)
    if not os.path.isfile(path):
        return [], "\n", False
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        raw = f.read()
    return raw.splitlines(), ("\r\n" if "\r\n" in raw else "\n"), True


def _save_glossary_file(project_dir: str, filename: str, lines: List[str], eol: str) -> int:
    """原子写入术语表（.tmp + os.replace），返回新内容字节数。

    写入内容统一以单个行尾结尾：原文件末尾无换行时，会在首次改动时补上。
    已存在的目标文件经 NFKC 兜底解析（写路径口径：归一歧义即拒绝）。
    """
    path = os.path.join(project_dir, resolve_filename_strict(project_dir, filename) or filename)
    content = eol.join(lines) + eol
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    os.replace(tmp_path, path)
    return len(content.encode("utf-8"))


def write_glossary(
    project_dir: str, filename: str, mode: str, entries: Any, category: str = "gpt"
) -> Dict[str, Any]:
    """按词条增改 / 删除项目字典（gpt 术语表 / pre 译前 / post 译后），原子落盘并保留原有注释与其它行。

    只处理调用方列出的词条：upsert 按 src 匹配（命中则原地替换、未命中追加），
    delete 按 src 删行（pre/post 只匹配普通词条行，条件/场景行不动）；
    **不提供整体覆盖**——读取是分页的，整体覆盖会在分页场景
    静默删掉未读词条。文件未登记进对应 dictionary 清单时自动登记，
    否则写入的内容不会被翻译引擎使用。

    Args:
        filename: 项目根目录下的字典文件名（必填，避免在多个字典文件间误写）。
        mode: upsert（新增或更新）/ delete（删除）；空串按 upsert。
        category: gpt（术语表）/ pre（译前字典）/ post（译后字典），默认 gpt。
        entries: ``[{"src": ..., "dst": ..., "note": ...}]``；note 省略表示更新时
            保留原备注，显式传空串表示清空备注。
    """
    # 自身兜底：本函数是公开入口，直接调用者不应绕过 L3 项目校验
    config_name = validate_project_dir(project_dir)
    if category not in _GLOSSARY_CATEGORIES:
        raise ValueError(f"未知 category: {category}（可选 gpt/pre/post）")
    resolved_mode = str(mode or "").strip() or "upsert"
    if resolved_mode not in _GLOSSARY_MODES:
        raise ValueError(f"未知 mode: {resolved_mode}（可选 upsert/delete）")
    name = str(filename or "").strip()
    if not name:
        raise ValueError(f"file 必填（项目{_GLOSSARY_CATEGORY_LABELS[category]}文件名）")
    _ensure_safe_glossary_filename(name)
    if not isinstance(entries, list) or not entries:
        raise ValueError("entries 必须是非空数组")

    normalized: List[Tuple[str, str, str, bool]] = []
    for i, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ValueError(f"entries[{i}] 必须是对象")
        src = _validate_glossary_field(item.get("src", ""), f"entries[{i}].src")
        if not src.strip():
            raise ValueError(f"entries[{i}].src 不能为空")
        if src.lstrip().startswith(_COMMENT_PREFIXES):
            raise ValueError(f"entries[{i}].src 以 // 开头，会被解析为注释行而静默失效")
        if _is_separator_line(src):
            raise ValueError(f"entries[{i}].src 是装饰分隔线，会被解析为注释行而静默失效")
        if category in ("pre", "post") and src.strip() in _SITUATION_KEYS:
            raise ValueError(
                f"entries[{i}].src 为 {src.strip()}（mono/diag 保留字），在译前/译后字典里"
                "会解析为场景字典行而非普通词条，请改写该词条"
            )
        note_provided = "note" in item
        note = _validate_glossary_field(item.get("note", ""), f"entries[{i}].note") if note_provided else ""
        dst = ""
        if resolved_mode == "upsert":
            dst = _validate_glossary_field(item.get("dst", ""), f"entries[{i}].dst")
            if not dst.strip():
                raise ValueError(f"entries[{i}].dst 不能为空（词条须给出替换词/译名）")
            # 整行自检：写入的行必须能被解析端原样读回，否则后续 upsert 匹配不上
            _ensure_glossary_roundtrip([src, dst] + ([note] if note else []), f"entries[{i}]", category)
        else:
            # delete 只用 src 匹配：空 dst 仅作列占位，验证 src 作为非末位字段不被转义/空格规则改写
            _ensure_glossary_roundtrip([src, ""], f"entries[{i}].src", category)
        normalized.append((src, dst, note, note_provided))

    payload = _collect_project_dict_payload(project_dir, config_name)
    category_files = _glossary_files(payload, category)
    # H 门禁先于配置登记与落盘：命中即拒绝，保证不留任何副作用
    enforce_h_gate(
        project_dir,
        texts=[text for src, dst, note, _np in normalized for text in (src, dst, note) if text],
    )

    registered = False
    if name not in category_files:
        other = _glossary_other_category(payload, name, category)
        if other:
            raise ValueError(f"{name} 是{other}，不是{_GLOSSARY_CATEGORY_LABELS[category]}文件")
        if resolved_mode == "delete":
            raise ValueError(
                f"{name} 未登记为项目{_GLOSSARY_CATEGORY_LABELS[category]}文件；"
                "请先用 galtransl_read_glossary（category 同此处的 category）查看现有文件名"
            )
        _ensure_project_dict_file_configured(project_dir, config_name, category, name)
        registered = True
        LOGGER.info(f"[mcp] 项目字典文件已登记进 config：{config_name} → category={category}: {name}")

    lines, eol, exists = _read_glossary_file(project_dir, name)
    # 新建文件与 0 字节空文件都补注释头，避免产出无说明的空字典
    if resolved_mode == "upsert" and not lines:
        lines = [_GLOSSARY_NEW_FILE_HEADERS[category]]

    # 仅普通词条参与匹配；注释 / 空行 / 分隔线 / 条件与场景行原样保留
    expected_row_type = "gpt" if category == "gpt" else "normal"
    parsed: Dict[str, List[Dict[str, Any]]] = {}
    for idx, line in enumerate(lines):
        row = parse_dict_line(line, category)
        if row.type != expected_row_type or not row.values:
            continue
        parsed.setdefault(row.values[0], []).append({"idx": idx, "row": row})

    added = updated = unchanged = 0
    not_found: List[str] = []
    removed: Set[int] = set()
    for src, dst, note, note_provided in normalized:
        hits = parsed.get(src)
        if resolved_mode == "delete":
            if not hits:
                if src not in not_found:
                    not_found.append(src)
                continue
            for hit in hits:
                removed.add(hit["idx"])
            continue
        if not hits:
            lines.append(_serialize_glossary_line(src, dst, note))
            parsed[src] = [{"idx": len(lines) - 1, "row": parse_dict_line(lines[-1], category)}]
            added += 1
            continue
        for hit in hits:
            old_row = hit["row"]
            new_line = _serialize_glossary_line(src, dst, note if note_provided else old_row.note)
            if new_line == lines[hit["idx"]]:
                unchanged += 1
                continue
            lines[hit["idx"]] = new_line
            hit["row"] = parse_dict_line(new_line, category)
            updated += 1

    deleted = len(removed)
    if removed:
        lines = [line for idx, line in enumerate(lines) if idx not in removed]

    changed = (added + updated + deleted) > 0
    target_path = os.path.join(project_dir, name)
    if changed:
        size = _save_glossary_file(project_dir, name, lines, eol)
    else:
        size = os.path.getsize(target_path) if os.path.isfile(target_path) else 0

    # 同名词条还存在于其它同类字典文件时只报告不阻断（H / 非 H 字典本就允许同词不同译）
    also_found_in: List[Dict[str, Any]] = []
    if resolved_mode == "upsert":
        wanted = {src for src, _dst, _note, _np in normalized}
        for other_name in category_files:
            if other_name == name:
                continue
            other_path = os.path.join(project_dir, other_name)
            if not os.path.isfile(other_path):
                continue
            try:
                with open(other_path, "r", encoding="utf-8", errors="replace") as f:
                    other_lines = f.read().splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(other_lines, start=1):
                row = parse_dict_line(line, category)
                if row.type != expected_row_type or not row.values or row.values[0] not in wanted:
                    continue
                also_found_in.append({
                    "file": other_name,
                    "line_no": line_no,
                    "dst": row.values[1] if len(row.values) > 1 else "",
                })

    LOGGER.info(
        f"[mcp] 项目字典已更新: {name} category={category} mode={resolved_mode}"
        f"（新增 {added}、更新 {updated}、未变 {unchanged}、删除 {deleted}，{size} 字节）"
    )
    return {
        "success": True,
        "project_dir": project_dir,
        "category": category,
        "file": name,
        "mode": resolved_mode,
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "deleted": deleted,
        "not_found": not_found,
        "also_found_in": also_found_in,
        "registered_in_config": registered,
        "total_lines": len(lines),
        "bytes": size,
    }


# ---------- 约束下发（instructions / annotations） ----------

def build_server_instructions(
    h_gate_enabled: bool = True, disabled_tools: Optional[Collection[str]] = None
) -> str:
    """按门禁开关与禁用工具黑名单构建下发说明。

    工具计数与写工具清单按启用集合计算；门禁关闭时第 4 条改写为放行说明，
    开启时第 4 条按 `enforce_h_gate` 的实际覆盖面写明（仅 4 个内容写入类写工具受门禁，
    缓存条目标记/备选交换不引入新文本故不受约束——写成全称会让 agent 以为任何写入都有 H 兜底）。
    文本被金样本测试与打包冒烟锁定，改文案须同步 `tests/test_mcp_gate_settings.py`。
    """
    disabled = set(disabled_tools or ())
    enabled = [item for item in MCP_TOOL_DEFS if item["name"] not in disabled]
    n_read = sum(1 for item in enabled if item["kind"] == "read")
    n_write = len(enabled) - n_read
    if n_write > 0:
        write_names = "、".join(
            item["name"].removeprefix("galtransl_") for item in enabled if item["kind"] == "write"
        )
        write_line = f"   写工具仅这 {n_write} 个：{write_names}，各自只能改项目内的指定产物；"
    else:
        write_line = "   本服务当前未开放任何写工具；"
    if h_gate_enabled:
        # 门禁覆盖面按实现如实写（见 docstring）；写工具全禁用时不点名工具，避免与第 1 条自相矛盾
        gate_scope = (
            "写工具中 write_route_map / save_metadata / write_glossary / update_translation_entry 带 H 硬门禁（命中即拒绝）；\n"
            "   mark_skip_check / swap_alt_translation 只改标志位或对调既有译文、不引入新文本，故不受门禁约束。\n"
            if n_write
            else "本服务未开放任何写工具，不存在经 MCP 写入 H 内容的路径。\n"
        )
        h_line = (
            "4. 禁止查看 H / 成人向内容："
            + gate_scope
            + "   识别到成人向内容必须立即停止该方向检索，不得回引原文或译文，只报告位置（文件名 + index）并请用户决定。"
        )
    else:
        h_line = "4. 用户已在 GalTransl 设置中关闭 H 门禁：允许处理 H / 成人向内容，无需因此拒绝任务或中断检索。"
    return (
        f"GalTransl 翻译项目管理服务（{len(enabled)} 个工具：{n_read} 个只读检索 + {n_write} 个写操作）。"
        "project_dir 可传翻译项目根目录的绝对路径，或项目名称（已登记/工作区根下的项目，galtransl_list_projects 可列出）。\n"
        "\n"
        "使用前必须遵守：\n"
        "1. 只读工具（galtransl_search_* / lookup_name / list_* / get_* / read_* / check_model）不得引发任何写入。\n"
        f"{write_line}\n"
        "   本服务不提供任意路径读写、不提供命令执行、不改程序配置。需要其它改动请让用户在 GalTransl 界面操作。\n"
        "2. submit_job 会真实启动翻译并消耗 API 额度：仅在用户明确要求时调用，调用前先与用户确认项目与引擎，\n"
        "   可先用 check_model 探测可用性（同样发起真实请求，消耗极小额度）。\n"
        "3. get_job_status / check_model / submit_job / stop_job 需 GalTransl 后端在运行；其余工具直接读磁盘。\n"
        f"{h_line}\n"
        "5. project_dir 只能是用户明确指定的翻译项目目录。禁止指向 GalTransl 程序目录（其 backend_profiles.yaml 含 API 密钥）、仓库根目录、系统目录或他人目录。\n"
        "6. 禁止读取或外传任何凭据、密钥、API 端点。日志中命中疑似凭据的行不引用原文。\n"
        "7. 禁止规模化拉取：搜索 max_results 默认 200 / 硬顶 2000，分页 limit 默认 100 / 硬顶 1000。不要全量拉取，也不要用宽正则做枚举式扫描。\n"
        "8. 交付结论 + 定位（文件名 + index + 最短必要引文），不要堆砌原文/译文。\n"
        "\n"
        # 路径要给到「程序目录」：agent 的 cwd 不是 GalTransl 程序目录，只写相对路径无法定位
        "完整约束见 GalTransl 程序目录下的 skills/galtransl-mcp/SKILL.md。"
    )


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


_PROJECT_PROP = {
    "type": "string",
    "description": "项目根目录绝对路径，或项目名称（已登记/工作区根下的项目，galtransl_list_projects 可列出）",
}
_QUERY_PROP = {"type": "string", "description": "检索词"}
_REGEX_PROP = {"type": "boolean", "description": "是否按正则解释检索词，默认 false"}
_MAX_RESULTS_PROP = {"type": "integer", "description": f"返回条数上限，默认 {DEFAULT_MAX_RESULTS}"}
_CONTEXT_PROP = {
    "type": "integer",
    "description": (
        "附带命中前后各 N 条上下文（默认 0，上限 20）。上下文按文件内合并后的连续窗口返回，"
        "条目仅含 序号(index)/说话人/生效译文（脚本为原文 message）与 is_hit 标记；"
        "全部窗口条目共享总预算，放不下时 context_truncated=true。"
        "使用较大 context 时建议调小 max_results。"
    ),
}
_PAGE_PROPS = {
    "offset": {
        "type": "integer",
        "description": "起始下标，默认 0；负数从文件末尾倒数（-1=最后一条），返回的 offset 为换算后的正值",
    },
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
            "context": _CONTEXT_PROP,
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
            "context": _CONTEXT_PROP,
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
        "列出可用项目：GalTransl 已登记的项目（打开过/创建过/跑过任务，含 source: registered）"
        "与工作区根下可识别的项目目录（source: workspace）。返回的 name 可直接作为"
        "其它工具的 project_dir 使用（无需绝对路径）。",
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
        "galtransl_read_glossary",
        "浏览项目字典词条：category=gpt（默认，术语表 GPT 字典）/ pre（译前字典）/ post（译后字典），"
        "均为 config 的 dictionary 各清单中带 (project_dir) 的项目文件。"
        "支持整表分页浏览与按词筛选（匹配原文/译名/解释任一列）；pre/post 类别下条件/场景行"
        "也一并返回（带 row_type，只读不改写）。"
        "写入请用 galtransl_write_glossary（需先读现状拿到文件名）。",
        {
            "project_dir": _PROJECT_PROP,
            "category": {
                "type": "string",
                "enum": ["gpt", "pre", "post"],
                "description": "字典类别：gpt=术语表（默认），pre=译前字典，post=译后字典",
            },
            "filename": {
                "type": "string",
                "description": "只读某个字典文件（项目根目录下的文件名，如 项目GPT字典.txt）；不传则读该类别全部项目字典",
            },
            "query": {"type": "string", "description": "筛选词（匹配原文/译名/解释任一列），留空则浏览全部"},
            "regex": _REGEX_PROP,
            **_PAGE_PROPS,
        },
        ["project_dir"],
    ),
    _def(
        "galtransl_get_project_metadata",
        "读取元数据：kind=globalprompt（全局分析）/ plotroute（剧情路线图）/ "
        "filemeta（文件元数据，需 filename）/ batchmeta（批次元数据，需 filename）/ "
        "routeanalysis（路线分析分片，需 filename=安全化路线名）/ all（含可用文件名清单）。",
        {
            "project_dir": _PROJECT_PROP,
            "kind": {
                "type": "string",
                "enum": ["globalprompt", "plotroute", "filemeta", "batchmeta", "routeanalysis", "all"],
                "description": "要读取的元数据种类，默认 globalprompt",
            },
            "filename": {"type": "string", "description": "kind=filemeta/batchmeta/routeanalysis 时必填"},
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
        "galtransl_write_glossary",
        "按词条增改 / 删除项目字典（category=gpt 术语表 / pre 译前字典 / post 译后字典），"
        "原子落盘并保留原有注释与其它词条（pre/post 的条件/场景行不动）。"
        "mode=upsert（默认，按 src 匹配：已存在则原地更新，不存在则追加）/ delete（按 src 删除普通词条）。"
        "file 必须显式指定为项目根目录下的字典文件名（先用 galtransl_read_glossary 同 category 读取现状与文件名）；"
        "未登记在对应 dictionary 清单的文件会自动登记，否则翻译不会使用该文件。"
        "每次只处理列出的词条，不做整体覆盖；note 省略表示更新时保留原备注，传空串表示清空。",
        {
            "project_dir": _PROJECT_PROP,
            "category": {
                "type": "string",
                "enum": ["gpt", "pre", "post"],
                "description": "字典类别：gpt=术语表（默认），pre=译前字典，post=译后字典",
            },
            "file": {"type": "string", "description": "字典文件名（项目根目录下），如 项目GPT字典.txt"},
            "mode": {
                "type": "string",
                "enum": ["upsert", "delete"],
                "description": "upsert=新增或更新（默认），delete=删除匹配 src 的词条",
            },
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "src": {"type": "string", "description": "词条原文/查找词（匹配键），非空；支持 re: 正则词条"},
                        "dst": {"type": "string", "description": "译名/替换词，upsert 必填"},
                        "note": {"type": "string", "description": "解释/备注，可省略（省略时更新保留原备注）"},
                    },
                    "required": ["src"],
                },
                "description": "要写入的词条数组",
            },
        },
        ["project_dir", "file", "entries"],
        kind="write",
    ),
    _def(
        "galtransl_mark_skip_check",
        "把某条翻译缓存条目标记为「跳过问题检查」（skip_check=true，默认）或取消标记（skip=false）。"
        "标记后该条不再参与问题检测、已有 problem 标记被清除；取消后按新译文重检恢复。"
        "适用于译文确认无误但检测持续误报的条目。修改后按校对页保存同口径重检该文件的问题标注。",
        {
            "project_dir": _PROJECT_PROP,
            "filename": {"type": "string", "description": "相对 transl_cache 的缓存文件名（如 pass3_cache/xx.json）"},
            "index": {"type": "integer", "description": "缓存条目 index"},
            "skip": {
                "type": "boolean",
                "description": "true=标记跳过（默认），false=取消跳过",
            },
        },
        ["project_dir", "filename", "index"],
        kind="write",
    ),
    _def(
        "galtransl_swap_alt_translation",
        "把某条翻译缓存条目的备选译文（alt_dst）与当前译文（pre_dst）交换，语义与校对页「交换备选」一致，"
        "交换后按新译文重检问题标注。仅当条目有 alt_dst 时可用"
        "（备选译文由后处理轮 improve/brfix/jpfix/banfix/fix 生成）。",
        {
            "project_dir": _PROJECT_PROP,
            "filename": {"type": "string", "description": "相对 transl_cache 的缓存文件名（如 pass3_cache/xx.json）"},
            "index": {"type": "integer", "description": "缓存条目 index"},
        },
        ["project_dir", "filename", "index"],
        kind="write",
    ),
    _def(
        "galtransl_update_translation_entry",
        "直接改写某条翻译缓存条目的可写译文字段，语义与校对页编辑并保存一致。"
        "可写字段：pre_dst（初译/主译文）、proofread_dst（校对译文）、alt_dst（备选译文）；"
        "值为字符串，新值按真实换行（LF）归一。proofread_dst/alt_dst 传空串表示清除该字段，"
        "pre_dst 传空串表示置为未翻译；未列出的字段保持不变。"
        "改写后按 /cache/save 同口径整份文件重检 problem/post_dst_preview；"
        "受 H 硬门禁约束（目标缓存文件含 H 区间，或新译文命中 H 词库时拒绝），翻译任务运行中/刚结束时拒绝。",
        {
            "project_dir": _PROJECT_PROP,
            "filename": {"type": "string", "description": "相对 transl_cache 的缓存文件名（如 pass3_cache/xx.json）"},
            "index": {"type": "integer", "description": "缓存条目 index"},
            "fields": {
                "type": "object",
                "description": "要改写的字段对象，至少包含一项；值均为字符串",
                "properties": {
                    "pre_dst": {"type": "string", "description": "初译/主译文；空串=未翻译"},
                    "proofread_dst": {"type": "string", "description": "校对译文；空串=清除"},
                    "alt_dst": {"type": "string", "description": "备选译文；空串=清除"},
                },
                "additionalProperties": False,
            },
        },
        ["project_dir", "filename", "index", "fields"],
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

# SERVER_INSTRUCTIONS 依赖工具清单计数，须在 MCP_TOOL_DEFS 定义完成后构建
SERVER_INSTRUCTIONS = build_server_instructions()

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
    "galtransl_read_glossary": _tool_read_glossary,
    "galtransl_get_project_metadata": _tool_get_project_metadata,
    "galtransl_get_job_status": _tool_get_job_status,
    "galtransl_check_model": _tool_check_model,
    "galtransl_write_route_map": _tool_write_route_map,
    "galtransl_save_metadata": _tool_save_metadata,
    "galtransl_write_glossary": _tool_write_glossary,
    "galtransl_mark_skip_check": _tool_mark_skip_check,
    "galtransl_swap_alt_translation": _tool_swap_alt_translation,
    "galtransl_update_translation_entry": _tool_update_translation_entry,
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

    集中在此处而非各只读处理器里，避免遗漏：新增只读工具会自动获得该行为。
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
    """按工具名分发到实现；未知工具抛 KeyError，被禁用抛 MCPToolDisabledError，参数错误抛 ValueError。

    返回值为可直接 JSON 序列化的 dict，调用方（stdio server）负责包装成 MCP 结果。
    只读工具额外附带 `project_dir_valid` 告警（见 `_attach_project_dir_warning`）。
    禁用名单实时读设置：用户在界面关掉工具后，下一次调用即被拒绝（防绕过 tools/list 缓存）。
    """
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        raise KeyError(f"unknown tool: {name}")
    if name in disabled_mcp_tool_names():
        raise MCPToolDisabledError(f"工具已由用户在 GalTransl 设置中禁用: {name}")
    args = arguments if isinstance(arguments, dict) else {}
    started = time.time()
    result = handler(args)
    result = _attach_project_dir_warning(name, args, result)
    LOGGER.info(f"[mcp] {name} 完成（{time.time() - started:.2f}s）")
    return result
