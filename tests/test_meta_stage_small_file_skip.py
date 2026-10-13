"""阶段级接线测试：元数据行数阈值跳过（internals.pipeline.metaMinLines）。

覆盖 `_run_stage_file_meta` 的端到端接线（阈值读配置 → 传给 worker 池 → 计数口径）：
- 行数不足的文件不调用后端、不写 pass1 产物；
- 交叉验证分母剔除跳过项，不误报「N 个文件未生成元数据」；
- 配置为 0 时行为回退（全部处理）。
"""
import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

from yaml import safe_load

from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.DefaultProjectConfig import DEFAULT_PROJECT_CONFIG_YAML
from GalTransl.Frontend import llm_pipeline
from GalTransl.server_runtime import RUNTIME_REGISTRY, _normalize_project_dir


def _lines(count: int) -> list:
    return [{"name": "A", "message": f"台词{index}"} for index in range(count)]


class MetaStageSmallFileSkipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "gt_input"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp, "transl_cache", "pass1_cache"), exist_ok=True)
        config = safe_load(DEFAULT_PROJECT_CONFIG_YAML)
        import yaml as _yaml

        with open(os.path.join(self.tmp, "config.yaml"), "w", encoding="utf-8") as f:
            _yaml.safe_dump(config, f, allow_unicode=True)
        self.config = CProjectConfig(self.tmp, "config.yaml")
        # 小文件（5 行）与大文件（30 行）
        self.files = {
            os.path.join(self.tmp, "gt_input", "small.txt.json"): _lines(5),
            os.path.join(self.tmp, "gt_input", "big.txt.json"): _lines(30),
        }

    def tearDown(self) -> None:
        RUNTIME_REGISTRY._states.pop(_normalize_project_dir(self.tmp), None)

    def _run_stage(self) -> tuple[str, list[str]]:
        """跑真实的 _run_stage_file_meta（后端类与模型预检/令牌池被替换），返回 (日志文本, 调用文件名)。"""
        calls: list[str] = []
        config = self.config
        out_dir = os.path.join(config.getCachePath(), "pass1_cache")

        class _FakeFileMetaData:
            """ForFileMetaData 替身：记录调用并像真实后端一样写出 pass1 产物。"""

            def __init__(self, *args, **kwargs) -> None:
                pass

            async def batch_translate(self, json_list, filename: str = "", force_regen: bool = False) -> bool:
                calls.append(filename)
                os.makedirs(out_dir, exist_ok=True)
                with open(os.path.join(out_dir, f"{filename}.meta.json"), "w", encoding="utf-8") as f:
                    json.dump({"id": filename, "角色": ["A"], "剧情": "p"}, f, ensure_ascii=False)
                return True

            async def shutdown(self) -> None:
                return None

        with mock.patch(
            "GalTransl.Backend.ForFileMetaData.ForFileMetaData",
            mock.MagicMock(return_value=_FakeFileMetaData()),
        ), mock.patch.object(
            llm_pipeline, "ensure_model_available_if_needed", mock.AsyncMock()
        ), mock.patch.object(
            llm_pipeline, "_stage_pool", lambda *a, **kw: None
        ), self.assertLogs("GalTransl", level="DEBUG") as captured:
            asyncio.run(
                llm_pipeline._run_stage_file_meta(
                    config, self.files, list(self.files), {}
                )
            )
        return "\n".join(captured.output), calls

    def test_small_file_skipped_without_false_missing_warning(self) -> None:
        logs, calls = self._run_stage()

        # 仅大文件被交给后端
        self.assertEqual(calls, ["big.txt.json"])
        # 小文件无 pass1 产物，大文件有
        self.assertTrue(
            os.path.isfile(
                os.path.join(self.tmp, "transl_cache", "pass1_cache", "big.txt.json.meta.json")
            )
        )
        self.assertFalse(
            os.path.isfile(
                os.path.join(self.tmp, "transl_cache", "pass1_cache", "small.txt.json.meta.json")
            )
        )
        # 跳过按预期行为记录，不产"缺失"告警（分母已剔除跳过项）
        self.assertIn("跳过 1 个行数不足 20 的文件", logs)
        self.assertIn("文件级元数据完成：1/1 个文件", logs)
        self.assertNotIn("文件级元数据警告", logs)
        notices = RUNTIME_REGISTRY.get_runtime_snapshot(self.tmp)["notices"]
        self.assertTrue(
            any("文件级元数据完成（1/1 个文件）" in n for n in notices), notices
        )
        self.assertFalse(any("未生成文件级元数据" in n for n in notices), notices)

    def test_threshold_zero_processes_all_files(self) -> None:
        self.config.keyValues["internals.pipeline.metaMinLines"] = 0

        logs, calls = self._run_stage()

        self.assertEqual(sorted(calls), ["big.txt.json", "small.txt.json"])
        self.assertIn("文件级元数据完成：2/2 个文件", logs)
        self.assertTrue(
            os.path.isfile(
                os.path.join(self.tmp, "transl_cache", "pass1_cache", "small.txt.json.meta.json")
            )
        )

    def _run_batch_stage(self) -> tuple[str, list[str]]:
        """跑真实的 _run_stage_batch_meta（批次级阶段同阈值同口径）。"""
        calls: list[str] = []
        config = self.config
        out_dir = os.path.join(config.getCachePath(), "pass2_cache")

        class _FakeBatchMetaData:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def batch_translate(self, json_list, filename: str = "", force_regen: bool = False) -> bool:
                calls.append(filename)
                os.makedirs(out_dir, exist_ok=True)
                with open(os.path.join(out_dir, f"{filename}.batch.json"), "w", encoding="utf-8") as f:
                    json.dump({"id": filename, "批次": [{"起": 1, "止": 30}]}, f, ensure_ascii=False)
                return True

            async def shutdown(self) -> None:
                return None

        with mock.patch(
            "GalTransl.Backend.ForBatchMetaData.ForBatchMetaData",
            mock.MagicMock(return_value=_FakeBatchMetaData()),
        ), mock.patch.object(
            llm_pipeline, "ensure_model_available_if_needed", mock.AsyncMock()
        ), mock.patch.object(
            llm_pipeline, "_stage_pool", lambda *a, **kw: None
        ), self.assertLogs("GalTransl", level="DEBUG") as captured:
            asyncio.run(
                llm_pipeline._run_stage_batch_meta(
                    config, self.files, list(self.files), {}
                )
            )
        return "\n".join(captured.output), calls

    def test_batch_stage_skips_small_file_without_false_warning(self) -> None:
        logs, calls = self._run_batch_stage()

        self.assertEqual(calls, ["big.txt.json"])
        self.assertFalse(
            os.path.isfile(
                os.path.join(self.tmp, "transl_cache", "pass2_cache", "small.txt.json.batch.json")
            )
        )
        self.assertIn("跳过 1 个行数不足 20 的文件（未划分翻译区间）", logs)
        self.assertIn("翻译区间划分完成：1/1 个文件", logs)
        self.assertNotIn("批次级元数据警告", logs)
        notices = RUNTIME_REGISTRY.get_runtime_snapshot(self.tmp)["notices"]
        self.assertTrue(
            any("翻译区间划分完成（1/1 个文件）" in n for n in notices), notices
        )


if __name__ == "__main__":
    unittest.main()
