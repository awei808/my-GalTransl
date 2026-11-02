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
        texts = [f"第{i}个足够长的中文测试句子内容" for i in range(20)]
        items, mapping = gs.build_sample(texts, n=20, dupes=2, seed=7)
        self.assertEqual(len(items), 22)
        self.assertEqual([it["id"] for it in items], list(range(1, 23)))
        self.assertEqual(len(mapping), 2)
        by_id = {it["id"]: it for it in items}
        for dup_id, orig_id in mapping.items():
            self.assertEqual(by_id[dup_id]["dst"], by_id[orig_id]["dst"])
            # 重复句按均匀间隔插入，不与原句紧邻
            self.assertGreater(abs(dup_id - orig_id), 3)
        # 盲标：不写分数，不含来源文件，label 待填
        for it in items:
            self.assertEqual(sorted(it), ["dst", "id", "label", "note", "subtype"])
            self.assertEqual(it["label"], "")

    def test_candidates_fewer_than_requested(self) -> None:
        texts = ["只有三个足够长的中文句子内容甲", "只有三个足够长的中文句子内容乙"]
        items, mapping = gs.build_sample(texts, n=400, dupes=30, seed=1)
        # 候选不足时按全部抽样，重复句上限收敛到 max(1, 样本数//10)
        self.assertEqual(len(items), 3)
        self.assertEqual(len(mapping), 1)


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
        agree, total, bad = ge.consistency(items, {2: 1, 3: 1})
        self.assertEqual(total, 1)
        self.assertEqual(agree, 0)
        self.assertEqual(bad, [(2, 1)])

    def test_load_dupes_reads_string_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "dupes.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"28": 364, "36": 103}, fh)
            self.assertEqual(ge.load_dupes(path), {28: 364, 36: 103})

    def test_load_dupes_missing_file_exits(self) -> None:
        with self.assertRaises(SystemExit):
            ge.load_dupes(os.path.join(tempfile.gettempdir(), "nope_dupes.json"))


class LoadGoldTests(unittest.TestCase):
    def test_reads_jsonl_and_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jsonl = os.path.join(tmp, "g.jsonl")
            with open(jsonl, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"id": 1, "dst": "甲" * 8, "label": "natural"}, ensure_ascii=False) + "\n")
            rows = ge.load_gold(jsonl)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["dst"], "甲" * 8)

            fixture = os.path.join(tmp, "g.json")
            with open(fixture, "w", encoding="utf-8") as fh:
                json.dump({"items": [{"text": "乙" * 8, "label": "ok"}]}, fh, ensure_ascii=False)
            rows = ge.load_gold(fixture)
            self.assertEqual(rows[0]["id"], 1)
            self.assertEqual(rows[0]["dst"], "乙" * 8)


class ScoreGoldSkipTests(unittest.TestCase):
    def test_skips_dupes_without_touching_index(self) -> None:
        items = [
            {"id": 1, "dst": "甲" * 8, "label": "natural"},
            {"id": 2, "dst": "乙" * 8, "label": "awkward"},
            {"id": 3, "dst": "丙" * 8, "label": "natural"},
        ]
        # 被跳过的行不进入打分，故 index=None 也不会被访问
        pos, neg, stats = ge.score_gold(items, None, 1.0, skip_ids={1, 2, 3})
        self.assertEqual(stats["dupes"], 3)
        self.assertEqual(pos, [])
        self.assertEqual(neg, [])


class ResolveCacheDirTests(unittest.TestCase):
    def test_prefers_pass3_cache_over_project_root_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # 项目根有干扰 json
            with open(os.path.join(tmp, "known_projects.json"), "w", encoding="utf-8") as fh:
                json.dump([{"name": "x"}], fh)
            cache = os.path.join(tmp, "transl_cache", "pass3_cache")
            os.makedirs(cache)
            with open(os.path.join(cache, "a.json"), "w", encoding="utf-8") as fh:
                json.dump([{"index": 0, "pre_dst": "这是一个足够长的中文句子"}], fh, ensure_ascii=False)
            self.assertEqual(gs.resolve_cache_dir(tmp), cache)

    def test_rejects_dir_without_cache_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.json"), "w", encoding="utf-8") as fh:
                json.dump([{"name": "x"}], fh)
            with self.assertRaises(SystemExit):
                gs.resolve_cache_dir(tmp)


if __name__ == "__main__":
    unittest.main()
