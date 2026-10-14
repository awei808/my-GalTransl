# my-GalTransl Agent 外置接入（agents/ + tools/）

程序本体不内置 Agent。Agent 能力通过 MCP 对外提供，官方外置 Agent 为 **dsh（DeepSeek Harness）**。改动本目录前先读仓库根 `AGENTS.md` 的硬性规则。

| 文件 | 职责 |
|---|---|
| `agents/dsh-preset/galtransl.preset.yml` | **唯一真相源**：dsh Agent 预设声明本体，顶层为条目清单（`cordis:include` 的目标格式）。只挂 `persona` / `agent-instructions` / `mcp-galtransl` / `tool-ask-user` 四行——文件、Shell、子代理、网页工具**根本不挂载**，这是 dsh 侧唯一可靠的收窄机制（顶层工具目录没有 allow/deny 过滤器） |
| `agents/dsh-preset/cordis.patch.yml` | 由上面那份**生成**的同内容 patch（带 `- insert:` 包裹）。**请勿手改** |
| `agents/dsh-preset/README.md` | 安装说明（home 层 `cordis:include`，用户只需新建 1 个文件）与两个实测坑（include 文件须放 `profiles\` 内；`path` 相对 profile 目录解析，需 `file:///`） |
| `tools/build_dsh_preset.py` | 从真相源生成 `cordis.patch.yml`；`--check` 校验同步 |

**改动流程**：只改真相源 `galtransl.preset.yml`；改完重跑 `python tools/build_dsh_preset.py` 重新生成 `cordis.patch.yml`，请勿手改生成物；回归测试 `tests/test_dsh_preset_package.py`（`python run_pytest_utf8.py tests/test_dsh_preset_package.py`）。
