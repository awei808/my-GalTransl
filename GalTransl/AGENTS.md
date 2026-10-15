# my-GalTransl 后端手册（GalTransl/）

本文件是后端（Python）架构与开发手册，与代码同目录维护。全局硬性规则见仓库根 `AGENTS.md`；前端见 `desktop/AGENTS.md`；dsh 预设见 `agents/AGENTS.md`。改动后端对外行为、新增模块或流水线阶段时，同步更新本文件对应行。

## 项目概述

GalTransl 是一个 Galgame（视觉小说）自动翻译工具，利用大语言模型（GPT-4、Claude、Deepseek、Gemini 等 OpenAI 兼容后端）和提示词工程进行翻译。版本号见 `GalTransl/__init__.py` 的 `GALTRANSL_VERSION`。通过多阶段流水线处理 name-message JSON 对，将日文原文翻译为中文（或其他目标语言）脚本，并支持字典、插件、校对审核、人名表、剧情路线图、语义复核、译文质量改进与统一问题修复等增强能力。程序本体不内置 Agent，Agent 能力通过 MCP 对外提供（官方外置 Agent 为 dsh）。

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
│  └─ Backend/BaseEngine.py（API 客户端基类，OpenAI 兼容）        │
│      ├─ BaseTranslate.py（翻译轮流水线基类）                    │
│      │    └─ ForGalJsonTranslate ← 主翻译后端（多轮/单轮对话    │
│      │         │    可选，混入 Conversation.MultiRoundChatMixin）│
│      │         ├─ ForImproveTranslation（译文改进）             │
│      │         ├─ ForBRStation（换行修复）/ ForJPResidue（残留  │
│      │         │    日文修复）/ ForBanWordFix（用词修复）        │
│      │         └─ ForSemCheck / ForSemCheckAgain（语义复核，    │
│      │              标记疑似错误）/ ForFixRound（统一问题修复）  │
│      ├─ ForGlobalPrompt / ForFileMetaData / ForBatchMetaData  │
│      ├─ ForPlotRouteMap（剧情路线图）/ GenDic（GPT 字典生成）   │
│      └─ 共享模块：metadata.py / utils.py / context.py          │
└─────────────────────────────────────────────────────────────┘
```

- **后端**：Python 3.12+，`GalTransl/` 包，支持 CLI（`python -m GalTransl`）与服务端（`run_backend.py` → `server.py`）两种启动方式。
- **前端**：SolidJS（非 React），位于 `desktop/`，通过 Tauri 壳作为桌面应用运行，也可纯浏览器开发模式运行。
- **通信**：前端经 HTTP REST 与后端通信；后端以 `asyncio` 协程驱动翻译，任务通过 `threading.Event` 停止事件实现可取消。

## Python 代码风格规范

### 1. 导入

- 标准库和第三方库用 `import X`，项目本地用 `from GalTransl.X import Y`。
- 禁止通配符导入（`from X import *`），仅 `Loader.py` 历史遗留过一个，已修复。
- 导入写在文件顶部，模块文档字符串之后。标准库、第三方库、项目本地导入之间不用空行严格分组，但本地导入倾向靠后。
- 多个同层导入可写在一行：`import os, time, sys`。
- `__all__` 不强制，当前代码库未使用。

```python
# 正确
import os
from typing import List
from GalTransl import LOGGER
from GalTransl.CSentense import CSentense, CTransList

# 禁止
from GalTransl.CSentense import *
```

### 2. 命名

| 类型 | 惯例 | 示例 |
|---|---|---|
| 数据实体类 | `C` 前缀 + PascalCase | `CSentense`, `COpenAIToken`, `CProjectConfig`, `CTransList` |
| 抽象/基类/插件类 | PascalCase（无 `C` 前缀） | `BaseTranslate`, `GTextPlugin`, `GFilePlugin`, `RequestHealthMetrics` |
| 函数/方法 | snake_case | `load_transList`, `find_problems`, `build_httpx_proxy_kwargs` |
| 私有函数/方法 | 前导 `_` + snake_case | `_cache_get`, `_build_cache_key_for_tran`, `_is_windows_file_lock_error` |
| 模块级常量 | UPPER_SNAKE | `GALTRANSL_VERSION`, `CONFIG_FILENAME`, `_CACHE_APPEND_SUFFIX` |
| 私有成员变量 | 前导 `_` | `self._pre_src`, `self._lock` |
| 枚举成员 | 中文 | `词频过高`, `残留日文`, `字典使用` |

- 所有标识符必须用英文，不得使用拼音。注释可以用中文。

### 3. 类型注解

- 所有函数签名必须包含参数类型和返回类型注解。
- 使用 `typing` 模块的泛型（`List[str]`, `Dict[str, int]`, `Optional[X]`, `Tuple[A, B]`），不要求使用内置泛型（`list[str]`）。
- `from __future__ import annotations` 可用可不用，不强求统一。

```python
# 正确
def load_transList(json_path_or_list: Union[str, list]) -> Tuple[CTransList, list]:
    ...

async def run_galtransl(cfg: CProjectConfig, translator: str, stop_event: threading.Event | None = None) -> None:
    ...

# 错误——缺少类型
def run_job(spec, state=None, stop_event=None):
    ...
```

### 4. 文档字符串

- 公开类、公开函数必须有文档字符串。内部辅助函数不强求。
- 语言：中文为主，关键概念可中英双语（如插件系统）。
- 格式：Google 风格（`Args:` / `Returns:` / `Raises:`），不强制严格对齐。
- 模块级文档字符串：用简短的 `"""读取 / 处理配置"""` 或 `"""缓存机制"""`。

```python
def load_transList(json_path_or_list: Union[str, list]) -> Tuple[CTransList, list]:
    """
    从json文件路径、json字符串、json list中载入待翻译列表
    json格式为[{"name":xx/"names":[],"message/pre_src":"xx"},...]
    """
```

### 5. 字符串

- 常规字符串用双引号 `"`。
- 字典键、短字符串、内部含双引号的字符串可用单引号 `'`。
- 多行字符串/横幅用原始字符串 `r"""..."""`。
- f-string 优于 `.format()` 和 `%`。

```python
# 推荐
raise ValueError(f"无法解析JSON文件 {json_path_or_list}: {str(e)}")
LOGGER.info(f"{filename}: {str(len(trans_result_list))}/{str(len_trans_list)}")

# 可接受
cache_obj["proofread_dst"] = tran.proofread_zh
'None' if line_priv == "" else line_priv
```

### 6. 异常处理

- 必须用 `except Exception:`，**禁止裸 `except:`**（会吞掉 `KeyboardInterrupt` 和 `SystemExit`）。
- 捕获后要么重新 `raise`，要么记录日志后恢复。仅当确信无需处理时才 `pass`。

```python
# 正确
except Exception:
    LOGGER.error("代理 %s 无法连接", proxy.addr)
    return False, proxy

# 禁止
except:
    pass
```

### 7. 日志

- 统一使用 `LOGGER`（在 `GalTransl/__init__.py` 中定义），**禁止 `print()`**。
- CLI 横幅（`PROGRAM_SPLASH`）和版本信息是唯一允许 `print()` 的场景（在 `__main__.py` 中）。
- 日志级别：
  - `LOGGER.info()` — 关键状态（文件开始/完成、进度）
  - `LOGGER.debug()` — 详细诊断（每轮提示词、缓存命中细节）
  - `LOGGER.warning()` — 可恢复的问题
  - `LOGGER.error()` — 需要关注的失败

```python
# 正确
LOGGER.error(f"Error reading translation_guideline file {file_path}: {e}")
LOGGER.info(f"{filename}: {str(len(trans_result_list))}/{str(len_trans_list)}")

# 禁止
print(f"错误: 文件 '{input_filepath}' 未找到。")
```

### 8. async/await

- 所有 I/O 密集型函数必须用 `async def`。
- 同步入口函数通过 `asyncio.run()` 调用异步核心。
- 并发控制用 `asyncio.Semaphore`。
- 跨线程共享状态用 `threading.Lock`。

```python
# 取消安全的中断睡眠
async def _interruptible_sleep(self, seconds: float) -> None:
    remaining = seconds
    while remaining > 0:
        if self._is_stop_requested(self.pj_config):
            raise JobCancelledError()
        chunk = min(remaining, 0.5)
        await asyncio.sleep(chunk)
        remaining -= chunk
```

### 9. 注释

- 注释用中文。
- 解释性注释放在被解释代码上方或同行。
- **连续 `#` 注释不得超过 2 行**，禁止 3 行及以上的注释块。复杂逻辑应精简为一两句话，或拆到 docstring 中。
- **禁止装饰性分隔线**：不允许 `# ====`、`# ----` 等多行分隔符，用单行简短标题即可（如 `# 生命周期 / 状态管理`）。
- **docstring 精简**：优先一句概述，`Args:`/`Returns:` 仅在非显而易见的参数上保留。超过 6 行内容的 docstring 应检查是否可以缩写。
- 临时禁用代码用注释（而非删除），但不应长期保留。
- 不必写冗余注释（如 `# 递增 i`）；代码自解释优先。

```python
# 正确：最多 2 行，简洁
# 缓存JSON key映射：新key -> 旧key（用于兼容读取旧缓存）
_CACHE_KEY_COMPAT = {
    "pre_src": "pre_jp",
    ...
}

# 禁止：3 行连续注释
# 定义允许的字符集：英文字母、数字和标点符号
# string.punctuation 包含 !"#$%&'()*+,-./:;<=>?@[]^_`{|}~
# string.ascii_letters 包含 a-z 和 A-Z

# 禁止：装饰性分隔线
# ======================================================================
# 对外入口
# ======================================================================
```

### 10. 其他

- **`__slots__`**：轻量数据类可选择性使用以优化内存（如 `IfWord`, `CBasicDicElement`）。
- **dataclass**：较新的模块（`server_runtime.py`, `Service.py`）使用 `@dataclass`，老模块不用。新代码推荐 dataclass。
- **空行**：模块级定义之间空一行。类方法之间不强制空行。
- **条件判断**：`is True` / `is False` 仅在需要区分 `None` 时使用，一般直接写 `if proxy:` / `if not proxy:`。
- **比较**：`isinstance(x, dict)` 而非 `type(x) == dict`。

### 11. 测试

- 测试框架：`unittest`（非 pytest）。
- 异步测试：`unittest.IsolatedAsyncioTestCase`。
- 测试文件放在 `tests/` 目录，命名：`test_<功能>.py`。
- 测试方法命名：`test_<场景描述>`，snake_case，可较长。
- 方法签名加 `-> None` 返回注解。

```python
import unittest

class SplitChunkRuntimeIndexTests(unittest.TestCase):
    def test_runtime_index_is_global_when_source_has_no_index(self) -> None:
        json_list = [{"message": f"line-{i}"} for i in range(1, 7)]
        splitter = DictionaryCountSplitter(dict_count=3, cross_num=0)
        chunks = splitter.split(json_list, file_path="dummy.json")
        self.assertEqual(
            [getattr(t, "runtime_index", None) for t in chunks[0].trans_list],
            [1, 2, 3],
        )

if __name__ == "__main__":
    unittest.main()
```

## 主要模块及其职责（GalTransl/）

| 文件 | 职责 |
|---|---|
| `__init__.py` | 包入口：`LOGGER` 日志器、版本号（`GALTRANSL_VERSION`）、目录常量、`TRANSLATOR_SUPPORTED` 翻译器清单、`LANG_SUPPORTED` 语言表、启动更新检查线程 |
| `__main__.py` | CLI 入口：`argparse` 解析 `--project_dir/--translator/--debug-level/--language` 并调用 `Service.run_job()` |
| `server.py` | **HTTP REST 服务器**（`ThreadingHTTPServer`，默认 127.0.0.1:12333）。项目管理、任务调度、运行时状态、缓存/字典/人名表读写等全部端点；内含单页 Web UI（`INDEX_HTML`） |
| `server_search.py` | 检索层：缓存条目/原始脚本/字典词条/文件与批次元数据的只读检索（MCP 与 HTTP 共用） |
| `mcp_tools.py` | **MCP 工具层**：只读检索工具（含任务状态查询/模型探测等作业域查询，以及字典读取 `read_glossary`，category 支持 gpt/pre/post）与写工具（路线图/元数据/项目字典/缓存条目跳检与备选交换/提交任务/停止任务，`kind=write` 不下发 `read_only_hint`；当前清单以 `enabled_tool_defs()` 与 `/api/mcp-tools` 为准）。含 `write_route_map`（mermaid 校验+原子写）、`write_metadata`（原子写，kind 仅 filemeta/batchmeta；plotroute 必走 write_route_map）、`read_glossary`/`write_glossary`（项目字典词条分页浏览与 upsert/delete，category=gpt/pre/post，原子写、保留注释与行尾、未登记文件自动登记对应清单、字段转义往返校验、不做整体覆盖）、`mark_skip_check`/`swap_alt_translation`（缓存条目逐条改 skip_check 标志 / 交换 alt_dst 与 pre_dst，复用 server_cache 检测链路按 /cache/save 同口径重检，改前快照、经 `/api/jobs` 与运行中/刚结束任务互斥、翻译运行中拒绝）、H 硬门禁 `enforce_h_gate`（复用 `_resolve_cache_h_ranges` + H 词库；可经全局设置 `mcpHGateEnabled` 关闭）。**project_dir 解析**：所有工具接受绝对路径或项目名称——名称优先查 `known_projects.json` 已登记项目（同名取最近使用，NFKC+casefold 容错，失效登记按「仍存在且是可识别项目」过滤），回退工作区根第一层目录扫描，找不到时列出两路来源的可用项目。**设置联动**：`mcpDisabledTools` 黑名单经 `enabled_tool_defs()` 过滤 tools/list 与心跳、`call_mcp_tool` 对禁用工具抛 `MCPToolDisabledError`（均实时读 app_settings.json）；`SERVER_INSTRUCTIONS` 由 `build_server_instructions()` 构建，工具计数/写工具清单随启用集合、H 条款随门禁开关联动。**L3 路径白名单**：写工具经 `_require_write_project_dir` 硬校验目标为可识别项目（存在 `config.inc.yaml`/`config.yaml`），只读工具经 `_attach_project_dir_warning` 附 `project_dir_valid` 告警不阻断。纯函数、不依赖 MCP 传输实现（0.6.0 起内置简易 agent 已移除，Agent 能力统一走 MCP） |
| `mcp_backend_client.py` | **MCP 后端 HTTP 客户端**（0.6.0）：作业域工具（提交/停止/任务状态查询/模型探测）经 HTTP 回到后端——`JobRegistry` 是后端进程内单例，MCP 是独立进程，import 只会拿到空壳。沿用 `GALTRANSL_API_TOKEN` 鉴权，`GALTRANSL_BACKEND_URL` 可覆盖基址；后端不可达时抛中文可读错误 |
| `known_projects.py` | 已知项目登记表（程序目录 `known_projects.json`）：后端在项目级 API 命中（`route_project_api` 入口）、项目创建（`/api/projects/init`）、任务提交（`JobRegistry.submit`）三处 best-effort 登记「项目名 → 绝对路径」（60s 节流 + 原子写 + 200 条上限）；MCP 独立进程离线读该文件按项目名解析 `project_dir`，不要求项目在工作区根下 |
| `server_runtime.py` | 运行时状态追踪：`RuntimeState`/`RuntimeRegistry`（服务端模式下每项目状态）、`RuntimeProgressCache`（缓存文件进度解析，retranslKey 感知）、9 阶段流水线定义与阶段映射 |
| `Service.py` | 任务调度服务层：`JobSpec`/`JobState` 数据类、`run_job_async()`/`run_job()`、取消（`JobCancelledError`）、错误日志（`error.log`，1MB 上限截断）、backend profile / prompt 覆盖注入 |
| `Runner.py` | 翻译运行编排器：`run_galtransl()` 挂载 job 日志 handler（线程过滤防并发重复）、插件加载（yapsy）、代理池/令牌池/分割器初始化、`start_time` 定时启动 |
| `plugin_loader.py` | 文本插件装载（服务端非任务路径复用）：`get_plugin_info_path()` 插件清单路径解析（与 Runner 共用）、`load_text_plugins()` 定位→装载→`gtp_init`（只文本插件、不调 `gtp_final`），按「项目目录 + 插件名单 + 配置文件/插件清单 mtime」进程级缓存。缓存重建/问题重检/构建输出据此与流水线同口径（缺插件会让检测跑在未规整文本上而虚报换行/标点类问题） |
| `Frontend/LLMTranslate.py` | **主翻译编排器**：`doLLMTranslate()` 读文件 → 切 chunk → worker 协程池（队列+哨兵）→ 单 chunk 翻译 → 后处理合并输出；`doLLMTranslSingleChunk()`；完整流水线 `_run_full_pipeline()`；自适应并发（`autoAdjustWorkers`） |
| `Backend/BaseEngine.py` | **所有 LLM 后端 API 客户端基类**：OpenAI 兼容客户端构建（多 token）、`ask_chatbot()`（流式/重试/429 退避/RPM 限制/API 调用日志）、`maxApiRetries` 尝试预算（默认 6，429 不计入）、思考参数路由、全局 RPM 限制、请求健康度统计；含引擎注册表 `ENGINE_REGISTRY`/`@register_engine` |
| `Backend/BaseTranslate.py` | **翻译轮流水线基类**（继承 BaseEngine）：`batch_translate()`、`_batch_translate_common()`、切批/动态句数调节、上下文恢复（`restore_context`）、译文归一化与缓存落盘 |
| `Backend/ForGalJsonTranslate.py` | **主翻译后端**（继承 BaseTranslate，混入 MultiRoundChatMixin）：对话模式可选（`gpt.chatMode`：multi 多轮对话 / single 单轮独立请求+`[history_result]` 上下文注入，单轮用专用提示词 `FORGAL_JSON_TRANS_PROMPT_SINGLE`）、注入 FileMetaData/BatchMetadata/GlobalPrompt、H 词过滤；引擎 ID `ForGal-json-translate`（旧名 `ForGal-json-multi-chat` 经 `TRANSLATOR_ALIASES` 兼容） |
| `Backend/Conversation.py` | 多轮对话状态管理 Mixin（`MultiRoundChatMixin`）：按文件隔离的 conversations、历史裁剪、失败强制首轮标记，与翻译流程解耦 |
| `Backend/metadata.py` | 文件级/批次级元数据实体（`FileMetaData`/`BatchMetadata`）与缓存加载器 |
| `Backend/utils.py` | 共享纯工具：换行符检测、区间解析、批次规整（`normalize_batch_intervals`）、分批后缀剥离 |
| `Backend/context.py` | 全局提示词/路线图上下文装配（供 ForGalJsonTranslate/ForFileMetaData/ForBatchMetaData/ForPlotRouteMap 复用） |
| `Backend/ForGlobalPrompt.py` | 全局游戏分析（全文回退路径）：读压缩全文生成角色/剧情/风格，写 `pass0_cache/GlobalPrompt.json`；路线分析/全局汇总两后端继承其提示词装配与解析规整 |
| `Backend/ForFileMetaData.py` | 文件级元数据：逐文件生成角色/服装/剧情/标签，写 `pass1_cache/*.meta.json` |
| `Backend/ForBatchMetaData.py` | 批次级元数据：划分翻译区间并标注视角/氛围/H/用词色彩，写 `pass2_cache/*.batch.json` |
| `Backend/ForPlotRouteMap.py` | 剧情路线图：基于各文件剧情+用户大纲生成剧情路线，写 `pass0_cache/PlotRouteMap.json` |
| `Backend/ForRouteAnalysis.py` | 路线分析：按路线图「文件归属」逐路线生成局部游戏分析分片 `pass0_cache/route_analysis/<路线名>.json`（路线名安全化、分片按文件范围复用、并行+取消传播；单路线输入超 `internals.globalanalysis.maxRouteInputChars` 时按文件分块依次分析并本地合并），继承 ForGlobalPrompt |
| `Backend/GenDic.py` | GPT 字典自动化构建：terms（本地提取词表逐词翻译）/ llm（AI 全权提取）/ segments（分词+集合覆盖采样）三模式，生成「项目GPT字典-生成.txt」；AI 标注「疑似H」的术语另存「项目GPT字典-生成_h.txt」并登记 gpt.dict（按文件名 `_h` 归类为 h 场景字典，仅 H 场景批次注入；「疑似非术语」仍为主文件 `//` 注释停用，旧版 `//` 行中的疑似H 自动迁移）；批次级断点续跑分片缓存 `transl_cache/gendic_cache/`（指纹含完整请求文本+模型名，forceRegenDic 跳过复用） |
| `Backend/ForGlobalAnalysis.py` | 全局分析汇总：融合各路线分析分片（+路线图 mermaid/节点剧情）为全量 `GlobalPrompt.json`（schema 与全文回退路径一致），继承 ForGlobalPrompt |
| `Backend/ForImproveTranslation.py` | 整文件翻译后评估译文，对可改进句生成备选译文（`alt_dst`），校对页一键交换 |
| `Backend/ForBRStation.py` | 仅对「换行位置异常」句子生成备选译文（`alt_dst`），校对页一键交换 |
| `Backend/ForJPResidue.py` | 对「残留日文」问题译文对照原文生成备选译文（`alt_dst`），校对页一键交换 |
| `Backend/ForBanWordFix.py` | 对「用词不当」问题译文对照原文与问题生成备选译文（`alt_dst`），校对页一键交换 |
| `Backend/ForSemCheck.py` | 语义差异检测：逐句对照原文与译文，仅将语义极大差异的句子标记为「疑似错误」（`suspected_error`），不改译文 |
| `Backend/ForSemCheckAgain.py` | 对已标记「疑似错误」的句子逐句二次复核，剔除误报（可接受的译文会被取消标记） |
| `Backend/BaseFixRound.py` | 问题修复轮基类：按问题类型组合生成备选译文的公共逻辑 |
| `Backend/ForFixRound.py` | 统一问题修复：按所选问题类型组合修复译文（多类可并行），每类按对应修复指令生成备选译文；输入模式自动推导（含需对照原文的类型即用译文+原文，否则仅译文） |
| `Backend/Prompts.py` | 所有提示词模板常量（翻译/校对/GenDic/FileMeta/BatchMeta/Global）与 H 词敏感词表 |
| `COpenAI.py` | `COpenAIToken`（API 密钥+端点）、`COpenAITokenPool`（带延迟追踪的负载均衡池）；历史遗留的 Sakura 端点队列代码已不可达 |
| `ConfigHelper.py` | `CProjectConfig`（项目配置封装）、`CProxyPool` 代理池、`initDictList`/`loadConfigFile`、httpx 版本兼容的代理参数 |
| `CSentense.py` | **核心数据模型**：`CSentense`（单句）、`CTransList`（列表别名），含 `pre_zh` 别名等 |
| `CSplitter.py` | 输入分割/输出合并：`DictionaryCountSplitter`/`EqualPartsSplitter`/`InputSplitter`/`OutputCombiner`/`DictionaryCombiner` |
| `Loader.py` | `load_transList()` 将 JSON（文件/字符串/列表）载入 `CTransList` 并链接前后句上下文 |
| `CSerialize.py` | 序列化输出：`update_json_with_transList`/`save_transList_to_json_cn` |
| `Cache.py` | 翻译缓存：`save_transCache_to_json`/`get_transCache_from_json`、`.append.jsonl` 增量合并（`compact_cache_append_logs`）、原子写入 |
| `Dictionary.py` | 字典系统：`CNormalDic`（译前/译后替换，条件规则）、`CGptDict`（GPT 字典）、`CBasicDicElement` 等 |
| `Name.py` | 人名替换表（CSV/XLSX）提取、加载、导出；`extract_names_from_dir`/`load_name_table`/`dump_name_table_from_chunks` |
| `Problem.py` | 质量检查：`find_problems()` 逐句检测并写入 `tran.problem` 属性。`problem` 为「半角逗号分隔的类型列表」，细节文本统一经 `normalize_problem_detail()` 把半角逗号转全角（前端 `lib/problems.ts` 的 `problemTypesOf` 与后端 `_filter_problem_by_types` 均按半角逗号切分，不归一会切出伪类型名） |
| `GTPlugin.py` | 插件基类：`GTextPlugin`（before/after_src_processed、before/after_dst_processed）、`GFilePlugin`（load/save） |
| `TextCompressor.py` | 文本无损压缩器，压缩全文供全局分析并校验完整性（`verify_compression`） |
| `DataValidator.py` | 统一数据校验：输入/压缩/LLM 响应/元数据/输出五层校验（`validate_input_json`/`validate_global_prompt` 等） |
| `TerminalOutput.py` | 终端进度条封装：`terminal_progress`、`NullProgressBar`、`should_print_translation_logs` |
| `ApiLogger.py` | API 调用结构化日志（`api_calls.log`），异步 writer，TraceID 串联请求/响应 |
| `AppSettings.py` | 应用级设置（`app_settings.json`）：`printTranslationLogInTerminal`/`maxConcurrentJobs`/`writeApiCallLog`/`writeFrontendLog`/`mcpHGateEnabled`（MCP H 门禁开关）/`mcpDisabledTools`（禁用工具名黑名单）——MCP 独立进程经 `resolve_app_dir()` 读同一份文件。`save_app_settings` 是该文件唯一写路径且为**整份覆写**（缺键/null 回落默认值），故它统一留痕：有变更记 INFO、请求未携带却被改动记 WARNING（护栏，见端点表 PUT 说明） |
| `DefaultProjectConfig.py` | 新项目的默认 `config.yaml` 模板内容（字符串常量） |
| `backend_security.py` | 后端 HTTP 安全策略：CORS 源白名单、`GALTRANSL_API_TOKEN` Bearer 鉴权（写端点可选）、路径穿越防护纯函数 |
| `i18n.py` | 多语言文本表（zh-cn/en）：`get_text()`/`GT_LANG` |
| `Utils.py` | 通用工具：读翻译规范文件、正则提取、标点/语言检测、换行处理 |
| `yapsy/` | 第三方插件框架（bundle 在此），`PluginManager` 定位/加载/激活插件 |

## 核心流水线（ForGal-full-pipeline，9 阶段）

`translator: "ForGal-full-pipeline"` 自动串联以下阶段（`server_runtime.PIPELINE_STAGE_NAMES` 共 9 阶段，前端"流程完成情况"按此显示；`_run_full_pipeline()` 按 `pipeline_stages.PIPELINE_STAGES` 清单顺序调度）：

| 阶段 | 名称 | 说明 | 产物 |
|---|---|---|---|
| 0 | 输入数据校验 | `DataValidator.validate_input_json` 逐文件校验 | — |
| 1 | 文本无损压缩 | `TextCompressor` 压缩全文并校验完整性 | — |
| 2 | 构建术语表 | `GenDic` 生成 GPT 字典（已有非空字典可跳过） | 项目根「项目GPT字典-生成.txt」 |
| 3 | 生成文件级元数据 | `ForFileMetaData` 逐文件（可生成称呼映射 `address_map`，开关 `internals.forfilemeta.address_map`）；非空行数 < `internals.pipeline.metaMinLines`（默认 20，0=不跳过）的文件跳过不生成 | `transl_cache/pass1_cache/*.meta.json` |
| 4 | 剧情路线图 | `ForPlotRouteMap` 基于各文件剧情+大纲 | `transl_cache/pass0_cache/PlotRouteMap.json` |
| 5 | 生成全局游戏分析 | **路线分片汇总**：`ForRouteAnalysis` 按「文件归属」逐路线分析（分片可复用，并行 `internals.globalanalysis.routeParallelism`）→ `ForGlobalAnalysis` 融合分片产出全量分析；路线图缺失/mermaid 为空/划分不出任何可分析路线时回退 `ForGlobalPrompt` 全文分析；覆盖缺口（未归属文件）合成为「未归属文件」独立分片继续分片汇总，路线数超 `internals.globalanalysis.maxRoutes` 仅软告警不阻断 | `pass0_cache/route_analysis/*.json` 分片 + `pass0_cache/GlobalPrompt.json` |
| 6 | 划分翻译区间 | `ForBatchMetaData` 批次+视角/氛围/H（0-1 浮点强度）标注；行数阈值同阶段 3 | `transl_cache/pass2_cache/*.batch.json` |
| 7 | 翻译执行 | `ForGalJsonTranslate` 翻译（对话模式 `gpt.chatMode`：multi 多轮 / single 单轮） | `transl_cache/pass3_cache/*.json` → `gt_output/` |
| 8 | 译文质量改进 | 按 `common.gpt.afterTranslation` **有序数组**依次执行后处理项：improve / brfix / jpfix / banfix / semcheck / semcheckagain / tonecheck / tonecheckagain / fix（fix 为 `{fix: {types: [...], injectProblem}}` 对象条目；旧 improve/brfix 字符串格式仍兼容；tonecheck/tonecheckagain 的色彩基准为**软依赖**——优先批次区间「用词色彩」标注，无批次元数据/区间不相交/色彩字段全空时回退对照 `src` 原文语气，不再跳过），逐文件生成备选译文或疑似错误标记 | `transl_cache/*.json` 的 `alt_dst` / `suspected_error` / `tone_issue` 字段 |

各阶段均可按缓存产物是否已存在而跳过（`internals.pipeline.forceRegen*` 与 `internals.globalanalysis.forceRegenRouteAnalysis` 可强制重生成）。元数据两阶段（3/6）另有行数阈值跳过：非空行数 < `internals.pipeline.metaMinLines`（默认 20）的文件不调用 AI、不写元数据产物，判定口径为 `metadata.count_nonempty_lines`（与前端进度分母同口径）；后端 `_run_meta_worker_pool` 返回 `MetaPoolResult` 单独汇报该类跳过，交叉验证与前端文件进度面板（`meta_min_lines`）按「已跳过」处理，避免误报缺失。独立引擎（`ForGlobalPrompt`/`ForRouteAnalysis`/`ForGlobalAnalysis`/`ForPlotRouteMap`/`GenDic`/`ForFileMetaData`/`ForBatchMetaData`/`ForImproveTranslation`/`ForBRStation`/`ForJPResidue`/`ForBanWordFix`/`ForSemCheck`/`ForSemCheckAgain`/`ForFixRound`/`dump-name`/`show-plugs`）也可单独作为 `translator` 运行。

## 关键依赖项（Python）

完整清单以 `requirements.txt` 为准，此处仅列理解架构所需的关键项：

- `openai`（`AsyncOpenAI`，核心 LLM 调用）+ `httpx`/`httpx-aiohttp`（异步传输，可选 `pyreqwest` 高性能 transport）
- `PyYAML`（项目配置解析）、`orjson`（高速 JSON 读写）、`tenacity`（异步重试）
- `tiktoken` / `vaporetto` / `budoux`（分词与 token 计数）、`fasttext-predict`（语言检测）、`opencc`（简繁转换）
- `openpyxl`（人名表 `.xlsx`）、`colorlog` / `alive-progress`（终端日志与进度条）
- `mcp` / `playwright` 仅 MCP 调试脚本（`desktop/scripts/tauri_webview_mcp.py`）专用，非运行时必需

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
| `tokenPool` / `proxyPool` | 令牌池/代理池 |
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

服务端模式下每项目一个，供前端 `/runtime` 轮询：`stage`/`stage_index`/`stage_total`（9 阶段）、`current_file`/`current_batch`/`batch_total`、`workers_active`/`workers_configured`、`translation_speed_lpm`（1 分钟窗口速度）、`latest_prompt_preview`/`translation_previews`（实时提示词/译文预览）、`ttft_states`（各 worker 流式首字延迟状态与预览，前端 TTFT 状态灯）、`recent_errors`/`recent_successes`（环形队列）、`notices`（一次性用户提示，前端 toast 后清除）。

### 缓存条目（CacheEntry）

缓存文件（`transl_cache/pass3_cache/*.json`）中每条 JSON 的核心字段：`index`、`name`、`pre_src`、`post_src`、`pre_dst`、`proofread_dst`、`trans_by`、`proofread_by`、`problem`、`post_dst_preview`，以及内部键 `__cache_key`（前句+当前句+后句上下文三元组，带 index 前缀区分同文不同位置）。译文质量改进/问题修复阶段（improve/brfix/jpfix/banfix/fix）会在条目上追加 `alt_dst`（备选译文，与主译文不同才落盘），校对页可一键交换；semcheck/semcheckagain 会写入 `suspected_error`（疑似错误标记，复核通过后移除）。

## API 接口文档（唯一归属）

> 前端调用封装与 Tauri IPC 命令见 `desktop/AGENTS.md`；本表是端点定义的唯一归属，前端文档不复制。

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
| POST | `/api/jobs` | 提交翻译任务 `{project_dir, config_file_name, translator, backend_profile?, backend_profile_data?, prompt_template_overrides?, file_filter?, config_overrides?}`（返回 202；file_filter 为文件子集，config_overrides 为点分键配置覆盖） |
| GET | `/api/plugins` | 插件元数据列表 |
| GET | `/api/problem-types` | 问题检测类型定义 |
| GET | `/api/translation-guidelines` | 翻译规范文件列表 |
| GET | `/api/app-settings` / PUT | 应用全局设置读写（含 MCP 门禁 `mcpHGateEnabled`/`mcpDisabledTools`）。**PUT 为整份覆写**：请求缺键或显式 null 一律回落默认值，调用方必须先 GET 再合并；写盘统一经 `AppSettings.save_app_settings` 留痕，其中「请求未携带却被改动」的键记 WARNING（缺键 PUT 会静默关掉 `writeApiCallLog`、清空 `mcpDisabledTools` 等） |
| GET | `/api/mcp-tools` | MCP 工具清单 `{tools: [{name, description, kind}]}`（设置页工具开关的唯一真源） |
| GET | `/api/project-config-template` | 默认项目配置 YAML 模板（带注释） |
| GET | `/api/prompt-templates` | 提示词模板列表 |
| GET | `/api/backend-profiles` | 全局API 配置列表 |
| GET/PUT/DELETE | `/api/backend-profiles/:name` | 全局API 配置读/写/删除 |
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
| POST | `/cache/search` | 跨缓存全文搜索 `{query, field, max_results, options?}`（`options.re` 为正则搜索，仅作用于搜索，替换仍为字面量） |
| POST | `/cache/replace` | 查找替换 `{query, replacement, field, dry_run}`；`src`/`problem` 字段拒绝替换（返回 400），`all` 仅译文侧字段 |
| POST | `/cache/replace-entry` | 单条缓存条目内替换（字段口径同 `/cache/replace`） |
| POST | `/cache/check` | 对条目重跑问题检测（`persist=true` 时写回缓存） |
| POST | `/cache/recheck-all` | 对项目全部翻译缓存文件重跑问题检测并写回 `problem`/`post_dst_preview`（后台异步，立即返回 202；文件范围与 `/problems`、构建输出一致，跳过 pass1/pass2 元数据与示例文件） |
| GET | `/cache/:filename/h-ranges` | 单文件 H 行区间（H 标记高亮） |
| GET | `/progress` | 翻译进度统计（total/translated/problems/failed/按文件，文件范围同上） |
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
| GET | `/problems?file=` | 翻译问题列表（不传 `file` 时覆盖全部翻译缓存文件） |
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
| GET | `/metadata/routeanalysis/:filename` | 路线分析分片（pass0_cache/route_analysis，文件名为安全化路线名） |
| POST | `/metadata/:type/:filename` | 保存单文件元数据条目 |
| POST | `/alt-translations` | 校验与合并备选译文（后处理阶段生成） |
| POST | `/review/ai-suggest` | 校对页单句 AI 精修 `{file, index, draft?, prev_dst?, next_dst?, instruction?}`（`draft` 键存在即按原值使用，空串=按未翻译交给 AI；前后句缺省回退磁盘缓存），一次性不落盘，响应 `{suggestion, model, source}`。后端解析链：请求体指定 → `common.stageBackends.proofread`（「人工校对时 AI 精修」专用槽位，引用失效返回 400，不静默回退）→ 项目主配置 → 首个全局兼容配置；全局单飞，翻译任务运行中返回 409 |
| POST | `/metadata/search` | 文件/批次元数据文本检索 `{query, scope?, max_results?, re?}` |

## 并发模型

- 项目级并行：`workersPerProject`（并发翻译的文件/块数量，信号量控制）
- 文件内并行：`splitFile`（Num：每块 N 句；Equal：等分为 N 块；`splitFileCrossNum` 跨块重叠保上下文）
- 令牌池负载均衡：`tokenStrategy`（random 随机轮询；fallback 主备用切换）
- 自适应并发：`autoAdjustWorkers: true` 按 429 比例与延迟动态调并发（信号量预占/释放实现，见 `set_effective_workers`）
- 全局 RPM 限制：`globalRequestRPM`（进程级 `_GLOBAL_RPM_LOCK` 槽位）
- 重试预算：`maxApiRetries`（默认 6 次尝试上限；429 退避不计入预算）
- 客户端治理：HTTP 连接池有界 + 30s 空闲过期；客户端连续失败自动回收重建
- TTFT 上报：流式首字延迟状态上报至 `RuntimeState.ttft_states`（前端状态灯）
- 停止取消：`threading.Event` stop_event，worker 循环/请求轮询/退避睡眠均检查，可中断流式响应
- 测试隔离：`CSplitter` 用 `threading.local()` 隔离并发任务的块追踪状态

## 字典系统

- **GPT 字典**（竖线 `|` 分隔 `日文|中文|解释`；旧 Tab/四空格/`->` 格式仅作兼容读取）：注入提示词指导名称/术语/角色翻译
- **译前字典**：翻译前日文→日文替换（规范化变体、纠正口齿不清）
- **译后字典**：翻译后中文→中文替换，支持条件规则（`pre_src/post_src|判断词|查找词|替换词`，旧键名 `pre_jp/post_jp`/`pre_zh/post_zh` 仍兼容；判断词支持 `re:` 正则，`!re:` 取反可组合，`>`/`<` 限定与正则组合时告警回退字面量）
- 注释：仅整行行首 `//` 生效（`#` 与反斜杠不再作为注释符）
- 正则词条：搜索词加 `re:` 前缀（在 `1^`/`^^` 剥离之后判定，可组合，如 `1^re:A+`），全部字典类型（GPT/译前/译后/条件/场景/h 词库/禁用词库）支持，但 `1^`/`^^` 组合仅替换/GPT 类词条（译前/译后/条件/场景/GPT）识别，h 词库与禁用词库仅识别行首 `re:`；`1^`/`^^` 为上游遗留兼容语法（加载时告警建议迁移），等价正则写法：`1^词|替换` → `re:(?s)^(.*?)词|\1替换`（`(?s)` 跨行、替换列落盘 `\\1`）、`^^词|替换` → `re:^词|替换`；`\|` 表示字段内字面竖线（正则选择符写法 `re:あ\|い`）；非法正则一律回退字面量匹配（替换/GPT 词条与检测词表均是）；可匹配空串（零宽）的正则在替换/GPT 词条中丢弃，检测词表（h 词库/禁用词库）中同样回退字面量；`check_dic_use` 重叠词消费式去重由 `dictionary.skipOverlapCheck`（默认 true）控制
- 全局字典在 `Dict/` 目录，项目字典在项目根目录
- `GenDic` 引擎可自动生成「项目GPT字典-生成.txt」，支持 terms（本地提取词表逐词翻译）/ segments（分词+集合覆盖采样）双模式

## 插件系统

使用 bundle 的 [yapsy](https://github.com/tibonihoo/yapsy) 插件框架。插件位于 `plugins/`（全局）或 `<project>/plugins/`（项目局部），每个插件有 `.yaml` 清单文件：

- **GFilePlugin**：自定义文件格式 I/O（`load_file`/`save_file`），如 srt/lrc/vtt/epub
- **GTextPlugin**：文本处理流水线钩子（`before_src_processed`/`after_src_processed`/`before_dst_processed`/`after_dst_processed`）

## 开发环境配置和运行指南（后端）

### 环境要求

- **Python 3.12+**（依赖版本面向 3.12；`build_release_py312.py` 启动时显式校验）
- 操作系统：主要面向 Windows（构建脚本/批处理），核心逻辑跨平台

### 常用命令

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

### 测试（后端）

- 框架 `unittest`（异步用 `IsolatedAsyncioTestCase`，pytest 作为 runner）：`python -m pytest tests/ -v`
- 全量 runner：`python run_pytest_utf8.py`（以完整 Unicode 路径全量运行 `tests/`，规避中文路径下 PowerShell 的转码乱码；可透传 pytest 参数，如 `python run_pytest_utf8.py -v tests/test_xxx.py -k foo`）
- 测试文件在 `tests/test_*.py`，覆盖缓存/字典/元数据/API 端点/安全/运行时进度/剧情路线图/备选译文/语义复核/统一问题修复/GenDic terms 模式等
- 测试隔离：`tests/` 下的端到端测试（如 `test_forglobalprompt_standalone.py`、`test_forfilemeta.py`）均在 `tempfile.mkdtemp()` 中动态构造临时项目，不依赖任何机器上的真实项目路径

### 调试辅助（结合测试翻译项目）

- **测试翻译项目**：`sampleProject/` 是仓库自带的样例项目（含 `gt_input`/`transl_cache`、`config.inc.yaml`、字典与 `name替换表` 模板）。实际调试翻译效果时，可在仓库外自行准备一个完整项目（含 `gt_input`、`config.yaml`、译前/译后字典与人名表），通过 `-p <项目目录>` 指定。
- 翻译项目目录内的日志：`GalTransl.log`（引擎日志）、`api_calls.log`（API 调用明细）、`frontend.log`（前端上报日志）、`error.log`（任务错误日志）
- 服务端模式下调试级别由项目 `loggingLevel`（config.yaml common 段）控制
- `GALTRANSL_ALLOWED_ORIGINS`：追加 Web 模式前端跨域源
- `GALTRANSL_API_TOKEN`：启用写端点 Bearer 鉴权
