"""问题检测阈值取值守卫：_coerce_positive_int 对 yaml 手改值的容错口径（与桌面端输入对齐）。"""
import unittest

from GalTransl.ConfigHelper import CProjectConfig


class _ThresholdGuardMixin:
    """通过 __new__ 绕过依赖真实项目目录的 __init__，只注入待校验的配置片段。"""

    section_key: str
    default_value: int

    def setUp(self) -> None:
        self.cfg = CProjectConfig.__new__(CProjectConfig)

    def value_of(self, raw: object) -> int:
        self.cfg.projectConfig = {"problemAnalyze": {self.section_key: raw}}
        return self.getter()

    def getter(self) -> int:
        raise NotImplementedError

    def test_valid_values_are_accepted(self) -> None:
        self.assertEqual(self.value_of(self.default_value), self.default_value)
        self.assertEqual(self.value_of(str(self.default_value)), self.default_value)
        self.assertEqual(self.value_of(float(self.default_value)), self.default_value)
        self.assertEqual(self.value_of(f" {self.default_value} "), self.default_value)
        self.assertEqual(self.value_of("1e1"), 10)
        self.assertEqual(self.value_of(1), 1)

    def test_non_integer_falls_back_to_default(self) -> None:
        # 非整数一律回退默认值（与前端 Number.isInteger 口径一致），不再静默截断
        for raw in [self.default_value + 0.5, 2.5, "17.5", 0.4, -0.4]:
            self.assertEqual(self.value_of(raw), self.default_value, f"raw={raw!r}")

    def test_invalid_values_fallback_to_default(self) -> None:
        for raw in [True, False, 0, -5, None, "", "abc", "0x10", "1_000",
                    "Infinity", "nan", float("inf"), float("nan"), 1e309]:
            self.assertEqual(self.value_of(raw), self.default_value, f"raw={raw!r}")

    def test_missing_key_fallback_to_default(self) -> None:
        self.cfg.projectConfig = {"problemAnalyze": {}}
        self.assertEqual(self.getter(), self.default_value)
        self.cfg.projectConfig = {}
        self.assertEqual(self.getter(), self.default_value)


class AvgSentenceLengthThresholdTests(_ThresholdGuardMixin, unittest.TestCase):
    section_key = "avgSentenceLengthThreshold"
    default_value = 17

    def getter(self) -> int:
        return self.cfg.getAvgSentenceLengthThreshold()


class HSentenceLengthThresholdTests(_ThresholdGuardMixin, unittest.TestCase):
    section_key = "avgSentenceLengthThresholdH"
    default_value = 24

    def getter(self) -> int:
        return self.cfg.getHSentenceLengthThreshold()


class AttributiveMaxLengthTests(_ThresholdGuardMixin, unittest.TestCase):
    section_key = "attributiveMaxLength"
    default_value = 10

    def getter(self) -> int:
        return self.cfg.getAttributiveMaxLength()


class AdverbialMaxLengthTests(_ThresholdGuardMixin, unittest.TestCase):
    section_key = "adverbialMaxLength"
    default_value = 12

    def getter(self) -> int:
        return self.cfg.getAdverbialMaxLength()


if __name__ == "__main__":
    unittest.main()
