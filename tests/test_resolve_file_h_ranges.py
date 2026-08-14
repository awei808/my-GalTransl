"""翻译阶段 H 区间解析（_resolve_file_h_ranges）回归测试。

背景：修复 H 场景「长句丢失换行」阈值误用。
postprocess_results 在翻译完成写缓存时调用 find_problems，此前不传 h_ranges，
导致 H 场景句子走平均分句阈值（avgSentenceLengthThreshold）而非 H 专用阈值
（avgSentenceLengthThresholdH）。修复后应解析该文件 pass2_cache 的 H 区间传入，
使 H 场景正确走 H 阈值。

本测试聚焦新辅助函数 _resolve_file_h_ranges 的解析与降级行为。
"""

import json
import os
import shutil
import tempfile
import unittest

from GalTransl.Frontend.LLMTranslate import _resolve_file_h_ranges


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class _FakeProjectConfig:
    """最小桩：仅提供 getCachePath()（真实项目目录下为 transl_cache）。"""

    def __init__(self, tmpdir: str):
        self._tmpdir = tmpdir

    def getCachePath(self) -> str:
        return os.path.join(self._tmpdir, "transl_cache")


class ResolveFileHrangesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="galtransl_file_hranges_")
        self.batch_dir = os.path.join(self.tmpdir, "transl_cache", "pass2_cache")
        self.pass3_dir = os.path.join(self.tmpdir, "transl_cache", "pass3_cache")
        os.makedirs(self.batch_dir, exist_ok=True)
        os.makedirs(self.pass3_dir, exist_ok=True)
        self.cfg = _FakeProjectConfig(self.tmpdir)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _batch(self, filename: str, ranges: list) -> None:
        _write_json(
            os.path.join(self.batch_dir, f"{filename}.batch.json"),
            {"id": filename, "批次": ranges},
        )

    def _cache(self, filename: str) -> str:
        path = os.path.join(self.pass3_dir, filename)
        _write_json(path, [])
        return path

    def test_resolves_single_h_range(self) -> None:
        cache_file_path = self._cache("story.txt.json")
        self._batch(
            "story.txt.json",
            [{"区间": [1, 20], "h": False}, {"区间": [21, 40], "h": True}],
        )
        h_ranges = _resolve_file_h_ranges(self.tmpdir, cache_file_path, self.cfg)
        self.assertEqual(h_ranges, [(21, 40)])

    def test_resolves_merged_h_ranges(self) -> None:
        cache_file_path = self._cache("story.txt.json")
        self._batch(
            "story.txt.json",
            [{"区间": [5, 10], "h": True}, {"区间": [11, 15], "h": True}],
        )
        h_ranges = _resolve_file_h_ranges(self.tmpdir, cache_file_path, self.cfg)
        # 相邻 h 批次应合并为一段
        self.assertEqual(h_ranges, [(5, 15)])

    def test_no_batch_file_returns_empty(self) -> None:
        # 独立运行翻译、未跑批注阶段时 pass2_cache 缺失 → 降级返回空
        cache_file_path = self._cache("story.txt.json")
        h_ranges = _resolve_file_h_ranges(self.tmpdir, cache_file_path, self.cfg)
        self.assertEqual(h_ranges, [])

    def test_batch_exists_but_no_h_returns_empty(self) -> None:
        cache_file_path = self._cache("story.txt.json")
        self._batch(
            "story.txt.json",
            [{"区间": [1, 10], "h": False}],
        )
        h_ranges = _resolve_file_h_ranges(self.tmpdir, cache_file_path, self.cfg)
        self.assertEqual(h_ranges, [])

    def test_cache_file_missing_returns_empty(self) -> None:
        cache_file_path = os.path.join(self.pass3_dir, "nope.txt.json")
        h_ranges = _resolve_file_h_ranges(self.tmpdir, cache_file_path, self.cfg)
        self.assertEqual(h_ranges, [])


if __name__ == "__main__":
    unittest.main()
