"""翻译缓存文件范围口径测试。

覆盖 _is_translation_cache_rel 的判定、_collect_cache_files 的收集结果，以及
recheck_pass3_cache_files 默认目标集是否与之一致（顶层旧布局缓存纳入、元数据/示例
文件排除）。问题列表（/problems）与 /progress 端点复用同一枚举，端到端断言见
test_cache_recheck_all.py::CacheScopeEndpointTests。
"""

import os
import tempfile
import unittest
from unittest.mock import patch

import orjson

from GalTransl.server import _collect_cache_files, _is_translation_cache_rel
from GalTransl.server import recheck_pass3_cache_files


def _entry(index: int, pre_src: str, pre_dst: str) -> dict:
    return {
        "index": index,
        "name": "",
        "pre_src": pre_src,
        "post_src": pre_src,
        "pre_dst": pre_dst,
        "proofread_dst": "",
        "trans_by": "model",
        "proofread_by": "",
    }


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))


class TranslationCacheRelTests(unittest.TestCase):
    def test_translation_cache_paths_accepted(self) -> None:
        for rel in (
            "pass3_cache/00_01_導入.txt.json",
            "story.txt.json",
            "pass3_cache/story.txt.json_1.json",
            "sub/deep/story.json",
        ):
            self.assertTrue(_is_translation_cache_rel(rel), rel)

    def test_metadata_paths_rejected(self) -> None:
        for rel in (
            "pass1_cache/story.txt.json.meta.json",
            "pass2_cache/story.txt.json.batch.json",
            "pass0_cache/GlobalPrompt.json",
            "pass0_cache/PlotRouteMap.json",
            "pass0_cache/route_analysis/路线A.json",
            "gendic_cache/terms-b0.json",
            "aftertrans_cache/story.txt.json",
            "pass3_cache/_示例缓存文件.json",
            ".snapshots/pass3_cache/story.txt.json.20260101-000000-000000.bak",
            "pass3_cache/story.txt.json.tmp",
            "pass3_cache/story.txt",
            "",
            "pass3_cache/",
        ):
            self.assertFalse(_is_translation_cache_rel(rel), rel)

    def test_backslash_rel_normalized(self) -> None:
        self.assertTrue(_is_translation_cache_rel("pass3_cache\\story.txt.json"))
        self.assertFalse(_is_translation_cache_rel("pass1_cache\\story.txt.json.meta.json"))

    def test_collect_cache_files_walks_tree(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            _write_json(os.path.join(cache_dir, "pass3_cache", "a.txt.json"), [])
            _write_json(os.path.join(cache_dir, "legacy.json"), [])
            _write_json(os.path.join(cache_dir, "pass3_cache", "_示例缓存文件.json"), [])
            _write_json(os.path.join(cache_dir, "pass1_cache", "a.txt.json.meta.json"), {})
            _write_json(os.path.join(cache_dir, "pass2_cache", "a.txt.json.batch.json"), {})
            _write_json(os.path.join(cache_dir, "pass0_cache", "GlobalPrompt.json"), {})
            _write_json(os.path.join(cache_dir, "gendic_cache", "terms-b0.json"), {})
            _write_json(os.path.join(cache_dir, ".snapshots", "pass3_cache", "a.txt.json.bak"), [])
            self.assertEqual(
                _collect_cache_files(cache_dir),
                ["legacy.json", "pass3_cache/a.txt.json"],
            )

    def test_collect_cache_files_missing_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_collect_cache_files(os.path.join(tmp, "nope")), [])


class RecheckDefaultScopeTests(unittest.TestCase):
    """默认目标集 = _collect_cache_files：顶层旧布局纳入，元数据/示例文件不动。"""

    def _results(self, entries: list) -> list:
        return [
            {
                "index": e.get("index", 0),
                "problem": "残留日文",
                "post_dst_preview": e.get("pre_dst", ""),
                "skip_check": False,
            }
            for e in entries
            if e.get("post_src")
        ]

    def test_top_level_legacy_cache_is_rechecked(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            legacy = os.path.join(cache_dir, "legacy.json")
            _write_json(legacy, [_entry(1, "テスト", "测试")])
            pass3 = os.path.join(cache_dir, "pass3_cache", "a.txt.json")
            _write_json(pass3, [_entry(1, "テスト", "测试")])

            with patch(
                "GalTransl.server_cache._run_problem_detection",
                side_effect=lambda entries, *a, **kw: (self._results(entries), True),
            ):
                n = recheck_pass3_cache_files(cache_dir, None, None, None, None, [])

            self.assertEqual(n, 2)
            for path in (legacy, pass3):
                with open(path, "rb") as f:
                    self.assertEqual(orjson.loads(f.read())[0]["problem"], "残留日文")

    def test_metadata_and_sample_files_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            skip_paths = {
                "pass3_cache/_示例缓存文件.json": [_entry(1, "テスト", "测试")],
                "pass1_cache/a.txt.json.meta.json": {"角色": []},
                "pass2_cache/a.txt.json.batch.json": {"批次": []},
                "pass0_cache/GlobalPrompt.json": {"游戏名称": "x"},
                "gendic_cache/terms-b0.json": {"entries": []},
                "aftertrans_cache/a.txt.json": {"fingerprint": "x"},
                "pass0_cache/route_analysis/路线A.json": {"路线": "A"},
            }
            before: dict[str, bytes] = {}
            for rel, data in skip_paths.items():
                path = os.path.join(cache_dir, *rel.split("/"))
                _write_json(path, data)
                with open(path, "rb") as f:
                    before[rel] = f.read()

            with patch(
                "GalTransl.server_cache._run_problem_detection",
                side_effect=lambda entries, *a, **kw: (self._results(entries), True),
            ) as mocked:
                n = recheck_pass3_cache_files(cache_dir, None, None, None, None, [])

            self.assertEqual(n, 0)
            mocked.assert_not_called()
            for rel, raw in before.items():
                with open(os.path.join(cache_dir, *rel.split("/")), "rb") as f:
                    self.assertEqual(f.read(), raw, rel)

    def test_progress_cb_total_matches_scope(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            _write_json(os.path.join(cache_dir, "legacy.json"), [_entry(1, "テスト", "测试")])
            _write_json(os.path.join(cache_dir, "pass3_cache", "a.txt.json"), [_entry(1, "テスト", "测试")])
            _write_json(os.path.join(cache_dir, "pass3_cache", "_示例缓存文件.json"), [_entry(1, "テスト", "测试")])
            calls: list = []

            with patch(
                "GalTransl.server_cache._run_problem_detection",
                side_effect=lambda entries, *a, **kw: (self._results(entries), True),
            ):
                recheck_pass3_cache_files(
                    cache_dir, None, None, None, None, [], progress_cb=lambda d, t: calls.append((d, t))
                )

            self.assertEqual(calls[0], (0, 2))
            self.assertEqual(calls[-1], (2, 2))


if __name__ == "__main__":
    unittest.main()
