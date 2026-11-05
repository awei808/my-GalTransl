# -*- coding: utf-8 -*-
"""人工修订成对集脚本的单测。

覆盖：diff 片段提取、称呼/术语/错译/标点分类、快照 diff 端到端（含只读性）。
"""
import json
import os
import sys
import tempfile
import unittest
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ngram_revision_pairs as rp


class DiffFragmentsTests(unittest.TestCase):
    def test_replace_delete_insert(self) -> None:
        self.assertEqual(rp.diff_fragments("分开来看单看每个部位", "分开来看各个部位"), ("单看每", "各"))
        self.assertEqual(rp.diff_fragments("为此，首先，得先看看", "为此，得先看看"), ("，首先", ""))
        self.assertEqual(rp.diff_fragments("为了创", "为了创先生"), ("", "先生"))

    def test_identical_text(self) -> None:
        self.assertEqual(rp.diff_fragments("一样", "一样"), ("", ""))


class ClassifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.terms = {"创", "创先生", "创君", "您", "你", "Cos塔利亚"}
        self.names = {"创", "凛音", "华恋"}

    def test_honorific_only_is_excluded(self) -> None:
        for removed, added in (("君", "先生"), ("", "先生"), ("创君", "创先生"), ("先——生——", "")):
            diff_type, category, included = rp.classify(removed, added, self.terms, self.names)
            self.assertEqual(diff_type, "称呼口径", (removed, added))
            self.assertEqual(category, "consistency")
            self.assertFalse(included)

    def test_glossary_or_voice_change_is_excluded(self) -> None:
        diff_type, category, included = rp.classify("你", "您", self.terms, self.names)
        self.assertEqual(diff_type, "术语/语体口径")
        self.assertFalse(included)

    def test_large_rewrite_is_excluded(self) -> None:
        diff_type, category, included = rp.classify("全权了或", "姿势是创君的之类的", self.terms, self.names)
        self.assertEqual((diff_type, category, included), ("大幅改写/疑似错译", "adequacy", False))

    def test_naturalness_edits_are_kept(self) -> None:
        for removed, added, expected in (
            ("，首先", "", "删冗/精简"),
            ("单看每", "各", "措辞/搭配"),
            ("顶点", "最高潮", "措辞/搭配"),
        ):
            diff_type, category, included = rp.classify(removed, added, self.terms, self.names)
            self.assertEqual(diff_type, expected, (removed, added))
            self.assertTrue(included)

    def test_punctuation_only_is_excluded(self) -> None:
        self.assertEqual(
            rp.classify("，", "。", self.terms, self.names)[0], "标点/格式"
        )


class VocabTests(unittest.TestCase):
    def test_loads_legacy_dict_separators(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "项目GPT字典.txt"), "w", encoding="utf-8") as fh:
                fh.write("//整行注释\n====\n創\t创/创先生\n華恋    华恋\n凛音->凛音\n")
            terms, names = rp.load_vocab(tmp)
        for word in ("创", "创先生", "华恋", "凛音"):
            self.assertIn(word, terms)
        self.assertEqual(names, set())  # 无 name 替换表时人名集合为空

    def test_loads_dict_and_name_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "项目GPT字典.txt"), "w", encoding="utf-8") as fh:
                fh.write("//注释\n創|创/创先生/创君|男主\n華恋|华恋|姐姐\n")
            with open(os.path.join(tmp, "name替换表.csv"), "w", encoding="utf-8-sig") as fh:
                fh.write("SRC_Name,DST_Name,Count\n創,创,4268\n凛音,凛音,3218\n")
            terms, names = rp.load_vocab(tmp)
        self.assertIn("创先生", terms)
        self.assertIn("华恋", terms)
        self.assertEqual(names, {"创", "凛音"})


class CollectPairsTests(unittest.TestCase):
    def _entry(self, index: int, src: str, dst: str) -> Dict[str, object]:
        return {"index": index, "post_src": src, "pre_dst": dst, "proofread_dst": "", "name": "凛音"}

    def test_snapshot_diff_is_read_only_and_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cur_dir = os.path.join(tmp, "transl_cache", "pass3_cache")
            snap_dir = os.path.join(tmp, "transl_cache", ".snapshots", "pass3_cache")
            os.makedirs(cur_dir)
            os.makedirs(snap_dir)
            name = "01_test.txt.json"
            old = [
                self._entry(0, "原文一", "分开来看单看每个部位。"),
                self._entry(1, "原文二", "创君，你该不会是紧张了吧？"),
                self._entry(2, "原文三", "这一句完全没有改动。"),
            ]
            new = [
                self._entry(0, "原文一", "分开来看各个部位。"),
                self._entry(1, "原文二", "创先生，你该不会是紧张了吧？"),
                self._entry(2, "原文三", "这一句完全没有改动。"),
            ]
            with open(os.path.join(snap_dir, name + ".20260101-000000-000000.bak"), "w", encoding="utf-8") as fh:
                json.dump(old, fh, ensure_ascii=False)
            with open(os.path.join(cur_dir, name), "w", encoding="utf-8") as fh:
                json.dump(new, fh, ensure_ascii=False)
            before = {p: os.stat(os.path.join(d, f)).st_mtime_ns
                      for d, f, p in ((cur_dir, name, "cur"), (snap_dir, os.listdir(snap_dir)[0], "snap"))}

            pairs, stats = rp.collect_pairs(tmp, {"创君", "创先生"}, {"创"})

            after = {p: os.stat(os.path.join(d, f)).st_mtime_ns
                     for d, f, p in ((cur_dir, name, "cur"), (snap_dir, os.listdir(snap_dir)[0], "snap"))}
            self.assertEqual(before, after)  # 只读
            self.assertEqual(stats["changed"], 2)
            self.assertEqual(len(pairs), 2)
            included = [p for p in pairs if p["included"]]
            self.assertEqual(len(included), 1)
            self.assertEqual(included[0]["diff_type"], "措辞/搭配")
            self.assertEqual(included[0]["src"], "原文一")
            self.assertEqual([p["diff_type"] for p in pairs if not p["included"]], ["称呼口径"])



class ExtraRevisionTests(unittest.TestCase):
    """审查跟进：多快照、旧布局、异常 index、覆写保护与相对阈值。"""

    def _entry(self, index: object, dst: str) -> Dict[str, object]:
        return {"index": index, "post_src": "原文", "pre_dst": dst, "name": "凛音"}

    def _write(self, path: str, rows: List[dict]) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False)

    def _project(self, tmp: str, top_level: bool = False) -> str:
        cur = os.path.join(tmp, "transl_cache", "" if top_level else "pass3_cache")
        snap = os.path.join(tmp, "transl_cache", ".snapshots", "" if top_level else "pass3_cache")
        name = "01_test.txt.json"
        self._write(os.path.join(snap, name + ".20260101-000000-000000.bak"),
                    [self._entry(0, "甲甲甲甲甲甲"), self._entry(1, "乙乙乙乙乙乙")])
        self._write(os.path.join(snap, name + ".20260102-000000-000000.bak"),
                    [self._entry(0, "甲甲乙甲甲甲"), self._entry(1, "乙乙乙乙乙乙")])
        self._write(os.path.join(cur, name),
                    [self._entry(0, "甲丙乙甲甲甲"), self._entry(1, "乙乙乙乙乙丙")])
        return tmp

    def test_adjacent_captures_intermediate_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(tmp)
            cumulative, _ = rp.collect_pairs(project, set(), set(), adjacent=False)
            adjacent, _ = rp.collect_pairs(project, set(), set(), adjacent=True)
        self.assertEqual(len(cumulative), 2)
        self.assertEqual(len(adjacent), 3)  # A→B 一处 + B→当前两处
        self.assertEqual({p["version"] for p in cumulative}, {"earliest->current"})
        self.assertEqual({p["version"] for p in adjacent}, {"v0->v1", "v1->v2"})

    def test_top_level_cache_layout_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(tmp, top_level=True)
            pairs, stats = rp.collect_pairs(project, set(), set())
        self.assertEqual(stats["compared_files"], 1)
        self.assertEqual(len(pairs), 2)

    def test_bad_and_duplicate_index_do_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cur = os.path.join(tmp, "transl_cache", "pass3_cache")
            snap = os.path.join(tmp, "transl_cache", ".snapshots", "pass3_cache")
            name = "01_test.txt.json"
            self._write(os.path.join(snap, name + ".20260101-000000-000000.bak"),
                        [self._entry(0, "甲甲甲甲甲甲"), self._entry(0, "乙乙乙乙乙乙"),
                         self._entry("bad", "丙丙丙丙丙丙")])
            self._write(os.path.join(cur, name),
                        [self._entry(0, "甲甲甲甲甲乙"), self._entry("bad", "丙丙丙丙丙丁")])
            pairs, stats = rp.collect_pairs(tmp, set(), set())
        self.assertGreaterEqual(stats["bad_index"], 1)
        self.assertEqual(stats["dup_index"], 1)
        self.assertTrue(all(isinstance(p["index"], int) for p in pairs))

    def test_check_outputs_requires_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "pairs.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{}")
            with self.assertRaises(SystemExit):
                rp._check_outputs(path, force=False)
            rp._check_outputs(path, force=True)

    def test_relative_rewrite_threshold_keeps_long_polish(self) -> None:
        # 长句里 9 字的润色不应被当错译（绝对阈值 8 会误判）
        diff_type, category, included = rp.classify("非常非常地", "相当自然地", set(), set(), ref_len=60)
        self.assertEqual(category, "naturalness")
        self.assertTrue(included)


if __name__ == "__main__":
    unittest.main()
