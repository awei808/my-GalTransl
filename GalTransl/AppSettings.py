from __future__ import annotations

import json
import os
from typing import Any

from GalTransl import LOGGER
from GalTransl.Utils import resolve_app_dir


DEFAULT_APP_SETTINGS: dict[str, Any] = {
    "printTranslationLogInTerminal": True,
    "maxConcurrentJobs": 4,
    "writeApiCallLog": False,
    "writeFrontendLog": False,
    "mcpHGateEnabled": True,
    "mcpDisabledTools": [],
}

# 程序目录口径统一走 resolve_app_dir：打包版 __file__ 指向临时解包目录，
# 会让设置写到 %TEMP%\_MEIxxxx（重启即丢）
_SETTINGS_PATH = os.path.join(resolve_app_dir(), "app_settings.json")


def _normalize_settings(data: dict[str, Any] | None) -> dict[str, Any]:
    source = data or {}
    # 显式 null 视为未设置（bool(None)=False 会静默关门禁，安全语义上不可接受）
    gate_raw = source.get("mcpHGateEnabled")
    return {
        "printTranslationLogInTerminal": bool(
            source.get(
                "printTranslationLogInTerminal",
                DEFAULT_APP_SETTINGS["printTranslationLogInTerminal"],
            )
        ),
        "maxConcurrentJobs": max(1, int(
            source.get(
                "maxConcurrentJobs",
                DEFAULT_APP_SETTINGS["maxConcurrentJobs"],
            )
        )),
        "writeApiCallLog": bool(
            source.get(
                "writeApiCallLog",
                DEFAULT_APP_SETTINGS["writeApiCallLog"],
            )
        ),
        "writeFrontendLog": bool(
            source.get(
                "writeFrontendLog",
                DEFAULT_APP_SETTINGS["writeFrontendLog"],
            )
        ),
        # MCP 门禁设置：H 门禁开关与禁用的工具名黑名单（MCP 独立进程读同一份文件）
        "mcpHGateEnabled": bool(
            DEFAULT_APP_SETTINGS["mcpHGateEnabled"] if gate_raw is None else gate_raw
        ),
        "mcpDisabledTools": _normalize_disabled_tools(
            source.get(
                "mcpDisabledTools",
                DEFAULT_APP_SETTINGS["mcpDisabledTools"],
            )
        ),
    }


def _normalize_disabled_tools(value: Any) -> list[str]:
    """规整禁用工具名列表：仅保留非空字符串项，去空白并去重；非列表回退空。"""
    if not isinstance(value, list):
        return []
    seen: dict[str, None] = {}
    for item in value:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if name:
            seen[name] = None
    return list(seen)


def load_app_settings() -> dict[str, Any]:
    if not os.path.isfile(_SETTINGS_PATH):
        return dict(DEFAULT_APP_SETTINGS)
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(DEFAULT_APP_SETTINGS)
        return _normalize_settings(data)
    except Exception:
        return dict(DEFAULT_APP_SETTINGS)


def _log_api_call_log_switch(
    previous: dict[str, Any],
    current: dict[str, Any],
    payload: Any,
    source: str,
) -> None:
    """记录 writeApiCallLog 的每次写盘（本模块是该键唯一的落盘路径）。

    额外记录 payload 是否携带该键：缺键会经 _normalize_settings 回落为默认 false，
    日志据此区分「显式写入」与「缺键回落」，便于排查开关被静默改回 false。
    """
    old = previous.get("writeApiCallLog", DEFAULT_APP_SETTINGS["writeApiCallLog"])
    new = current["writeApiCallLog"]
    explicit = isinstance(payload, dict) and "writeApiCallLog" in payload
    detail = f"来源={source or '未知'}, 写盘数据含该键={explicit}"
    if old == new:
        LOGGER.debug(f"[AppSettings] writeApiCallLog 写盘（值未变）：{new}（{detail}）")
    else:
        LOGGER.info(f"[AppSettings] writeApiCallLog 变更：{old} -> {new}（{detail}）")


def save_app_settings(settings: dict[str, Any], source: str = "") -> dict[str, Any]:
    # 变更前值取自落盘文件（读取失败时按默认值口径），供开关变更日志比对
    previous = load_app_settings()
    normalized = _normalize_settings(settings)
    tmp_path = _SETTINGS_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, _SETTINGS_PATH)
    _log_api_call_log_switch(previous, normalized, settings, source)
    return normalized
