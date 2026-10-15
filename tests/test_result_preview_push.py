"""结果预览统一入口与各后端接入点测试：

- BaseEngine.push_result_preview：空文本跳过、runtime_project_dir 优先（回退
  getProjectDir）、config 异常时静默、不显式传 worker_id（worker 归属由
  server_runtime.set_live_snippets 按 WORKER_ID_CTX 内部解析）；
- BaseSparseFixRound._parse_fix_response：成功应用 better 后推送预览（含
  Better 行），0 成功不推送；
- ForSemCheck / ForToneCheck._parse_fix_response：命中句推送（含中文标签）；
- ForSemCheckAgain / ForToneCheckAgain._parse_confirm_response：确认/撤销均推送；
- ForPlotRouteMap._save_plot_route_map：落盘成功后推送产物 JSON。
"""
import os
import tempfile
import unittest
from unittest.mock import patch

from GalTransl.Backend.BaseEngine import BaseEngine
from GalTransl.Backend.BaseFixRound import BaseSparseFixRound
from GalTransl.Backend.ForBRStation import ForBRStation
from GalTransl.Backend.ForPlotRouteMap import ForPlotRouteMap
from GalTransl.Backend.ForSemCheck import ForSemCheck
from GalTransl.Backend.ForSemCheckAgain import ForSemCheckAgain
from GalTransl.Backend.ForToneCheck import ForToneCheck
from GalTransl.Backend.ForToneCheckAgain import ForToneCheckAgain
from GalTransl.CSentense import CSentense

_SNIPPETS_TARGET = "GalTransl.Backend.BaseEngine.set_live_snippets"


class _FakeOpencc:
    @staticmethod
    def convert(text):
        return text


class _PreviewConfig:
    """预览推送配置替身：只暴露 runtime_project_dir 与 getKey。"""

    def __init__(self, runtime_dir: str = "/tmp/does-not-exist-preview") -> None:
        self.runtime_project_dir = runtime_dir

    def getKey(self, key: str, default=None):
        return default

    def getCachePath(self) -> str:
        return self.runtime_project_dir


class _SnippetRecorder:
    """捕获 set_live_snippets 调用的上下文替身。"""

    def __init__(self) -> None:
        self.calls = []

    def __call__(self, project_dir, prompt_preview="", translation_preview="", worker_id="", filename=""):
        self.calls.append(
            {
                "project_dir": project_dir,
                "prompt_preview": prompt_preview,
                "translation_preview": translation_preview,
                "worker_id": worker_id,
                "filename": filename,
            }
        )

    @property
    def translation_calls(self) -> list:
        return [c for c in self.calls if c["translation_preview"]]


class PushResultPreviewEntryTests(unittest.TestCase):
    """BaseEngine.push_result_preview 统一入口行为。"""

    def test_empty_text_skips_push(self) -> None:
        rec = _SnippetRecorder()
        with patch(_SNIPPETS_TARGET, side_effect=rec):
            BaseEngine.push_result_preview(_PreviewConfig(), "")
        self.assertEqual(rec.calls, [])

    def test_push_uses_runtime_project_dir_and_no_explicit_worker_id(self) -> None:
        rec = _SnippetRecorder()
        with patch(_SNIPPETS_TARGET, side_effect=rec):
            BaseEngine.push_result_preview(_PreviewConfig("/proj/dir"), "结果文本")
        self.assertEqual(len(rec.calls), 1)
        call = rec.calls[0]
        self.assertEqual(call["project_dir"], "/proj/dir")
        self.assertEqual(call["translation_preview"], "结果文本")
        # worker 归属不显式传入：由 server_runtime 按 WORKER_ID_CTX 解析分板块
        self.assertEqual(call["worker_id"], "")

    def test_falls_back_to_get_project_dir(self) -> None:
        class _DirOnlyConfig:
            def getProjectDir(self) -> str:
                return "/proj/fallback"

        rec = _SnippetRecorder()
        with patch(_SNIPPETS_TARGET, side_effect=rec):
            BaseEngine.push_result_preview(_DirOnlyConfig(), "文本")
        self.assertEqual(rec.calls[0]["project_dir"], "/proj/fallback")

    def test_config_without_any_dir_is_silent(self) -> None:
        rec = _SnippetRecorder()
        with patch(_SNIPPETS_TARGET, side_effect=rec):
            BaseEngine.push_result_preview(object(), "文本")
        self.assertEqual(rec.calls, [])

    def test_snippet_failure_is_silent(self) -> None:
        with patch(_SNIPPETS_TARGET, side_effect=RuntimeError("boom")):
            # 不得抛异常：预览仅用于 UI 展示，不能影响翻译主流程
            BaseEngine.push_result_preview(_PreviewConfig(), "文本")


def _make_fix_parser(config: _PreviewConfig) -> ForBRStation:
    """绕过重型 __init__，仅装配 _parse_fix_response 与预览推送所需属性。"""
    obj = object.__new__(ForBRStation)
    obj.target_lang = "Chinese"
    obj.opencc = _FakeOpencc()
    obj.pj_config = config
    return obj


def _make_semcheck_parser(cls, config: _PreviewConfig):
    """ForSemCheck / ForToneCheck / 两个复核轮的共用替身装配。"""
    obj = object.__new__(cls)
    obj._log_tag = "[测试]"
    obj._disabled_reason = ""
    obj.pj_config = config
    return obj


class SparseFixRoundPreviewTests(unittest.TestCase):
    """稀疏修复轮（improve/brfix/jpfix/banfix/fix 公共路径）结果预览。"""

    def test_applied_better_pushed_with_better_line(self) -> None:
        parser = _make_fix_parser(_PreviewConfig())
        t1 = CSentense("src1", index=1)
        t1.post_src = "src1"
        t1.pre_dst = "旧译文"
        t1.proofread_zh = ""
        rec = _SnippetRecorder()
        with patch(_SNIPPETS_TARGET, side_effect=rec):
            success, found = parser._parse_fix_response(
                '{"id": 1, "better": "新译文Y"}', [t1], ""
            )
        self.assertEqual((success, found), (1, 1))
        self.assertEqual(t1.alt_dst, "新译文Y")
        texts = [c["translation_preview"] for c in rec.translation_calls]
        self.assertEqual(len(texts), 1)
        self.assertIn("> Src: src1", texts[0])
        self.assertIn("> Dst: 旧译文", texts[0])
        self.assertIn("> Better: 新译文Y", texts[0])

    def test_zero_success_pushes_nothing(self) -> None:
        parser = _make_fix_parser(_PreviewConfig())
        t1 = CSentense("src1", index=1)
        t1.post_src = "原文AAA"
        t1.pre_dst = "译1"
        t1.proofread_zh = ""
        rec = _SnippetRecorder()
        with patch(_SNIPPETS_TARGET, side_effect=rec):
            # better 与原文相同（回显）被过滤：无成功句，不推送预览
            success, _found = parser._parse_fix_response(
                '{"id": 1, "better": "原文AAA"}', [t1], ""
            )
        self.assertEqual(success, 0)
        self.assertEqual(rec.translation_calls, [])

    def test_preview_failure_does_not_break_parse(self) -> None:
        parser = _make_fix_parser(_PreviewConfig())
        t1 = CSentense("src1", index=1)
        t1.post_src = "src1"
        t1.pre_dst = "旧译文"
        t1.proofread_zh = ""
        with patch(_SNIPPETS_TARGET, side_effect=RuntimeError("boom")):
            success, found = parser._parse_fix_response(
                '{"id": 1, "better": "新译文Y"}', [t1], ""
            )
        self.assertEqual((success, found), (1, 1))

    def test_swap_fix_to_current_preview_reflects_swapped_fields(self) -> None:
        # gpt.swapFixToCurrent=true：修复结果覆盖 pre_dst，旧译文进 alt_dst，
        # 预览须如实反映交换后的字段（Dst=新译文、Better=旧译文）
        parser = _make_fix_parser(_PreviewConfig())
        parser.pj_config.getKey = lambda key, default=None: (
            True if key == "gpt.swapFixToCurrent" else default
        )
        t1 = CSentense("src1", index=1)
        t1.post_src = "src1"
        t1.pre_dst = "旧译文"
        t1.proofread_zh = ""
        rec = _SnippetRecorder()
        with patch(_SNIPPETS_TARGET, side_effect=rec):
            parser._parse_fix_response('{"id": 1, "better": "新译文Y"}', [t1], "")
        self.assertEqual(t1.pre_dst, "新译文Y")
        self.assertEqual(t1.alt_dst, "旧译文")
        texts = [c["translation_preview"] for c in rec.translation_calls]
        self.assertEqual(len(texts), 1)
        self.assertIn("> Dst: 新译文Y", texts[0])
        self.assertIn("> Better: 旧译文", texts[0])


class SemCheckPreviewTests(unittest.TestCase):
    """ForSemCheck 命中标记预览。"""

    def test_hit_pushed_with_issue_label(self) -> None:
        parser = _make_semcheck_parser(ForSemCheck, _PreviewConfig())
        trans = CSentense("src3", index=3)
        trans.post_src = "src3"
        trans.pre_dst = "译文"
        trans.proofread_zh = ""
        trans.suspected_error = ""
        rec = _SnippetRecorder()
        with patch(_SNIPPETS_TARGET, side_effect=rec):
            hit, _ = parser._parse_fix_response(
                '1mj|{"id": 3, "reason": "译文串行"}', [trans], "\r\n"
            )
        self.assertEqual(hit, 1)
        self.assertEqual(trans.suspected_error, "译文串行")
        texts = [c["translation_preview"] for c in rec.translation_calls]
        self.assertEqual(len(texts), 1)
        self.assertIn("> 疑似错误: 译文串行", texts[0])

    def test_zero_hit_pushes_nothing(self) -> None:
        parser = _make_semcheck_parser(ForSemCheck, _PreviewConfig())
        rec = _SnippetRecorder()
        with patch(_SNIPPETS_TARGET, side_effect=rec):
            parser._parse_fix_response("", [], "\r\n")
        self.assertEqual(rec.translation_calls, [])


class SemCheckAgainPreviewTests(unittest.TestCase):
    """ForSemCheckAgain 确认/撤销预览。"""

    def test_confirm_and_dismiss_both_pushed(self) -> None:
        parser = _make_semcheck_parser(ForSemCheckAgain, _PreviewConfig())
        t1 = CSentense("src3", index=3)
        t1.post_src = "src3"
        t1.pre_dst = "译文"
        t1.proofread_zh = ""
        t1.suspected_error = "旧原因"
        t2 = CSentense("src4", index=4)
        t2.post_src = "src4"
        t2.pre_dst = "译文2"
        t2.proofread_zh = ""
        t2.suspected_error = "旧原因2"
        rec = _SnippetRecorder()
        with patch(_SNIPPETS_TARGET, side_effect=rec):
            confirm, dismiss = parser._parse_confirm_response(
                'tma|{"id": 3, "keep": true, "reason": "人名错译"}\n'
                'tma|{"id": 4, "keep": false}',
                [t1, t2],
            )
        self.assertEqual((confirm, dismiss), (1, 1))
        texts = [c["translation_preview"] for c in rec.translation_calls]
        self.assertEqual(len(texts), 1)
        self.assertIn("> 疑似错误: 人名错译", texts[0])
        self.assertIn("v--4（已撤销：可接受译文）", texts[0])

    def test_empty_verdict_pushes_nothing(self) -> None:
        parser = _make_semcheck_parser(ForSemCheckAgain, _PreviewConfig())
        rec = _SnippetRecorder()
        with patch(_SNIPPETS_TARGET, side_effect=rec):
            parser._parse_confirm_response("", [])
        self.assertEqual(rec.translation_calls, [])


class ToneCheckPreviewTests(unittest.TestCase):
    """ForToneCheck / ForToneCheckAgain 预览（与语义检测同构，标签为「色彩不一致」）。"""

    def test_tonecheck_hit_pushed_with_label(self) -> None:
        parser = _make_semcheck_parser(ForToneCheck, _PreviewConfig())
        trans = CSentense("src7", index=7)
        trans.post_src = "src7"
        trans.pre_dst = "译文"
        trans.proofread_zh = ""
        trans.tone_issue = ""
        rec = _SnippetRecorder()
        with patch(_SNIPPETS_TARGET, side_effect=rec):
            hit, _ = parser._parse_fix_response(
                '1mj|{"id": 7, "reason": "过于书面"}', [trans], "\r\n"
            )
        self.assertEqual(hit, 1)
        self.assertEqual(trans.tone_issue, "过于书面")
        texts = [c["translation_preview"] for c in rec.translation_calls]
        self.assertIn("> 色彩不一致: 过于书面", texts[0])

    def test_tonecheckagain_confirm_and_dismiss_pushed(self) -> None:
        parser = _make_semcheck_parser(ForToneCheckAgain, _PreviewConfig())
        t1 = CSentense("src8", index=8)
        t1.post_src = "src8"
        t1.pre_dst = "译文"
        t1.proofread_zh = ""
        t1.tone_issue = "旧标记"
        t2 = CSentense("src9", index=9)
        t2.post_src = "src9"
        t2.pre_dst = "译文2"
        t2.proofread_zh = ""
        t2.tone_issue = "旧标记2"
        rec = _SnippetRecorder()
        with patch(_SNIPPETS_TARGET, side_effect=rec):
            confirm, dismiss = parser._parse_confirm_response(
                'tma|{"id": 8, "keep": true}\ntma|{"id": 9, "keep": false}',
                [t1, t2],
            )
        self.assertEqual((confirm, dismiss), (1, 1))
        texts = [c["translation_preview"] for c in rec.translation_calls]
        self.assertEqual(len(texts), 1)
        self.assertIn("v--8", texts[0])
        self.assertIn("v--9（已撤销：可接受译文）", texts[0])


class PlotRouteMapPreviewTests(unittest.TestCase):
    """ForPlotRouteMap 落盘成功后推送产物 JSON。"""

    def test_save_pushes_payload_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            obj = object.__new__(ForPlotRouteMap)
            obj.pj_config = _PreviewConfig(tmpdir)
            data = {
                "mermaid": 'flowchart TD\n  subgraph prologue["序章"]\n  A["开始"]\n  end',
                "文件归属": {"a.json": ["prologue"]},
                "节点剧情": {"序章": "主角登场"},
            }
            rec = _SnippetRecorder()
            with patch(_SNIPPETS_TARGET, side_effect=rec):
                ok = obj._save_plot_route_map(data, "线性", "测试大纲")
            self.assertTrue(ok)
            # 产物已落盘到 pass0_cache/PlotRouteMap.json
            self.assertTrue(
                os.path.exists(
                    os.path.join(tmpdir, "pass0_cache", "PlotRouteMap.json")
                )
            )
            texts = [c["translation_preview"] for c in rec.translation_calls]
            self.assertEqual(len(texts), 1)
            self.assertIn('"mermaid"', texts[0])
            self.assertIn("主角登场", texts[0])


if __name__ == "__main__":
    unittest.main()
