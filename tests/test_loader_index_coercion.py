"""Loader index 类型统一为 int 的回归测试（M16）。

修复字符串 index 与 Problem.py 的 lo <= tran.index <= hi 整数比较冲突。
"""

import unittest

from GalTransl.Loader import load_transList


class LoaderIndexCoercionTests(unittest.TestCase):
    def test_string_numeric_index_converted_to_int(self) -> None:
        trans_list, _ = load_transList([{"message": "A", "index": "7"}])
        self.assertEqual(trans_list[0].index, 7)
        self.assertIsInstance(trans_list[0].index, int)

    def test_float_integral_index_converted_to_int(self) -> None:
        trans_list, _ = load_transList([{"message": "A", "index": 3.0}])
        self.assertEqual(trans_list[0].index, 3)
        self.assertIsInstance(trans_list[0].index, int)

    def test_missing_index_defaults_to_position(self) -> None:
        trans_list, _ = load_transList([{"message": "A"}, {"message": "B"}])
        self.assertEqual(trans_list[0].index, 1)
        self.assertEqual(trans_list[1].index, 2)

    def test_invalid_index_raises_value_error_with_position(self) -> None:
        with self.assertRaisesRegex(ValueError, "第1项"):
            load_transList([{"message": "A", "index": "abc"}])
        with self.assertRaises(ValueError):
            load_transList([{"message": "A", "index": 1.5}])
        with self.assertRaises(ValueError):
            load_transList([{"message": "A", "index": True}])


if __name__ == "__main__":
    unittest.main()
