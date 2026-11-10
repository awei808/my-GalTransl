# my-GalTransl Agent 外置接入（agents/ + tools/）

程序本体不内置 Agent。Agent 能力通过 MCP 对外提供，官方外置 Agent 为 **dsh（DeepSeek Harness）**。改动本目录前先读仓库根 `AGENTS.md` 的硬性规则。

| 文件 | 职责 |
|---|---|
| `agents/dsh-preset/galtransl.preset.yml` | **唯一真相源**：dsh Agent 预设声明本体，顶层为条目清单（`cordis:include` 的目标格式），内含**两条预设**：`galtransl-coder`（翻译助手 + 程序员，额外挂 `tool-fs` 通用文件工具）与 `galtransl`（纯翻译助手，只有 MCP 工具）。只挂 `persona` / `agent-instructions` / `mcp-galtransl` / `tool-fs`（仅 coder）/ `tool-ask-user` / `tool-subagent` / `tool-subagent-control`——Shell、网页工具**根本不挂载**，这是 dsh 侧唯一可靠的收窄机制（顶层工具目录没有 allow/deny 过滤器）。子代理用 `spawn` 后端且不单独选预设，继承本预设的同一工具面，`maxDepth: 1` 不能再派生；官方 subagent 工具无同步模式，两条预设都靠 persona 约定「一轮最多派 1 个、派完结束本轮等通知」 |
| `agents/dsh-preset/cordis.patch.yml` | 由上面那份**生成**的同内容 patch（带 `- insert:` 包裹，含两条预设），同时充当 bundle patch。**请勿手改** |
| `agents/dsh-preset/package.json` | **bundle 清单**：`dsh.bundle.patch` 指向生成物，使本目录可被 dsh 插件管理器直接安装（桌面版一键装，无需复制文件/重启） |
| `agents/dsh-preset/locale/*.json`、`icon.svg` | 插件管理器与设置里显示的标题/描述/图标（显示资源清单见 dsh `host-plugin` 规范） |
| `agents/dsh-preset/README.md` | 安装说明（A：bundle 安装，仅当前 profile；B：home 层 `cordis:include`，全 profile）与两个实测坑（include 文件须放 `profiles\` 内；`path` 相对 profile 目录解析，需 `file:///`） |
| `tools/build_dsh_preset.py` | 从真相源生成 `cordis.patch.yml`；`--check` 校验同步 |

**发布打包**：`build_release_py312.py` 会把本目录打成 `GalTransl_{版本}_dsh-preset.zip`（bundle，MCP 路径改写为打包版 exe 占位符），并从同一真相源生成 `GalTransl_{版本}_preset-plain.zip`（预设明文，供非 dsh 的自定义预设 Agent 改写）。这两份 zip 内的 `command`/`cwd` 都是占位路径，用户需改成自己的程序根。

**改动流程**：只改真相源 `galtransl.preset.yml`；改完重跑 `python tools/build_dsh_preset.py` 重新生成 `cordis.patch.yml`，请勿手改生成物；回归测试 `tests/test_dsh_preset_package.py`（`python run_pytest_utf8.py tests/test_dsh_preset_package.py`，含两条预设各自的工具白名单、persona 断言与共用段一致性）。两条预设的「硬性约束」及之后段落由测试锁定为逐字一致，改一条必须同步另一条。

**安装**：`plugin_manager` 的 `install_bundle`，`target` 指向 `agents/dsh-preset` 绝对路径。装完新开会话在 Agent 预设选择器里二选一：`GalTransl Coder`（可读写文件、可主动给程序建议）或 `GalTransl 翻译助手`（只做翻译、不主动给程序建议）；MCP 是否连上以程序目录 `mcp_status.json` 心跳（90 秒新鲜 + 工具数）为准。
