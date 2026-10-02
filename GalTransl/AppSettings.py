from __future__ import annotations

import json
import os
from typing import Any

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


def save_app_settings(settings: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_settings(settings)
    tmp_path = _SETTINGS_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, _SETTINGS_PATH)
    return normalized
