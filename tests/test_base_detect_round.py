# -*- coding: utf-8 -*-
"""BaseDetectRound 公共检测轮基类的契约测试。

锁定「新增检测后端只需声明类属性」这条约定：
  - 六个检测/复核引擎的类属性金样本（标记字段 / 展示用词 / 日志前缀 / 轮次语义
    / 批次配置链 / 提示词模板），任一处被改动即失败，避免日志与前端文案漂移
  - _resolve_num_per_request：配置链自低到高逐层套用，与逐层嵌套回退等价
  - _prepare_round：首轮清旧标记后取有效译文；复核轮保留标记、只取已标记句
  - _apply_better_result：检测轮绝不写 alt_dst / 主译文
  - _log_summary：首轮命中/无命中、复核确认/撤销两种措辞 + 子类附加片段
"""
import unittest
from unittest.mock import Mock

from GalTransl.CSentense import CSentense
from GalTransl.Backend.BaseDetectRound import BaseDetectRound
from GalTransl.Backend.ForSemCheck import ForSemCheck
from GalTransl.Backend.ForSemCheckAgain import ForSemCheckAgain
from GalTransl.Backend.ForToneCheck import ForToneCheck
from GalTransl.Backend.ForToneCheckAgain import ForToneCheckAgain
from GalTransl.Backend.ForVerboseCheck import ForVerboseCheck
from GalTransl.Backend.ForVerboseCheckAgain import ForVerboseCheckAgain


ALL_ENGINES = (
    ForSemCheck,
    ForSemCheckAgain,
    ForToneCheck,
    ForToneCheckAgain,
    ForVerboseCheck,
    ForVerboseCheckAgain,
)


class _FakeConfig:
    active_workers = 1

    def __init__(self, values: dict | None = None) -> None:
        self._values = values or {}

    def getKey(self, key: str):
        return self._values.get(key)


def _trans(index: int, pre_dst: str = "译文", marker_attr: str = "") -> CSentense:
    t = CSentense(f"src{index}", index=index)
    t.post_src = "原文"
    t.pre_dst = pre_dst
    t.proofread_zh = ""
    if marker_attr:
        setattr(t, marker_attr, "旧标记")
    return t


class ClassAttributeGoldenTests(unittest.TestCase):
    """类属性金样本：新增检测后端抄这张表即可，改动任一值需同步文档与测例。"""

    GOLDEN = {
        ForSemCheck: ("[语义检测]", "suspected_error", "疑似错误", "detect", "sparse"),
        ForSemCheckAgain: ("[语义复核]", "suspected_error", "疑似错误", "review", "confirm"),
        ForToneCheck: ("[色彩检查]", "tone_issue", "色彩不一致", "detect", "sparse"),
        ForToneCheckAgain: ("[色彩复核]", "tone_issue", "色彩不一致", "review", "confirm"),
        ForVerboseCheck: ("[冗长检测]", "verbose_issue", "节奏冗长", "detect", "sparse"),
        ForVerboseCheckAgain: (
            "[冗长复核]",
            "verbose_issue",
            "节奏冗长",
            "review",
            "confirm",
        ),
    }

    def test_golden_attributes(self) -> None:
        for cls, (tag, marker, label, kind, mode) in self.GOLDEN.items():
            self.assertEqual(cls._log_tag, tag, cls.__name__)
            self.assertEqual(cls._marker_attr, marker, cls.__name__)
            self.assertEqual(cls._issue_label, label, cls.__name__)
            self.assertEqual(cls._round_kind, kind, cls.__name__)
            self.assertEqual(cls._parse_mode, mode, cls.__name__)

    def test_prompt_pairs_are_complete(self) -> None:
        for cls in ALL_ENGINES:
            system_prompt, trans_prompt = cls._prompt_pair
            self.assertTrue(system_prompt.strip(), cls.__name__)
            self.assertIn("[TargetLang]", trans_prompt, cls.__name__)
            self.assertIn("[Input]", trans_prompt, cls.__name__)

    def test_review_rounds_disable_echo_retry_and_inject_reason(self) -> None:
        for cls in ALL_ENGINES:
            if cls._round_kind != "review":
                continue
            self.assertFalse(cls._echo_retry_enabled, cls.__name__)
            self.assertEqual(cls._extra_problem_attr, cls._marker_attr, cls.__name__)

    def test_standalone_specs_match_log_tags(self) -> None:
        from GalTransl.Frontend.llm_standalone import STANDALONE_BACKENDS

        for cls in ALL_ENGINES:
            self.assertEqual(
                STANDALONE_BACKENDS[cls.__name__].log_tag, cls._log_tag, cls.__name__
            )

    def test_all_detection_engines_share_the_base(self) -> None:
        for cls in ALL_ENGINES:
            self.assertTrue(issubclass(cls, BaseDetectRound), cls.__name__)


class ResolveNumPerRequestTests(unittest.TestCase):
    def _engine(self, cls, values: dict) -> object:
        obj = object.__new__(cls)
        obj.pj_config = _FakeConfig(values)
        return obj

    def test_verbose_check_key_wins_over_lower_levels(self) -> None:
        values = {
            "gpt.numPerRequestVerboseCheck": 2,
            "gpt.numPerRequestToneCheck": 3,
            "gpt.numPerRequestSemCheck": 4,
            "gpt.numPerRequestBetter": 5,
        }
        obj = self._engine(ForVerboseCheck, values)
        self.assertEqual(obj._resolve_num_per_request(9), 2)

    def test_tone_chain_prefix_order(self) -> None:
        obj = self._engine(ForToneCheck, {})
        self.assertEqual(obj._resolve_num_per_request(9), 9)  # 全缺省 → 调用方实参
        obj.pj_config = _FakeConfig({"gpt.numPerRequestBetter": 5})
        self.assertEqual(obj._resolve_num_per_request(9), 5)
        obj.pj_config = _FakeConfig({"gpt.numPerRequestSemCheck": 4})
        self.assertEqual(obj._resolve_num_per_request(9), 4)
        obj.pj_config = _FakeConfig({"gpt.numPerRequestToneCheck": 3})
        self.assertEqual(obj._resolve_num_per_request(9), 3)

    def test_invalid_value_falls_back(self) -> None:
        obj = self._engine(ForSemCheck, {"gpt.numPerRequestSemCheck": "abc"})
        self.assertEqual(obj._resolve_num_per_request(7), 7)

    def test_zero_argument_falls_back_to_twenty(self) -> None:
        obj = self._engine(ForSemCheck, {})
        self.assertEqual(obj._resolve_num_per_request(0), 20)


class PrepareRoundTests(unittest.TestCase):
    def test_detect_round_clears_old_marks(self) -> None:
        obj = object.__new__(ForSemCheck)
        obj._log_tag = "[语义检测]"
        trans_list = [_trans(1, marker_attr="suspected_error")]
        targets = obj._prepare_round("f.json", trans_list)
        self.assertEqual([t.index for t in targets], [1])
        self.assertEqual(trans_list[0].suspected_error, "")

    def test_review_round_keeps_marks_and_skips_invalid_translation(self) -> None:
        obj = object.__new__(ForSemCheckAgain)
        obj._log_tag = "[语义复核]"
        trans_list = [
            _trans(1, marker_attr="suspected_error"),
            _trans(2, pre_dst="", marker_attr="suspected_error"),
            _trans(3),
        ]
        targets = obj._prepare_round("f.json", trans_list)
        self.assertEqual([t.index for t in targets], [1])
        self.assertEqual(trans_list[0].suspected_error, "旧标记")

    def test_review_round_logs_first_round_hint(self) -> None:
        obj = object.__new__(ForToneCheckAgain)
        obj._log_tag = "[色彩复核]"
        with self.assertLogs("GalTransl", level="INFO") as cm:
            targets = obj._prepare_round("f.json", [_trans(1)])
        self.assertEqual(targets, [])
        self.assertIn("先执行词语色彩检查（ForToneCheck）", "\n".join(cm.output))


class ApplyBetterResultTests(unittest.TestCase):
    def test_detection_round_never_writes_translation(self) -> None:
        obj = object.__new__(ForVerboseCheck)
        trans = _trans(1)
        applied = obj._apply_better_result(trans, "当前译文", "新译文", 1)
        self.assertFalse(applied)
        self.assertEqual(trans.alt_dst, "")
        self.assertEqual(trans.pre_dst, "译文")


class SummaryLogTests(unittest.TestCase):
    def _obj(self, cls) -> object:
        obj = object.__new__(cls)
        obj._log_tag = cls._log_tag
        obj.pj_config = _FakeConfig()
        return obj

    def test_detect_summary_hit(self) -> None:
        obj = self._obj(ForSemCheck)
        with self.assertLogs("GalTransl", level="INFO") as cm:
            obj._log_summary("f.json", {"hit": 3, "confirm": 0, "dismiss": 0})
        self.assertIn("语义检测完成，共命中 3 句", "\n".join(cm.output))

    def test_detect_summary_no_hit_uses_issue_label(self) -> None:
        obj = self._obj(ForVerboseCheck)
        with self.assertLogs("GalTransl", level="INFO") as cm:
            obj._log_summary("f.json", {"hit": 0, "confirm": 0, "dismiss": 0})
        self.assertIn("冗长检测完成，未发现节奏冗长", "\n".join(cm.output))

    def test_review_summary_with_suffix_hook(self) -> None:
        obj = self._obj(ForToneCheckAgain)
        obj._summary_suffix = Mock(return_value="，1 句因色彩基准段为空保留")
        with self.assertLogs("GalTransl", level="INFO") as cm:
            obj._log_summary("f.json", {"hit": 0, "confirm": 2, "dismiss": 1})
        self.assertIn(
            "二次复核完成：确认 2 句，撤销 1 句，1 句因色彩基准段为空保留",
            "\n".join(cm.output),
        )


class RoundStartLogTests(unittest.TestCase):
    """轮次开始日志：措辞由类属性决定，逐字沿用各后端原有文案。"""

    def _obj(self, cls) -> object:
        obj = object.__new__(cls)
        obj._log_tag = cls._log_tag
        return obj

    def test_detect_round_uses_noun(self) -> None:
        cases = {
            ForSemCheck: "开始单轮语义差异检测，共 5 句，1 批",
            ForToneCheck: "开始单轮词语色彩一致性检查，共 5 句，1 批",
            ForVerboseCheck: "开始单轮节奏冗长检测，共 5 句，1 批",
        }
        for cls, expected in cases.items():
            with self.subTest(engine=cls.__name__):
                with self.assertLogs("GalTransl", level="INFO") as cm:
                    self._obj(cls)._log_round_start("f.json", 5, 1)
                self.assertIn(expected, "\n".join(cm.output))

    def test_review_round_uses_fixed_wording(self) -> None:
        for cls in (ForSemCheckAgain, ForToneCheckAgain, ForVerboseCheckAgain):
            with self.subTest(engine=cls.__name__):
                with self.assertLogs("GalTransl", level="INFO") as cm:
                    self._obj(cls)._log_round_start("f.json", 3, 2)
                self.assertIn(
                    "开始二次复核，共 3 句命中句，2 批", "\n".join(cm.output)
                )


if __name__ == "__main__":
    unittest.main()
