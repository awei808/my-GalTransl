"""JobRegistry 历史裁剪与并发上限懒重建的回归测试（M2 / M3）。"""

import threading
import unittest
from unittest import mock

from GalTransl.server import JobRegistry
from GalTransl.Service import create_job_state
from GalTransl.server import JobSpec


def _finished_state(job_id: str, created_at: str, status: str = "completed") -> object:
    spec = JobSpec(job_id=job_id, project_dir=r"C:\tmp\p", translator="ForGal-json-multi-chat")
    state = create_job_state(spec)
    state.status = status
    state.created_at = created_at
    return state


class JobRegistryPruneTests(unittest.TestCase):
    def test_prune_keeps_newest_finished_and_running_jobs(self) -> None:
        registry = JobRegistry(max_workers=1)
        try:
            # 250 条已完成（created_at 递增）+ 1 条运行中（最旧）
            for i in range(250):
                registry._jobs[f"done-{i:03d}"] = _finished_state(
                    f"done-{i:03d}", f"2026-08-01T00:{i // 60:02d}:{i % 60:02d}Z"
                )
            registry._jobs["running-oldest"] = _finished_state(
                "running-oldest", "2026-01-01T00:00:00Z", status="running"
            )
            registry._prune_jobs_locked()

            self.assertLessEqual(len(registry._jobs), registry._MAX_KEPT_JOBS)
            self.assertIn("running-oldest", registry._jobs)  # 运行中永不删除
            self.assertIn("done-249", registry._jobs)  # 最新保留
            self.assertNotIn("done-000", registry._jobs)  # 最旧已删
        finally:
            registry._executor.shutdown(wait=False)

    def test_prune_noop_when_under_limit(self) -> None:
        registry = JobRegistry(max_workers=1)
        try:
            registry._jobs["a"] = _finished_state("a", "2026-08-01T00:00:00Z")
            registry._prune_jobs_locked()
            self.assertIn("a", registry._jobs)
        finally:
            registry._executor.shutdown(wait=False)


class JobRegistryResizeTests(unittest.TestCase):
    def _capture_spec_and_submit(self, registry: JobRegistry, payload: dict, called: threading.Event, captured: dict) -> None:
        def fake_run_job(spec, state, stop_event=None):
            captured["spec"] = spec
            called.set()

        with mock.patch("GalTransl.server.run_job", side_effect=fake_run_job):
            registry.submit(payload)
            self.assertTrue(called.wait(5), "run_job 未被调用")

    def test_executor_lazily_rebuilt_when_max_workers_changed(self) -> None:
        registry = JobRegistry(max_workers=1)
        try:
            # 模拟 PUT /app-settings 更新了数值但 executor 未重建
            registry._max_workers = 3
            self.assertEqual(registry._executor._max_workers, 1)

            called = threading.Event()
            captured = {}
            self._capture_spec_and_submit(
                registry,
                {"project_dir": r"C:\tmp\p1", "translator": "ForGal-json-multi-chat"},
                called,
                captured,
            )
            # executor 已懒重建为新上限，且任务正常提交
            self.assertEqual(registry._executor._max_workers, 3)
            self.assertIsNotNone(captured.get("spec"))
        finally:
            registry._executor.shutdown(wait=True)

    def test_no_rebuild_when_max_workers_unchanged(self) -> None:
        registry = JobRegistry(max_workers=2)
        try:
            old_executor = registry._executor
            called = threading.Event()
            captured = {}
            self._capture_spec_and_submit(
                registry,
                {"project_dir": r"C:\tmp\p2", "translator": "ForGal-json-multi-chat"},
                called,
                captured,
            )
            # 上限未变化：executor 不应被重建
            self.assertIs(registry._executor, old_executor)
        finally:
            registry._executor.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
