"""MCP 进程心跳：外部 agent 连接状态的跨进程探测（0.5.1）。

MCP 服务是被客户端按需拉起的**独立 stdio 进程**，后端进程无法直接感知其存活。
故由 MCP 侧定期刷新心跳文件、退出时删除；后端只读该文件判断
「当前是否有外部 agent 连着」，供前端指示灯使用。

心跳落在程序目录（与 `app_settings.json` / `backend_profiles.yaml` 同口径，
经 `Utils.resolve_app_dir()`），使源码模式与打包版都能被后端读到同一路径。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from GalTransl import LOGGER
from GalTransl.Utils import resolve_app_dir

HEARTBEAT_FILENAME = "mcp_status.json"
# 心跳刷新间隔（MCP 侧）与判定过期阈值（后端侧）。阈值取间隔的 3 倍，容忍抖动。
HEARTBEAT_INTERVAL_SECONDS = 30.0
HEARTBEAT_STALE_SECONDS = 90.0


def heartbeat_path() -> str:
    """心跳文件绝对路径（程序目录下）。"""
    return os.path.join(resolve_app_dir(), HEARTBEAT_FILENAME)


def write_heartbeat(tools_count: int = 0) -> bool:
    """写入/刷新心跳；失败返回 False（心跳不可用不应影响 MCP 服务本身）。"""
    path = heartbeat_path()
    payload = {
        "pid": os.getpid(),
        "tools": int(tools_count),
        "started_at": _read_started_at(path) or _now_iso(),
        "updated_at": _now_iso(),
    }
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        # 原子替换，避免后端读到写了一半的内容
        os.replace(tmp_path, path)
        return True
    except Exception as exc:
        LOGGER.warning(f"[mcp] 心跳写入失败 {path}: {exc}")
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return False


def remove_heartbeat() -> None:
    """删除心跳文件（MCP 进程正常退出时调用）。"""
    path = heartbeat_path()
    for target in (path, path + ".tmp"):
        try:
            if os.path.isfile(target):
                os.remove(target)
        except OSError as exc:
            LOGGER.debug(f"[mcp] 心跳清理失败 {target}: {exc}")


def read_heartbeat(now: Optional[float] = None) -> Dict[str, Any]:
    """读取心跳并判定是否新鲜，返回可直接给前端用的状态对象。

    Returns:
        {"available": bool, "path": str, "age_seconds": float|None, ...}；
        文件不存在或已过期时 available 为 False，其余字段尽力填充。
    """
    path = heartbeat_path()
    result: Dict[str, Any] = {
        "available": False,
        "path": path,
        "pid": None,
        "tools": 0,
        "started_at": "",
        "updated_at": "",
        "age_seconds": None,
        "stale_after_seconds": HEARTBEAT_STALE_SECONDS,
    }
    if not os.path.isfile(path):
        return result
    current = time.time() if now is None else now
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return result
        result["pid"] = payload.get("pid")
        result["tools"] = payload.get("tools", 0)
        result["started_at"] = str(payload.get("started_at", ""))
        result["updated_at"] = str(payload.get("updated_at", ""))
        age = max(0.0, current - os.path.getmtime(path))
        result["age_seconds"] = round(age, 1)
        result["available"] = age <= HEARTBEAT_STALE_SECONDS
    except Exception as exc:
        LOGGER.warning(f"[mcp] 心跳读取失败 {path}: {exc}")
    return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_started_at(path: str) -> str:
    """复用已有心跳的 started_at，使定期刷新不覆盖首次启动时间。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            return str(payload.get("started_at", ""))
    except Exception:
        pass
    return ""
