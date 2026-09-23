---
name: galtransl-mcp
description: 通过 galtransl MCP 工具检索 GalTransl 翻译项目的术语与译文（翻译缓存、原始脚本、字典、人名表、日志、元数据），并给出外接 agent 必须遵守的硬性约束与禁止查看清单（H 内容、凭据与端点、越界路径、写操作、规模化拉取）。当需要核查某个术语译法是否统一、某个角色名是否已收录、某段原文的现有译文、某条问题译文的位置，或想了解项目配置与流水线阶段时使用。使用本 MCP 前必须先读「硬性约束与禁止查看」一节。
---

# GalTransl 术语与译文检索

本技能通过 MCP 服务 `galtransl` 访问本机 GalTransl 项目（**只读**）。所有工具都需要**项目根目录的绝对路径**（`project_dir`），例如
`D:\解包或汉化用\xp3专用汉化文件夹\gal翻译\test-dev`。

服务不需要 GalTransl 后端在运行——它直接读取项目目录下的文件。

## 硬性约束与禁止查看（先读本节，再调工具）

本节是外接 agent 的**行为契约**。11 个工具全部只读，但「只读」不等于「可以随便读」——下列内容是**禁止查看**的，且不得以任何间接手段绕过。

### 1. 禁止查看：H / 成人向内容（最高优先级）

**事实依据（0.5.1 实测，非推测）**：MCP 读取路径**没有任何 H 门禁过滤**。`docx/MCP接入指南.md` §8 与 0.5.1 接入计划均声明「H 内容门禁只预留拦截点，完整实现排在 0.6.1」，而计划中承诺预留的 `_apply_h_filter()` 在 `GalTransl/mcp_tools.py` 里**实际并不存在**。没有任何一层会替你挡住 H 文本。

可能吐出 H 内容的调用：

| 工具 | 泄漏形态 |
|---|---|
| `galtransl_search_cache` | `post_src`（原文）/ `pre_dst`（译文）本身即成人向台词 |
| `galtransl_search_scripts` | `message` 为未翻译的 H 原文 |
| `galtransl_read_translation_file` / `galtransl_read_source_script` | 整段 H 原文与译文（分页逐条） |
| `galtransl_list_problems` | 问题类型含「用词不当」（按 H 词库匹配），命中条目大概率落在 H 区间 |
| `galtransl_get_project_metadata`（`kind=batchmeta`） | 批次区间带 h 强度档位标注 |

**约束**：

- 不得以「检索术语」「核对一致性」为名，对 H 区间做**主动、成片、逐条**的读取或枚举。
- 识别判据：项目配置 `common.skipH`；batchmeta 的 h 档位（判定线取 `internals.hLevels.intimate`，默认 50/100）；字典 `category` 为 `forbiddenh` / `gpth`。一旦识别出结果属 H 内容，**立即停止该方向**，不得继续翻页扩大样本。
- **不得回引**：不复制 H 原文、不复制 H 译文、不做「原文/译文对照展示」、不做改写或摘要。
- 只允许报告**位置**（文件名 + index + 区间），并明确请用户决定是否处理。
- H 文本不得写入任何外部产物：commit message、issue/PR、报告文档、其他会话上下文、联网请求。
- 用户明确要求处理 H 内容时，说明 0.5.1 的 MCP 读路径无 H 门禁、该能力排在 0.6.1，请其在 GalTransl 界面本地处理，或等门禁落地后再授权。

### 2. 禁止查看：凭据、密钥与端点

| 目标 | 位置 |
|---|---|
| `backend_profiles.yaml` | GalTransl **程序目录**（含 API 密钥，已写入 `.gitignore` 并停止跟踪） |
| `app_settings.json` | 程序目录（应用级设置，非项目文件，不属授权范围） |
| `GALTRANSL_API_TOKEN` | 进程环境变量（后端写端点 Bearer 鉴权） |
| `api_calls.log` | 项目目录（API 调用明细，不在 MCP 工具范围内） |
| API 域名 / 端点 | `GalTransl.log` 可能含 `API URL: <domain>/chat/completions`（检测接口，info 级）与 `Call <domain> ...`（每次调用，debug 级） |

- `project_dir` **禁止**指向 GalTransl 程序目录、仓库根目录、系统目录、盘符根或他人目录；它必须是用户明确指定的**翻译项目目录**。
- 路径防护的口径要说清：只有 `read_translation_file` / `read_source_script` 经 `safe_under_project()` 校验（拒绝绝对路径与 `../`）；**`project_dir` 本身没有白名单校验**，越界责任在使用方。`get_project_metadata` 的 `filename` 是字符串拼接、**不走该防护**，故只允许传裸文件名（见「注意事项」）。
- `galtransl_search_logs` 在 `source: "frontend"` 且项目内无 `frontend.log` 时，会**回退读工作区根目录**的 `frontend.log`。禁止借该回退去读项目外的日志。
- 密钥在日志中是脱敏的（`maskToken()` → `sk-abc...wxyz`），但仍**不得**索取、推断、复述或外传任何密钥、令牌、API 端点；日志里命中疑似凭据的行一律不引用原文。

### 3. 禁止查看：非授权项目与他人数据

- `galtransl_list_projects` 会列出工作区根下**所有**可识别项目。**禁止**把它当作「可自由浏览的清单」——只能用于定位用户当前指定的那一个项目。
- 禁止跨项目聚合、比对或导出内容；禁止遍历用户未提及的项目。

### 4. 禁止：任何写操作与副作用

- 0.5.1 的 11 个工具**全部只读**，没有启动/停止翻译、改配置、写字典、写译文的能力（作业启停与写类工具排在 0.6.0）。
- 禁止绕过 MCP 去达成写效果：不得调用 GalTransl HTTP 写端点（`POST /api/jobs`、`PUT /api/projects/:id/config`、`/cache/save`、`/cache/replace` 等），不得直接编辑项目目录下的文件。
- 用户要求「帮我改一下字典/译文/配置」时，说明本 MCP 只读并请其在 GalTransl 界面操作，不要用任何间接手段代劳。

### 5. 禁止：规模化拉取（上下文纪律）

- 搜索类 `max_results` 默认 200、硬顶 2000；分页 `limit` 默认 100、硬顶 1000。
- 禁止全量拉取（整文件、整项目、全部缓存）；禁止用空查询或 `regex: "."` 之类宽正则做枚举式扫描。
- 交付给用户的应当是**结论 + 定位**（文件名 + index + 最短必要引文），不是原文/译文堆砌。

### 6. 前端绿灯 ≠ 授权

顶部 MCP 指示灯变绿只表示「当前有 MCP 进程连着」（心跳文件 mtime 90 秒内新鲜），**不代表**用户授权了任何检索范围。授权范围由用户显式给出的 `project_dir` 与指令决定。

### 越权请求的标准回绝口径

用户要求触碰上述任一禁区时，按此回应——不要沉默执行，也不要自行扩大解释：

> 该内容不在 galtransl MCP 的授权读取范围内（<禁区名>）。0.5.1 的读路径无 H 门禁、也没有任何写工具，我不会读取或改写它。可以在 GalTransl 界面本地处理，或由你明确授权具体范围后我再继续。

## 工具清单（11 个）

### 搜索

| 工具 | 用途 | 关键参数 |
|---|---|---|
| `galtransl_search_cache` | 检索**已入库译文**（原文/译文/问题/说话人） | `query`、`field`(all/src/dst/problem)、`regex` |
| `galtransl_search_scripts` | 检索**原始脚本**（`gt_input`），未跑过翻译也能用 | `query`、`regex` |
| `galtransl_search_dict` | 检索**字典词条**（项目字典 + 公共 `Dict/`） | `query`、`direction`(any/jp2zh/zh2jp)、`include_common` |
| `galtransl_lookup_name` | 查**角色名译名表**（`name替换表.csv/xlsx`） | `name`（传字符串，见注意事项） |
| `galtransl_search_logs` | 按关键词检索**日志** | `keyword`、`source`(engine/frontend)、`tail` |
| `galtransl_list_problems` | 列出**带问题标记**的缓存条目 | `problem_type`（留空则全部） |

### 只读

| 工具 | 用途 | 关键参数 |
|---|---|---|
| `galtransl_list_projects` | 列出工作区下可识别的项目（拿到 `project_dir`） | `workspace_root`（可选） |
| `galtransl_get_project_overview` | 项目概览：目标语言、每请求条数、注入块开关、文件数、流水线阶段 | — |
| `galtransl_read_translation_file` | 读取某个缓存文件的条目（分页） | `filename`、`offset`、`limit` |
| `galtransl_read_source_script` | 读取某个原始脚本文件的条目（分页） | `filename`、`offset`、`limit` |
| `galtransl_get_project_metadata` | 读元数据：`globalprompt` / `filemeta` / `batchmeta` / `all` | `kind`、`filename` |

## 推荐工作流

### 1. 核查某术语的译法是否统一（最常用）

1. `galtransl_get_project_overview` — 先确认项目结构与目标语言、翻译规范。
2. `galtransl_search_dict`（`direction: "any"`）— 看该术语**是否已条目化**、字典里定的译法是什么。
3. `galtransl_search_cache`（`field: "dst"`）— 看**实际译文**是否都用了这个译法；改用 `field: "src"` 可反查所有出现该原文的位置。
4. 若字典与实际译文不一致 → 明确指出冲突条目（文件名 + index + 现有译文），建议应统一成哪个。

### 2. 核查角色名/称呼

1. `galtransl_lookup_name` — 确认译名表是否收录；`found: false` 表示未收录。
2. `galtransl_search_cache`（`query` 用原文名，或 `field: "src"`）— 看正文里该角色的译文用名是否与译名表一致。
3. 注意说话人字段也可命中：结果的 `match_speaker` 为 true 表示命中的是说话人徽章。

### 3. 定位并理解待修复译文

1. `galtransl_list_problems` — 先看有哪些问题类型（返回 `available_problem_types`）与命中条数。
2. 用 `problem_type` 收窄到某类问题。
3. `galtransl_read_translation_file` 打开命中文件、用 `offset`/`limit` 翻到对应 index 附近，看上下文再给修改建议。

### 4. 只想知道原文，不关心已有译文

`galtransl_search_scripts` 直接搜 `gt_input` 原始脚本，不依赖缓存是否生成。命中结果里的 `index` 与缓存条目的 `index` 同源，可直接对照。

## 注意事项

- **全只读**：没有任何写工具。需要修改字典/译文/配置时，请指导用户在 GalTransl 界面里操作。
- **H 内容**：0.5.1 读路径**无 H 门禁过滤**，禁止主动读取/回引 H 文本——判据与处置见上方「硬性约束与禁止查看」§1。
- **结果上限**：搜索类工具默认最多返回 200 条（硬顶 2000），超出时返回体里 `truncated` 为 `true`，`total` 才是全部命中数。需要更多就加 `regex` 收窄检索词，或提高 `max_results`。
- **正则**：`regex: true` 时检索词按正则解释；非法正则会返回参数错误。
- **大文件**：`read_translation_file` / `read_source_script` 用 `offset`/`limit` 分页，默认每页 100 条（硬顶 1000），不要一次拉全文件。
- **返回字段易误读**：`search_cache` 结果里的 `post_src` 实际装的是**页面可见原文**（`pre_src` 优先），`pre_dst` 装的是 `pre_dst`/`pre_zh`/`proofread_dst` 首个非空；这与 `read_translation_file` 返回的原始条目字段口径**不同**，不要混用。`search_dict` 结果的检索词/替换词键名是 `src`/`dst`，另有 `origin`（project/common）与 `category`（pre/gpth/gptnh/gpt/post/forbiddenh/forbiddennh）。
- **`list_problems` 的 `problem_type` 是正则关键词**，不是枚举值（留空时用 `.+` 命中所有非空 `problem`）；完整枚举清单看返回体里的 `available_problem_types`。
- **`lookup_name` 的 `name` 在 schema 里声明为 `string`**（实现虽容忍数组，但按 schema 校验的客户端会拒绝）：请传单个字符串、多次调用。
- **`get_project_metadata` 的 `filename` 只传裸文件名**（不带路径、不带 `.meta.json`/`.batch.json` 后缀）——该参数未经路径防护，属未受信输入。
