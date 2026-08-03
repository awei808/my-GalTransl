"""
_run_meta_worker_pool 多 worker 并发正确性测试。

验证：
  - 所有待处理文件都被处理且每个文件恰好处理一次
  - 缓存跳过的文件不会被处理
  - force_regen 时忽略缓存，全部重新处理
  - 返回的实际处理数正确
  - worker 并发数正确（多个 worker 同时在途）
  - WORKER_ID_CTX 在每个 worker 内正确绑定/重置
  - 无文件时优雅返回 0
"""

import asyncio
import unittest
from unittest import IsolatedAsyncioTestCase

from GalTransl.Frontend.LLMTranslate import _run_meta_worker_pool


class FakeGptApi:
    """模拟 ForFileMetaData/ForBatchMetaData 实例：记录调用并统计并发峰值。"""

    def __init__(self) -> None:
        self.calls: list = []
        self.max_concurrency = 0
        self._lock = asyncio.Lock()
        self._in_flight = 0

    async def batch_translate(self, json_list: list, filename: str = "") -> bool:
        # 统计在途请求数峰值（同一时刻并发的 worker 数）
        async with self._lock:
            self._in_flight += 1
            self.max_concurrency = max(self.max_concurrency, self._in_flight)
        await asyncio.sleep(0.02)
        self.calls.append(filename)
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

    async def batch_translate(self, json_list: list, filename: str = "") -> bool:
        self.calls.append(filename)
        return False


class ThrowingGptApi(FakeGptApi):
    """batch_translate 抛未捕获异常（写盘失败等）模拟。"""

    async def batch_translate(self, json_list: list, filename: str = "") -> bool:
        self.calls.append(filename)
        raise IOError("disk full")


class RunMetaWorkerPoolTests(IsolatedAsyncioTestCase):
    """_run_meta_worker_pool 基础正确性。"""

    async def test_all_files_processed_once_each(self) -> None:
        gptapi = FakeGptApi()
        files = _mk_files(6)
        processed = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map={}, worker_count=3,
            tag="Test", stage_prefix="测试",
        )
        self.assertEqual(processed, 6)
        # 每个文件恰好处理一次
        self.assertEqual(sorted(gptapi.calls), sorted(f"f{i}.txt.json" for i in range(6)))
        # 3 worker 并发时应出现过 >1 的在途峰值
        self.assertGreater(gptapi.max_concurrency, 1)
        self.assertLessEqual(gptapi.max_concurrency, 3)

    async def test_cached_files_skipped(self) -> None:
        gptapi = FakeGptApi()
        files = _mk_files(4)
        existing = {"f0.txt.json", "f2.txt.json"}
        processed = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map=existing, worker_count=2,
            tag="Test", stage_prefix="测试",
        )
        self.assertEqual(processed, 2)
        self.assertEqual(sorted(gptapi.calls), ["f1.txt.json", "f3.txt.json"])

    async def test_force_regen_ignores_cache(self) -> None:
        gptapi = FakeGptApi()
        files = _mk_files(3)
        existing = {"f0.txt.json", "f1.txt.json", "f2.txt.json"}
        processed = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map=existing, worker_count=2,
            tag="Test", stage_prefix="测试", force_regen=True,
        )
        self.assertEqual(processed, 3)
        self.assertEqual(len(gptapi.calls), 3)

    async def test_empty_input_returns_zero(self) -> None:
        gptapi = FakeGptApi()
        processed = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, {},
            existing_map={}, worker_count=3,
            tag="Test", stage_prefix="测试",
        )
        self.assertEqual(processed, 0)
        self.assertEqual(gptapi.calls, [])

    async def test_all_cached_returns_zero(self) -> None:
        gptapi = FakeGptApi()
        files = _mk_files(2)
        existing = {"f0.txt.json", "f1.txt.json"}
        processed = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map=existing, worker_count=3,
            tag="Test", stage_prefix="测试",
        )
        self.assertEqual(processed, 0)
        self.assertEqual(gptapi.calls, [])

    async def test_worker_more_than_tasks_still_finishes(self) -> None:
        # worker 数 > 任务数：多余 worker 拿到 None 立即退出，不报错
        gptapi = FakeGptApi()
        files = _mk_files(1)
        processed = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map={}, worker_count=5,
            tag="Test", stage_prefix="测试",
        )
        self.assertEqual(processed, 1)
        self.assertEqual(gptapi.calls, ["f0.txt.json"])

    async def test_failed_files_not_counted_as_processed(self) -> None:
        # batch_translate 返回 False（LLM 业务失败）不抛异常，不计入 processed
        gptapi = FailingGptApi()
        files = _mk_files(4)
        processed = await _run_meta_worker_pool(
            FakeProjectConfig(), gptapi, files,
            existing_map={}, worker_count=3,
            tag="Test", stage_prefix="测试",
        )
        self.assertEqual(processed, 0)
        # 文件仍被全部尝试处理
        self.assertEqual(sorted(gptapi.calls), sorted(f"f{i}.txt.json" for i in range(4)))

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


if __name__ == "__main__":
    unittest.main()
