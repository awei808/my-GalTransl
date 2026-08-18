"""RuntimeRegistry 过期项目状态清理的回归测试（M6）。"""

import unittest
from datetime import datetime, timedelta

from GalTransl.server_runtime import RuntimeRegistry, RuntimeState, _utcnow_text, _normalize_project_dir


class RuntimeRegistryPruneTests(unittest.TestCase):
    def test_prune_removes_only_stale_states_over_limit(self) -> None:
        registry = RuntimeRegistry()
        # 塞入超过数量上限的状态，updated_at 均为 3 天前（超过 24h 阈值）
        stale_ts = (datetime.utcnow() - timedelta(days=3)).isoformat(timespec="seconds") + "Z"
        for i in range(registry._MAX_STATES + 10):
            state = RuntimeState(project_dir=f"/proj/stale-{i}")
            state.updated_at = stale_ts
            registry._states[_normalize_project_dir(f"/proj/stale-{i}")] = state

        # 新项目 ensure_project 触发清理
        fresh = registry.ensure_project("/proj/fresh")
        fresh.updated_at = _utcnow_text()

        fresh_key = _normalize_project_dir("/proj/fresh")
        self.assertLessEqual(len(registry._states), registry._MAX_STATES + 1)
        self.assertIn(fresh_key, registry._states)
        # 清理发生在 fresh 加入前：stale 被收敛到上限以内（fresh 占 1 个名额后 stale ≤ 上限）
        stale_prefix = _normalize_project_dir("/proj/stale-")
        stale_remaining = [n for n in registry._states if n.startswith(stale_prefix)]
        self.assertLessEqual(len(stale_remaining), registry._MAX_STATES)

    def test_no_prune_when_under_limit(self) -> None:
        registry = RuntimeRegistry()
        for i in range(5):
            registry.ensure_project(f"/proj/p{i}")
        registry.ensure_project("/proj/p-new")
        self.assertEqual(len(registry._states), 6)


if __name__ == "__main__":
    unittest.main()
