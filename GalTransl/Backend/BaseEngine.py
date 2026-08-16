import asyncio
import httpx
from opencc import OpenCC
from typing import Any, Optional, List
from collections import deque
from threading import Lock
from contextvars import ContextVar
from GalTransl.COpenAI import COpenAITokenPool, COpenAIToken
from GalTransl.ConfigHelper import CProxyPool, build_httpx_proxy_kwargs
from GalTransl import LOGGER, LANG_SUPPORTED
from GalTransl.i18n import get_text
from GalTransl.ConfigHelper import (
    CProjectConfig,
)
from GalTransl.Utils import load_guideline_file
from GalTransl.Backend.utils import coerce_bool
from GalTransl.TerminalOutput import should_print_translation_logs
from openai import RateLimitError, AsyncOpenAI
from openai import DefaultAioHttpClient
from openai._types import NOT_GIVEN
import json
import random
import time
from contextlib import suppress
from GalTransl.server_runtime import set_live_snippets
from GalTransl.ApiLogger import api_logger

try:
    from pyreqwest.compatibility.httpx import HttpxTransport
except Exception:
    HttpxTransport = None


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
        self._lock = Lock()

    def _trim_locked(self, now: float, window_seconds: float) -> None:
        cutoff = now - max(5.0, float(window_seconds))
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def record(self, latency_seconds: float, is_rate_limited: bool) -> None:
        now = time.monotonic()
        latency = max(0.0, float(latency_seconds))
        with self._lock:
            self._samples.append((now, latency, bool(is_rate_limited)))
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
                }
            rate_limited = sum(1 for _, _, limited in self._samples if limited)
            avg_latency = sum(lat for _, lat, _ in self._samples) / total
            return {
                "total": total,
                "rate_limited": rate_limited,
                "rate_limited_ratio": rate_limited / total,
                "avg_latency": avg_latency,
            }


# 引擎名 -> 后端模块路径。init_gptapi 依赖它惰性加载（importlib）目标模块：
# 模块加载时 @register_engine 装饰器运行，把「name -> 构造工厂」写入 ENGINE_REGISTRY。
ENGINE_MODULE_PATHS: dict[str, str] = {
    "ForGlobalPrompt": "GalTransl.Backend.ForGlobalPrompt",
    "ForGal-json-multi-chat": "GalTransl.Backend.ForGalJsonMulitChat",
    "ForImproveTranslation": "GalTransl.Backend.ForImproveTranslation",
    "ForBRStation": "GalTransl.Backend.ForBRStation",
    "ForJPResidue": "GalTransl.Backend.ForJPResidue",
    "ForBanWordFix": "GalTransl.Backend.ForBanWordFix",
    "GenDic": "GalTransl.Backend.GenDic",
    "ForFileMetaData": "GalTransl.Backend.ForFileMetaData",
    "ForBatchMetaData": "GalTransl.Backend.ForBatchMetaData",
    "ForPlotRouteMap": "GalTransl.Backend.ForPlotRouteMap",
}

# 引擎注册表：eng_type 名称 -> 惰性构造工厂。
# 各后端子类用 @register_engine("名称") 装饰器登记；init_gptapi 从本表取工厂调用。
ENGINE_REGISTRY: dict[str, callable] = {}


def register_engine(name: str):
    """类装饰器：把后端类登记到引擎注册表（ENGINE_REGISTRY）。

    登记的值为惰性构造工厂：构造时才 import 所在模块并实例化类。
    name 必须已声明于 ENGINE_MODULE_PATHS，且类所在模块与之匹配，否则报错提示。

    Args:
        name: 引擎类型标识（eng_type），如 "ForGal-json-multi-chat"。
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
                get_text("invalid_source_language", self.target_lang, self.source_lang)
            )
        else:
            self.source_lang = LANG_SUPPORTED[self.source_lang]
        if self.target_lang not in LANG_SUPPORTED.keys():
            raise ValueError(
                get_text("invalid_target_language", self.target_lang, self.target_lang)
            )
        else:
            self.target_lang = LANG_SUPPORTED[self.target_lang]

        # 429等待时间（废弃）
        self.wait_time = config.getKey("gpt.tooManyRequestsWaitTime", 60)
        # 跳过重试
        self.skipRetry = config.getKey("skipRetry", False)
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

        self._current_temp_type = ""
        self._shutdown_done = False

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

    def init_chatbot(self, eng_type: str, config: CProjectConfig) -> None:
        section_name = "OpenAI-Compatible"

        self.api_timeout = config.getBackendConfigSection(section_name).get(
            "apiTimeout", 300
        )
        self.apiErrorWait = config.getBackendConfigSection(section_name).get(
            "apiErrorWait", "auto"
        )
        self.tokenStrategy = config.getBackendConfigSection(section_name).get(
            "tokenStrategy", "random"
        )
        self.stream = config.getBackendConfigSection(section_name).get("stream", True)
        # 思考相关配置（profile 级，缺省时零发送，向后兼容）
        self.provider = config.getBackendConfigSection(section_name).get("provider", "auto")
        self.thinking_mode = config.getBackendConfigSection(section_name).get(
            "thinking_mode", "default"
        )
        self.reasoning_effort = config.getBackendConfigSection(section_name).get(
            "reasoning_effort", ""
        )
        self.extra_body_raw = config.getBackendConfigSection(section_name).get(
            "extra_body", ""
        )

        change_prompt = CProjectConfig.getProjectConfig(config)["common"].get(
            "gpt.change_prompt", "no"
        )
        prompt_content = CProjectConfig.getProjectConfig(config)["common"].get(
            "gpt.prompt_content", ""
        )
        if change_prompt == "AdditionalPrompt" and prompt_content != "":
            self.trans_prompt = (
                "# Additional Requirements: "
                + prompt_content
                + "\n"
                + self.trans_prompt
            )
        if change_prompt == "OverwritePrompt" and prompt_content != "":
            self.trans_prompt = prompt_content

        # 规范化 apiErrorWait："auto"/非法值->-1，数字字符串->int，避免后续比较的 TypeError
        if isinstance(self.apiErrorWait, bool):
            # bool 是 int 的子类，显式拒绝，避免 True/False 被当作 1/0
            self.apiErrorWait = -1
        elif isinstance(self.apiErrorWait, (int, float)):
            self.apiErrorWait = int(self.apiErrorWait)
        else:
            raw_wait = str(self.apiErrorWait).strip().lower()
            if raw_wait == "auto" or raw_wait == "":
                self.apiErrorWait = -1
            else:
                try:
                    self.apiErrorWait = int(float(raw_wait))
                except (TypeError, ValueError):
                    self.apiErrorWait = -1

        if self.proxyProvider:
            proxy_addr = self.proxyProvider.getProxy().addr
        else:
            proxy_addr = None

        trust_env = False  # 不使用系统代理
        proxy_kwargs = build_httpx_proxy_kwargs(proxy_addr)
        self.client_list = []
        for token in self.tokenProvider.get_available_token():
            http_client = None

            use_pyreqwest_transport = HttpxTransport is not None and not proxy_kwargs
            if use_pyreqwest_transport:
                try:
                    http_client = httpx.AsyncClient(
                        trust_env=trust_env,
                        limits=httpx.Limits(
                            max_keepalive_connections=None, max_connections=None
                        ),
                        transport=HttpxTransport(),
                    )
                except Exception as e:
                    LOGGER.warning(
                        f"初始化 pyreqwest HttpxTransport 失败，回退 DefaultAioHttpClient: {e}"
                    )

            if http_client is None:
                if HttpxTransport is not None and proxy_kwargs:
                    LOGGER.warning(
                        "检测到代理配置，当前回退到 DefaultAioHttpClient（pyreqwest transport 路径未启用代理注入）"
                    )
                http_client = DefaultAioHttpClient(
                    trust_env=trust_env,
                    limits=httpx.Limits(
                        max_keepalive_connections=None, max_connections=None
                    ),
                    **proxy_kwargs,
                )

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
    def _is_stop_requested(pj_config: CProjectConfig) -> bool:
        stop_event = getattr(pj_config, "stop_event", None)
        return stop_event is not None and stop_event.is_set()

    def _check_stop_requested(self) -> None:
        if self._is_stop_requested(self.pj_config):
            from GalTransl.Service import JobCancelledError

            raise JobCancelledError()

    def _build_prompt_request(
        self,
        input_src: str,
        gptdict: str,
        plot_metadata: str = "",
        batch_metadata: str = "",
    ) -> str:
        prompt_req = self.trans_prompt
        prompt_req = prompt_req.replace(
            "[translation_guideline]", self.pj_config.translation_guideline
        )
        prompt_req = prompt_req.replace("[Input]", input_src)
        prompt_req = prompt_req.replace("[Glossary]", gptdict)
        prompt_req = prompt_req.replace("[plot_metadata]", plot_metadata)
        # 批次级元数据(BatchMetadata)：默认空串（占位符被清除），
        # 仅多轮后端在首轮按需注入。其余后端不受影响。
        prompt_req = prompt_req.replace("[batch_metadata]", batch_metadata)
        prompt_req = prompt_req.replace("[SourceLang]", self.source_lang)
        prompt_req = prompt_req.replace("[TargetLang]", self.target_lang)
        return prompt_req

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

    def _record_request_health(self, latency_seconds: float, is_rate_limited: bool) -> None:
        try:
            self.request_health_metrics.record(latency_seconds, is_rate_limited)
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
        api_try_count = base_try_count
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
        # ForGalJsonMulitChat 中显式推送同一内容，此处为其他后端补齐。
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
                _call_trace = ""
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
                )
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
                        retry_count=api_try_count, prompt_tokens=_pt,
                        completion_tokens=_ct,
                        response_preview=result or "",
                        reasoning=reasoning_result,
                    )
                return result, token
            except Exception as e:
                is_rate_limited = isinstance(e, RateLimitError)
                self._record_request_health(
                    time.monotonic() - request_started,
                    is_rate_limited=is_rate_limited,
                )

                from GalTransl.Service import JobCancelledError
                if isinstance(e, JobCancelledError):
                    # ── API 调用日志：取消 ──
                    if _call_trace:
                        _lat = (time.monotonic() - request_started) * 1000
                        api_logger.record(
                            _call_trace, status="cancelled", latency_ms=_lat,
                            retry_count=api_try_count, error=str(e),
                        )
                    raise

                api_try_count += 1
                if max_retry_count is not None and api_try_count >= max_retry_count:
                    # ── API 调用日志：达到重试上限 ──
                    if _call_trace:
                        _lat = (time.monotonic() - request_started) * 1000
                        api_logger.record(
                            _call_trace, status="failed", latency_ms=_lat,
                            retry_count=api_try_count, error=str(e),
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
                            retry_count=api_try_count, error=str(e),
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
                    self.pj_config.bar.text(
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
                            retry_count=api_try_count,
                            error=(str(e) or "")[:2000],
                        )
                    LOGGER.warning(
                        f"[API Error]{token_info}{file_name} {message_text}"
                    )

                    self._record_runtime_error(
                        kind="api",
                        message=message_text,
                        filename=raw_file_name,
                        retry_count=api_try_count,
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

        for client, _ in getattr(self, "client_list", []):
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
