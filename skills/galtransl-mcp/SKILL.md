---
name: galtransl-mcp
description: 通过 galtransl MCP 工具检索 GalTransl 翻译项目的术语与译文（翻译缓存、原始脚本、字典、人名表、日志、元数据）。当需要核查某个术语译法是否统一、某个角色名是否已收录、某段原文的现有译文、某条问题译文的位置，或想了解项目配置与流水线阶段时使用。
---

# GalTransl 术语与译文检索

本技能通过 MCP 服务 `galtransl` 访问本机 GalTransl 项目（**只读**）。所有工具都需要**项目根目录的绝对路径**（`project_dir`），例如
`D:\解包或汉化用\xp3专用汉化文件夹\gal翻译\test-dev`。

服务不需要 GalTransl 后端在运行——它直接读取项目目录下的文件。

## 工具清单（11 个）

### 搜索

| 工具 | 用途 | 关键参数 |
|---|---|---|
| `galtransl_search_cache` | 检索**已入库译文**（原文/译文/问题/说话人） | `query`、`field`(all/src/dst/problem)、`regex` |
| `galtransl_search_scripts` | 检索**原始脚本**（`gt_input`），未跑过翻译也能用 | `query`、`regex` |
| `galtransl_search_dict` | 检索**字典词条**（项目字典 + 公共 `Dict/`） | `query`、`direction`(any/jp2zh/zh2jp)、`include_common` |
| `galtransl_lookup_name` | 查**角色名译名表**（`name替换表.csv/xlsx`） | `name`（字符串或数组） |
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
- **H 内容**：当前版本（0.5.1）的读取路径**未做 H 门禁过滤**，且 H 门禁的完整实现排在 0.6.1。检索结果可能包含成人向内容，请勿在无授权场景下展开。
- **结果上限**：搜索类工具默认最多返回 200 条（硬顶 2000），超出时返回体里 `truncated` 为 `true`，`total` 才是全部命中数。需要更多就加 `regex` 收窄检索词，或提高 `max_results`。
- **正则**：`regex: true` 时检索词按正则解释；非法正则会返回参数错误。
- **大文件**：`read_translation_file` / `read_source_script` 用 `offset`/`limit` 分页，默认每页 100 条（硬顶 1000），不要一次拉全文件。
