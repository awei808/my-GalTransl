# GalTransl Agent 预设（dsh）

给 **DeepSeek Harness（dsh）** 用的 GalTransl 专用 Agent 预设：只挂载 GalTransl 的 MCP 工具，
**不挂载** 文件读写、Shell、子代理、网页抓取等原生工具。

## 验证状态（2026-10-01 实测）

用 `dsh --dump-config` + 实际启动两种方式验证，结论如下（非推测）：

| 验证项 | 结果 |
|---|---|
| 预设行能否组合进 profile tree | ✅ 成功，`--dump-config` 完整回显 4 个子行 |
| 预设区块内是否含危险工具 | ✅ 无。区块内 `@deepseek-ai/dsh-*` 仅 5 条：`dsh-agent-preset`(自身) / `dsh-persona` / `dsh-agent-instructions` / `dsh-mcp-client` / `dsh-tool-ask-user` |
| MCP 能否实际握手 | ✅ `[galtransl-mcp] 启动 stdio 服务（版本 0.5.4）`、`已下发约束 instructions（541 字）与 11 个只读 annotations` |
| 心跳是否写入 | ✅ `mcp_status.json` 写入 `pid` / `tools: 11` |

**关键实测发现**：宿主层（`dsh-base` + `dsh-web-app`）的 `tool-fs` / `tool-pwsh` /
`tool-bash` / `tool-fs-search` / `tool-jobs` 等行**本来就已经是 `disabled: true`**
（被 web 组合包的 patch 关掉），能力改由各 preset 自行挂载。
所以本预设**不需要**去"禁用"任何东西——**不挂就是没有**。
`--dump-config` 输出里出现的危险工具行全部位于**其他内置预设**
（`standard` / `ptc` / `minimal` / `cordis`）的区块内，与本预设无关。

## 这个预设解决什么问题

GalTransl 本体不再内置 Agent，改为**只提供 MCP 连接与 MCP 工具**；dsh 作为官方外置 Agent。
本预设把 dsh 的工具面收窄到「只有 GalTransl MCP 工具」这一件事上。

**安全边界（重要）**：dsh 的顶层工具目录**没有** allow/deny 过滤器——`ctx.tools.restrict()`
拒绝 context-global 作用域。因此唯一可靠的收窄机制是**不挂载那些工具所属的 bundle**。
本预设里没有注册 `tool-fs` / `tool-pwsh` / `tool-bash` / `tool-web` / 子代理等行，
所以**即使把会话权限切到「完全访问」（danger-full-access），也没有可提权的工具存在**。

沙箱与审批策略只是默认值、可被会话覆盖，本预设**不依赖**它们做安全保证。

> **该保证的边界**：上述覆盖的是 **dsh 侧的工具面**。GalTransl MCP 服务自身是独立进程
> （`run_mcp_server.py` / `galtransl_mcp.exe`），以普通用户权限运行，其能力边界由
> **工具实现**决定，不受 dsh 工具目录约束。当前 11 个工具**全部只读**，故现状无写能力；
> 将来若新增写类工具（如 `kind=job`），这个安全表述需同步复核。

## 文件

| 文件 | 作用 |
|---|---|
| `presets/galtransl.patch.yml` | 预设声明本体（`@deepseek-ai/dsh-agent-preset` 行） |
| `cordis.patch.yml` | 安装片段：把上面那条声明插入用户的 dsh profile |
| `README.md` | 本文件 |

## 安装

dsh 的 profile 位于 `$DSH_HOME/profiles/<name>/`（Windows 默认 `C:\Users\<你>\.dsh\profiles\`）。
预设**只能装到 profile**，dsh **不支持**项目级预设。

> ⚠️ **不要用 `$DSH_HOME\.agent-presets\<name>\` 目录格式**（`preset.yml` + `agent.cordis.yml`）。
> 那是 dsh 的**遗留格式**，现行版本已不再读取该目录。
> 现行机制是「`@deepseek-ai/dsh-agent-preset` 声明行」，只能通过 profile 的 patch 层安装。

1. 把 `cordis.patch.yml` 里的 `- insert:` 段并入该 profile 的 `cordis.patch.yml`
   （例如 `C:\Users\<你>\.dsh\profiles\desktop\cordis.patch.yml`）。
   ⚠️ 外部/新增行**必须放在 `insert:` 列表里**——裸写 `- id: X / name: Y` 会被当成
   对已有条目的覆写，tree 中不存在时报 `entry not found` 且不插入。

2. 改其中 `command` / `args` / `cwd` 三处**绝对路径**，指向你本机的实际位置（见下节）。

3. （可选）若想保持预设本体独立成文件：**不能**直接把 `presets/galtransl.patch.yml`
   用作 `cordis:include` 的目标——它的顶层是完整的 `- insert:` 补丁，而 `cordis:include`
   期望的是**一份字面条目清单**。正确做法是把里面那条 preset 声明单独取出
   （去掉 `- insert:` 与 `- id:` 两层缩进，使 `- name: '@deepseek-ai/dsh-agent-preset'` 顶格），
   存成独立文件后按 `cordis.patch.yml` 末尾注释的写法引用。

   > 注意：`<profile>\presets\` **不是** dsh 的自动发现目录（`presets/` 是
   > `@deepseek-ai/dsh-web-app` 这个 bundle 的内部目录）。放哪里都可以，
   > 关键是必须被 profile 的 `cordis.patch.yml` 显式引用才生效。

4. 重启 dsh，在会话的 **Agent 预设** 选择器里选 `GalTransl`
   （预设**每会话选择**，且**首轮后锁定**——要换预设请开新会话）。

5. 自检：`dsh --profile <name> --dump-config` 应能看到 `id: preset-galtransl` 行及
   其 4 个子行，且该区块内**没有**任何 `tool-fs` / `tool-pwsh` 等危险工具。

## 必须改的路径

`cordis.patch.yml` 里 `@deepseek-ai/dsh-mcp-client` 行的 `command` / `args` / `cwd`：

- **源码模式**：指向 `venv\Scripts\python.exe` 与 `run_mcp_server.py`。
- **打包版**：`command` 指向 `<程序根>\backend\galtransl_mcp.exe`，`args: []`。

> `command` 含路径分隔符时**必须写绝对路径**：dsh 的 subprocess provider 只对裸名查 `PATH`，
> 相对路径因解析基准未定义会被直接拒绝。

## 预设内容说明

`galtransl.patch.yml` 的 `plugins:` 只有这些行：

| 行 | bundle | 作用 |
|---|---|---|
| `persona` | `@deepseek-ai/dsh-persona` | 系统提示词（GalTransl 硬性契约） |
| `agent-instructions` | `@deepseek-ai/dsh-agent-instructions` | AGENTS.md 发现 |
| `mcp-galtransl` | `@deepseek-ai/dsh-mcp-client` | 连 GalTransl MCP（stdio） |
| `tool-ask-user` | `@deepseek-ai/dsh-tool-ask-user` | 向用户提问（唯一保留的交互工具） |

**刻意不挂载**（下列均为**行 id**）：`tool-fs`、`tool-fs-search`、`tool-bash`、`tool-pwsh`、
`tool-jobs`、`tool-web`、`tool-skill`、`skill-filesystem`、`tool-goal`、`tool-todo`、
`tool-present`、`planning`（plan-mode）、`compaction`（compaction-basic / command-compact /
tool-result-pruner）、`delegation`（tool-subagent-control / list-agents / subagent ×4 /
workflow / ralph）、`tool-plugin-manager`（此行的 bundle 是
`@deepseek-ai/dsh-plugin-manager/tools`，与其他行的 `dsh-tool-*` 命名不同）。

## 已知限制

- **`includeRuntimeContext: false` 是全有或全无**：它会关闭该作用域的所有上下文贡献，
  包括沙箱策略与审批策略的 runtime 快照（包内注释已说明）。本预设**不改**这一项，
  以免模型看不到自己处于什么沙箱模式。
- **模型不由本预设决定**：dsh 的模型路由在宿主层（`agent-default-model` 行），
  预设不拥有它。装完仍需在 profile/设置里配好模型。
- **不兼容 built-in `standard`**：本预设是独立预设，不覆写 `standard`。
  若你想要「GalTransl 工具 + 通用编码能力」的混合体，请用 dsh 的新增预设功能自行编写。

## 卸载

从 profile 的 `cordis.patch.yml` 中删除该 `insert` 条目与 `presets/galtransl.patch.yml`，重启 dsh。
