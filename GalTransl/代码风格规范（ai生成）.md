# GalTransl 代码风格与规范

---

## 1. 总则

这是一个**务实优先、中文团队主导**的工程。模块化、类型注解、配置/i18n/插件体系设计成熟；但**缺乏统一的强制 lint/format 约束**，风格靠"样板示例"维持，因此存在少量不合规写法（详见第 12 节）。

代码总体遵循 **PEP 8 的大方向**（缩进 4 空格、snake_case 函数与变量、模块级常量大写），但在**类名**上保留了显著的「匈牙利式 `C` 前缀」遗留约定。

---

## 2. 语言与运行环境

| 项 | 约定 |
|---|---|
| Python | **3.11+**（README 要求 3.11.9），大量使用现代语法 |
| 泛型 | 优先内置泛型 `tuple[float, float, bool]`、`list[str]`，与 `typing` 混用 |
| 数据类 | `@dataclass`（但常**手写 `__init__`**，见第 4 节） |
| 异步 | `asyncio` + `httpx`（基于 `AsyncOpenAI`） |
| 序列化 | `orjson` 替代 `json`（提速） |
| 字符串 | `OpenCC` 做繁简转换；`tiktoken` 估算 token |
| 桌面端 | **Tauri(Rust) + React/TypeScript**（独立进程、独立仓库内目录 `desktop/`） |

依赖清单见 `requirements.txt`（openai、httpx-aiohttp、orjson、opencc、PyYAML、tenacity、tiktoken、InquirerPy、aiofiles、pyreqwest 等）。

---

## 3. 命名规范

### 3.1 类（Class）—— 两类并存，且有违例

| 风格 | 约定 | 示例（源码实锤） |
|---|---|---|
| **核心 C 前缀类**（匈牙利式遗留） | `C` + PascalCase | `COpenAI`、`COpenAIToken`、`COpenAITokenPool`、`CProxy`、`CProxyPool`、`CProjectConfig`、`CSentense`、`CBasicDicElement`、`CGptDict`、`CRebuildTranslate`、`CSakuraTranslate` |
| 普通 PascalCase 类 | 纯 PascalCase | `BaseTranslate`、`RequestHealthMetrics`、`InputSplitter`、`OutputCombiner`、`ConfigHelper`、`Dictionary`、`Loader`、`Runner` |
| ⚠️ **不合规（小写开头类名）** | 插件约定类统一叫 `file_plugin`/`text_xxx` | `file_plugin(GFilePlugin)`（所有 file 插件）、`ifWord`（Dictionary.py:8）、`skip_noJP(GTextPlugin)`、`comet_calculator` |

> **规律**：`GalTransl/` 核心包里"业务实体/管理类"带 `C` 前缀；`plugins/` 插件里类固定叫 `file_plugin` / `text_xxx`（插件加载器按此约定反射实例化），由此**天然产生小写开头类名**——这是插件框架的硬约束，不算随意违规。

### 3.2 函数 / 方法
- **snake_case**，私有成员加前导 `_`：
  `_coerce_bool`、`_coerce_positive_int`、`_build_prompt_request`、`_apply_history_result`、`load_file`、`gtp_init`、`save_file`、`gtp_final`

### 3.3 变量 / 属性
- **snake_case**（含缩写）：`self.pj_config`、`eng_type`、`json_list`、`src_msg`、`num_pre_request`、`token_pool`、`proxy_pool`

### 3.4 模块常量
- **UPPER_SNAKE**，私有加前导 `_`：
  `_GLOBAL_RPM_LOCK`、`_GLOBAL_NEXT_ALLOWED_TS`、`_SIGCHARS`、`DEFAULT_PROJECT_CONFIG_YAML`
- 单例/全局出口挂在 `GalTransl/__init__.py`：`LOGGER`、`GALTRANSL_VERSION`、`AUTHOR`、`TRANSLATOR_SUPPORTED`、`LANG_SUPPORTED`、`DEBUG_LEVEL`、`CONFIG_FILENAME`、`INPUT_FOLDERNAME` 等，各处 `from GalTransl import LOGGER` 共用。

---

## 4. 类型注解

**采用度很高**——几乎所有函数签名都带返回类型：

```python
def _coerce_bool(value) -> bool: ...
def _coerce_positive_int(value, default: int) -> int: ...
def _get_effective_num_per_request(self, configured_value: int, proofread: bool = False) -> int: ...
def snapshot(self, window_seconds: float = 30.0) -> dict: ...
```

- 混用 `typing`（`Optional/List/Dict/Union/Tuple/ClassVar`）与内置泛型。
- `@dataclass` 中**仍常手写 `__init__`**（非惯用法但合法）：

```python
@dataclass
class ifWord:
    without_flag: bool = False
    startswith_flag: bool = False
    endswith_flag: bool = False
    word: str = ""
    __slots__ = ["without_flag", "startswith_flag", "endswith_flag", "word"]
    def __init__(self, if_word): ...
```

- **海量小对象用 `__slots__` 省内存**：`ifWord`、`CBasicDicElement` 等。

---

## 5. 注释与文档字符串

- **主体注释为中文**：`# 翻译规范`、`# 429等待时间（废弃）`、`# 保存间隔`、`# 选翻译器`。
- **文档字符串常见中英双语**，且格式**三四种混用**（无统一 docstring 规范）：
  - Google 式 `Args:/Returns:`
  - Sphinx 式 `:param:/:return:`
  - 紧凑式 `:line: 一行描述`
  - 大量方法**完全没有 docstring**（`_coerce_bool`、`get_dst` 等）。
- 插件文件 docstring 常为「英文句 + 中文句」逐行对照（面向国际贡献者）。

---

## 6. 模块结构与全局约定

- **`GalTransl/__init__.py` 是中央出口**：挂模块级常量/单例，避免循环依赖 + 统一入口。
- **i18n 机制**：`from GalTransl.i18n import get_text, GT_LANG`，所有 UI 与报错文案走 `get_text("key", lang)`，不直接写死字符串：

```python
raise ValueError(get_text("invalid_source_language", self.target_lang, self.source_lang))
LOGGER.error(get_text("cache_read_error", GT_LANG, cache_file_path))
```

- **双层配置**：
  - 工程配置：`config.getKey("gpt.contextNum", 8)`（点分路径 + 默认值），见第 11 节。
  - 后端配置段：`config.getBackendConfigSection("OpenAI-Compatible").get(...)`，YAML 驱动。

---

## 7. 错误处理与鲁棒性

- **可选依赖兜底**：`try/except Exception` 包裹，缺失则置 `None` 后续判空（如 `HttpxTransport = None`）。
- **配置段缺失兜底**：`try/except` 包成默认值（如 `backend_rpm` 取不到就 `0`）。
- **临时挂状态**：用 `getattr/setattr` 给 config 对象临时挂运行态（如 `request_health_metrics`）。
- **报错走 i18n**：用户可见错误全部 `get_text(...)`，不直接 raise 裸字符串。
- **API 调用重试**：`ask_chatbot` 内统一重试/指数退避 + 换 key，SDK 设 `max_retries=0`（不用 SDK 自带重试）。

---

## 8. 并发与性能取向

- **全局 RPM 节流**：`threading.Lock`（`_GLOBAL_RPM_LOCK`）+ 全局时间戳。
- **每任务（每线程）独立追踪**：`threading.local()` 做 chunk 追踪，避免并发互相干扰。
- **流式边收边解析**：`stream=True` + `stream_line_callback`，响应未结束即逐行解析（速度感来源）。
- **小对象优化**：`__slots__` + `orjson` + `deque`（带时间窗指标滑动统计）。
- **翻译器内集成 OpenCC** 繁简转换，避免二次调用。

---

## 9. 插件机制（yapsy）

- 基类 `GFilePlugin`（文件格式）、`GTextPlugin`（文本处理），插件只需实现四个钩子：
  `gtp_init` / `load_file` / `save_file` / `gtp_final`
- 约定：目录 `file_<格式>_<扩展名>/` 或 `text_<功能>/`，含 `xxxx.py` + `xxxx.yaml`；类固定叫 `file_plugin` / `text_xxx`。
- 插件**自包含**，甚至把 `lxml`/`ebooklib`/`webvtt` 直接 vendored 进插件目录（`file_epub_epub` 插件）。

---

## 10. 设计模式

- **模板方法（Template Method）**：`BaseTranslate` 定义翻译骨架（`translate` 抽象、调度、解析、缓存），子类 `ForGalJsonTranslate` / `ForGalTsvTranslate` / `ForNovelTranslate` / `CSakuraTranslate` 只重写"如何编码输入 + 如何解析输出"。
- **策略（Strategy）**：`init_gptapi()` 用 `match eng_type` 把 `translator` 字符串映射到具体后端类（延迟 import）。
- **输入/输出拆分器**：`InputSplitter`/`OutputCombiner` 基类用 `pass` 占位抽象方法，子类 `DictionaryCountSplitter`/`EqualPartsSplitter`/`DictionaryCombiner` 实现。

---

## 11. 配置读取约定（重点）

所有配置通过 `CProjectConfig.getKey(key, default=None)` 读取，键为**点分路径**字符串：

```python
workersPerProject = projectConfig.getKey("workersPerProject") or 1          # 缺省 or 兜底
enable_auto_workers = bool(projectConfig.getKey("autoAdjustWorkers", False)) # 传 default
soryBy = projectConfig.getKey("sortBy", "name")
projectConfig.getKey("gpt.numPerRequestTranslate")   # 注意前缀 gpt，非 common
```

> ⚠️ **关键陷阱**：`getKey` 本身**不做默认值兜底**（`return self.keyValues.get(key, default)`，default 来自调用方）。因此工程 YAML **缺字段会运行时崩溃**（如 `gpt.numPerRequestTranslate` 缺失 → `int + None` TypeError）。加载逻辑 `loadConfigFile` 未合并 `DefaultProjectConfig.py` 的默认值模板，`validate()` 也被注释。新代码若依赖某配置项，**必须自己 or 兜底或确保调用方传 default**。

---

## 12. 规范缺口（客观观察）

- 顶层**无 `pyproject.toml` / `.flake8` / `setup.cfg` / `.editorconfig` / `ruff` / `mypy`**——无强制 linter/autoformatter。
- 由此导致：小写开头类名（`file_plugin`、`ifWord`、`skip_noJP`）、docstring 格式混乱、零星无意义 `pass`。
- 无统一 import 排序（标准库 / 三方 / 本地混合，未强制 isort）。

---

## 13. 新增代码应遵循的约定清单（Actionable）

若要在本项目新增/修改代码（如新增后端 `ForGalJsonTranslateDS.py`）：

1. **新增后端**：继承 `BaseTranslate`，实现 `translate()` + 解析器；在 `Frontend/LLMTranslate.py` 的 `init_gptapi()` 加 `case "ForGal-json-ds":` 映射（延迟 import）。
2. **类名**：核心实体/管理类用 `C` 前缀（`CPlotMetadata`）；插件类用 `file_plugin`/`text_xxx`；普通工具类用纯 PascalCase。**不要**起无意义的小写开头类名（除非是插件框架约束）。
3. **函数/变量**：snake_case，私有加 `_`；常量 UPPER_SNAKE。
4. **类型注解**：所有函数签名带返回类型；用内置泛型优先于 `typing`。
5. **配置读取**：通过 `config.getKey("gpt.xxx", default)`，**务必传 default 或 `or` 兜底**，避免 None 崩溃。
6. **i18n**：用户可见文案一律 `get_text("key", GT_LANG)`，不写死中/英字符串。
7. **错误与重试**：API 调用走基类 `ask_chatbot`（自带重试/节流/换 key），不要自己裸调 `requests`；可选依赖用 `try/except` 兜底。
8. **流式 + 边解析**：翻译结果优先支持 `stream=True` + 边收边解析，保持与现有 jsonline 契约（`sig|{id,(name),dst}`）兼容。
9. **注释**：关键业务逻辑用中文注释说明意图；docstring 可选但建议 Google 式统一。
10. **复用而非重写**：通用能力（客户端构造、节流、解析校验、批量调度）一律调基类方法，子类只管"格式相关"的编码/解析。

---

## 附：风格速查表

| 维度 | 约定 | 反例（需避免/已有违例） |
|---|---|---|
| 类 | `C` 前缀核心类 / 纯 PascalCase / 插件 `file_plugin` | 随心小写开头（`ifWord`、`skip_noJP`，仅插件豁免） |
| 函数/变量 | snake_case，私有 `_` | camelCase |
| 常量 | UPPER_SNAKE，私有 `_` | 小写常量 |
| 类型 | 全函数返回类型 + 内置泛型 | 无注解 |
| 配置 | `getKey("gpt.x", default)` 必带兜底 | 裸 `getKey("gpt.x")` 致 None 崩溃 |
| i18n | `get_text("key", GT_LANG)` | 写死中/英串 |
| API 调用 | 经基类 `ask_chatbot` | 裸 `requests`/SDK 自带重试 |
| 文档 | 中文注释 + （建议）Google docstring | 完全无注释 |
| lint | 无强制（靠约定） | —— |