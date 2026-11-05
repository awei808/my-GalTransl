# -*- coding: utf-8 -*-
"""多指标对比台的单测：分数协议、三张表计算与端到端 CLI。"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from typing import Dict, List, Tuple
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ngram_gold_eval as bench
from GalTransl import ngram_rules as nr


class ScoreFileTests(unittest.TestCase):
    def _write(self, path: str, lines: List[object]) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line if isinstance(line, str) else json.dumps(line, ensure_ascii=False))
                fh.write("\n")

    def test_text_and_key_fields_and_bad_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.jsonl")
            self._write(path, [
                {"text": "甲 乙", "score": 0.9},
                {"key": "丙", "score": "0.2"},
                {"text": "缺分", "score": None},
                "not-json",
                {"text": "", "score": 1.0},
            ])
            scores, stats = bench.load_metric_scores(path)
        self.assertEqual(stats, {"rows": 5, "used": 2, "skipped": 3, "duplicated": 0})
        # normalize_text 去空白后建键，gold 里的原始文本也能查到
        self.assertEqual(bench.lookup(scores, "甲乙"), 0.9)
        self.assertEqual(bench.lookup(scores, "丙"), 0.2)
        self.assertIsNone(bench.lookup(scores, "缺分"))

    def test_missing_file_exits_with_hint(self) -> None:
        with self.assertRaises(SystemExit):
            bench.load_metric_scores(os.path.join(tempfile.gettempdir(), "no_such_scores.jsonl"))


class PoolAndPairTests(unittest.TestCase):
    def test_gold_pools_report_missing_and_skips(self) -> None:
        items = [
            {"id": 1, "dst": "甲", "label": "natural"},
            {"id": 2, "dst": "乙", "label": "awkward"},
            {"id": 3, "dst": "丙", "label": ""},
            {"id": 4, "dst": "丁", "label": "natural"},
        ]
        scores = {nr.normalize_text("甲"): 0.9, nr.normalize_text("乙"): 0.1}
        pos, neg, cov = bench.gold_pools_for(items, scores, skip_ids={4})
        self.assertEqual((pos, neg), ([0.9], [0.1]))
        self.assertEqual(cov["missing"], 0)  # 有标签且未被 skip 的两条都有分数
        self.assertEqual(cov["skipped"], 2)  # 丙未标注 + 丁被 skip

    def test_missing_score_is_counted(self) -> None:
        items = [
            {"id": 1, "dst": "甲", "label": "natural"},
            {"id": 2, "dst": "乙", "label": "awkward"},
        ]
        pos, neg, cov = bench.gold_pools_for(items, {nr.normalize_text("甲"): 0.9})
        self.assertEqual((pos, neg, cov["missing"]), ([0.9], [], 1))

    def test_pair_result_counts(self) -> None:
        scores = {nr.normalize_text(k): v for k, v in
                  {"甲": 0.9, "乙": 0.8, "丙": 0.2, "丁": 0.1}.items()}
        pairs = [
            {"before": "丙", "after": "甲", "included": True},
            {"before": "乙", "after": "丁", "included": True},
            {"before": "甲", "after": "甲", "included": True},
            {"before": "不存在", "after": "甲", "included": True},
        ]
        self.assertEqual(bench.pair_result_for(pairs, scores), (1, 1, 1, 1))

    def test_load_pairs_filters_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                for row in ({"included": True}, {"included": False}, {"included": True}):
                    fh.write(json.dumps(row) + "\n")
            self.assertEqual(len(bench.load_pairs(path)), 2)
            self.assertEqual(len(bench.load_pairs(path, include_excluded=True)), 3)

    def test_build_ngram_scores_skips_unscorable(self) -> None:
        with mock.patch.object(nr, "score_text", side_effect=lambda text, _idx, _lam:
                               0.5 if text != "短" else None):
            scores = bench.build_ngram_scores(["甲", "甲", "短"], object(), 1.0)
        self.assertEqual(scores, {nr.normalize_text("甲"): 0.5})


class BenchCLITests(unittest.TestCase):
    def _build_fixtures(self, tmp: str) -> Tuple[str, str, str, str]:
        gold = os.path.join(tmp, "gold.jsonl")
        pairs = os.path.join(tmp, "pairs.jsonl")
        high = os.path.join(tmp, "qemind.jsonl")
        low = os.path.join(tmp, "ppl.jsonl")
        scores = {"甲": 0.9, "乙": 0.8, "丙": 0.2, "丁": 0.1}
        ppl = {"甲": 10, "乙": 20, "丙": 30, "丁": 40}
        with open(gold, "w", encoding="utf-8") as fh:
            for i, (text, label) in enumerate(
                (("甲", "natural"), ("乙", "natural"), ("丙", "awkward"), ("丁", "awkward")), 1
            ):
                fh.write(json.dumps({"id": i, "dst": text, "label": label}, ensure_ascii=False) + "\n")
        with open(pairs, "w", encoding="utf-8") as fh:
            for before, after in (("丙", "甲"), ("甲", "甲"), ("不存在", "甲")):
                fh.write(json.dumps({"before": before, "after": after, "included": True},
                                    ensure_ascii=False) + "\n")
        for path, table in ((high, scores), (low, ppl)):
            with open(path, "w", encoding="utf-8") as fh:
                for text, value in table.items():
                    fh.write(json.dumps({"text": text, "score": value}, ensure_ascii=False) + "\n")
        return gold, pairs, high, low

    def _run(self, gold: str, pairs: str, high: str, low: str,
             extra: List[str]) -> Tuple[int, str]:
        argv = ["--gold", gold, "--pairs", pairs, "--no-ngram",
                "--scores", f"qemind={high}", "--scores", f"ppl={low}", "--invert", "ppl"] + extra
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = bench.main(argv)
        return code, out.getvalue()

    def test_three_tables_and_invert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixtures = self._build_fixtures(tmp)
            code, text = self._run(*fixtures, [])
        self.assertEqual(code, 0)
        for title in ("[1/3] AUC", "[2/3] 成对准确率", "[3/3] 误标预算"):
            self.assertIn(title, text)
        # 两个指标方向一致后 AUC 均为 1.0，成对准确率 100%
        self.assertEqual(text.count("1.0000"), 2)
        self.assertIn("100.0%", text)
        self.assertIn("1/1", text)  # 并列/缺分列

    def test_gate_fails_when_any_metric_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixtures = self._build_fixtures(tmp)
            code_ok, _ = self._run(*fixtures, ["--gate", "0.9"])
            code_fail, _ = self._run(*fixtures, ["--gate", "1.1"])
        self.assertEqual(code_ok, 0)
        self.assertEqual(code_fail, 1)

    def test_no_ngram_without_scores_exits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gold, _, _, _ = self._build_fixtures(tmp)
            with self.assertRaises(SystemExit):
                bench.main(["--gold", gold, "--no-ngram"])



class ExtraBenchTests(unittest.TestCase):
    """审查跟进：重复键、池空、混合 gate、参数校验与 sidecar 合并。"""

    def _write_jsonl(self, path: str, rows: List[dict]) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def test_duplicate_keys_are_reported_and_last_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.jsonl")
            self._write_jsonl(path, [{"text": "甲", "score": 0.1}, {"text": "甲", "score": 0.9}])
            scores, stats = bench.load_metric_scores(path)
        self.assertEqual(stats["duplicated"], 1)
        self.assertEqual(bench.lookup(scores, "甲"), 0.9)

    def test_load_pairs_all_merges_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.jsonl")
            self._write_jsonl(path, [{"included": True}, {"included": True}])
            self._write_jsonl(path + ".excluded.jsonl", [{"included": False}])
            self.assertEqual(len(bench.load_pairs(path)), 2)
            self.assertEqual(len(bench.load_pairs(path, include_excluded=True)), 3)

    def _gold_and_two_metrics(self, tmp: str) -> Tuple[str, str, str]:
        gold = os.path.join(tmp, "g.jsonl")
        good = os.path.join(tmp, "good.jsonl")
        bad = os.path.join(tmp, "bad.jsonl")
        self._write_jsonl(gold, [
            {"id": 1, "dst": "甲", "label": "natural"},
            {"id": 2, "dst": "乙", "label": "natural"},
            {"id": 3, "dst": "丙", "label": "awkward"},
            {"id": 4, "dst": "丁", "label": "awkward"},
        ])
        self._write_jsonl(good, [{"text": t, "score": v} for t, v in
                                 (("甲", 0.9), ("乙", 0.8), ("丙", 0.2), ("丁", 0.1))])
        self._write_jsonl(bad, [{"text": t, "score": v} for t, v in
                                (("甲", 0.1), ("乙", 0.2), ("丙", 0.8), ("丁", 0.9))])
        return gold, good, bad

    def _run(self, argv: List[str]) -> Tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = bench.main(argv)
        return code, out.getvalue()

    def test_gate_checks_every_metric(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gold, good, bad = self._gold_and_two_metrics(tmp)
            code, _ = self._run(["--gold", gold, "--no-ngram",
                                 "--scores", f"good={good}", "--scores", f"bad={bad}",
                                 "--gate", "0.9"])
        self.assertEqual(code, 1)  # bad 的 AUC=0，只看首指标会漏

    def test_empty_pool_returns_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gold, good, _ = self._gold_and_two_metrics(tmp)
            partial = os.path.join(tmp, "partial.jsonl")
            self._write_jsonl(partial, [{"text": "甲", "score": 0.9}, {"text": "乙", "score": 0.8}])
            code, _ = self._run(["--gold", gold, "--no-ngram", "--scores", f"p={partial}"])
        self.assertEqual(code, 1)  # 负例池为空，不能静默给 AUC=0.5

    def test_unknown_invert_and_grid_metric_exit_early(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gold, good, _ = self._gold_and_two_metrics(tmp)
            base = ["--gold", gold, "--no-ngram", "--scores", f"good={good}"]
            with self.assertRaises(SystemExit):
                bench.main(base + ["--invert", "nope"])
            with self.assertRaises(SystemExit):
                bench.main(base + ["--grid-metric", "nope"])


if __name__ == "__main__":
    unittest.main()
