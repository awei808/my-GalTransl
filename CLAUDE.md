# CLAUDE.md

本文件为 Claude Code（claude.ai/code）在处理此仓库代码时提供指导。

## 项目概述

GalTransl 是一个 Galgame（视觉小说）自动翻译工具，利用大语言模型（GPT-4、Claude、Deepseek、Sakura）和提示词工程进行翻译。版本 7.3.0。通过多阶段流水线处理 name-message JSON 对，将日文原文翻译为中文（或其他目标语言）脚本。

## 命令

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 运行 CLI 翻译
python -m GalTransl -p <项目目录> -t <翻译引擎> [-l debug|info|warning|error] [-lang zh-cn|en]

# 运行全部测试
python -m pytest tests/ -v

# 运行单个测试文件
python -m pytest tests/test_split_chunk_runtime_index.py -v

# 运行单个测试用例
python -m pytest tests/test_split_chunk_runtime_index.py::SplitChunkRuntimeIndexTests::test_runtime_index_is_global_when_source_has_no_index -v

# 构建 Windows 发布版（需要 PyInstaller + Node.js 用于 Tauri 前端）
python build_release.py              # 完整构建
python build_release.py --skip-fe    # 仅后端（WSL/Linux）

# 后端服务器（桌面端用）
python run_backend.py --port 18910

# 桌面端开发
cd desktop && npm install && npm run tauri dev

# 构建桌面端可发行文件
C:/Python312/python.exe build_release.py
```

测试使用 `unittest`（异步用 `IsolatedAsyncioTestCase`）。没有用到 pytest 特有的功能。

## 架构

### 核心流水线（三阶段翻译）

完整翻译流水线（`translator: "ForGal-json-multi-chat"`）分三个阶段运行，每个阶段由不同的后端模块驱动：

1. **阶段 1 — `ForFileMetaData`**：读取输入 JSON 文件 → 生成 `transl_cache/pass1_cache/FileMetaData.json`，包含每个文件的剧情摘要、角色列表、题材标签。这不是翻译步骤；它是对源材料的分析。

2. **阶段 2 — `ForBatchMetaData`**：读取 `FileMetaData.json` + 源文件 → 将文本划分为翻译区间（批次），写入 `transl_cache/pass2_cache/BatchMetadata.json`。每个批次标注了视角、氛围、H 级别和用词色彩，用于指导阶段 3 的翻译风格。

3. **阶段 3 — `ForGalJsonMultiChat`**：实际的翻译。使用 `FileMetaData` 和 `BatchMetadata` 作为注入的上下文信息。多轮对话在连续批次之间保持上下文连贯。这是核心翻译器。

其他翻译引擎类型：`GenDic`（自动生成 GPT 字典）、`dump-name`（导出名称表）。

### 关键文件及其职责

| 文件 | 职责 |
|---|---|
| `GalTransl/__init__.py` | 版本号（`7.3.0`）、常量（文件夹名、支持的翻译引擎）、全局变量 |
| `GalTransl/__main__.py` | CLI 入口点，通过 `argparse` 解析参数，验证参数后调用 `Service.run_job()` |
| `GalTransl/Service.py` | 任务生命周期：`JobSpec` 数据类、`run_job()` 编排、取消操作、错误日志记录 |
| `GalTransl/Runner.py` | 设置插件、代理、令牌池、分割器；调度 `doLLMTranslate`（在 Frontend 中） |
| `GalTransl/Frontend/LLMTranslate.py` | **主翻译编排器**：加载文件 → 切分为块 → 工作协程池 → 翻译每个块 → 缓存 → 问题检测 → 输出。`doLLMTranslate()` 函数是核心循环。 |
| `GalTransl/Backend/BaseTranslate.py` | **所有 LLM 后端的基类**。处理 OpenAI 兼容的 API 调用、令牌轮换、速率限制（`_GLOBAL_RPM_LOCK`）、重试、响应解析、自适应并发。`batch_translate()` 是主要入口。 |
| `GalTransl/Backend/ForGalJsonMulitChat.py` | 多轮对话翻译子类。管理对话历史，将 FileMetaData/BatchMetadata 注入提示词，确保跨批次的上下文连续性。 |
| `GalTransl/Backend/Prompts.py` | 系统提示词和翻译提示词模板。 |
| `GalTransl/Backend/ForFileMetaData.py` | 阶段 1 实现——生成每个文件的元数据。 |
| `GalTransl/Backend/ForBatchMetaData.py` | 阶段 2 实现——将文本划分为带风格标签的批次。 |
| `GalTransl/Backend/GenDic.py` | 从源文本自动生成 GPT 字典。 |
| `GalTransl/COpenAI.py` | `COpenAIToken`（单个 API 密钥 + 端点）和 `COpenAITokenPool`（带延迟追踪的负载均衡池）。支持 SakuraLLM 端点。 |
| `GalTransl/CSentense.py` | **核心数据模型**：`CSentense`——一个句子的数据结构，包含 `pre_src`（原文）、`post_src`（预处理后）、`pre_dst`（原始翻译）、`post_dst`（后处理后）、`proofread_dst`、说话人、前后句链接。 |
| `GalTransl/CSplitter.py` | 将输入切分为块：`DictionaryCountSplitter`（每块 N 句）和 `EqualPartsSplitter`（N 等分）。处理跨块重叠以保持上下文。 |
| `GalTransl/Cache.py` | **翻译缓存**：JSON 快照 + `.append.jsonl` 增量文件。缓存键 = 前句+当前句+后句的上下文三元组。`compact_cache_append_logs()` 将追加日志合并到快照中。`save_transCache_to_json()` 的 `post_save=True` 模式重写快照。 |
| `GalTransl/ConfigHelper.py` | YAML 配置解析（`CProjectConfig`）、代理管理（`CProxyPool`）、兼容 httpx 版本的代理设置。 |
| `GalTransl/Dictionary.py` | `CGptDict`（GPT 字典，带名称/上下文注入）、`CNormalDic`（译前和译后替换，支持条件规则）。 |
| `GalTransl/Problem.py` | 翻译后问题检测：词频、标点错误、日文残留、换行问题、长度不匹配、字典合规性、编码问题。 |
| `GalTransl/Loader.py` | 将输入 JSON（文件路径、JSON 字符串或列表）解析为 `CTransList`（`CSentense` 对象列表）。 |
| `GalTransl/GTPlugin.py` | 插件基类：`GTextPlugin`（4 个钩子：before/after_src_processed、before/after_dst_processed）和 `GFilePlugin`（load_file/save_file）。 |
| `GalTransl/server.py` | 桌面端的 HTTP REST API 服务器。`ThreadingHTTPServer` 在 18910 端口。提供项目管理、翻译任务、运行时进度、字典编辑的端点。 |
| `GalTransl/server_runtime.py` | 运行时状态追踪：`RuntimeRegistry`（服务端模式下每个项目的状态）、`RuntimeProgressCache`（缓存文件进度解析）、retranslKey 感知的进度计算。 |
| `GalTransl/i18n.py` | 通过 `get_text()` 提供国际化字符串（zh-cn、en）。 |

### 数据流

```
gt_input/*.json  ──→  Loader.load_transList()  ──→  CSplitter.split()  ──→  块（chunks）
                                                                              │
                                              ┌─────────────────────────────────┘
                                              ▼
                              doLLMTranslate() 工作协程池
                                              │
                          ┌───────────────────┼───────────────────┐
                          ▼                   ▼                   ▼
                   文本插件_源前         gptapi.batch_         文本插件_译后
                   before_src           translate()            after_dst
                          │                   │                   │
                          ▼                   ▼                   ▼
                     缓存命中检查         LLM API 调用         缓存写入
                     (get_transCache)    (BaseTranslate)      (save_transCache)
                          │
                          ▼
              ┌── 缓存未命中 ──→ 翻译 ──→ 写入追加条目
              │
              └── 缓存命中 ──→ 跳过（或 retranslKey 触发重新翻译）

所有块完成 ──→ find_problems() ──→ save_transCache(post_save=True) ──→ 合并输出 ──→ gt_output/*.json
```

### 缓存格式

缓存文件位于 `transl_cache/pass3_cache/`。每个输入文件对应一个 `.json` 快照。翻译过程中，新结果会追加到 `.append.jsonl` 中（每行一个 JSON 对象）。文件完成（或任务取消）时，追加日志会被合并到快照中。

一个缓存条目包含：`index`、`name`、`pre_src`、`post_src`、`pre_dst`、`proofread_dst`、`trans_by`、`proofread_by`、`problem`、`post_dst_preview`。

`__cache_key` 字段（前句+当前句+后句的上下文三元组）用于将源句子与缓存翻译匹配，并带有索引前缀以区分不同位置上的相同句子。

### 项目结构

典型的翻译项目：
```
my_project/
  config.yaml                   # 项目配置
  gt_input/                     # 输入 JSON 文件（name-message 格式）
  gt_output/                    # 输出的翻译后 JSON 文件
  transl_cache/pass3_cache/     # 翻译缓存（增量）
  transl_cache/pass1_cache/     # 文件级元数据缓存
  transl_cache/pass2_cache/     # 批次级元数据缓存
  项目GPT字典.txt                # 项目专用 GPT 字典
  项目字典_译前.txt               # 译前替换规则
  项目字典_译后.txt               # 译后替换规则
```

### 字典系统

- **GPT 字典**（制表符分隔 `日文\t中文\t解释`）：注入到提示词中，用于指导名称、术语、角色特征的翻译
- **译前字典**：翻译前的日文→日文替换（规范化变体、纠正口齿不清）
- **译后字典**：翻译后的中文→中文替换，包括条件规则（`pre_jp/post_jp[tab]判断词[tab]查找词[tab]替换词`）
- 全局字典在 `Dict/` 目录下，项目字典在项目根目录

### 插件系统

使用经过轻微修改的 [yapsy](https://github.com/tibonihoo/yapsy) 插件框架。插件位于 `plugins/`（全局）或 `<project>/plugins/`（项目局部）。每个插件有一个 `.yaml` 清单文件。两种类型：
- **GFilePlugin**：自定义文件格式 I/O（例如 `file_subtitle_srt_lrc_vtt`、`file_epub_epub`）
- **GTextPlugin**：文本处理流水线钩子

## 并发

- 项目级并行通过 `workersPerProject` 控制（并发翻译的文件数量）
- 文件内并行通过 `splitFile` 控制（Num 模式：每块 N 句；Equal 模式：等分为 N 块）
- 令牌池使用 `tokenStrategy`（random 随机轮询；fallback 主备用切换）在多个 API 密钥间负载均衡
- `autoAdjustWorkers: true` 根据 429 速率限制比例和响应延迟动态调整并发度
- 测试隔离：`CSplitter` 中使用 `threading.local()` 隔离每个并发任务的块追踪状态

## 桌面端

使用 Tauri + React 构建，代码在 `desktop/` 目录下。桌面端将 Python 后端服务器（`run_backend.py` → `GalTransl/server.py`）作为子进程启动，通过 18910 端口的 REST API 通信。前端是一个 React SPA（`desktop/src/`），通过 API 与后端交互，实现项目管理、翻译控制、进度监控和字典编辑。
