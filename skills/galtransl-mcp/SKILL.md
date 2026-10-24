---
name: galtransl-mcp
description: 通过 galtransl MCP 工具检索 GalTransl 翻译项目的术语与译文（翻译缓存、原始脚本、字典与术语表、人名表、日志、元数据），并可写入路线图/元数据、按词条增改项目字典（术语表/译前/译后）、标记条目跳过检查、交换备选译文、直接改写缓存条目译文字段、启停翻译任务。含外接 agent 必须遵守的硬性约束与禁止查看清单（H 内容、凭据与端点、越界路径、写操作边界、规模化拉取）。当需要核查某个术语译法是否统一、某个角色名是否已收录、某段原文的现有译文、某条问题译文的位置，或想了解项目配置与流水线阶段、修改剧情路线图、维护字典词条、处理误报条目或备选译文、启动/停止翻译时使用。使用本 MCP 前必须先读「硬性约束与禁止查看」一节。
---

# GalTransl 翻译项目工具（MCP）

本技能通过 MCP 服务 `galtransl` 访问本机 GalTransl 项目。22 个工具中 14 个为**只读**检索，
8 个为**受限写入**（路线图、元数据、项目字典、缓存条目标记/交换/译文字段改写、启停翻译）。所有工具的 `project_dir`
可传**项目根目录的绝对路径**（例如 `D:\解包或汉化用\xp3专用汉化文件夹\gal翻译\test-dev`），
或**项目名称**——名称解析来源有二：GalTransl 已登记的项目（在界面中打开过/创建过/提交过翻译任务，
由后端记录在程序目录 `known_projects.json`，同名时取最近使用的那个）与工作区根目录下含配置文件的项目目录。
用 `galtransl_list_projects` 可一次列出全部可用名称与绝对路径。

只读检索工具不需要 GalTransl 后端在运行——它们直接读取项目目录下的文件。
作业域的 4 个工具（`get_job_status` / `check_model` / `submit_job` / `stop_job`）**需要后端在运行**
（作业状态只存在于后端进程内，MCP 是独立进程）。

## 硬性约束与禁止查看（先读本节，再调工具）

本节是外接 agent 的**行为契约**。14 个只读工具不因「只读」就可以随便读——下列内容是**禁止查看**的；
8 个写工具也各有明确边界，不得视为通用写权限。

### 1. 禁止查看：H / 成人向内容（最高优先级）

**事实依据（0.6.0 实测）**：MCP **读路径仍然没有 H 门禁过滤**——只读工具会原样返回 H 原文/译文。
但**部分写工具有硬门禁**：`galtransl_write_route_map` / `galtransl_save_metadata` / `galtransl_write_glossary` /
`galtransl_update_translation_entry` 在写入前会检查目标缓存文件是否落在 H 区间、待写入文本是否命中项目 H 词库
（`forbiddenDictH`），命中即**拒绝写入并返回错误**。
即：**读要自己守规矩，而写的兜底只覆盖这 4 个内容写入工具**。（`mark_skip_check` / `swap_alt_translation`
只改标志位或对调既有译文、`submit_job` / `stop_job` 无内容写入，均无门禁检查——H 区间条目的标志位与既有译文
仍可被改动；但 §1 对 H 内容的读取纪律同样适用。）

> **门禁的失效边界（务必知道）**：门禁是 **fail-open** 的——判定不了就放行，宁可漏拦也不阻断正常写入。
> 因此当项目**未配置 H 词库**（`forbiddenDictH` / `hCheckDict` 均无）或**尚无批次元数据**时，
> 文件维度与文本维度都判为「非 H」，上述 4 个工具**实际不会拦截**。
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
- 门禁**不覆盖**缓存条目的标记/交换工具（`galtransl_mark_skip_check` / `galtransl_swap_alt_translation`）：它们只改标志位或对调**既有**译文，不引入新文本，**H 区间条目同样可操作**（`swap_alt_translation` 对调 H 译文属允许操作，别把它当门禁失效去报 bug）。但 `galtransl_update_translation_entry` **会引入新文本**，因此**受门禁约束**，H 区间条目会被拒绝。**这不放宽读取纪律**——不得为了判断该不该标记 / 交换 / 改写而逐条翻阅 H 译文，需要判断时请用户决定。


### 2. 禁止查看：凭据、密钥与端点

| 目标 | 位置 |
|---|---|
| `backend_profiles.yaml` | GalTransl **程序目录**（含 API 密钥，已写入 `.gitignore` 并停止跟踪） |
| `app_settings.json` | 程序目录（应用级设置，非项目文件，不属授权范围） |
| `GALTRANSL_API_TOKEN` | 进程环境变量（后端写端点 Bearer 鉴权） |
| `api_calls.log` | 项目目录（API 调用明细，不在 MCP 工具范围内） |
| API 域名 / 端点 | `GalTransl.log` 可能含 `API URL: <domain>/chat/completions`（检测接口，info 级）与 `Call <domain> ...`（每次调用，debug 级） |

- `project_dir` **禁止**指向 GalTransl 程序目录、仓库根目录、系统目录、盘符根或他人目录；它必须是用户明确指定的**翻译项目目录**（绝对路径或该项目的名称）。
- **写工具的路径白名单（0.6.0）**：8 个写工具会硬校验 `project_dir` 是「可识别的 GalTransl 项目」——即目录下**确实存在** `config.inc.yaml` 或 `config.yaml`，否则拒绝执行。所以把 `project_dir` 指向仓库根、系统目录或空目录时，写工具会直接报错，不会误写外部文件。
- **只读工具不硬拦**：它们只要求目录存在，非法项目时在返回体里给出 `project_dir_valid: false` + `project_dir_hint`（仅告警，不阻断检索）。看到该字段请先与用户确认路径是否正确，不要据此继续扩大检索范围。
- 路径防护的口径要说清：只有 `read_translation_file` / `read_source_script` 经 `safe_under_project()` 校验（拒绝绝对路径与 `../`）；写工具（`save_metadata`）会对 `filename` 做穿越校验，并对 `project_dir` 做上述白名单校验。
- `galtransl_search_logs` 在 `source: "frontend"` 且项目内无 `frontend.log` 时，会**回退读工作区根目录**的 `frontend.log`。禁止借该回退去读项目外的日志。
- 密钥在日志中是脱敏的（`maskToken()` → `sk-abc...wxyz`），但仍**不得**索取、推断、复述或外传任何密钥、令牌、API 端点；日志里命中疑似凭据的行一律不引用原文。

### 3. 禁止查看：非授权项目与他人数据

- `galtransl_list_projects` 会列出**所有**可用项目（已登记 + 工作区根下）。**禁止**把它当作「可自由浏览的清单」——只能用于定位用户当前指定的那一个项目。
- 禁止跨项目聚合、比对或导出内容；禁止遍历用户未提及的项目。

### 4. 写操作边界（共 8 个写工具）

写能力**仅限**下列 8 个工具，每个都只能改项目内的指定产物。
其中 `write_route_map` / `save_metadata` / `write_glossary` / `update_translation_entry` 另受 **H 硬门禁**（见 §1）；
`mark_skip_check` / `swap_alt_translation` 只改标志位或对调既有文本、不引入新文本，**不在门禁范围内**。

| 写工具 | 能做什么 | 边界 |
|---|---|---|
| `galtransl_write_route_map` | 整体覆盖写剧情路线图 | 只能写 `transl_cache/pass0_cache/PlotRouteMap.json`；mermaid 经生成侧同口径校验 |
| `galtransl_save_metadata` | 原子写单文件元数据 | 只能写 `pass1_cache/*.meta.json`、`pass2_cache/*.batch.json`（kind=filemeta/batchmeta）；剧情路线图须走 `write_route_map`（mermaid 校验），全局分析由流水线生成不开放写入；`filename` 经穿越校验 |
| `galtransl_write_glossary` | 按词条增改 / 删除项目字典（`category` = gpt 术语表 / pre 译前 / post 译后） | `file` 必填且只能是 config 对应 dictionary 清单中的项目文件（未登记时会自动登记）；`mode` = upsert / delete，按 `src` 匹配（pre/post 只动普通词条行，条件/场景行不动）；只处理列出的词条，**不做整体覆盖**；保留原注释与其它词条；`note` 省略表示更新时保留原备注 |
| `galtransl_mark_skip_check` | 标记 / 取消某条缓存条目的「跳过问题检查」 | 只改 `skip_check` 标志位并按保存同口径重检该文件问题标注（标记会清除该条 problem）；不写任何译文文本；翻译任务运行中会被拒绝 |
| `galtransl_swap_alt_translation` | 交换某条缓存条目的备选译文与当前译文 | 只在 `alt_dst` 与 `pre_dst` 之间对调既有文本（不引入新文本），交换后重检；仅当条目有 `alt_dst` 时可用；翻译任务运行中会被拒绝 |
| `galtransl_update_translation_entry` | 直接改写某条缓存条目的译文字段 | 仅可写 `pre_dst` / `proofread_dst` / `alt_dst`（校对页同口径）；改后整文件重检；**受 H 硬门禁约束**；翻译任务运行中/刚结束时拒绝 |
| `galtransl_submit_job` | 提交翻译任务（**会真实消耗 API 额度**） | 仅在用户明确要求时调用；调用前确认项目与引擎 |
| `galtransl_stop_job` | 停止项目当前任务 | 无运行中任务时后端返回 409 |

**明确的禁止项**：

- 这些工具**不提供**任意路径读写、**不提供**命令执行、**不修改**程序配置与 API 密钥。
- 写工具只接受**可识别的 GalTransl 项目目录**（含 `config.inc.yaml`/`config.yaml`）。指向别处会被拒绝，**不要**为了绕过而去找一个含配置文件的目录顶替——`project_dir` 必须是用户指定的那一个。
- 禁止绕过 MCP 去达成其它写效果：不得直接调 GalTransl HTTP 写端点（`PUT /api/projects/:id/config`、`/cache/save`、`/cache/replace`、`/cache/delete-*` 等），不得直接编辑项目目录下的文件。上面 8 个工具本身走后端是**实现细节**，不代表你也获得了直接打这些端点的授权。
- 用户要求「帮我改一下译文」时，用 `galtransl_update_translation_entry` 逐条改写 `pre_dst` / `proofread_dst` / `alt_dst`（先读现状确认 index 与原文，只改用户指定的条目）；配置/公共字典仍无写能力，请用户在 GalTransl 界面操作（项目**字典**例外：用 `write_glossary` 按词条增改）。**不要**用任何间接手段代劳；`mark_skip_check` / `swap_alt_translation` 仍只能改标志位或对调既有文本，不能拿来「改写」译文。
- `submit_job` 是有副作用且耗时的操作：**不要**在用户只是「看看进度」「评估一下」时调用。

### 5. 禁止：规模化拉取（上下文纪律）

- 搜索类 `max_results` 默认 200、硬顶 2000；分页 `limit` 默认 100、硬顶 1000。
- 禁止全量拉取（整文件、整项目、全部缓存）；禁止用空查询或 `regex: "."` 之类宽正则做枚举式扫描。
- `field: all_fields` 检索条目全部内部字段，命中面比可见字段检索更广，更应收窄检索词再逐步放宽。
- 交付给用户的应当是**结论 + 定位**（文件名 + index + 最短必要引文），不是原文/译文堆砌。

### 6. 前端绿灯 ≠ 授权

顶部 MCP 指示灯变绿只表示「当前有 MCP 进程连着」（心跳文件 mtime 90 秒内新鲜），**不代表**用户授权了任何检索范围。授权范围由用户显式给出的 `project_dir` 与指令决定。

### 越权请求的标准回绝口径

用户要求触碰上述任一禁区时，按此回应——不要沉默执行，也不要自行扩大解释：

> 该内容不在 galtransl MCP 的授权范围内（<禁区名>）。我没有任意路径读写或执行命令的能力，也不会绕过去做。可以在 GalTransl 界面本地处理，或由你明确授权具体范围后我再继续。

## 工具清单（22 个）

### 搜索（只读）

| 工具 | 用途 | 关键参数 |
|---|---|---|
| `galtransl_search_cache` | 检索**已入库译文**（原文/译文/问题/说话人），可带前后文窗口；`field: all_fields` 可检索条目全部内部字段 | `query`、`field`(all/src/dst/problem/all_fields)、`regex`、`context`(命中前后各 N 条，≤20) |
| `galtransl_search_scripts` | 检索**原始脚本**（`gt_input`），未跑过翻译也能用，可带前后文窗口 | `query`、`regex`、`context`(命中前后各 N 条，≤20) |
| `galtransl_search_dict` | 检索**字典词条**（项目字典 + 公共 `Dict/`） | `query`、`direction`(any/jp2zh/zh2jp)、`include_common` |
| `galtransl_lookup_name` | 查**角色名译名表**（`name替换表.csv/xlsx`） | `name`（传字符串，见注意事项） |
| `galtransl_search_logs` | 按关键词检索**日志** | `keyword`、`source`(engine/frontend)、`tail` |
| `galtransl_list_problems` | 列出**带问题标记**的缓存条目 | `problem_type`（留空则全部） |

### 只读

| 工具 | 用途 | 关键参数 |
|---|---|---|
| `galtransl_list_projects` | 列出可用项目（已登记 + 工作区根下，拿到 `project_dir`） | `workspace_root`（可选） |
| `galtransl_get_project_overview` | 项目概览：目标语言、每请求条数、注入块开关、文件清单、流水线阶段 | — |
| `galtransl_read_translation_file` | 读取某个缓存文件的条目（分页；`offset` 负数=从末尾倒数） | `filename`、`offset`、`limit` |
| `galtransl_read_source_script` | 读取某个原始脚本文件的条目（分页；`offset` 负数=从末尾倒数） | `filename`、`offset`、`limit` |
| `galtransl_read_glossary` | 浏览项目字典词条（`category`=gpt 术语表/pre 译前/post 译后）：整表分页 / 按词筛选（原文/译名/解释任一列）；pre/post 下条件/场景行也返回（带 row_type） | `category`、`filename`（可选，限定单个字典文件）、`query`、`regex`、`offset`（负数=倒数）、`limit` |
| `galtransl_get_project_metadata` | 读元数据：`globalprompt` / `plotroute` / `filemeta` / `batchmeta` / `routeanalysis` / `all` | `kind`、`filename` |
| `galtransl_get_job_status` | 查**任务状态与实时进度**（阶段/百分比/worker/速度；**需后端**） | — |
| `galtransl_check_model` | 校验模型/令牌可用性（**发一次真实探测请求；需后端**） | `translator`、`config_file_name`、`backend_profile` |

### 写入（均受路径白名单约束；其中路线图/元数据/项目字典/缓存条目译文字段另受 H 门禁）

| 工具 | 用途 | 关键参数 |
|---|---|---|
| `galtransl_write_route_map` | 整体覆盖写剧情路线图（未提供字段保留旧值） | `mermaid`、`文件归属`、`节点剧情`、`结构类型`、`用户大纲` |
| `galtransl_save_metadata` | 原子写元数据 | `kind`(filemeta/batchmeta)、`filename`、`entry` |
| `galtransl_write_glossary` | 按词条增改 / 删除项目字典（**不做整体覆盖**） | `category`(gpt/pre/post)、`file`（字典文件名，必填）、`mode`(upsert/delete)、`entries`(`src`/`dst`/`note`) |
| `galtransl_mark_skip_check` | 标记 / 取消缓存条目的「跳过问题检查」 | `filename`（缓存相对路径）、`index`、`skip`(默认 true) |
| `galtransl_swap_alt_translation` | 交换缓存条目的备选译文与当前译文 | `filename`（缓存相对路径）、`index` |
| `galtransl_update_translation_entry` | 直接改写缓存条目的译文字段（校对页同口径，改后重检） | `filename`（缓存相对路径）、`index`、`fields`（`pre_dst`/`proofread_dst`/`alt_dst`，值为字符串） |
| `galtransl_submit_job` | 提交翻译任务（**需后端运行；消耗额度**） | `translator`（必填）、`config_file_name`、`backend_profile`、`file_filter` |
| `galtransl_stop_job` | 停止项目当前任务（**需后端运行**） | — |

## 缓存结构与项目阶段判定

判断项目当前进展用证据说话，不要靠猜：

1. 有翻译任务在跑时，`galtransl_get_job_status` 的 `stage`（阶段名）/ `stage_index`（0-8）最准。
2. 否则看 `galtransl_get_project_overview` 的 `cache_file_names`（相对 `transl_cache/`，带目录前缀），
   再抽查 `galtransl_read_translation_file` 的条目字段形态。

`transl_cache/` 目录语义与阶段对应（阶段名与 `stage_index` 均按 `get_job_status` 口径）：

| 目录 | 内容 | 阶段（stage_index） |
|---|---|---|
| `pass1_cache/*.meta.json` | 文件级元数据 | 3 生成文件级元数据 |
| `pass0_cache/PlotRouteMap.json` | 剧情路线图 | 4 生成剧情路线图 |
| `pass0_cache/GlobalPrompt.json`、`pass0_cache/route_analysis/*.json` | 全局分析与路线分析分片 | 5 生成全局游戏分析 |
| `pass2_cache/*.batch.json` | 批次区间与用词色彩标注 | 6 划分翻译区间 |
| `pass3_cache/*.json`（顶层旧布局 `*.json` 同义） | 翻译缓存 | 7 翻译执行中 |
| `gendic_cache/`、`aftertrans_cache/`、`*.append.jsonl` | 术语表分片 / 后处理断点标记 / 增量合并日志，**不是翻译缓存** | — |

翻译条目字段指纹：`pre_dst`+`trans_by` = 已翻译；`trans_by` 带 `(Failed)` = 翻译失败句；
`problem` = 问题标注（阶段7 每个文件翻译完成时即写入，阶段8 与写工具重检会刷新，单凭它不能判定阶段8已跑）；
`alt_dst` / `suspected_error` / `tone_issue` / `verbose_issue` = 阶段8（AI初步处理）校润产物；`proofread_dst` = 已校对。
文件的**非空行数**低于 `metaMinLines`（默认 20）时不生成元数据，`pass1`/`pass2` 缺它属正常，不是丢失。

分阶段策略：

- **元数据阶段**（pass0-2 产物不全、pass3 尚无译文）：核对/补写文件元数据与批次区间（`save_metadata`）、
  维护剧情路线图（`write_route_map`）、整理字典（`write_glossary`）；此时没有译文可改，不要碰译文改写工具。
- **翻译阶段**：先 `get_job_status` 确认是否运行中；`mark_skip_check` / `swap_alt_translation` /
  `update_translation_entry` 在任务运行中/刚结束（约 30 秒）会被拒绝（见注意事项末两条），缓存条目改动等任务结束；
  字典/元数据写入虽不被拒绝，也建议等任务结束再改。
- **校润阶段**（`alt_dst` / `suspected_error` 等阶段8标记已出现）：`list_problems` 定位 →
  `update_translation_entry` 逐条改写或 `swap_alt_translation` 换备选，检测误报用 `mark_skip_check` 跳过；
  `suspected_error` 只是「疑似」标记，改前先读上下文确认；写后按校对页同口径自动重检，无需手动重跑检测。

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
3. 看上下文再给修改建议：优先用 `galtransl_search_cache` 传 `context`（如 2~5）直接带出命中前后的台词窗口；或 `galtransl_read_translation_file` 用 `offset`/`limit` 翻到对应条目附近（`offset` 支持负数从文件末尾倒数）。

### 4. 只想知道原文，不关心已有译文

`galtransl_search_scripts` 直接搜 `gt_input` 原始脚本，不依赖缓存是否生成。命中结果里的 `index` 与缓存条目的 `index` 同源，可直接对照；需要前后文时传 `context` 带出相邻原文窗口。

### 5. 修改剧情路线图（写）

1. `galtransl_get_project_metadata`（`kind: "plotroute"`）或 `galtransl_read_translation_file` — **先读现状**。
2. `galtransl_write_route_map` — **整体覆盖**写入：未被要求修改的字段（`结构类型`/`用户大纲`/`文件归属`/`节点剧情`）必须原样带回，禁止凭空删减路线或文件。
3. 若返回 H 门禁错误 → 如实转告用户，不要改写措辞重试。

### 6. 维护项目字典（写，术语表/译前/译后）

1. `galtransl_read_glossary`（`category` 按要维护的类别传 gpt/pre/post）— **先读现状**：拿到该类别的文件清单与现有词条（结果里的 `file` + `line_no` 用于定位；pre/post 下 `row_type` 为 conditional/situation 的行是条件/场景行，只能读不能通过本工具改写）。
2. `galtransl_write_glossary` — `category` 与读取时一致，`file` 必须用读取结果里的文件名；`mode: "upsert"` 按 `src` 匹配增改（已存在则原地更新，不存在则追加）、`delete` 删除普通词条；每次只提交要求改动的词条，禁止用空 entries 或删光词条的方式「清空重写」。
3. 返回体 `also_found_in` 非空表示同名词条还存在于其它同类字典文件（H / 非 H 场景本就允许同词不同译）；有冲突疑义时先与用户确认，不要自作主张删改另一份。
4. 若返回 H 门禁错误 → 如实转告用户，不要改写措辞重试。

### 7. 处理误报条目、备选译文与改写译文（写，逐条）

1. `galtransl_list_problems` 或 `galtransl_read_translation_file` — 定位条目（文件名 + index），确认译文确实无误但检测持续误报时，用 `galtransl_mark_skip_check`（`skip: true`）标记跳过；误标了再用 `skip: false` 取消。标记会清除该条 problem，取消后按新状态重检。
2. 条目带 `alt_dst` 备选译文且用户想采用它时，用 `galtransl_swap_alt_translation` 交换（等价于校对页「交换备选」按钮）；交换后问题标注按新译文重检。
3. 用户明确要求润色/改写某句译文时，用 `galtransl_update_translation_entry` 写 `pre_dst`（主译文）或 `proofread_dst`（校对译文）；只提交 `fields` 中需要改的字段，未列出的字段保持不变。改写会引入新文本，**受 H 门禁约束**。
4. 三个工具都只能逐条处理，且翻译任务运行中/刚结束时会被拒绝；改写后按校对页保存同口径整文件重检，返回体的 `problem_before` / `problem_after` 可用于核对影响。

### 8. 启动 / 停止翻译（写，消耗额度）

1. 先与用户确认**项目**与**引擎**（`translator`，如 `ForGal-full-pipeline`）；`galtransl_get_project_overview` 可帮助确认项目结构。
2. 可选：`galtransl_check_model` 预检模型/令牌可用性（会发一次真实请求），避免提交后才发现配置失效。
3. `galtransl_submit_job` — 返回 `job_id`；可用 `file_filter` 限定文件子集。
4. `galtransl_get_job_status` — 观察当前阶段、进度百分比、worker 数与速度；需要中止时调 `galtransl_stop_job`（无运行中任务返回 409）。
5. 更细的实时画面（预览、TTFT 状态灯）请让用户在 GalTransl 界面查看；MCP 侧只提供进度摘要。

## 注意事项

- **只读 + 受限写入**：14 个只读工具 + 8 个写工具（边界见 §4）。项目字典可用 `write_glossary` 按词条增改；缓存条目可逐条标记跳过检查、交换备选译文或直接改写译文字段；公共字典/配置仍需在 GalTransl 界面操作。
- **H 内容**：读路径**无 H 门禁过滤**（须自行遵守 §1）；写工具中 `write_route_map` / `save_metadata` / `write_glossary` / `update_translation_entry` **有硬门禁**，命中 H 区间或 H 词库即拒绝写入；`mark_skip_check` / `swap_alt_translation` 只改标志位或对调既有译文，**不在门禁范围内**（H 区间条目同样可操作）。门禁 **fail-open**，项目未配 H 词库时不拦，详见 §1 的失效边界。
- **作业域工具需后端**：`get_job_status` / `check_model` / `submit_job` / `stop_job` 需要 GalTransl 程序在运行；后端未启动时会返回「无法连接 GalTransl 后端」的中文错误，此时转告用户启动程序即可，不要反复重试。`check_model` 会向模型端点发起一次真实探测请求（消耗极小额度），不要在无谓的场景反复调用。
- **结果上限**：搜索类工具默认最多返回 200 条（硬顶 2000），超出时返回体里 `truncated` 为 `true`，`total` 才是全部命中数。需要更多就加 `regex` 收窄检索词，或提高 `max_results`。
- **正则**：`regex: true` 时检索词按正则解释；非法正则会返回参数错误。
- **大文件**：`read_translation_file` / `read_source_script` 用 `offset`/`limit` 分页，默认每页 100 条（硬顶 1000），不要一次拉全文件。`offset` 传负数从文件末尾倒数（-1=最后一条，如取文件最后几句），返回体 `offset` 为换算后的正值。
- **上下文窗口（`search_cache` / `search_scripts` 的 `context`）**：命中按文件合并成连续区间返回（相邻命中的邻域不重复）；窗口条目仅含 序号(`index`)/说话人/生效译文（脚本为原文 `message`）与 `is_hit` 标记，`index` 与命中结果同口径（条目自身 index 字段，缺失时回退数组下标）。全部窗口条目共享 1000 条预算，放不下时 `context_truncated=true`；`total`/`truncated` 仍只统计命中。上下文条目不占 `max_results`，开大 `context` 时应调小 `max_results`。注意缓存条目的 `index` 字段与 `read_*` 的 `offset`（数组下标）是两套定位符，index 有偏移的项目里不要互相换算。
- **返回字段易误读**：`search_cache` 结果里的 `post_src` 实际装的是**页面可见原文**（`pre_src` 优先），`pre_dst` 装的是 `pre_dst`/`pre_zh`/`proofread_dst` 首个非空；这与 `read_translation_file` 返回的原始条目字段口径**不同**，不要混用。`search_dict` 结果的检索词/替换词键名是 `src`/`dst`，另有 `origin`（project/common）与 `category`（pre/gpth/gptnh/gpt/post/forbiddenh/forbiddennh）。
- **`all_fields` 全字段检索**（`search_cache` 的 `field: "all_fields"`）：递归匹配条目全部文本字段与布尔标记（布尔按 `true`/`false` 小写匹配，如定位跳过检查的条目用 query `"true"`）；数值字段（`index`）与 `__` 开头的内部键（如旧快照残留的 `__cache_key`）不参与匹配。命中条目**不返回 `match_*` 标志**，改为 `matched`（字段路径 + 命中片段，封顶 8 个，超出时 `matched_truncated: true`）；可与其他取值一样组合 `context` 上下文窗口。检索范围含内部字段，H 纪律同 §1。
- **`list_problems` 的 `problem_type` 是正则关键词**，不是枚举值（留空时用 `.+` 命中所有非空 `problem`）；完整枚举清单看返回体里的 `available_problem_types`。
- **`lookup_name` 的 `name` 在 schema 里声明为 `string`**（实现虽容忍数组，但按 schema 校验的客户端会拒绝）：请传单个字符串、多次调用。
- **`get_project_metadata` / `read_*` 的 `filename` 只传裸文件名**（不带路径、不带 `.meta.json`/`.batch.json` 后缀；`read_glossary` 的 `filename` 传字典文件名，如 `项目GPT字典.txt`）。`save_metadata` 的 `filename` 有穿越校验，但同样只应传裸文件名。
- **缓存条目写工具（`mark_skip_check` / `swap_alt_translation` / `update_translation_entry`）**：`filename` 传相对 `transl_cache` 的缓存路径，**仅翻译缓存可用**（`pass3_cache/*.json` 与顶层旧布局 `*.json`；pass1/pass2/pass0 元数据文件会被拒绝），以 `get_project_overview` 的 `cache_file_names` 为参考但注意其中元数据文件不可用；每次修改前会先快照旧文件、修改后按校对页保存同口径重检整份文件的问题标注；返回体 `changed=false` 表示没有实际变化（含条目已处于目标状态的 no-op，此时不做重检）。翻译任务运行中会直接报错；**任务刚结束约 30 秒内也会报错**（后端正在合并增量缓存与重检），稍候重试即可；后端不可达时放行但返回体 `job_check` 会注明，此时请先与用户确认没有翻译在跑。重检会在本进程内装载项目文本插件（与校对页同口径），行为异常的第三方插件可能影响该工具。
- **`update_translation_entry` 的字段语义**：只接受 `pre_dst` / `proofread_dst` / `alt_dst`，值必须是字符串；新值按真实换行（`\n`）归一。`proofread_dst` / `alt_dst` 传空串表示清除该字段，`pre_dst` 传空串表示置为未翻译；未列出的字段保持不变。写 `pre_dst` / `proofread_dst` 时会一并清掉旧键 `pre_zh` / `proofread_zh`（否则读侧会回退旧值）。
