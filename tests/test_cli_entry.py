import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from GalTransl import GALTRANSL_VERSION
from GalTransl.ConfigHelper import detect_config_file
from GalTransl.Service import JobSpec, run_job_async
from GalTransl.__main__ import _resolve_config_file, worker

REPO_ROOT = Path(__file__).resolve().parent.parent


class DetectConfigFileTests(unittest.TestCase):
    def test_inc_yaml_takes_priority_when_both_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            for name in ("config.inc.yaml", "config.yaml"):
                with open(os.path.join(tmp_dir, name), "w", encoding="utf-8") as f:
                    f.write("common: {}\n")
            self.assertEqual(detect_config_file(tmp_dir), "config.inc.yaml")

    def test_falls_back_to_config_yaml_when_only_yaml_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with open(os.path.join(tmp_dir, "config.yaml"), "w", encoding="utf-8") as f:
                f.write("common: {}\n")
            self.assertEqual(detect_config_file(tmp_dir), "config.yaml")

    def test_returns_config_yaml_default_when_neither_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            self.assertEqual(detect_config_file(tmp_dir), "config.yaml")


class ResolveConfigFileTests(unittest.TestCase):
    def test_explicit_config_name_wins_when_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            for name in ("config.inc.yaml", "config.yaml", "custom.yaml"):
                with open(os.path.join(tmp_dir, name), "w", encoding="utf-8") as f:
                    f.write("common: {}\n")
            self.assertEqual(_resolve_config_file(tmp_dir, "custom.yaml"), "custom.yaml")

    def test_explicit_config_with_path_is_normalized_to_file_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with open(os.path.join(tmp_dir, "custom.yaml"), "w", encoding="utf-8") as f:
                f.write("common: {}\n")
            self.assertEqual(
                _resolve_config_file(tmp_dir, "some/dir/custom.yaml"), "custom.yaml"
            )

    def test_explicit_config_missing_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            self.assertIsNone(_resolve_config_file(tmp_dir, "not_exist.yaml"))

    def test_auto_detect_prefers_inc_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            for name in ("config.inc.yaml", "config.yaml"):
                with open(os.path.join(tmp_dir, name), "w", encoding="utf-8") as f:
                    f.write("common: {}\n")
            self.assertEqual(_resolve_config_file(tmp_dir, None), "config.inc.yaml")

    def test_returns_none_when_no_config_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            self.assertIsNone(_resolve_config_file(tmp_dir, None))


def _make_fake_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        projectConfig={"backendSpecific": {}},
        keyValues={},
        runtime_project_dir="",
        get_workers_per_project=lambda: 1,
    )


def _patch_job_env(fake_cfg: SimpleNamespace):
    return [
        patch("GalTransl.Service.CProjectConfig", return_value=fake_cfg),
        patch("GalTransl.Service.load_app_settings", return_value={}),
        patch("GalTransl.Service.run_galtransl", new=AsyncMock(return_value=None)),
        patch("GalTransl.server.reset_runtime_project"),
        patch("GalTransl.server.update_runtime_status"),
    ]


class JobSpecNonInteractiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_spec_sets_cfg_non_interactive_true(self) -> None:
        fake_cfg = _make_fake_cfg()
        spec = JobSpec(
            project_dir="dummy-project",
            config_file_name="config.inc.yaml",
            translator="gpt4",
        )
        patches = _patch_job_env(fake_cfg)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            state = await run_job_async(spec)
        self.assertTrue(state.success)
        self.assertIs(fake_cfg.non_interactive, True)

    async def test_cli_spec_sets_cfg_non_interactive_false(self) -> None:
        fake_cfg = _make_fake_cfg()
        spec = JobSpec(
            project_dir="dummy-project",
            config_file_name="config.yaml",
            translator="gpt4",
            non_interactive=False,
        )
        patches = _patch_job_env(fake_cfg)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            state = await run_job_async(spec)
        self.assertTrue(state.success)
        self.assertIs(fake_cfg.non_interactive, False)


class WorkerEntrypointTests(unittest.TestCase):
    def test_worker_passes_non_interactive_false_by_default(self) -> None:
        fake_cfg = _make_fake_cfg()
        patches = _patch_job_env(fake_cfg)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            success = worker(
                "dummy-project",
                "config.yaml",
                "gpt4",
                show_banner=False,
            )
        self.assertTrue(success)
        self.assertIs(fake_cfg.non_interactive, False)


class CliSmokeTests(unittest.TestCase):
    def _run_cli(self, *cli_args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, "-m", "GalTransl", *cli_args],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_help_lists_config_option_and_translator_descriptions(self) -> None:
        result = self._run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--config", result.stdout)
        self.assertIn("-c CONFIG", result.stdout)
        self.assertIn("ForGal-full-pipeline", result.stdout)
        self.assertIn("available translators", result.stdout)

    def test_version_flag_prints_core_version(self) -> None:
        result = self._run_cli("--version")
        self.assertEqual(result.returncode, 0)
        self.assertIn(GALTRANSL_VERSION, result.stdout)

    def test_missing_config_exits_with_code_1(self) -> None:
        result = self._run_cli(
            "-p", "sampleProject", "-t", "show-plugs", "-c", "not_exist.yaml"
        )
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
