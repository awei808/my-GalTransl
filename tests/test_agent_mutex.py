"""agent 会话与翻译任务双向互斥的单测。

覆盖：
- JobRegistry.submit 在 agent 会话进行中拒绝提交（反向互斥）
- handle_agent_chat 在翻译任务运行中返回 409（正向互斥）
- 同项目 agent 会话单飞 409，会话结束（含异常路径）后恢复

LLM/后端解析通过打桩短路，不访问网络。
"""
import threading
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from GalTransl.server_agent import (
    _AGENT_BUSY,
    _AGENT_BUSY_LOCK,
    _SuggestConfigError,
    handle_agent_chat,
    is_agent_busy,
)
from GalTransl.server_jobs import JobRegistry


class _FakeHandler:
    """记录 _send_json 调用的最小 handler 桩。"""

    def __init__(self) -> None:
        self.calls: list = []

    def _send_json(self, payload, status=200):  # noqa: ANN001
        self.calls.append((status, payload))


class _RunningRegistry:
    """模拟「该项目有翻译任务在跑」的 registry 桩。"""

    @staticmethod
    def _has_running_job_for_project(project_dir: str) -> bool:
        return True


class _IdleRegistry:
    @staticmethod
    def _has_running_job_for_project(project_dir: str) -> bool:
        return False


class AgentTaskMutexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project_dir = tempfile.mkdtemp(prefix="gt_agent_mutex_")
        self.busy_key = str(Path(self.project_dir).resolve())
        self.addCleanup(self._cleanup_busy)

    def _cleanup_busy(self) -> None:
        with _AGENT_BUSY_LOCK:
            _AGENT_BUSY.discard(self.busy_key)

    def _mark_busy(self) -> None:
        with _AGENT_BUSY_LOCK:
            _AGENT_BUSY.add(self.busy_key)

    def test_is_agent_busy_matches_registered_key(self) -> None:
        self.assertFalse(is_agent_busy(self.project_dir))
        self._mark_busy()
        self.assertTrue(is_agent_busy(self.project_dir))

    def test_submit_rejected_while_agent_busy(self) -> None:
        self._mark_busy()
        registry = JobRegistry()
        with self.assertRaises(ValueError) as ctx:
            registry.submit(
                {
                    "project_dir": self.project_dir,
                    "translator": "ForGal-json-translate",
                }
            )
        self.assertIn("Agent", str(ctx.exception))
        # 提交被拒后不得残留任务记录
        self.assertEqual(registry.get_project_job(self.project_dir), None)

    def test_submit_allowed_when_agent_idle(self) -> None:
        # 打桩 run_job：验证 submit 确实把任务交给 executor；called 事件确认打桩被调用
        called = threading.Event()

        def fake_run(spec, state, stop_event=None):  # noqa: ANN001
            called.set()
            return state

        registry = JobRegistry()
        try:
            with mock.patch("GalTransl.server_jobs.run_job", side_effect=fake_run) as fake_run_mock:
                state = registry.submit(
                    {
                        "project_dir": self.project_dir,
                        "translator": "ForGal-json-translate",
                    }
                )
                self.assertEqual(state["status"], "pending")
                self.assertTrue(called.wait(timeout=5), "run_job 未被 executor 调用")
                self.assertGreater(fake_run_mock.call_count, 0)
        finally:
            registry._executor.shutdown(wait=True)

    def test_agent_chat_conflicts_while_job_running(self) -> None:
        handler = _FakeHandler()
        handle_agent_chat(handler, _RunningRegistry(), self.project_dir, {"message": "你好"})
        status, payload = handler.calls[0]
        self.assertEqual(status, 409)
        self.assertIn("翻译任务运行中", payload["error"])
        # 被拒的会话不占用单飞登记
        self.assertFalse(is_agent_busy(self.project_dir))

    def test_agent_chat_requires_message(self) -> None:
        handler = _FakeHandler()
        handle_agent_chat(handler, _IdleRegistry(), self.project_dir, {"message": "  "})
        status, payload = handler.calls[0]
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "message required")

    def test_agent_chat_single_flight_then_release(self) -> None:
        started = threading.Event()
        gate = threading.Event()

        def fake_resolve(payload, project_dir):  # noqa: ANN001
            started.set()
            gate.wait(timeout=5)
            raise _SuggestConfigError("no backend")

        handler1 = _FakeHandler()
        thread = threading.Thread(
            target=handle_agent_chat,
            args=(handler1, _IdleRegistry(), self.project_dir, {"message": "第一条"}),
            daemon=True,
        )
        with mock.patch("GalTransl.server_agent._resolve_suggest_backend", fake_resolve):
            thread.start()
            self.assertTrue(started.wait(timeout=5))
            # 会话进行中：同项目第二个请求 409
            handler2 = _FakeHandler()
            handle_agent_chat(handler2, _IdleRegistry(), self.project_dir, {"message": "第二条"})
            status2, payload2 = handler2.calls[0]
            self.assertEqual(status2, 409)
            self.assertIn("会话进行中", payload2["error"])
            # 放行第一条会话（走异常路径收尾）
            gate.set()
            thread.join(timeout=5)
        status1, _ = handler1.calls[0]
        self.assertEqual(status1, 400)
        # 会话异常结束后单飞登记释放：下一个请求不再 409（而是走到配置错误 400）
        handler3 = _FakeHandler()
        with mock.patch(
            "GalTransl.server_agent._resolve_suggest_backend",
            lambda payload, project_dir: (_ for _ in ()).throw(_SuggestConfigError("no backend")),
        ):
            handle_agent_chat(handler3, _IdleRegistry(), self.project_dir, {"message": "第三条"})
        status3, _ = handler3.calls[0]
        self.assertEqual(status3, 400)
        self.assertFalse(is_agent_busy(self.project_dir))


if __name__ == "__main__":
    unittest.main()
