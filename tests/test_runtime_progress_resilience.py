"""reset 沿用旧 file_totals，避免轮询窗口内 total=0 致进度条消失。"""
import tempfile
import unittest

from GalTransl.server_runtime import RuntimeRegistry, _normalize_project_dir


class RuntimeProgressResilienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.reg = RuntimeRegistry()

    def _sealed(self) -> bool:
        return self.reg._states[_normalize_project_dir(self.tmp)].progress_sealed

    def test_reset_preserves_previous_file_totals(self) -> None:
        # 首轮重建完成后写入 file_totals，再次 reset（下一轮开始）应沿用而非清空
        self.reg.update_status(
            self.tmp,
            file_totals={"a.json": 10, "b.json": 20},
            cache_file_display_map={"a.json": "a"},
        )
        state_before = self.reg.get_runtime_snapshot(self.tmp)
        self.assertEqual(state_before["file_totals"], {"a.json": 10, "b.json": 20})
        self.assertTrue(self._sealed())

        # 下一轮开始触发 reset
        self.reg.reset_project(self.tmp)
        state_after = self.reg.get_runtime_snapshot(self.tmp)
        self.assertEqual(state_after["file_totals"], {"a.json": 10, "b.json": 20})
        self.assertEqual(state_after["cache_file_display_map"], {"a.json": "a"})
        # 沿用期间视为未封口（重建中）
        self.assertFalse(self._sealed())

    def test_fresh_project_reset_has_empty_totals(self) -> None:
        # 首次 reset（无旧 state）应保持空白 total
        self.reg.reset_project(self.tmp)
        state = self.reg.get_runtime_snapshot(self.tmp)
        self.assertEqual(state["file_totals"], {})
        self.assertTrue(self._sealed())

    def test_get_progress_uses_carried_totals(self) -> None:
        # 沿用旧 totals 后，即便尚未收到新 file_totals，snapshot 仍提供非零 total
        self.reg.update_status(self.tmp, file_totals={"x.json": 5})
        self.reg.reset_project(self.tmp)
        snapshot = self.reg.get_runtime_snapshot(self.tmp)
        total = sum(snapshot["file_totals"].values())
        self.assertEqual(total, 5)
        # 收到新 totals 后重新封口
        self.reg.update_status(self.tmp, file_totals={"x.json": 8, "y.json": 2})
        sealed = self.reg.get_runtime_snapshot(self.tmp)
        self.assertTrue(self._sealed())
        self.assertEqual(sum(sealed["file_totals"].values()), 10)


if __name__ == "__main__":
    unittest.main()
