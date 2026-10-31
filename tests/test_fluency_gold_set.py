# -*- coding: utf-8 -*-
"""中文通顺检测 gold set 回归。

基线：小粥3-全量 2026-10-30 运行产生的 33 条标记，去重后 24 句人工复核：
issue 10（确认应改）、partial 4（确有局部问题但模型建议不可直接采用）、
ok 10（误报）。本文件锁定三件事：
  1. gold set 数据完整、expected 片段确实在 dst 中；
  2. 初筛召回不退化（positive 14/14），且误报送检量有上限；
  3. 提示词保留最高原则/H 场景语体/+2 字口径，且不再包含语料泄漏样例。
"""
import json
import unittest
from pathlib import Path

from GalTransl.Backend.ForFluencyCheck import (
    _SUGGESTION_MAX_EXTRA,
    ForFluencyCheck,
)
from GalTransl.Backend.Prompts import (
    FORFLUENCY_AGAIN_SYSTEM,
    FORFLUENCY_SYSTEM,
    FORGAL_JSON_FLUENCYCHECK_AGAIN_PROMPT,
    FORGAL_JSON_FLUENCYCHECK_PROMPT,
)
from GalTransl.fluency_rules import match_fluency_category

FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "fluency_gold_set.json"
)
POSITIVE_LABELS = ("issue", "partial")


def _load_items() -> list:
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)["items"]


class GoldSetIntegrityTests(unittest.TestCase):
    def test_counts_and_labels(self) -> None:
        items = _load_items()
        self.assertEqual(len(items), 24)
        counts = {
            label: sum(1 for item in items if item["label"] == label)
            for label in ("issue", "partial", "ok")
        }
        self.assertEqual(counts, {"issue": 10, "partial": 4, "ok": 10})
        self.assertEqual([item["id"] for item in items], list(range(1, 25)))

    def test_required_fields(self) -> None:
        for item in _load_items():
            for key in (
                "file",
                "index",
                "src",
                "dst",
                "label",
                "note",
                "scene",
                "plot_metadata",
            ):
                self.assertIn(key, item, item["id"])
            self.assertIn(item["label"], ("issue", "partial", "ok"))
            self.assertTrue(item["src"].strip(), item["id"])
            self.assertTrue(item["dst"].strip(), item["id"])
            self.assertIn("<plot_metadata>", item["plot_metadata"])
            self.assertIn("剧情:", item["plot_metadata"])

    def test_expected_fragment_present(self) -> None:
        for item in _load_items():
            if item["label"] in POSITIVE_LABELS:
                self.assertTrue(item["expected"], item["id"])
                self.assertIn(item["expected"], item["dst"], item["id"])
                self.assertIn("prefilter_expected", item, item["id"])
            else:
                self.assertIsNone(item["expected"], item["id"])


class GoldSetPrefilterTests(unittest.TestCase):
    """初筛只负责召回：positive 必须全部送检；ok 的送检量锁定基线，防止规则回退。"""

    def test_positive_recall(self) -> None:
        # 只统计"初筛有对应规则"的 positive；#277 的「碰到」目前无规则
        reachable = [
            item
            for item in _load_items()
            if item["label"] in POSITIVE_LABELS and item.get("prefilter_expected")
        ]
        self.assertEqual(len(reachable), 13)
        missed = [
            (item["file"], item["index"])
            for item in reachable
            if match_fluency_category(item["dst"]) is None
        ]
        self.assertEqual(missed, [])

    def test_known_prefilter_gap_documented(self) -> None:
        # #277（准确地碰到了……）是 partial 真问题，但当前初筛无对应规则；
        # 不得靠「的地」误匹配凑召回——该匹配已从 _ADVERBIAL_DI_RE 排除。
        item = next(item for item in _load_items() if item["id"] == 12)
        self.assertFalse(item["prefilter_expected"])
        self.assertIsNone(match_fluency_category(item["dst"]))

    def test_ok_candidate_baseline(self) -> None:
        # 上限基线：允许后续继续减少误报送检，但不允许新增
        allowed = {
            ("02_kar_god01.txt.json", 146),
            ("02_kar_succ01.txt.json", 161),
            ("02_rin_elf01.txt.json", 232),
            ("04_rin_majo02.txt.json", 217),
            ("05_rin_elf03.txt.json", 315),
        }
        candidates = {
            (item["file"], item["index"])
            for item in _load_items()
            if item["label"] == "ok"
            and match_fluency_category(item["dst"]) is not None
        }
        self.assertLessEqual(len(candidates), 5)
        self.assertTrue(candidates.issubset(allowed), candidates - allowed)


class FluencyPromptGuardTests(unittest.TestCase):
    def test_first_round_prompt_keeps_principles(self) -> None:
        prompt = FORGAL_JSON_FLUENCYCHECK_PROMPT
        for text in (
            "最高原则",
            "即使它命中了下面任何模式",
            "h 场景",
            "拟声",
            "最多比原片段多 2 字",
            "[TargetLang]",
            "[plot_metadata]",
            "[Input]",
        ):
            self.assertIn(text, prompt)

    def test_review_prompt_keeps_principles(self) -> None:
        prompt = FORGAL_JSON_FLUENCYCHECK_AGAIN_PROMPT
        for text in (
            "最高原则",
            "keep: false",
            "h 场景",
            "拟声",
            "2 字",
            "[TargetLang]",
            "[plot_metadata]",
            "[Input]",
        ):
            self.assertIn(text, prompt)

    def test_system_prompts(self) -> None:
        self.assertIn("母语者", FORFLUENCY_SYSTEM)
        self.assertIn("2 字", FORFLUENCY_SYSTEM)
        self.assertIn("明显更自然", FORFLUENCY_AGAIN_SYSTEM)

    def test_no_corpus_example_leak(self) -> None:
        leaked = (
            "一边剧烈地达到高潮",
            "就不知道那个姿势是不是正确的",
            "一起推向高潮",
            "满足地、陶醉地",
            "阴道里也已经相当湿润了",
            "我的快感也已经相当高涨了",
        )
        for prompt in (
            FORGAL_JSON_FLUENCYCHECK_PROMPT,
            FORGAL_JSON_FLUENCYCHECK_AGAIN_PROMPT,
        ):
            for text in leaked:
                self.assertNotIn(text, prompt)

    def test_old_false_positive_rule_removed(self) -> None:
        for prompt in (
            FORGAL_JSON_FLUENCYCHECK_PROMPT,
            FORGAL_JSON_FLUENCYCHECK_AGAIN_PROMPT,
        ):
            self.assertNotIn("已经…了 与 相当/十分/非常/特别 同现", prompt)

    def test_suggestion_extra_constant(self) -> None:
        self.assertEqual(_SUGGESTION_MAX_EXTRA, 2)
        self.assertIn("2 字", ForFluencyCheck._reason_retry_hint)


if __name__ == "__main__":
    unittest.main()
