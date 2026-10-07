# -*- coding: utf-8 -*-
"""postprocess_results 收尾职责回归测试。

afterTranslation 后处理链已上移为流水线阶段 8（llm_postprocess.run_improve_stage），
postprocess_results 退化为纯收尾（问题检测/缓存快照/输出合并）。本测试锁定：
无论任务引擎与 gpt.afterTranslation 配置如何，postprocess_results 都不得执行
后处理链（_run_after_trans_single_file 永不被调用）。

patch 一律打在 GalTransl.Frontend.llm_postprocess（调用方在本模块命名空间查找
名字，打 LLMTranslate 会静默打空）。
"""
import unittest
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

from GalTransl.Frontend.LLMTranslate import postprocess_results


class _FakeProjectConfig:
    """postprocess_results 所需的最小配置桩（口径同 test_postprocess_h_ranges）。"""

    def __init__(self, select_translator: str) -> None:
        self.select_translator = select_translator

    def getProjectDir(self) -> str:
        return "/tmp/proj"

    def getInputPath(self) -> str:
        return "/tmp/proj/gt_input"

    def getOutputPath(self) -> str:
        return "/tmp/proj/gt_output"

    def getCachePath(self) -> str:
        return "/tmp/proj/transl_cache"

    @property
    def gpt_dic(self) -> list:
        return []

    @property
    def name_replaceDict(self) -> dict:
        return {}

    @property
    def file_save_funcs(self) -> dict:
        return {}

    def getKey(self, key: str, default: Any = None) -> Any:
        return default


def _make_chunk(file_path: str = "/tmp/proj/gt_input/story.txt.json") -> SimpleNamespace:
    return SimpleNamespace(
        trans_list=[],
        json_list=[],
        file_path=file_path,
        chunk_index=0,
        total_chunks=1,
        had_retranslated=False,
    )


def _run_finalize(
    cfg: _FakeProjectConfig, after_order: list, run_after_trans: AsyncMock
) -> Optional[Exception]:
    """在公共 patch 集合下执行 postprocess_results，返回异常（无异常为 None）。"""
    chunk = _make_chunk()
    try:
        with patch(
            "GalTransl.Frontend.llm_postprocess._resolve_after_translation_order",
            return_value=after_order,
        ), patch(
            "GalTransl.Frontend.llm_postprocess._run_after_trans_single_file",
            new=run_after_trans,
        ), patch(
            "GalTransl.Frontend.llm_postprocess.ensure_model_available_if_needed",
            new=AsyncMock(return_value=None),
        ), patch(
            "GalTransl.Frontend.llm_postprocess._resolve_file_h_ranges",
            return_value=[],
        ), patch(
            "GalTransl.Frontend.llm_postprocess.find_problems",
            return_value=None,
        ), patch(
            "GalTransl.Frontend.llm_postprocess._update_runtime",
            return_value=None,
        ), patch(
            "GalTransl.Frontend.llm_postprocess.save_transCache_to_json",
            new=AsyncMock(),
        ), patch(
            "GalTransl.Frontend.llm_postprocess.DictionaryCombiner.combine",
            return_value=([], []),
        ):
            import asyncio

            asyncio.run(postprocess_results([chunk], cfg))
    except Exception as e:  # noqa: BLE001 - 测试内捕获后交由断言判别
        return e
    return None


class PostprocessResultsNoChainTests(unittest.TestCase):
    """postprocess_results 任何情况下都不执行 afterTranslation 链。"""

    def _assert_no_chain(self, eng_type: str) -> None:
        # 故意给非空 order：若收尾函数仍残留链逻辑，下方 not_awaited 断言必失败
        run_after_trans = AsyncMock()
        err = _run_finalize(_FakeProjectConfig(eng_type), ["improve"], run_after_trans)
        self.assertIsNone(err)
        run_after_trans.assert_not_awaited()

    def test_standalone_translation_no_chain(self) -> None:
        self._assert_no_chain("ForGal-json-translate")

    def test_full_pipeline_no_chain(self) -> None:
        # 链由 run_improve_stage 统一编排，收尾函数即使流水线调用也不跑链
        self._assert_no_chain("ForGal-full-pipeline")

    def test_rebuild_engine_no_chain(self) -> None:
        self._assert_no_chain("rebuilda")

    def test_standalone_fix_engine_no_chain(self) -> None:
        self._assert_no_chain("ForFixRound")

    def test_empty_translator_no_chain(self) -> None:
        self._assert_no_chain("")


if __name__ == "__main__":
    unittest.main()
