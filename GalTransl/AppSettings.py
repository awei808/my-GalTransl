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

# 写盘日志里的值截断长度（mcpDisabledTools 等列表可能很长）
_LOGGED_VALUE_MAX = 80


def _raw_or_default(source: dict[str, Any], key: str) -> Any:
    """取键的原始值；缺键与显式 null 同口径，一律回落该键的默认值。

    null 不能走 bool()/int()：`bool(None)=False` 会静默关掉默认开启的开关
    （mcpHGateEnabled / printTranslationLogInTerminal），`int(None)` 抛 TypeError
    会让 PUT 变 500。因此所有键统一在这里归一。
    """
    raw = source.get(key)
    return DEFAULT_APP_SETTINGS[key] if raw is None else raw


def _normalize_settings(data: dict[str, Any] | None) -> dict[str, Any]:
    source = data or {}
    return {
        "printTranslationLogInTerminal": bool(
            _raw_or_default(source, "printTranslationLogInTerminal")
        ),
        "maxConcurrentJobs": max(1, int(_raw_or_default(source, "maxConcurrentJobs"))),
        "writeApiCallLog": bool(_raw_or_default(source, "writeApiCallLog")),
        "writeFrontendLog": bool(_raw_or_default(source, "writeFrontendLog")),
        # MCP 门禁设置：H 门禁开关与禁用的工具名黑名单（MCP 独立进程读同一份文件）
        "mcpHGateEnabled": bool(_raw_or_default(source, "mcpHGateEnabled")),
        "mcpDisabledTools": _normalize_disabled_tools(
            _raw_or_default(source, "mcpDisabledTools")
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


def _fmt_setting_value(value: Any) -> str:
    """日志用值格式化：长值截断，避免一条日志刷屏。"""
    text = str(value)
    return text if len(text) <= _LOGGED_VALUE_MAX else text[: _LOGGED_VALUE_MAX - 3] + "..."


def _log_settings_write(
    previous: dict[str, Any],
    current: dict[str, Any],
    payload: Any,
    source: str,
) -> None:
    """记录本次写盘（本模块是 app_settings.json 唯一的落盘路径）。

    设置是**整份覆写**：请求缺键、或显式 null，都会回落默认值。故除变更留痕外，对
    「请求未携带却被改动」的键额外报警——writeApiCallLog 被清回 false 只是其中一例，
    mcpDisabledTools 被清空更等于把用户已禁用的写工具重新启用。
    """
    changed: dict[str, tuple[Any, Any]] = {}
    for key in DEFAULT_APP_SETTINGS:
        old = previous.get(key, DEFAULT_APP_SETTINGS[key])
        new = current.get(key, DEFAULT_APP_SETTINGS[key])
        if old != new:
            changed[key] = (old, new)
    keys = sorted(map(str, payload)) if isinstance(payload, dict) else type(payload).__name__
    detail = f"来源={source or '未知'}, 请求键={keys}"
    if not changed:
        LOGGER.debug(f"[AppSettings] 设置写盘（值未变）（{detail}）")
        return
    LOGGER.info(
        "[AppSettings] 设置变更："
        + ", ".join(
            f"{key}: {_fmt_setting_value(old)} -> {_fmt_setting_value(new)}"
            for key, (old, new) in changed.items()
        )
        + f"（{detail}）"
    )
    # 缺键与显式 null 同口径（见 _raw_or_default）：都静默回落默认值
    silent = {
        key: (old, new)
        for key, (old, new) in changed.items()
        if not (isinstance(payload, dict) and payload.get(key) is not None)
    }
    if silent:
        LOGGER.warning(
            "[AppSettings] 请求未携带却被改动的设置键："
            + ", ".join(
                f"{key}: {_fmt_setting_value(old)} -> {_fmt_setting_value(new)}"
                for key, (old, new) in silent.items()
            )
            + f"（{detail}）：本文件为整份覆写，缺键/显式 null 一律回落默认值，"
            "疑似调用方只发了部分字段，请检查其是否先读取再合并"
        )


def save_app_settings(settings: dict[str, Any], source: str = "") -> dict[str, Any]:
    # 变更前值取自落盘文件（读取失败时按默认值口径），供写盘留痕/报警比对
    previous = load_app_settings()
    normalized = _normalize_settings(settings)
    tmp_path = _SETTINGS_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, _SETTINGS_PATH)
    try:
        _log_settings_write(previous, normalized, settings, source)
    except Exception:
        # 留痕/报警本身失败不得反噬：设置已落盘，异常会让 PUT 变 500 语义矛盾
        LOGGER.warning("AppSettings 写盘留痕失败（设置已落盘）", exc_info=True)
    return normalized
