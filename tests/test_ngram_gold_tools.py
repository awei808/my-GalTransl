# -*- coding: utf-8 -*-
"""ngram gold set 采样/评估工具的单测。

覆盖：
  - collect_candidates：译文口径（proofread_dst 优先）、短句剔除、去重
  - build_sample：抽样数量、重复句映射、id 连续、盲标字段不含分数/来源
  - label_of / auc：标签归一化与可分性
  - best_threshold：按误标预算取最高阈值
  - consistency：重复句标注一致率
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

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
        # 盲标：不写分数，不含来源文件，label 待填
        for it in items:
            self.assertEqual(sorted(it), ["dst", "id", "label", "note", "subtype"])
            self.assertEqual(it["label"], "")

    def test_dupes_never_adjacent_across_seeds(self) -> None:
        """重复句与原句至少隔 2 行（回滚 _spread_dupes 即失败）。"""
        for n, dupes in ((5, 2), (12, 3), (20, 2), (40, 4)):
            texts = [f"第{i}个足够长的中文测试句子内容" for i in range(n)]
            for seed in range(5):
                _items, mapping = gs.build_sample(texts, n=n, dupes=dupes, seed=seed)
                for dup_id, orig_id in mapping.items():
                    self.assertGreater(
                        abs(dup_id - orig_id), 2, f"n={n} dupes={dupes} seed={seed} 重复句相邻"
                    )

    def test_tiny_sample_dupes_still_distinct_rows(self) -> None:
        """3 行装不下 2 行间隔时，只要求重复句与原句不是同一行（不重复计数）。"""
        texts = ["甲甲甲甲甲甲甲甲", "乙乙乙乙乙乙乙乙"]
        for seed in range(5):
            _items, mapping = gs.build_sample(texts, n=2, dupes=1, seed=seed)
            self.assertEqual(len(mapping), 1)
            dup_id, orig_id = next(iter(mapping.items()))
            self.assertNotEqual(dup_id, orig_id)

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


class MainAutoDupesTests(unittest.TestCase):
    def test_main_excludes_sidecar_dupes(self) -> None:
        """不给 --dupes 时也应按同名旁路文件排除重复句（回滚探测逻辑即失败）。"""
        rows = [
            {"id": 1, "dst": "甲" * 8, "label": "natural"},
            {"id": 2, "dst": "乙" * 8, "label": "awkward"},
            {"id": 3, "dst": "甲" * 8, "label": "natural"},
        ]
        scores = {"甲" * 8: 0.9, "乙" * 8: 0.1}
        with tempfile.TemporaryDirectory() as tmp:
            gold = os.path.join(tmp, "g.jsonl")
            with open(gold, "w", encoding="utf-8") as fh:
                fh.write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
            with open(gold + ".dupes.json", "w", encoding="utf-8") as fh:
                json.dump({"3": 1}, fh)
            out = io.StringIO()
            with mock.patch.object(ge.nr, "load_index", return_value=(object(), "")), mock.patch.object(
                ge.nr, "score_text", side_effect=lambda text, _idx, _lam: scores[text.strip()]
            ), contextlib.redirect_stdout(out):
                code = ge.main(["--gold", gold])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("重复句排除 1", text)
        self.assertIn("正例 1，负例 1", text)


class SpreadDupesTests(unittest.TestCase):
    def test_moves_adjacent_dupe_away_from_origin(self) -> None:
        rows = [
            {"text": "A", "origin": 0, "dup_of": None},
            {"text": "A", "origin": None, "dup_of": 0},
            {"text": "B", "origin": 1, "dup_of": None},
            {"text": "C", "origin": 2, "dup_of": None},
            {"text": "D", "origin": 3, "dup_of": None},
        ]
        gs._spread_dupes(rows)
        dup_pos = next(i for i, r in enumerate(rows) if r["dup_of"] is not None)
        origin_pos = next(i for i, r in enumerate(rows) if r.get("origin") == 0)
        self.assertGreater(abs(dup_pos - origin_pos), 2)
        # 换位只搬动纯原句行，原句集合不变
        self.assertEqual({r["origin"] for r in rows if r["origin"] is not None}, {0, 1, 2, 3})


class FixtureTests(unittest.TestCase):
    """入库金标的结构契约（内容变化需同步 description/counts）。"""

    def test_fixture_is_well_formed(self) -> None:
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fixtures", "ngram_gold_set.json"
        )
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        items = data["items"]
        self.assertEqual(len(items), data["counts"]["natural"] + data["counts"]["awkward"])
        self.assertEqual(len({it["id"] for it in items}), len(items))
        labels = [it["label"] for it in items]
        self.assertEqual(set(labels), {"natural", "awkward"})
        self.assertEqual(labels.count("natural"), data["counts"]["natural"])
        # 每条都必须能被打分器评分（汉字不足的句子在采样阶段已剔除）
        for it in items:
            self.assertGreaterEqual(nr.han_char_count(nr.normalize_text(it["dst"])), nr.MIN_SENT_CHARS)
        self.assertEqual(ge.load_gold(path)[0]["id"], items[0]["id"])


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

    def test_tolerates_non_list_items(self) -> None:
        """items 为对象（非法形态）时应跳过该文件而不是抛 TypeError。"""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.json"), "w", encoding="utf-8") as fh:
                json.dump({"items": {"a": 1}}, fh)
            with self.assertRaises(SystemExit):
                gs.resolve_cache_dir(tmp)


if __name__ == "__main__":
    unittest.main()
