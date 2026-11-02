# -*- coding: utf-8 -*-
"""ForFluencyCheck 中文通顺检测的单元测试。

覆盖：
  - fluency_rules 规则初筛：三条真实种子句（#40/#217/#283）分别命中 B/A/C 类，
    干净句不命中；初筛只管召回（气氛推向高潮/她达到了高潮 也召回，由 LLM 否决）
  - _parse_fix_response：reason 必须是「类别：「原片段」→「建议片段」(依据:…)」，
    原片段须在 dst 中、建议片段非空/不同/最多比原片段多 2 字（覆写后的证据校验口径）
  - find_problems 认领：fluency_issue 非空 → 输出「中文不顺」（reason 附带展示）
  - _FIX_SPECS：认领类型必须注册统一修复指令（否则勾选修复时无指令可用）
  - batch_translate：规则初筛压缩送检量（干净句不送 LLM）；初筛无命中整轮跳过；
    复核轮不做初筛（已标记句即使 dst 不再命中规则也送复核）
  - 分批：优先 gpt.numPerRequestFluencyCheck，回退 VerboseCheck / SemCheck / 实参
  - 整批回显：追加纠正提示重试一次
  - Cache._build_cache_obj：fluency_issue 随快照落盘
  - 注册链：ENGINE_MODULE_PATHS / ENGINE_REGISTRY / NEED_OpenAITokenPool /
    STANDALONE_BACKENDS / 问题类型目录同步 / 复核轮哨兵口径
"""
import unittest
from unittest.mock import Mock, patch

from GalTransl.CSentense import CSentense
from GalTransl.Cache import _build_cache_obj
from GalTransl.Problem import find_problems
from GalTransl.ConfigHelper import CProblemType, PROBLEM_TYPE_CATALOG_ORDER
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_FLUENCYCHECK_AGAIN_PROMPT,
    FORGAL_JSON_FLUENCYCHECK_PROMPT,
)
from GalTransl.Backend.ForFluencyCheck import ForFluencyCheck
from GalTransl.Backend.ForFluencyCheckAgain import ForFluencyCheckAgain
from GalTransl.fluency_rules import find_fluency_candidates, match_fluency_category

# 三条真实种子句（来自小粥3 pass3_cache，problem 均为空的漏检真空区）
SEED_40_DST = "虽然现在已经很舒服了，\n不照着原作Ob★Gra来，就不知道那个姿势是不是正确的。"
SEED_217_DST = "然后就如她所说，一边剧烈地达到高潮，一边喷了出来。"
SEED_283_DST = "我和华恋相视点头，\n一起推向高潮。"


class _FluencyOnlyPjConfig:
    """find_problems 配置替身：仅启用「中文不顺」检测项。"""

    def getProblemAnalyzeArinashiDict(self) -> dict:
        return {}

    def getProblemAnalyzeConfig(self, key: str) -> list:
        return [CProblemType.中文不顺] if key == "problemList" else []

    def hasProblemAnalyzeConfig(self, key: str) -> bool:
        return True

    def getKey(self, key: str):
        return None


def _make_parser() -> ForFluencyCheck:
    """绕过重型 __init__，仅装配解析与筛选所需属性。"""
    obj = object.__new__(ForFluencyCheck)
    obj._log_tag = "[通顺检测]"
    obj._disabled_reason = ""
    obj.pj_config = _FluencyOnlyPjConfig()
    return obj


def _trans(
    index: int,
    pre_dst: str = "译文",
    post_src: str = "原文",
    proofread_zh: str = "",
) -> CSentense:
    t = CSentense(f"src{index}", index=index)
    t.post_src = post_src
    t.pre_dst = pre_dst
    t.proofread_zh = proofread_zh
    t.fluency_issue = ""
    return t


def problem_types_of(problem: str) -> list:
    """problem 的类型名列表：与前端 problemTypesOf 同一切分口径。"""
    return [
        seg.strip().split("：")[0].split(":")[0]
        for seg in problem.split(",")
        if seg.strip()
    ]


class FluencyRulesTests(unittest.TestCase):
    """规则初筛：三条种子分别命中 B/A/C 类；初筛只管召回，误报由 LLM 否决。"""

    def test_lang_supports_fluency(self) -> None:
        # target_lang 取值为 LANG_SUPPORTED 的值（Simplified_Chinese 等），
        # 非 zh 开头：语言门禁必须按 "Chinese" 子串判断（曾按 startswith("zh")
        # 误禁用全部中文项目的回归锁定）
        from GalTransl.Backend.ForFluencyCheck import _lang_supports_fluency

        self.assertTrue(_lang_supports_fluency("Simplified_Chinese"))
        self.assertTrue(_lang_supports_fluency("Traditional_Chinese"))
        self.assertFalse(_lang_supports_fluency("English"))
        self.assertFalse(_lang_supports_fluency("Japanese"))
        self.assertFalse(_lang_supports_fluency(""))

    def test_judge_tail_matches_inside_closing_quote(self) -> None:
        # 句末闭引号/括号前收尾同样召回
        self.assertEqual(match_fluency_category("「就不知道是不是正确的」"), "B")

    def test_seed_40_hits_b(self) -> None:
        self.assertEqual(match_fluency_category(SEED_40_DST), "B")

    def test_seed_217_hits_a(self) -> None:
        self.assertEqual(match_fluency_category(SEED_217_DST), "A")

    def test_seed_283_hits_c(self) -> None:
        self.assertEqual(match_fluency_category(SEED_283_DST), "C")

    def test_clean_sentence_not_matched(self) -> None:
        self.assertIsNone(match_fluency_category("她的手指微微颤抖着。"))
        self.assertIsNone(match_fluency_category("是昨天告诉我的。"))

    def test_recall_only_hits_still_matched(self) -> None:
        # 气氛作主语时搭配成立、叙述句达到高潮自然——但初筛按字面量召回，
        # 否决权在 LLM 复核（本测试锁定该口径，防止否决逻辑被挪进初筛）
        self.assertEqual(match_fluency_category("气氛被推向高潮。"), "C")
        self.assertEqual(match_fluency_category("她达到了高潮。"), "A")

    def test_d_co_occurrence_rules(self) -> None:
        # 「已经+程度+了」是标准完成体，不再召回（gold set 多例误报源）
        self.assertIsNone(match_fluency_category("他已经十分疲惫了。"))
        # 真正的冗赘形态是状态名词化
        self.assertEqual(match_fluency_category("他已经是十分疲惫的状态了。"), "D")
        self.assertEqual(match_fluency_category("已经到了非常危险的状态。"), "D")
        self.assertEqual(match_fluency_category("缓缓地轻轻地抚摸着。"), "D")
        # 单出现是合法用法：不召回
        self.assertIsNone(match_fluency_category("非常好看。"))
        self.assertIsNone(match_fluency_category("已经结束了。"))

    def test_reaction_not_light_verb_candidate(self) -> None:
        # 「做出反应」是固定自然搭配，不再作为初筛候选
        self.assertIsNone(match_fluency_category("我对他做出了反应。"))

    def test_discussion_light_verb_matched(self) -> None:
        # 提示词校准样例「进行了讨论→讨论了」必须能被初筛召回，避免样例与规则脱节
        self.assertEqual(match_fluency_category("他对这个方案进行了讨论。"), "A")

    def test_de_di_not_counted_as_adverb(self) -> None:
        # 「的地」是名词短语（舒服的地方），不能算第二个「～地」状语
        self.assertIsNone(match_fluency_category("却准确地碰到了凛音最舒服的地方。"))

    def test_proofread_zh_preferred_over_pre_dst(self) -> None:
        trans = _trans(1, pre_dst="达到高潮", proofread_zh="攀上了高潮")
        candidates, _ = find_fluency_candidates([trans])
        self.assertEqual(candidates, [])  # 校对稿已修好 → 不再召回

    def test_empty_dst_skipped(self) -> None:
        trans = _trans(1, pre_dst="", proofread_zh="")
        candidates, counts = find_fluency_candidates([trans])
        self.assertEqual(candidates, [])
        self.assertEqual(sum(counts.values()), 0)


class ParseFluencyJsonlineTests(unittest.TestCase):
    """首轮稀疏解析：覆写后的证据校验（原片段在 dst、建议不空/不同/最多 +2 字）。"""

    def test_valid_reason_kept(self) -> None:
        parser = _make_parser()
        trans = _trans(1, pre_dst=SEED_217_DST)
        hit, _ = parser._parse_fix_response(
            'ab1|{"id": 1, "reason": "A：「达到高潮」→「高潮」(依据:轻动词冗余，删之信息不减)"}',
            [trans],
            "\r\n",
        )
        self.assertEqual(hit, 1)
        self.assertEqual(
            trans.fluency_issue,
            "A：「达到高潮」→「高潮」(依据:轻动词冗余，删之信息不减)",
        )

    def test_equal_length_suggestion_kept(self) -> None:
        # 等长也允许（#283 推向高潮→攀上高潮 4→4）
        parser = _make_parser()
        trans = _trans(2, pre_dst=SEED_283_DST)
        parser._parse_fix_response(
            'cd2|{"id": 2, "reason": "C：「推向高潮」→「攀上高潮」(依据:人不能推向高潮)"}',
            [trans],
            "\r\n",
        )
        self.assertEqual(
            trans.fluency_issue, "C：「推向高潮」→「攀上高潮」(依据:人不能推向高潮)"
        )

    def test_suggestion_up_to_two_chars_longer_kept(self) -> None:
        # 放宽 +2 字：允许用更自然的改写换取略长（脱力了下来→整个人没了力气 5→7）
        parser = _make_parser()
        trans = _trans(11, pre_dst="终于，凛音脱力了下来。")
        hit, _ = parser._parse_fix_response(
            'ab11|{"id": 11, "reason": "C：「脱力了下来」→「整个人没了力气」(依据:日语词加趋向补语)"}',
            [trans],
            "\r\n",
        )
        self.assertEqual(hit, 1)
        self.assertNotEqual(trans.fluency_issue, "")

    def test_suggestion_longer_discarded(self) -> None:
        # 硬约束机械兜底：超过 +2 字上限（高潮 2→达到了高潮 5，+3）丢弃
        parser = _make_parser()
        trans = _trans(3, pre_dst=SEED_217_DST)
        quality: dict = {}
        hit, _ = parser._parse_fix_response(
            'ef3|{"id": 3, "reason": "A：「高潮」→「达到了高潮」(依据:补全)"}',
            [trans],
            "\r\n",
            quality,
        )
        self.assertEqual((hit, trans.fluency_issue), (0, ""))
        self.assertEqual(quality.get("no_reason"), 1)

    def test_suggestion_same_as_original_discarded(self) -> None:
        parser = _make_parser()
        trans = _trans(4, pre_dst=SEED_217_DST)
        quality: dict = {}
        parser._parse_fix_response(
            'gh4|{"id": 4, "reason": "A:「达到高潮」→「达到高潮」(依据:不变)"}',
            [trans],
            "\r\n",
            quality,
        )
        self.assertEqual(trans.fluency_issue, "")
        self.assertEqual(quality.get("no_reason"), 1)

    def test_original_not_in_dst_discarded(self) -> None:
        # 模型编造片段：dst 里没有原片段，判定不可信
        parser = _make_parser()
        trans = _trans(5, pre_dst="一句很短的译文")
        quality: dict = {}
        hit, _ = parser._parse_fix_response(
            'ij5|{"id": 5, "reason": "B:「是不是对的」→「对不对」(依据:冗赘)"}',
            [trans],
            "\r\n",
            quality,
        )
        self.assertEqual((hit, trans.fluency_issue), (0, ""))
        self.assertEqual(quality.get("no_reason"), 1)

    def test_wrong_fragment_count_discarded(self) -> None:
        # 只给一个片段 / 依据里也写「」造成三片段：都不是约定格式
        parser = _make_parser()
        trans = _trans(6, pre_dst=SEED_217_DST)
        quality: dict = {}
        parser._parse_fix_response(
            'kl6|{"id": 6, "reason": "A:「达到高潮」(依据:冗余)"}',
            [trans],
            "\r\n",
            quality,
        )
        self.assertEqual(trans.fluency_issue, "")
        parser._parse_fix_response(
            'mn6|{"id": 6, "reason": "A:「达到高潮」→「高潮」(依据:「删掉即可」)"}',
            [trans],
            "\r\n",
        )
        self.assertEqual(trans.fluency_issue, "")

    def test_placeholder_and_empty_reason_discarded(self) -> None:
        parser = _make_parser()
        trans = _trans(7, pre_dst=SEED_217_DST)
        quality: dict = {}
        hit, _ = parser._parse_fix_response(
            'op7|{"id": 7, "reason": "中文不顺"}', [trans], "\r\n", quality
        )
        self.assertEqual((hit, trans.fluency_issue, quality.get("no_reason")), (0, "", 1))
        hit, _ = parser._parse_fix_response('qr7|{"id": 7}', [trans], "\r\n", {})
        self.assertEqual((hit, trans.fluency_issue), (0, ""))

    def test_fragment_line_break_encoding_restored(self) -> None:
        # 与基类同口径：<br> 编码的换行在比对前还原，含换行的原片段可命中
        parser = _make_parser()
        trans = _trans(8, pre_dst="一边剧烈地达到<br>高潮了")
        parser._parse_fix_response(
            'st8|{"id": 8, "reason": "A:「达到<br>高潮」→「高潮」(依据:轻动词冗余)"}',
            [trans],
            "\r\n",
        )
        self.assertEqual(
            trans.fluency_issue, "A:「达到<br>高潮」→「高潮」(依据:轻动词冗余)"
        )

    def test_echo_line_with_src_dst_discarded(self) -> None:
        parser = _make_parser()
        trans = _trans(9)
        quality: dict = {}
        hit, _ = parser._parse_fix_response(
            'uv9|{"id": 9, "src": "原文", "dst": "译文"}', [trans], "\r\n", quality
        )
        self.assertEqual((hit, trans.fluency_issue), (0, ""))
        self.assertEqual(quality.get("echo_lines"), 1)


class FluencyClaimTests(unittest.TestCase):
    def test_reason_appended_in_problem(self) -> None:
        trans = _trans(1)
        trans.fluency_issue = "A：「达到高潮」→「高潮」(依据:轻动词冗余)"
        find_problems([trans], _FluencyOnlyPjConfig())
        self.assertEqual(
            trans.problem, "中文不顺：A：「达到高潮」→「高潮」(依据:轻动词冗余)"
        )
        self.assertEqual(problem_types_of(trans.problem), ["中文不顺"])

    def test_sentinel_claimed_plain(self) -> None:
        trans = _trans(2)
        trans.fluency_issue = "1"
        find_problems([trans], _FluencyOnlyPjConfig())
        self.assertEqual(trans.problem, "中文不顺")

    def test_type_disabled_does_not_claim(self) -> None:
        class _NoTypes(_FluencyOnlyPjConfig):
            def getProblemAnalyzeConfig(self, key: str) -> list:
                return []

        trans = _trans(3)
        trans.fluency_issue = "A：「达到高潮」→「高潮」"
        find_problems([trans], _NoTypes())
        self.assertEqual(trans.problem, "")

    def test_non_string_marker_claimed_plain(self) -> None:
        trans = _trans(4)
        trans.fluency_issue = None
        find_problems([trans], _FluencyOnlyPjConfig())
        self.assertEqual(trans.problem, "中文不顺")


class FixSpecTests(unittest.TestCase):
    def test_claimed_type_has_fix_spec(self) -> None:
        # 认领即必须注册修复指令：否则统一修复勾选后既无指令也拿不到原文
        from GalTransl.Backend.ForFixRound import _FIX_SPECS, MODE_SRC_DST

        spec = _FIX_SPECS.get(CProblemType.中文不顺)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.mode, MODE_SRC_DST)
        self.assertIn("替换建议", spec.instruction)


class CacheRoundTripTests(unittest.TestCase):
    def test_fluency_issue_persisted(self) -> None:
        trans = _trans(1)
        trans.fluency_issue = "A：「达到高潮」→「高潮」"
        cache_obj = _build_cache_obj(trans)
        self.assertEqual(cache_obj["fluency_issue"], "A：「达到高潮」→「高潮」")

    def test_empty_marker_not_persisted(self) -> None:
        cache_obj = _build_cache_obj(_trans(2))
        self.assertNotIn("fluency_issue", cache_obj)

    def test_rebuild_trans_list_keeps_fluency_issue(self) -> None:
        # /cache/save 等链路经 _rebuild_trans_list_with_postprocess 重建 CSentense：
        # fluency_issue 不映射会在重检时丢标记（与 suspected/tone/verbose 同口径）
        from GalTransl.server_cache import _rebuild_trans_list_with_postprocess

        entries = [
            {
                "index": 1,
                "name": "",
                "pre_src": "原文",
                "post_src": "原文",
                "pre_dst": SEED_217_DST,
                "fluency_issue": "A:「达到高潮」→「高潮」",
            }
        ]
        with patch(
            "GalTransl.Frontend.LLMTranslate.preprocess_trans_list",
            side_effect=lambda cfg, tl: tl,
        ), patch(
            "GalTransl.Frontend.LLMTranslate.postprocess_trans_list",
            side_effect=lambda cfg, tl: tl,
        ):
            trans_list = _rebuild_trans_list_with_postprocess(
                entries, None, None, None, []
            )
        self.assertEqual(len(trans_list), 1)
        self.assertEqual(trans_list[0].fluency_issue, "A:「达到高潮」→「高潮」")


class _FakeBatchConfig:
    """分批配置替身：可控 getKey 返回值。"""

    active_workers = 1

    def __init__(self, values: dict) -> None:
        self._values = values

    def getKey(self, key: str):
        return self._values.get(key)


class _FluencyCheckTestBase:
    """装配 batch_translate 测试替身的公共方法。"""

    def _make_obj(
        self,
        values: dict,
        llm_resp: str = "",
        metadata_block: str = "",
        cls=ForFluencyCheck,
        trans_prompt: str = FORGAL_JSON_FLUENCYCHECK_PROMPT,
    ):
        obj = object.__new__(cls)
        obj._log_tag = "[通顺检测]"
        obj._disabled_reason = ""
        obj.pj_config = _FakeBatchConfig(values)
        obj.system_prompt = "system"
        obj.trans_prompt = trans_prompt
        obj.target_lang = "Simplified_Chinese"
        obj.eng_type = cls.__name__
        obj.batch_from_metadata = False  # 与真实 __init__ 口径一致
        obj._recorded_errors = []
        obj._llm_calls = []
        obj._resolve_file_metadata = Mock(
            return_value=object() if metadata_block else None
        )
        obj._format_file_metadata_block = Mock(return_value=metadata_block)
        obj._record_round_runtime_error = (
            lambda filename, idx_tip, message, model: obj._recorded_errors.append(
                (filename, idx_tip, message, model)
            )
        )

        async def fake_llm(messages, filename, idx_tip, cb):
            obj._llm_calls.append(messages)
            return llm_resp, None

        obj._call_llm = fake_llm
        return obj


class BatchTranslateTests(unittest.IsolatedAsyncioTestCase, _FluencyCheckTestBase):
    async def test_disabled_skips_and_keeps_old_mark(self) -> None:
        obj = object.__new__(ForFluencyCheck)
        obj._log_tag = "[通顺检测]"
        obj._disabled_reason = "主翻译令牌池无可用 token"
        trans = _trans(1)
        trans.fluency_issue = "旧标记"
        trans_list = [trans]
        with patch.object(ForFluencyCheck, "_call_llm") as mock_llm:
            result = await obj.batch_translate("f.json", "c.json", trans_list, 20)
        mock_llm.assert_not_awaited()
        self.assertIs(result, trans_list)
        self.assertEqual(trans.fluency_issue, "旧标记")

    async def test_only_rule_hits_are_sent_to_llm(self) -> None:
        # 两级流水线：干净句不产生任何 API 调用，只送初筛候选
        obj = self._make_obj(
            {}, llm_resp='ab2|{"id": 2, "reason": "A:「达到高潮」→「高潮」(依据:轻动词冗余)"}'
        )
        seeds = [
            _trans(1, pre_dst=SEED_40_DST, post_src="种子40原文"),
            _trans(2, pre_dst=SEED_217_DST, post_src="种子217原文"),
            _trans(3, pre_dst=SEED_283_DST, post_src="种子283原文"),
        ]
        clean = [
            _trans(4, pre_dst="她的手指微微颤抖着。", post_src="干净原文甲"),
            _trans(5, pre_dst="是昨天告诉我的。", post_src="干净原文乙"),
        ]
        trans_list = seeds + clean
        with patch.object(
            ForFluencyCheck, "_filter_target_translations", return_value=trans_list
        ):
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        self.assertEqual(len(obj._llm_calls), 1)  # 3 候选 → 1 批
        user = obj._llm_calls[0][-1]["content"]
        for seed in seeds:
            self.assertIn(seed.post_src, user)
        for t in clean:
            self.assertNotIn(t.post_src, user)

    async def test_no_rule_hit_skips_round_and_clears_old_marks(self) -> None:
        # 初筛无命中：整轮不发请求，旧标记仍按首轮口径清空（幂等）
        obj = self._make_obj({})
        trans = _trans(1, pre_dst="她的手指微微颤抖着。")
        trans.fluency_issue = "旧标记"
        with patch.object(
            ForFluencyCheck, "_filter_target_translations", return_value=[trans]
        ):
            await obj.batch_translate("f.json", "c.json", [trans], 20)
        self.assertEqual(obj._llm_calls, [])
        self.assertEqual(trans.fluency_issue, "")

    async def test_marks_written_and_cleared_idempotently(self) -> None:
        resp = "\n".join(
            [
                'ab1|{"id": 1, "reason": "A:「达到高潮」→「高潮」(依据:轻动词冗余)"}',
                'cd2|{"id": 2, "reason": "B:「是不是正确的」→「正不正确」(依据:判断句冗赘)"}',
                'ef3|{"id": 3, "reason": "C:「推向高潮」→「攀上高潮」(依据:人不能推向高潮)"}',
            ]
        )
        obj = self._make_obj({}, llm_resp=resp)
        trans_list = [
            _trans(1, pre_dst=SEED_217_DST),
            _trans(2, pre_dst=SEED_40_DST),
            _trans(3, pre_dst=SEED_283_DST),
        ]
        clean_with_old_mark = _trans(4, pre_dst="她的手指微微颤抖着。")
        clean_with_old_mark.fluency_issue = "旧标记"
        trans_list.append(clean_with_old_mark)
        with patch.object(
            ForFluencyCheck, "_filter_target_translations", return_value=trans_list
        ):
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        self.assertEqual(
            trans_list[0].fluency_issue, "A:「达到高潮」→「高潮」(依据:轻动词冗余)"
        )
        self.assertEqual(
            trans_list[1].fluency_issue,
            "B:「是不是正确的」→「正不正确」(依据:判断句冗赘)",
        )
        self.assertEqual(
            trans_list[2].fluency_issue,
            "C:「推向高潮」→「攀上高潮」(依据:人不能推向高潮)",
        )
        self.assertEqual(clean_with_old_mark.fluency_issue, "")  # 首轮清旧标记

    async def test_echo_response_retried_once_then_discarded(self) -> None:
        resp = "\n".join(
            f'ab{i}|{{"id": {i}, "reason": "A:「达到高潮」→「高潮」(依据:冗余)"}}'
            for i in range(1, 7)
        )
        obj = self._make_obj({}, llm_resp=resp)
        trans_list = [_trans(i, pre_dst=SEED_217_DST) for i in range(1, 7)]
        with patch.object(
            ForFluencyCheck, "_filter_target_translations", return_value=trans_list
        ):
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        self.assertEqual(len(obj._llm_calls), 2)
        self.assertIn("整批回显", obj._llm_calls[1][-1]["content"])
        self.assertTrue(all(t.fluency_issue == "" for t in trans_list))
        self.assertIn("重试后仍异常", obj._recorded_errors[0][2])

    async def test_no_reason_ratio_discards_batch(self) -> None:
        resp = "\n".join(f'ab{i}|{{"id": {i}}}' for i in range(1, 7))
        obj = self._make_obj({}, llm_resp=resp)
        trans_list = [_trans(i, pre_dst=SEED_217_DST) for i in range(1, 7)]
        with patch.object(
            ForFluencyCheck, "_filter_target_translations", return_value=trans_list
        ):
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        self.assertEqual(len(obj._llm_calls), 2)
        self.assertIn("片段", obj._llm_calls[1][-1]["content"])
        self.assertTrue(all(t.fluency_issue == "" for t in trans_list))
        self.assertEqual(len(obj._recorded_errors), 1)


class ReviewRoundTests(unittest.IsolatedAsyncioTestCase, _FluencyCheckTestBase):
    """复核轮不做规则初筛：已标记句即使 dst 不再命中规则也必须送复核。"""

    def _make_review_obj(self, llm_resp: str):
        return self._make_obj(
            {},
            llm_resp=llm_resp,
            cls=ForFluencyCheckAgain,
            trans_prompt=FORGAL_JSON_FLUENCYCHECK_AGAIN_PROMPT,
        )

    async def test_marked_sentence_without_rule_hit_still_reviewed(self) -> None:
        obj = self._make_review_obj('ab1|{"id": 1, "keep": false}')
        trans = _trans(1, pre_dst="她的手指微微颤抖着。")  # 不再命中规则
        trans.fluency_issue = "A:「达到高潮」→「高潮」(依据:轻动词冗余)"
        with patch.object(
            ForFluencyCheckAgain, "_filter_target_translations", return_value=[trans]
        ):
            await obj.batch_translate("f.json", "c.json", [trans], 20)
        self.assertEqual(len(obj._llm_calls), 1)
        user = obj._llm_calls[0][-1]["content"]
        # 复核提示词口径 + 第一轮原因注入 problem 键
        self.assertIn("否决复核", user)
        self.assertIn("轻动词冗余", user)
        self.assertEqual(trans.fluency_issue, "")  # keep:false → 撤销

    async def test_keep_true_keeps_marker(self) -> None:
        obj = self._make_review_obj(
            'ab1|{"id": 1, "keep": true, "reason": "A:「达到高潮」→「高潮」(依据:轻动词冗余)"}'
        )
        trans = _trans(1, pre_dst=SEED_217_DST)
        trans.fluency_issue = "A:「达到高潮」→「高潮」(依据:轻动词冗余)"
        with patch.object(
            ForFluencyCheckAgain, "_filter_target_translations", return_value=[trans]
        ):
            await obj.batch_translate("f.json", "c.json", [trans], 20)
        self.assertNotEqual(trans.fluency_issue, "")


class BatchSplitTests(unittest.IsolatedAsyncioTestCase, _FluencyCheckTestBase):
    """验证分批优先级：numPerRequestFluencyCheck > VerboseCheck > SemCheck > 实参。"""

    async def _run_split(self, values: dict, num_arg: int = 20, total: int = 5) -> list:
        obj = self._make_obj(values, llm_resp="")
        targets = [_trans(i, pre_dst=SEED_217_DST) for i in range(1, total + 1)]
        with patch.object(
            ForFluencyCheck, "_filter_target_translations", return_value=targets
        ):
            await obj.batch_translate("f.json", "c.json", targets, num_arg)
        return [len(call[-1]["content"]) for call in obj._llm_calls]

    async def test_uses_fluency_check_key_first(self) -> None:
        sizes = await self._run_split(
            {
                "gpt.numPerRequestFluencyCheck": 2,
                "gpt.numPerRequestVerboseCheck": 3,
                "gpt.numPerRequestSemCheck": 4,
            }
        )
        self.assertEqual(len(sizes), 3)  # 5 句 / 2 = 3 批

    async def test_falls_back_to_verbose_check_key(self) -> None:
        sizes = await self._run_split(
            {"gpt.numPerRequestVerboseCheck": 2, "gpt.numPerRequestSemCheck": 3}
        )
        self.assertEqual(len(sizes), 3)

    async def test_falls_back_to_argument(self) -> None:
        sizes = await self._run_split({}, num_arg=5)
        self.assertEqual(len(sizes), 1)


class RegistrationTests(unittest.TestCase):
    def test_engine_module_and_registry(self) -> None:
        from GalTransl.Backend.BaseEngine import ENGINE_MODULE_PATHS, ENGINE_REGISTRY

        self.assertEqual(
            ENGINE_MODULE_PATHS["ForFluencyCheck"], "GalTransl.Backend.ForFluencyCheck"
        )
        self.assertEqual(
            ENGINE_MODULE_PATHS["ForFluencyCheckAgain"],
            "GalTransl.Backend.ForFluencyCheckAgain",
        )
        for name in ("ForFluencyCheck", "ForFluencyCheckAgain"):
            self.assertIn(name, ENGINE_REGISTRY)
            self.assertTrue(callable(ENGINE_REGISTRY[name]))

    def test_token_pool_and_standalone_registration(self) -> None:
        from GalTransl import NEED_OpenAITokenPool, TRANSLATOR_SUPPORTED
        from GalTransl.Frontend.llm_standalone import STANDALONE_BACKENDS

        self.assertIn("ForFluencyCheck", NEED_OpenAITokenPool)
        self.assertIn("ForFluencyCheckAgain", NEED_OpenAITokenPool)
        self.assertIn("ForFluencyCheck", TRANSLATOR_SUPPORTED)
        self.assertIn("ForFluencyCheckAgain", TRANSLATOR_SUPPORTED)
        self.assertEqual(STANDALONE_BACKENDS["ForFluencyCheck"].log_tag, "[通顺检测]")
        self.assertEqual(
            STANDALONE_BACKENDS["ForFluencyCheckAgain"].log_tag, "[通顺复核]"
        )

    def test_problem_catalog_stays_in_sync(self) -> None:
        from GalTransl.server_meta import _PROBLEM_TYPE_CATALOG

        # 目录只允许尾部追加（fix 序号写法按目录顺序解析，插入中部会移位旧配置）
        self.assertEqual(PROBLEM_TYPE_CATALOG_ORDER[-1], "语域异常")
        self.assertEqual(
            [entry["name"] for entry in _PROBLEM_TYPE_CATALOG],
            list(PROBLEM_TYPE_CATALOG_ORDER),
        )

    def test_stage_and_chain_registration(self) -> None:
        """译后链白名单与自定义流水线步骤键（Phase 2 接线）。"""
        from GalTransl.Frontend.llm_postprocess import _resolve_after_translation_order
        from GalTransl.Frontend.pipeline_stages import (
            AFTER_TRANS_STEP_KEYS,
            resolve_pipeline_steps,
        )

        class _Cfg:
            def __init__(self, values: dict) -> None:
                self._values = values

            def getKey(self, key: str, default=None):
                return self._values.get(key, default)

        chain = ["fluencycheck", "fluencycheckagain"]
        self.assertEqual(
            _resolve_after_translation_order(_Cfg({"gpt.afterTranslation": chain})),
            chain,
        )
        for key in chain:
            self.assertIn(key, AFTER_TRANS_STEP_KEYS)

        steps, warnings, custom = resolve_pipeline_steps(
            _Cfg({"internals.pipeline.steps": ["translate", *chain]})
        )
        self.assertTrue(custom)
        self.assertEqual(warnings, [])
        self.assertEqual(steps, ["translate", *chain])

        # 译后链白名单覆盖全部流水线译后键（fix 须为对象条目，不参与字符串比对）
        all_keys = [k for k in AFTER_TRANS_STEP_KEYS if k != "fix"] + ["improve"]
        self.assertEqual(
            _resolve_after_translation_order(
                _Cfg({"gpt.afterTranslation": all_keys})
            ),
            all_keys,
        )

    def test_after_translation_dispatch_instantiates_fluency_backends(self) -> None:
        """mode → 后端类分支写错时本用例失败（不只锁常量）。"""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch

        from GalTransl.Frontend.llm_postprocess import _run_after_trans_single_file

        class _Cfg:
            proxyPool = None
            gpt_dic = {}
            stage_token_pools = {}
            tokenPool = None

            def getKey(self, key: str, default=None):
                return default

        for mode, module, cls_name in (
            ("fluencycheck", "ForFluencyCheck", "ForFluencyCheck"),
            ("fluencycheckagain", "ForFluencyCheckAgain", "ForFluencyCheckAgain"),
        ):
            inst = MagicMock()
            inst.batch_translate = AsyncMock()
            inst.shutdown = AsyncMock()
            with patch(
                f"GalTransl.Backend.{module}.{cls_name}", return_value=inst
            ) as p_cls:
                asyncio.run(
                    _run_after_trans_single_file(
                        mode,
                        "f.json",
                        "f.json",
                        [SimpleNamespace()],
                        _Cfg(),
                        20,
                    )
                )
            p_cls.assert_called_once()
            self.assertEqual(p_cls.call_args[0][1], cls_name)
            inst.batch_translate.assert_awaited_once()
            inst.shutdown.assert_awaited_once()

    def test_review_sentinel_treated_as_no_reason(self) -> None:
        # 复核轮注入第一轮原因：占位文案「中文不顺」不注入（与疑似错误/节奏冗长同口径）
        from GalTransl.Backend.ForGalJsonTranslate import ForGalJsonTranslate

        trans = _trans(1)
        trans.fluency_issue = "中文不顺"
        self.assertEqual(
            ForGalJsonTranslate._issue_reason_of(trans, "fluency_issue"), ""
        )
        trans.fluency_issue = "A:「达到高潮」→「高潮」"
        self.assertEqual(
            ForGalJsonTranslate._issue_reason_of(trans, "fluency_issue"),
            "A:「达到高潮」→「高潮」",
        )


if __name__ == "__main__":
    unittest.main()
