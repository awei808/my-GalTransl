# 项目规则（AGENTS.md）

本文件是入口地图：只保留全局硬性规则与任务路由，架构细节一律在子文档（见「任务路由表」）。以下规则在本项目（`D:\解包或汉化用\my-galtransl\my-GalTransl`）内始终生效，除非用户另行指示。

## 项目概述

GalTransl：Galgame（视觉小说）自动翻译工具，利用 LLM（OpenAI 兼容后端）+ 提示词工程多阶段流水线翻译 name-message JSON。Python 3.12 后端（`GalTransl/`）+ Tauri 2/SolidJS 桌面前端（`desktop/`），HTTP REST 通信（默认 127.0.0.1:12333）。程序本体不内置 Agent，Agent 能力经 MCP 对外提供。

## 硬性规则（始终生效）

### 代码修改规则

修改代码前，先分析修改的风险、收益、可改进处，然后深度找修改可能带来的潜在bug，思考怎样避免bug、有什么可改进处、输出哪些日志及日志对应的层级。最后给出计划，经过我批准后才可以实际改动。

### 代码审查规则

审查代码从以下方面审查：
- 有没有基础的函数缺少参数、引用依赖缺失、口径不一致、配置无法读取、前后端传输数据传错接口和位置等基本问题；
- 有没有集成问题、状态残留问题、竞态、测试缺失、与板块内的其他功能交互缺失等常见问题；
- 是否采用项目内统一的架构设计；
- 若有其他问题，一并指出。

### 自动审查规则

每次完成代码修改、且在向用户汇报"完成"之前，必须先启动子代理按上方「代码审查规则」对本次全部改动（以 `git diff` / `git status` 确定的范围为口径）执行一遍自查（子代理无需跑测试，测试在主代理中跑），并在最终回复中给出审查结论（通过，或发现的问题列表）。未做审查不得直接汇报完成。仅改动文档、配置注释等非代码文件时，只需简要确认无误，不必走完整审查流程。审查范围跨前后端时，先读对应两份架构文档再审查。

### 命令行指令规则

程序项目目录是 `D:\解包或汉化用\my-galtransl\my-GalTransl`，有中文，需避免与中文路径冲突的命令行指令，尽量使用相对路径而非绝对路径。
`D:\解包或汉化用\xp3专用汉化文件夹\gal翻译\GalTransl_0.1.0_win.小粥3全量\小粥3-全量` 是测试用的翻译项目目录。
`run_pytest_utf8.py`、`run_vitest.py` 分别可以绕过 powershell 跑对应测试，避免 powershell 的编码问题。

### 查找bug规则

- 已接入 mcp，可使用 mcp 工具辅助查找和复现 bug。
- 在翻译项目目录（`D:\解包或汉化用\xp3专用汉化文件夹\gal翻译\test-dev`）中有日志 `GalTransl.log`、`api_calls.log`、`frontend.log` 可以查看。
- 搜索和定位代码不要使用 powershell5，已安装pwsh(powershell7)，建议使用git bash和pwsh。

### 代码八荣八耻

- 以臆猜接口为耻，以查档求证为荣
- 以模糊开工为耻，以对齐需求为荣
- 以脑补业务为耻，以请示规则为荣
- 以新增冗余为耻，以复用存量为荣
- 以省略校验为耻，以完备测例为荣
- 以乱改架构为耻，以恪守规范为荣
- 以不懂装懂为耻，以坦诚存疑为荣
- 以批量乱改为耻，以分步迭代为荣

## 任务路由表（改动前先读对应文档）

| 任务 | 先读 |
|---|---|
| 后端（`GalTransl/`、`run_backend.py`、翻译流水线/字典/缓存/MCP） | `GalTransl/AGENTS.md`（架构、模块表、9 阶段流水线、数据模型、API 端点表、并发/字典/插件、Python 代码风格规范） |
| 前端（`desktop/`：页面/store/API 封装/Tauri 壳） | `desktop/AGENTS.md` |
| dsh 预设（`agents/`、`tools/build_dsh_preset.py`） | `agents/AGENTS.md` |
| API 新增或修改（跨端） | 两份都读（端点定义唯一归属 `GalTransl/AGENTS.md`） |
| 构建/发版 | 本文件「全局命令」+ 两份子文档「开发与运行」 |

## 全局命令

- 后端测试（务必用 runner 规避 PowerShell 编码问题）：`python run_pytest_utf8.py [pytest 参数]`
- 前端测试/检查：`cd desktop && npm run test`（也可用仓库根 `python run_vitest.py`）；`npm run typecheck` / `lint` / `format`
- 构建：`python build_release_py312.py`（`--skip-fe` 仅后端 / `--skip-be` 仅前端 / `--clean` / `--no-zip` / `--no-smoke` / `--allow-incomplete`；`.venv-build` 每次强制重建干净虚拟环境。旧版 `build_release.py` 未同步且不产 MCP exe/guides/skills，勿用）

## 更新协议

- 新规则、新架构说明一律写进对应子文档，本文件只登记路由与硬规则，不追加内容。
- 本文件超过 150 行时先下沉再新增。
- 所有文档避免写会过时的计数（测试文件数、视图数、工具数、版本号），用目录/常量引用代替；架构说明就近放在对应子目录的文档中维护。
