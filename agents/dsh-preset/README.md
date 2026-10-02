# GalTransl Agent 预设（dsh）

给 **DeepSeek Harness（dsh）** 用的 GalTransl 专用 Agent 预设：只挂载 GalTransl 的 MCP 工具，
**不挂载** 文件读写、Shell、子代理、网页抓取等原生工具。

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
> **工具实现**决定，不受 dsh 工具目录约束。
>
> 0.6.0 起 MCP 侧有 4 个写工具，故「无写能力」的说法已不成立。MCP 侧自身的收敛靠三层：
> 写工具只改项目内指定产物（无任意路径写、无命令执行）、L3 项目白名单（拒绝非项目目录）、
> H 硬门禁（命中即拒绝）。**这三层是 MCP 进程自己的约束，与本预设无关**——
> 即使不用本预设、用 dsh 内置的 `standard`，MCP 的写入边界也一样。
>
> 注：H 门禁与 MCP 工具开关可在 GalTransl 设置界面调整（写入 `app_settings.json`，MCP 进程实时读取）：
> **工具清单与调用拦截实时生效**；**服务说明是 MCP 进程启动快照**，改设置后需重开 agent 会话刷新。
> persona 是静态文本，其 H 段已写成**条件式**：以 galtransl MCP 服务下发的使用说明为准
> （门禁开 → 禁止查看、写被拒如实转告；门禁关 → 按用户配置正常处理；未见到说明 → 保守按禁止处理），
> 无需因开关增删 persona 条目。

## 文件

| 文件 | 作用 |
|---|---|
| `galtransl.preset.yml` | **唯一真相源**：预设声明本体，顶层是条目清单（`cordis:include` 的目标格式） |
| `cordis.patch.yml` | 由上面那份**生成**的同内容 patch（带 `- insert:` 包裹），供不想用 include 的用户直接并入 profile。**请勿手改**，改真相源后重跑 `python tools/build_dsh_preset.py` |
| `README.md` | 本文件 |

## 安装（推荐：home 层 include）

> **为什么是 home 层**：dsh 有两层用户可写的 patch，层叠顺序为
> bundle 层 → profile 的 `cordis.patch.yml` → **`$DSH_HOME/cordis.patch.yml`（home 层）** → `--patch`。
> home 层**对所有 profile 生效**，且文件不存在时会被静默跳过（不影响 dsh 启动）。
> 这意味着用户只需**新建一个文件**，完全不碰自己 profile 里任何现有配置。

dsh 的配置根 `$DSH_HOME` 在 Windows 默认是 `C:\Users\<你>\.dsh\`。
预设**只能装到 profile / bundle patch**——dsh **不支持**项目级预设。

> ⚠️ **不要用 `$DSH_HOME\.agent-presets\<name>\` 目录格式**（`preset.yml` + `agent.cordis.yml`）。
> 那是 dsh 的**遗留格式**，现行版本已不再读取该目录（`registry/README.md`：
> "The registry neither scans directories nor accepts preset paths"）。
> 现行机制是「`@deepseek-ai/dsh-agent-preset` 声明行」，只能通过 patch 层安装。

**安装步骤：**

1. 把 `galtransl.preset.yml` 复制到 `$DSH_HOME\profiles\` 下
   （即 `C:\Users\<你>\.dsh\profiles\galtransl.preset.yml`）。

2. 在 `$DSH_HOME\cordis.patch.yml` 写入下面这条 include 行
   （**该文件不存在就新建**；已存在则把 `- insert:` 那一段并进去）：

   ```yaml
   - insert:
       - id: galtransl-preset-include
         name: cordis:include
         config:
           path: file:///C:/Users/<你>/.dsh/profiles/galtransl.preset.yml
   ```

3. 改 `galtransl.preset.yml` 里 `mcp-galtransl` 行的 `command` / `args` / `cwd`
   三处绝对路径，指向你本机的实际位置（见下节）。

4. 重启 dsh，在会话的 **Agent 预设** 选择器里选 `GalTransl`
   （预设**每会话选择**，且**首轮后锁定**——要换预设请开新会话）。

### 备选：直接并入 profile 的 patch

不想用 include，就把 `cordis.patch.yml` 里 `- insert:` 那一段原样并入
`$DSH_HOME\profiles\<name>\cordis.patch.yml`。代价是**换 profile 要重做一遍**。

> ⚠️ 外部/新增行**必须放在 `insert:` 列表里**——裸写 `- id: X / name: Y` 会被当成
> 对已有条目的覆写，tree 中不存在时报 `entry not found` 且不插入。

## 实测踩过的两个坑（务必知道）

用 `cordis:include` 时，下面两条**都不是直觉行为**，实测确认：

1. **被 include 的文件必须放在 `profiles\` 目录内**（或其 `node_modules` 可解析的位置）。
   放到别处会报 `preset-galtransl (@deepseek-ai/dsh-agent-preset): failed to import`——
   因为**被 include 文件里的裸包名（`@deepseek-ai/dsh-*`）不按该文件所在目录解析**，
   而是走宿主模块管线（包名占位在 `$DSH_HOME/profiles/node_modules`）。

2. **`path` 是相对 profile 目录解析的，不是相对 patch 文件**。
   所以用绝对 `file:///` URL 最稳（写成裸 Windows 路径会报
   `ERR_INVALID_URL_SCHEME: The URL must be of scheme file`）。

另外：目标文件必须**顶层就是数组的条目清单**——写 `- insert:` 会被拒绝
（`config file must be a top-level array of entries`）。`galtransl.preset.yml` 已是正确格式。

## 自检

- `dsh --profile <name> --dump-config` 应能看到 `id: preset-galtransl` 行及其 4 个子行，
  且该区块内**没有**任何 `tool-fs` / `tool-pwsh` 等危险工具，输出里应有
  `# == C:\Users\<你>\.dsh\cordis.patch.yml` 归属头。
- ⚠️ **`--dump-config` 看不到 include 展开的内容**（它只渲染该 include 行本身）。
  要确认预设**真的被加载**，必须实际启动，看 stderr 有无 `failed to import`，
  并确认日志里出现 `[galtransl-mcp] 启动 stdio 服务`。
- ⚠️ **`desktop` profile 不能这样自检**：它由 Electron 独占，CLI 会报
  `profile "desktop" is managed exclusively by the Electron application`。
  桌面用户请改为在 dsh 里直接看 **Agent 预设选择器里有没有 `GalTransl`**——
  预设注册成功就一定出现在列表里（没有「隐藏」开关字段）。

## 必须改的路径

`galtransl.preset.yml` 里 `@deepseek-ai/dsh-mcp-client` 行的 `command` / `args` / `cwd`：

- **源码模式**：指向 `venv\Scripts\python.exe` 与 `run_mcp_server.py`。
- **打包版**：`command` 指向 `<程序根>\backend\galtransl_mcp.exe`，`args: []`。

> `command` 含路径分隔符时**必须写绝对路径**：dsh 的 subprocess provider 只对裸名查 `PATH`，
> 相对路径因解析基准未定义会被直接拒绝。

## 预设内容说明

`galtransl.preset.yml` 的 `plugins:` 只有这些行：

| 行 | bundle | 作用 |
|---|---|---|
| `persona` | `@deepseek-ai/dsh-persona` | 系统提示词（GalTransl 硬性契约）。字段是 `prefix`，**不是** `text` |
| `agent-instructions` | `@deepseek-ai/dsh-agent-instructions` | AGENTS.md 发现（`maxBytes` 必须重申） |
| `mcp-galtransl` | `@deepseek-ai/dsh-mcp-client` | 连 GalTransl MCP（stdio） |
| `tool-ask-user` | `@deepseek-ai/dsh-tool-ask-user` | 向用户提问（唯一保留的交互工具） |

**刻意不挂载**（下列均为**行 id**）：`tool-fs`、`tool-fs-search`、`tool-bash`、`tool-pwsh`、
`tool-jobs`、`tool-web`、`tool-skill`、`skill-filesystem`、`tool-goal`、`tool-todo`、
`tool-present`、`tool-str-replace-editor`、`tool-cordis`、`planning`（plan-mode）、
`compaction`（compaction-basic / command-compact / tool-result-pruner）、
`delegation`（tool-subagent-control / tool-subagent / tool-subagent-fork /
tool-subagent-list-agents / tool-agent-team / tool-workflow / tool-ralph）、
`tool-plugin-manager`（此行的 bundle 是 `@deepseek-ai/dsh-plugin-manager/tools`，
与其他行的 `dsh-tool-*` 命名不同）。

> 这份清单不必手动维护完整：回归测试用的是**白名单**
> （`ALLOWED_PLUGIN_BUNDLES`，只允许上面那 4 个 bundle），
> 所以 dsh 将来新增任何工具都不会让测试静默失效。

有回归测试锁定这份清单：`tests/test_dsh_preset_package.py`。

## 升级 / 维护

预设内容**只改** `galtransl.preset.yml`，然后：

```bash
python tools/build_dsh_preset.py          # 刷新 cordis.patch.yml
python tools/build_dsh_preset.py --check  # 校验两者是否同步
```

改了真相源后，把新的 `galtransl.preset.yml` 覆盖到 `$DSH_HOME\profiles\` 即可升级。

## 已知限制

- **`includeRuntimeContext: false` 是全有或全无**：它会关闭该作用域的所有上下文贡献，
  包括沙箱策略与审批策略的 runtime 快照（包内注释已说明）。本预设**不改**这一项，
  以免模型看不到自己处于什么沙箱模式。
- **模型不由本预设决定**：dsh 的模型路由在宿主层（`agent-default-model` 行），
  预设不拥有它。装完仍需在 profile/设置里配好模型。
- **不兼容 built-in `standard`**：本预设是独立预设，不覆写 `standard`。
  若你想要「GalTransl 工具 + 通用编码能力」的混合体，请用 dsh 的新增预设功能自行编写。
- **home 层对所有 profile 生效**：若你已在用 home 层，需自行把 include 行合并进去。

## 卸载

删除 `$DSH_HOME\cordis.patch.yml` 里那段 include 条目与
`$DSH_HOME\profiles\galtransl.preset.yml`，重启 dsh。
