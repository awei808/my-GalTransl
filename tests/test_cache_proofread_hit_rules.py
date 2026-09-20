"""缓存命中规则：缺 proofread_dst 字段的旧缓存不得被当成"已校对"而静默误命中。

有校对稿（proofread_dst 非空）等于最终稿：原文后来改没改都照样命中——这是设计意图。
但字段整个缺失（很老的缓存、手改过的缓存）应视为"没校对过"：post_src 变更、pre_dst
为空等检查一步不能少，否则原文早已改过的旧缓存会被静默算命中。
"""

import asyncio
import json
import os
import tempfile
import unittest

from GalTransl.Cache import get_transCache_from_json
from GalTransl.CSentense import CSentense


def _write_cache(path: str, entry: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([entry], f, ensure_ascii=False)


def _tran(src: str, post_src: str = "") -> CSentense:
    tran = CSentense(src, speaker="", index=1)
    tran.post_src = post_src or src
    return tran


def _load(cache_file: str, tran: CSentense):
    return asyncio.run(get_transCache_from_json([tran], cache_file, eng_type=""))


class ProofreadHitRulesTests(unittest.TestCase):
    def test_missing_proofread_field_with_changed_post_src_misses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "sc.json")
            _write_cache(
                cache_file,
                {"index": 1, "name": "", "pre_src": "原文A", "post_src": "原文A处理", "pre_dst": "旧译"},
            )
            tran = _tran("原文A", post_src="原文A处理改")  # 处理后原文已变
            hit, unhit = _load(cache_file, tran)
            self.assertEqual(hit, [])
            self.assertEqual(unhit, [tran])

    def test_missing_proofread_field_with_empty_pre_dst_misses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "sc.json")
            _write_cache(
                cache_file,
                {"index": 1, "name": "", "pre_src": "原文A", "post_src": "原文A"},
            )
            tran = _tran("原文A")
            hit, unhit = _load(cache_file, tran)
            self.assertEqual(hit, [])
            self.assertEqual(unhit, [tran])

    def test_present_proofread_still_hits_even_if_post_src_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "sc.json")
            _write_cache(
                cache_file,
                {
                    "index": 1,
                    "name": "",
                    "pre_src": "原文A",
                    "post_src": "原文A",
                    "pre_dst": "旧译",
                    "proofread_dst": "校对稿",
                },
            )
            tran = _tran("原文A", post_src="原文A处理改")  # 原文已改：有校对稿即最终稿，仍命中
            hit, unhit = _load(cache_file, tran)
            self.assertEqual(unhit, [])
            self.assertEqual(hit, [tran])
            self.assertEqual(tran.post_dst, "校对稿")

    def test_missing_proofread_field_unchanged_src_still_hits(self) -> None:
        # 常规路径不受影响：缺字段但原文未变、pre_dst 非空 → 正常命中
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = os.path.join(tmp, "sc.json")
            _write_cache(
                cache_file,
                {"index": 1, "name": "", "pre_src": "原文A", "post_src": "原文A", "pre_dst": "旧译"},
            )
            tran = _tran("原文A")
            hit, unhit = _load(cache_file, tran)
            self.assertEqual(unhit, [])
            self.assertEqual(hit, [tran])
            self.assertEqual(tran.post_dst, "旧译")


if __name__ == "__main__":
    unittest.main()
