"""TTFT 状态灯的状态流转测试（server_runtime.set_ttft_state）：

- 成功链路 WAITING → FIRST_TOKEN → COMPLETED：ttft_ms/model 沿用继承逻辑保留，
  供前端展示「已完成 + 耗时」；
- IDLE 守卫保留：显式复位 IDLE 仍强制清空 ttft_ms/model（成功分支已改发 COMPLETED，
  后端当前不再发 IDLE，锁守卫防未来调用方写入脏数据）；
- 非 worker 上下文（worker_id 缺省/空/"-1"）不写入。

回归背景：成功分支原为「复位 IDLE 并清空 ttft_ms」，前端黑灯无文字也无耗时；
改为 COMPLETED 保留 ttft_ms 后，前端才能显示「已完成 · 首字 Xms」。
"""
import tempfile
import unittest

from GalTransl.server_runtime import (
    RUNTIME_REGISTRY,
    _normalize_project_dir,
    set_ttft_state,
)


class _RegistryIsolation:
    """临时项目注册与回收的公共骨架，避免污染全局 RUNTIME_REGISTRY。"""

    def track(self, tmpdir: str):
        key = _normalize_project_dir(tmpdir)
        self.addCleanup(RUNTIME_REGISTRY._states.pop, key, None)
        return RUNTIME_REGISTRY.ensure_project(tmpdir)


class TtftStateFlowTests(_RegistryIsolation, unittest.TestCase):
    """set_ttft_state 的状态流转与继承口径。"""

    def _snapshot_ttft(self, tmpdir: str, worker_id: str) -> dict:
        snapshot = RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir)
        return snapshot["ttft_states"][worker_id]

    def test_completed_keeps_ttft_and_model(self) -> None:
        """成功链路：COMPLETED 不带 ttft_ms/model 时继承 FIRST_TOKEN 写入的值。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            set_ttft_state(tmpdir, "WAITING", worker_id="0", model="model-a")
            set_ttft_state(tmpdir, "FIRST_TOKEN", worker_id="0", ttft_ms=1234.0, model="model-a")
            # BaseEngine 成功分支即此调用形态：不传 ttft_ms/model
            set_ttft_state(tmpdir, "COMPLETED", worker_id="0")
            ttft = self._snapshot_ttft(tmpdir, "0")
            self.assertEqual(ttft["status"], "COMPLETED")
            self.assertEqual(ttft["ttft_ms"], 1234.0)
            self.assertEqual(ttft["model"], "model-a")

    def test_completed_with_explicit_ttft_overrides(self) -> None:
        """显式提供 ttft_ms 时用新值（继承仅在未提供时生效）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            set_ttft_state(tmpdir, "FIRST_TOKEN", worker_id="0", ttft_ms=1000.0)
            set_ttft_state(tmpdir, "COMPLETED", worker_id="0", ttft_ms=2000.0)
            ttft = self._snapshot_ttft(tmpdir, "0")
            self.assertEqual(ttft["status"], "COMPLETED")
            self.assertEqual(ttft["ttft_ms"], 2000.0)

    def test_idle_still_clears_ttft_and_model(self) -> None:
        """IDLE 守卫：显式复位仍强制清空，传入的 ttft_ms/model 不得残留。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            set_ttft_state(tmpdir, "FIRST_TOKEN", worker_id="0", ttft_ms=1234.0, model="model-a")
            set_ttft_state(tmpdir, "IDLE", worker_id="0", ttft_ms=9999.0, model="model-b")
            ttft = self._snapshot_ttft(tmpdir, "0")
            self.assertEqual(ttft["status"], "IDLE")
            self.assertIsNone(ttft["ttft_ms"])
            self.assertEqual(ttft["model"], "")

    def test_non_worker_context_not_written(self) -> None:
        """公共上下文不写入：worker_id 缺省时回退 WORKER_ID_CTX（默认 "-1"），一并拦截。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            set_ttft_state(tmpdir, "WAITING")  # 缺省 → WORKER_ID_CTX 默认 "-1"
            set_ttft_state(tmpdir, "WAITING", worker_id="")
            set_ttft_state(tmpdir, "WAITING", worker_id="-1")
            snapshot = RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir)
            self.assertEqual(snapshot["ttft_states"], {})


if __name__ == "__main__":
    unittest.main()
