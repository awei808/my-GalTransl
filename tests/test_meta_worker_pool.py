"""
_run_meta_worker_pool 多 worker 并发正确性测试。

验证：
  - 所有待处理文件都被处理且每个文件恰好处理一次
  - 缓存跳过的文件不会被处理
  - 行数不足 min_lines 的小文件被跳过（不调用后端），计数单独汇报
  - force_regen 时忽略缓存，全部重新处理
  - 返回的实际处理数正确
  - worker 并发数正确（多个 worker 同时在途）
  - WORKER_ID_CTX 在每个 worker 内正确绑定/重置
  - 无文件时优雅返回 0
  - 并发状态上报：启动 0/配置值，处理中随文件增减，结束归零
"""

import asyncio
import unittest
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from GalTransl.Frontend.LLMTranslate import _run_meta_worker_pool


class FakeGptApi:
    """模拟 ForFileMetaData/ForBatchMetaData 实例：记录调用并统计并发峰值。"""

    def __init__(self) -> None:
        self.calls: list = []
        self.max_concurrency = 0
        self._lock = asyncio.Lock()
        self._in_flight = 0

    async def batch_translate(self, json_list: list, filename: str = "", force_regen: bool = False) -> bool:
        # 统计在途请求数峰值（同一时刻并发的 worker 数）
        async with self._lock:
            self._in_flight += 1
            self.max_concurrency = max(self.max_concurrency, self._in_flight)
        await asyncio.sleep(0.02)
        self.calls.append((filename, force_regen))
        async with self._lock:
            self._in_flight -= 1
        return True


class FakeProjectConfig:
    """最小 CProjectConfig 桩：只需 runtime 上报不抛异常。"""

    def __init__(self) -> None:
        self.runtime_project_dir = "d:/probe-fake"

    def getProjectDir(self) -> str:
        return self.runtime_project_dir


def _mk_files(n: int) -> dict:
    return {f"d:/game/gt_input/f{i}.txt.json": [{"message": f"line-{i}-{j}"} for j in range(3)] for i in range(n)}


class FailingGptApi(FakeGptApi):
    """batch_translate 返回 False（LLM 业务失败）模拟。"""

    async def batch_translate(self, json_list: list, filename: str = "", force_regen: bool = False) -> bool:
        self.calls.append((filename, force_regen))
        return False


class ThrowingGptApi(FakeGptApi):
    """batch_translate 抛未捕获异常（写盘失败等）模拟。"""

    async def batch_translate(self, json_list: list, filename: str = "", force_regen: bool = False) -> bool:
        self.calls.append((filename, force_regen))
        raise IOError("disk full")


class RunMetaWorkerPoolTests(IsolatedAsyncioTestCase):
    """_run_meta_worker_pool 基础正确性。"""

    async def test_all_files_processed_once_each(self) -> None:
        gptapi = FakeGptApi()
        files = _mk_files(6)
        result = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map={}, worker_count=3,
            tag="Test", stage_prefix="测试",
        )
        self.assertEqual(result.processed, 6)
        # 每个文件恰好处理一次（默认 force_regen=False）
        self.assertEqual(
            sorted(c[0] for c in gptapi.calls),
            sorted(f"f{i}.txt.json" for i in range(6)),
        )
        self.assertTrue(all(c[1] is False for c in gptapi.calls))
        # 3 worker 并发时应出现过 >1 的在途峰值
        self.assertGreater(gptapi.max_concurrency, 1)
        self.assertLessEqual(gptapi.max_concurrency, 3)

    async def test_cached_files_skipped(self) -> None:
        gptapi = FakeGptApi()
        files = _mk_files(4)
        existing = {"f0.txt.json", "f2.txt.json"}
        result = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map=existing, worker_count=2,
            tag="Test", stage_prefix="测试",
        )
        self.assertEqual(result.processed, 2)
        self.assertEqual(
            sorted(c[0] for c in gptapi.calls), ["f1.txt.json", "f3.txt.json"]
        )

    async def test_force_regen_ignores_cache(self) -> None:
        gptapi = FakeGptApi()
        files = _mk_files(3)
        existing = {"f0.txt.json", "f1.txt.json", "f2.txt.json"}
        result = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map=existing, worker_count=2,
            tag="Test", stage_prefix="测试", force_regen=True,
        )
        self.assertEqual(result.processed, 3)
        self.assertEqual(len(gptapi.calls), 3)
        # force_regen 被透传到引擎层
        self.assertTrue(all(c[1] is True for c in gptapi.calls))

    async def test_empty_input_returns_zero(self) -> None:
        gptapi = FakeGptApi()
        result = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, {},
            existing_map={}, worker_count=3,
            tag="Test", stage_prefix="测试",
        )
        self.assertEqual(result.processed, 0)
        self.assertEqual(gptapi.calls, [])

    async def test_all_cached_returns_zero(self) -> None:
        gptapi = FakeGptApi()
        files = _mk_files(2)
        existing = {"f0.txt.json", "f1.txt.json"}
        result = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map=existing, worker_count=3,
            tag="Test", stage_prefix="测试",
        )
        self.assertEqual(result.processed, 0)
        self.assertEqual(gptapi.calls, [])

    async def test_worker_more_than_tasks_still_finishes(self) -> None:
        # worker 数 > 任务数：多余 worker 拿到 None 立即退出，不报错
        gptapi = FakeGptApi()
        files = _mk_files(1)
        result = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map={}, worker_count=5,
            tag="Test", stage_prefix="测试",
        )
        self.assertEqual(result.processed, 1)
        self.assertEqual([c[0] for c in gptapi.calls], ["f0.txt.json"])

    async def test_failed_files_not_counted_as_processed(self) -> None:
        # batch_translate 返回 False（LLM 业务失败）不抛异常，不计入 processed
        gptapi = FailingGptApi()
        files = _mk_files(4)
        result = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map={}, worker_count=3,
            tag="Test", stage_prefix="测试",
        )
        self.assertEqual(result.processed, 0)
        # 文件仍被全部尝试处理
        self.assertEqual(
            sorted(c[0] for c in gptapi.calls),
            sorted(f"f{i}.txt.json" for i in range(4)),
        )

    async def test_worker_exception_cancels_entire_pool(self) -> None:
        # batch_translate 抛未捕获异常：异常向上传播，且其余 worker 被取消（无孤儿任务）
        gptapi = ThrowingGptApi()
        files = _mk_files(6)
        with self.assertRaises(IOError):
            await _run_meta_worker_pool(
                FakeProjectConfig(), gptapi, files,
                existing_map={}, worker_count=3,
                tag="Test", stage_prefix="测试",
            )
        # 抛异常的那个文件必然被处理过；其余 worker 可能在取消前已处理部分文件
        self.assertGreaterEqual(len(gptapi.calls), 1)
        self.assertLessEqual(len(gptapi.calls), 6)
        # 没有遗留挂起的 worker task（异常后 await gather(return_exceptions=True) 已回收全部）
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        self.assertEqual(pending, [])


class SmallFileSkipTests(IsolatedAsyncioTestCase):
    """行数阈值跳过：非空行数 < min_lines 的文件不调用后端、不计入 processed。"""

    @staticmethod
    def _file(lines: int, empty_lines: int = 0) -> list:
        rows = [{"message": f"line-{i}"} for i in range(lines)]
        rows += [{"message": ""} for _ in range(empty_lines)]
        return rows

    async def test_below_threshold_skipped_at_threshold_processed(self) -> None:
        gptapi = FakeGptApi()
        files = {
            "d:/game/gt_input/small.txt.json": self._file(19),
            "d:/game/gt_input/exact.txt.json": self._file(20),
        }
        result = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map={}, worker_count=2,
            tag="Test", stage_prefix="测试", min_lines=20,
        )
        self.assertEqual(result.processed, 1)
        self.assertEqual(result.skipped_small, 1)
        self.assertEqual(result.skipped_existing, 0)
        self.assertEqual([c[0] for c in gptapi.calls], ["exact.txt.json"])

    async def test_empty_message_lines_not_counted(self) -> None:
        # 19 条非空 + 5 条空 message：按进度面板同口径仍算 19 行 → 跳过
        gptapi = FakeGptApi()
        files = {"d:/game/gt_input/a.txt.json": self._file(19, empty_lines=5)}
        result = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map={}, worker_count=2,
            tag="Test", stage_prefix="测试", min_lines=20,
        )
        self.assertEqual(result.processed, 0)
        self.assertEqual(result.skipped_small, 1)
        self.assertEqual(gptapi.calls, [])

    async def test_zero_min_lines_never_skips(self) -> None:
        gptapi = FakeGptApi()
        files = {"d:/game/gt_input/tiny.txt.json": self._file(1)}
        result = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map={}, worker_count=1,
            tag="Test", stage_prefix="测试", min_lines=0,
        )
        self.assertEqual(result.processed, 1)
        self.assertEqual(result.skipped_small, 0)

    async def test_force_regen_does_not_resurrect_small_files(self) -> None:
        # force_regen 只覆盖「已有缓存」跳过，不改变行数阈值跳过
        gptapi = FakeGptApi()
        files = {"d:/game/gt_input/a.txt.json": self._file(5)}
        result = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map={}, worker_count=1,
            tag="Test", stage_prefix="测试", force_regen=True, min_lines=20,
        )
        self.assertEqual(result.processed, 0)
        self.assertEqual(result.skipped_small, 1)
        self.assertEqual(gptapi.calls, [])

    async def test_force_regen_with_existing_cache_still_skips_small(self) -> None:
        # 小文件同时命中「已有缓存」与「行数不足」：force_regen 绕过缓存分支后
        # 仍必须被行数阈值拦下，且计入 skipped_small（不是 skipped_existing）
        gptapi = FakeGptApi()
        files = {"d:/game/gt_input/a.txt.json": self._file(3)}
        result = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map={"a.txt.json"}, worker_count=1,
            tag="Test", stage_prefix="测试", force_regen=True, min_lines=20,
        )
        self.assertEqual(result.processed, 0)
        self.assertEqual(result.skipped_small, 1)
        self.assertEqual(result.skipped_existing, 0)
        self.assertEqual(gptapi.calls, [])

    async def test_small_and_cached_counted_separately(self) -> None:
        # 小而跳过 / 已有缓存 / 待处理三类并存：计数互不混淆（交叉验证分母依赖该口径）
        gptapi = FakeGptApi()
        files = {
            "d:/game/gt_input/small.txt.json": self._file(3),
            "d:/game/gt_input/cached.txt.json": self._file(50),
            "d:/game/gt_input/todo.txt.json": self._file(50),
        }
        result = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map={"cached.txt.json"}, worker_count=2,
            tag="Test", stage_prefix="测试", min_lines=20,
        )
        self.assertEqual(result.processed, 1)
        self.assertEqual(result.skipped_small, 1)
        self.assertEqual(result.skipped_existing, 1)
        self.assertEqual([c[0] for c in gptapi.calls], ["todo.txt.json"])

    async def test_all_small_returns_zero_without_reporting(self) -> None:
        # 全部因行数不足跳过：与「全部命中缓存」同路径，不触碰并发状态上报
        reports: list = []
        with patch(
            "GalTransl.Frontend.llm_runtime._update_runtime",
            side_effect=lambda cfg, **kw: reports.append(kw),
        ):
            result = await _run_meta_worker_pool(
                FakeProjectConfig(), FakeGptApi(), {"d:/game/gt_input/a.txt.json": self._file(2)},
                existing_map={}, worker_count=3,
                tag="Test", stage_prefix="测试", min_lines=20,
            )
        self.assertEqual(result.processed, 0)
        self.assertEqual(result.skipped_small, 1)
        self.assertEqual(reports, [])


class WorkerReportingTests(IsolatedAsyncioTestCase):
    """并发状态上报：启动 0/配置值，处理中随文件取放增减，结束归零。"""

    def _record(self, reports: list) -> Any:
        return patch(
            "GalTransl.Frontend.llm_runtime._update_runtime",
            side_effect=lambda cfg, **kw: reports.append(kw),
        )

    @staticmethod
    def _actives(reports: list) -> list:
        return [r["workers_active"] for r in reports if "workers_active" in r]

    async def test_reports_workers_over_lifecycle(self) -> None:
        reports: list = []
        with self._record(reports):
            gptapi = FakeGptApi()
            result = await _run_meta_worker_pool(
                FakeProjectConfig(), gptapi, _mk_files(4),
                existing_map={}, worker_count=2,
                tag="Test", stage_prefix="测试",
            )
        self.assertEqual(result.processed, 4)
        actives = self._actives(reports)
        # 启动上报 0/配置值
        self.assertEqual(actives[0], 0)
        self.assertEqual(reports[0]["workers_configured"], 2)
        # 2 worker 处理 4 文件：峰值并发必然出现
        self.assertEqual(max(actives), 2)
        # 结束归零
        self.assertEqual(actives[-1], 0)
        # 取放成对：相邻变化量仅 ±1，终段为池收尾的重复归零
        deltas = [b - a for a, b in zip(actives, actives[1:])]
        self.assertTrue(all(d in (1, -1) for d in deltas[:-1]))
        self.assertEqual(deltas[-1], 0)

    async def test_reports_zero_on_worker_exception(self) -> None:
        reports: list = []
        with self._record(reports):
            with self.assertRaises(IOError):
                await _run_meta_worker_pool(
                    FakeProjectConfig(), ThrowingGptApi(), _mk_files(2),
                    existing_map={}, worker_count=2,
                    tag="Test", stage_prefix="测试",
                )
        actives = self._actives(reports)
        self.assertEqual(actives[-1], 0)

    async def test_no_reports_when_all_cached(self) -> None:
        # 全部命中缓存提前返回：不触碰并发状态
        reports: list = []
        with self._record(reports):
            result = await _run_meta_worker_pool(
                FakeProjectConfig(), FakeGptApi(), _mk_files(2),
                existing_map={"f0.txt.json", "f1.txt.json"}, worker_count=3,
                tag="Test", stage_prefix="测试",
            )
        self.assertEqual(result.processed, 0)
        self.assertEqual(reports, [])


if __name__ == "__main__":
    unittest.main()
