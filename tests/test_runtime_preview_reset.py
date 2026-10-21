"""预览快照的跨阶段清理、说明性预览让位与前端选取口径测试：

- `RuntimeRegistry.reset_previews`：清空 translation_previews / preview_note_keys /
  prompt_previews / ttft_states / latest_prompt_preview，保留 file_totals /
  meta_min_lines / progress_sealed；
- 说明性预览（0 命中/0 采纳，note_only=True）不覆盖同板块已有的真实结果；
- 流水线 `_stage_begin`：阶段切换清理上一阶段预览，使非 worker 上下文后端写入的
  公共 key "-1"（剧情路线图 / 全局分析）不再被残留的 worker 板块遮蔽；
- 前端选取口径复刻（lib/previewSelection.ts）：选中 worker 优先 → 公共 key "-1"
  →（无板块时）首条可用预览。

回归背景：阶段 4（ForPlotRouteMap）/ 阶段 5（全局分析）直接在流水线里 await，不绑
worker 上下文，预览一律写 "-1"；残留的 worker 板块会让它永远显示不出来。
"""
import tempfile
import unittest
from typing import Optional
from unittest.mock import patch

from GalTransl.Frontend import llm_pipeline
from GalTransl.Frontend.llm_pipeline import _stage_begin
from GalTransl.server_runtime import (
    RUNTIME_REGISTRY,
    _normalize_project_dir,
    reset_runtime_previews,
    set_live_snippets,
    set_ttft_state,
    update_runtime_status,
)


def _frontend_pick(snapshot: dict, worker_id: Optional[str]) -> str:
    """复刻前端 lib/previewSelection.ts 的 pickTranslationPreview（口径变更须同步两侧）。"""
    previews = snapshot["translation_previews"]
    if worker_id:
        return previews.get(worker_id) or previews.get("-1") or ""
    return previews.get("-1") or (list(previews.values())[0] if previews else "")


class _StageConfig:
    """_stage_begin 的配置替身：只暴露运行时项目目录。"""

    def __init__(self, runtime_dir: str) -> None:
        self.runtime_project_dir = runtime_dir

    def getProjectDir(self) -> str:
        return self.runtime_project_dir


class _RegistryIsolation:
    """临时项目注册与回收的公共骨架，避免污染全局 RUNTIME_REGISTRY。"""

    def track(self, tmpdir: str):
        key = _normalize_project_dir(tmpdir)
        self.addCleanup(RUNTIME_REGISTRY._states.pop, key, None)
        return RUNTIME_REGISTRY.ensure_project(tmpdir)


class ResetPreviewsTests(_RegistryIsolation, unittest.TestCase):
    """reset_previews 的清理范围与保留项。"""

    def test_clears_previews_but_keeps_progress_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = self.track(tmpdir)
            update_runtime_status(
                tmpdir, file_totals={"a.json": 10}, meta_min_lines=5
            )
            set_live_snippets(tmpdir, prompt_preview="提示词", filename="a.json", worker_id="0")
            set_live_snippets(tmpdir, translation_preview="译文", worker_id="0")
            set_ttft_state(tmpdir, "WAITING", worker_id="0")
            before = RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir)
            self.assertTrue(before["prompt_previews"])
            self.assertTrue(before["translation_previews"])
            self.assertTrue(before["ttft_states"])
            self.assertEqual(before["latest_prompt_preview"], "提示词")

            reset_runtime_previews(tmpdir)
            after = RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir)
            self.assertEqual(after["prompt_previews"], {})
            self.assertEqual(after["translation_previews"], {})
            self.assertEqual(after["ttft_states"], {})
            # 左栏「当前提示词」在无 worker 板块时直接读该字段，残留会显示上一阶段内容
            self.assertEqual(after["latest_prompt_preview"], "")
            # 进度相关数据不得被清：否则进度条回退、阈值判定失效
            self.assertEqual(after["file_totals"], {"a.json": 10})
            self.assertEqual(after["meta_min_lines"], 5)
            self.assertTrue(state.progress_sealed)

    def test_reset_clears_note_keys(self) -> None:
        """说明性预览的让位集合须随预览一起清，否则下阶段首次 0 命中会被误判为"已有真实结果"。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            set_live_snippets(tmpdir, translation_preview="[说明] 0 命中", worker_id="0", note_only=True)
            self.assertIn("0", RUNTIME_REGISTRY._states[_normalize_project_dir(tmpdir)].preview_note_keys)
            reset_runtime_previews(tmpdir)
            self.assertEqual(
                RUNTIME_REGISTRY._states[_normalize_project_dir(tmpdir)].preview_note_keys, set()
            )

    def test_reset_project_clears_note_keys(self) -> None:
        """任务开始重建 RuntimeState：让位集合须为空（否则新任务的真实结果会被说明挡住）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            set_live_snippets(tmpdir, translation_preview="[说明] 0 命中", worker_id="0", note_only=True)
            RUNTIME_REGISTRY.reset_project(tmpdir)
            self.assertEqual(
                RUNTIME_REGISTRY._states[_normalize_project_dir(tmpdir)].preview_note_keys, set()
            )

    def test_unknown_project_is_silent(self) -> None:
        # 未注册项目直接返回，不创建状态、不抛异常
        with tempfile.TemporaryDirectory() as tmpdir:
            reset_runtime_previews(tmpdir)
            self.assertNotIn(_normalize_project_dir(tmpdir), RUNTIME_REGISTRY._states)


class NoteOnlyPreviewTests(_RegistryIsolation, unittest.TestCase):
    """说明性预览（0 命中/0 采纳）不得刷掉真实结果。"""

    def test_note_does_not_overwrite_real_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            set_live_snippets(tmpdir, translation_preview="[真实] 命中 1 句", worker_id="0")
            set_live_snippets(tmpdir, translation_preview="[说明] 本批 0 命中", worker_id="0", note_only=True)
            snapshot = RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir)
            self.assertEqual(snapshot["translation_previews"]["0"], "[真实] 命中 1 句")

    def test_note_overwrites_previous_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            set_live_snippets(tmpdir, translation_preview="[说明] 第 1 批 0 命中", worker_id="0", note_only=True)
            set_live_snippets(tmpdir, translation_preview="[说明] 第 2 批 0 命中", worker_id="0", note_only=True)
            snapshot = RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir)
            self.assertEqual(snapshot["translation_previews"]["0"], "[说明] 第 2 批 0 命中")

    def test_discarded_preview_overwrites_real_and_yields_to_next_note(self) -> None:
        """丢弃说明须覆盖已作废的命中，且让位给后续批次的 0 命中说明。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            set_live_snippets(tmpdir, translation_preview="[真实] 命中 3 句", worker_id="0")
            set_live_snippets(
                tmpdir,
                translation_preview="[说明] 本批判定为整批回显，命中已丢弃",
                worker_id="0",
                note_only=True,
                force=True,
            )
            snapshot = RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir)
            self.assertEqual(
                snapshot["translation_previews"]["0"], "[说明] 本批判定为整批回显，命中已丢弃"
            )
            set_live_snippets(tmpdir, translation_preview="[说明] 重试后 0 命中", worker_id="0", note_only=True)
            snapshot = RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir)
            self.assertEqual(snapshot["translation_previews"]["0"], "[说明] 重试后 0 命中")

    def test_real_result_overwrites_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            set_live_snippets(tmpdir, translation_preview="[说明] 0 命中", worker_id="0", note_only=True)
            set_live_snippets(tmpdir, translation_preview="[真实] 命中 2 句", worker_id="0")
            snapshot = RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir)
            self.assertEqual(snapshot["translation_previews"]["0"], "[真实] 命中 2 句")


class StageBeginPreviewsTests(_RegistryIsolation, unittest.TestCase):
    """阶段切换清理预览后的可见性（回归场景）。"""

    def test_stage_begin_clears_previous_stage_previews(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            set_live_snippets(tmpdir, prompt_preview="[阶段3] 提示词", filename="a.json", worker_id="0")
            set_live_snippets(tmpdir, translation_preview="[阶段3] 元数据", worker_id="0")
            set_ttft_state(tmpdir, "WAITING", worker_id="0")
            with patch("GalTransl.Frontend.llm_pipeline._update_runtime") as upd:
                _stage_begin(_StageConfig(tmpdir), "validate", "输入数据校验")
            upd.assert_called_once()
            self.assertEqual(upd.call_args.kwargs["stage"], "输入数据校验")
            snapshot = RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir)
            self.assertEqual(snapshot["prompt_previews"], {})
            self.assertEqual(snapshot["translation_previews"], {})
            # 状态灯与 tab 同批清空，避免出现「有灯无 tab」的错位
            self.assertEqual(snapshot["ttft_states"], {})

    def test_translate_stage_clears_previews_too(self) -> None:
        """阶段 7（翻译）曾绕过 _stage_begin：阶段 6 的残留板块会被当成"首条"显示。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            set_live_snippets(tmpdir, prompt_preview="[阶段6] 提示词", filename="a.json", worker_id="0")
            set_live_snippets(tmpdir, translation_preview="[阶段6] 批次元数据", worker_id="0")
            with patch("GalTransl.Frontend.llm_pipeline._update_runtime"):
                _stage_begin(_StageConfig(tmpdir), "translate", "翻译执行中")
            snapshot = RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir)
            self.assertEqual(snapshot["translation_previews"], {})

    def test_public_key_visible_after_stage_switch(self) -> None:
        """阶段3 留下 worker 板块 → 阶段切换 → 阶段4 写公共 key "-1"：前端须取到 "-1"。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            set_live_snippets(tmpdir, prompt_preview="[阶段3] 提示词", filename="a.json", worker_id="0")
            set_live_snippets(tmpdir, translation_preview="[阶段3] 元数据", worker_id="0")
            self.assertEqual(
                _frontend_pick(RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir), "0"),
                "[阶段3] 元数据",
            )
            with patch("GalTransl.Frontend.llm_pipeline._update_runtime"):
                _stage_begin(_StageConfig(tmpdir), "plot_route", "生成剧情路线图")
            # 非 worker 上下文后端：不传 worker_id，写公共 key "-1"
            set_live_snippets(tmpdir, translation_preview="[阶段4] 路线图 mermaid")
            snapshot = RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir)
            self.assertEqual(list(snapshot["translation_previews"]), ["-1"])
            # 无 worker 板块时前端取首条；有板块时回退也须命中 "-1"
            self.assertEqual(_frontend_pick(snapshot, None), "[阶段4] 路线图 mermaid")
            self.assertEqual(_frontend_pick(snapshot, "0"), "[阶段4] 路线图 mermaid")

    def test_no_stale_worker_tab_after_stage_switch(self) -> None:
        """阶段7 六个 worker → 阶段8 三个 worker：tab 不得残留上阶段的陈旧译文。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            for i in range(6):
                set_live_snippets(
                    tmpdir,
                    prompt_preview=f"[阶段7] 提示词 w{i}",
                    filename=f"f{i}.json",
                    worker_id=str(i),
                )
                set_live_snippets(tmpdir, translation_preview=f"[阶段7] 译文 w{i}", worker_id=str(i))
            with patch("GalTransl.Frontend.llm_pipeline._update_runtime"):
                _stage_begin(_StageConfig(tmpdir), "translate", "翻译执行中")
            for i in range(3):
                set_live_snippets(tmpdir, translation_preview=f"[阶段8] 改进结果 w{i}", worker_id=str(i))
            snapshot = RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir)
            self.assertEqual(sorted(snapshot["translation_previews"]), ["0", "1", "2"])
            for wid in ("0", "1", "2"):
                self.assertEqual(
                    snapshot["translation_previews"][wid], f"[阶段8] 改进结果 w{wid}"
                )

    def test_worker_preview_still_wins_over_public_key(self) -> None:
        """多 worker 分板块语义不变：选中 worker 有值时优先取它。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            set_live_snippets(tmpdir, prompt_preview="提示词 w1", filename="b.json", worker_id="1")
            set_live_snippets(tmpdir, translation_preview="[阶段7] 译文 w1", worker_id="1")
            set_live_snippets(tmpdir, translation_preview="[公共] 全局分析", worker_id="-1")
            snapshot = RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir)
            self.assertEqual(_frontend_pick(snapshot, "1"), "[阶段7] 译文 w1")
            self.assertEqual(_frontend_pick(snapshot, "0"), "[公共] 全局分析")
            self.assertEqual(_frontend_pick(snapshot, None), "[公共] 全局分析")

    def test_no_cross_worker_fallback_when_worker_selected(self) -> None:
        """选中板块为空且无公共结果时留空：不得拿别的 worker 顶替（tab 标签会说谎）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            set_live_snippets(tmpdir, translation_preview="[阶段7] 译文 w0", worker_id="0")
            snapshot = RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir)
            self.assertEqual(_frontend_pick(snapshot, "1"), "")


class TranslateStageEntryTests(_RegistryIsolation, unittest.IsolatedAsyncioTestCase):
    """阶段 7（翻译）必须走 _stage_begin：曾因绕过它漏清预览，须有回归锁。

    这里锁的是「入口」而非「结果」：阶段 7 曾内联 LOGGER.info + _update_runtime 而
    漏掉 reset，故断言 _stage_begin 被调用，避免日后改回内联写法时静默回退。
    """

    async def test_run_stage_translate_clears_previews_via_stage_begin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.track(tmpdir)
            set_live_snippets(tmpdir, translation_preview="[阶段6] 批次元数据", worker_id="0")
            real_begin = llm_pipeline._stage_begin
            with (
                patch.object(llm_pipeline, "_update_runtime"),
                patch.object(llm_pipeline, "_stage_enabled", return_value=False),
                patch.object(llm_pipeline, "_stage_begin", side_effect=real_begin) as begin_spy,
            ):
                await llm_pipeline._run_stage_translate(_StageConfig(tmpdir), {}, [], {})
            begin_spy.assert_called_once()
            self.assertEqual(begin_spy.call_args.args[1], "translate")
            self.assertEqual(
                RUNTIME_REGISTRY.get_runtime_snapshot(tmpdir)["translation_previews"], {}
            )


if __name__ == "__main__":
    unittest.main()
