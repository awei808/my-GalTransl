# -*- coding: utf-8 -*-
"""人工修订成对集脚本的单测。

覆盖：diff 片段提取、称呼/术语/错译/标点分类、快照 diff 端到端（含只读性）。
"""
import json
import os
import sys
import tempfile
import unittest

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
    def _entry(self, index: int, src: str, dst: str) -> dict:
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


if __name__ == "__main__":
    unittest.main()
