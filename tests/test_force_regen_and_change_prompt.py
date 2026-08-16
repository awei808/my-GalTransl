"""
forceRegen 透传与修复轮 change_prompt 回归测试。

覆盖两类修复（对应审查报告 F1 / F2）：
  1. ForFileMetaData / ForBatchMetaData.batch_translate 的 force_regen 参数：
     - force_regen=False 且缓存存在时直接跳过（不调 LLM）；
     - force_regen=True 时忽略缓存，进入 LLM 调用（此处用解析失败的桩响应，
       仅断言 ask_chatbot 被调用，避免真实写盘）。
  2. 修复轮（ForJPResidue / ForBRStation）经 BaseFixRound._finalize_prompts
     对专用模板重新应用 common.gpt.change_prompt：
     - AdditionalPrompt 前缀拼接、OverwritePrompt 整体替换、"no" 不变。

实例化采用 __new__ + patch 绕过真实网络/OpenCC/项目文件依赖，
不依赖 tempfile，可在受限文件沙箱中运行。
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from GalTransl.Backend.BaseEngine import BaseEngine
from GalTransl.Backend.ForFileMetaData import ForFileMetaData
from GalTransl.Backend.ForBatchMetaData import ForBatchMetaData
from GalTransl.Backend.ForGalJsonMulitChat import ForGalJsonMulitChat
from GalTransl.Backend.ForJPResidue import ForJPResidue
from GalTransl.Backend.ForBRStation import ForBRStation
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_JPREPAIR_PROMPT,
    FORGAL_JSON_BRSTATION_PROMPT,
)


class _FakeLLM:
    """记录调用并返回不可解析文本，使 batch_translate 在解析阶段失败返回 False。"""

    def __init__(self) -> None:
        self.calls: list = []

    async def __call__(self, messages=None, file_name="", max_retry_count=3, **kw):
        self.calls.append(file_name)
        return "this is not valid json", None


class ForceRegenEngineTests(unittest.IsolatedAsyncioTestCase):
    """F1：引擎内缓存命中检查必须感知 force_regen。"""

    def _make_filemeta(self):
        backend = ForFileMetaData.__new__(ForFileMetaData)
        backend.pj_config = SimpleNamespace(
            getCachePath=lambda: "C:/nonexistent-cache",
            getDictCfgSection=lambda: None,
            runtime_project_dir="C:/nonexistent-cache",
            translation_guideline="",
        )
        backend.trans_prompt = (
            "[translation_guideline]\n[Input]\n[Glossary]\n[global_prompt]\n"
            "[plot_metadata]\n[SourceLang]\n[TargetLang]"
        )
        backend._inject_guideline = False
        backend._global_prompt_loaded = True
        backend._global_prompt = None
        backend.source_lang = "ja"
        backend.target_lang = "zh-cn"
        backend.system_prompt = ""
        backend.ask_chatbot = _FakeLLM()
        return backend

    def _make_batchmeta(self):
        backend = ForBatchMetaData.__new__(ForBatchMetaData)
        backend.pj_config = SimpleNamespace(
            getCachePath=lambda: "C:/nonexistent-cache",
            getDictCfgSection=lambda: None,
            runtime_project_dir="C:/nonexistent-cache",
            translation_guideline="",
        )
        backend.trans_prompt = (
            "[translation_guideline]\n[Input]\n[Glossary]\n[global_prompt]\n"
            "[plot_metadata]\n[SourceLang]\n[TargetLang]"
        )
        backend._inject_guideline = False
        backend._global_prompt_loaded = True
        backend._global_prompt = None
        backend._file_metadata_loaded = True
        backend._file_metadata_by_file = {}
        backend.max_batches = 20
        backend.min_batch_size = None
        backend.max_batch_size = None
        backend.source_lang = "ja"
        backend.target_lang = "zh-cn"
        backend.system_prompt = ""
        backend.ask_chatbot = _FakeLLM()
        return backend

    async def test_filemeta_cache_hit_skips_without_force(self) -> None:
        backend = self._make_filemeta()
        with patch("os.path.isfile", return_value=True):
            ok = await backend.batch_translate(
                [{"message": "x"}], filename="f.json", force_regen=False
            )
        self.assertTrue(ok)
        self.assertEqual(backend.ask_chatbot.calls, [])

    async def test_filemeta_force_regen_bypasses_cache(self) -> None:
        backend = self._make_filemeta()
        with patch("os.path.isfile", return_value=True):
            ok = await backend.batch_translate(
                [{"message": "x"}], filename="f.json", force_regen=True
            )
        # 桩响应解析失败 → 返回 False，但 ask_chatbot 已被调用（缓存检查被绕过）
        self.assertFalse(ok)
        self.assertEqual(len(backend.ask_chatbot.calls), 1)

    async def test_batchmeta_cache_hit_skips_without_force(self) -> None:
        backend = self._make_batchmeta()
        with patch("os.path.isfile", return_value=True):
            ok = await backend.batch_translate(
                [{"message": "x"}], filename="f.json", force_regen=False
            )
        self.assertTrue(ok)
        self.assertEqual(backend.ask_chatbot.calls, [])

    async def test_batchmeta_force_regen_bypasses_cache(self) -> None:
        backend = self._make_batchmeta()
        with patch("os.path.isfile", return_value=True):
            ok = await backend.batch_translate(
                [{"message": "x"}], filename="f.json", force_regen=True
            )
        self.assertFalse(ok)
        self.assertEqual(len(backend.ask_chatbot.calls), 1)


class _KeyValueConfig:
    """最小配置桩：仅提供 ForGalJsonMulitChat.__init__ 需要的 getKey。"""

    def __init__(self, values: dict) -> None:
        self._values = values

    def getKey(self, key, default=None):
        return self._values.get(key, default)


class ChangePromptFixRoundTests(unittest.TestCase):
    """F2：gpt.change_prompt 对修复轮专用模板生效（经 _finalize_prompts 重放）。"""

    def _make(self, change_prompt: str, prompt_content: str, cls=ForJPResidue):
        config = _KeyValueConfig({"gpt.enhance_jailbreak": False})
        common = {"gpt.change_prompt": change_prompt, "gpt.prompt_content": prompt_content}

        def _fake_base_init(self, config_, eng_type, proxy_pool=None, token_pool=None):
            self.pj_config = config_

        with patch.object(BaseEngine, "__init__", _fake_base_init), \
             patch.object(BaseEngine, "init_chatbot", lambda self, *a, **k: None), \
             patch.object(
                 BaseEngine, "_apply_internal_prompt_template_overrides",
                 lambda self: None,
             ), \
             patch("GalTransl.ConfigHelper.CProjectConfig.getProjectConfig",
                   return_value={"common": common}):
            t = cls(config, "test-eng", None, None)
        return t

    def test_jpresidue_additional_prompt_prefix(self) -> None:
        t = self._make("AdditionalPrompt", "补充要求内容")
        self.assertTrue(
            t.trans_prompt.startswith("# Additional Requirements: 补充要求内容\n")
        )
        self.assertIn(FORGAL_JSON_JPREPAIR_PROMPT, t.trans_prompt)

    def test_jpresidue_overwrite_prompt(self) -> None:
        t = self._make("OverwritePrompt", "完全替换内容")
        self.assertEqual(t.trans_prompt, "完全替换内容")

    def test_jpresidue_no_change_prompt_keeps_template(self) -> None:
        t = self._make("no", "")
        self.assertEqual(t.trans_prompt, FORGAL_JSON_JPREPAIR_PROMPT)

    def test_brstation_change_prompt_applies(self) -> None:
        t = self._make("AdditionalPrompt", "换行补充要求", cls=ForBRStation)
        self.assertTrue(
            t.trans_prompt.startswith("# Additional Requirements: 换行补充要求\n")
        )
        self.assertIn(FORGAL_JSON_BRSTATION_PROMPT, t.trans_prompt)

    def test_brstation_hook_replaces_br_issue_guide(self) -> None:
        """合并后的 _build_first_round_content 必须经由钩子注入 [br_issue_guide]。"""
        t = self._make("no", "", cls=ForBRStation)
        self.assertEqual(
            t._apply_extra_first_round_replacements("x[br_issue_guide]y"),
            "x" + t._build_br_issue_guide() + "y",
        )


if __name__ == "__main__":
    unittest.main()
