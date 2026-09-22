"""检索层：缓存条目 / 原始脚本 / 字典词条的只读检索（0.5.1 MCP 外部 agent 接入）。

缓存检索主体自 server_handlers_project 的 POST /cache/search 抽出，供该端点与
MCP 工具层共用同一份实现（避免口径漂移）；脚本原文与字典检索为本模块新增。
本模块只读、无副作用；所有入口均要求显式传入 project_dir（不依赖进程 cwd）。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

from GalTransl import CACHE_FOLDERNAME, INPUT_FOLDERNAME, LOGGER
from GalTransl.Dictionary import DictRow, parse_dict_line
from GalTransl.server_cache import _collect_cache_files
from GalTransl.server_dict import (
    DICT_PROJECT_MARKER,
    _collect_common_dict_payload,
    _collect_project_dict_payload,
    _common_dict_directory,
)

DEFAULT_MAX_RESULTS = 200
HARD_MAX_RESULTS = 2000

# 字典载荷的列表键 -> parse_dict_line 类别（决定该文件行的解析分支）。
# GPT 字典的 h/非h 细分必须先于 gpt_dict_files 登记，否则会被兜底映射成 "gpt" 而丢失粒度。
_DICT_CATEGORY_BY_LIST = (
    ("gpt_dict_files_h", "gpth"),
    ("gpt_dict_files_nh", "gptnh"),
    ("pre_dict_files", "pre"),
    ("gpt_dict_files", "gpt"),
    ("post_dict_files", "post"),
    ("forbidden_dict_files_h", "forbiddenh"),
    ("forbidden_dict_files_nh", "forbiddennh"),
)


def _clamp_max_results(value: Any) -> int:
    """把调用方给的 max_results 收敛到 [0, HARD_MAX_RESULTS]；非数值回落默认。

    0 与负数一律返回 0（表示不返回结果），与旧 /cache/search 端点
    `min(int(payload.get("max_results", 500)), 2000)` 的口径保持一致。
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_RESULTS
    if parsed < 0:
        return 0
    return min(parsed, HARD_MAX_RESULTS)


def _make_matcher(query: str, use_regex: bool) -> Callable[[str], bool]:
    """构造单文本匹配函数；正则模式下编译失败抛 re.error 由调用方处置。"""
    if use_regex:
        pattern = re.compile(query)
        return lambda text: bool(pattern.search(text))
    lowered = query.lower()
    return lambda text: lowered in text.lower()


def _empty_result(scope: str, query: str) -> Dict[str, Any]:
    return {"scope": scope, "query": query, "results": [], "total": 0, "truncated": False}


def _load_json_array(file_path: str) -> List[Any]:
    """读取 JSON 数组文件；顶层不是数组时返回空列表。"""
    with open(file_path, "rb") as f:
        data = json.loads(f.read().decode("utf-8"))
    return data if isinstance(data, list) else []


def _speaker_of(raw_name: Any) -> str:
    """说话人字段归一化：names 列表按空格拼接，其余转字符串。"""
    if isinstance(raw_name, list):
        return " ".join(str(item) for item in raw_name)
    return str(raw_name) if raw_name else ""


def search_cache_entries(
    project_dir: str,
    query: str,
    field: str = "all",
    use_regex: bool = False,
    max_results: Any = DEFAULT_MAX_RESULTS,
) -> Dict[str, Any]:
    """跨文件检索已入库的翻译缓存条目。

    字段口径与 POST /cache/search 完全一致：原文取 pre_src/post_src/post_jp/pre_jp
    首个非空，译文取 pre_dst/pre_zh/proofread_dst/proofread_zh 首个非空，说话人取
    name/names（列表按空格拼接后参与匹配，但结果中的 speaker 保留原值，与前端一致）。

    Args:
        project_dir: 项目根目录绝对路径。
        query: 检索词（非空才检索）。
        field: all / src / dst / problem 之一；all 时四类字段任一命中即计入。
        use_regex: True 时 query 按正则解释，编译失败抛 re.error。
        max_results: 返回条数上限，收敛到 [1, HARD_MAX_RESULTS]。

    Returns:
        {"scope", "query", "results", "total", "truncated"}；total 为全部命中数，
        results 最多 max_results 条，超出时 truncated 为 True。
    """
    query = str(query or "").strip()
    if not query:
        return _empty_result("cache", query)
    limit = _clamp_max_results(max_results)
    matcher = _make_matcher(query, use_regex)
    started = time.time()

    cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
    results: List[Dict[str, Any]] = []
    total = 0
    skipped = 0
    if os.path.isdir(cache_dir):
        for rel in _collect_cache_files(cache_dir):
            file_path = os.path.join(cache_dir, rel)
            try:
                entries = _load_json_array(file_path)
            except Exception as exc:
                skipped += 1
                LOGGER.warning(f"search/cache 跳过无法解析的缓存文件 {rel}: {exc}")
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                # 可见文本对齐页面渲染：原文行显示 pre_src（post_src 为更底层原文，页面不展示）
                src_text = (
                    entry.get("pre_src", "")
                    or entry.get("post_src", "")
                    or entry.get("post_jp", "")
                    or entry.get("pre_jp", "")
                )
                dst_text = (
                    entry.get("pre_dst", "")
                    or entry.get("pre_zh", "")
                    or entry.get("proofread_dst", "")
                    or entry.get("proofread_zh", "")
                )
                problem_text = entry.get("problem", "")
                raw_name = entry.get("name", "") or entry.get("names", "")
                speaker_text = _speaker_of(raw_name)
                match_src = bool(matcher(src_text))
                match_dst = bool(matcher(dst_text))
                match_problem = bool(matcher(problem_text))
                match_speaker = bool(speaker_text) and bool(matcher(speaker_text))
                if field == "src" and not match_src:
                    continue
                if field == "dst" and not match_dst:
                    continue
                if field == "problem" and not match_problem:
                    continue
                if field == "all" and not (match_src or match_dst or match_problem or match_speaker):
                    continue
                total += 1
                if len(results) < limit:
                    results.append({
                        "filename": rel,
                        "index": entry.get("index", 0),
                        "speaker": raw_name,
                        "post_src": src_text,
                        "pre_dst": dst_text,
                        "match_src": match_src,
                        "match_dst": match_dst,
                        "match_problem": match_problem,
                        "match_speaker": match_speaker,
                        "problem": entry.get("problem", ""),
                        "trans_by": entry.get("trans_by", ""),
                    })
    truncated = total > len(results)
    LOGGER.info(
        f"search/cache 完成: query={query!r} field={field} 命中 {total} 条"
        f"（返回 {len(results)} 条{'，已截断' if truncated else ''}，耗时 {time.time() - started:.2f}s）"
    )
    return {
        "scope": "cache",
        "query": query,
        "results": results,
        "total": total,
        "truncated": truncated,
        "skipped_files": skipped,
    }


def _iter_script_files(input_dir: str) -> List[str]:
    """递归收集原始脚本 JSON（相对 input_dir 的 '/' 路径）。"""
    files: List[str] = []
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            if not name.endswith(".json"):
                continue
            files.append(os.path.relpath(os.path.join(root, name), input_dir).replace("\\", "/"))
    return sorted(files)


def search_source_scripts(
    project_dir: str,
    query: str,
    use_regex: bool = False,
    max_results: Any = DEFAULT_MAX_RESULTS,
) -> Dict[str, Any]:
    """检索原始脚本（gt_input 下的 name-message JSON），不依赖是否已生成缓存。

    条目结构为 {"message": 原文, "name"/"names": 说话人可选}，index 即数组下标
    （与翻译缓存的 index 同源，可直接对照缓存条目）。

    Returns:
        {"scope", "query", "results", "total", "truncated"}；results 条目含
        filename / index / speaker / message / match_speaker。
    """
    query = str(query or "").strip()
    if not query:
        return _empty_result("script", query)
    limit = _clamp_max_results(max_results)
    matcher = _make_matcher(query, use_regex)
    started = time.time()

    input_dir = os.path.join(project_dir, INPUT_FOLDERNAME)
    results: List[Dict[str, Any]] = []
    total = 0
    skipped = 0
    if os.path.isdir(input_dir):
        for rel in _iter_script_files(input_dir):
            file_path = os.path.join(input_dir, rel)
            try:
                entries = _load_json_array(file_path)
            except Exception as exc:
                skipped += 1
                LOGGER.warning(f"search/script 跳过无法解析的脚本文件 {rel}: {exc}")
                continue
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                message = str(entry.get("message", "") or "")
                raw_name = entry.get("name", "") or entry.get("names", "")
                speaker_text = _speaker_of(raw_name)
                match_message = bool(matcher(message))
                match_speaker = bool(speaker_text) and bool(matcher(speaker_text))
                if not (match_message or match_speaker):
                    continue
                total += 1
                if len(results) < limit:
                    results.append({
                        "filename": rel,
                        "index": entry.get("index", index),
                        "speaker": raw_name,
                        "message": message,
                        "match_message": match_message,
                        "match_speaker": match_speaker,
                    })
    truncated = total > len(results)
    LOGGER.info(
        f"search/script 完成: query={query!r} 命中 {total} 条"
        f"（返回 {len(results)} 条{'，已截断' if truncated else ''}，耗时 {time.time() - started:.2f}s）"
    )
    return {
        "scope": "script",
        "query": query,
        "results": results,
        "total": total,
        "truncated": truncated,
        "skipped_files": skipped,
    }


def _dict_file_categories(payload: Dict[str, Any]) -> Dict[str, str]:
    """file_key -> parse_dict_line 类别；同一文件只取首个命中的配置键。"""
    mapping: Dict[str, str] = {}
    for list_key, category in _DICT_CATEGORY_BY_LIST:
        for file_key in payload.get(list_key) or []:
            mapping.setdefault(str(file_key), category)
    return mapping


def _dict_row_words(row: DictRow) -> Dict[str, str]:
    """取字典行的「检索词 / 替换词」，按行类型区分（与引擎 load_dic 口径一致）。"""
    values = list(row.values)
    if row.type == "conditional":
        src = values[2] if len(values) > 2 else ""
        dst = values[3] if len(values) > 3 else ""
    elif row.type == "situation":
        src = values[1] if len(values) > 1 else ""
        dst = values[2] if len(values) > 2 else ""
    else:
        src = values[0] if len(values) > 0 else ""
        dst = values[1] if len(values) > 1 else ""
    return {"src": src, "dst": dst}


def _iter_dict_sources(
    project_dir: str,
    config_name: str,
    include_common: bool,
    common_dict_dir: Optional[str],
) -> List[Dict[str, Any]]:
    """汇总待检索的字典文件（项目字典 + 可选公共字典）。

    每项为 {"origin", "file", "category", "lines"}。公共字典目录不存在时直接跳过，
    不调用载荷函数——`_collect_common_dict_payload` 内含 os.makedirs，只读检索不应建目录。
    """
    sources: List[Dict[str, Any]] = []

    def _append(payload: Dict[str, Any], origin: str, strip_marker: bool) -> None:
        categories = _dict_file_categories(payload)
        for file_key, info in (payload.get("dict_contents") or {}).items():
            if not isinstance(info, dict) or info.get("error"):
                continue
            name = str(file_key)
            # 项目字典的键带 (project_dir) 前缀，公共字典的键即文件名
            if strip_marker:
                name = name.replace(DICT_PROJECT_MARKER, "").strip()
            sources.append({
                "origin": origin,
                "file": name,
                "category": categories.get(str(file_key), "pre"),
                "lines": info.get("lines") or [],
            })

    _append(_collect_project_dict_payload(project_dir, config_name), "project", True)
    if include_common:
        target_dir = common_dict_dir if common_dict_dir is not None else _common_dict_directory()
        if os.path.isdir(target_dir):
            _append(_collect_common_dict_payload(target_dir), "common", False)
    return sources


def search_dict_entries(
    project_dir: str,
    query: str,
    direction: str = "any",
    use_regex: bool = False,
    config_name: str = "config.yaml",
    max_results: Any = DEFAULT_MAX_RESULTS,
    include_common: bool = True,
    common_dict_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """检索字典词条（项目字典 + 公共 Dict），用于核查术语译法一致性。

    项目字典取自 config 的 dictionary 配置（译前 / GPT / 译后 / 禁用词），
    公共字典取自程序目录下的 Dict/（按文件名或 .category_map.json 分类）。
    按行解析后区分检索词与替换词；注释行与空行不参与匹配。

    Args:
        direction: any（双向）/ jp2zh（只匹配原文列）/ zh2jp（只匹配译文列）。
        include_common: 是否一并检索公共字典。
        common_dict_dir: 公共字典目录；None 时取默认（程序目录下 Dict/）。

    Returns:
        {"scope", "query", "results", "total", "truncated"}；results 条目含
        origin（project/common）/ file / category / line_no / src / dst /
        note / row_type / is_regex / match_src / match_dst。
    """
    query = str(query or "").strip()
    if not query:
        return _empty_result("dict", query)
    if direction not in ("any", "jp2zh", "zh2jp"):
        raise ValueError(f"unsupported direction: {direction}")
    limit = _clamp_max_results(max_results)
    matcher = _make_matcher(query, use_regex)
    started = time.time()

    results: List[Dict[str, Any]] = []
    total = 0
    for source in _iter_dict_sources(project_dir, config_name, include_common, common_dict_dir):
        category = source["category"]
        for line_no, line in enumerate(source["lines"], start=1):
            row = parse_dict_line(line, category)
            if row.type in ("blank", "comment"):
                continue
            words = _dict_row_words(row)
            match_src = bool(words["src"]) and bool(matcher(words["src"]))
            match_dst = bool(words["dst"]) and bool(matcher(words["dst"]))
            if direction == "jp2zh" and not match_src:
                continue
            if direction == "zh2jp" and not match_dst:
                continue
            if direction == "any" and not (match_src or match_dst):
                continue
            total += 1
            if len(results) < limit:
                results.append({
                    "origin": source["origin"],
                    "file": source["file"],
                    "category": category,
                    "line_no": line_no,
                    "src": words["src"],
                    "dst": words["dst"],
                    "note": row.note,
                    "row_type": row.type,
                    "is_regex": row.is_regex,
                    "match_src": match_src,
                    "match_dst": match_dst,
                })
    truncated = total > len(results)
    LOGGER.info(
        f"search/dict 完成: query={query!r} direction={direction} 命中 {total} 条"
        f"（返回 {len(results)} 条{'，已截断' if truncated else ''}，耗时 {time.time() - started:.2f}s）"
    )
    return {
        "scope": "dict",
        "query": query,
        "results": results,
        "total": total,
        "truncated": truncated,
    }
