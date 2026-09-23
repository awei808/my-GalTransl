# MCP 约束下发计划（把外接 agent 约束从文档落到协议）

> 状态：**待用户裁决**（见 §0）—— 批准后按 §4 实施。
> 版本定位：建议与 **0.6.1（H 门禁）** 同期；若需先落地"软约束"，可作 **0.5.2** 补丁单独发（见 §10）。
> 与既有计划的关系：本计划是 `docx/0.5.1-MCP接入计划.md` 的后续，**不改动 11 个工具的读写语义、不改动任何业务逻辑**，只改"下发什么约束"。
> 前序成果：`skills/galtransl-mcp/SKILL.md` 已写入「硬性约束与禁止查看」章节（6 条禁区 + 回绝口径）。

---

## §0 决策总表（待裁决）

| # | 决策点 | 选项 | 建议 |
|---|---|---|---|
| 1 | 下发通道 | A 只改 `instructions` / B `instructions` + `annotations` / C 只改 `annotations` | **B**（两个通道互补，理由见 §1.3） |
| 2 | 约束文案放哪 | A 硬编码在 `run_mcp_server.py` / B 放 `mcp_tools.py` 常量 | **B**（保持工具层无传输依赖，且可被 0.6.0 内置 agent 复用） |
| 3 | annotations 来源 | A 逐工具新写一份 / B 由既有 `kind: "read"` 派生 | **B**（`kind` 已是 read 标记，避免两处口径） |
| 4 | 版本定位 | A 0.5.2 补丁 / B 0.6.1 同期 | **B**（annotations 只是 hint，真强制在 H 门禁；但若你想尽快对外声明约束则选 A） |
| 5 | 是否把 `api_calls.log` 加进 `search_logs` 的 source | A 不加 / B 加 | **A**（该文件含原文/译文与端点，见 §3） |

---

## §1 问题：为什么必须做这一步

### 1.1 skill 文件管不到外接 agent

`skills/galtransl-mcp/SKILL.md` 只有**能读到本仓库**的 agent 才会遵守。外接客户端（CodeBuddy / Claude Desktop / Cherry Studio）各自的 skill 目录在**用户级**，不会自动读取本仓库的 `skills/`。也就是说：现在写完的约束章节，对"真正的外接 agent"**默认不可见**。

### 1.2 协议层唯一的下发通道现状

`run_mcp_server.py:111` 已经在下发 `instructions`，但内容只有一句：

```python
_LIST_TOOLS_TIMEOUT_NOTE = "所有工具均需提供项目根目录绝对路径 project_dir"   # run_mcp_server.py:46
```

**没有任何约束**。同时 `MCP_TOOL_DEFS` 里每个工具的 `kind: "read"`（`GalTransl/mcp_tools.py:377`）**从未下发**——`handle_list_tools` 只映射了 `name` / `description` / `input_schema` 三个字段（`run_mcp_server.py:49-62`），`kind` 是死字段。

### 1.3 两条通道互补（决策 1 的依据）

| 通道 | 生效范围 | 强度 | 版本依赖 |
|---|---|---|---|
| `instructions` | 自然语言，多数客户端注入系统提示 | 软（模型可能忽略） | ⚠️ 仅握手协议（≤2025-11-25）；`InitializeResult.instructions` 在 **2026-07-28 已移除**（SDK 原文：*"Removed in protocol 2026-07-28"*），现代协议改走 `server/discover` → `DiscoverResult.instructions` |
| `Tool.annotations` | 结构化字段，客户端据此提示用户授权 | 软（协议明确是 hint） | 随 `tools/list` 下发，SDK 序列化**不按协议版本裁剪**（`mcp/server/runner.py:118` 统一 `model_dump(by_alias=True, exclude_none=True)`） |

**结论**：两者都不能替代服务端强制过滤，但**只有 `annotations` 是与协议版本无关的通道**，故建议 B（两条都做）。

---

## §2 前置核实（已完成，可复核）

| # | 核实项 | 结果 | 依据 |
|---|---|---|---|
| 1 | `Tool.annotations` 是否存在 | 存在，类型 `ToolAnnotations \| None` | `mcp_types/_types.py:1411`（Tool）/ `:1365`（ToolAnnotations） |
| 2 | `ToolAnnotations` 字段名 | `title` / `read_only_hint` / `destructive_hint` / `idempotent_hint` / `open_world_hint`（**无 `description`**） | 同上 `:1365-1409` |
| 3 | 构造用 snake_case 还是 camelCase | 两者皆可：`MCPModel.model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)`；线上下发为 camelCase（`readOnlyHint`） | `mcp_types/_types.py:45-48` |
| 4 | `instructions` 下发路径 | `Server(instructions=...)` → `InitializationOptions.instructions` → `InitializeResult.instructions` | `mcp/server/runner.py:443`；`mcp_types/_types.py:539-548` |
| 5 | 现代协议（2026-07-28） | `initialize` 已移除，改 `server/discover` → `DiscoverResult.instructions` | `mcp_types/_types.py:593-604` |
| 6 | SDK 的 `Server` 是否分发 `server/discover` | **未发现**（`mcp/server/*.py` 无该 method 分发）→ 现代协议客户端可能收不到 `instructions` | 全文检索 `server/discover` 仅命中一处注释 |
| 7 | 传输层测试覆盖 | **`run_mcp_server.py` 零单测**（`tests/` 无任何引用） | `tests/` 检索 `run_mcp_server` 为空 |
| 8 | 测试套件是否依赖 `mcp` 包 | **当前不依赖**（`test_mcp_tools.py` 只 import `GalTransl.mcp_tools`，该模块无 mcp 依赖） | `GalTransl/mcp_tools.py:1-33` 导入段 |
| 9 | 已锁定的既有测例 | `test_defs_and_handlers_match` / `test_tool_count_is_eleven` / `test_every_tool_is_read_only_kind` / `test_every_def_has_valid_schema` | `tests/test_mcp_tools.py:33/37/57/46` |

> 核实项 7/8 直接决定 §4 的分层：**约束常量与 annotations 映射必须放在不依赖 mcp 的 `mcp_tools.py`**，否则给测试套件引入 `mcp` 包硬依赖。

---

## §3 `api_calls.log` 敏感数据实测（回答提问）

抽样：`test-dev/api_calls.log`，14352 行 / 0.87 MB（真实项目，非构造样本）。

| # | 敏感数据 | 出现位置 | 实测证据 | 危害 |
|---|---|---|---|---|
| 1 | **API 端点完整 URL**（服务商域名 / 中转域名 / 本机端点） | 每个 `>>>` 请求头行的 `endpoint` 字段 | 含 URL 行 **88 = `>>>` 表头行 88**，非表头行 **0**；其中 `127.0.0.1` **78 条**（本地推理端点） | 暴露用户的模型供应商与自建/中转基础设施 |
| 2 | **原文 + 译文正文（含 H 内容）** | `-REQ` 块（`prompt`）+ `-RESP` 块（`response`） | 代码：`_prompt_snip = str(messages[-1]["content"])`（`BaseEngine.py:1121-1131`）、`response_preview=result`（`:1311`） | **最高**：成人向原文/译文逐句落盘 |
| 3 | **术语表 / 角色名 / 翻译规范 / 批次元数据** | `-REQ` 块内注入段 | 提示词模板注入 `<glossary>`、角色、规范、`src`+`dst` jsonline（`Prompts.py:FORGAL_JSON_TRANS_PROMPT`） | 未公开设定外泄 |
| 4 | **思考链内容** | `-REASONING` 块（`reasoning` 参数） | `ApiLogger.py:222-233` 单独标记输出；本次样本为 0（非思考模型） | 思考链可能复述 H 内容与提示词内部细节 |
| 5 | **文件名 + 行号区间** | 请求头 `file=` 字段 | 实测 `file=00_03_華恋との出会い.txt.json:43~63` | 剧情结构、角色名剧透 |
| 6 | **后端引擎名 + 模型名** | 请求头 | 实测 `ForSemCheck gemma-3-270m-it-q4_k_m` | 成本与配置信息 |
| 7 | **错误正文** | `<<<` 行下方 | `error=(str(e) or "")[:2000]`（`BaseEngine.py:1469`）；本次抽样 5 条正文均不含 URL | 可能含服务商返回的账号/配额/请求 ID |

### 3.1 反证式结论：**不含明文密钥**

实测 `sk-[A-Za-z0-9]` / `Bearer` / `api[_-]?key` / `Authorization` 四类模式在日志中命中 **均为 0 次**。

原因（代码层面）：`api_logger.begin()` 只接收 `endpoint=token.domain`（`BaseEngine.py:1129`），**从不传 `token.token`**。全项目 token 唯一被日志记录的处所是 `BaseEngine.py:1110` 的 `LOGGER.debug(f"Call {token.domain} withs token {token.maskToken()}")`，且经 `maskToken()` 脱敏（`token[:6]+"..."+token[-4:]`），写入的是 `GalTransl.log` 而非 `api_calls.log`。

### 3.2 其他属性

- **位置**：`{project_dir}/api_calls.log`（**在项目目录内**，即 MCP 的 `project_dir` 之下）。
- **写入开关**：`AppSettings.writeApiCallLog`，默认 **false**（`ApiLogger.py:136`）→ 默认不落盘。
- **保留策略**：36h，超 20MB 依次降档 24/18/12/8/4h（`cleanup_api_log`，`ApiLogger.py:347-376`）。
- **与 MCP 的关系**：**当前 MCP 工具不读它**——`galtransl_search_logs` 只认 `GalTransl.log`（engine）与 `frontend.log`（frontend），`_LOG_SOURCES` 枚举里没有 `api_calls.log`（`GalTransl/mcp_tools.py:37`）。

### 3.3 由此得出的约束（已写入 skill，本次计划仅确认）

- 该文件**不得**纳入 MCP 可检索范围（决策 5 选 A）。若将来要加，必须先做字段级脱敏（至少抹掉 `endpoint`、`file` 与 `-REQ`/`-RESP` 正文）。
- 约束文案里应把"API 端点"与"原文/译文"一起列为敏感项，而不是只提密钥——**密钥不在里面，端点和正文才在里面**，这正是"想当然"最容易写反的地方。

---

## §4 修改方案

### 4.1 `GalTransl/mcp_tools.py`（新增，纯常量 + 纯函数，**不 import mcp**）

| 新增项 | 内容 |
|---|---|
| `SERVER_INSTRUCTIONS: str` | §4.3 的文案全文（模块级常量） |
| `READ_ONLY_ANNOTATIONS: Dict[str, Any]` | `{"read_only_hint": True, "destructive_hint": False, "idempotent_hint": True, "open_world_hint": False}`（键名用 SDK 的 snake_case；纯 dict，不依赖 mcp） |
| `def tool_annotations(tool_def: Dict[str, Any]) -> Dict[str, Any]` | 按 `kind` 派生：`kind == "read"` → 返回 `READ_ONLY_ANNOTATIONS`；其它 kind → 返回 `{}`（为 0.6.0 的 job 类工具留出分支） |

### 4.2 `run_mcp_server.py`（改 2 处）

| 位置 | 现状 | 改为 |
|---|---|---|
| `:46` `_LIST_TOOLS_TIMEOUT_NOTE` | 一句路径提示 | 删除，改用 `mcp_tools.SERVER_INSTRUCTIONS`（现名与内容不符，顺带修正，见 §6） |
| `:111` `Server(..., instructions=_LIST_TOOLS_TIMEOUT_NOTE)` | — | `instructions=SERVER_INSTRUCTIONS` |
| `:54-60` `Tool(...)` | 映射 3 字段 | 补 `annotations=ToolAnnotations(**tool_annotations(item)) or None`（空 dict 时传 `None`，避免下发空对象） |

导入段补 `ToolAnnotations`（来自 `mcp.types`，与既有 `Tool` 同源）。

### 4.3 `instructions` 文案草案（待你定稿）

```
GalTransl 术语与译文检索服务（只读，11 个工具）。所有工具都需提供翻译项目根目录的绝对路径 project_dir。

使用前必须遵守：
1. 全部工具只读：不得写文件、改配置、启停翻译。需要修改请让用户在 GalTransl 界面操作。
2. 禁止查看 H / 成人向内容：本服务读路径无 H 门禁过滤。识别到成人向内容必须立即停止该方向检索，
   不得回引原文或译文，只报告位置（文件名 + index）并请用户决定。
3. project_dir 只能是用户明确指定的翻译项目目录。禁止指向 GalTransl 程序目录（其 backend_profiles.yaml
   含 API 密钥）、仓库根目录、系统目录或他人目录。
4. 禁止读取或外传任何凭据、密钥、API 端点。日志中命中疑似凭据的行不引用原文。
5. 禁止规模化拉取：搜索 max_results 默认 200 / 硬顶 2000，分页 limit 默认 100 / 硬顶 1000。
   不要全量拉取，也不要用宽正则做枚举式扫描。
6. 交付结论 + 定位（文件名 + index + 最短必要引文），不要堆砌原文/译文。

完整约束见随项目分发的 skills/galtransl-mcp/SKILL.md。
```

长度约 460 字（≈700 字节）。**刻意只放"必须知道的硬约束"**，细则留在 skill —— 理由见 §5 R1。

### 4.4 `annotations` 映射结果（11 个工具一致）

```json
{"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}
```

依据：11 个工具全部只读本地磁盘、不改环境、同参数重复调用无额外副作用、不访问开放世界（仅本机文件）。

### 4.5 明确不改

- 不改 `MCP_TOOL_DEFS` 的 `name` / `description` / `input_schema`（避免与既有 36 条测例口径漂移）。
- 不改任何工具实现、`server_search.py`、检索上限。
- 不加任何写工具（作业启停仍留 0.6.0）。
- **不做** H 门禁实现（留 0.6.1，见 §10）。

---

## §5 风险与潜在 bug（含防范）

| # | 风险 | 分析与防范 |
|---|---|---|
| R1 | **`instructions` 被客户端截断或忽略**：文案过长可能被裁，或客户端根本不注入 | 文案压到 ~460 字，只保留 6 条硬约束；细则留 skill 与 `description`。**无法在本机确证各客户端上限**，故不依赖它单独生效（与 annotations 双通道） |
| R2 | **现代协议（2026-07-28）客户端收不到 `instructions`** | SDK 侧未发现 `server/discover` 分发（§2 #6）。annotations 作为版本无关通道兜底；实施时**先核实** `mcp_types` 是否提供现代协议入口，不得假设 |
| R3 | **把 annotations 当成门禁**（最危险） | SDK 原文明确 *"Clients should never make tool use decisions based on ToolAnnotations received from untrusted servers"*，且 MCP 规范称其为 hint。**必须在计划、提交信息与 skill 中都写明：真正的 H 过滤只能在 0.6.1 服务端实现**，本次仅是"声明与提示" |
| R4 | **约束文案与实际实现不符**（写成"已过滤 H"） | 文案第 2 条**如实写"无 H 门禁过滤"**——承诺了却没实现比不写更糟（合规错觉） |
| R5 | **给测试套件引入 `mcp` 包硬依赖** | 现状 `tests/` 不依赖 `mcp`（§2 #8）。因此约束常量与映射函数放 `mcp_tools.py`（无 mcp 导入），测例只测纯函数；**不新增 import `run_mcp_server` 的单测** |
| R6 | **`tool_annotations` 对非 read 工具返回空**导致下发 `{}` | 传参处显式 `or None`，避免下发空对象（空对象在部分客户端会被判为"未声明"而非"只读"） |
| R7 | **改 `_LIST_TOOLS_TIMEOUT_NOTE` 名字导致引用漏改** | 全仓仅 `run_mcp_server.py:46`（定义）与 `:111`（使用）两处，改后 grep 确认为 0 残留 |
| R8 | **工具清单口径漂移** | 既有 `test_defs_and_handlers_match` 锁 defs↔handlers 一一对应；新增断言只加"annotations 由 kind 派生"，不改既有断言 |
| R9 | **打包版行为不一致** | `galtransl_mcp.spec` 打包的是 `run_mcp_server.py`，改动随打包生效；构建脚本的 `smoke_test_mcp()` 只验 `initialize` 握手——**建议在冒烟里加一条 `instructions` 非空断言**（可选，见 §6） |
| R10 | **文案含中文**，部分客户端/模型对中文系统提示处理不佳 | 项目既有 11 个工具 description 与接入文档均为中文，保持一致优先；如需英文版可后置（见 §6） |

---

## §6 可改进处（顺带，需你裁决是否纳入本次）

| # | 项 | 收益 |
|---|---|---|
| 1 | `_LIST_TOOLS_TIMEOUT_NOTE` → `SERVER_INSTRUCTIONS` | 现名（"timeout note"）与内容（路径提示）完全不符，属既有命名债 |
| 2 | `kind: "read"` 由死字段变为 annotations 来源 | 消除"声明了却不生效"的字段，为 0.6.0 白名单复用 |
| 3 | 给每个工具补 `title`（如"检索译文缓存"） | 客户端展示更可读；纯展示，零风险 |
| 4 | `description` 统一补"（只读）" | 与 annotations 双重声明 |
| 5 | 构建冒烟加 `instructions` 非空断言 | 防止打包版漏带约束 |
| 6 | 提供英文版 `instructions`（按客户端 locale 切换不可行，只能二选一或双语） | 外接客户端多为英文界面；但双语会显著加长（R1） |

---

## §7 日志设计

| 级别 | 内容 | 理由 |
|---|---|---|
| `LOGGER.info` | MCP 进程启动时：`[mcp] 已下发约束 instructions（N 字）与 11 个只读 annotations` | 让"到底下发了什么"可事后核查，与心跳/`/api/mcp-status` 对齐 |
| `LOGGER.debug` | `tools/list` 返回时记录首条 tool 的 annotations 键集合 | 排查客户端是否收到结构化声明 |
| — | 不新增 warning / error | 本次无失败路径 |

> 沿用既有口径：`initialize` 记录客户端名与协商版本（0.5.1 计划 §8 已列），本次不重复实现。

---

## §8 测例

| 文件 | 新增用例 | 覆盖 |
|---|---|---|
| `tests/test_mcp_tools.py` | `test_server_instructions_mentions_all_six_constraints` | 文案含 6 个关键词：只读 / H / project_dir / 密钥或端点 / max_results / 定位 |
| 同上 | `test_server_instructions_states_no_h_filter` | 文案**必须**含"无 H 门禁过滤"字样（锁 R4，防文案漂移成"已过滤"） |
| 同上 | `test_read_only_annotations_keys_match_sdk` | 键名集合 == `{read_only_hint, destructive_hint, idempotent_hint, open_world_hint}`（锁 §2 #2 的核实结果，SDK 升级改名时立刻暴露） |
| 同上 | `test_tool_annotations_derives_from_kind` | 11 个 `kind=="read"` 全部派生为只读 annotations；构造一个 `kind=="job"` 的桩 → 返回空 dict |
| 同上 | `test_instructions_length_is_bounded` | 长度上限（建议 ≤ 900 字节）锁 R1 |
| 既有（不改） | `test_defs_and_handlers_match` / `test_tool_count_is_eleven` / `test_every_tool_is_read_only_kind` | 零回归 |

**不新增** import `run_mcp_server` 的单测（R5）；传输层由手工 stdio 探针验证（§9）。

---

## §9 验收标准

1. 后端全量测试 **不低于基线 1708 passed / 237 subtests**，新增用例全绿。
2. 管道联调（stdio 探针，非推测）：
   - `initialize` 响应含非空 `instructions`，且含"无 H 门禁过滤"字样；
   - `tools/list` 返回 **11** 项，每项 `annotations.readOnlyHint == true`；
   - 未知工具仍以 `isError` 返回且**协议连接不中断**（既有行为不回退）。
3. `grep -r "_LIST_TOOLS_TIMEOUT_NOTE"` 结果为 0（R7）。
4. `tests/` 中**无** `import run_mcp_server` / `import mcp`（R5）。
5. 无新增 >2000 行文件；改动文件均不超限。
6. 打包版（若本次出包）：`galtransl_mcp.exe` 的 `initialize` 同样含 `instructions`。

---

## §10 提交粒度与版本定位（决策 4）

| 方案 | 内容 | 适用 |
|---|---|---|
| **A：0.5.2 补丁** | 仅 §4 的改动 + §8 测例 | 想尽快对外声明约束；但此时 H 门禁未落地，约束仍是"软"的 |
| **B：0.6.1 同期（建议）** | §4 改动 + H 门禁（`_apply_h_filter` 落地，`hLevels` + `is_h_value`）+ token 限制 | 约束文案里"无 H 门禁过滤"这句可以改成"已按 h 档位过滤"，**声明与实现一致**；且 0.6.1 本就是"约束"版本 |

建议 **B**，一次提交：`feat: MCP 下发只读声明与硬性约束（instructions + annotations） (#0.6.1)`。

> 若选 A，则文案第 2 条必须原样保留"无 H 门禁过滤"，并在 0.6.1 再改一次（两次改文案，成本可接受但需记账）。

---

## 附录：事实依据（可复核）

- `run_mcp_server.py:46/49-62/107-114`（instructions 现状、Tool 映射、Server 构造）
- `GalTransl/mcp_tools.py:37/371-378/390-511/513-525`（`_LOG_SOURCES`、`_def`/`kind`、`MCP_TOOL_DEFS`、分发表）
- `GalTransl/ApiLogger.py:59-87/136/180-257/347-376`（begin、写入开关、格式化、清理）
- `GalTransl/Backend/BaseEngine.py:1110/1121-1132/1306-1312/1394-1397/1408-1411/1466-1470`（api_logger 调用点与字段来源）
- `GalTransl/Backend/Prompts.py`（`FORGAL_JSON_TRANS_PROMPT` 注入段）
- `venv/Lib/site-packages/mcp_types/_types.py:45-48/539-548/593-604/1365-1409/1411`
- `venv/Lib/site-packages/mcp/server/runner.py:118/443`
- `tests/test_mcp_tools.py:33/37/46/57`（既有锁定口径）
- 实测样本：`test-dev/api_calls.log`（14352 行 / 0.87MB；URL 88 = `>>>` 88；`127.0.0.1` 78；`sk-`/`Bearer`/`api_key`/`Authorization` 均 0）
