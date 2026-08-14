"""翻译阶段 H 区间传递回归测试（postprocess_results）。

背景：postprocess_results 在翻译完成写缓存时调用 find_problems，此前不传 h_ranges，
导致 H 场景句子「长句丢失换行」走平均分句阈值而非 H 专用阈值。修复后应把
_resolve_file_h_ranges 解析出的 H 区间传给 find_problems。

本测试 patch 副作用依赖，聚焦验证「h_ranges 被正确传递到 find_problems」。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from GalTransl.Frontend.LLMTranslate import postprocess_results


class _FakeProjectConfig:
    def getProjectDir(self) -> str:
        return "/tmp/proj"

    def getInputPath(self) -> str:
        return "/tmp/proj/gt_input"

    def getOutputPath(self) -> str:
        return "/tmp/proj/gt_output"

    def getCachePath(self) -> str:
        return "/tmp/proj/transl_cache"

    @property
    def select_translator(self) -> str:
        return "ForGal-full-pipeline"

    @property
    def gpt_dic(self) -> list:
        return []

    @property
    def name_replaceDict(self) -> dict:
        return {}

    @property
    def file_save_funcs(self) -> dict:
        return {}


class PostprocessHRangesPassTests(unittest.TestCase):
    def _make_chunk(self, file_path: str = "/tmp/proj/gt_input/story.txt.json") -> SimpleNamespace:
        return SimpleNamespace(
            trans_list=[],
            file_path=file_path,
            chunk_index=0,
            total_chunks=1,
        )

    def test_find_problems_receives_resolved_h_ranges(self) -> None:
        cfg = _FakeProjectConfig()
        chunk = self._make_chunk()
        captured = {}

        def _fake_find_problems(trans_list, projectConfig, gpt_dic, h_ranges=None):
            captured["h_ranges"] = h_ranges

        with patch(
            "GalTransl.Frontend.LLMTranslate._resolve_file_h_ranges",
            return_value=[(0, 10)],
        ), patch(
            "GalTransl.Frontend.LLMTranslate.find_problems",
            side_effect=_fake_find_problems,
        ), patch(
            "GalTransl.Frontend.LLMTranslate._resolve_after_translation_mode",
            return_value="none",
        ), patch(
            "GalTransl.Frontend.LLMTranslate._update_runtime",
            return_value=None,
        ), patch(
            "GalTransl.Frontend.LLMTranslate.save_transCache_to_json",
            new=AsyncMock(),
        ), patch(
            "GalTransl.Frontend.LLMTranslate.DictionaryCombiner.combine",
            return_value=([], []),
        ):
            # 用 asyncio.run 驱动 async 函数
            import asyncio

            asyncio.run(postprocess_results([chunk], cfg))

        self.assertEqual(captured.get("h_ranges"), [(0, 10)])

    def test_find_problems_receives_empty_h_ranges_when_none(self) -> None:
        cfg = _FakeProjectConfig()
        chunk = self._make_chunk()
        captured = {}

        def _fake_find_problems(trans_list, projectConfig, gpt_dic, h_ranges=None):
            captured["h_ranges"] = h_ranges

        with patch(
            "GalTransl.Frontend.LLMTranslate._resolve_file_h_ranges",
            return_value=[],
        ), patch(
            "GalTransl.Frontend.LLMTranslate.find_problems",
            side_effect=_fake_find_problems,
        ), patch(
            "GalTransl.Frontend.LLMTranslate._resolve_after_translation_mode",
            return_value="none",
        ), patch(
            "GalTransl.Frontend.LLMTranslate._update_runtime",
            return_value=None,
        ), patch(
            "GalTransl.Frontend.LLMTranslate.save_transCache_to_json",
            new=AsyncMock(),
        ), patch(
            "GalTransl.Frontend.LLMTranslate.DictionaryCombiner.combine",
            return_value=([], []),
        ):
            import asyncio

            asyncio.run(postprocess_results([chunk], cfg))

        self.assertEqual(captured.get("h_ranges"), [])


if __name__ == "__main__":
    unittest.main()
