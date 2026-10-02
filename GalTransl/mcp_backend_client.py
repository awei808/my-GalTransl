"""MCP 后端 HTTP 客户端：让 MCP 工具触达只在后端进程内存在的作业状态。

背景（0.6.0）：JobRegistry 是后端进程内的单例（server_jobs.py），MCP server 是
独立 stdio 进程，直接 import 只会拿到一个空壳。故作业域工具（提交/停止/状态查询/
模型探测）必须经 HTTP 回到后端。这是 0.5.1「不学上游打自家 REST」约定的一处**范围
克制**的例外：仅作业域工具走 HTTP，其余只读检索工具仍直接读磁盘、不经网络。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict

from GalTransl import LOGGER
from GalTransl.backend_security import load_api_token

DEFAULT_BACKEND_URL = "http://127.0.0.1:12333"
DEFAULT_TIMEOUT_SECONDS = 30.0


def backend_url() -> str:
    """后端基址；GALTRANSL_BACKEND_URL 可覆盖（后端经 --port 改端口时使用）。"""
    return (os.environ.get("GALTRANSL_BACKEND_URL") or DEFAULT_BACKEND_URL).rstrip("/")


def _request_json(method: str, path: str, payload: Dict[str, Any] | None, timeout: float) -> Dict[str, Any]:
    """向后端发一次 JSON 请求并返回解析后的响应体。

    写端点（POST/PUT/DELETE）在后端配置了 GALTRANSL_API_TOKEN 时需要 Bearer；
    GET 免鉴权，带上也无害，统一附加以少一分口径分歧。

    Raises:
        RuntimeError: 后端未启动 / 超时 / 返回非 2xx 时抛出，message 为面向 agent 的
            中文说明（附后端响应体的 error 字段，便于 agent 转告用户）。
    """
    url = f"{backend_url()}{path}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    token = load_api_token()
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = _extract_error(exc.read())
        raise RuntimeError(f"后端返回 {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        # Windows 上连接被拒时 urlopen 给出的 reason 可能显示为 "timed out"，故不直接断言原因
        raise RuntimeError(
            f"无法连接 GalTransl 后端（{backend_url()}）：{exc.reason}。"
            "请确认 GalTransl 程序已启动、端口与 GALTRANSL_BACKEND_URL 一致；"
            "该工具依赖后端进程内的作业状态，无法离线执行。"
        ) from exc
    except TimeoutError as exc:
        raise RuntimeError(f"等待后端响应超时（{timeout:.0f}s）：{url}") from exc
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"后端响应不是合法 JSON：{raw[:200]}") from exc
    LOGGER.debug(f"[mcp] {method} {path} -> {type(parsed).__name__}")
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def post_json(path: str, payload: Dict[str, Any], timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """向后端 POST 一个 JSON 并返回解析后的响应体（见 _request_json 的错误口径）。"""
    return _request_json("POST", path, payload, timeout)


def get_json(path: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """向后端 GET 一个 JSON 并返回解析后的响应体（见 _request_json 的错误口径）。"""
    return _request_json("GET", path, None, timeout)


def _extract_error(raw: bytes) -> str:
    """从后端错误响应体里取出 error 字段；取不到则回退原文截断。"""
    text = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text[:200] or "(空响应)"
    if isinstance(parsed, dict) and parsed.get("error"):
        return str(parsed["error"])
    return text[:200]
