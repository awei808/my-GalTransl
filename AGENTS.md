# 项目规则（AGENTS.md）

本文件由 DSH 的 `agent-instructions` 机制发现并**始终注入上下文**（与 CLAUDE.md 一起，合计上限 64KB），对应 CodeBuddy 规则中 `alwaysApply: true` 的语义。来源：

- `C:\Users\19342\.codebuddy\rules\`（5 条）
- 本项目 `.codebuddy\rules\代码风格规范.mdc`（1 条）

以下规则在本项目（`D:\解包或汉化用\my-galtransl\my-GalTransl`）内始终生效，除非用户另行指示。

---

## 代码修改规则

修改代码前，先分析修改的风险、收益、可改进处，然后深度找修改可能带来的潜在bug，思考怎样避免bug、输出哪些日志及日志对应的层级。最后给出计划，经过我批准后才可以实际改动。

## 代码审查规则

审查代码从以下方面审查：
- 有没有基础的函数缺少参数、引用依赖缺失、口径不一致、配置无法读取、前后端传输数据传错接口和位置等基本问题；
- 有没有集成问题、状态残留问题、竞态等常见问题；
- 若有其他问题，一并指出。

## 命令行指令规则

程序项目目录是 `D:\解包或汉化用\my-galtransl\my-GalTransl`，有中文，需避免与中文路径冲突的命令行指令，尽量使用相对路径而非绝对路径。
`D:\解包或汉化用\xp3专用汉化文件夹\gal翻译\GalTransl_0.1.0_win.小粥3全量\小粥3-全量` 是测试用的翻译项目目录。

## 查找bug规则

- 已接入 mcp，可使用 mcp 工具辅助查找和复现 bug。
- 在翻译项目目录（`D:\解包或汉化用\xp3专用汉化文件夹\gal翻译\test-dev`）中有日志 `GalTransl.log`、`api_calls.log`、`frontend.log` 可以查看。
- 搜索和定位代码不要使用 powershell，使用其他工具。

## 荣耻（新八荣八耻）

- 以臆猜接口为耻，以查档求证为荣
- 以模糊开工为耻，以对齐需求为荣
- 以脑补业务为耻，以请示规则为荣
- 以新增冗余为耻，以复用存量为荣
- 以省略校验为耻，以完备测例为荣
- 以乱改架构为耻，以恪守规范为荣
- 以不懂装懂为耻，以坦诚存疑为荣
- 以批量乱改为耻，以分步迭代为荣

## my-GalTransl 代码风格规范

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
