"""项目字典配置公共字典兜底补全测试。

覆盖：
  1. 空/缺失字典配置 → 补全全部公共字典到对应配置键；
  2. 已有部分字典 → 只补缺失项，不重复追加；
  3. 幂等：二次调用无变更；
  4. 已配置的 (project_dir) 项目字典不受影响；
  5. 用户主动移除的公共字典被重新补回（需求语义）；
  6. 公共字典目录不存在 → 配置不变。
"""

import os
import tempfile
import unittest

import yaml

from GalTransl.server import _ensure_common_dicts_in_config

COMMON_FILES = [
    "00通用字典_符号_译后.txt",
    "00通用字典_译后.txt",
    "00通用字典_译前.txt",
    "01H字典_矫正_译前.txt",
    "GPT字典.txt",
    "GPT字典_h.txt",
    "禁用词_h.txt",
    "禁用词_非h.txt",
]


class EnsureCommonDictsTests(unittest.TestCase):
    """_ensure_common_dicts_in_config：缺失公共字典自动补全（幂等）。"""

    def setUp(self) -> None:
        self.pdir = tempfile.mkdtemp(prefix="dict_auto_complete_")
        self.ddict = tempfile.mkdtemp(prefix="dict_auto_common_")
        for name in COMMON_FILES:
            with open(os.path.join(self.ddict, name), "w", encoding="utf-8") as f:
                f.write("测试词|测试译\n")

    def _write_config(self, dictionary: dict) -> None:
        with open(os.path.join(self.pdir, "config.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"dictionary": dictionary}, f, allow_unicode=True)

    def _read_dictionary(self) -> dict:
        with open(os.path.join(self.pdir, "config.yaml"), encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("dictionary", {})

    def test_empty_config_gets_all_common_dicts(self) -> None:
        self._write_config({})
        changed = _ensure_common_dicts_in_config(self.pdir, "config.yaml", self.ddict)
        self.assertTrue(changed)
        dic = self._read_dictionary()
        self.assertCountEqual(dic.get("preDict", []), ["00通用字典_译前.txt", "01H字典_矫正_译前.txt"])
        self.assertCountEqual(
            dic.get("postDict", []), ["00通用字典_译后.txt", "00通用字典_符号_译后.txt"]
        )
        self.assertCountEqual(dic.get("gpt.dict", []), ["GPT字典.txt", "GPT字典_h.txt"])
        self.assertCountEqual(dic.get("forbiddenDictH", []), ["禁用词_h.txt"])
        self.assertCountEqual(dic.get("forbiddenDictNonH", []), ["禁用词_非h.txt"])

    def test_partial_config_only_adds_missing(self) -> None:
        self._write_config(
            {
                "gpt.dict": ["GPT字典.txt"],
                "preDict": ["00通用字典_译前.txt", "01H字典_矫正_译前.txt"],
            }
        )
        changed = _ensure_common_dicts_in_config(self.pdir, "config.yaml", self.ddict)
        self.assertTrue(changed)
        dic = self._read_dictionary()
        self.assertCountEqual(dic["gpt.dict"], ["GPT字典.txt", "GPT字典_h.txt"])
        # 已存在的 preDict 原样保留，不因补全而重排
        self.assertEqual(dic["preDict"], ["00通用字典_译前.txt", "01H字典_矫正_译前.txt"])
        self.assertCountEqual(dic["postDict"], ["00通用字典_译后.txt", "00通用字典_符号_译后.txt"])

    def test_idempotent_second_call_no_change(self) -> None:
        self._write_config({})
        self.assertTrue(_ensure_common_dicts_in_config(self.pdir, "config.yaml", self.ddict))
        dic_before = self._read_dictionary()
        changed = _ensure_common_dicts_in_config(self.pdir, "config.yaml", self.ddict)
        self.assertFalse(changed)
        self.assertEqual(self._read_dictionary(), dic_before)

    def test_project_marker_entries_untouched(self) -> None:
        self._write_config(
            {
                "gpt.dict": ["(project_dir)项目GPT字典.txt"],
                "preDict": ["(project_dir)项目字典_译前.txt"],
            }
        )
        changed = _ensure_common_dicts_in_config(self.pdir, "config.yaml", self.ddict)
        self.assertTrue(changed)
        dic = self._read_dictionary()
        # 项目字典引用保留，公共引用照补
        self.assertIn("(project_dir)项目GPT字典.txt", dic["gpt.dict"])
        self.assertIn("GPT字典.txt", dic["gpt.dict"])
        self.assertIn("(project_dir)项目字典_译前.txt", dic["preDict"])

    def test_removed_common_dict_is_readded(self) -> None:
        # 需求语义：用户主动从配置移除的公共字典会被重新补回
        self._write_config(
            {
                "gpt.dict": [],
                "preDict": [],
                "postDict": [],
                "forbiddenDictH": [],
                "forbiddenDictNonH": [],
            }
        )
        changed = _ensure_common_dicts_in_config(self.pdir, "config.yaml", self.ddict)
        self.assertTrue(changed)
        dic = self._read_dictionary()
        self.assertCountEqual(dic["gpt.dict"], ["GPT字典.txt", "GPT字典_h.txt"])
        self.assertEqual(dic["forbiddenDictH"], ["禁用词_h.txt"])
        self.assertEqual(dic["forbiddenDictNonH"], ["禁用词_非h.txt"])

    def test_missing_common_dir_no_change(self) -> None:
        self._write_config({})
        missing_dir = os.path.join(self.pdir, "no_such_dict_dir")
        changed = _ensure_common_dicts_in_config(self.pdir, "config.yaml", missing_dir)
        self.assertFalse(changed)
        self.assertEqual(self._read_dictionary(), {})


if __name__ == "__main__":
    unittest.main()
