"""
批次 4/5 共享收口件的单元测试。

覆盖：
  1. CProjectConfig.get_workers_per_project —— workersPerProject 解析口径收口
     （字符串/缺失/0/负数/非法值全部回退 1，避免 Semaphore(0) 死锁）。
  2. utils.build_script_text —— 元数据引擎剧本正文构造参数化（行号/压平/names 回退）。
  3. metadata.format_file_metadata_block —— 文件级元数据块统一形态（含/不含指导语）。
  4. metadata.save_metadata_json —— per-file 原子写（临时文件 + os.replace）。

纯函数测试不依赖临时目录；save_metadata_json 用 tempfile（常规环境运行）。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from GalTransl.Backend.utils import build_script_text
from GalTransl.Backend.metadata import (
    FileMetaData,
    build_glossary_prompt_text,
    format_file_metadata_block,
    save_metadata_json,
)
from GalTransl.ConfigHelper import CProjectConfig


class GetWorkersPerProjectTests(unittest.TestCase):
    """CProjectConfig.get_workers_per_project 解析口径。"""

    def _cfg(self, raw):
        return SimpleNamespace(getKey=lambda key, default=None: raw)

    def _call(self, raw) -> int:
        return CProjectConfig.get_workers_per_project(self._cfg(raw))

    def test_missing_defaults_to_one(self) -> None:
        self.assertEqual(self._call(None), 1)

    def test_numeric_string(self) -> None:
        self.assertEqual(self._call("4"), 4)

    def test_plain_int(self) -> None:
        self.assertEqual(self._call(16), 16)

    def test_zero_raised_to_one(self) -> None:
        # 避免 asyncio.Semaphore(0) 死锁
        self.assertEqual(self._call(0), 1)
        self.assertEqual(self._call("0"), 1)

    def test_negative_raised_to_one(self) -> None:
        self.assertEqual(self._call(-3), 1)

    def test_garbage_falls_back_to_one(self) -> None:
        self.assertEqual(self._call("abc"), 1)


class BuildScriptTextTests(unittest.TestCase):
    """utils.build_script_text 参数化行为。"""

    def test_filemeta_style_plain(self) -> None:
        data = [{"name": "夢", "message": "こんにちは"}, {"message": "旁白"}]
        text, max_index = build_script_text(data, filename="f.json", tag="T")
        self.assertEqual(text, "夢：こんにちは\n旁白")
        self.assertEqual(max_index, 0)  # 无行号模式不统计

    def test_batchmeta_style_line_numbers_and_flatten(self) -> None:
        data = [{"name": "夢", "message": "a\nb\tc"}, {"message": "d\r\ne"}]
        text, max_index = build_script_text(
            data,
            filename="f.json",
            tag="T",
            use_line_numbers=True,
            flatten_whitespace=True,
            accept_names_plural=True,
        )
        self.assertEqual(text, "[1] 夢：a b c\n[2] d e")
        self.assertEqual(max_index, 2)

    def test_explicit_index_respected(self) -> None:
        # 显式 index 优先；无 index 项回退为自身位置（i+1），不接续显式 index
        data = [{"index": 5, "message": "x"}, {"message": "y"}]
        text, max_index = build_script_text(
            data, tag="T", use_line_numbers=True, flatten_whitespace=True
        )
        self.assertEqual(text, "[5] x\n[2] y")
        self.assertEqual(max_index, 5)

    def test_names_fallback(self) -> None:
        data = [{"names": "凛音", "message": "x"}]
        text, _ = build_script_text(
            data, tag="T", accept_names_plural=True
        )
        self.assertEqual(text, "凛音：x")

    def test_all_missing_message_returns_empty(self) -> None:
        data = [{"message": ""}, {"message": ""}]
        text, max_index = build_script_text(data, tag="T")
        self.assertEqual(text, "")
        self.assertEqual(max_index, 0)


class FormatFileMetadataBlockTests(unittest.TestCase):
    """metadata.format_file_metadata_block 统一形态。"""

    def _md(self):
        return FileMetaData(
            id="s.json",
            character=["爱丽丝", "波波"],
            costume="日常服",
            plot="冒险",
            tags=["奇幻"],
        )

    def test_wrapped_with_guidance(self) -> None:
        block = format_file_metadata_block(self._md())
        self.assertIn("<plot_metadata>", block)
        self.assertIn("id: s.json", block)
        self.assertIn("角色: 爱丽丝、波波", block)
        self.assertIn("服装: 日常服", block)
        self.assertIn("剧情: 冒险", block)
        self.assertIn("标签: 奇幻", block)
        self.assertIn("请参考上述", block)

    def test_no_guidance_for_batch_scene(self) -> None:
        block = format_file_metadata_block(self._md(), include_guidance=False)
        self.assertIn("<plot_metadata>", block)
        self.assertNotIn("请参考上述", block)

    def test_empty_fields_fallback(self) -> None:
        md = FileMetaData(character=[], costume="", plot="", tags=[])
        block = format_file_metadata_block(md)
        self.assertIn("角色: 无", block)
        self.assertIn("服装: 无", block)


class BuildGlossaryPromptTextTests(unittest.TestCase):
    """metadata.build_glossary_prompt_text 边界行为（不依赖真实字典文件）。"""

    def _cfg(self, dict_cfg):
        return SimpleNamespace(
            getDictCfgSection=lambda: dict_cfg,
            getProjectDir=lambda: "C:/nonexistent",
        )

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(
            build_glossary_prompt_text([], self._cfg({}), "T"), ""
        )

    def test_no_dict_section_returns_empty(self) -> None:
        self.assertEqual(
            build_glossary_prompt_text([{"message": "x"}], self._cfg(None), "T"), ""
        )

    def test_no_gpt_dict_returns_empty(self) -> None:
        self.assertEqual(
            build_glossary_prompt_text(
                [{"message": "x"}], self._cfg({"gpt.dict": []}), "T"
            ),
            "",
        )


class SaveMetadataJsonTests(unittest.TestCase):
    """metadata.save_metadata_json 原子写（临时文件 + os.replace）。"""

    def test_writes_and_replaces_atomically(self) -> None:
        tmp = tempfile.mkdtemp(prefix="save_meta_")
        try:
            cfg = SimpleNamespace(getCachePath=lambda: tmp)
            path = save_metadata_json(
                cfg, "passX", "file_a", "meta", {"id": "file_a"}, "T"
            )
            self.assertTrue(os.path.isfile(path))
            self.assertFalse(os.path.exists(path + ".tmp"))  # 临时文件已替换
            with open(path, encoding="utf-8") as f:
                import json

                self.assertEqual(json.load(f)["id"], "file_a")
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
