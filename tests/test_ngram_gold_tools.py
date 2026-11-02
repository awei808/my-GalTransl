# -*- coding: utf-8 -*-
"""ngram gold set 采样/评估工具的单测。

覆盖：
  - collect_candidates：译文口径（proofread_dst 优先）、短句剔除、去重
  - build_sample：抽样数量、重复句映射、id 连续、盲标字段不含分数/来源
  - label_of / auc：标签归一化与可分性
  - best_threshold：按误标预算取最高阈值
  - consistency：重复句标注一致率
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ngram_gold_eval as ge
import ngram_gold_sample as gs
from GalTransl import ngram_rules as nr


class CollectCandidatesTests(unittest.TestCase):
    def _write(self, cache_dir: str, name: str, items: list) -> None:
        with open(os.path.join(cache_dir, name), "w", encoding="utf-8") as fh:
            json.dump(items, fh, ensure_ascii=False)

    def test_prefers_proofread_and_drops_short_and_dups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write(
                tmp,
                "a.json",
                [
                    {"index": 0, "pre_dst": "这是一个足够长的中文句子用来测试"},
                    {"index": 1, "pre_dst": "短句"},
                    {"index": 2, "pre_dst": "甲" * 10, "proofread_dst": "乙" * 10},
                ],
            )
            self._write(tmp, "b.json", {"items": [{"index": 0, "pre_dst": "这是一个足够长的中文句子用来测试"}]})
            texts, stats = gs.collect_candidates(tmp, nr.MIN_SENT_CHARS)
            self.assertEqual(stats["total"], 4)
            self.assertEqual(stats["short"], 1)
            self.assertEqual(stats["dup"], 1)
            # proofread_dst 优先于 pre_dst（口径与 find_ngram_flagged 一致）
            self.assertIn("乙" * 10, texts)
            self.assertNotIn("甲" * 10, texts)


class BuildSampleTests(unittest.TestCase):
    def test_ids_contiguous_and_dupes_mapped(self) -> None:
        texts = [f"第{i}个足够长的中文测试句子内容" for i in range(10)]
        items, mapping = gs.build_sample(texts, n=6, dupes=2, seed=7)
        self.assertEqual(len(items), 8)
        self.assertEqual([it["id"] for it in items], list(range(1, 9)))
        self.assertEqual(len(mapping), 2)
        self.assertEqual(set(mapping), {it["id"] for it in items if it["id"] in mapping})
        by_id = {it["id"]: it for it in items}
        for dup_id, orig_id in mapping.items():
            self.assertEqual(by_id[dup_id]["dst"], by_id[orig_id]["dst"])
        # 盲标：不写分数，不含来源文件，label 待填
        for it in items:
            self.assertEqual(sorted(it), ["dst", "id", "label", "note", "subtype"])
            self.assertEqual(it["label"], "")

    def test_candidates_fewer_than_requested(self) -> None:
        texts = ["只有三个足够长的中文句子内容甲", "只有三个足够长的中文句子内容乙"]
        items, mapping = gs.build_sample(texts, n=400, dupes=30, seed=1)
        # 候选不足时按全部抽样，重复句上限收敛到候选数
        self.assertEqual(len(items) - len(mapping), 2)
        self.assertEqual(len(mapping), 2)


class LabelAndThresholdTests(unittest.TestCase):
    def test_label_of_normalizes(self) -> None:
        self.assertEqual(ge.label_of({"label": "natural"}), "positive")
        self.assertEqual(ge.label_of({"label": " True "}), "positive")
        self.assertEqual(ge.label_of({"label": "awkward"}), "negative")
        self.assertEqual(ge.label_of({"label": "False"}), "negative")
        self.assertEqual(ge.label_of({"label": ""}), "")
        self.assertEqual(ge.label_of({"label": "stylistic"}), "")

    def test_auc_bounds(self) -> None:
        self.assertEqual(nr.auc([1.0, 0.9], [0.1, 0.0]), 1.0)
        self.assertEqual(nr.auc([0.0], [1.0]), 0.0)
        self.assertEqual(nr.auc([0.5], [0.5]), 0.5)
        self.assertEqual(nr.auc([], [1.0]), 0.5)

    def test_best_threshold_respects_fpr_budget(self) -> None:
        pos = [0.5, 0.4, 0.3]
        neg = [0.2, 0.1, 0.0]
        threshold, fpr, recall = ge.best_threshold(pos, neg, 0.34)
        self.assertAlmostEqual(threshold, 0.4)
        self.assertAlmostEqual(fpr, 1 / 3, places=6)
        self.assertAlmostEqual(recall, 1.0)
        # 零误标预算：阈值压到最小正例分（再高就会标出正例）
        threshold, fpr, recall = ge.best_threshold(pos, neg, 0.0)
        self.assertAlmostEqual(threshold, 0.3)
        self.assertEqual(fpr, 0.0)


class ConsistencyTests(unittest.TestCase):
    def test_agreement_counts(self) -> None:
        items = [
            {"id": 1, "dst": "甲" * 8, "label": "natural"},
            {"id": 2, "dst": "乙" * 8, "label": "awkward"},
            {"id": 3, "dst": "丙" * 8, "label": ""},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "dupes.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"2": 1, "3": 1}, fh)
            agree, total, bad = ge.consistency(items, path)
        self.assertEqual(total, 1)
        self.assertEqual(agree, 0)
        self.assertEqual(bad, [(2, 1)])


if __name__ == "__main__":
    unittest.main()
