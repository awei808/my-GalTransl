import asyncio
import httpx
import math
from datetime import timedelta
from opencc import OpenCC
from typing import Any, Dict, Optional, List, Tuple
from collections import deque
from threading import Lock
from contextvars import ContextVar
from GalTransl.COpenAI import COpenAITokenPool, COpenAIToken
from GalTransl.ConfigHelper import CProxyPool, build_httpx_proxy_kwargs
from GalTransl import LOGGER, LANG_SUPPORTED
from GalTransl.i18n import GT_LANG, get_text
from GalTransl.ConfigHelper import (
    CProjectConfig,
)
from GalTransl.Utils import load_guideline_file
from GalTransl.Backend.utils import coerce_bool, coerce_positive_int_strict
from GalTransl.TerminalOutput import should_print_translation_logs
from openai import RateLimitError, AsyncOpenAI, APIConnectionError, APITimeoutError
from openai import DefaultAioHttpClient
from openai._types import NOT_GIVEN
import json
import random
import time
from contextlib import suppress
from GalTransl.server_runtime import WORKER_ID_CTX, set_live_snippets, set_ttft_state
from GalTransl.ApiLogger import api_logger

try:
    from pyreqwest.compatibility.httpx import HttpxTransport
    from pyreqwest.client import ClientBuilder as PyreqwestClientBuilder
except Exception:
    HttpxTransport = None
    PyreqwestClientBuilder = None


def _default_http_limits() -> httpx.Limits:
    """有界连接池：30s 空闲过期自愈指向已重启网关的僵尸空闲连接。

    单客户端（单 token 单引擎实例）100 并发上限远高于实际 worker 数，属安全值。
    """
    return httpx.Limits(
        max_keepalive_connections=20, max_connections=100, keepalive_expiry=30.0
    )


# 注入块开关默认值（internals.promptBlocks）：全 True = 与 0.5.0 之前行为一致。
# 仅覆盖「可安全关闭」的内容块；[Input]/[SourceLang]/[TargetLang] 不参与开关。
PROMPT_BLOCK_DEFAULTS: Dict[str, bool] = {
    "translationGuideline": True,
    "glossary": True,
    "plotMetadata": True,
    "batchMetadata": True,
    "globalPrompt": True,
}


_GLOBAL_RPM_LOCK = Lock()
_GLOBAL_NEXT_ALLOWED_TS = 0.0


def _infer_provider(model_name: str) -> str:
    """按模型名自动推断服务商，用于 thinking 参数路由。"""
    n = (model_name or "").lower()
    if "qwen" in n:
        return "qwen"
    if "glm" in n:
        return "zhipu"
    if "gemini" in n:
        return "gemini"
    if "claude" in n:
        return "anthropic"
    if "grok" in n:
        return "grok"
    if "kimi" in n or "moonshot" in n:
        return "kimi"
    if "reasoner" in n or "deepseek" in n:
        return "deepseek"
    return "openai"

# 最近一次 LLM 请求的模型/流式标志：用 ContextVar 按 asyncio task 隔离，
# 避免多 worker 并发调用 ask_chatbot 时实例属性互相覆盖
_LAST_CHATBOT_MODEL_CTX: ContextVar = ContextVar("galtransl_last_chatbot_model", default="")
_LAST_CHATBOT_STREAM_CTX: ContextVar = ContextVar("galtransl_last_chatbot_stream", default=False)


class RequestHealthMetrics:
    def __init__(self) -> None:
        self._samples: deque[tuple[float, float, bool]] = deque()
        # 流式首字响应时间样本（秒），仅成功且流式请求记录
        self._ttft_samples: deque[tuple[float, float]] = deque()
        self._lock = Lock()

    def _trim_locked(self, now: float, window_seconds: float) -> None:
        cutoff = now - max(5.0, float(window_seconds))
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        while self._ttft_samples and self._ttft_samples[0][0] < cutoff:
            self._ttft_samples.popleft()

    def record(self, latency_seconds: float, is_rate_limited: bool, ttft_seconds: float | None = None) -> None:
        now = time.monotonic()
        latency = max(0.0, float(latency_seconds))
        with self._lock:
            self._samples.append((now, latency, bool(is_rate_limited)))
            if ttft_seconds is not None:
                self._ttft_samples.append((now, max(0.0, float(ttft_seconds))))
            self._trim_locked(now, 120.0)

    def snapshot(self, window_seconds: float = 30.0) -> dict:
        now = time.monotonic()
        with self._lock:
            self._trim_locked(now, window_seconds)
            total = len(self._samples)
            if total == 0:
                return {
                    "total": 0,
                    "rate_limited": 0,
                    "rate_limited_ratio": 0.0,
                    "avg_latency": 0.0,
                    "avg_ttft": 0.0,
                    "p95_ttft": 0.0,
                }
            rate_limited = sum(1 for _, _, limited in self._samples if limited)
            avg_latency = sum(lat for _, lat, _ in self._samples) / total
            if self._ttft_samples:
                ttf = sorted(f for _, f in self._ttft_samples)
                avg_ttft = sum(ttf) / len(ttf)
                idx = min(len(ttf) - 1, int(math.ceil(0.95 * len(ttf))) - 1)
                p95_ttft = ttf[max(0, idx)]
            else:
                avg_ttft = 0.0
                p95_ttft = 0.0
            return {
                "total": total,
                "rate_limited": rate_limited,
                "rate_limited_ratio": rate_limited / total,
                "avg_latency": avg_latency,
                "avg_ttft": avg_ttft,
                "p95_ttft": p95_ttft,
            }


# 引擎名 -> 后端模块路径。init_gptapi 依赖它惰性加载（importlib）目标模块：
# 模块加载时 @register_engine 装饰器运行，把「name -> 构造工厂」写入 ENGINE_REGISTRY。
ENGINE_MODULE_PATHS: dict[str, str] = {
    "ForGlobalPrompt": "GalTransl.Backend.ForGlobalPrompt",
    "ForGal-json-translate": "GalTransl.Backend.ForGalJsonTranslate",
    # 旧引擎名别名：兼容旧配置/旧任务，指向同一模块
    "ForGal-json-multi-chat": "GalTransl.Backend.ForGalJsonTranslate",
    "ForImproveTranslation": "GalTransl.Backend.ForImproveTranslation",
    "ForBRStation": "GalTransl.Backend.ForBRStation",
    "ForJPResidue": "GalTransl.Backend.ForJPResidue",
    "ForBanWordFix": "GalTransl.Backend.ForBanWordFix",
    "ForFixRound": "GalTransl.Backend.ForFixRound",
    "ForSemCheck": "GalTransl.Backend.ForSemCheck",
    "ForSemCheckAgain": "GalTransl.Backend.ForSemCheckAgain",
    "ForToneCheck": "GalTransl.Backend.ForToneCheck",
    "ForToneCheckAgain": "GalTransl.Backend.ForToneCheckAgain",
    "GenDic": "GalTransl.Backend.GenDic",
    "ForFileMetaData": "GalTransl.Backend.ForFileMetaData",
    "ForBatchMetaData": "GalTransl.Backend.ForBatchMetaData",
    "ForPlotRouteMap": "GalTransl.Backend.ForPlotRouteMap",
    "rebuildr": "GalTransl.Backend.RebuildTranslate",
    "rebuilda": "GalTransl.Backend.RebuildTranslate",
}

# 引擎注册表：eng_type 名称 -> 惰性构造工厂。
# 各后端子类用 @register_engine("名称") 装饰器登记；init_gptapi 从本表取工厂调用。
ENGINE_REGISTRY: dict[str, callable] = {}


def register_engine(name: str):
    """类装饰器：把后端类登记到引擎注册表（ENGINE_REGISTRY）。

    登记的值为惰性构造工厂：构造时才 import 所在模块并实例化类。
    name 必须已声明于 ENGINE_MODULE_PATHS，且类所在模块与之匹配，否则报错提示。

    Args:
        name: 引擎类型标识（eng_type），如 "ForGal-json-translate"。
    """
    def _deco(cls):
        _module = cls.__module__
        _cls_name = cls.__name__
        declared_module = ENGINE_MODULE_PATHS.get(name)
        if declared_module is None:
            raise ValueError(
                f"@register_engine({name!r}) 未在 ENGINE_MODULE_PATHS 中声明，"
                f"init_gptapi 将无法惰性加载该引擎，请在 BaseEngine.py 中补全映射"
            )
        if declared_module != _module:
            raise ValueError(
                f"@register_engine({name!r}) 模块不匹配：声明 {declared_module}，"
                f"实际 {_module}"
            )

        def _factory(config, eng_type, proxy_pool, token_pool):
            import importlib
            module = importlib.import_module(_module)
            impl = getattr(module, _cls_name)
            return impl(config, eng_type, proxy_pool, token_pool)

        ENGINE_REGISTRY[name] = _factory
        return cls
    return _deco


def _apply_change_prompt(config: CProjectConfig, prompt: str) -> str:
    """应用 common.gpt.change_prompt 对 user 提示词模板的修改。

    AdditionalPrompt：在模板头部拼接 "# Additional Requirements: <内容>"；
    OverwritePrompt：用 prompt_content 整体替换模板。两者仅当 prompt_content
    非空时生效（change_prompt 默认 "no" 直接返回原模板）。

    翻译轮在 init_chatbot 中调用本函数；修复轮在覆盖专用模板后经
    BaseFixRound._finalize_prompts 再次调用，使同一配置对修复轮同样生效。
    """
    common = CProjectConfig.getProjectConfig(config)["common"]
    change_prompt = common.get("gpt.change_prompt", "no")
    prompt_content = common.get("gpt.prompt_content", "")
    if change_prompt == "AdditionalPrompt" and prompt_content != "":
        prompt = "# Additional Requirements: " + prompt_content + "\n" + prompt
    if change_prompt == "OverwritePrompt" and prompt_content != "":
        prompt = prompt_content
    return prompt


class BaseEngine:
    """LLM API 客户端基类：所有翻译/元数据/字典后端共用的底层能力。

    负责 OpenAI 兼容客户端的构建（token 池/代理池/思考参数）、统一的重试退避、
    429 限速处理、全局 RPM 限制、API 调用日志、请求健康度统计，以及提示词占位符
    装配（_build_prompt_request）。不含任何翻译流水线逻辑。
    """

    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool] = None,
        token_pool: COpenAITokenPool = None,
    ) -> None:
        """
        Args:
            config: 项目配置对象。
            eng_type: 引擎类型标识。
            proxy_pool: 代理池对象，为 None 时不使用代理。
            token_pool: API Token 池，管理多个 API 密钥的轮换。
        """
        self.pj_config: CProjectConfig = config
        self.eng_type: str = eng_type
        self.last_file_name: str = ""
        # 翻译规范
        if val := config.getKey("gpt.translation_guideline"):
            guideline_file = val
        else:
            guideline_file = "Basic.md"
        self.pj_config.translation_guideline = load_guideline_file(guideline_file)

        # 保存间隔
        if val := config.getKey("save_steps"):
            self.save_steps = val
        else:
            self.save_steps = 1
        # 语言设置
        if val := config.getKey("language"):
            sp = val.split("2")
            self.source_lang = sp[0]
            self.target_lang = sp[-1]
        elif val := config.getKey("sourceLanguage"):  # 兼容旧版本配置
            self.source_lang = val
            self.target_lang = config.getKey("targetLanguage")
        else:
            self.source_lang = "ja"
            self.target_lang = "zh-cn"
        if self.source_lang not in LANG_SUPPORTED.keys():
            raise ValueError(
                get_text("invalid_source_language", GT_LANG, self.source_lang)
            )
        else:
            self.source_lang = LANG_SUPPORTED[self.source_lang]
        if self.target_lang not in LANG_SUPPORTED.keys():
            raise ValueError(
                get_text("invalid_target_language", GT_LANG, self.target_lang)
            )
        else:
            self.target_lang = LANG_SUPPORTED[self.target_lang]

        # 跳过h
        self.skipH = config.getKey("skipH", False)

        self.tokenProvider = token_pool

        metrics = getattr(config, "request_health_metrics", None)
        if metrics is None:
            metrics = RequestHealthMetrics()
            setattr(config, "request_health_metrics", metrics)
        self.request_health_metrics: RequestHealthMetrics = metrics

        backend_rpm = 0
        try:
            backend_rpm = int(
                config.getBackendConfigSection("OpenAI-Compatible").get(
                    "globalRequestRPM", 0
                )
                or 0
            )
        except Exception:
            backend_rpm = 0
        self.global_request_rpm = max(0, backend_rpm)

        if config.getKey("internals.enableProxy") == True:
            self.proxyProvider = proxy_pool
        else:
            self.proxyProvider = None

        self._shutdown_done = False
        # 客户端韧性：连续传输错误的客户端自动重建。失败计数以客户端对象本身为键
        # （对象被 _retired_clients 持引用，无 id 复用问题）。
        self._client_failure_counts: dict[AsyncOpenAI, int] = {}
        self._retired_clients: list[AsyncOpenAI] = []
        self._client_recycle_lock = asyncio.Lock()

        if self.target_lang == "Simplified_Chinese":
            self.opencc = OpenCC("t2s.json")
        elif self.target_lang == "Traditional_Chinese":
            self.opencc = OpenCC("s2tw.json")

    @staticmethod
    def _coerce_bool(value) -> bool:
        return coerce_bool(value, default=False)

    @staticmethod
    def _coerce_positive_int(value: Any, default: int) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError):
            result = default
        return max(1, result)

    @staticmethod
    def _coerce_optional_int(value: Any, default: int) -> int:
        # 请求次数上限：0 或负数表示不限制；非法值回退默认。
        try:
            result = int(value)
        except (TypeError, ValueError):
            return default
        return max(0, result)

    @staticmethod
    def _coerce_ratio(value: Any, default: float) -> float:
        # 错误率上限：[0.0, 1.0]，<=0 表示不限制；非法值回退默认。
        try:
            result = float(value)
        except (TypeError, ValueError):
            return default
        if result <= 0 or result > 1:
            return 0.0
        return result

    @staticmethod
    def _coerce_nonneg_float(value: Any, default: float) -> float:
        # 最小请求间隔(秒)：>=0，0 表示不限制；非法值回退默认。
        try:
            result = float(value)
        except (TypeError, ValueError):
            return default
        return max(0.0, result)

    @staticmethod
    def _coerce_error_wait(value: Any) -> float:
        # API 错误重试等待(秒)："auto"/空/非法 -> -1（指数退避），数字 -> float（固定退避，支持亚秒）。
        # bool 是 int 的子类，显式拒绝，避免 True/False 被当作 1/0 秒。
        if isinstance(value, bool):
            return -1.0
        if isinstance(value, (int, float)):
            result = float(value)
        else:
            raw_wait = str(value).strip().lower()
            if raw_wait == "auto" or raw_wait == "":
                return -1.0
            try:
                result = float(raw_wait)
            except (TypeError, ValueError):
                return -1.0
        # nan/inf 无意义，统一回退指数退避；负数视为 auto（与文档范围 0-120 对齐）
        if not math.isfinite(result) or result < 0:
            return -1.0
        return result

    def _apply_internal_prompt_template_overrides(self) -> None:
        """Apply runtime prompt-template overrides passed from backend service layer."""
        system_prompt_override = self.pj_config.getKey(
            "internals.prompt_template.system_prompt_override", None
        )
        user_prompt_override = self.pj_config.getKey(
            "internals.prompt_template.user_prompt_override", None
        )
        if isinstance(system_prompt_override, str):
            self.system_prompt = system_prompt_override
        if isinstance(user_prompt_override, str):
            self.trans_prompt = user_prompt_override

    def _setup_prompts(self, eng_type: str, config: CProjectConfig) -> None:
        """在 system_prompt / trans_prompt 赋值之后统一应用 override 并初始化 LLM 客户端。

        子类 __init__ 依次执行 super().__init__()、赋值 system_prompt/trans_prompt 后，
        调用本方法完成「应用用户模板 override → 初始化聊天客户端」两步。调用顺序即
        标准顺序：override 先于 init_chatbot，因此 init_chatbot 内部的 change_prompt
        逻辑会覆盖 override 结果（向后兼容）。

        注意：GenDic 刻意保持「先 init_chatbot 后 override」的原始顺序，不使用本方法。
        """
        self._apply_internal_prompt_template_overrides()
        self.init_chatbot(eng_type, config)

    def _effective_backend_section(
        self, section_name: str = "OpenAI-Compatible", config: Optional[CProjectConfig] = None
    ) -> dict:
        """解析本引擎生效的后端配置段。

        大阶段独立 API：token 池携带其来源 profile 的配置段（backend_section）时，
        实例级配置优先于项目全局 backendSpecific；未携带时行为与旧版一致。
        任务级参数（如 globalRequestRPM）仍读主配置，不经此方法覆盖。

        config 缺省时回退 self.pj_config（真实引擎均已由 __init__ 赋值）。
        """
        if section_name == "OpenAI-Compatible":
            token_provider = getattr(self, "tokenProvider", None)
            pool_section = getattr(token_provider, "backend_section", None)
            if isinstance(pool_section, dict):
                return pool_section
        cfg = config if config is not None else getattr(self, "pj_config", None)
        if cfg is None:
            raise AttributeError("无可用配置源（config 参数与 self.pj_config 均缺失）")
        return cfg.getBackendConfigSection(section_name)

    def init_chatbot(self, eng_type: str, config: CProjectConfig) -> None:
        # 废弃的 SakuraLLM 代码：eng_type 无 sakura 名称，SakuraLLM 分支实际不可达（Sakura 配置段已移除）。
        # 各后端在 config.inc.yaml 的 backendSpecific 下独立配置，互不影响。
        section_name = "SakuraLLM" if "sakura" in (eng_type or "").lower() else "OpenAI-Compatible"
        backend_cfg = self._effective_backend_section(section_name, config)

        # API 调用限制（后端级、可独立配置）：错误率上限 / 最小请求间隔 / 请求次数上限。
        # 默认值为 0 表示「不限制」，需用户在 config.inc.yaml 的 backendSpecific 段显式配置才启用，
        # 避免内置默认配置（无这三个字段）的用户被悄悄开启限流与终止。
        # 0 或不合法均表示不限制；非法值回退为 0。
        self.api_max_error_rate = self._coerce_ratio(backend_cfg.get("apiMaxErrorRate", 0), 0)
        self.api_min_interval_sec = self._coerce_nonneg_float(backend_cfg.get("apiMinIntervalSec", 0), 0)
        self.api_max_requests = self._coerce_optional_int(backend_cfg.get("apiMaxRequests", 0), 0)
        self._request_count = 0
        self._last_request_ts = 0.0
        self._total_requests = 0
        self._failed_requests = 0
        self._rate_lock = Lock()

        self.api_timeout = backend_cfg.get(
            "apiTimeout", 300
        )
        self.apiErrorWait = backend_cfg.get("apiErrorWait", "auto")
        # 规范化 apiErrorWait："auto"/非法值->-1（指数退避），数字->float（固定退避，支持亚秒）
        self.apiErrorWait = self._coerce_error_wait(self.apiErrorWait)
        self.tokenStrategy = backend_cfg.get("tokenStrategy", "random")
        self.stream = backend_cfg.get("stream", True)
        # 单次 LLM 调用的最大尝试预算（不含 429 限流重试）：默认 6，
        # 死端点不再无限重试，耗尽后由上层失败兜底（跳批/留待下次运行）
        self.max_api_retries = coerce_positive_int_strict(
            backend_cfg.get("maxApiRetries", 6), 6
        )
        # 思考相关配置（profile 级，缺省时零发送，向后兼容）
        self.provider = backend_cfg.get("provider", "auto")
        self.thinking_mode = backend_cfg.get("thinking_mode", "default")
        self.reasoning_effort = backend_cfg.get("reasoning_effort", "")
        self.extra_body_raw = backend_cfg.get("extra_body", "")

        self.trans_prompt = _apply_change_prompt(config, self.trans_prompt)

        if self.proxyProvider:
            proxy_addr = self.proxyProvider.getProxy().addr
        else:
            proxy_addr = None

        self.client_list = []
        for token in self.tokenProvider.get_available_token():
            http_client = self._build_http_client(proxy_addr)

            client = AsyncOpenAI(
                api_key=token.token,
                base_url=token.domain,
                max_retries=0,
                http_client=http_client,
            )
            self.client_list.append((client, token))
            # 只记脱敏 token 与域名，避免完整密钥进入日志
            LOGGER.debug(f"[api] 创建客户端 domain={token.domain} token={token.maskToken()}")

        LOGGER.info(
            f"[api] 后端初始化 eng_type={eng_type} provider={self.provider} "
            f"stream={self.stream} thinking_mode={self.thinking_mode} "
            f"reasoning_effort={self.reasoning_effort or '未设置'} "
            f"可用key数={len(self.client_list)}"
        )

    @staticmethod
    def _build_http_client(proxy_addr: Optional[str]):
        """构建单个 token 使用的 HTTP 客户端（无代理走 pyreqwest，有代理回退 httpx）。"""
        trust_env = False  # 不使用系统代理
        proxy_kwargs = build_httpx_proxy_kwargs(proxy_addr)
        if HttpxTransport is not None and not proxy_kwargs:
            try:
                pyreqwest_client = None
                if PyreqwestClientBuilder is not None:
                    pyreqwest_client = (
                        PyreqwestClientBuilder()
                        .pool_idle_timeout(timedelta(seconds=30))
                        .pool_max_idle_per_host(20)
                        .build()
                    )
                return httpx.AsyncClient(
                    trust_env=trust_env,
                    limits=_default_http_limits(),
                    transport=HttpxTransport(pyreqwest_client)
                    if pyreqwest_client is not None
                    else HttpxTransport(),
                )
            except Exception as e:
                LOGGER.warning(
                    f"初始化 pyreqwest HttpxTransport 失败，回退 DefaultAioHttpClient: {e}"
                )
        elif HttpxTransport is not None and proxy_kwargs:
            LOGGER.warning(
                "检测到代理配置，当前回退到 DefaultAioHttpClient（pyreqwest transport 路径未启用代理注入）"
            )
        return DefaultAioHttpClient(
            trust_env=trust_env,
            limits=_default_http_limits(),
            **proxy_kwargs,
        )

    @staticmethod
    def _is_transport_error(error: BaseException) -> bool:
        """判断是否为连接层错误（可安全通过重建客户端恢复）。

        5xx/429/认证等 API 状态错误不在此列，避免无意义的客户端重建。
        """
        return isinstance(
            error,
            (
                APIConnectionError,
                APITimeoutError,
                httpx.TransportError,
                TimeoutError,
                ConnectionError,
                OSError,
            ),
        )

    async def _recycle_failed_client(
        self, failed_client: AsyncOpenAI, token: COpenAIToken
    ) -> Optional[AsyncOpenAI]:
        """连续传输错误后原地替换单个不健康的 API 客户端（全新连接池），不打断其他 worker。

        替换出的旧客户端移入 _retired_clients 持引用：既防其他 worker 仍在其上的
        在途请求被误关，也顺带保证其内存地址不被复用。关闭统一延迟到 shutdown()。
        """
        if getattr(self, "_shutdown_done", False):
            return None
        async with self._client_recycle_lock:
            # 锁内复查：shutdown 可能在外层检查后、加锁前完成置位并关闭全部
            # 客户端（其快照不含新建客户端），此时重建将泄漏到进程退出
            if getattr(self, "_shutdown_done", False):
                return None
            current = next(
                (
                    pair
                    for pair in self.client_list
                    if pair[0] is failed_client and pair[1] is token
                ),
                None,
            )
            if current is None:
                return None

            proxy_addr = None
            if self.proxyProvider:
                try:
                    proxy_addr = self.proxyProvider.getProxy().addr
                except Exception:
                    proxy_addr = None

            replacement = AsyncOpenAI(
                api_key=token.token,
                base_url=token.domain,
                max_retries=0,
                http_client=self._build_http_client(proxy_addr),
            )
            self.client_list = [
                (replacement if client is failed_client else client, pair_token)
                for client, pair_token in self.client_list
            ]
            self._retired_clients.append(failed_client)
            LOGGER.warning(f"连续网络错误，已刷新 API 客户端 [{token.maskToken()}]")
            return replacement

    @staticmethod
    def _is_stop_requested(pj_config: CProjectConfig) -> bool:
        stop_event = getattr(pj_config, "stop_event", None)
        return stop_event is not None and stop_event.is_set()

    def _check_stop_requested(self) -> None:
        if self._is_stop_requested(self.pj_config):
            from GalTransl.Service import JobCancelledError

            raise JobCancelledError()

    async def _call_llm_with_error_report(
        self,
        messages: list,
        filename: str,
        max_retry_count: int = 3,
        tag: str = "",
        kind: str = "llm",
    ) -> tuple:
        """单次 LLM 调用 + 统一运行态错误上报（元数据类引擎共用）。

        成功返回 ``(rsp, token)``；失败时记录 LOGGER.error（含 exc_info）并上报
        运行态（kind=llm，供工作台"最近错误"卡片），返回 ``(None, None)``，
        由调用方按空响应处理（如 _parse_meta(rsp or "") 返回 None 后走失败分支）。

        注意：JobCancelledError 不被本方法截获，会沿调用栈上抛（与翻译轮一致），
        保证取消任务不被误记成 LLM 调用失败。
        """
        try:
            return await self.ask_chatbot(
                messages=messages,
                file_name=filename,
                max_retry_count=max_retry_count,
            )
        except Exception as e:
            from GalTransl.Service import JobCancelledError

            if isinstance(e, JobCancelledError):
                raise
            LOGGER.error(
                f"[{tag}] {filename} LLM 请求失败：{type(e).__name__}: {e}",
                exc_info=True,
            )
            self._record_runtime_error(
                kind=kind,
                message=f"{type(e).__name__}: {e}",
                filename=filename,
                index_range="-",
                level="error",
            )
            return None, None

    def _build_guideline_block(self) -> str:
        """按 _inject_guideline 开关构建翻译规范注入块（带标题；关闭或为空时返回空串）。

        仅文件级/批次级/全局分析三个元数据类后端使用（ForFileMetaData /
        ForBatchMetaData / ForGlobalPrompt），对应配置键按引擎命名空间隔离：
        internals.forfilemeta.inject_guideline / internals.forbatchmeta.inject_guideline /
        internals.forglobalprompt.inject_guideline。该开关**不作用于翻译轮与修复轮**
        （ForGalJsonTranslate、ForImproveTranslation、ForBRStation、ForJPResidue、
        ForBanWordFix），它们由 _build_prompt_request 默认裸替换
        pj_config.translation_guideline（无条件注入，无此开关）。
        """
        if getattr(self, "_inject_guideline", True):
            guideline = getattr(self.pj_config, "translation_guideline", "") or ""
        else:
            guideline = ""
        guideline = (guideline or "").strip()
        if guideline:
            return f"# 翻译规范\n{guideline}\n"
        return ""

    def _build_prompt_request(
        self,
        input_src: str,
        gptdict: str,
        plot_metadata: str = "",
        batch_metadata: str = "",
        translation_guideline: Optional[str] = None,
        global_prompt: str = "",
    ) -> str:
        """按统一占位符口径装配 user 提示词。

        Args:
            input_src: 待处理输入文本（替换 [Input]）。
            gptdict: 术语表（替换 [Glossary]）。
            plot_metadata: 文件级元数据块（替换 [plot_metadata]，默认空串清除占位符）。
            batch_metadata: 批次级元数据块（替换 [batch_metadata]，默认空串清除占位符）。
            translation_guideline: 翻译规范块；None 时使用
                ``pj_config.translation_guideline``（裸替换，翻译轮/修复轮默认行为）。
                子类（文件级/批次级/全局分析）可传入带标题或为空的规整块以覆盖。
            global_prompt: 全局提示词块（替换 [global_prompt]，默认空串清除占位符）。

        Returns:
            占位符替换后的提示词文本。
        """
        prompt_req = self.trans_prompt
        if translation_guideline is None:
            guideline = self.pj_config.translation_guideline
        else:
            guideline = translation_guideline

        # 注入块清单（0.5.0）：占位符 -> (值, 开关键)。开关关闭时按空串替换，
        # 占位符仍被清除（等价于该块「不注入」），模板结构不被破坏。
        blocks = self._prompt_block_values(
            input_src=input_src,
            gptdict=gptdict,
            plot_metadata=plot_metadata,
            batch_metadata=batch_metadata,
            guideline=guideline,
            global_prompt=global_prompt,
        )
        for placeholder, value in blocks:
            prompt_req = prompt_req.replace(placeholder, value)
        return prompt_req

    def _prompt_block_values(
        self,
        input_src: str,
        gptdict: str,
        plot_metadata: str,
        batch_metadata: str,
        guideline: str,
        global_prompt: str,
    ) -> List[Tuple[str, str]]:
        """返回 (占位符, 替换值) 清单，按块开关决定各块是否为空串。

        开关读取自 ``internals.promptBlocks.<name>``，**默认全部为 True**，
        故未配置任何开关时行为与 0.5.0 之前完全一致（零回归）。

        `[Input]` / `[SourceLang]` / `[TargetLang]` 为功能性占位符，不参与
        开关（关闭会让提示词失去待译内容或语言约束），恒按原值替换。
        """
        # 块名 -> (占位符, 值)；顺序即替换顺序，不影响结果（各占位符互不包含）
        toggles = self._prompt_block_toggles()
        return [
            ("[translation_guideline]", guideline if toggles["translationGuideline"] else ""),
            ("[Glossary]", gptdict if toggles["glossary"] else ""),
            ("[plot_metadata]", plot_metadata if toggles["plotMetadata"] else ""),
            ("[batch_metadata]", batch_metadata if toggles["batchMetadata"] else ""),
            ("[global_prompt]", global_prompt if toggles["globalPrompt"] else ""),
            ("[Input]", input_src),
            ("[SourceLang]", self.source_lang),
            ("[TargetLang]", self.target_lang),
        ]

    def _prompt_block_toggles(self) -> Dict[str, bool]:
        """读取注入块开关（``internals.promptBlocks`` 段）；缺省全部 True。

        每引擎实例构造时缓存一次，避免每轮提示词拼装都走配置查找。
        配置对象可能是不带 ``getKey`` 的 duck-typed 桩（测试/嵌入场景），
        此时一律取默认值（全开），等价于 0.5.0 之前的行为。
        """
        cached = getattr(self, "_prompt_block_toggles_cache", None)
        if cached is not None:
            return cached
        get_key = getattr(self.pj_config, "getKey", None)
        toggles: Dict[str, bool] = {}
        for name, default in PROMPT_BLOCK_DEFAULTS.items():
            if get_key is None:
                toggles[name] = default
                continue
            raw = get_key(f"internals.promptBlocks.{name}", default)
            toggles[name] = coerce_bool(raw, default=default)
        self._prompt_block_toggles_cache = toggles
        return toggles

    async def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep that can be interrupted by stop_event.

        Instead of blocking for the full duration, we check every 0.5s
        so that a stop request is honoured promptly.
        """
        remaining = seconds
        while remaining > 0:
            if self._is_stop_requested(self.pj_config):
                from GalTransl.Service import JobCancelledError
                raise JobCancelledError()
            chunk = min(remaining, 0.5)
            await asyncio.sleep(chunk)
            remaining -= chunk

    async def _wait_for_global_rpm_slot(self) -> None:
        if self.global_request_rpm <= 0:
            return

        global _GLOBAL_NEXT_ALLOWED_TS
        interval = 60.0 / float(self.global_request_rpm)
        wait_seconds = 0.0

        with _GLOBAL_RPM_LOCK:
            now = time.monotonic()
            if now >= _GLOBAL_NEXT_ALLOWED_TS:
                _GLOBAL_NEXT_ALLOWED_TS = now + interval
                wait_seconds = 0.0
            else:
                wait_seconds = _GLOBAL_NEXT_ALLOWED_TS - now
                _GLOBAL_NEXT_ALLOWED_TS = _GLOBAL_NEXT_ALLOWED_TS + interval

        if wait_seconds > 0:
            LOGGER.debug(
                f"[并发] rpm 限速 {self.global_request_rpm}/min，等待 {wait_seconds:.2f}s"
            )
            if wait_seconds > 5:
                LOGGER.info(
                    f"[并发] rpm 限速等待较长：{wait_seconds:.1f}s（上限 {self.global_request_rpm}/min）"
                )
            await self._interruptible_sleep(wait_seconds)

    def _record_request_health(
        self, latency_seconds: float, is_rate_limited: bool, ttft_seconds: float | None = None
    ) -> None:
        try:
            self.request_health_metrics.record(latency_seconds, is_rate_limited, ttft_seconds=ttft_seconds)
        except Exception:
            return

    @property
    def runtime_project_dir(self) -> str:
        """当前任务的项目根目录（服务端模式下优先取 runtime_project_dir）。"""
        return getattr(
            self.pj_config, "runtime_project_dir", None
        ) or self.pj_config.getProjectDir()

    def _record_runtime_error(
        self,
        kind: str,
        message: str,
        filename: str = "",
        index_range: str = "",
        model: Optional[str] = None,
        level: str = "error",
        retry_count: Optional[int] = None,
        sleep_seconds: Optional[float] = None,
    ) -> None:
        """统一运行态错误上报（工作台"最近错误"卡片），失败静默。"""
        try:
            from GalTransl.server import record_runtime_error

            record_runtime_error(
                self.runtime_project_dir,
                kind=kind,
                message=message,
                filename=filename,
                index_range=index_range,
                model=model or self.get_last_chatbot_model(),
                level=level,
                retry_count=retry_count,
                sleep_seconds=sleep_seconds,
            )
        except Exception:
            pass

    def _record_runtime_success(
        self,
        filename: str,
        *,
        index: int = 0,
        speaker: Optional[str] = None,
        source_preview: str = "",
        translation_preview: str = "",
        trans_by: str = "",
    ) -> None:
        """统一运行态成功上报（工作台"最近成功"卡片），失败静默。"""
        try:
            from GalTransl.server import record_runtime_success

            record_runtime_success(
                self.runtime_project_dir,
                filename=filename,
                index=index,
                speaker=speaker,
                source_preview=source_preview,
                translation_preview=translation_preview,
                trans_by=trans_by,
            )
        except Exception:
            pass

    def get_last_chatbot_model(self) -> str:
        """返回当前 task 最近一次 LLM 请求使用的模型名（多 worker 场景下按 task 隔离）。"""
        return _LAST_CHATBOT_MODEL_CTX.get()

    def get_last_chatbot_stream(self) -> bool:
        """返回当前 task 最近一次 LLM 请求是否为流式（多 worker 场景下按 task 隔离）。"""
        return _LAST_CHATBOT_STREAM_CTX.get()

    def _build_thinking_params(
        self, model_name: str
    ) -> tuple[dict[str, Any], Any, bool]:
        """按服务商生成思考参数，返回 (extra_body, reasoning_effort, thinking_on)。

        未配置任何思考参数（thinking_mode=default 且 effort/budget/extra_body 均空）时
        返回空体，请求体与旧版完全一致，保证向后兼容。
        """
        provider = getattr(self, "provider", "auto") or "auto"
        mode = getattr(self, "thinking_mode", "default") or "default"
        effort = (getattr(self, "reasoning_effort", "") or "").strip().lower()
        extra_raw = getattr(self, "extra_body_raw", "") or ""

        if provider == "auto":
            provider = _infer_provider(model_name)

        extra: dict[str, Any] = {}
        if extra_raw.strip():
            try:
                parsed = json.loads(extra_raw)
                if isinstance(parsed, dict):
                    extra.update(parsed)
            except Exception:
                LOGGER.warning(f"[thinking] 解析 extra_body JSON 失败，已忽略：{extra_raw}")

        eff: Any = NOT_GIVEN

        if mode == "off":
            # 显式关闭思考：仅支持关闭的平台发送关闭参数；
            # openai/grok 思考由模型名控制或不可关闭，不发送
            if provider == "deepseek":
                # DeepSeek 官方：thinking: {type: disabled}（经 extra_body）
                extra["thinking"] = {"type": "disabled"}
            elif provider == "qwen":
                # 阿里百炼官方：enable_thinking=false 关闭混合思考
                extra["enable_thinking"] = False
            elif provider == "zhipu":
                # 智谱官方：thinking: {type: disabled}
                extra["thinking"] = {"type": "disabled"}
            elif provider == "kimi":
                # Kimi 官方：kimi-k2.5/k2.6 支持 thinking.type=disabled；
                # k3/k2.7-code 不可关闭，交由平台返回或忽略
                extra["thinking"] = {"type": "disabled"}
            elif provider == "gemini":
                eff = "none"
            # anthropic：新版接口无统一关闭参数，交由模型默认（不发送）
        elif mode == "on":
            if provider == "deepseek":
                # DeepSeek 官方：thinking: {type: enabled}（经 extra_body）
                extra["thinking"] = {"type": "enabled"}
            elif provider == "qwen":
                # 阿里百炼官方：enable_thinking=true（qwen3 混合思考）
                extra["enable_thinking"] = True
            elif provider == "zhipu":
                extra["thinking"] = {"type": "enabled"}
            elif provider == "kimi":
                extra["thinking"] = {"type": "enabled"}
            elif provider == "anthropic":
                # Anthropic 官方（Opus 4.6+）：output_config.effort 控制思考深度
                extra["output_config"] = {"effort": effort if effort else "high"}
            # openai/grok/gemini：思考由模型名控制或默认开启，不发送开关参数

        # 思考强度：按平台与模型精确路由 reasoning_effort
        if effort:
            if provider == "grok" and "grok-4" in (model_name or "").lower():
                LOGGER.warning("[thinking] grok-4 不支持 reasoning_effort，已忽略")
            elif provider == "grok" and effort not in ("low", "high"):
                LOGGER.warning("[thinking] Grok 的 reasoning_effort 仅支持 low/high，已忽略")
            elif provider == "openai" and effort == "max":
                LOGGER.warning("[thinking] OpenAI 不支持 reasoning_effort=max，已忽略")
            elif provider == "gemini" and effort == "max":
                LOGGER.warning("[thinking] Gemini 不支持 reasoning_effort=max，已忽略")
            elif provider == "kimi" and "k3" not in (model_name or "").lower():
                LOGGER.warning("[thinking] 仅 kimi-k3 支持 reasoning_effort，已忽略")
            elif provider in ("openai", "gemini", "grok", "deepseek", "kimi"):
                eff = effort

        # 开启思考时禁用与推理冲突的参数（Grok/Kimi/DeepSeek 思考模式限制）
        thinking_on = mode == "on"
        return extra, eff, thinking_on

    def _check_request_count_quota(self) -> None:
        # 累计 API 请求次数达到上限则终止整个翻译流程（api_max_requests<=0 不限制）。
        if self.api_max_requests <= 0:
            return
        # 并发计数需加锁，避免多协程同时自增导致阈值判断不准确。
        with self._rate_lock:
            self._request_count += 1
            count = self._request_count
        if count > self.api_max_requests:
            from GalTransl.Service import JobCancelledError
            LOGGER.error(
                f"[{self.eng_type}] API 请求次数已达上限 {self.api_max_requests}，终止翻译流程"
            )
            raise JobCancelledError()

    async def _throttle_request_rate(self) -> None:
        # 两次 API 请求之间的最小间隔节流（api_min_interval_sec<=0 不限制）。
        interval = self.api_min_interval_sec
        if interval <= 0:
            return
        now = time.monotonic()
        with self._rate_lock:
            wait = max(0.0, interval - (now - self._last_request_ts))
        if wait > 0:
            LOGGER.debug(f"[{self.eng_type}] API 请求节流，等待 {wait:.2f}s")
            await asyncio.sleep(wait)
        with self._rate_lock:
            self._last_request_ts = time.monotonic()

    # 错误率统计的最小样本量：样本过小（如仅 1~2 次请求）时错误率无统计意义，
    # 偶发网络抖动会导致早期误杀整个任务，故低于门槛时不判断。
    MIN_ERROR_RATE_SAMPLES = 20

    def _check_error_rate_quota(self) -> None:
        # 累计错误率（失败请求/总请求）超过上限则终止整个翻译流程（api_max_error_rate<=0 不限制）。
        # 与 RequestHealthMetrics 的限流统计(rate_limited)解耦：此处统计「API 调用抛异常」的失败（不含 429 限流）。
        if self.api_max_error_rate <= 0:
            return
        with self._rate_lock:
            total = self._total_requests
            failed = self._failed_requests
        if total < self.MIN_ERROR_RATE_SAMPLES:
            return
        error_rate = failed / total
        if error_rate >= self.api_max_error_rate:
            from GalTransl.Service import JobCancelledError
            LOGGER.error(
                f"[{self.eng_type}] API 错误率 {error_rate:.1%} 已超过上限 "
                f"{self.api_max_error_rate:.1%}（失败 {failed}/{total}），终止翻译流程"
            )
            raise JobCancelledError()

    async def ask_chatbot(
        self,
        prompt: str = "",
        system: str = "",
        messages: Optional[list[dict]] = None,
        temperature: Any = NOT_GIVEN,
        frequency_penalty: Any = NOT_GIVEN,
        top_p: Any = NOT_GIVEN,
        stream: Any = NOT_GIVEN,
        max_tokens: Any = NOT_GIVEN,
        reasoning_effort: Any = NOT_GIVEN,
        file_name: str = "",
        base_try_count: int = 0,
        stream_line_callback: Optional[Any] = None,
        max_retry_count: Optional[int] = None,
    ) -> tuple[str, COpenAIToken]:
        # 未显式传 max_retry_count 时使用实例默认预算 maxApiRetries（默认 6）
        if max_retry_count is None:
            max_retry_count = getattr(self, "max_api_retries", None)
        if max_retry_count is not None:
            max_retry_count = coerce_positive_int_strict(max_retry_count, 6)
        # api_try_count 驱动 token 轮换与退避指数（每次失败都递增）；
        # api_attempts 是尝试预算（429 限流不计入），两者独立计数
        api_try_count = base_try_count
        api_attempts = 0
        # 每次调用先复位请求状态：在首次尝试置位前就失败的调用（如取消、
        # tokenStrategy 非法），运行态上报不会再误挂上一个请求的模型名
        _LAST_CHATBOT_MODEL_CTX.set("")
        _LAST_CHATBOT_STREAM_CTX.set(False)
        client: AsyncOpenAI
        token: COpenAIToken
        client, token = random.choices(self.client_list, k=1)[0]
        if messages is None:
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]

        # 实时推送「当前提示词」预览，使翻译控制台的提示词面板
        # 在执行任何后端时都能显示（不再局限于多轮对话后端）。
        # 取最后一条 user 消息内容作为“当前提示词”；多轮对话后端已在
        # ForGalJsonTranslate 中显式推送同一内容，此处为其他后端补齐。
        try:
            _runtime_dir = getattr(self.pj_config, "runtime_project_dir", None) or getattr(
                self.pj_config, "getProjectDir", lambda: ""
            )()
            _user_content = ""
            if messages:
                for _m in reversed(messages):
                    if _m.get("role") == "user":
                        _user_content = _m.get("content") or ""
                        break
            else:
                _user_content = prompt
            if _runtime_dir and _user_content:
                set_live_snippets(_runtime_dir, prompt_preview=_user_content, filename=file_name)
        except Exception:
            # 预览仅用于 UI 展示，任何异常都不应影响翻译主流程
            pass

        if "gemini" in token.model_name:
            temperature = NOT_GIVEN

        while True:
            # Check stop_event before each API attempt to make cancellation work during retry backoffs
            if self._is_stop_requested(self.pj_config):
                from GalTransl.Service import JobCancelledError
                raise JobCancelledError()

            request_started = time.monotonic()
            # except 路径引用这两个变量（tokenStrategy 非法等在赋值前抛出时不可未绑定）
            _call_trace = ""
            _pj_dir = ""
            try:
                if self.tokenStrategy == "random":
                    if api_try_count % 2 == 0:
                        client, token = random.choices(self.client_list, k=1)[0]
                elif self.tokenStrategy == "fallback":
                    index = api_try_count % len(self.client_list)
                    client, token = self.client_list[index]
                else:
                    raise ValueError("tokenStrategy must be random or fallback")
                is_stream=stream if stream != NOT_GIVEN else token.stream
                _LAST_CHATBOT_STREAM_CTX.set(bool(is_stream))
                _LAST_CHATBOT_MODEL_CTX.set(getattr(token, "model_name", ""))
                LOGGER.debug(f"Call {token.domain} withs token {token.maskToken()}")

                # ── API 调用日志：记录请求信息 ──
                try:
                    _pj_dir = getattr(self.pj_config, "runtime_project_dir",
                                      self.pj_config.getProjectDir())
                except Exception:
                    _pj_dir = ""
                if _pj_dir:
                    _prompt_snip = ""
                    try:
                        _prompt_snip = str(messages[-1]["content"]) if messages else str(prompt)
                    except Exception:
                        pass
                    _call_trace = api_logger.begin(
                        _pj_dir,
                        backend=self.eng_type,
                        file=file_name,
                        model=token.model_name,
                        endpoint=token.domain,
                        stream=bool(is_stream),
                        prompt_preview=_prompt_snip,
                    )

                await self._wait_for_global_rpm_slot()

                # 后端级调用限制：先检查次数上限，再做最小间隔节流。
                self._check_request_count_quota()
                await self._throttle_request_rate()

                # Create the API call as a task so we can cancel it if
                # the user requests a stop while the request is in-flight.
                LOGGER.info(f"timeout: {self.api_timeout}")
                # 按服务商生成思考参数（未配置时返回空体，向后兼容）
                extra_body, thinking_effort, thinking_on = self._build_thinking_params(
                    token.model_name
                )
                eff = reasoning_effort if reasoning_effort != NOT_GIVEN else thinking_effort
                api_task = asyncio.ensure_future(
                    client.chat.completions.create(
                        model=token.model_name,
                        messages=messages,
                        stream=is_stream,
                        # 思考模式下禁用与推理冲突的参数（Grok/Kimi/DeepSeek 限制）
                        temperature=NOT_GIVEN if thinking_on else temperature,
                        frequency_penalty=NOT_GIVEN if thinking_on else frequency_penalty,
                        top_p=NOT_GIVEN if thinking_on else top_p,
                        max_tokens=max_tokens,
                        timeout=self.api_timeout,
                        reasoning_effort=eff,
                        extra_body=extra_body if extra_body else None,
                    )
                )

                # 流式首字状态灯：请求已发起，等待首个正文分片
                try:
                    if _pj_dir and is_stream:
                        set_ttft_state(
                            _pj_dir, "WAITING", worker_id=WORKER_ID_CTX.get(),
                            model=token.model_name,
                        )
                except Exception:
                    pass

                # Poll stop_event while waiting; detect stop within 0.5s even when endpoint is slow
                while not api_task.done():
                    if self._is_stop_requested(self.pj_config):
                        api_task.cancel()
                        with suppress(BaseException):
                            try:
                                await asyncio.wait_for(
                                    asyncio.shield(api_task), timeout=2.0
                                )
                            except (asyncio.TimeoutError, asyncio.CancelledError):
                                pass
                        from GalTransl.Service import JobCancelledError
                        raise JobCancelledError()
                    done, _ = await asyncio.wait({api_task}, timeout=0.5)
                    if done:
                        break

                response = api_task.result()
                result = ""
                lastline = ""
                reasoning_result = ""
                _ttft_ms: float | None = None  # 流式首字响应时间（毫秒），未收到为 None
                if is_stream:
                    stream_abort_requested = False
                    stream_line_buffer = ""
                    stream_completed = False
                    try:
                        async for chunk in response:
                            # Check stop in the middle of streaming so we don't
                            # have to wait for the entire stream to finish.
                            if self._is_stop_requested(self.pj_config):
                                stream_abort_requested = True
                                from GalTransl.Service import JobCancelledError
                                raise JobCancelledError()
                            if not chunk.choices:
                                continue
                            if hasattr(chunk.choices[0].delta, "reasoning_content"):
                                _reasoning_piece = (
                                    chunk.choices[0].delta.reasoning_content or ""
                                )
                                reasoning_result = reasoning_result + _reasoning_piece
                                lastline = lastline + _reasoning_piece
                            if hasattr(chunk.choices[0].delta, "content"):
                                content_piece = chunk.choices[0].delta.content or ""
                                # 首个非空正文分片到达：记录 TTFT 并点亮首字灯
                                if content_piece and _ttft_ms is None:
                                    _ttft_ms = (time.monotonic() - request_started) * 1000
                                    try:
                                        if _pj_dir:
                                            set_ttft_state(
                                                _pj_dir, "FIRST_TOKEN",
                                                worker_id=WORKER_ID_CTX.get(),
                                                ttft_ms=_ttft_ms,
                                                model=token.model_name,
                                            )
                                    except Exception:
                                        pass
                                result = result + content_piece
                                lastline = lastline + content_piece
                                stream_line_buffer += content_piece
                                if stream_line_callback and "\n" in stream_line_buffer:
                                    line_parts = stream_line_buffer.split("\n")
                                    finished_lines = line_parts[:-1]
                                    stream_line_buffer = line_parts[-1]
                                    try:
                                        callback_result = stream_line_callback(
                                            finished_lines, False
                                        )
                                        if callback_result is False:
                                            stream_abort_requested = True
                                            break
                                    except Exception:
                                        pass
                            if "\n" in lastline:
                                if should_print_translation_logs(self.pj_config) and self.pj_config.active_workers == 1:
                                    lastline_sp = lastline.split("\n")
                                    print("\n".join(lastline_sp[:-1]))
                                    lastline = lastline_sp[-1]
                        stream_completed = True
                        if stream_line_callback and stream_line_buffer:
                            try:
                                callback_result = stream_line_callback(
                                    [stream_line_buffer], True
                                )
                                if callback_result is False:
                                    stream_abort_requested = True
                            except Exception:
                                pass
                    finally:
                        if not stream_completed or stream_abort_requested:
                            close_stream = getattr(response, "aclose", None)
                            if callable(close_stream):
                                try:
                                    await asyncio.wait_for(close_stream(), timeout=3.0)
                                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                                    pass
                else:
                    try:
                        _msg = response.choices[0].message
                        result = _msg.content
                        reasoning_result = getattr(
                            _msg, "reasoning_content", None
                        ) or ""
                    except Exception:
                        raise ValueError(
                            "response.choices[0].message.content is None, no_candidates"
                        )
                    if not isinstance(result, str) or result.strip() == "":
                        raise ValueError(
                            "response.choices[0].message.content is empty"
                        )
                self._record_request_health(
                    time.monotonic() - request_started,
                    is_rate_limited=False,
                    ttft_seconds=(_ttft_ms / 1000.0) if _ttft_ms is not None else None,
                )
                # 先累加总请求数，再判断错误率（与失败路径口径一致）
                with self._rate_lock:
                    self._total_requests += 1
                # 错误率超阈值时终止整个翻译流程
                self._check_error_rate_quota()
                # ── API 调用日志：成功 ──
                if _call_trace:
                    _lat = (time.monotonic() - request_started) * 1000
                    _pt, _ct = 0, 0
                    try:
                        _usage = getattr(response, "usage", None)
                        if _usage:
                            _pt = getattr(_usage, "prompt_tokens", 0) or 0
                            _ct = getattr(_usage, "completion_tokens", 0) or 0
                    except Exception:
                        pass
                    api_logger.record(
                        _call_trace, status="success", latency_ms=_lat,
                        retry_count=api_attempts, prompt_tokens=_pt,
                        completion_tokens=_ct,
                        response_preview=result or "",
                        reasoning=reasoning_result,
                        ttft_ms=_ttft_ms,
                    )
                # 请求成功返回：该 worker 当前无进行中请求，状态灯复位 IDLE（ttft_ms 清 None）
                try:
                    if _pj_dir:
                        set_ttft_state(
                            _pj_dir, "IDLE", worker_id=WORKER_ID_CTX.get(),
                        )
                except Exception:
                    pass
                failure_counts = getattr(self, "_client_failure_counts", None)
                if failure_counts is not None:
                    failure_counts.pop(client, None)
                return result, token
            except Exception as e:
                # 配额终止信号（次数/错误率超限）直接穿透，避免被当作失败重复记账。
                # 该信号来自 try 内的 _check_request_count_quota/_check_error_rate_quota，
                # 冒泡到此处被捕获为 e，需立即重抛，不计入错误率统计。
                from GalTransl.Service import JobCancelledError
                if isinstance(e, JobCancelledError):
                    if _call_trace:
                        _lat = (time.monotonic() - request_started) * 1000
                        api_logger.record(
                            _call_trace, status="cancelled", latency_ms=_lat,
                            retry_count=api_attempts, error=str(e),
                        )
                    try:
                        if _pj_dir:
                            set_ttft_state(
                                _pj_dir, "CANCELLED", worker_id=WORKER_ID_CTX.get(),
                            )
                    except Exception:
                        pass
                    raise
                is_rate_limited = isinstance(e, RateLimitError)
                self._record_request_health(
                    time.monotonic() - request_started,
                    is_rate_limited=is_rate_limited,
                )
                with self._rate_lock:
                    self._total_requests += 1
                    # 429 限流(is_rate_limited)属于正常速率约束，不计入错误率失败统计
                    if not is_rate_limited:
                        self._failed_requests += 1
                # 错误率超阈值时终止整个翻译流程
                self._check_error_rate_quota()

                # 连续传输错误计数：达到阈值重建该客户端（全新连接池）后再重试；
                # 放在熔断检查之后，任务被熔断终止时不必白建客户端。
                if BaseEngine._is_transport_error(e):
                    failure_counts = getattr(self, "_client_failure_counts", None)
                    if failure_counts is not None:
                        failure_counts[client] = failure_counts.get(client, 0) + 1
                        if failure_counts[client] >= 3:
                            # 先记住失败客户端：回收成功后 client 变量会指向新客户端
                            failed_client = client
                            try:
                                replacement = await self._recycle_failed_client(client, token)
                                if replacement is not None:
                                    client = replacement
                            except Exception:
                                LOGGER.debug("刷新失败的 API 客户端时出错", exc_info=True)
                            failure_counts.pop(failed_client, None)

                # 流式首字状态灯：限流/异常触发重试，状态复位回 WAITING（新请求重新计时）
                try:
                    if _pj_dir and is_stream:
                        set_ttft_state(
                            _pj_dir, "RETRYING", worker_id=WORKER_ID_CTX.get(),
                            model=token.model_name,
                        )
                except Exception:
                    pass

                api_try_count += 1
                if not is_rate_limited:
                    # 429 限流是暂时性速率约束，不消耗尝试预算，仅退避后重试
                    api_attempts += 1
                    if max_retry_count is not None and api_attempts >= max_retry_count:
                        # ── API 调用日志：达到尝试上限 ──
                        if _call_trace:
                            _lat = (time.monotonic() - request_started) * 1000
                            api_logger.record(
                                _call_trace, status="failed", latency_ms=_lat,
                                retry_count=api_attempts, error=str(e),
                            )
                        raise RuntimeError(
                            f"ask_chatbot reached retry limit ({max_retry_count}): "
                            f"{type(e).__name__}: {e}"
                        ) from e

                # gemini no_candidates
                if "candidates" in str(e) and api_try_count > 1:
                    # ── API 调用日志：Gemini 空响应 ──
                    if _call_trace:
                        _lat = (time.monotonic() - request_started) * 1000
                        api_logger.record(
                            _call_trace, status="failed", latency_ms=_lat,
                            retry_count=api_attempts, error=str(e),
                        )
                    return "", token
                if self.apiErrorWait >= 0:
                    sleep_time = self.apiErrorWait + random.random()
                else:
                    # https://aws.amazon.com/cn/blogs/architecture/exponential-backoff-and-jitter/
                    sleep_time = 2 ** min(api_try_count, 6)
                    sleep_time = random.randint(0, sleep_time)

                if len(self.client_list) > 1:
                    token_info = f"[{token.maskToken()}]"
                else:
                    token_info = ""

                if is_rate_limited:
                    # bar 仅在翻译阶段/GenDic 赋值；元数据引擎独立运行无 bar，判空避免 AttributeError 打断退避重试
                    bar = getattr(self.pj_config, "bar", None)
                    if bar is not None:
                        bar.text(
                            "-> 检测到频率限制(429 RateLimitError)，翻译仍在进行中但速度将受影响..."
                        )
                else:
                    if file_name != "" and file_name[:1] != "[":
                        file_name = f"[{file_name}]"
                    raw_file_name = file_name[1:-1] if file_name.startswith("[") and file_name.endswith("]") else file_name
                    error_parts = []
                    exception_type = type(e).__name__
                    exception_text = str(e).strip()
                    if exception_text:
                        error_parts.append(f"{exception_type}: {exception_text}")
                    else:
                        error_parts.append(exception_type)

                    api_error_text = ""
                    try:
                        raw_api_error = response.model_extra.get("error")
                        if isinstance(raw_api_error, dict):
                            api_error_text = str(
                                raw_api_error.get("message")
                                or raw_api_error.get("code")
                                or raw_api_error
                            ).strip()
                        elif raw_api_error is not None:
                            api_error_text = str(raw_api_error).strip()
                    except Exception:
                        pass

                    if api_error_text:
                        error_parts.append(f"API返回: {api_error_text}")

                    message_text = " | ".join(part for part in error_parts if part)
                    message_text = f"{message_text} | sleeping {sleep_time:.3f}s"
                    # ── API 调用日志：可重试错误 ──
                    if _call_trace:
                        _lat = (time.monotonic() - request_started) * 1000
                        api_logger.record(
                            _call_trace, status="error", latency_ms=_lat,
                            retry_count=api_attempts,
                            error=(str(e) or "")[:2000],
                        )
                    LOGGER.warning(
                        f"[API Error]{token_info}{file_name} {message_text}"
                    )

                    self._record_runtime_error(
                        kind="api",
                        message=message_text,
                        filename=raw_file_name,
                        retry_count=api_attempts,
                        model=getattr(token, "model_name", "") or None,
                        sleep_seconds=float(sleep_time),
                        level="warning",
                    )

                await self._interruptible_sleep(sleep_time)

    def clean_up(self) -> None:
        pass

    async def shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True

        clients = [client for client, _ in getattr(self, "client_list", [])]
        clients.extend(getattr(self, "_retired_clients", []))
        seen_clients: set[int] = set()
        for client in clients:
            if id(client) in seen_clients:
                continue
            seen_clients.add(id(client))
            if client is None:
                continue

            close_callable = getattr(client, "close", None)
            if callable(close_callable):
                try:
                    maybe_coro = close_callable()
                    if asyncio.iscoroutine(maybe_coro):
                        try:
                            await asyncio.wait_for(maybe_coro, timeout=3.0)
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            pass
                    continue
                except Exception:
                    pass

            aclose_callable = getattr(client, "aclose", None)
            if callable(aclose_callable):
                try:
                    maybe_coro = aclose_callable()
                    if asyncio.iscoroutine(maybe_coro):
                        try:
                            await asyncio.wait_for(maybe_coro, timeout=3.0)
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            pass
                except Exception:
                    pass

        retired_clients = getattr(self, "_retired_clients", None)
        if retired_clients is not None:
            retired_clients.clear()
