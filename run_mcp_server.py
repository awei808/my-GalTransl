"""GalTransl MCP Server（stdio 传输）。

以标准 MCP 协议向外部 agent（CodeBuddy / Claude Desktop / Cherry Studio 等）暴露
GalTransl 的只读工具（工具清单见 GalTransl/mcp_tools.py）。

独立进程运行：仅读磁盘上的项目文件，**不需要 GalTransl 后端在跑**。
运行环境需同时具备 `mcp` 包与 GalTransl 的依赖（本项目开发环境已同时满足）。

客户端配置示例（.mcp.json / claude_desktop_config.json）：
    {
      "mcpServers": {
        "galtransl": {
          "type": "stdio",
          "command": "python",
          "args": ["run_mcp_server.py"]
        }
      }
    }
"""
import asyncio
import json
import sys
from typing import Any

from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
)

from GalTransl import GALTRANSL_VERSION
from GalTransl.mcp_heartbeat import (
    HEARTBEAT_INTERVAL_SECONDS,
    remove_heartbeat,
    write_heartbeat,
)
from GalTransl.mcp_tools import MCP_TOOL_DEFS, call_mcp_tool

SERVER_NAME = "galtransl"
SERVER_DESCRIPTION = (
    "GalTransl 术语与译文检索（只读）：翻译缓存、原始脚本、字典、人名表、日志、元数据"
)
_LIST_TOOLS_TIMEOUT_NOTE = "所有工具均需提供项目根目录绝对路径 project_dir"


async def handle_list_tools(
    ctx: ServerRequestContext, params: Any = None
) -> ListToolsResult:
    """返回工具清单，字段口径与 mcp_tools.MCP_TOOL_DEFS 保持一致。"""
    return ListToolsResult(
        tools=[
            Tool(
                name=item["name"],
                description=item["description"],
                input_schema=item["input_schema"],
            )
            for item in MCP_TOOL_DEFS
        ]
    )


async def handle_call_tool(
    ctx: ServerRequestContext, params: Any
) -> CallToolResult:
    """执行工具并把结果序列化为文本内容。

    工具级错误（未知工具 / 参数错误 / 执行异常）一律以 is_error 返回，
    不冒泡为协议错误——否则客户端会整条连接失败而看不到具体原因。
    """
    name = getattr(params, "name", "") or ""
    arguments = getattr(params, "arguments", None) or {}
    try:
        result = call_mcp_tool(name, arguments if isinstance(arguments, dict) else {})
    except KeyError:
        return CallToolResult(
            content=[TextContent(type="text", text=f"未知工具: {name}")],
            is_error=True,
        )
    except ValueError as exc:
        return CallToolResult(
            content=[TextContent(type="text", text=f"参数错误: {exc}")],
            is_error=True,
        )
    except Exception as exc:
        return CallToolResult(
            content=[
                TextContent(type="text", text=f"执行 {name} 失败: {type(exc).__name__}: {exc}")
            ],
            is_error=True,
        )
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
    )


async def _heartbeat_loop() -> None:
    """定期刷新心跳文件，供后端/前端判断「当前是否有外部 agent 连着」。"""
    while True:
        write_heartbeat(len(MCP_TOOL_DEFS))
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


async def main() -> None:
    server = Server(
        name=SERVER_NAME,
        version=GALTRANSL_VERSION,
        description=SERVER_DESCRIPTION,
        instructions=_LIST_TOOLS_TIMEOUT_NOTE,
        on_list_tools=handle_list_tools,
        on_call_tool=handle_call_tool,
    )
    write_heartbeat(len(MCP_TOOL_DEFS))
    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
                raise_exceptions=True,
            )
    finally:
        # 正常退出或异常退出都要清心跳，避免前端亮起"假在线"绿灯
        heartbeat_task.cancel()
        remove_heartbeat()


if __name__ == "__main__":
    print(f"[galtransl-mcp] 启动 stdio 服务（版本 {GALTRANSL_VERSION}）", file=sys.stderr, flush=True)
    asyncio.run(main())
