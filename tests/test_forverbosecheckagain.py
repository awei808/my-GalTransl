# -*- coding: utf-8 -*-
"""ForVerboseCheckAgain 节奏冗长复核的单元测试。

覆盖：
  - _parse_confirm_response：keep true 确认（新 reason 覆盖，半角逗号归一）、
    keep false 撤销、缺判定行/keep 值异常/乱码 reason 的 fail-safe 保留
  - batch_translate：禁用降级跳过（保留标记）；只复核已标记且译文有效的句子；
    无标记/标记译文失效两种引导日志；LLM 失败保留标记
  - 输入行 problem 键：注入第一轮具体原因，哨兵 "1" 不注入
  - 复核轮 user 提示词：注入文件级元数据与任务说明，不注入术语表/批次元数据/历史
  - 汇总日志：确认/撤销句数
"""
import unittest
from unittest.mock import Mock, patch

from GalTransl.CSentense import CSentense
from GalTransl.Backend.Prompts import FORGAL_JSON_VERBOSECHECK_AGAIN_PROMPT
from GalTransl.Backend.ForVerboseCheckAgain import ForVerboseCheckAgain


class _FakePjConfig:
    def getKey(self, key: str):
        return None


class _FakeBatchConfig:
    """分批配置替身：可控 getKey 返回值，active_workers=1 触发输入日志。"""

    def __init__(self, values: dict) -> None:
        self._values = values

    active_workers = 1

    def getKey(self, key: str):
        return self._values.get(key)


def _make_parser() -> ForVerboseCheckAgain:
    """绕过重型 __init__，仅装配 _parse_confirm_response 所需属性。"""
    obj = object.__new__(ForVerboseCheckAgain)
    obj._log_tag = "[冗长复核]"
    obj._disabled_reason = ""
    obj.pj_config = _FakePjConfig()
    return obj


def _trans(
    index: int, pre_dst: str = "译文", verbose: str = "", post_src: str = "原文"
) -> CSentense:
    t = CSentense(f"src{index}", index=index)
    t.post_src = post_src
    t.pre_dst = pre_dst
    t.proofread_zh = ""
    t.verbose_issue = verbose
    return t


class ParseConfirmResponseTests(unittest.TestCase):
    def test_confirm_keeps_mark_and_updates_reason(self) -> None:
        # 复核轮同样要求片段证据（继承自 ForVerboseCheck 的 _reason_evidence_required）
        parser = _make_parser()
        trans = _trans(3, pre_dst="非常非常感谢你", verbose="旧原因")
        confirm, dismiss = parser._parse_confirm_response(
            'tma|{"id": 3, "keep": true, "reason": "同义堆叠：「非常」/「非常」"}',
            [trans],
        )
        self.assertEqual((confirm, dismiss), (1, 0))
        self.assertEqual(trans.verbose_issue, "同义堆叠：「非常」/「非常」")

    def test_confirm_reason_halfwidth_commas_normalized(self) -> None:
        parser = _make_parser()
        trans = _trans(4, pre_dst="其实说实话的话呢也就是说算了", verbose="旧原因")
        parser._parse_confirm_response(
            'ab1|{"id": 4, "keep": true, "reason": "垫词多：「的话呢,也就是说」"}',
            [trans],
        )
        self.assertEqual(trans.verbose_issue, "垫词多：「的话呢，也就是说」")

    def test_confirm_reason_without_evidence_keeps_old_mark(self) -> None:
        # 新 reason 的片段在译文中找不到：不覆盖第一轮原因，保留既有标记
        parser = _make_parser()
        trans = _trans(12, pre_dst="一句很短的译文", verbose="旧原因")
        confirm, dismiss = parser._parse_confirm_response(
            'ab1|{"id": 12, "keep": true, "reason": "同义堆叠：「那些」/「那些」"}',
            [trans],
        )
        self.assertEqual((confirm, dismiss), (1, 0))
        self.assertEqual(trans.verbose_issue, "旧原因")

    def test_confirm_reason_with_src_ref_updates_mark(self) -> None:
        # (src:…) 源成分标注在「」外：不参与片段校验，确认后整体覆盖第一轮原因
        parser = _make_parser()
        trans = _trans(15, pre_dst="床单的被单瞬间湿透", verbose="旧原因")
        confirm, dismiss = parser._parse_confirm_response(
            'ab1|{"id": 15, "keep": true, "reason": "同源二译：「床单」/「被单」(src:シーツ)"}',
            [trans],
        )
        self.assertEqual((confirm, dismiss), (1, 0))
        self.assertEqual(
            trans.verbose_issue, "同源二译：「床单」/「被单」(src:シーツ)"
        )

    def test_no_src_short_fragment_keeps_first_round_reason(self) -> None:
        # 无源增补片段不足 4 字（单个副词）：证据校验不过 → 不覆盖第一轮原因，
        # 但复核结论仍按确认计数（fail-safe 保留标记）
        parser = _make_parser()
        trans = _trans(16, pre_dst="华恋那边也早已湿透了", verbose="旧原因")
        confirm, dismiss = parser._parse_confirm_response(
            'ab1|{"id": 16, "keep": true, "reason": "无源增补：「早已」(类别：时机)"}',
            [trans],
        )
        self.assertEqual((confirm, dismiss), (1, 0))
        self.assertEqual(trans.verbose_issue, "旧原因")

    def test_echo_line_discarded_instead_of_missing_keep(self) -> None:
        # 复核轮回显输入行：整行丢弃（否则会落到 keep 缺失分支，被当成「未获得判定」误报）
        parser = _make_parser()
        trans_list = [_trans(13, verbose="原因A"), _trans(14, verbose="原因B")]
        confirm, dismiss = parser._parse_confirm_response(
            'a1b|{"id": 13, "src": "原文", "dst": "译文"}\n'
            'c2d|{"id": 14, "keep": false}',
            trans_list,
        )
        self.assertEqual((confirm, dismiss), (0, 1))
        self.assertEqual(trans_list[0].verbose_issue, "原因A")
        self.assertEqual(trans_list[1].verbose_issue, "")

    def test_confirm_without_reason_keeps_old_mark(self) -> None:
        parser = _make_parser()
        trans = _trans(5, verbose="旧原因")
        parser._parse_confirm_response('ab1|{"id": 5, "keep": true}', [trans])
        self.assertEqual(trans.verbose_issue, "旧原因")

    def test_confirm_placeholder_reason_keeps_old_mark(self) -> None:
        # 模型照抄类型名当 reason → 视为无新原因，不覆盖成「节奏冗长：节奏冗长」
        parser = _make_parser()
        trans = _trans(11, verbose="旧原因")
        parser._parse_confirm_response(
            'ab1|{"id": 11, "keep": true, "reason": "节奏冗长"}', [trans]
        )
        self.assertEqual(trans.verbose_issue, "旧原因")

    def test_dismiss_clears_mark(self) -> None:
        parser = _make_parser()
        trans = _trans(6, verbose="旧原因")
        confirm, dismiss = parser._parse_confirm_response(
            'ab1|{"id": 6, "keep": false}', [trans]
        )
        self.assertEqual((confirm, dismiss), (0, 1))
        self.assertEqual(trans.verbose_issue, "")

    def test_invalid_keep_value_keeps_mark(self) -> None:
        parser = _make_parser()
        trans = _trans(7, verbose="旧原因")
        with self.assertLogs("GalTransl", level="WARNING"):
            confirm, dismiss = parser._parse_confirm_response(
                'ab1|{"id": 7, "keep": "yes"}', [trans]
            )
        self.assertEqual((confirm, dismiss), (0, 0))
        self.assertEqual(trans.verbose_issue, "旧原因")

    def test_missing_lines_keep_marks(self) -> None:
        parser = _make_parser()
        trans_list = [_trans(8, verbose="原因A"), _trans(9, verbose="原因B")]
        with self.assertLogs("GalTransl", level="WARNING") as cm:
            parser._parse_confirm_response('ab1|{"id": 8, "keep": true}', trans_list)
        self.assertEqual(trans_list[0].verbose_issue, "原因A")
        self.assertEqual(trans_list[1].verbose_issue, "原因B")
        self.assertIn("未获得复核判定", "\n".join(cm.output))

    def test_garbled_reason_keeps_old_mark(self) -> None:
        parser = _make_parser()
        trans = _trans(10, verbose="旧原因")
        parser._parse_confirm_response(
            'ab1|{"id": 10, "keep": true, "reason": "\ufffd\ufffd"}', [trans]
        )
        self.assertEqual(trans.verbose_issue, "旧原因")


class _ReviewTestBase:
    def _make_obj(self, values: dict, llm_resp: str = "", metadata_block: str = ""):
        obj = object.__new__(ForVerboseCheckAgain)
        obj._log_tag = "[冗长复核]"
        obj._disabled_reason = ""
        obj.pj_config = _FakeBatchConfig(values)
        obj.system_prompt = "system"
        obj.trans_prompt = FORGAL_JSON_VERBOSECHECK_AGAIN_PROMPT
        obj.target_lang = "Simplified_Chinese"
        obj.eng_type = "ForVerboseCheckAgain"
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


class BatchTranslateTests(unittest.IsolatedAsyncioTestCase, _ReviewTestBase):
    async def test_disabled_skips_and_keeps_marks(self) -> None:
        obj = object.__new__(ForVerboseCheckAgain)
        obj._log_tag = "[冗长复核]"
        obj._disabled_reason = "主翻译令牌池无可用 token"
        trans = _trans(1, verbose="旧标记")
        trans_list = [trans]
        with patch.object(ForVerboseCheckAgain, "_call_llm") as mock_llm:
            result = await obj.batch_translate("f.json", "c.json", trans_list, 20)
        mock_llm.assert_not_awaited()
        self.assertIs(result, trans_list)
        self.assertEqual(trans.verbose_issue, "旧标记")

    async def test_only_marked_sentences_are_reviewed(self) -> None:
        obj = self._make_obj(
            {}, llm_resp='ab1|{"id": 2, "keep": false}\ncd2|{"id": 3, "keep": true}'
        )
        trans_list = [
            _trans(1, verbose=""),  # 未标记 → 不送复核
            _trans(2, verbose="重复陈述"),  # 标记 → 撤销
            _trans(3, verbose="垫词过多"),  # 标记 → 确认
            _trans(4, verbose="标记但无译文", pre_dst=""),  # 译文失效 → 不送
        ]
        await obj.batch_translate("f.json", "c.json", trans_list, 20)
        user = obj._llm_calls[0][-1]["content"]
        self.assertIn('"id": 2', user)
        self.assertIn('"id": 3', user)
        self.assertNotIn('"id": 1', user)
        self.assertNotIn('"id": 4', user)
        self.assertEqual(trans_list[1].verbose_issue, "")
        self.assertEqual(trans_list[2].verbose_issue, "垫词过多")

    async def test_no_marks_logs_first_round_hint(self) -> None:
        obj = self._make_obj({}, llm_resp="")
        trans_list = [_trans(1, verbose="")]
        with self.assertLogs("GalTransl", level="INFO") as cm:
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        joined = "\n".join(cm.output)
        self.assertIn("无待复核的命中句", joined)
        self.assertIn("节奏冗长检测（ForVerboseCheck）", joined)
        self.assertEqual(obj._llm_calls, [])

    async def test_marks_without_valid_translation_logs_hint(self) -> None:
        obj = self._make_obj({}, llm_resp="")
        trans_list = [_trans(1, verbose="旧标记", pre_dst="")]
        with self.assertLogs("GalTransl", level="INFO") as cm:
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        self.assertIn("存在节奏冗长标记但均无有效译文", "\n".join(cm.output))
        self.assertEqual(obj._llm_calls, [])

    async def test_problem_key_injects_first_round_reason(self) -> None:
        obj = self._make_obj({}, llm_resp='ab1|{"id": 2, "keep": true}')
        trans_list = [_trans(1, verbose="同义堆叠：非常非常"), _trans(2, verbose="1")]
        await obj.batch_translate("f.json", "c.json", trans_list, 20)
        user = obj._llm_calls[0][-1]["content"]
        self.assertIn('"problem": "同义堆叠：非常非常"', user)
        self.assertNotIn('"problem": "1"', user)

    async def test_prompt_injects_metadata_only(self) -> None:
        obj = self._make_obj(
            {},
            llm_resp='ab1|{"id": 1, "keep": true}',
            metadata_block="\n<plot_metadata>\n角色: 創\n</plot_metadata>\n",
        )
        trans_list = [_trans(1, verbose="垫词过多")]
        await obj.batch_translate("f.json", "c.json", trans_list, 20)
        user = obj._llm_calls[0][-1]["content"]
        self.assertIn("### 任务", user)
        self.assertIn("角色: 創", user)
        self.assertNotIn("[plot_metadata]", user)
        self.assertNotIn("[Glossary]", user)
        self.assertNotIn("<batch_metadata>", user)
        self.assertNotIn("[history_result]", user)

    async def test_llm_failure_keeps_marks_fail_safe(self) -> None:
        obj = self._make_obj({})
        trans_list = [_trans(1, verbose="旧标记")]

        async def boom(messages, filename, idx_tip, cb):
            obj._llm_calls.append(messages)
            raise RuntimeError("boom")

        obj._call_llm = boom
        await obj.batch_translate("f.json", "c.json", trans_list, 20)
        self.assertIn("boom", obj._recorded_errors[0][2])
        self.assertEqual(trans_list[0].verbose_issue, "旧标记")

    async def test_summary_logs_confirm_and_dismiss_counts(self) -> None:
        obj = self._make_obj(
            {}, llm_resp='ab1|{"id": 1, "keep": true}\ncd2|{"id": 2, "keep": false}'
        )
        trans_list = [_trans(1, verbose="原因A"), _trans(2, verbose="原因B")]
        with self.assertLogs("GalTransl", level="INFO") as cm:
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        self.assertIn("二次复核完成：确认 1 句，撤销 1 句", "\n".join(cm.output))


if __name__ == "__main__":
    unittest.main()
