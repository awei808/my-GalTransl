"""元数据阶段行数阈值（internals.pipeline.metaMinLines）口径测试。

- count_nonempty_lines：与前端进度分母同口径的「非空行数」统计；
- get_meta_min_lines：默认 20、0 合法（关闭规则）、非法值回退默认。
"""
import unittest

from GalTransl.Backend.metadata import (
    META_MIN_LINES_DEFAULT,
    count_nonempty_lines,
    get_meta_min_lines,
)


class _StubConfig:
    """最小配置桩：只需 getKey(key, default)。"""

    def __init__(self, value: object = None, present: bool = True) -> None:
        self._value = value
        self._present = present

    def getKey(self, key: str, default: object = None) -> object:
        if not self._present:
            return default
        return self._value


class CountNonemptyLinesTests(unittest.TestCase):
    def test_counts_only_nonempty_messages(self) -> None:
        jsons = [
            {"name": "A", "message": "你好"},
            {"name": "B", "message": ""},
            {"name": "C", "message": "   "},
            {"name": "D", "message": "世界"},
        ]
        self.assertEqual(count_nonempty_lines(jsons), 2)

    def test_empty_and_none_inputs_return_zero(self) -> None:
        self.assertEqual(count_nonempty_lines([]), 0)
        self.assertEqual(count_nonempty_lines(None), 0)

    def test_non_dict_rows_ignored(self) -> None:
        jsons = [None, "字符串", 42, {"message": "有效"}]
        self.assertEqual(count_nonempty_lines(jsons), 1)

    def test_falsy_message_treated_as_empty(self) -> None:
        # 与进度分母口径完全一致：`str(value or "")` 使 0/None/False 都算空行，
        # 真值非字符串（如 123）按 str() 计为有效行
        jsons = [{"message": 0}, {"message": 123}, {"message": None}, {"message": False}]
        self.assertEqual(count_nonempty_lines(jsons), 1)


class GetMetaMinLinesTests(unittest.TestCase):
    def test_default_when_key_missing(self) -> None:
        self.assertEqual(
            get_meta_min_lines(_StubConfig(present=False)), META_MIN_LINES_DEFAULT
        )

    def test_int_value_preserved(self) -> None:
        self.assertEqual(get_meta_min_lines(_StubConfig(30)), 30)

    def test_zero_means_disabled_and_is_legal(self) -> None:
        self.assertEqual(get_meta_min_lines(_StubConfig(0)), 0)

    def test_numeric_string_accepted(self) -> None:
        self.assertEqual(get_meta_min_lines(_StubConfig("45")), 45)

    def test_integral_float_accepted(self) -> None:
        # YAML 手改写 `0.0` 想关闭规则时不能反而落回默认 20
        self.assertEqual(get_meta_min_lines(_StubConfig(0.0)), 0)
        self.assertEqual(get_meta_min_lines(_StubConfig(20.0)), 20)

    def test_invalid_values_fall_back_to_default(self) -> None:
        for bad in (True, False, "abc", "", "20.5", "+20", -1, 17.5, -1.0, [20], None):
            with self.subTest(bad=bad):
                self.assertEqual(
                    get_meta_min_lines(_StubConfig(bad)), META_MIN_LINES_DEFAULT
                )


if __name__ == "__main__":
    unittest.main()
