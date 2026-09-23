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
from typing import Any, Dict

from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
    ToolAnnotations,
)

from GalTransl import GALTRANSL_VERSION
from GalTransl.mcp_heartbeat import (
    HEARTBEAT_INTERVAL_SECONDS,
    remove_heartbeat,
    write_heartbeat,
)
from GalTransl.mcp_tools import (
    MCP_TOOL_DEFS,
    SERVER_INSTRUCTIONS,
    call_mcp_tool,
    tool_annotations,
)

SERVER_NAME = "galtransl"
SERVER_DESCRIPTION = (
    "GalTransl 术语与译文检索（只读）：翻译缓存、原始脚本、字典、人名表、日志、元数据"
)


def _accepted_annotations(item: Dict[str, Any]) -> Dict[str, Any]:
    """只保留 SDK 当前版本认得的 annotation 字段。

    SDK 若改名（如 read_only_hint → readOnlyHint），这里退化为不下发该字段，
    而不是让 ToolAnnotations(**...) 抛 TypeError 把整个 tools/list 打挂——对客户端而言
    「少一个 hint」远好于「一个工具都看不到」。
    """
    return {
        key: value
        for key, value in tool_annotations(item).items()
        if key in ToolAnnotations.model_fields
    }


def _to_mcp_tool(item: Dict[str, Any]) -> Tool:
    """把 MCP_TOOL_DEFS 条目映射为 SDK Tool，并按 kind 附带只读 annotations。

    非 read 类工具（0.6.0 作业域）不带 annotations：传 None 由 SDK 的 exclude_none
    省略该字段，避免下发空对象被客户端误判为「未声明」。
    """
    annotations = _accepted_annotations(item)
    return Tool(
        name=item["name"],
        description=item["description"],
        input_schema=item["input_schema"],
        annotations=ToolAnnotations(**annotations) if annotations else None,
    )


async def handle_list_tools(
    ctx: ServerRequestContext, params: Any = None
) -> ListToolsResult:
    """返回工具清单，字段口径与 mcp_tools.MCP_TOOL_DEFS 保持一致。"""
    return ListToolsResult(tools=[_to_mcp_tool(item) for item in MCP_TOOL_DEFS])


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
        instructions=SERVER_INSTRUCTIONS,
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
    # LOGGER 在本进程无 handler（INFO 会被 lastResort 丢弃），故启动信息走 stderr；
    # stdio 传输下 stdout 属协议流，任何日志都不得写入。
    _derived = sum(1 for item in MCP_TOOL_DEFS if tool_annotations(item))
    _delivered = sum(1 for item in MCP_TOOL_DEFS if _accepted_annotations(item))
    print(f"[galtransl-mcp] 启动 stdio 服务（版本 {GALTRANSL_VERSION}）", file=sys.stderr, flush=True)
    print(
        f"[galtransl-mcp] 已下发约束 instructions（{len(SERVER_INSTRUCTIONS)} 字）"
        f"与 {_delivered} 个只读 annotations",
        file=sys.stderr,
        flush=True,
    )
    if _delivered != _derived:
        print(
            "[galtransl-mcp] 警告：部分 annotation 字段不被当前 mcp SDK 识别，已跳过下发",
            file=sys.stderr,
            flush=True,
        )
    asyncio.run(main())
