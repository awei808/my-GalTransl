# -*- coding: utf-8 -*-
"""流水线「翻译后处理阶段」单元测试。

验证：
  - _resolve_after_translation_order：接受有序数组（[improve, brfix]）与旧字符串
    （none / improve+brfix 组合），统一返回白名单内的有序 key 列表（保序、去重）；
    缺省回退 enableBetterTranslation（true→[improve]）、非法值回退空列表。
  - _run_after_trans_single_file：按 mode 实例化正确后端、调用 batch_translate、
    注入 file_metadata、finally 内 shutdown；异常不泄漏连接。
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock

from GalTransl.Frontend.LLMTranslate import (
    _resolve_after_translation_order,
    _run_after_trans_single_file,
)


def _make_projectConfig(after=None, enable_better=False, enable_improve=None):
    def getKey(key, default=None):
        if key == "gpt.afterTranslation":
            return after
        if key == "gpt.enableBetterTranslation":
            return enable_better
        if key == "internals.pipeline.enableImprove":
            return enable_improve if enable_improve is not None else default
        return default

    return SimpleNamespace(
        getKey=getKey,
        file_metadata={"name": "x"},
        proxyPool=None,
        tokenPool=None,
        gpt_dic={},
    )


class ResolveOrderTests(unittest.TestCase):
    def test_none_explicit_returns_empty(self):
        self.assertEqual(
            _resolve_after_translation_order(_make_projectConfig(after="none")), []
        )

    def test_empty_array_returns_empty(self):
        self.assertEqual(
            _resolve_after_translation_order(_make_projectConfig(after=[])), []
        )

    def test_improve(self):
        self.assertEqual(
            _resolve_after_translation_order(_make_projectConfig(after="improve")),
            ["improve"],
        )

    def test_brfix(self):
        self.assertEqual(
            _resolve_after_translation_order(_make_projectConfig(after="brfix")),
            ["brfix"],
        )

    def test_string_combo_keeps_order(self):
        self.assertEqual(
            _resolve_after_translation_order(_make_projectConfig(after="improve+brfix")),
            ["improve", "brfix"],
        )

    def test_string_combo_reversed_kept(self):
        # 顺序由配置决定，函数只做白名单过滤不重排
        self.assertEqual(
            _resolve_after_translation_order(_make_projectConfig(after="brfix+improve")),
            ["brfix", "improve"],
        )

    def test_array_combo_keeps_order(self):
        self.assertEqual(
            _resolve_after_translation_order(
                _make_projectConfig(after=["improve", "brfix"])
            ),
            ["improve", "brfix"],
        )

    def test_array_reversed_kept(self):
        self.assertEqual(
            _resolve_after_translation_order(
                _make_projectConfig(after=["brfix", "improve"])
            ),
            ["brfix", "improve"],
        )

    def test_array_deduplicates_keeping_first(self):
        self.assertEqual(
            _resolve_after_translation_order(
                _make_projectConfig(after=["improve", "improve", "brfix"])
            ),
            ["improve", "brfix"],
        )

    def test_array_ignores_none_sentinel(self):
        self.assertEqual(
            _resolve_after_translation_order(
                _make_projectConfig(after=["none", "improve"])
            ),
            ["improve"],
        )

    def test_array_ignores_non_string_items(self):
        self.assertEqual(
            _resolve_after_translation_order(
                _make_projectConfig(after=["improve", 123, None, "brfix"])
            ),
            ["improve", "brfix"],
        )

    def test_fallback_enableBetterTranslation_true(self):
        self.assertEqual(
            _resolve_after_translation_order(_make_projectConfig(enable_better=True)),
            ["improve"],
        )

    def test_default_empty_when_absent(self):
        self.assertEqual(
            _resolve_after_translation_order(_make_projectConfig()), []
        )

    def test_invalid_value_falls_back_empty(self):
        self.assertEqual(
            _resolve_after_translation_order(_make_projectConfig(after="foobar")), []
        )

    def test_partial_invalid_filtered(self):
        # 仅保留白名单 token
        self.assertEqual(
            _resolve_after_translation_order(_make_projectConfig(after="improve+bogus")),
            ["improve"],
        )


class RunAfterSingleFileTests(unittest.TestCase):
    def _fake_backend(self, mode):
        inst = MagicMock()
        inst.set_file_metadata = MagicMock()
        inst.batch_translate = AsyncMock()
        inst.shutdown = AsyncMock()
        return inst

    def test_improve_instantiates_correct_backend_and_shutdown(self):
        proj = _make_projectConfig(after="improve")
        improve_inst = self._fake_backend("improve")
        br_inst = self._fake_backend("brfix")

        with patch(
            "GalTransl.Backend.ForImproveTranslation.ForImproveTranslation",
            return_value=improve_inst,
        ) as p_imp, patch(
            "GalTransl.Backend.ForBRStation.ForBRStation",
            return_value=br_inst,
        ):
            asyncio.run(
                _run_after_trans_single_file(
                    "improve", "f.json", "f.json", [SimpleNamespace()],
                    proj, 100,
                )
            )
            p_imp.assert_called_once()
            # 构造参数：projectConfig, eng_type, proxyPool, tokenPool
            args, _ = p_imp.call_args
            self.assertEqual(args[1], "ForImproveTranslation")
            improve_inst.set_file_metadata.assert_called_once()
            self.assertTrue(improve_inst.shutdown.called)

    def test_brfix_instantiates_correct_backend(self):
        proj = _make_projectConfig(after="brfix")
        br_inst = self._fake_backend("brfix")
        with patch(
            "GalTransl.Backend.ForBRStation.ForBRStation",
            return_value=br_inst,
        ) as p_br:
            asyncio.run(
                _run_after_trans_single_file(
                    "brfix", "f.json", "f.json", [SimpleNamespace()],
                    proj, 100,
                )
            )
            p_br.assert_called_once()
            self.assertEqual(p_br.call_args[0][1], "ForBRStation")
            self.assertTrue(br_inst.shutdown.called)

    def test_unknown_mode_no_instantiate(self):
        proj = _make_projectConfig()
        with patch(
            "GalTransl.Backend.ForImproveTranslation.ForImproveTranslation"
        ) as p_imp, patch(
            "GalTransl.Backend.ForBRStation.ForBRStation"
        ) as p_br:
            asyncio.run(
                _run_after_trans_single_file(
                    "bogus", "f.json", "f.json", [SimpleNamespace()],
                    proj, 100,
                )
            )
            p_imp.assert_not_called()
            p_br.assert_not_called()

    def test_shutdown_called_even_on_batch_error(self):
        proj = _make_projectConfig(after="improve")
        inst = self._fake_backend("improve")

        async def _boom(*a, **k):
            raise RuntimeError("llm down")
        inst.batch_translate = _boom

        with patch(
            "GalTransl.Backend.ForImproveTranslation.ForImproveTranslation",
            return_value=inst,
        ):
            # 异常应向外抛出（caller 负责捕获），但 shutdown 必须执行
            with self.assertRaises(RuntimeError):
                asyncio.run(
                    _run_after_trans_single_file(
                        "improve", "f.json", "f.json", [SimpleNamespace()],
                        proj, 100,
                    )
                )
            self.assertTrue(inst.shutdown.called)


if __name__ == "__main__":
    unittest.main()
