"""流式首字状态灯（TTFT）核心逻辑的回归测试。

覆盖：
- set_ttft_state 的状态写入 / 覆盖 / worker 隔离
- 非 worker 上下文（worker_id="-1"）不污染状态灯
- RequestHealthMetrics 的 TTFT 样本统计（avg / p95）
"""

import unittest

from GalTransl.Backend.BaseEngine import RequestHealthMetrics
from GalTransl.server_runtime import (
    RUNTIME_REGISTRY,
    RuntimeState,
    TTFTStatus,
    _normalize_project_dir,
    set_ttft_state,
)


class TTFTPreviewStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pdir = "/proj/ttft-test"
        self.key = _normalize_project_dir(self.pdir)
        RUNTIME_REGISTRY._states[self.key] = RuntimeState(project_dir=self.pdir)

    def tearDown(self) -> None:
        RUNTIME_REGISTRY._states.pop(self.key, None)

    def test_waiting_then_first_token_records_ttft(self) -> None:
        wid = "0"
        set_ttft_state(self.pdir, TTFTStatus.WAITING, worker_id=wid, model="gpt-4o")
        snap = RUNTIME_REGISTRY._states[self.key].ttft_states[wid]
        self.assertEqual(snap.status, TTFTStatus.WAITING)
        self.assertIsNone(snap.ttft_ms)

        set_ttft_state(self.pdir, TTFTStatus.FIRST_TOKEN, worker_id=wid, ttft_ms=820.5)
        snap = RUNTIME_REGISTRY._states[self.key].ttft_states[wid]
        self.assertEqual(snap.status, TTFTStatus.FIRST_TOKEN)
        self.assertAlmostEqual(snap.ttft_ms, 820.5)

    def test_worker_isolation(self) -> None:
        set_ttft_state(self.pdir, TTFTStatus.WAITING, worker_id="0")
        set_ttft_state(self.pdir, TTFTStatus.FIRST_TOKEN, worker_id="1", ttft_ms=300.0)
        states = RUNTIME_REGISTRY._states[self.key].ttft_states
        self.assertEqual(states["0"].status, TTFTStatus.WAITING)
        self.assertEqual(states["1"].status, TTFTStatus.FIRST_TOKEN)
        self.assertAlmostEqual(states["1"].ttft_ms, 300.0)

    def test_idle_reset_clears_ttft(self) -> None:
        # 请求成功后复位 IDLE 须清空 ttft_ms，表示"当前无进行中请求"
        wid = "0"
        set_ttft_state(self.pdir, TTFTStatus.FIRST_TOKEN, worker_id=wid, ttft_ms=820.5)
        set_ttft_state(self.pdir, TTFTStatus.IDLE, worker_id=wid)
        snap = RUNTIME_REGISTRY._states[self.key].ttft_states[wid]
        self.assertEqual(snap.status, TTFTStatus.IDLE)
        self.assertIsNone(snap.ttft_ms)

    def test_non_worker_context_skipped(self) -> None:
        set_ttft_state(self.pdir, TTFTStatus.WAITING, worker_id="-1")
        self.assertNotIn("-1", RUNTIME_REGISTRY._states[self.key].ttft_states)

    def test_unknown_project_no_raise(self) -> None:
        # 不存在的项目目录不应抛异常
        set_ttft_state("/proj/never-created", TTFTStatus.WAITING, worker_id="0")


class TTFTHealthMetricsTests(unittest.TestCase):
    def test_avg_and_p95_ttft(self) -> None:
        metrics = RequestHealthMetrics()
        # 注入 TTFT 样本（秒）：0.1, 0.2, 0.3, 0.4, 0.5, 1.0, 2.0
        for s in (0.1, 0.2, 0.3, 0.4, 0.5, 1.0, 2.0):
            metrics.record(latency_seconds=1.0, is_rate_limited=False, ttft_seconds=s)
        snap = metrics.snapshot(window_seconds=120.0)
        # 7 个样本 (0.1~2.0) 均值 = 4.5/7 ≈ 0.642857
        self.assertAlmostEqual(snap["avg_ttft"], 4.5 / 7, places=6)
        # 7 个样本，p95 取 ceil(0.95*7)=7 → 最大样本 2.0
        self.assertAlmostEqual(snap["p95_ttft"], 2.0, places=6)

    def test_no_ttft_samples_yields_zero(self) -> None:
        metrics = RequestHealthMetrics()
        metrics.record(latency_seconds=1.0, is_rate_limited=False)
        snap = metrics.snapshot(window_seconds=120.0)
        self.assertEqual(snap["avg_ttft"], 0.0)
        self.assertEqual(snap["p95_ttft"], 0.0)


if __name__ == "__main__":
    unittest.main()
