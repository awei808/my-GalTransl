"""工具引擎（recheck / check-batch-size / build-output）分派与执行测试。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import orjson

from GalTransl.UtilityEngines import (
    UTILITY_ENGINES,
    is_utility_engine,
    run_utility_engine,
)
from GalTransl.server import _check_batch_size


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))


def _read_json(path: str):
    with open(path, "rb") as f:
        return orjson.loads(f.read())


def _write_config(proj: str, text: str = "common: {}\n") -> None:
    with open(os.path.join(proj, "config.yaml"), "w", encoding="utf-8") as f:
        f.write(text)


class UtilityEngineDispatchTests(unittest.TestCase):
    def test_membership(self) -> None:
        for name in UTILITY_ENGINES:
            self.assertTrue(is_utility_engine(name))
        self.assertFalse(is_utility_engine("ForGal-full-pipeline"))
        self.assertFalse(is_utility_engine(""))

    def test_unknown_engine_raises(self) -> None:
        with self.assertRaises(ValueError):
            run_utility_engine(SimpleNamespace(), "not-a-engine")


class CheckBatchSizeTests(unittest.TestCase):
    def test_reports_oversize_files(self) -> None:
        with tempfile.TemporaryDirectory() as proj:
            _write_config(
                proj,
                "internals:\n  forbatchmeta:\n    max_batch_size: 10\n    max_batches: 10\n",
            )
            # 0.9 × 10 × 10 = 90 行
            _write_json(
                os.path.join(proj, "gt_input", "big.json"),
                [{"message": f"line-{i}"} for i in range(95)],
            )
            _write_json(
                os.path.join(proj, "gt_input", "small.json"),
                [{"message": f"line-{i}"} for i in range(5)],
            )
            result = _check_batch_size(proj, "config.yaml")
            self.assertTrue(result["applicable"])
            self.assertEqual(result["max_natural_lines"], 90)
            self.assertEqual([o["filename"] for o in result["oversize_files"]], ["big.json"])
            self.assertEqual(result["oversize_files"][0]["lines"], 95)

    def test_utility_engine_runs_check_batch_size(self) -> None:
        with tempfile.TemporaryDirectory() as proj:
            _write_config(proj)
            _write_json(os.path.join(proj, "gt_input", "a.json"), [{"message": "x"}])
            cfg = SimpleNamespace(getProjectDir=lambda: proj, config_name="config.yaml")
            # 不抛异常即通过（结果经 LOGGER 输出）
            run_utility_engine(cfg, "check-batch-size")


class BuildOutputEngineTests(unittest.TestCase):
    def test_builds_output_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as proj:
            _write_config(proj)
            _write_json(
                os.path.join(proj, "gt_input", "demo.json"),
                [{"name": "织姫", "message": "「こんにちは」"}],
            )
            _write_json(
                os.path.join(proj, "transl_cache", "pass3_cache", "demo.json"),
                [
                    {
                        "index": 1,
                        "name": "织姫",
                        "pre_src": "「こんにちは」",
                        "post_src": "「こんにちは」",
                        "pre_dst": "你好",
                        "proofread_dst": "你好",
                        "trans_by": "m",
                        "proofread_by": "",
                    }
                ],
            )
            cfg = SimpleNamespace(getProjectDir=lambda: proj)
            run_utility_engine(cfg, "build-output")
            out_path = os.path.join(proj, "gt_output", "demo.json")
            self.assertTrue(os.path.isfile(out_path))
            out = _read_json(out_path)
            self.assertEqual(out[0]["message"], "「你好」")
            self.assertEqual(out[0]["name"], "织姫")

    def test_missing_input_dir_raises(self) -> None:
        with tempfile.TemporaryDirectory() as proj:
            _write_config(proj)
            cfg = SimpleNamespace(getProjectDir=lambda: proj)
            with self.assertRaises(RuntimeError):
                run_utility_engine(cfg, "build-output")


class RecheckEngineTests(unittest.TestCase):
    def test_recheck_runs_and_writes_back(self) -> None:
        with tempfile.TemporaryDirectory() as proj:
            _write_config(proj)
            cache_dir = os.path.join(proj, "transl_cache")
            entries = [
                {
                    "index": 1,
                    "name": "",
                    "pre_src": "あ",
                    "post_src": "あ",
                    "pre_dst": "你",
                    "proofread_dst": "",
                    "trans_by": "m",
                    "proofread_by": "",
                }
            ]
            _write_json(os.path.join(cache_dir, "pass3_cache", "demo.json"), entries)

            detection = [
                {"index": 1, "problem": "测试问题", "post_dst_preview": "你", "skip_check": False}
            ]
            cfg = SimpleNamespace(
                getProjectDir=lambda: proj,
                getCachePath=lambda: cache_dir,
                config_name="config.yaml",
            )
            # _load_rebuild_deps 由 UtilityEngines 经 `from GalTransl.server import` 调用
            # → patch 打 server 命名空间；_run_problem_detection 由 server_cache 内的
            # recheck_pass3_cache_files 调用 → patch 必须打 server_cache（否则静默打空）。
            with patch(
                "GalTransl.server._load_rebuild_deps",
                return_value=(SimpleNamespace(), None, None, None, [], [], []),
            ), patch(
                "GalTransl.server_cache._run_problem_detection",
                return_value=(detection, True),
            ):
                run_utility_engine(cfg, "recheck")

            written = _read_json(os.path.join(cache_dir, "pass3_cache", "demo.json"))
            self.assertEqual(written[0]["problem"], "测试问题")
            self.assertEqual(written[0]["post_dst_preview"], "你")


if __name__ == "__main__":
    unittest.main()
