# -*- coding: utf-8 -*-
"""ForSemCheckAgain 语义复核（命中句二次复核）的单元测试。

覆盖：
  - _parse_confirm_response：keep:true 确认（干净新 reason 覆盖旧标记）/
    keep:false 撤销 / 乱码 reason 不覆盖 / keep 值异常与判定缺失 fail-safe
    保留 / 未知 id 与非 JSON 行跳过 / 重复 id 后行覆盖前行 / 空响应全缺失
  - batch_translate：无命中句跳过不发请求 / 禁用降级保留旧标记 /
    LLM 调用失败 fail-safe 保留并上报 / 批次按 numPerRequestSemCheck 拆分
    （优先于改进轮批次）/ 单轮 user 提示词仅注入任务说明与批次 input
"""
import asyncio
import re
import unittest
from unittest.mock import AsyncMock, patch

from GalTransl.CSentense import CSentense
from GalTransl.Backend.Prompts import FORGAL_JSON_SEMCHECK_AGAIN_PROMPT
from GalTransl.Backend.ForSemCheckAgain import ForSemCheckAgain


class _FakePjConfig:
    """_parse_confirm_response 测试替身：仅需 _log_tag 所在对象可用。"""

    def getKey(self, key: str):
        return None


class _FakeBatchConfig:
    """复核分批配置替身：可控 getKey 返回值，active_workers=1 触发输入日志。"""

    def __init__(self, values: dict) -> None:
        self._values = values

    active_workers = 1

    def getKey(self, key: str):
        return self._values.get(key)


def _make_parser() -> ForSemCheckAgain:
    """绕过重型 __init__，仅装配 _parse_confirm_response 所需属性。"""
    obj = object.__new__(ForSemCheckAgain)
    obj._log_tag = "[语义复核]"
    obj._disabled_reason = ""
    obj.pj_config = _FakePjConfig()
    return obj


def _trans(index: int, pre_dst: str = "译文", suspected: str = "") -> CSentense:
    t = CSentense(f"src{index}", index=index)
    t.post_src = f"src{index}"
    t.pre_dst = pre_dst
    t.proofread_zh = ""
    t.suspected_error = suspected
    return t


class ParseConfirmResponseTests(unittest.TestCase):
    def test_confirm_keeps_mark_and_updates_reason(self) -> None:
        parser = _make_parser()
        trans = _trans(3, suspected="旧原因")
        line = 'tma|{"id": 3, "keep": true, "reason": "人名错译：華恋→华良"}'
        confirm, dismiss = parser._parse_confirm_response(line, [trans])
        self.assertEqual((confirm, dismiss), (1, 0))
        self.assertEqual(trans.suspected_error, "人名错译：華恋→华良")

    def test_confirm_without_reason_keeps_old_mark(self) -> None:
        parser = _make_parser()
        trans = _trans(3, suspected="旧原因")
        line = 'tma|{"id": 3, "keep": true}'
        confirm, dismiss = parser._parse_confirm_response(line, [trans])
        self.assertEqual((confirm, dismiss), (1, 0))
        self.assertEqual(trans.suspected_error, "旧原因")

    def test_dismiss_clears_mark(self) -> None:
        parser = _make_parser()
        trans = _trans(5, suspected="疑似错误")
        line = '1mj|{"id": 5, "keep": false}'
        confirm, dismiss = parser._parse_confirm_response(line, [trans])
        self.assertEqual((confirm, dismiss), (0, 1))
        self.assertEqual(trans.suspected_error, "")

    def test_garbled_reason_not_overwrite_old_mark(self) -> None:
        parser = _make_parser()
        trans = _trans(3, suspected="旧原因")
        line = 'tma|{"id": 3, "keep": true, "reason": "人名��乱码"}'
        confirm, dismiss = parser._parse_confirm_response(line, [trans])
        self.assertEqual((confirm, dismiss), (1, 0))
        # 乱码 reason 不污染标记，保留第一轮原因
        self.assertEqual(trans.suspected_error, "旧原因")

    def test_keep_anomaly_keeps_mark_fail_safe(self) -> None:
        parser = _make_parser()
        trans = _trans(3, suspected="疑似错误")
        # 模型输出字符串 "true" 而非布尔：keep 值异常 → 保留标记
        line = 'tma|{"id": 3, "keep": "true"}'
        confirm, dismiss = parser._parse_confirm_response(line, [trans])
        self.assertEqual((confirm, dismiss), (0, 0))
        self.assertEqual(trans.suspected_error, "疑似错误")

    def test_missing_verdict_keeps_mark_fail_safe(self) -> None:
        parser = _make_parser()
        trans_list = [_trans(2, suspected="疑似错误"), _trans(3, suspected="疑似错误")]
        # 仅 id=2 获判定，id=3 缺失 → 保留
        line = 'abc|{"id": 2, "keep": false}'
        confirm, dismiss = parser._parse_confirm_response(line, trans_list)
        self.assertEqual((confirm, dismiss), (0, 1))
        self.assertEqual(trans_list[0].suspected_error, "")
        self.assertEqual(trans_list[1].suspected_error, "疑似错误")

    def test_empty_response_all_missing_kept(self) -> None:
        parser = _make_parser()
        trans_list = [_trans(1, suspected="疑似错误"), _trans(2, suspected="疑似错误")]
        confirm, dismiss = parser._parse_confirm_response("", trans_list)
        self.assertEqual((confirm, dismiss), (0, 0))
        self.assertTrue(all(t.suspected_error == "疑似错误" for t in trans_list))

    def test_unknown_id_skipped(self) -> None:
        parser = _make_parser()
        trans = _trans(1, suspected="疑似错误")
        line = 'nnk|{"id": 999, "keep": false}'
        confirm, dismiss = parser._parse_confirm_response(line, [trans])
        self.assertEqual((confirm, dismiss), (0, 0))
        self.assertEqual(trans.suspected_error, "疑似错误")

    def test_non_json_line_skipped(self) -> None:
        parser = _make_parser()
        trans = _trans(1, suspected="疑似错误")
        line = "纯文本没有 JSON"
        confirm, dismiss = parser._parse_confirm_response(line, [trans])
        self.assertEqual((confirm, dismiss), (0, 0))
        self.assertEqual(trans.suspected_error, "疑似错误")

    def test_duplicate_id_last_line_wins(self) -> None:
        parser = _make_parser()
        trans = _trans(3, suspected="旧原因")
        text = "\n".join(
            [
                'a1b|{"id": 3, "keep": false}',
                'c2d|{"id": 3, "keep": true, "reason": "后行覆盖"}',
            ]
        )
        # 逐行应用：先撤销后确认，两行均计数；最终标记以后行为准
        confirm, dismiss = parser._parse_confirm_response(text, [trans])
        self.assertEqual((confirm, dismiss), (1, 1))
        self.assertEqual(trans.suspected_error, "后行覆盖")

    def test_sparse_by_id_order_irrelevant(self) -> None:
        parser = _make_parser()
        trans_list = [
            _trans(3, suspected="疑似错误"),
            _trans(12, suspected="疑似错误"),
            _trans(14, suspected="疑似错误"),
        ]
        text = "\n".join(
            [
                'tc7|{"id": 14, "keep": false}',
                'tma|{"id": 3, "keep": true}',
                '1mj|{"id": 12, "keep": false}',
            ]
        )
        confirm, dismiss = parser._parse_confirm_response(text, trans_list)
        self.assertEqual((confirm, dismiss), (1, 2))
        self.assertEqual([t.suspected_error for t in trans_list], ["疑似错误", "", ""])


class BatchTranslateGuardTests(unittest.IsolatedAsyncioTestCase):
    def _make_obj(self, values: dict | None = None) -> ForSemCheckAgain:
        obj = object.__new__(ForSemCheckAgain)
        obj._log_tag = "[语义复核]"
        obj._disabled_reason = ""
        obj.pj_config = _FakeBatchConfig(values or {})
        obj.system_prompt = "system"
        obj.trans_prompt = FORGAL_JSON_SEMCHECK_AGAIN_PROMPT
        obj.target_lang = "Simplified_Chinese"
        obj.eng_type = "ForSemCheckAgain"
        obj._recorded_errors = []
        # 基类属性：_resolve_file_metadata 会访问 file_metadata_map / project_config
        # 与惰性载入标志 _file_metadata_loaded / _file_metadata_by_file
        obj.file_metadata_map = {}
        obj.project_config = None
        obj._file_metadata_loaded = True
        obj._file_metadata_by_file = {}

        def fake_record(filename, idx_tip, message, model):
            obj._recorded_errors.append((filename, idx_tip, message, model))

        obj._record_round_runtime_error = fake_record
        return obj

    async def test_no_flagged_sentences_skips_without_llm_call(self) -> None:
        obj = self._make_obj()
        with patch.object(
            ForSemCheckAgain, "_call_llm", new=AsyncMock()
        ) as mock_llm:
            trans_list = [_trans(1, "正常译文", ""), _trans(2, "正常译文二", "")]
            result = await obj.batch_translate("f.json", "c.json", trans_list, 20)
        mock_llm.assert_not_awaited()
        self.assertIs(result, trans_list)

    async def test_no_mark_skips_with_guidance_log(self) -> None:
        # 全文件无 suspected_error：跳过并提示先执行语义检测
        obj = self._make_obj()
        trans_list = [_trans(1, "正常译文", ""), _trans(2, "正常译文二", "")]
        with patch.object(
            ForSemCheckAgain, "_call_llm", new=AsyncMock()
        ) as mock_llm, self.assertLogs("GalTransl", level="INFO") as cm:
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        mock_llm.assert_not_awaited()
        joined = "\n".join(cm.output)
        self.assertIn("无待复核的命中句", joined)
        self.assertIn("先执行语义差异检测", joined)

    async def test_mark_without_dst_skips_with_guidance_log(self) -> None:
        # 有标记但译文失效（pre_dst 为空）：提示译文未生成
        obj = self._make_obj()
        trans_list = [_trans(1, "正常译文", ""), _trans(2, "", "疑似错误")]
        with patch.object(
            ForSemCheckAgain, "_call_llm", new=AsyncMock()
        ) as mock_llm, self.assertLogs("GalTransl", level="INFO") as cm:
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        mock_llm.assert_not_awaited()
        joined = "\n".join(cm.output)
        self.assertIn("存在疑似错误标记但均无有效译文", joined)

    async def test_disabled_skips_and_keeps_old_marks(self) -> None:
        obj = self._make_obj()
        obj._disabled_reason = "主翻译令牌池无可用 token"
        trans_list = [_trans(1, "译文", "疑似错误")]
        with patch.object(
            ForSemCheckAgain, "_call_llm", new=AsyncMock()
        ) as mock_llm:
            result = await obj.batch_translate("f.json", "c.json", trans_list, 20)
        mock_llm.assert_not_awaited()
        self.assertIs(result, trans_list)
        self.assertEqual(trans_list[0].suspected_error, "疑似错误")

    async def test_llm_failure_keeps_marks_and_records_error(self) -> None:
        obj = self._make_obj()

        async def failing_llm(messages, filename, idx_tip, cb):
            raise RuntimeError("boom")

        obj._call_llm = failing_llm
        trans_list = [
            _trans(1, "正常译文", ""),
            _trans(2, "第一轮标记的句子", "疑似错误"),
        ]
        await obj.batch_translate("f.json", "c.json", trans_list, 20)
        # fail-safe：调用失败保留第一轮信号，并上报 1 条运行时错误
        self.assertEqual(trans_list[0].suspected_error, "")
        self.assertEqual(trans_list[1].suspected_error, "疑似错误")
        self.assertEqual(len(obj._recorded_errors), 1)
        self.assertIn("boom", obj._recorded_errors[0][2])

    async def test_mixed_verdict_applied(self) -> None:
        obj = self._make_obj()

        async def fake_llm(messages, filename, idx_tip, cb):
            resp = (
                'abc|{"id": 2, "keep": true, "reason": "人名错译"}\n'
                'def|{"id": 3, "keep": false}'
            )
            return resp, None

        obj._call_llm = fake_llm
        trans_list = [
            _trans(1, "正常译文", ""),
            _trans(2, "确认句", "疑似错误"),
            _trans(3, "撤销句", "疑似错误"),
        ]
        await obj.batch_translate("f.json", "c.json", trans_list, 20)
        self.assertEqual(trans_list[0].suspected_error, "")
        self.assertEqual(trans_list[1].suspected_error, "人名错译")
        self.assertEqual(trans_list[2].suspected_error, "")

    async def test_file_metadata_injected_into_user_content(self) -> None:
        obj = self._make_obj()
        captured = {}

        async def fake_llm(messages, filename, idx_tip, cb):
            captured["user"] = messages[1]["content"]
            return "", None

        obj._call_llm = fake_llm
        fake_meta = type(
            "FakeFileMetaData",
            (),
            {
                "id": "demo.json",
                "character": ["創", "華恋"],
                "costume": ["女仆装"],
                "plot": "众人入住 cosplay 度假岛，华恋是女仆",
                "tags": ["日常", "H"],
            },
        )()
        trans_list = [_trans(2, "确认句", "疑似错误")]
        with patch.object(
            ForSemCheckAgain, "_resolve_file_metadata", return_value=fake_meta
        ) as mock_resolve:
            await obj.batch_translate("demo.json", "c.json", trans_list, 20)
        mock_resolve.assert_called_once_with("demo.json")
        self.assertIn("<plot_metadata>", captured["user"])
        self.assertIn("角色: 創、華恋", captured["user"])
        self.assertIn("剧情: 众人入住 cosplay 度假岛，华恋是女仆", captured["user"])
        # 元数据块位于任务说明之前
        self.assertLess(
            captured["user"].index("<plot_metadata>"),
            captured["user"].index("### 任务"),
        )

    async def test_no_metadata_keeps_prompt_clean(self) -> None:
        obj = self._make_obj()
        captured = {}

        async def fake_llm(messages, filename, idx_tip, cb):
            captured["user"] = messages[1]["content"]
            return "", None

        obj._call_llm = fake_llm
        trans_list = [_trans(2, "确认句", "疑似错误")]
        with patch.object(
            ForSemCheckAgain, "_resolve_file_metadata", return_value=None
        ):
            await obj.batch_translate("demo.json", "c.json", trans_list, 20)
        # 未注入元数据时不应出现实际元数据内容（模板文字含 <plot_metadata> 字样，故断言内容）
        self.assertNotIn("角色: ", captured["user"])
        self.assertNotIn("剧情: ", captured["user"])
        self.assertIn("### 任务", captured["user"])


class SemcheckAgainBatchSplitTests(unittest.TestCase):
    """验证 batch_translate 按 numPerRequestSemCheck 分批发送，优先于改进轮批次。"""

    def _run_split(self, values: dict, num_arg: int = 20, total: int = 5) -> list:
        obj = object.__new__(ForSemCheckAgain)
        obj._log_tag = "[语义复核]"
        obj._disabled_reason = ""
        obj.pj_config = _FakeBatchConfig(values)
        obj.system_prompt = "system"
        obj.trans_prompt = FORGAL_JSON_SEMCHECK_AGAIN_PROMPT
        obj.target_lang = "Simplified_Chinese"
        obj.eng_type = "ForSemCheckAgain"
        # 基类属性：_resolve_file_metadata 会访问 file_metadata_map / project_config
        # 与惰性载入标志 _file_metadata_loaded / _file_metadata_by_file
        obj.file_metadata_map = {}
        obj.project_config = None
        obj._file_metadata_loaded = True
        obj._file_metadata_by_file = {}
        calls = []

        async def fake_llm(messages, filename, idx_tip, cb):
            calls.append(messages)
            return "", None

        obj._call_llm = fake_llm
        targets = [_trans(i, suspected="疑似错误") for i in range(1, total + 1)]
        asyncio.run(obj.batch_translate("f.json", "c.json", targets, num_arg))
        sizes = []
        for m in calls:
            user = m[1]["content"]
            in_input = False
            n = 0
            for line in user.splitlines():
                if line.strip().startswith("<input>"):
                    in_input = True
                    continue
                if line.strip().startswith("</input>"):
                    in_input = False
                    continue
                if in_input and re.match(r"^[A-Za-z0-9]{3}\|", line.strip()):
                    n += 1
            sizes.append(n)
        return sizes

    def test_semcheck_batch_preferred_over_better(self) -> None:
        # numPerRequestSemCheck=2 优先，5 句分成 2,2,1
        sizes = self._run_split(
            {"gpt.numPerRequestSemCheck": 2, "gpt.numPerRequestBetter": 100}
        )
        self.assertEqual(sizes, [2, 2, 1])

    def test_falls_back_to_numPerRequestBetter(self) -> None:
        sizes = self._run_split({"gpt.numPerRequestBetter": 2})
        self.assertEqual(sizes, [2, 2, 1])

    def test_falls_back_to_argument_when_any_unset(self) -> None:
        sizes = self._run_split({}, num_arg=2)
        self.assertEqual(sizes, [2, 2, 1])


class SemcheckAgainPromptInjectionTests(unittest.TestCase):
    """验证单轮 user 提示词：仅注入任务说明与批次 input，不注入其它内容。"""

    def _make_obj(self) -> ForSemCheckAgain:
        obj = object.__new__(ForSemCheckAgain)
        obj.trans_prompt = FORGAL_JSON_SEMCHECK_AGAIN_PROMPT
        obj.target_lang = "Simplified_Chinese"
        return obj

    def test_injects_only_task_and_input(self) -> None:
        obj = self._make_obj()
        prompt = obj._build_semcheck_user_content(input_src='#01|{"id":1}')
        self.assertIn("### 任务", prompt)
        self.assertIn("#01|{\"id\":1}", prompt)
        self.assertIn("Simplified_Chinese", prompt)

    def test_no_glossary_or_batch_or_metadata(self) -> None:
        obj = self._make_obj()
        prompt = obj._build_semcheck_user_content(input_src='#01|{"id":1}')
        self.assertNotIn("[Glossary]", prompt)
        self.assertNotIn("[translation_guideline]", prompt)
        self.assertNotIn("[global_prompt]", prompt)
        self.assertNotIn("[plot_metadata]", prompt)
        self.assertNotIn("[history_result]", prompt)
        self.assertNotIn("<batch_metadata>", prompt)
        self.assertNotIn("<translation_guidelines>", prompt)
        self.assertNotIn("<glossary>", prompt)
        self.assertNotIn("[TargetLang]", prompt)
        self.assertNotIn("[Input]", prompt)

    def test_metadata_block_injected_before_task(self) -> None:
        obj = self._make_obj()
        metadata_block = (
            "\n<plot_metadata>\n"
            "id: 01_05_事前準備.txt.json\n"
            "角色: 創、華恋、凛音\n"
            "剧情: 众人入住 cosplay 度假岛 VIP 栋\n"
            "标签: 日常、H\n"
            "</plot_metadata>\n"
        )
        prompt = obj._build_semcheck_user_content(
            input_src='#01|{"id":1}', metadata_block=metadata_block
        )
        # 元数据块在任务说明之前，作为全局语境
        self.assertLess(prompt.index("<plot_metadata>"), prompt.index("### 任务"))
        self.assertIn("角色: 創、華恋、凛音", prompt)
        self.assertIn("剧情: 众人入住 cosplay 度假岛 VIP 栋", prompt)
        # 占位符仍被正确替换
        self.assertIn("Simplified_Chinese", prompt)
        self.assertNotIn("[TargetLang]", prompt)
        self.assertNotIn("[Input]", prompt)

    def test_metadata_block_empty_keeps_old_behavior(self) -> None:
        obj = self._make_obj()
        prompt = obj._build_semcheck_user_content(
            input_src='#01|{"id":1}', metadata_block=""
        )
        # 模板文字本身含 <plot_metadata> 字样，断言实际元数据内容不出现即可
        self.assertNotIn("角色: ", prompt)
        self.assertNotIn("剧情: ", prompt)
        self.assertIn("### 任务", prompt)


if __name__ == "__main__":
    unittest.main()
