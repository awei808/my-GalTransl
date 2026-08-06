"""
Tauri WebView2 MCP Server

通过 Playwright CDP 直连 Tauri 应用的 WebView2，提供浏览器自动化能力。
用于 CodeBuddy AI 自动捕获前端报错、检查页面结构。

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
_reconnecting = False
# 保活阶段的阻塞事件；断开时由 _on_disconnect 置位，唤醒主循环回到 while 顶部重连
_reconnect_event = asyncio.Event()


async def connect_to_webview(cdp_port: int, delay: float = 5.0) -> None:
    """
    通过 CDP 连接到 Tauri WebView2。

    行为分两个阶段：
      1. 首次连接：无限轮询等待 WebView2 就绪，不会因连接失败而退出；
      2. 运行期保活：连接成功后进入保活状态，监听 page/browser 断开事件，
         一旦 traul 窗口关闭或崩溃，自动回到等待分支重新连接（自动重连）。
    """
    global _page, _browser, _playwright, _console_messages, _network_requests, _connected, _reconnecting

    attempt = 0
    while True:
        attempt += 1
        p = None
        try:
            p = await async_playwright().start()
            _playwright = p
            _browser = await p.chromium.connect_over_cdp(
                f"http://127.0.0.1:{cdp_port}"
            )

            # 获取或创建页面
            contexts = _browser.contexts
            if contexts and contexts[0].pages:
                _page = contexts[0].pages[0]
            else:
                ctx = contexts[0] if contexts else await _browser.new_context()
                _page = ctx.pages[0] if ctx.pages else await ctx.new_page()

            # 注册控制台消息监听
            _console_messages = []
            _page.on("console", lambda msg: _console_messages.append({
                "type": msg.type,
                "text": msg.text,
                "url": msg.location.get("url", ""),
                "line": msg.location.get("lineNumber", 0),
                "column": msg.location.get("columnNumber", 0),
            }))

            # 注册网络请求监听
            _network_requests = []
            _page.on("request", lambda req: _network_requests.append({
                "url": req.url,
                "method": req.method,
                "resource_type": req.resource_type,
                "headers": dict(req.headers),
                "timestamp": _safe_start_time(req),
            }))
            _page.on("requestfailed", lambda req: _network_requests.append({
                "url": req.url,
                "method": req.method,
                "type": "requestfailed",
                "failure": _safe_failure_text(req),
            }))

            # 注册断开事件，触发自动重连
            _page.on("close", lambda: asyncio.ensure_future(_on_disconnect("close")))
            _page.on("crash", lambda: asyncio.ensure_future(_on_disconnect("crash")))
            _browser.on("disconnected", lambda: asyncio.ensure_future(_on_disconnect("disconnected")))

            _connected = True
            _reconnecting = False
            if attempt == 1:
                print(f"[tauri-webview-mcp] 已连接到 WebView2 (CDP 端口 {cdp_port})",
                      file=sys.stderr, flush=True)
            else:
                print(f"[tauri-webview-mcp] 已恢复 WebView2 连接 (CDP 端口 {cdp_port})",
                      file=sys.stderr, flush=True)
            # 进入保活阶段：阻塞直到断开事件置位，再回到 while 顶部重连
            _reconnect_event.clear()
            await _reconnect_event.wait()

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
            else:
                print(f"[tauri-webview-mcp] 重连失败，{delay}s 后重试: {type(e).__name__}: {e}",
                      file=sys.stderr, flush=True)
            if p is not None:
                try:
                    await p.stop()
                except Exception:
                    pass
            await asyncio.sleep(delay)


async def _on_disconnect(reason: str) -> None:
    """
    连接断开回调（由 page/browser 事件触发）。
    置重连标志并切断当前连接，主循环会从保活阶段回到等待分支自动重连。
    """
    global _connected, _reconnecting, _page, _browser, _playwright

    if _reconnecting:
        return  # 已在重连中，避免重复触发
    _connected = False
    _reconnecting = True
    _page = None
    print(f"[tauri-webview-mcp] WebView2 连接断开，准备重连 (原因: {reason})",
          file=sys.stderr, flush=True)

    # 关闭失效的 browser / playwright driver，避免子进程残留
    if _browser is not None:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception:
            pass
        _playwright = None

    # 唤醒主循环的保活阻塞，使其回到 while 顶部重新连接
    _reconnect_event.set()


def _require_page():
    """确保已连接到页面"""
    if _reconnecting:
        raise RuntimeError("WebView2 连接已断开，正在重连，请稍后重试")
    if _page is None:
        raise RuntimeError("未连接到 WebView2，请先启动 Tauri 应用并确保 CDP 端口已开启")
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
    page = _require_page()

    try:
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
    args = parser.parse_args()

    # 创建 MCP Server
    server = Server(
        name="tauri-webview-mcp",
        version="0.1.0",
        description="通过 CDP 直连 Tauri WebView2，捕获前端报错和检查页面结构",
        on_list_tools=handle_list_tools,
        on_call_tool=handle_call_tool,
    )

    # 先启动 stdio 握手，WebView2 连接放后台任务，避免未就绪时阻塞 MCP 启用
    async with stdio_server() as (read_stream, write_stream):
        connect_task = asyncio.create_task(connect_to_webview(args.cdp_port))
        try:
            init_options = server.create_initialization_options()
            await server.run(
                read_stream,
                write_stream,
                init_options,
                raise_exceptions=True,
            )
        finally:
            connect_task.cancel()
            try:
                await connect_task
            except (asyncio.CancelledError, Exception):
                pass


if __name__ == "__main__":
    asyncio.run(main())
