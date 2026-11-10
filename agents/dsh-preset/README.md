# GalTransl Agent 预设（dsh）

给 **DeepSeek Harness（dsh）** 用的 GalTransl 专用 Agent 预设，含**两条**、每会话二选一：

| 预设（选择器里显示） | 定位 | 工具面 |
|---|---|---|
| `GalTransl Coder` | 翻译助手 **+ 程序员**：可主动给出针对 GalTransl 程序的建议，可直接读写代码与文档 | MCP（23）+ 通用文件工具 `read`/`read_image`/`write`/`edit` + 子代理 |
| `GalTransl 翻译助手` | **只做翻译**：不主动给程序建议，不碰程序文件 | 仅 MCP（23）+ 子代理 |

两条都不挂 Shell 与网页抓取工具。子代理继承所在预设——工具面与父会话完全相同，
因此它放大的是单轮可处理的分工粒度与上下文容量，不是权限面；并发度由 persona 的串行约定收住。

> **子代理是串行的**：dsh 官方 `subagent` 工具没有同步开关（实现里 `delivery` 写死
> `"parent"`，调用即返回子会话 id）。因此两条预设都在 persona 里约定：**同一轮最多派 1 个**，
> 派出后立即结束本轮、等完成通知再继续；不要并发派多个、不要轮询。这是提示词约定，
> 不是硬机制——真正的并发上限仍由宿主 `subagent.maxActiveSubagents` 决定。

## 这个预设解决什么问题

GalTransl 本体不再内置 Agent，改为**只提供 MCP 连接与 MCP 工具**；dsh 作为官方外置 Agent。
本预设把 dsh 的工具面收窄到「GalTransl MCP 工具 +（仅 coder）通用文件工具 + 子代理」，
并按用途分成两条，避免「只想翻文本」的会话被开放文件写入。

**安全边界（重要）**：dsh 的顶层工具目录**没有** allow/deny 过滤器——`ctx.tools.restrict()`
拒绝 context-global 作用域。因此唯一可靠的收窄机制是**不挂载那些工具所属的 bundle**。

- `GalTransl 翻译助手`：没有注册 `tool-fs` / `tool-fs-search` / `tool-bash` / `tool-pwsh` / `tool-web`
  等行，**即使把会话权限切到「完全访问」（danger-full-access），也没有可提权的工具存在**。
- `GalTransl Coder`：**例外地挂 `tool-fs`**（宿主层已被 web 组合包置 `disabled: true`，
  由预设挂载正是其设计用途），因此它**具备真实的文件写入能力，不具备上面那条「无危险工具」性质**。
  它的边界是：persona 约定「只改用户点名/授权的文件、不碰凭据文件、不读可能含 H 内容的缓存/脚本」
  + 宿主 `fs-observation-policy`（覆盖已有文件前必须先 `read`，否则报 `FS_NOT_OBSERVED`）
  + 沙箱默认模式（可被会话覆盖，不可依赖）。需要严格只读会话时请用 `GalTransl 翻译助手`。
- 两条预设都**没有** Shell / 网页 / 插件管理 / cordis 配置工具。

> ⚠️ **H 硬门禁只拦 MCP 工具，拦不住 coder 的文件工具**：`read` / `read_image` 直接读盘，
> `write` / `edit` 直接写盘，能绕过 `mcp_tools.py` 的门禁读到或写回 `transl_cache/`、`gt_input/`
> 里的成人向内容。coder 的 persona 已把「门禁开启时不要用文件工具打开这类文件，改用 MCP 检索并
> 按硬性约束 2 处理」写成硬性要求，但那是**提示词约定**；对 H 内容合规有强要求时请用
> `GalTransl 翻译助手`——它没有任何文件工具，门禁无法被绕过。

子代理不是例外：`tool-subagent` 用的是 `spawn` 后端，子代理**不单独选预设**（未配置
`agentPreset` / `allowedAgentPresets`），因此继承父会话的预设，拿到的仍是同一批工具；
`maxDepth: 1` 让它不能再派生下一级。**coder 的子代理同样持有文件工具**，派活时要把改动范围划清。

沙箱与审批策略只是默认值、可被会话覆盖，本预设**不依赖**它们做安全保证。

> **该保证的边界**：上述覆盖的是 **dsh 侧的工具面**。GalTransl MCP 服务自身是独立进程
> （`run_mcp_server.py` / `galtransl_mcp.exe`），以普通用户权限运行，其能力边界由
> **工具实现**决定，不受 dsh 工具目录约束。
>
> 0.6.0 起 MCP 侧有写工具（路线图/元数据、项目字典、缓存条目标志位/备选译文/译文字段、任务启停），故「无写能力」的说法已不成立。MCP 侧自身的收敛靠三层：
> 写工具只改项目内指定产物（无任意路径写、无命令执行）、L3 项目白名单（拒绝非项目目录）、
> H 硬门禁（路线图/元数据/项目字典/缓存条目译文字段 4 个内容写入工具受约束，命中即拒绝；缓存条目标志位与备选交换不引入新文本，不受门禁）。
> **这三层是 MCP 进程自己的约束，与本预设无关**——
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
| `galtransl.preset.yml` | **唯一真相源**：预设声明本体，顶层是条目清单（`cordis:include` 的目标格式），含 `galtransl-coder` 与 `galtransl` 两条预设 |
| `cordis.patch.yml` | 由上面那份**生成**的同内容 patch（带 `- insert:` 包裹，含两条预设），同时充当本 bundle 的 patch。**请勿手改**，改真相源后重跑 `python tools/build_dsh_preset.py` |
| `package.json` | **bundle 清单**：`dsh.bundle.patch` 指向上面的生成物，使本目录可被 dsh 直接安装 |
| `locale/en.json`、`locale/zh.json` | 插件管理器/设置里显示的标题与描述 |
| `icon.svg` | 插件列表图标（原创几何图形，无第三方素材） |
| `README.md` | 本文件 |

## 安装 A（推荐：dsh 桌面版，插件管理器一键装）

本目录**本身就是一个 dsh bundle**（`package.json` 的 `dsh.bundle.patch` → `cordis.patch.yml`），
不需要复制文件、不需要手工改 profile，也不用重启：

1. dsh → 设置 → 插件 → 安装本地插件，选本目录；
   或让 agent 执行 `plugin_manager` 的 `install_bundle`，`target` 指向本目录绝对路径。
2. 安装返回 `application: applied` 后，Loader 中会出现 `preset-galtransl-coder` 与
   `preset-galtransl` 两行，插件管理器的 bundle 列表出现 `@local/dsh-galtransl-preset`。
3. **新建会话**，在 Agent 预设选择器里二选一：`GalTransl Coder` 或 `GalTransl 翻译助手`
   （预设每会话选择、首轮后锁定；选错要换请开新会话）。

要点与边界：

- **只对当前 profile 生效**（Desktop 是 `desktop`，CLI/Web 是 `web`）；换 profile 需重装。
- 目录是 **link 安装**：改本目录即改已装 bundle；改完重跑生成脚本后，新会话即读到新内容。
- bundle **不含任何 JS**，只声明预设行；能力仍只来自 `cordis.patch.yml` 里的预设行。
- 卸载：插件管理器里移除 `@local/dsh-galtransl-preset`（等价于删 profile 里的 bundle 行）。

## 安装 B（备选：home 层 include，对所有 profile 生效）

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

3. 改 `galtransl.preset.yml` 里**每条预设各自**的 `mcp-galtransl` 行 `command` / `args` / `cwd`
   绝对路径，指向你本机的实际位置（见下节；漏改那条会保留源码路径并可能指向错误位置）。

4. 重启 dsh，在会话的 **Agent 预设** 选择器里二选一：`GalTransl Coder` 或 `GalTransl 翻译助手`
   （预设**每会话选择**，且**首轮后锁定**——要换预设请开新会话）。

### 备选：直接并入 profile 的 patch

不想用 include，就把 `cordis.patch.yml` 里 `- insert:` 那一段原样并入
`$DSH_HOME\profiles\<name>\cordis.patch.yml`。代价是**换 profile 要重做一遍**。

> ⚠️ 外部/新增行**必须放在 `insert:` 列表里**——裸写 `- id: X / name: Y` 会被当成
> 对已有条目的覆写，tree 中不存在时报 `entry not found` 且不插入。

## 实测踩过的两个坑（务必知道）

用 `cordis:include` 时，下面两条**都不是直觉行为**，实测确认：

1. **被 include 的文件必须放在 `profiles\` 目录内**（或其 `node_modules` 可解析的位置）。
   放到别处会报 `preset-galtransl (@deepseek-ai/dsh-agent-preset): failed to import`
   （报错里的 preset id 取决于哪条条目先失败；两条预设都会各自报一次）——
   因为**被 include 文件里的裸包名（`@deepseek-ai/dsh-*`）不按该文件所在目录解析**，
   而是走宿主模块管线（包名占位在 `$DSH_HOME/profiles/node_modules`）。

2. **`path` 是相对 profile 目录解析的，不是相对 patch 文件**。
   所以用绝对 `file:///` URL 最稳（写成裸 Windows 路径会报
   `ERR_INVALID_URL_SCHEME: The URL must be of scheme file`）。

另外：目标文件必须**顶层就是数组的条目清单**——写 `- insert:` 会被拒绝
（`config file must be a top-level array of entries`）。`galtransl.preset.yml` 已是正确格式。

## 自检

- 安装 A：`plugin_manager` 的 `list_bundles` 应出现 `@local/dsh-galtransl-preset`；
  `cordis_inspect_query`（Host `Config.listConfigs`，`name: @deepseek-ai/dsh-agent-preset`）
  应出现 `patchId: preset-galtransl-coder` 与 `patchId: preset-galtransl` 两条。MCP 是否真的连上，
  看程序目录 `mcp_status.json`：`updated_at` 在 90 秒内且 `tools` 为实际启用数，
  即说明会话初始化时握手成功。
- 安装 B：`dsh --profile <name> --dump-config` 应能看到 `id: preset-galtransl-coder` 与
  `id: preset-galtransl` 两个区块及其全部子行。两块内**都没有** Shell / 网页 / 插件管理工具；
  `preset-galtransl-coder` 内应**有** `tool-fs` 一行，`preset-galtransl` 内应**没有**。
  输出里应有 `# == C:\Users\<你>\.dsh\cordis.patch.yml` 归属头。
- ⚠️ **`--dump-config` 看不到 include 展开的内容**（它只渲染该 include 行本身）。
  要确认预设**真的被加载**，必须实际启动，看 stderr 有无 `failed to import`，
  并确认日志里出现 `[galtransl-mcp] 启动 stdio 服务`。
- ⚠️ **`desktop` profile 不能用 CLI 自检**：它由 Electron 独占，CLI 会报
  `profile "desktop" is managed exclusively by the Electron application`。
  桌面用户走安装 A 的 `list_bundles` + 心跳两条，或直接看 **Agent 预设选择器里有没有
  `GalTransl Coder` 与 `GalTransl 翻译助手`**（预设注册成功就一定出现在列表里，没有「隐藏」开关字段）。
- **coder 的文件工具要单独冒烟**（这两条无法由 CLI 自检，只能实际开一个 coder 会话）：
  1. `read` 一个仓库文件应返回带行号的内容 —— 证明预设作用域能解析到宿主层的 `fs` 后端；
  2. 直接 `edit` 一个**未读过**的文件应报 `FS_NOT_OBSERVED` —— 证明 `fs-observation-policy` 生效；
  3. `read_image` 一张 png 能返回图片引用 —— 证明附件存储与图片路由可用（文本路由会明确拒绝）。
  三条任一不成立，说明宿主层 `fs-sandbox` / `fs-observation-policy` / 附件存储没就位，
  需在 profile 里补挂，而不是改本预设。
- **两种预设各开一个会话会起两个 MCP 进程**：每条预设各挂一份 `serverName: galtransl` 的客户端，
  会话之间互相独立，`mcp_status.json` 会被后启动的那个覆盖心跳（这是既有行为，不是本批改动引入）。
  排查「MCP 是否连上」时以**当前会话**的时间戳为准。

## 必须改的路径

`galtransl.preset.yml` 里**每条预设各一处** `@deepseek-ai/dsh-mcp-client` 行的
`command` / `args` / `cwd`（`galtransl-coder` 与 `galtransl` 都要改，且两条应保持一致）：

- **源码模式**：指向 `venv\Scripts\python.exe` 与 `run_mcp_server.py`。
- **打包版**：`command` 指向 `<程序根>\backend\galtransl_mcp.exe`，`args: []`。

> `command` 含路径分隔符时**必须写绝对路径**：dsh 的 subprocess provider 只对裸名查 `PATH`，
> 相对路径因解析基准未定义会被直接拒绝。

## 预设内容说明

两条预设的行清单，差异只在 `tool-fs`：

| 行 | bundle | 作用 | coder | 翻译助手 |
|---|---|---|---|---|
| `persona` | `@deepseek-ai/dsh-persona` | 系统提示词（GalTransl 硬性契约）。字段是 `prefix`，**不是** `text` | ✅ | ✅ |
| `agent-instructions` | `@deepseek-ai/dsh-agent-instructions` | AGENTS.md 发现（`maxBytes` 必须重申） | ✅ | ✅ |
| `mcp-galtransl` | `@deepseek-ai/dsh-mcp-client` | 连 GalTransl MCP（stdio） | ✅ | ✅ |
| `tool-fs` | `@deepseek-ai/dsh-tool-fs` | `read` / `read_image` / `write` / `edit`（通用读写文件） | ✅ | ❌ |
| `tool-ask-user` | `@deepseek-ai/dsh-tool-ask-user` | 向用户提问（唯一面向用户的交互工具） | ✅ | ✅ |
| `tool-subagent` | `@deepseek-ai/dsh-tool-subagent` | `subagent`：`spawn` 后端派子代理，`maxDepth: 1`（不能再派生）。不单独选预设 → 继承本预设 | ✅ | ✅ |
| `tool-subagent-control` | `@deepseek-ai/dsh-tool-subagent-control` | `send_message` / `interrupt_agent`：子代理回报结果、父会话追加指令或叫停 | ✅ | ✅ |

两条预设的 persona 差异：

- **coder**：身份是「翻译助手 + 程序员」，可以**主动**给出针对 GalTransl 程序的建议
  （要指到文件/函数、给验证方式，不擅自展开重构），并写明文件工具的使用边界
  （覆盖前必须先 `read`、只改授权文件、不碰 `backend_profiles.yaml` / `app_settings.json`）。
- **翻译助手**：身份是「只做翻译助手」，**不主动**给程序建议（除非用户主动要求），
  且明确「本会话没有文件读写、Shell、网页抓取工具」。

两条共用的段落（子代理串行约定、硬性约束、H 门禁、缓存结构与阶段判定）由测试锁定为**逐字一致**：
`tests/test_dsh_preset_package.py::PersonaFieldTests::test_shared_tail_is_identical_across_presets`。

**刻意不挂载**（下列均为**行 id**）：`tool-fs-search`、`tool-bash`、`tool-pwsh`、
`tool-jobs`、`tool-web`、`tool-skill`、`skill-filesystem`、`tool-goal`、`tool-todo`、
`tool-present`、`tool-str-replace-editor`、`tool-cordis`、`planning`（plan-mode）、
`compaction`（compaction-basic / command-compact / tool-result-pruner）、
`tool-subagent-fork`（fork 后端会把当前对话前缀喂给子代理，本预设只用 `spawn`）、
`tool-subagent-list-agents`、`tool-agent-team` / `tool-workflow` / `tool-ralph`（多代理编排）、
`tool-plugin-manager`（此行的 bundle 是 `@deepseek-ai/dsh-plugin-manager/tools`，
与其他行的 `dsh-tool-*` 命名不同）。`tool-fs` 只有 coder 挂。

> 这份清单不必手动维护完整：回归测试用的是**白名单**
> （按预设分叉的 `ALLOWED_PLUGIN_BUNDLES`），所以 dsh 将来新增任何工具都不会让测试静默失效。

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
  「GalTransl 工具 + 通用编码能力」的混合体已由 `galtransl-coder` 提供；
  若要更进一步（例如加 `tool-fs-search` 全仓检索），请用 dsh 的新增预设功能自行编写。
- **coder 的文件边界是约定 + 宿主策略，不是硬隔离**：`tool-fs` 没有「只允许某目录」的
  预设级配置，实际防线是 persona（只改授权文件、不碰凭据）+ 宿主 `fs-observation-policy`
  + 沙箱默认模式（可被会话覆盖）。要绝对只读，请选 `GalTransl 翻译助手`。
- **coder 没有 Shell**：它能读写文件但不能跑测试/构建/`git`；需要执行命令时让用户自己跑，
  或在会话里另行授权。这也是它无法自行验证改动的固有限制，persona 已要求它给出验证方式而非假装跑过。
- **子代理默认串行是 persona 约定，不是硬机制**：官方 `subagent` 工具调用即返回
  （`delivery: "parent"`），没有同步参数；「一轮最多派 1 个、派完结束本轮等通知」写在 persona 里靠模型遵守。
  宿主侧 `subagent.maxActiveSubagents`（默认 8）才是真正的并发上限，本预设不改它。
- **子代理的写边界同样靠约定**：子代理继承本预设，因此同样持有 8 个 MCP 写工具
  （coder 还持有文件工具），dsh 侧没有「按工具名禁用」的能力（`toolFilter` 已废弃）。
  「一个文件 / 一个 index 区间只派一个子代理」「`submit_job` 只由主会话调」写在 persona 里。
  需要强隔离时，可另建一个只连 MCP、不挂子代理的预设；若要让子代理改走那个预设，
  需同时加 `agentPreset` 与 `allowedAgentPresets`（`agentPreset` 必须在允许列表内，
  否则预设行激活报错），并同步放宽 `tests/test_dsh_preset_package.py` 里禁止这两个键的安全断言。
  本预设默认不做这一步。
- **home 层对所有 profile 生效**：若你已在用 home 层，需自行把 include 行合并进去。

## 卸载

删除 `$DSH_HOME\cordis.patch.yml` 里那段 include 条目与
`$DSH_HOME\profiles\galtransl.preset.yml`，重启 dsh。
