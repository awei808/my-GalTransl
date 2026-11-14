# -*- coding: utf-8 -*-
"""修复报告注册表（server_runtime）单元测试。

覆盖：
  - add_fix_change：按文件聚合、entries 按 index 排序、无运行态/已收尾时拒绝
  - finish_fix_report：置结束时间与 label、total=0 也写文件、无运行态时静默
  - 明细上限：per-file / total 超限截断，count 保持精确
  - record_fix_change / load_fix_report_file：模块级封装的 best-effort 语义
"""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock, patch

from GalTransl.server_runtime import (
    FIX_REPORT_PER_FILE_LIMIT,
    FIX_REPORT_TOTAL_LIMIT,
    RUNTIME_REGISTRY,
    _normalize_project_dir,
    finish_project_fix_report,
    load_fix_report_file,
    record_fix_change,
)


def _entry(filename="a.json", index=1, **over):
    entry = {
        "filename": filename,
        "index": index,
        "speaker": "",
        "post_src": "src",
        "old_dst": "旧",
        "new_dst": "新",
        "problems": "",
        "backend": "ForImproveTranslation",
        "writes_main": False,
    }
    entry.update(over)
    return entry


def _fake_config(project_dir):
    """record_fix_change 所需的最小配置替身（runtime_project_dir 优先口径）。"""
    return mock.Mock(runtime_project_dir=project_dir)


class FixReportRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project_dir = tempfile.mkdtemp(prefix="fix_report_reg_")
        # 模拟任务已启动：reset 后 RuntimeState 存在且报告为空
        RUNTIME_REGISTRY.reset_project(self.project_dir)

    def tearDown(self) -> None:
        # 测试专用清理：移除本用例的项目运行态，避免临时目录残留
        RUNTIME_REGISTRY._states.pop(_normalize_project_dir(self.project_dir), None)

    def test_add_and_snapshot_groups_and_sorts(self) -> None:
        record_fix_change(_fake_config(self.project_dir), _entry("b.json", index=2))
        record_fix_change(_fake_config(self.project_dir), _entry("a.json", index=5))
        record_fix_change(_fake_config(self.project_dir), _entry("b.json", index=1))
        snap = RUNTIME_REGISTRY.get_fix_report_snapshot(self.project_dir)
        self.assertEqual(snap["total"], 3)
        self.assertEqual(snap["finished_at"], "")
        self.assertEqual([f["filename"] for f in snap["files"]], ["a.json", "b.json"])
        by_file = {f["filename"]: f for f in snap["files"]}
        self.assertEqual(by_file["a.json"]["count"], 1)
        self.assertEqual(by_file["b.json"]["count"], 2)
        self.assertEqual([e["index"] for e in by_file["b.json"]["entries"]], [1, 2])

    def test_snapshot_entry_fields_preserved(self) -> None:
        record_fix_change(
            _fake_config(self.project_dir),
            _entry(index=3, speaker="爱丽丝", old_dst="旧译", new_dst="新译",
                   problems="残留日文, 用词不当", writes_main=True),
        )
        entry = RUNTIME_REGISTRY.get_fix_report_snapshot(self.project_dir)["files"][0]["entries"][0]
        self.assertEqual(entry["speaker"], "爱丽丝")
        self.assertEqual(entry["old_dst"], "旧译")
        self.assertEqual(entry["new_dst"], "新译")
        self.assertEqual(entry["problems"], "残留日文, 用词不当")
        self.assertTrue(entry["writes_main"])

    def test_add_rejected_after_finish(self) -> None:
        record_fix_change(_fake_config(self.project_dir), _entry(index=1))
        RUNTIME_REGISTRY.finish_fix_report(self.project_dir, label="ForImproveTranslation")
        record_fix_change(_fake_config(self.project_dir), _entry(index=2))
        snap = RUNTIME_REGISTRY.get_fix_report_snapshot(self.project_dir)
        self.assertEqual(snap["total"], 1)
        self.assertEqual(snap["label"], "ForImproveTranslation")
        self.assertNotEqual(snap["finished_at"], "")

    def test_add_without_running_state_is_silent_noop(self) -> None:
        # CLI 直跑后端（无任务）不产生孤儿记录
        orphan_dir = tempfile.mkdtemp(prefix="fix_report_orphan_")
        try:
            record_fix_change(_fake_config(orphan_dir), _entry())
            self.assertIsNone(RUNTIME_REGISTRY.get_fix_report_snapshot(orphan_dir))
        finally:
            RUNTIME_REGISTRY._states.pop(_normalize_project_dir(orphan_dir), None)

    def test_finish_without_state_is_silent(self) -> None:
        empty_dir = tempfile.mkdtemp(prefix="fix_report_empty_")
        finish_project_fix_report(empty_dir, label="x")  # 不得抛异常、不写文件
        self.assertFalse(
            os.path.exists(os.path.join(empty_dir, "transl_cache", "fix_report"))
        )

    def test_finish_writes_file_even_when_empty(self) -> None:
        # total=0 也落盘：明确"本次无修复"，避免回退读到陈旧报告
        finish_project_fix_report(self.project_dir, label="ForGal-full-pipeline")
        path = os.path.join(
            self.project_dir, "transl_cache", "fix_report", "latest.json"
        )
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["label"], "ForGal-full-pipeline")
        self.assertEqual(load_fix_report_file(self.project_dir), data)

    def test_finish_without_label_keeps_existing(self) -> None:
        RUNTIME_REGISTRY.finish_fix_report(self.project_dir, label="保持")
        RUNTIME_REGISTRY.finish_fix_report(self.project_dir)  # 二次收尾不覆盖 label
        snap = RUNTIME_REGISTRY.get_fix_report_snapshot(self.project_dir)
        self.assertEqual(snap["label"], "保持")

    def test_per_file_cap_truncates_but_counts_precise(self) -> None:
        with mock.patch.object(
            _registry_module(), "FIX_REPORT_PER_FILE_LIMIT", 3
        ):
            for i in range(1, 6):
                record_fix_change(
                    _fake_config(self.project_dir), _entry(index=i)
                )
        snap = RUNTIME_REGISTRY.get_fix_report_snapshot(self.project_dir)
        group = snap["files"][0]
        self.assertEqual(group["count"], 5)  # 计数精确
        self.assertEqual(len(group["entries"]), 3)  # 明细截断到上限
        self.assertTrue(group["truncated"])
        self.assertTrue(snap["truncated"])
        # 保留的是按 index 排序后的前 3 条（1/2/3）
        self.assertEqual([e["index"] for e in group["entries"]], [1, 2, 3])

    def test_total_cap_truncates_across_files(self) -> None:
        with mock.patch.object(_registry_module(), "FIX_REPORT_TOTAL_LIMIT", 4):
            for i in range(1, 6):
                record_fix_change(
                    _fake_config(self.project_dir),
                    _entry(filename=f"f{i % 2}.json", index=i),
                )
        snap = RUNTIME_REGISTRY.get_fix_report_snapshot(self.project_dir)
        self.assertEqual(snap["total"], 5)
        stored = sum(len(f["entries"]) for f in snap["files"])
        self.assertEqual(stored, 4)
        self.assertTrue(snap["truncated"])

    def test_load_fix_report_file_missing_or_corrupt(self) -> None:
        self.assertIsNone(load_fix_report_file(self.project_dir))
        path = os.path.join(
            self.project_dir, "transl_cache", "fix_report", "latest.json"
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("这不是JSON{{{")
        self.assertIsNone(load_fix_report_file(self.project_dir))


def _registry_module():
    """取 server_runtime 模块对象（常量补丁目标）。"""
    import GalTransl.server_runtime as mod

    return mod


class FixReportConstantsTest(unittest.TestCase):
    """上限常量存在且为正（防止误配为 0 导致全量截断）。"""

    def test_limits_positive(self) -> None:
        self.assertGreater(FIX_REPORT_PER_FILE_LIMIT, 0)
        self.assertGreater(FIX_REPORT_TOTAL_LIMIT, 0)


class RunJobFinishHookTests(unittest.IsolatedAsyncioTestCase):
    """run_job_async 收尾钩子：任务结束（成功/取消）必须收尾本次修复报告并落盘。

    "本次"语义由任务入口的 reset_runtime_project 重建 RuntimeState 保证，
    收尾侧由 finally 中的 finish_project_fix_report 保证——两条路径都在本类锁定。
    """

    def setUp(self) -> None:
        self.project_dir = tempfile.mkdtemp(prefix="fix_report_job_")
        self.cache_dir = tempfile.mkdtemp(prefix="fix_report_job_cache_")

    def tearDown(self) -> None:
        RUNTIME_REGISTRY._states.pop(_normalize_project_dir(self.project_dir), None)

    def _fake_cfg(self) -> SimpleNamespace:
        return SimpleNamespace(
            projectConfig={"backendSpecific": {}},
            keyValues={},
            non_interactive=True,
            runtime_project_dir=self.project_dir,
            print_translation_log_in_terminal=True,
            tPlugins=[],
            getKey=lambda key, default=None: 1 if key == "workersPerProject" else default,
            get_workers_per_project=lambda: 1,
            getCachePath=lambda: self.cache_dir,
        )

    async def _run_job(self, translator="ForImproveTranslation", run_galtransl=None):
        from GalTransl.Service import JobSpec, run_job_async

        spec = JobSpec(
            job_id="jobfx",
            project_dir=self.project_dir,
            config_file_name="config.yaml",
            translator=translator,
        )
        mock_run = AsyncMock(return_value=None) if run_galtransl is None else run_galtransl
        with patch(
            "GalTransl.Service.CProjectConfig", return_value=self._fake_cfg()
        ), patch("GalTransl.Service.load_app_settings", return_value={}), patch(
            "GalTransl.Service.run_galtransl", new=mock_run
        ):
            return await run_job_async(spec)

    async def test_completed_job_finishes_and_persists_report(self) -> None:
        state = await self._run_job()
        self.assertEqual(state.status, "completed")
        snap = RUNTIME_REGISTRY.get_fix_report_snapshot(self.project_dir)
        self.assertIsNotNone(snap)
        self.assertEqual(snap["label"], "ForImproveTranslation")
        self.assertNotEqual(snap["finished_at"], "")
        path = os.path.join(
            self.project_dir, "transl_cache", "fix_report", "latest.json"
        )
        self.assertTrue(os.path.exists(path))

    async def test_cancelled_job_still_finishes_report(self) -> None:
        from GalTransl.Service import JobCancelledError

        async def cancelled(*a, **k):
            raise JobCancelledError()

        # 取消路径的 finally 会做缓存合并与重检：与既有 run_job_async 测试同款打桩
        fake_rebuild = (None, None, None, None, [], None, None)
        with patch(
            "GalTransl.server._load_rebuild_deps", return_value=fake_rebuild
        ), patch("GalTransl.server.recheck_pass3_cache_files", return_value=0):
            state = await self._run_job(run_galtransl=cancelled)
        self.assertEqual(state.status, "cancelled")
        snap = RUNTIME_REGISTRY.get_fix_report_snapshot(self.project_dir)
        self.assertIsNotNone(snap)
        self.assertNotEqual(snap["finished_at"], "")

    async def test_finish_hook_called_with_translator_label(self) -> None:
        with patch("GalTransl.server.finish_project_fix_report") as m_finish:
            await self._run_job(translator="ForFixRound")
        m_finish.assert_called_once_with(self.project_dir, label="ForFixRound")


class FixReportCachePathTests(unittest.TestCase):
    """报告条目的 cache_path：把流水线文件名解析为校对页可打开的缓存相对路径。"""

    def setUp(self) -> None:
        self.project_dir = tempfile.mkdtemp(prefix="fix_report_path_")
        self.pass3 = os.path.join(self.project_dir, "transl_cache", "pass3_cache")
        os.makedirs(self.pass3, exist_ok=True)
        RUNTIME_REGISTRY.reset_project(self.project_dir)

    def tearDown(self) -> None:
        RUNTIME_REGISTRY._states.pop(_normalize_project_dir(self.project_dir), None)

    def _touch(self, name: str) -> None:
        with open(os.path.join(self.pass3, name), "w", encoding="utf-8") as f:
            f.write("[]")

    def _write_shard(self, name: str, indexes: list[int]) -> None:
        """写一个带条目序号的缓存分片（分片按 index 区间选择）。"""
        rows = [{"index": i, "pre_src": "s", "pre_dst": "d"} for i in indexes]
        with open(os.path.join(self.pass3, name), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)

    def _entry_of(self, filename: str, index: int = 1) -> dict:
        record_fix_change(
            _fake_config(self.project_dir), _entry(filename, index=index)
        )
        files = RUNTIME_REGISTRY.get_fix_report_snapshot(self.project_dir)["files"]
        group = next(item for item in files if item["filename"] == filename)
        return next(item for item in group["entries"] if item["index"] == index)

    def test_exact_cache_file_resolved(self) -> None:
        self._touch("a.json")
        self.assertEqual(self._entry_of("a.json")["cache_path"], "pass3_cache/a.json")

    def test_input_name_without_json_suffix_resolved(self) -> None:
        # 流水线名 a.txt → 缓存实际是 a.txt.json
        self._touch("a.txt.json")
        self.assertEqual(
            self._entry_of("a.txt")["cache_path"], "pass3_cache/a.txt.json"
        )

    def test_input_name_with_json_suffix_stripped(self) -> None:
        # 流水线名 a.txt.json（输入本身是 json）→ 缓存同名，不重复补后缀
        self._touch("a.txt.json")
        self.assertEqual(
            self._entry_of("a.txt.json")["cache_path"], "pass3_cache/a.txt.json"
        )

    def test_subdir_name_converted_to_cache_separator(self) -> None:
        # 子目录在缓存名里以 -} 连接；流水线名可能写成 / 或 -}
        self._touch("a-}b.txt.json")
        self.assertEqual(
            self._entry_of("a-}b.txt")["cache_path"], "pass3_cache/a-}b.txt.json"
        )
        RUNTIME_REGISTRY.reset_project(self.project_dir)
        self.assertEqual(
            self._entry_of("a/b.txt")["cache_path"], "pass3_cache/a-}b.txt.json"
        )

    def test_split_chunk_selected_by_index(self) -> None:
        # 生产分片名 = <输入名>_<N>.json（输入本身已带 .json 时形如 x.txt.json_0.json）
        self._write_shard("b.txt.json_2.json", list(range(1, 11)))
        self._write_shard("b.txt.json_10.json", list(range(11, 21)))
        self.assertEqual(
            self._entry_of("b.txt.json", index=5)["cache_path"],
            "pass3_cache/b.txt.json_2.json",
        )
        self.assertEqual(
            self._entry_of("b.txt.json", index=15)["cache_path"],
            "pass3_cache/b.txt.json_10.json",
        )

    def test_split_chunk_falls_back_to_first_shard(self) -> None:
        # 区间读不出（空数组）时退回首片；首片按数字后缀判定，字符串序会把 _10 排前面
        self._touch("a.txt_2.json")
        self._touch("a.txt_10.json")
        self.assertEqual(
            self._entry_of("a.txt", index=999)["cache_path"],
            "pass3_cache/a.txt_2.json",
        )

    def test_alt_stem_does_not_hit_unrelated_shard(self) -> None:
        # x.txt.json 无自身分片；同目录 x.txt_0.json 属另一个输入文件，
        # index 不在其区间内时不得误命中（宁可留空让前端回退原名）
        self._write_shard("x.txt_0.json", [1, 2, 3])
        self.assertNotIn("cache_path", self._entry_of("x.txt.json", index=50))

    def test_missing_file_leaves_field_empty(self) -> None:
        self.assertNotIn("cache_path", self._entry_of("missing.json"))

    def test_legacy_root_layout_resolved_without_prefix(self) -> None:
        # 旧项目缓存直接放在 transl_cache 根下：返回不带前缀的相对名
        with open(
            os.path.join(self.project_dir, "transl_cache", "old.json"), "w", encoding="utf-8"
        ) as f:
            f.write("[]")
        self.assertEqual(self._entry_of("old.json")["cache_path"], "old.json")

    def test_traversal_filename_not_resolved(self) -> None:
        # 报告文件可被手工编辑：绝对路径/目录穿越大一律不解析
        self.assertNotIn("cache_path", self._entry_of("../../outside.json"))
        self.assertNotIn("cache_path", self._entry_of("C:/Windows/win.ini"))
        self.assertNotIn("cache_path", self._entry_of("a/.."))
        self.assertNotIn("cache_path", self._entry_of("\\\\server\\share\\x.json"))

    def test_legacy_report_backfilled(self) -> None:
        from GalTransl.server_runtime import backfill_fix_report_cache_paths

        self._touch("b.txt.json")
        report = {
            "files": [
                {
                    "filename": "b.txt",
                    "count": 2,
                    "truncated": False,
                    "entries": [
                        {"filename": "b.txt", "index": 1},
                        {"filename": "b.txt", "index": 2, "cache_path": "keep/me.json"},
                    ],
                }
            ]
        }
        backfill_fix_report_cache_paths(self.project_dir, report)
        entries = report["files"][0]["entries"]
        self.assertEqual(entries[0]["cache_path"], "pass3_cache/b.txt.json")
        self.assertEqual(entries[1]["cache_path"], "keep/me.json")


if __name__ == "__main__":
    unittest.main()
