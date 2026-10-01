---
name: galtransl-mcp
description: 通过 galtransl MCP 工具检索 GalTransl 翻译项目的术语与译文（翻译缓存、原始脚本、字典、人名表、日志、元数据），并可写入路线图/元数据、启停翻译任务。含外接 agent 必须遵守的硬性约束与禁止查看清单（H 内容、凭据与端点、越界路径、写操作边界、规模化拉取）。当需要核查某个术语译法是否统一、某个角色名是否已收录、某段原文的现有译文、某条问题译文的位置，或想了解项目配置与流水线阶段、修改剧情路线图、启动/停止翻译时使用。使用本 MCP 前必须先读「硬性约束与禁止查看」一节。
---

# GalTransl 术语与译文检索

本技能通过 MCP 服务 `galtransl` 访问本机 GalTransl 项目。15 个工具中 11 个为**只读**检索，
4 个为**受限写入**（路线图、元数据、启停翻译）。所有工具都需要**项目根目录的绝对路径**（`project_dir`），例如
`D:\解包或汉化用\xp3专用汉化文件夹\gal翻译\test-dev`。

只读工具不需要 GalTransl 后端在运行——它们直接读取项目目录下的文件。
写入作业域的两个工具（`submit_job` / `stop_job`）**需要后端在运行**（作业状态只存在于后端进程内）。

## 硬性约束与禁止查看（先读本节，再调工具）

本节是外接 agent 的**行为契约**。11 个只读工具不因「只读」就可以随便读——下列内容是**禁止查看**的；
4 个写工具也各有明确边界，不得视为通用写权限。

### 1. 禁止查看：H / 成人向内容（最高优先级）

**事实依据（0.6.0 实测）**：MCP **读路径仍然没有 H 门禁过滤**——只读工具会原样返回 H 原文/译文。
但**写工具已有硬门禁**：`galtransl_write_route_map` / `galtransl_save_metadata` 在写入前会检查
目标缓存文件是否落在 H 区间、待写入文本是否命中项目 H 词库（`forbiddenDictH`），命中即**拒绝写入并返回错误**。
即：**读要自己守规矩，写由服务兜底**。

> **门禁的失效边界（务必知道）**：门禁是 **fail-open** 的——判定不了就放行，宁可漏拦也不阻断正常写入。
> 因此当项目**未配置 H 词库**（`forbiddenDictH` / `hCheckDict` 均无）或**尚无批次元数据**时，
> 文件维度与文本维度都判为「非 H」，写工具**实际不会拦截**。
> 即门禁只在「项目已配 H 词库 + 已有批次元数据」时才是有效防线，**不能据此认为 H 一定被挡住**。
> H 内容仍须自行遵守本节 §1。

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
- 写工具被 H 门禁拒绝时，**如实转告用户**「该部分因 H 门禁未执行」，请其在 GalTransl 界面手动处理；**不要**改写措辞绕过门禁重试。


### 2. 禁止查看：凭据、密钥与端点

| 目标 | 位置 |
|---|---|
| `backend_profiles.yaml` | GalTransl **程序目录**（含 API 密钥，已写入 `.gitignore` 并停止跟踪） |
| `app_settings.json` | 程序目录（应用级设置，非项目文件，不属授权范围） |
| `GALTRANSL_API_TOKEN` | 进程环境变量（后端写端点 Bearer 鉴权） |
| `api_calls.log` | 项目目录（API 调用明细，不在 MCP 工具范围内） |
| API 域名 / 端点 | `GalTransl.log` 可能含 `API URL: <domain>/chat/completions`（检测接口，info 级）与 `Call <domain> ...`（每次调用，debug 级） |

- `project_dir` **禁止**指向 GalTransl 程序目录、仓库根目录、系统目录、盘符根或他人目录；它必须是用户明确指定的**翻译项目目录**。
- **写工具的路径白名单（0.6.0）**：4 个写工具会硬校验 `project_dir` 是「可识别的 GalTransl 项目」——即目录下**确实存在** `config.inc.yaml` 或 `config.yaml`，否则拒绝执行。所以把 `project_dir` 指向仓库根、系统目录或空目录时，写工具会直接报错，不会误写外部文件。
- **只读工具不硬拦**：它们只要求目录存在，非法项目时在返回体里给出 `project_dir_valid: false` + `project_dir_hint`（仅告警，不阻断检索）。看到该字段请先与用户确认路径是否正确，不要据此继续扩大检索范围。
- 路径防护的口径要说清：只有 `read_translation_file` / `read_source_script` 经 `safe_under_project()` 校验（拒绝绝对路径与 `../`）；写工具（`save_metadata`）会对 `filename` 做穿越校验，并对 `project_dir` 做上述白名单校验。
- `galtransl_search_logs` 在 `source: "frontend"` 且项目内无 `frontend.log` 时，会**回退读工作区根目录**的 `frontend.log`。禁止借该回退去读项目外的日志。
- 密钥在日志中是脱敏的（`maskToken()` → `sk-abc...wxyz`），但仍**不得**索取、推断、复述或外传任何密钥、令牌、API 端点；日志里命中疑似凭据的行一律不引用原文。

### 3. 禁止查看：非授权项目与他人数据

- `galtransl_list_projects` 会列出工作区根下**所有**可识别项目。**禁止**把它当作「可自由浏览的清单」——只能用于定位用户当前指定的那一个项目。
- 禁止跨项目聚合、比对或导出内容；禁止遍历用户未提及的项目。

### 4. 写操作边界（0.6.0 起有 4 个写工具）

写能力**仅限**下列 4 个工具，每个都只能改项目内的指定产物：

| 写工具 | 能做什么 | 边界 |
|---|---|---|
| `galtransl_write_route_map` | 整体覆盖写剧情路线图 | 只能写 `transl_cache/pass0_cache/PlotRouteMap.json`；mermaid 经生成侧同口径校验 |
| `galtransl_save_metadata` | 原子写单文件元数据 | 只能写 `pass1_cache/*.meta.json`、`pass2_cache/*.batch.json`、`pass0_cache/{PlotRouteMap,GlobalPrompt}.json`；`filename` 经穿越校验 |
| `galtransl_submit_job` | 提交翻译任务（**会真实消耗 API 额度**） | 仅在用户明确要求时调用；调用前确认项目与引擎 |
| `galtransl_stop_job` | 停止项目当前任务 | 无运行中任务时后端返回 409 |

**明确的禁止项**：

- 这些工具**不提供**任意路径读写、**不提供**命令执行、**不修改**程序配置与 API 密钥。
- 写工具只接受**可识别的 GalTransl 项目目录**（含 `config.inc.yaml`/`config.yaml`）。指向别处会被拒绝，**不要**为了绕过而去找一个含配置文件的目录顶替——`project_dir` 必须是用户指定的那一个。
- 禁止绕过 MCP 去达成其它写效果：不得直接调 GalTransl HTTP 写端点（`PUT /api/projects/:id/config`、`/cache/save`、`/cache/replace`、`/cache/delete-*` 等），不得直接编辑项目目录下的文件。上面 4 个工具本身走后端是**实现细节**，不代表你也获得了直接打这些端点的授权。
- 用户要求「帮我改一下字典/译文/配置」时，说明本 MCP 无此能力并请其在 GalTransl 界面操作；**不要**用任何间接手段代劳。
- `submit_job` 是有副作用且耗时的操作：**不要**在用户只是「看看进度」「评估一下」时调用。

### 5. 禁止：规模化拉取（上下文纪律）

- 搜索类 `max_results` 默认 200、硬顶 2000；分页 `limit` 默认 100、硬顶 1000。
- 禁止全量拉取（整文件、整项目、全部缓存）；禁止用空查询或 `regex: "."` 之类宽正则做枚举式扫描。
- 交付给用户的应当是**结论 + 定位**（文件名 + index + 最短必要引文），不是原文/译文堆砌。

### 6. 前端绿灯 ≠ 授权

顶部 MCP 指示灯变绿只表示「当前有 MCP 进程连着」（心跳文件 mtime 90 秒内新鲜），**不代表**用户授权了任何检索范围。授权范围由用户显式给出的 `project_dir` 与指令决定。

### 越权请求的标准回绝口径

用户要求触碰上述任一禁区时，按此回应——不要沉默执行，也不要自行扩大解释：

> 该内容不在 galtransl MCP 的授权范围内（<禁区名>）。我没有任意路径读写或执行命令的能力，也不会绕过去做。可以在 GalTransl 界面本地处理，或由你明确授权具体范围后我再继续。

## 工具清单（15 个）

### 搜索（只读）

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

### 写入（受 H 门禁与路径约束）

| 工具 | 用途 | 关键参数 |
|---|---|---|
| `galtransl_write_route_map` | 整体覆盖写剧情路线图（未提供字段保留旧值） | `mermaid`、`文件归属`、`节点剧情`、`结构类型`、`用户大纲` |
| `galtransl_save_metadata` | 原子写元数据 | `kind`(filemeta/batchmeta/plotroute/globalprompt)、`filename`、`entry` |
| `galtransl_submit_job` | 提交翻译任务（**需后端运行；消耗额度**） | `translator`（必填）、`config_file_name`、`backend_profile`、`file_filter` |
| `galtransl_stop_job` | 停止项目当前任务（**需后端运行**） | — |

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

### 5. 修改剧情路线图（写）

1. `galtransl_get_project_metadata`（`kind: "plotroute"`）或 `galtransl_read_translation_file` — **先读现状**。
2. `galtransl_write_route_map` — **整体覆盖**写入：未被要求修改的字段（`结构类型`/`用户大纲`/`文件归属`/`节点剧情`）必须原样带回，禁止凭空删减路线或文件。
3. 若返回 H 门禁错误 → 如实转告用户，不要改写措辞重试。

### 6. 启动 / 停止翻译（写，消耗额度）

1. 先与用户确认**项目**与**引擎**（`translator`，如 `ForGal-full-pipeline`）；`galtransl_get_project_overview` 可帮助确认项目结构。
2. `galtransl_submit_job` — 返回 `job_id`；可用 `file_filter` 限定文件子集。
3. `galtransl_stop_job` — 需要中止时调用；无运行中任务返回 409。
4. 进度请让用户在 GalTransl 界面查看；本 MCP 无「查进度」工具。

## 注意事项

- **只读 + 受限写入**：11 个只读工具 + 4 个写工具（边界见 §4）。需要改字典/译文/配置时，请指导用户在 GalTransl 界面里操作。
- **H 内容**：读路径**无 H 门禁过滤**（须自行遵守 §1）；写工具**有硬门禁**，命中 H 区间或 H 词库即拒绝写入——但门禁 **fail-open**，项目未配 H 词库时不拦，详见 §1 的失效边界。
- **写工具需后端**：`submit_job` / `stop_job` 需要 GalTransl 程序在运行；后端未启动时会返回「无法连接 GalTransl 后端」的中文错误，此时转告用户启动程序即可，不要反复重试。
- **结果上限**：搜索类工具默认最多返回 200 条（硬顶 2000），超出时返回体里 `truncated` 为 `true`，`total` 才是全部命中数。需要更多就加 `regex` 收窄检索词，或提高 `max_results`。
- **正则**：`regex: true` 时检索词按正则解释；非法正则会返回参数错误。
- **大文件**：`read_translation_file` / `read_source_script` 用 `offset`/`limit` 分页，默认每页 100 条（硬顶 1000），不要一次拉全文件。
- **返回字段易误读**：`search_cache` 结果里的 `post_src` 实际装的是**页面可见原文**（`pre_src` 优先），`pre_dst` 装的是 `pre_dst`/`pre_zh`/`proofread_dst` 首个非空；这与 `read_translation_file` 返回的原始条目字段口径**不同**，不要混用。`search_dict` 结果的检索词/替换词键名是 `src`/`dst`，另有 `origin`（project/common）与 `category`（pre/gpth/gptnh/gpt/post/forbiddenh/forbiddennh）。
- **`list_problems` 的 `problem_type` 是正则关键词**，不是枚举值（留空时用 `.+` 命中所有非空 `problem`）；完整枚举清单看返回体里的 `available_problem_types`。
- **`lookup_name` 的 `name` 在 schema 里声明为 `string`**（实现虽容忍数组，但按 schema 校验的客户端会拒绝）：请传单个字符串、多次调用。
- **`get_project_metadata` / `read_*` 的 `filename` 只传裸文件名**（不带路径、不带 `.meta.json`/`.batch.json` 后缀）。`save_metadata` 的 `filename` 有穿越校验，但同样只应传裸文件名。
