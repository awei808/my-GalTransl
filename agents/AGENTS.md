# my-GalTransl Agent 外置接入（agents/ + tools/）

程序本体不内置 Agent。Agent 能力通过 MCP 对外提供，官方外置 Agent 为 **dsh（DeepSeek Harness）**。改动本目录前先读仓库根 `AGENTS.md` 的硬性规则。

| 文件 | 职责 |
|---|---|
| `agents/dsh-preset/galtransl.preset.yml` | **唯一真相源**：dsh Agent 预设声明本体，顶层为条目清单（`cordis:include` 的目标格式）。只挂 `persona` / `agent-instructions` / `mcp-galtransl` / `tool-ask-user` 四行——文件、Shell、子代理、网页工具**根本不挂载**，这是 dsh 侧唯一可靠的收窄机制（顶层工具目录没有 allow/deny 过滤器） |
| `agents/dsh-preset/cordis.patch.yml` | 由上面那份**生成**的同内容 patch（带 `- insert:` 包裹），同时充当 bundle patch。**请勿手改** |
| `agents/dsh-preset/package.json` | **bundle 清单**：`dsh.bundle.patch` 指向生成物，使本目录可被 dsh 插件管理器直接安装（桌面版一键装，无需复制文件/重启） |
| `agents/dsh-preset/locale/*.json`、`icon.svg` | 插件管理器与设置里显示的标题/描述/图标（显示资源清单见 dsh `host-plugin` 规范） |
| `agents/dsh-preset/README.md` | 安装说明（A：bundle 安装，仅当前 profile；B：home 层 `cordis:include`，全 profile）与两个实测坑（include 文件须放 `profiles\` 内；`path` 相对 profile 目录解析，需 `file:///`） |
| `tools/build_dsh_preset.py` | 从真相源生成 `cordis.patch.yml`；`--check` 校验同步 |

**改动流程**：只改真相源 `galtransl.preset.yml`；改完重跑 `python tools/build_dsh_preset.py` 重新生成 `cordis.patch.yml`，请勿手改生成物；回归测试 `tests/test_dsh_preset_package.py`（`python run_pytest_utf8.py tests/test_dsh_preset_package.py`，含 bundle 清单与显示资源断言）。

**安装**：`plugin_manager` 的 `install_bundle`，`target` 指向 `agents/dsh-preset` 绝对路径。装完新开会话在 Agent 预设选择器里选 `GalTransl`；MCP 是否连上以程序目录 `mcp_status.json` 心跳（90 秒新鲜 + 工具数）为准。
