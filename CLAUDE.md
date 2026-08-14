# CLAUDE.md
## 项目概述

GalTransl 是一个 Galgame（视觉小说）自动翻译工具，利用大语言模型（GPT-4、Claude、Deepseek、Sakura 等 OpenAI 兼容后端）和提示词工程进行翻译。版本 0.3.0。通过多阶段流水线处理 name-message JSON 对，将日文原文翻译为中文（或其他目标语言）脚本，并支持字典、插件、校对审核、人名表、剧情路线图、译文质量改进等增强能力。

## 整体架构

项目采用「Python 后端 + Tauri 桌面壳」的 C/S 架构，四个层次自下而上：

```
┌─────────────────────────────────────────────────────────────┐
│  桌面壳 desktop/（Tauri 2 + Rust + SolidJS 前端）              │
│  · Rust：进程生命周期管理、ensure_backend_ready 拉起后端        │
│  · SolidJS：页面 UI，通过 HTTP 轮询与后端交互                  │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP REST（默认 http://127.0.0.1:12333）
┌──────────────────────────▼──────────────────────────────────┐
│  HTTP 服务层 GalTransl/server.py                             │
│  · ThreadingHTTPServer + 手写路由（非 FastAPI）               │
│  · 项目管理 / 任务调度 / 运行时状态 / 缓存读写 / 字典 / 人名表   │
│  · server_runtime.py：进程内运行时状态（进度、提示、错误）      │
└──────────────────────────┬──────────────────────────────────┘
                           │ run_job / JobSpec
┌──────────────────────────▼──────────────────────────────────┐
│  任务调度层 Service.py → Runner.py                           │
│  · JobSpec/JobState 数据类，run_job_async 编排任务生命周期      │
│  · 插件加载、代理池/令牌池/分割器初始化、日志 handler 挂载       │
└──────────────────────────┬──────────────────────────────────┘
                           │ doLLMTranslate()
┌──────────────────────────▼──────────────────────────────────┐
│  翻译流水线 GalTransl/Frontend/LLMTranslate.py               │
│  · 读文件 → 切 chunk → worker 协程池 → 逐 chunk 翻译 → 合并输出  │
│  └─ Backend/BaseTranslate.py（LLM 后端基类，OpenAI 兼容）      │
│      ├─ ForGalJsonMulitChat  ← 主翻译后端（多轮对话）          │
│      ├─ ForGal-full-pipeline  ← 完整流水线编排                 │
│      ├─ ForGlobalPrompt / ForFileMetaData / ForBatchMetaData  │
│      ├─ ForPlotRouteMap（剧情路线图）/ GenDic（GPT 字典生成）   │
│      └─ ForImproveTranslation / ForBRStation（译文质量改进）   │
└─────────────────────────────────────────────────────────────┘
```

- **后端**：Python 3.12+，`GalTransl/` 包，支持 CLI（`python -m GalTransl`）与服务端（`run_backend.py` → `server.py`）两种启动方式。
- **前端**：SolidJS（非 React），位于 `desktop/`，通过 Tauri 壳作为桌面应用运行，也可纯浏览器开发模式运行。
- **通信**：前端经 HTTP REST 与后端通信；后端以 `asyncio` 协程驱动翻译，任务通过 `threading.Event` 停止事件实现可取消。

## 主要模块及其职责

### 后端核心（GalTransl/）

| 文件 | 职责 |
|---|---|
| `__init__.py` | 包入口：`LOGGER` 日志器、版本号（`0.3.0`）、目录常量、`TRANSLATOR_SUPPORTED` 翻译器清单、`LANG_SUPPORTED` 语言表、启动更新检查线程 |
| `__main__.py` | CLI 入口：`argparse` 解析 `--project_dir/--translator/--debug-level/--language` 并调用 `Service.run_job()` |
| `server.py` | **HTTP REST 服务器**（`ThreadingHTTPServer`，默认 127.0.0.1:12333）。项目管理、任务调度、运行时状态、缓存/字典/人名表读写等全部端点；内含单页 Web UI（`INDEX_HTML`） |
| `server_runtime.py` | 运行时状态追踪：`RuntimeState`/`RuntimeRegistry`（服务端模式下每项目状态）、`RuntimeProgressCache`（缓存文件进度解析，retranslKey 感知）、8 阶段流水线定义与阶段映射 |
| `Service.py` | 任务调度服务层：`JobSpec`/`JobState` 数据类、`run_job_async()`/`run_job()`、取消（`JobCancelledError`）、错误日志（`error.log`，1MB 上限截断）、backend profile / prompt 覆盖注入 |
| `Runner.py` | 翻译运行编排器：`run_galtransl()` 挂载 job 日志 handler（线程过滤防并发重复）、插件加载（yapsy）、代理池/令牌池/分割器初始化、`start_time` 定时启动 |
| `Frontend/LLMTranslate.py` | **主翻译编排器**：`doLLMTranslate()` 读文件 → 切 chunk → worker 协程池（队列+哨兵）→ 单 chunk 翻译 → 后处理合并输出；`doLLMTranslSingleChunk()`；完整流水线 `_run_full_pipeline()`；自适应并发（`autoAdjustWorkers`） |
| `Backend/BaseTranslate.py` | **所有 LLM 后端基类**：OpenAI 兼容客户端构建（多 token）、`ask_chatbot()`（流式/重试/429 退避/RPM 限制/API 调用日志）、`batch_translate()`、`_batch_translate_common()`、响应解析与失败兜底、动态句数调节 |
| `Backend/ForGalJsonMulitChat.py` | **主翻译后端**：多轮对话、注入 FileMetaData/BatchMetadata/GlobalPrompt、恢复上下文（`restore_context`）、H 词过滤 |
| `Backend/ForGlobalPrompt.py` | 全局游戏分析：读压缩全文生成角色/剧情/风格，写 `pass0_cache/GlobalPrompt.json` |
| `Backend/ForFileMetaData.py` | 文件级元数据：逐文件生成角色/服装/剧情/标签，写 `pass1_cache/*.meta.json` |
| `Backend/ForBatchMetaData.py` | 批次级元数据：划分翻译区间并标注视角/氛围/H/用词色彩，写 `pass2_cache/*.batch.json` |
| `Backend/ForPlotRouteMap.py` | 剧情路线图：基于各文件剧情+用户大纲生成剧情路线，写 `pass0_cache/PlotRouteMap.json` |
| `Backend/GenDic.py` | GPT 字典自动化构建：分词+集合覆盖采样，生成「项目GPT字典-生成.txt」 |
| `Backend/ForImproveTranslation.py` | 整文件翻译后评估译文，对可改进句生成备选译文（`alt_dst`），校对页一键交换 |
| `Backend/ForBRStation.py` | 仅对「换行位置异常」句子生成备选译文（`alt_dst`），校对页一键交换 |
| `Backend/Prompts.py` | 所有提示词模板常量（翻译/校对/GenDic/FileMeta/BatchMeta/Global）与 H 词敏感词表 |
| `COpenAI.py` | `COpenAIToken`（API 密钥+端点）、`COpenAITokenPool`（带延迟追踪的负载均衡池）、Sakura 端点队列 |
| `ConfigHelper.py` | `CProjectConfig`（项目配置封装）、`CProxyPool` 代理池、`initDictList`/`loadConfigFile`、httpx 版本兼容的代理参数 |
| `CSentense.py` | **核心数据模型**：`CSentense`（单句）、`CTransList`（列表别名），含 `pre_zh` 别名等 |
| `CSplitter.py` | 输入分割/输出合并：`DictionaryCountSplitter`/`EqualPartsSplitter`/`InputSplitter`/`OutputCombiner`/`DictionaryCombiner` |
| `Loader.py` | `load_transList()` 将 JSON（文件/字符串/列表）载入 `CTransList` 并链接前后句上下文 |
| `CSerialize.py` | 序列化输出：`update_json_with_transList`/`save_transList_to_json_cn` |
| `Cache.py` | 翻译缓存：`save_transCache_to_json`/`get_transCache_from_json`、`.append.jsonl` 增量合并（`compact_cache_append_logs`）、原子写入 |
| `Dictionary.py` | 字典系统：`CNormalDic`（译前/译后替换，条件规则）、`CGptDict`（GPT 字典）、`CBasicDicElement` 等 |
| `Name.py` | 人名替换表（CSV/XLSX）提取、加载、导出；`extract_names_from_dir`/`load_name_table`/`dump_name_table_from_chunks` |
| `Problem.py` | 质量检查：`find_problems()` 逐句检测并写入 `tran.problem` 属性 |
| `GTPlugin.py` | 插件基类：`GTextPlugin`（before/after_src_processed、before/after_dst_processed）、`GFilePlugin`（load/save） |
| `TextCompressor.py` | 文本无损压缩器，压缩全文供全局分析并校验完整性（`verify_compression`） |
| `DataValidator.py` | 统一数据校验：输入/压缩/LLM 响应/元数据/输出五层校验（`validate_input_json`/`validate_global_prompt` 等） |
| `TerminalOutput.py` | 终端进度条封装：`terminal_progress`、`NullProgressBar`、`should_print_translation_logs` |
| `ApiLogger.py` | API 调用结构化日志（`api_calls.log`），异步 writer，TraceID 串联请求/响应 |
| `AppSettings.py` | 应用级设置（`app_settings.json`）：`printTranslationLogInTerminal`/`maxConcurrentJobs` |
| `DefaultProjectConfig.py` | 新项目的默认 `config.yaml` 模板内容（字符串常量） |
| `backend_security.py` | 后端 HTTP 安全策略：CORS 源白名单、`GALTRANSL_API_TOKEN` Bearer 鉴权（写端点可选）、路径穿越防护纯函数 |
| `i18n.py` | 多语言文本表（zh-cn/en）：`get_text()`/`GT_LANG` |
| `Utils.py` | 通用工具：读翻译规范文件、正则提取、标点/语言检测、换行处理 |
| `yapsy/` | 第三方插件框架（bundle 在此），`PluginManager` 定位/加载/激活插件 |

### 桌面端（desktop/）

| 目录 | 职责 |
|---|---|
| `src/pages/` | 页面视图：home（首页）、translate（翻译控制台）、review（校对审核）、settings（设置）、project-config（项目配置）、wizard（新建项目向导 5 步）、dictionary（字典）、logs（日志）、backends（后端配置）、plugins（插件）、prompts（提示词模板） |
| `src/components/` | 布局组件：TitleBar/ActivityBar/SidebarPanel/MainArea/StatusBar，及 toast/confirm 弹窗宿主、icons |
| `src/lib/api/` | HTTP API 层：`client.ts`（请求封装/后端 URL/token/项目 ID 编解码）、`project.ts`/`general.ts`（各 API 函数）、`types.ts`（TS 类型）、`preferences.ts`（localStorage 偏好） |
| `src/stores/` | SolidJS store 状态管理：`appStore`（全局状态+导航）、`undoStore`（校对撤销/重做）、`confirmStore`/`toastStore`/`logStore` |
| `src/lib/cacheWatcher.ts` | 缓存文件夹轮询监控（3s），驱动文件树/问题侧栏刷新 |
| `src-tauri/` | Rust 侧：后端生命周期管理 + Tauri command（`ensure_backend_ready` 等） |

## 核心流水线（ForGal-full-pipeline，8 阶段）

`translator: "ForGal-full-pipeline"` 自动串联以下阶段（`server_runtime.PIPELINE_STAGE_NAMES` 共 8 阶段，前端"流程完成情况"按此显示；`LLMTranslate._run_full_pipeline()` 内部按 0-6 编号执行并额外含"剧情路线图"子阶段）：

| 阶段 | 名称 | 说明 | 产物 |
|---|---|---|---|
| 0 | 输入数据校验 | `DataValidator.validate_input_json` 逐文件校验 | — |
| 1 | 文本无损压缩 | `TextCompressor` 压缩全文并校验完整性 | — |
| 2 | 生成全局游戏分析 | `ForGlobalPrompt` 生成角色/剧情/风格 | `transl_cache/pass0_cache/GlobalPrompt.json` |
| 3 | 构建术语表 | `GenDic` 生成 GPT 字典（已有非空字典可跳过） | 项目根「项目GPT字典-生成.txt」 |
| 4 | 生成文件级元数据 | `ForFileMetaData` 逐文件 | `transl_cache/pass1_cache/*.meta.json` |
| 4.5 | 剧情路线图 | `ForPlotRouteMap` 基于各文件剧情+大纲 | `transl_cache/pass0_cache/PlotRouteMap.json` |
| 5 | 划分翻译区间 | `ForBatchMetaData` 批次+视角/氛围/H 标注 | `transl_cache/pass2_cache/*.batch.json` |
| 6 | 翻译执行 | `ForGalJsonMulitChat` 多轮对话翻译 | `transl_cache/pass3_cache/*.json` → `gt_output/` |
| 7 | 译文质量改进 | `ForImproveTranslation`/`ForBRStation` 按 `common.gpt.afterTranslation`（improve/brfix/improve+brfix）在翻译后逐文件生成备选译文 | `transl_cache/*.json` 的 `alt_dst` 字段 |

各阶段均可按缓存产物是否已存在而跳过（`internals.pipeline.forceRegen*` 可强制重生成）。独立引擎（`ForGlobalPrompt`/`ForPlotRouteMap`/`GenDic`/`ForFileMetaData`/`ForBatchMetaData`/`ForImproveTranslation`/`ForBRStation`/`dump-name`/`show-plugs`）也可单独作为 `translator` 运行。

## 关键依赖项说明

### Python（requirements.txt）

| 依赖 | 用途 |
|---|---|
| `openai` | OpenAI 兼容 API 客户端（`AsyncOpenAI`），核心 LLM 调用 |
| `httpx-aiohttp` / `httpx` | 异步 HTTP 客户端（`ask_chatbot` 底层传输） |
| `pyreqwest` | 可选的高性能 HTTP transport（有则优先用于 AsyncOpenAI，无则回退） |
| `PyYAML` | 项目配置 `config.yaml` 解析 |
| `opencc` | 简繁转换（目标语言为简/繁体时校正译文） |
| `tenacity` | 重试装饰器（`ConfigHelper` 中异步重试） |
| `orjson` | 高速 JSON 读写（缓存、运行时进度解析） |
| `tiktoken` / `vaporetto` / `budoux` | 分词器（token 计数/日文分词） |
| `fasttext-predict` | 语言检测 |
| `colorlog` / `alive-progress` | 终端彩色日志与进度条 |
| `openpyxl` | 人名替换表 `.xlsx` 读写 |
| `InquirerPy` | CLI 交互选择 |
| `playsound3` / `beautifulsoup4` / `aiofiles` | 杂项工具（音频/HTML/异步文件） |
| `mcp` / `playwright` | 仅 MCP 调试脚本（`desktop/scripts/tauri_webview_mcp.py`）专用，非运行时必需 |
| `packaging` / `Requests` | 版本比较 / 兼容 |

### 前端（desktop/package.json）

| 依赖 | 用途 |
|---|---|
| `solid-js` ^1.9 | UI 框架（SolidJS，含 `solid-js/store` 状态管理） |
| `@tauri-apps/api` ^2.10 / `@tauri-apps/cli` ^2.10 | Tauri 2 桌面壳（`invoke`、`listen`） |
| `@tauri-apps/plugin-dialog` / `plugin-shell` | 系统对话框与 shell 操作 |
| `dompurify` | HTML 消毒（富文本渲染） |
| `mermaid` 11.16 | 剧情路线图渲染（`PlotRoutePanel`） |
| `vite` ^5 + `vite-plugin-solid` | 构建工具（dev server 绑定 127.0.0.1:1420） |
| `vitest` + `jsdom` + `@solidjs/testing-library` | 前端测试 |
| `eslint`（`eslint-plugin-solid`）+ `prettier` + `typescript` | 代码质量与格式 |

## 数据模型概览

### CSentense（GalTransl/CSentense.py）

单句翻译单元的核心字段（`CTransList` 为 `List[CSentense]` 别名）：

| 字段 | 说明 |
|---|---|
| `index` / `runtime_index` | 源文件中的行号 / 运行时全局序号 |
| `name` / `speaker` | 说话人姓名 / 展示角色名 |
| `pre_src` / `post_src` | 原文 / 预处理后原文 |
| `pre_dst` / `post_dst` | 原始译文 / 后处理后译文（`pre_zh` 是 `pre_dst` 的兼容别名） |
| `proofread_dst` / `proofread_zh` | 校对后译文 |
| `trans_by` / `proofread_by` | 翻译/校对所用模型名（失败时带 `(Failed)` 标记） |
| `problem` | 问题检测结果（`Problem.py` 写入） |
| `trans_conf` / `doub_content` / `unknown_proper_noun` | 置信度/存疑内容/未知专有名词 |
| `prev_tran` / `next_tran` | 前后句链接（上下文恢复用） |
| `pre_jp` / `post_jp` | 历史兼容别名（`pre_src`/`post_src`） |

### CProjectConfig（GalTransl/ConfigHelper.py）

| 字段 | 说明 |
|---|---|
| `projectConfig` | 解析后的 YAML 配置 dict |
| `projectDir` / `inputPath` / `outputPath` / `cachePath` | 项目根 / `gt_input` / `gt_output` / `transl_cache`（兼容旧 `json_jp`/`json_cn`） |
| `keyValues` | `common` 段拍平后的配置（`getKey()` 读取） |
| `select_translator` | 本次选择的翻译引擎 |
| `pre_dic` / `post_dic` / `gpt_dic` | 译前/译后/GPT 字典实例 |
| `tPlugins` / `fPlugins` | 文本插件/文件插件列表 |
| `tokenPool` / `proxyPool` / `endpointQueue` | 令牌池/代理池/Sakura 端点队列 |
| `input_splitter` / `output_combiner` | 分割器/合并器 |
| `bar` | 进度条对象 |
| `non_interactive` | 服务端模式标志（前端启动时为 True） |
| `runtime_project_dir` / `config_name` | 运行时项目路径 / 真实配置文件名 |
| `global_prompt` / `file_metadata` | 流水线产物注入（供后端读取） |
| `stop_event` | 停止事件（线程安全取消） |
| `runtime_workers_*` / `active_workers` | 并发 worker 运行时状态 |

### JobSpec / JobState（GalTransl/Service.py）

- **JobSpec**：`project_dir`、`translator`、`config_file_name`、`job_id`、`backend_profile`、`backend_profile_data`、`prompt_template_overrides`（按 translator 键控的 system/user prompt 覆盖）
- **JobState**：`job_id`、`status`（pending/running/completed/cancelled/failed）、`success`、`error`、`gendic_added_entries`/`gendic_duplicated_entries`、`created_at`/`started_at`/`finished_at`

### RuntimeState（GalTransl/server_runtime.py）

服务端模式下每项目一个，供前端 `/runtime` 轮询：`stage`/`stage_index`/`stage_total`（8 阶段）、`current_file`/`current_batch`/`batch_total`、`workers_active`/`workers_configured`、`translation_speed_lpm`（1 分钟窗口速度）、`latest_prompt_preview`/`latest_assembled_preview`（实时提示词/译文预览）、`recent_errors`/`recent_successes`（环形队列）、`notices`（一次性用户提示，前端 toast 后清除）。

### 缓存条目（CacheEntry）

缓存文件（`transl_cache/pass3_cache/*.json`）中每条 JSON 的核心字段：`index`、`name`、`pre_src`、`post_src`、`pre_dst`、`proofread_dst`、`trans_by`、`proofread_by`、`problem`、`post_dst_preview`，以及内部键 `__cache_key`（前句+当前句+后句上下文三元组，带 index 前缀区分同文不同位置）。译文质量改进阶段（improve/brfix）会在条目上追加 `alt_dst`（备选译文，与主译文不同才落盘），校对页可一键交换。

## API 接口文档

### 基础约定

- **基址**：`http://127.0.0.1:12333`（`run_backend.py` 可 `--port` 指定；Tauri 托管态由 `ensure_backend_ready` 动态覆盖）
- **实现**：`GalTransl/server.py` 基于标准库 `http.server`（`ThreadingHTTPServer` + 手写路由），非 FastAPI
- **项目 ID**：`encodeProjectDir(绝对路径)` → urlsafe Base64，作为 URL 路径段（`/api/projects/:id/...`）
- **请求体**：JSON（`Content-Type: application/json`）
- **鉴权**：默认不启用；设置环境变量 `GALTRANSL_API_TOKEN` 后，写端点（POST/PUT/DELETE）需 `Authorization: Bearer <token>`（`backend_security.token_ok`，hmac 时序安全比较）
- **CORS**：源白名单见 `backend_security.load_allowed_origins()`（tauri://localhost、127.0.0.1:1420、127.0.0.1:12333 等；可用 `GALTRANSL_ALLOWED_ORIGINS` 追加）
- **错误**：`{"error": "message"}`，HTTP 4xx/5xx
- **流式**：仅 `/api/projects/:id/name-table/ai-translate` 为流式 text/plain

### 全局端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 内嵌单页 Web UI |
| GET | `/api/version` | 版本号 |
| GET | `/api/version/check` | 版本 + 最新版本 + `update_available` |
| GET | `/api/translators` | 支持的翻译引擎列表（隐藏 show-plugs/dump-name） |
| GET | `/api/jobs` | 翻译任务历史 |
| GET | `/api/jobs/:job_id` | 单个任务详情（404 兜底） |
| POST | `/api/jobs` | 提交翻译任务 `{project_dir, config_file_name, translator, backend_profile?, backend_profile_data?, prompt_template_overrides?}`（返回 202） |
| GET | `/api/plugins` | 插件元数据列表 |
| GET | `/api/problem-types` | 问题检测类型定义 |
| GET | `/api/translation-guidelines` | 翻译规范文件列表 |
| GET | `/api/app-settings` / PUT | 应用全局设置读写 |
| GET | `/api/project-config-template` | 默认项目配置 YAML 模板（带注释） |
| GET | `/api/prompt-templates` | 提示词模板列表 |
| GET | `/api/backend-profiles` | 全局后端配置列表 |
| GET/PUT/DELETE | `/api/backend-profiles/:name` | 全局后端配置读/写/删除 |
| POST | `/api/openai-models` | 探测 OpenAI 兼容接口模型列表 `{endpoint, token?, proxy?, timeout?}` |
| GET | `/api/dictionaries/common` | 公共字典目录与内容 |
| POST | `/api/dictionaries/common/create` / `save` / `delete` | 公共字典管理 |
| POST | `/api/dictionaries/parse` | 字典文本解析为结构化行 |
| POST | `/api/log` | 前端日志上报（写 frontend.log） |
| GET | `/api/projects/workspace-root` | 服务端 workspace 根目录 |
| POST | `/api/projects/init` | 服务端 workspace 根下创建项目 `{name, overwrite?}` |

### 项目级端点（`/api/projects/:id/...`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/config?config=xxx.yaml` | 读取项目配置（query 指定真实配置文件名，默认 config.yaml） |
| PUT | `/config` | 保存项目配置（会丢失注释） |
| GET | `/config-name` | 探测真实配置文件名（config.inc.yaml 优先） |
| GET | `/config-schema` | 参数路径→注释描述映射（设置界面参数解释） |
| POST | `/import` | 导入源文件 `{source_paths: []}` |
| GET | `/files` | 项目文件树（input/output/cache） |
| POST | `/reveal` | 系统文件管理器中定位文件/打开文件夹 |
| GET | `/cache` | 缓存文件列表 |
| GET | `/cache/:filename` | 读取单个缓存文件（`entries: CacheEntry[]`） |
| POST | `/cache/save` | 全量覆盖保存缓存条目 |
| POST | `/cache/delete-entry` | 按 index 删除单条 |
| POST | `/cache/delete-file` | 批量删除缓存文件 |
| POST | `/cache/search` | 跨缓存全文搜索 `{query, field, max_results}` |
| POST | `/cache/replace` | 查找替换 `{query, replacement, field, dry_run}` |
| POST | `/cache/check` | 对条目重跑问题检测（不落盘） |
| POST | `/cache/recheck-all` | 对所有缓存条目重跑问题检测（不落盘） |
| GET | `/cache/:filename/h-ranges` | 单文件 H 行区间（H 标记高亮） |
| GET | `/progress` | 翻译进度统计（total/translated/problems/failed/按文件/重译统计） |
| GET | `/runtime` | 实时运行时快照（**建议 2~3s 轮询**） |
| POST | `/runtime/notices/clear` | 清除未读提示 |
| POST | `/stop` | 停止当前翻译任务 |
| POST | `/check-model` | 校验模型可用性 `{translator, config_file_name, backend_profile?}` |
| POST | `/check-batch-size` | 批次划分预检，计算最大可自然划分行数并返回超限文件 |
| POST | `/build/validate` | 构建前校验（仅提示不阻断） |
| POST | `/build-output` / `/build-output/:filename` | 从缓存构建输出文件（全量或指定文件） |
| GET | `/dictionary?config=` | 项目译前/GPT/译后字典内容 |
| GET | `/dictionary/project?config=` | 项目级字典管理视图 |
| POST | `/dictionary/project/create` / `save` / `delete` | 项目字典文件管理 |
| GET | `/problems?file=` | 翻译问题列表 |
| GET | `/logs?source=&tail=` | 项目日志（engine/frontend，默认 2000 行） |
| GET | `/name-table` | 人名替换表（name替换表.csv/.xlsx） |
| POST | `/name-table/generate` | AI 自动生成人名表 |
| POST | `/name-table/save` | 保存人名表 |
| GET | `/name-dict` | 人名 SRC→DST 映射 |
| POST | `/name-table/ai-translate` | AI 批量翻译人名（流式） |
| GET | `/metadata/filemeta/:filename` | 文件级元数据（pass1_cache） |
| GET | `/metadata/batchmeta/:filename` | 批次级元数据（pass2_cache） |
| GET | `/metadata/globalprompt` | 全局分析（pass0_cache） |
| GET | `/metadata/plotroute` | 剧情路线图（pass0_cache） |
| POST | `/metadata/:type/:filename` | 保存单文件元数据条目 |
| POST | `/alt-translations` | 校验与合并备选译文（improve/brfix 生成） |

### Tauri Rust 命令（IPC `invoke`，非 HTTP）

| 命令 | 参数 | 说明 |
|---|---|---|
| `ensure_backend_ready` | `{hideConsole, timeoutMs}` | 检测 12333 端口，未监听则拉起 Python 后端 |
| `open_folder` | `{path}` | 系统资源管理器打开目录 |
| `reveal_file` | `{path}` | 定位并高亮文件 |

> 注：文件读写类命令（`create_dir`/`write_text_file`/`copy_files`）已移除。项目创建/导入统一走后端 `/api/projects/init` 与 `/import`，前端日志统一走 `/api/log`。

## 开发环境配置和运行指南

### 环境要求

- **Python 3.12+**（依赖版本面向 3.12；`build_release_py312.py` 启动时显式校验）
- **Node.js 18+**（前端构建，Tauri 需要）
- **Rust 工具链**（仅构建桌面壳时需要；前端纯浏览器开发模式不需要）
- 操作系统：主要面向 Windows（构建脚本/批处理），核心逻辑跨平台

### 后端（Python）

```bash
# 安装依赖
pip install -r requirements.txt

# CLI 翻译
python -m GalTransl -p <项目目录> -t <翻译引擎> [-l debug|info|warning|error] [-lang zh-cn|en]

# 服务端（桌面端用，默认端口 12333）
python run_backend.py                # 默认 127.0.0.1:12333
python run_backend.py --port 18910   # 指定端口

# 终端批处理（Windows）
run_GalTransl_terminal.bat
```

### 桌面端（Tauri + SolidJS）

```bash
# 安装依赖
cd desktop && npm install

# 完整桌面开发（自动拉起后端）
npm run tauri:dev
# 或 Windows 一键：run_desktop_dev.bat

# 仅前端开发（需后端已在 12333 端口运行）
npm run dev          # dev server: 127.0.0.1:1420

# 前端测试 / 代码检查
npm run test           # vitest
npm run test:watch
npm run lint / lint:fix
npm run format         # Prettier
```

### 构建发布版

```bash
python build_release.py              # 完整构建（后端 PyInstaller + 前端 Tauri）
python build_release.py --skip-fe    # 仅后端（复用已有前端 exe）
python build_release.py --skip-be    # 仅前端
python build_release.py --clean      # 清理旧产物
python build_release.py --no-zip     # 不创建 zip
```

注意：构建脚本会在 `.venv-build` 目录下**强制清理并重建虚拟环境**、重装全量依赖，即每次构建都从干净环境开始；`--clean` 仅额外清理其他构建产物。

### 测试

- **后端**：`unittest`（异步用 `IsolatedAsyncioTestCase`，pytest 可作为 runner）：`python -m pytest tests/ -v`
- **前端**：`vitest` + `jsdom` + `@solidjs/testing-library`：`cd desktop && npm run test`
- 测试文件：后端在 `tests/test_*.py`（44 个测试文件，覆盖缓存/字典/元数据/API 端点/安全/运行时进度/剧情路线图/备选译文等），前端在 `desktop/src/__tests__/`（8 个测试文件）

### 调试辅助（结合测试翻译项目）

- **测试翻译项目**：`sampleProject/` 是仓库自带的样例项目（含 `gt_input`/`transl_cache`、`config.inc.yaml`、字典与 `name替换表` 模板）。实际调试翻译效果时，可在仓库外自行准备一个完整项目（含 `gt_input`、`config.yaml`、译前/译后字典与人名表），通过 `-p <项目目录>` 指定。
- **测试隔离**：`tests/` 下的端到端测试（如 `test_forglobalprompt_standalone.py`、`test_forfilemeta.py`）均在 `tempfile.mkdtemp()` 中动态构造临时项目，不依赖任何机器上的真实项目路径。
- 翻译项目目录内的日志：`GalTransl.log`（引擎日志）、`api_calls.log`（API 调用明细）、`frontend.log`（前端上报日志）、`error.log`（任务错误日志）
- 服务端模式下调试级别由项目 `loggingLevel`（config.yaml common 段）控制
- `GALTRANSL_ALLOWED_ORIGINS`：追加 Web 模式前端跨域源
- `GALTRANSL_API_TOKEN`：启用写端点 Bearer 鉴权

## 并发模型

- 项目级并行：`workersPerProject`（并发翻译的文件/块数量，信号量控制）
- 文件内并行：`splitFile`（Num：每块 N 句；Equal：等分为 N 块；`splitFileCrossNum` 跨块重叠保上下文）
- 令牌池负载均衡：`tokenStrategy`（random 随机轮询；fallback 主备用切换）
- 自适应并发：`autoAdjustWorkers: true` 按 429 比例与延迟动态调并发（信号量预占/释放实现，见 `set_effective_workers`）
- 全局 RPM 限制：`globalRequestRPM`（进程级 `_GLOBAL_RPM_LOCK` 槽位）
- 停止取消：`threading.Event` stop_event，worker 循环/请求轮询/退避睡眠均检查，可中断流式响应
- 测试隔离：`CSplitter` 用 `threading.local()` 隔离并发任务的块追踪状态

## 字典系统

- **GPT 字典**（制表符分隔 `日文\t中文\t解释`）：注入提示词指导名称/术语/角色翻译
- **译前字典**：翻译前日文→日文替换（规范化变体、纠正口齿不清）
- **译后字典**：翻译后中文→中文替换，支持条件规则（`pre_jp/post_jp[tab]判断词[tab]查找词[tab]替换词`）
- 全局字典在 `Dict/` 目录，项目字典在项目根目录
- `GenDic` 引擎可自动从源文本生成「项目GPT字典-生成.txt」

## 插件系统

使用 bundle 的 [yapsy](https://github.com/tibonihoo/yapsy) 插件框架。插件位于 `plugins/`（全局）或 `<project>/plugins/`（项目局部），每个插件有 `.yaml` 清单文件：

- **GFilePlugin**：自定义文件格式 I/O（`load_file`/`save_file`），如 srt/lrc/vtt/epub
- **GTextPlugin**：文本处理流水线钩子（`before_src_processed`/`after_src_processed`/`before_dst_processed`/`after_dst_processed`）

## 桌面端（前端架构）

- **框架**：SolidJS（`solid-js`）+ `solid-js/store` 状态管理（非 React）
- **路由**：无 URL 路由，`MainArea` 用 `Switch`/`Match` 基于 `appState.activeView` 切换视图。11 个视图：`home`、`translate`、`review`、`settings`、`new-project`、`logs`、`dict`、`backend-profiles`、`plugins`、`prompt-templates`、`project-config`（`@solidjs/router` 虽在依赖中但未使用）
- **API 通信**：`src/lib/api/client.ts` 的 `apiRequest<T>()`（30s 超时、可选 Bearer token、项目 ID base64 编解码）；Tauri 侧 `invoke("ensure_backend_ready")` 管理后端进程生命周期
- **状态管理**：`appStore`（全局：视图/项目/连接/侧边栏/缓存树）、`undoStore`（校对撤销重做）、`confirmStore`/`toastStore`/`logStore`
- **测试**：Vitest + jsdom + `@solidjs/testing-library`
- **代码质量**：ESLint（`eslint-plugin-solid`，`no-explicit-any: error`）+ Prettier

### 全局快捷键（App.tsx `handleGlobalKeyDown`）

| 快捷键 | 功能 | 作用域 |
|---|---|---|
| Ctrl+F | 当前文件内查找（校对页监听 `galtransl:find-in-file` 事件） | 全局（浮层在校对页呈现） |
| Ctrl+H | 打开查找替换侧边栏 | 全局 |
| Ctrl+B | 切换侧边栏 | 全局 |
| Ctrl+S | 保存当前文件（dispatch `galtransl:save` 事件） | 全局 |
| Ctrl+Z / Ctrl+Y | 校对审核页撤销 / 重做 | 仅校对审核页 |
