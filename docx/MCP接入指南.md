# GalTransl MCP 接入指南（外部 Agent 调用）

> 版本：0.5.1 起提供。让外部 agent（CodeBuddy / Claude Desktop / Cherry Studio 等 MCP 客户端）
> 以**只读**方式检索 GalTransl 项目的术语、译文、字典、人名表、日志与元数据。
> 实现见 `run_mcp_server.py`（stdio 传输）+ `GalTransl/mcp_tools.py`（11 个工具）。

---

## 1. 这是什么

外部 agent 通过标准 MCP 协议调用 GalTransl 的**检索能力**，典型用途是「核查术语译法是否统一」：

- 某术语在字典里定的译法 vs 实际译文里用的译法是否一致
- 某角色名是否已进译名表、正文用名是否与译名表一致
- 某类问题译文（残留日文、词频过高…）分布在哪些文件、哪些行

**只读**：没有启动翻译、改配置、写字典的工具，不会改动项目文件。

---

## 2. 环境要求

运行 MCP 服务的 Python 解释器需要**同时**具备：

| 依赖 | 说明 |
|---|---|
| `mcp>=2.0,<3.0` | 已在 `requirements.txt:30-32` 声明 |
| GalTransl 的依赖 | `PyYAML` / `orjson` / `requests` 等（见 `requirements.txt`） |

本项目开发机上 `C:/Python312/python.exe` 同时满足两者（`mcp` 装在用户级 site-packages）。
若用其它环境，先安装：

```
pip install "mcp>=2.0,<3.0"
```

> 不需要 GalTransl 后端（`galtransl_backend`）在运行——MCP 服务直接读取项目目录下的文件。

---

## 3. 客户端配置

### 3.1 本仓库自带（`.mcp.json`）

仓库根目录的 `.mcp.json` 已注册 `galtransl` 条目：

```json
{
  "mcpServers": {
    "galtransl": {
      "type": "stdio",
      "command": "python",
      "args": ["run_mcp_server.py"],
      "description": "GalTransl 术语与译文检索（只读）：翻译缓存 / 原始脚本 / 字典 / 人名表 / 日志 / 元数据"
    }
  }
}
```

该写法依赖客户端把工作目录设为仓库根。**若客户端的工作目录不是仓库根，改用绝对路径**：

```json
"command": "C:/Python312/python.exe",
"args": ["D:/解包或汉化用/my-galtransl/my-GalTransl/run_mcp_server.py"]
```

### 3.2 CodeBuddy（用户级）

编辑 `~/.workbuddy/mcp.json`，把上面的 `galtransl` 条目并入 `mcpServers`。写入后**不会自动生效**：
到连接器管理页右上角的「自定义连接器」入口，对该 server 点「信任」才会启用。

### 3.3 Claude Desktop

编辑 `claude_desktop_config.json`（Windows 位于 `%APPDATA%\Claude\`），同样并入 `mcpServers` 后重启客户端。

---

## 4. 可用工具（11 个）

| 工具 | 用途 |
|---|---|
| `galtransl_search_cache` | 检索已入库译文（原文/译文/问题/说话人），支持 4 种字段与正则 |
| `galtransl_search_scripts` | 检索原始脚本 `gt_input`，未跑过翻译也能用 |
| `galtransl_search_dict` | 检索字典词条（项目字典 + 公共 `Dict/`），可限定方向 |
| `galtransl_lookup_name` | 查译名表（`name替换表.csv/xlsx`） |
| `galtransl_search_logs` | 按关键词检索日志，返回绝对行号 |
| `galtransl_list_problems` | 列出带问题标记的缓存条目 |
| `galtransl_list_projects` | 列出工作区下可识别的项目 |
| `galtransl_get_project_overview` | 项目概览（目标语言、每请求条数、注入块开关、流水线阶段） |
| `galtransl_read_translation_file` | 读取缓存文件条目（分页） |
| `galtransl_read_source_script` | 读取原始脚本文件条目（分页） |
| `galtransl_get_project_metadata` | 读元数据（globalprompt / filemeta / batchmeta） |

所有工具都需要 `project_dir`（项目根目录绝对路径）。工作流建议见 `skills/galtransl-mcp/SKILL.md`。

---

## 5. 联调记录（实测）

本机实测（探针脚本直连 stdio，非推测）：

| 步骤 | 结果 |
|---|---|
| `initialize` | 返回 `protocolVersion: 2025-06-18`、`capabilities.tools`、`serverInfo{name: galtransl}` |
| `notifications/initialized` | 无响应（通知不产生响应，符合协议） |
| `tools/list` | 返回 **11** 个工具，`inputSchema` 合法 |
| `tools/call` → `galtransl_get_project_overview` | 在真实项目上正确读出 `language: zh-cn`、`numPerRequestTranslate: 16`、`translation_guideline` 等 |
| `tools/call` → `galtransl_search_scripts` | 在真实项目上成功命中「クルト」及其所在文件与 index |
| `tools/call` → 未知工具 | 返回 `isError: true` + 「未知工具」文案，**协议连接未中断** |

---

## 6. 故障排查

| 现象 | 原因与处理 |
|---|---|
| 客户端里看不到 `galtransl` 工具 | 检查配置 JSON 语法；CodeBuddy 需在连接器管理页对自定义连接器点「信任」 |
| 工具报「未知工具」 | 客户端缓存了旧的工具清单，重启客户端 |
| 报 `ModuleNotFoundError: mcp` | 该 Python 环境没装 `mcp`（见 §2），或 `command` 指向了错误的解释器 |
| 报 `project_dir 不存在或不是目录` | 传的是相对路径或含 `~`；必须用绝对路径 |
| 搜索没有结果 | 项目没跑过翻译时用 `galtransl_search_cache` 必然为空 → 改用 `galtransl_search_scripts` |
| 结果被截断 | 返回体 `truncated: true` 且 `total` 大于返回条数 → 加 `regex` 收窄或提高 `max_results` |

---

## 7. 安全说明

- 服务仅在本机以 stdio 子进程方式运行，**不监听任何网络端口**。
- 所有文件访问限于传入的 `project_dir` 之下（读取文件时经 `safe_under_project` 做路径归属校验，拒绝 `../` 与绝对路径）。
- **H 内容**：0.5.1 的读取路径**未做 H 门禁过滤**（完整实现排在 0.6.1）。检索结果可能包含成人向内容。
- 服务进程读得到项目目录下的一切文件（包括日志），请只把 `project_dir` 指向自己的翻译项目目录。
