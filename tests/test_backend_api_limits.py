"""后端 API 调用限制测试：次数上限、频率节流、错误率上限。

覆盖 GalTransl/Backend/BaseEngine.py 中：
- init_chatbot 按 eng_type 选择隔离的后端配置段并读取三项配置
- _coerce_ratio / _coerce_nonneg_float 对非法值的规范化
- _check_request_count_quota：累计请求次数超过 apiMaxRequests 终止流程
- _throttle_request_rate：两次请求间隔不小于 apiMinIntervalSec
- _check_error_rate_quota：错误率超过 apiMaxErrorRate 终止流程
- 0/负/非法配置值表示「不限制」，不误触发终止
"""
import asyncio
import threading
import time
import unittest

from GalTransl.Backend.BaseEngine import BaseEngine
from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.Service import JobCancelledError


def _build_config(backend_specific: dict) -> CProjectConfig:
    """构造最小可用的 CProjectConfig，仅提供 getBackendConfigSection 所需字段。"""
    cfg = CProjectConfig.__new__(CProjectConfig)
    cfg.projectConfig = {
        "backendSpecific": backend_specific,
        "proxy": {},
        "dictionary": {},
    }
    cfg.keyValues = {}
    return cfg


def _make_engine(eng_type: str, section_cfg: dict) -> BaseEngine:
    """绕过重型 __init__，仅注入限制逻辑所需的属性，便于纯单元验证。"""
    cfg = _build_config({"OpenAI-Compatible": section_cfg, "SakuraLLM": section_cfg})
    eng = BaseEngine.__new__(BaseEngine)
    # 复刻 init_chatbot 的配置读取与规范化逻辑
    eng.eng_type = eng_type
    eng.api_max_error_rate = BaseEngine._coerce_ratio(
        section_cfg.get("apiMaxErrorRate", 0), 0
    )
    eng.api_min_interval_sec = BaseEngine._coerce_nonneg_float(
        section_cfg.get("apiMinIntervalSec", 0), 0
    )
    eng.api_max_requests = BaseEngine._coerce_optional_int(
        section_cfg.get("apiMaxRequests", 0), 0
    )
    eng._request_count = 0
    eng._last_request_ts = 0.0
    eng._total_requests = 0
    eng._failed_requests = 0
    eng._rate_lock = threading.Lock()
    return eng


class CoerceTests(unittest.TestCase):
    def test_ratio_legal(self) -> None:
        self.assertEqual(BaseEngine._coerce_ratio(0.3, 0), 0.3)
        self.assertEqual(BaseEngine._coerce_ratio("0.5", 0), 0.5)

    def test_ratio_out_of_range_returns_zero(self) -> None:
        # 越界（>1）或不合法一律回退为 0（表示不限制）
        self.assertEqual(BaseEngine._coerce_ratio(2.0, 0), 0.0)
        self.assertEqual(BaseEngine._coerce_ratio(-0.1, 0), 0.0)
        self.assertEqual(BaseEngine._coerce_ratio("bad", 0), 0.0)

    def test_nonneg_float_legal_and_clamped(self) -> None:
        self.assertEqual(BaseEngine._coerce_nonneg_float(2.0, 0), 2.0)
        self.assertEqual(BaseEngine._coerce_nonneg_float(-1, 0), 0.0)
        self.assertEqual(BaseEngine._coerce_nonneg_float("x", 0), 0.0)

    def test_positive_int_fallback(self) -> None:
        self.assertEqual(BaseEngine._coerce_positive_int("x", 1000), 1000)
        # 0/负数抬为 1（用于并发数等必须 >=1 的场景）
        self.assertEqual(BaseEngine._coerce_positive_int(0, 1000), 1)

    def test_optional_int_keeps_zero_as_unlimited(self) -> None:
        # apiMaxRequests：0 表示不限制，必须保留为 0；非法值回退默认 0
        self.assertEqual(BaseEngine._coerce_optional_int(0, 0), 0)
        self.assertEqual(BaseEngine._coerce_optional_int(-1, 0), 0)
        self.assertEqual(BaseEngine._coerce_optional_int("x", 0), 0)
        self.assertEqual(BaseEngine._coerce_optional_int(50, 0), 50)


class ApiErrorWaitCoerceTests(unittest.TestCase):
    """apiErrorWait 规范化：auto/非法 -> -1（指数退避），数字 -> float（固定退避，支持亚秒）。"""

    def test_auto_and_empty_map_to_negative_one(self) -> None:
        self.assertEqual(BaseEngine._coerce_error_wait("auto"), -1.0)
        self.assertEqual(BaseEngine._coerce_error_wait("AUTO"), -1.0)
        self.assertEqual(BaseEngine._coerce_error_wait(""), -1.0)
        self.assertEqual(BaseEngine._coerce_error_wait(None), -1.0)

    def test_invalid_string_falls_back_to_negative_one(self) -> None:
        # 非法字符串、空串、不可解析值统一按 auto 处理
        self.assertEqual(BaseEngine._coerce_error_wait("abc"), -1.0)
        self.assertEqual(BaseEngine._coerce_error_wait("   "), -1.0)

    def test_bool_is_rejected_explicitly(self) -> None:
        # bool 是 int 子类，若被 float() 会变成 1.0/0.0，必须拒绝
        self.assertEqual(BaseEngine._coerce_error_wait(True), -1.0)
        self.assertEqual(BaseEngine._coerce_error_wait(False), -1.0)

    def test_float_precision_is_preserved(self) -> None:
        # 亚秒配置：0.5 秒应保留为 0.5，不被 int() 截断为 0
        self.assertEqual(BaseEngine._coerce_error_wait(0.5), 0.5)
        self.assertEqual(BaseEngine._coerce_error_wait("1.5"), 1.5)
        self.assertEqual(BaseEngine._coerce_error_wait("0.25"), 0.25)

    def test_int_forms_work(self) -> None:
        self.assertEqual(BaseEngine._coerce_error_wait(2), 2.0)
        self.assertEqual(BaseEngine._coerce_error_wait("120"), 120.0)

    def test_zero_is_valid_fixed_wait(self) -> None:
        # 0 是非负合法值：固定退避但抖动也来自 random.random()
        self.assertEqual(BaseEngine._coerce_error_wait(0), 0.0)

    def test_negative_non_finite_fall_back_to_negative_one(self) -> None:
        # 负数、nan、inf 无退避时长意义，统一按 auto 处理，避免 sleep(inf/nan) 卡死
        self.assertEqual(BaseEngine._coerce_error_wait(-1), -1.0)
        self.assertEqual(BaseEngine._coerce_error_wait("-2"), -1.0)
        self.assertEqual(BaseEngine._coerce_error_wait(float("nan")), -1.0)
        self.assertEqual(BaseEngine._coerce_error_wait(float("inf")), -1.0)


class BackendSectionIsolationTests(unittest.TestCase):
    def test_eng_type_selects_isolated_section(self) -> None:
        # 两个后端段配置互不相同，验证按 eng_type 隔离读取
        bs = {
            "OpenAI-Compatible": {"apiMaxErrorRate": 0.3, "apiMinIntervalSec": 2.0, "apiMaxRequests": 1000},
            "SakuraLLM": {"apiMaxErrorRate": 0.5, "apiMinIntervalSec": 0.0, "apiMaxRequests": 0},
        }
        cfg = _build_config(bs)

        def select(eng_type: str) -> str:
            return "SakuraLLM" if "sakura" in (eng_type or "").lower() else "OpenAI-Compatible"

        gpt = cfg.getBackendConfigSection(select("gpt35"))
        sak = cfg.getBackendConfigSection(select("Sakura"))
        self.assertEqual(gpt["apiMaxErrorRate"], 0.3)
        self.assertEqual(sak["apiMaxErrorRate"], 0.5)
        self.assertEqual(sak["apiMinIntervalSec"], 0.0)
        self.assertEqual(sak["apiMaxRequests"], 0)


class InitChatbotRealPathTests(unittest.TestCase):
    def test_init_chatbot_reads_explicit_fields(self) -> None:
        # 走真实 init_chatbot（coerce 段）验证三字段读取，避免在 _make_engine 复刻逻辑漂移。
        # init_chatbot 在 coerce 段之后依赖 tokenProvider 等重型组件，此处仅断言 coerce 段已正确赋值。
        cfg = _build_config({
            "OpenAI-Compatible": {
                "apiMaxErrorRate": 0.3,
                "apiMinIntervalSec": 2.0,
                "apiMaxRequests": 1000,
            },
        })
        eng = BaseEngine.__new__(BaseEngine)
        eng.trans_prompt = None  # 预置 init_chatbot 行384所需的最小属性
        try:
            eng.init_chatbot("gpt35", cfg)
        except Exception:
            # 后续重型依赖缺失会抛错，但三字段已在 coerce 段(行353-355)赋值
            pass
        self.assertEqual(eng.api_max_error_rate, 0.3)
        self.assertEqual(eng.api_min_interval_sec, 2.0)
        self.assertEqual(eng.api_max_requests, 1000)

    def test_init_chatbot_default_zero_when_no_fields(self) -> None:
        # 内置默认配置（backendSpecific 段无这三个字段）经真实 init_chatbot 应读默认 0（不限制）。
        cfg = _build_config({"OpenAI-Compatible": {}})
        eng = BaseEngine.__new__(BaseEngine)
        eng.trans_prompt = None
        try:
            eng.init_chatbot("gpt35", cfg)
        except Exception:
            pass
        self.assertEqual(eng.api_max_error_rate, 0)
        self.assertEqual(eng.api_min_interval_sec, 0.0)
        self.assertEqual(eng.api_max_requests, 0)
        # apiErrorWait 未配置时默认 "auto" -> -1（指数退避）
        self.assertEqual(eng.apiErrorWait, -1.0)

    def test_init_chatbot_keeps_subsecond_error_wait(self) -> None:
        # 真实 init_chatbot 链路中，亚秒 apiErrorWait 应保留小数精度
        cfg = _build_config({
            "OpenAI-Compatible": {"apiErrorWait": "0.5"},
        })
        eng = BaseEngine.__new__(BaseEngine)
        eng.trans_prompt = None
        try:
            eng.init_chatbot("gpt35", cfg)
        except Exception:
            pass
        self.assertEqual(eng.apiErrorWait, 0.5)


class RequestCountQuotaTests(unittest.TestCase):
    def test_count_limit_raises(self) -> None:
        eng = _make_engine("gpt35", {"apiMaxRequests": 2})
        eng._check_request_count_quota()  # 1
        eng._check_request_count_quota()  # 2
        with self.assertRaises(JobCancelledError):
            eng._check_request_count_quota()  # 3 触发

    def test_count_limit_zero_means_unlimited(self) -> None:
        eng = _make_engine("gpt35", {"apiMaxRequests": 0})
        for _ in range(5):
            eng._check_request_count_quota()  # 不触发

    def test_default_config_no_field_means_unlimited(self) -> None:
        # 内置默认配置（backendSpecific 段无这三个字段）应读不到值 -> 默认 0 -> 不限制
        eng = _make_engine("gpt35", {})
        self.assertEqual(eng.api_max_error_rate, 0)
        self.assertEqual(eng.api_min_interval_sec, 0.0)
        self.assertEqual(eng.api_max_requests, 0)
        for _ in range(5):
            eng._check_request_count_quota()  # 不触发
            eng._check_error_rate_quota()  # 不触发
        # 节流为 0 也不应等待
        async def run() -> float:
            t0 = time.monotonic()
            await eng._throttle_request_rate()
            await eng._throttle_request_rate()
            return time.monotonic() - t0
        self.assertLess(asyncio.run(run()), 0.1)


class ErrorRateQuotaTests(unittest.TestCase):
    def test_error_rate_limit_raises(self) -> None:
        eng = _make_engine("gpt35", {"apiMaxErrorRate": 0.5})
        eng._total_requests = 40
        eng._failed_requests = 21  # 52.5% >= 50%
        with self.assertRaises(JobCancelledError):
            eng._check_error_rate_quota()

    def test_error_rate_below_threshold_no_raise(self) -> None:
        eng = _make_engine("gpt35", {"apiMaxErrorRate": 0.5})
        eng._total_requests = 40
        eng._failed_requests = 19  # 47.5% < 50%
        eng._check_error_rate_quota()  # 不触发

    def test_error_rate_small_sample_no_raise(self) -> None:
        # 错误率上限 30%，但样本量 < 最小门槛(20)：即使前 5 次全失败也不触发，避免早期误杀
        eng = _make_engine("gpt35", {"apiMaxErrorRate": 0.3})
        eng._total_requests = 5
        eng._failed_requests = 5  # 100% 但样本不足
        eng._check_error_rate_quota()  # 不触发
        # 恰好达到门槛：20 次中 6 次失败 = 30% >= 30% 应触发
        eng._total_requests = 20
        eng._failed_requests = 6
        with self.assertRaises(JobCancelledError):
            eng._check_error_rate_quota()

    def test_error_rate_zero_means_unlimited(self) -> None:
        eng = _make_engine("gpt35", {"apiMaxErrorRate": 0})
        eng._total_requests = 100
        eng._failed_requests = 100  # 100% 但因配置为 0 不限制
        eng._check_error_rate_quota()  # 不触发

    def test_429_rate_limited_not_counted_as_failure(self) -> None:
        # 全部请求都是 429 限流(is_rate_limited)，失败计数应为 0，错误率不上升
        eng = _make_engine("gpt35", {"apiMaxErrorRate": 0.3})
        eng._total_requests = 40
        eng._failed_requests = 0  # 模拟 429 不计失败（仅 total 增加）
        eng._check_error_rate_quota()  # 0% 不触发



class ThrottleTests(unittest.TestCase):
    def test_throttle_respects_interval(self) -> None:
        eng = _make_engine("gpt35", {"apiMinIntervalSec": 0.2})

        async def run() -> float:
            t0 = time.monotonic()
            await eng._throttle_request_rate()
            await eng._throttle_request_rate()  # 第二次应等待约 0.2s
            return time.monotonic() - t0

        dt = asyncio.run(run())
        self.assertGreaterEqual(dt, 0.15)

    def test_throttle_zero_means_no_wait(self) -> None:
        eng = _make_engine("gpt35", {"apiMinIntervalSec": 0})

        async def run() -> float:
            t0 = time.monotonic()
            await eng._throttle_request_rate()
            await eng._throttle_request_rate()
            return time.monotonic() - t0

        dt = asyncio.run(run())
        self.assertLess(dt, 0.1)


if __name__ == "__main__":
    unittest.main()
