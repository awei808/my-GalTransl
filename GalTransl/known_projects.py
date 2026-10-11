"""已知项目登记表：项目名称 → 绝对路径的持久化映射。

后端在三个天然入口登记项目：项目级 API 命中（route_project_api 入口）、项目创建
（/api/projects/init）、翻译任务提交（JobRegistry.submit）。登记文件落在程序目录
known_projects.json（与 app_settings.json 同目录）：MCP 是独立进程，直接读该文件
即可离线把项目名解析为绝对路径，不要求项目位于工作区根下、也不依赖后端在运行。
登记内容仅含目录路径与配置文件名，不含任何凭据或译文数据。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from GalTransl import LOGGER
from GalTransl.ConfigHelper import detect_config_file
from GalTransl.Utils import resolve_app_dir

KNOWN_PROJECTS_FILENAME = "known_projects.json"
_MAX_KEPT_PROJECTS = 200
# 同一项目的登记节流窗口：窗口内重复命中不产生 IO（项目级 API 会被前端高频轮询）
_RECORD_THROTTLE_SECONDS = 60.0

_LOCK = threading.Lock()
# 进程内节流缓存：规整路径 -> 上次实际落盘的 monotonic 时间戳
_LAST_RECORDED: Dict[str, float] = {}


def known_projects_path() -> str:
    """登记文件路径：程序目录 known_projects.json；GALTRANSL_KNOWN_PROJECTS_PATH 可覆盖（测试/隔离用）。"""
    override = (os.environ.get("GALTRANSL_KNOWN_PROJECTS_PATH") or "").strip()
    if override:
        return os.path.normpath(override)
    return os.path.join(resolve_app_dir(), KNOWN_PROJECTS_FILENAME)


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalize_dir(project_dir: str) -> str:
    return os.path.normpath(str(project_dir or "").strip())


def load_known_projects() -> List[Dict[str, Any]]:
    """读取登记表（文件不存在/损坏返回空列表）；按 last_seen 新→旧排序。"""
    path = known_projects_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        LOGGER.warning(f"[known-projects] 登记文件无法解析，忽略: {path}")
        return []
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, list):
        return []
    valid: List[Dict[str, Any]] = []
    for item in projects:
        if not isinstance(item, dict):
            continue
        project_dir = _normalize_dir(item.get("project_dir", ""))
        name = str(item.get("name", "") or "").strip()
        if not project_dir or not name:
            continue
        valid.append({
            "project_dir": project_dir,
            "name": name,
            "config_file_name": str(item.get("config_file_name", "") or ""),
            "last_seen": str(item.get("last_seen", "") or ""),
        })
    valid.sort(key=lambda p: p["last_seen"], reverse=True)
    return valid


def record_known_project(project_dir: str, config_file_name: str = "") -> bool:
    """登记 / 刷新一个已知项目；返回是否实际落盘（任何失败静默返回 False）。

    全程持锁：ThreadingHTTPServer 多线程下避免并发读改写丢条目、写同一 .tmp 互相截断。
    节流：同一目录在窗口内的重复登记直接返回；时间戳在成功落盘后才刷新，
    写失败（如目标文件被 MCP 进程占用导致 sharing violation）时窗口内可重试。
    仅登记真实存在的可识别项目（config 文件确认存在）；失效项目由读取侧过滤。
    """
    norm = _normalize_dir(project_dir)
    if not norm:
        return False
    now = time.monotonic()
    with _LOCK:
        last = _LAST_RECORDED.get(norm)
        if last is not None and (now - last) < _RECORD_THROTTLE_SECONDS:
            return False
        if not os.path.isdir(norm):
            return False
        try:
            if not config_file_name:
                config_file_name = detect_config_file(norm)
            if not os.path.isfile(os.path.join(norm, config_file_name)):
                return False
            projects = [
                p for p in load_known_projects()
                if _normalize_dir(p["project_dir"]) != norm
            ]
            projects.insert(0, {
                "project_dir": norm,
                "name": os.path.basename(norm),
                "config_file_name": config_file_name,
                "last_seen": _now_iso_utc(),
            })
            projects = projects[:_MAX_KEPT_PROJECTS]
            path = known_projects_path()
            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "projects": projects}, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception as exc:
            LOGGER.debug(f"[known-projects] 登记失败（忽略）: {norm}: {exc}")
            return False
        _LAST_RECORDED[norm] = now
        return True
