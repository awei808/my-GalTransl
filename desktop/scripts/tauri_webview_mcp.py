"""
Tauri WebView2 MCP Server

通过 Playwright CDP 直连 Tauri 应用的 WebView2，提供浏览器自动化能力。
用于 CodeBuddy AI 自动捕获前端报错、检查页面结构。
连接断开后（关闭/重启 Tauri 窗口）会自动清理并重连，无需重启 MCP 服务。

用法:
  python desktop/scripts/tauri_webview_mcp.py [--cdp-port PORT]

前置条件:
  1. 启动 Tauri 应用前设置环境变量:
     set WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222
  2. 然后启动 Tauri 应用 (npm run tauri:dev)
"""

import asyncio
import json
import sys
import tempfile
import argparse
from typing import Any

from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import Tool, CallToolResult, CallToolRequestParams, ListToolsResult, TextContent
from playwright.async_api import async_playwright


# 全局状态
_page = None
_browser = None
_playwright = None
_console_messages = []
_network_requests = []
_connected = False
_connect_lock = None      # asyncio.Lock：串行化连接/重连，避免竞态重复建连
_connected_event = None   # asyncio.Event：连接就绪信号，工具调用等待它
_CDP_PORT = 9222          # 当前 CDP 端口（用于错误提示）
_TOOL_WAIT_TIMEOUT = 30.0 # 工具调用在未连接时等待重连的超时秒数


async def _connect_until_success(cdp_port: int, delay: float = 1.5) -> None:
    """
    通过 CDP 连接 Tauri WebView2，直到成功为止（调用方须持有 _connect_lock）。
    每次（重）连接都重新注册控制台/网络监听并清空历史缓存。
    """
    global _page, _browser, _playwright, _connected
    attempt = 0
    while True:
        attempt += 1
        p = None
        try:
            p = await async_playwright().start()
            browser = await p.chromium.connect_over_cdp(
                f"http://127.0.0.1:{cdp_port}"
            )

            # 获取或创建页面
            contexts = browser.contexts
            if contexts and contexts[0].pages:
                page = contexts[0].pages[0]
            else:
                ctx = contexts[0] if contexts else await browser.new_context()
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()

            # 注册控制台消息监听
            _console_messages.clear()
            page.on("console", lambda msg: _console_messages.append({
                "type": msg.type,
                "text": msg.text,
                "url": msg.location.get("url", ""),
                "line": msg.location.get("lineNumber", 0),
                "column": msg.location.get("columnNumber", 0),
            }))

            # 注册网络请求监听
            _network_requests.clear()
            page.on("request", lambda req: _network_requests.append({
                "url": req.url,
                "method": req.method,
                "resource_type": req.resource_type,
                "headers": dict(req.headers),
                "timestamp": _safe_start_time(req),
            }))
            page.on("requestfailed", lambda req: _network_requests.append({
                "url": req.url,
                "method": req.method,
                "type": "requestfailed",
                "failure": _safe_failure_text(req),
            }))

            # WebView2 退出/连接断开时置为未连接，由监控任务统一清理与重连
            browser.on("disconnected", _on_browser_disconnected)

            _page, _browser, _playwright = page, browser, p
            _connected = True
            _connected_event.set()
            print(f"[tauri-webview-mcp] 已连接到 WebView2 (CDP 端口 {cdp_port})",
                  file=sys.stderr, flush=True)
            return

        except asyncio.CancelledError:
            # MCP 会话结束时任务被取消，清理 Playwright 实例避免 driver 残留
            if p is not None:
                try:
                    await p.stop()
                except Exception:
                    pass
            raise
        except Exception:
            if attempt == 1:
                print(f"[tauri-webview-mcp] 正在等待 Tauri WebView2 (CDP 端口 {cdp_port}) 就绪...",
                      file=sys.stderr, flush=True)
            elif attempt % 20 == 0:
                print(f"[tauri-webview-mcp] 已重试 {attempt} 次仍无法连接，继续等待...",
                      file=sys.stderr, flush=True)
            if p is not None:
                try:
                    await p.stop()
                except Exception:
                    pass
            await asyncio.sleep(delay)


async def _on_browser_disconnected(*args) -> None:
    """CDP 连接断开事件回调：仅置为未连接，由 monitor 任务负责清理与重连"""
    global _connected
    if _connected:
        print("[tauri-webview-mcp] WebView2 连接已断开（CDP disconnected），开始重连...",
              file=sys.stderr, flush=True)
    _connected = False
    _connected_event.clear()


async def _is_connection_alive() -> bool:
    """低开销心跳：页面能执行 JS 视为连接存活（兜底 disconnected 事件丢失/WebView2 挂死）"""
    page = _page
    if page is None or _browser is None:
        return False
    try:
        await asyncio.wait_for(page.evaluate("1"), timeout=2.0)
        return True
    except Exception:
        return False


async def _cleanup_connection() -> None:
    """清理失效连接（幂等）。只停 Playwright 实例断开 CDP，不向 WebView2 发送关闭命令"""
    global _page, _browser, _playwright, _connected
    _connected = False
    _connected_event.clear()
    _page = None
    p = _playwright
    _browser, _playwright = None, None
    if p is not None:
        try:
            await p.stop()
        except Exception:
            pass


async def monitor_connection(cdp_port: int, delay: float = 1.5, heartbeat_interval: float = 5.0) -> None:
    """
    常驻监控任务：保证始终有可用的 WebView2 连接。
    未连接时持续重连；已连接时周期性心跳，断线后自动清理并重连。
    """
    global _connected
    while True:
        if not _connected:
            async with _connect_lock:
                if not _connected:
                    await _connect_until_success(cdp_port, delay)
        else:
            if await _is_connection_alive():
                await asyncio.sleep(heartbeat_interval)
            else:
                print("[tauri-webview-mcp] WebView2 心跳检测失败，连接已断开，开始重连...",
                      file=sys.stderr, flush=True)
                await _cleanup_connection()


async def _ensure_page(timeout: float = 30.0):
    """确保有可用页面；未连接时等待后台重连，超时给出明确中文错误"""
    if _page is not None and _connected:
        return _page
    if _connected_event is None:
        raise RuntimeError("MCP 服务尚未初始化")
    print("[tauri-webview-mcp] 工具在未连接状态下被调用，等待 WebView2 重连...",
          file=sys.stderr, flush=True)
    try:
        await asyncio.wait_for(_connected_event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        raise RuntimeError(
            f"未连接到 WebView2（{timeout:.0f}s 内未重连成功）。"
            f"请启动 Tauri 应用，并确保以 WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port={_CDP_PORT} 启动"
        ) from None
    return _page


def _safe_start_time(req) -> int:
    """兼容 Playwright RequestTiming 对象与 CDP dict 两种形态，避免监听器抛错污染会话"""
    try:
        t = req.timing
        if not t:
            return 0
        if isinstance(t, dict):
            return t.get("start_time", 0) or 0
        return getattr(t, "start_time", 0) or 0
    except Exception:
        return 0


def _safe_failure_text(req) -> str:
    """兼容 Playwright RequestFailed 对象与 CDP str 两种形态，避免监听器抛错污染会话"""
    try:
        f = req.failure
        if not f:
            return "unknown"
        if isinstance(f, str):
            return f
        return getattr(f, "error_text", None) or "unknown"
    except Exception:
        return "unknown"


# ── Tool 处理函数 ──────────────────────────────────────────────

async def handle_list_tools(
    ctx: ServerRequestContext, params: Any = None
) -> ListToolsResult:
    return ListToolsResult(tools=[
        Tool(
            name="browser_navigate",
            description="导航到指定的 URL（如 http://127.0.0.1:1420）",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要导航的完整 URL",
                    }
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="browser_console_messages",
            description="获取浏览器控制台消息（JS 报错、警告等），可按级别过滤",
            input_schema={
                "type": "object",
                "properties": {
                    "level": {
                        "type": "string",
                        "enum": ["error", "warning", "info", "debug"],
                        "description": "消息级别，error 仅返回报错，warning 返回报错+警告",
                    }
                },
            },
        ),
        Tool(
            name="browser_snapshot",
            description="获取当前页面的可访问性快照（DOM 结构），用于 AI 分析页面布局",
            input_schema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_take_screenshot",
            description="截取当前页面截图并保存到临时文件",
            input_schema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_click",
            description="点击页面上的元素（通过 CSS 选择器定位）",
            input_schema={
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS 选择器，如 'button#start'、'.nav-item'",
                    }
                },
                "required": ["selector"],
            },
        ),
        Tool(
            name="browser_type",
            description="在输入框中输入文本",
            input_schema={
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS 选择器定位输入框",
                    },
                    "text": {
                        "type": "string",
                        "description": "要输入的文本",
                    },
                    "submit": {
                        "type": "boolean",
                        "description": "输入后是否按 Enter 提交",
                    },
                },
                "required": ["selector", "text"],
            },
        ),
        Tool(
            name="browser_network_requests",
            description="获取页面加载以来的网络请求列表（用于检查 API 调用失败）",
            input_schema={
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": "URL 过滤关键字，只返回 URL 中包含该字符串的请求",
                    }
                },
            },
        ),
        Tool(
            name="browser_get_html",
            description="获取当前页面的完整 HTML 源代码",
            input_schema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_get_text",
            description="获取当前页面中可见的文本内容（过滤掉 HTML 标签）",
            input_schema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_refresh",
            description="刷新当前页面",
            input_schema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="browser_evaluate",
            description="在当前页面执行 JavaScript 代码并返回结果",
            input_schema={
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "要执行的 JavaScript 代码",
                    }
                },
                "required": ["script"],
            },
        ),
    ])


async def handle_call_tool(
    ctx: ServerRequestContext, params: CallToolRequestParams
) -> CallToolResult:
    name = params.name
    arguments = params.arguments or {}

    try:
        page = await _ensure_page(_TOOL_WAIT_TIMEOUT)
        if name == "browser_navigate":
            url = arguments["url"]
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return CallToolResult(content=[
                TextContent(type="text", text=f"已导航到 {url}"),
                TextContent(
                    type="text",
                    text=f"页面标题: {await page.title()}",
                ),
            ])

        elif name == "browser_console_messages":
            level = arguments.get("level", "error")
            level_map = {
                "error": ["error"],
                "warning": ["error", "warning"],
                "info": ["error", "warning", "info"],
                "debug": ["error", "warning", "info", "debug", "log"],
            }
            allowed = level_map.get(level, ["error"])
            filtered = [
                m for m in _console_messages if m["type"] in allowed
            ]
            if not filtered:
                return CallToolResult(content=[
                    TextContent(type="text", text=f"暂无 {level} 级别及以上的控制台消息"),
                ])

            text_lines = [f"共 {len(filtered)} 条控制台消息 (级别 >= {level}):"]
            for i, msg in enumerate(filtered, 1):
                loc = f"{msg['url']}:{msg['line']}:{msg['column']}" if msg["url"] else "unknown"
                text_lines.append(f"\n[{i}] [{msg['type']}] {msg['text']}")
                text_lines.append(f"    来源: {loc}")
            return CallToolResult(content=[
                TextContent(type="text", text="\n".join(text_lines)),
            ])

        elif name == "browser_snapshot":
            try:
                snap = await page.accessibility.snapshot()
                if snap:
                    text = json.dumps(snap, ensure_ascii=False, indent=2)
                else:
                    text = "(无障碍树为空，返回 HTML 内容)"
                    text += f"\n\n{await page.content()}"
            except Exception:
                text = await page.content()
            # 截取前 5000 字符避免回包过大
            if len(text) > 5000:
                text = text[:5000] + f"\n\n... (内容过长，已截断，共 {len(text)} 字符)"
            return CallToolResult(content=[
                TextContent(type="text", text=text),
            ])

        elif name == "browser_take_screenshot":
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp_path = tmp.name
            tmp.close()
            await page.screenshot(path=tmp_path, full_page=True)
            return CallToolResult(content=[
                TextContent(
                    type="text",
                    text=f"截图已保存到 {tmp_path}，请在文件管理器中查看",
                ),
            ])

        elif name == "browser_click":
            selector = arguments["selector"]
            await page.click(selector, timeout=10000)
            return CallToolResult(content=[
                TextContent(type="text", text=f"已点击元素: {selector}"),
            ])

        elif name == "browser_type":
            selector = arguments["selector"]
            text = arguments["text"]
            submit = arguments.get("submit", False)
            await page.fill(selector, text)
            if submit:
                await page.press(selector, "Enter")
            action = "并提交" if submit else ""
            return CallToolResult(content=[
                TextContent(
                    type="text",
                    text=f"已在 {selector} 中输入 '{text}'{action}",
                ),
            ])

        elif name == "browser_network_requests":
            filter_keyword = arguments.get("filter", "")
            if filter_keyword:
                filtered = [
                    r for r in _network_requests
                    if filter_keyword.lower() in r["url"].lower()
                ]
            else:
                filtered = _network_requests

            if not filtered:
                return CallToolResult(content=[
                    TextContent(type="text", text="暂无网络请求记录"),
                ])

            text_lines = [f"共 {len(filtered)} 条网络请求:"]
            for i, req in enumerate(filtered, 1):
                status = req.get("failure", "")
                status_str = f" [失败: {status}]" if status else ""
                text_lines.append(
                    f"\n[{i}] {req['method']} {req['url']}{status_str}"
                )
            return CallToolResult(content=[
                TextContent(type="text", text="\n".join(text_lines)),
            ])

        elif name == "browser_get_html":
            html = await page.content()
            if len(html) > 5000:
                html = html[:5000] + f"\n\n... (内容过长，已截断，共 {len(html)} 字符)"
            return CallToolResult(content=[
                TextContent(type="text", text=html),
            ])

        elif name == "browser_get_text":
            text = await page.evaluate("document.body.innerText")
            if len(text) > 5000:
                text = text[:5000] + f"\n\n... (内容过长，已截断，共 {len(text)} 字符)"
            return CallToolResult(content=[
                TextContent(type="text", text=text),
            ])

        elif name == "browser_refresh":
            await page.reload(wait_until="domcontentloaded")
            return CallToolResult(content=[
                TextContent(type="text", text="页面已刷新"),
            ])

        elif name == "browser_evaluate":
            script = arguments["script"]
            result = await page.evaluate(script)
            result_str = json.dumps(result, ensure_ascii=False, indent=2) \
                if not isinstance(result, str) else result
            return CallToolResult(content=[
                TextContent(type="text", text=result_str),
            ])

        else:
            return CallToolResult(
                content=[TextContent(type="text", text=f"未知工具: {name}")],
                is_error=True,
            )

    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"执行 {name} 失败: {type(e).__name__}: {e}")],
            is_error=True,
        )


# ── 启动入口 ────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(description="Tauri WebView2 MCP Server")
    parser.add_argument(
        "--cdp-port",
        type=int,
        default=9222,
        help="WebView2 CDP 调试端口（默认 9222）",
    )
    parser.add_argument(
        "--reconnect-interval",
        type=float,
        default=1.5,
        help="重连尝试间隔秒数（默认 1.5）",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=5.0,
        help="连接心跳检查间隔秒数（默认 5.0）",
    )
    parser.add_argument(
        "--tool-wait-timeout",
        type=float,
        default=30.0,
        help="工具调用在未连接时等待重连的超时秒数（默认 30）",
    )
    args = parser.parse_args()

    global _connect_lock, _connected_event, _CDP_PORT, _TOOL_WAIT_TIMEOUT
    _CDP_PORT = args.cdp_port
    _TOOL_WAIT_TIMEOUT = args.tool_wait_timeout
    _connect_lock = asyncio.Lock()
    _connected_event = asyncio.Event()

    # 创建 MCP Server
    server = Server(
        name="tauri-webview-mcp",
        version="0.2.0",
        description="通过 CDP 直连 Tauri WebView2，捕获前端报错和检查页面结构（断开后自动重连）",
        on_list_tools=handle_list_tools,
        on_call_tool=handle_call_tool,
    )

    # 先启动 stdio 握手，WebView2 连接放后台监控任务，避免未就绪时阻塞 MCP 启用
    async with stdio_server() as (read_stream, write_stream):
        monitor_task = asyncio.create_task(
            monitor_connection(args.cdp_port, args.reconnect_interval, args.heartbeat_interval)
        )
        try:
            init_options = server.create_initialization_options()
            await server.run(
                read_stream,
                write_stream,
                init_options,
                raise_exceptions=True,
            )
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except (asyncio.CancelledError, Exception):
                pass


if __name__ == "__main__":
    asyncio.run(main())
