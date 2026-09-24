# -*- coding: utf-8 -*-
"""ForToneCheckAgain 词语色彩复核（命中句二次复核）的单元测试。

覆盖：
  - _parse_confirm_response：keep:true 确认（干净新 reason 覆盖旧标记）/
    keep:false 撤销 / 乱码 reason 不覆盖 / keep 值异常与判定缺失 fail-safe
    保留 / 未知 id 与非 JSON 行跳过 / 重复 id 后行覆盖前行 / 空响应全缺失
  - batch_translate：无命中句跳过不发请求（引导先跑 tonecheck）/ 有标记但译文
    失效跳过 / 禁用降级保留旧标记 / 无批次元数据保留标记并跳过（与第一轮相反）/
    本批无相交色彩标注跳过且保留标记（防无依据撤销）/ LLM 调用失败 fail-safe
    保留并上报 / 混合判定应用
  - 分批：优先 numPerRequestToneCheck，回退 numPerRequestSemCheck /
    numPerRequestBetter / 实参
  - user 提示词：仅注入色彩标注、任务说明与批次 input；元数据块置于任务说明之前
  - 注册链：ENGINE_MODULE_PATHS / ENGINE_REGISTRY / NEED_OpenAITokenPool /
    afterTranslation 白名单 / STANDALONE_BACKENDS
"""
import asyncio
import re
import unittest
from unittest.mock import AsyncMock, Mock, patch

from GalTransl.CSentense import CSentense
from GalTransl.Backend.Prompts import FORGAL_JSON_FORWORDTONE_AGAIN_PROMPT
from GalTransl.Backend.ForToneCheckAgain import ForToneCheckAgain
from GalTransl.Backend.metadata import BatchMetadata


class _FakePjConfig:
    """_parse_confirm_response 测试替身：仅需 _log_tag 所在对象可用。"""

    def getKey(self, key: str):
        return None


class _FakeBatchConfig:
    """分批配置替身：可控 getKey 返回值，active_workers=1 触发输入日志。"""

    def __init__(self, values: dict) -> None:
        self._values = values

    active_workers = 1

    def getKey(self, key: str):
        return self._values.get(key)


def _make_parser() -> ForToneCheckAgain:
    """绕过重型 __init__，仅装配 _parse_confirm_response 所需属性。"""
    obj = object.__new__(ForToneCheckAgain)
    obj._log_tag = "[色彩复核]"
    obj._disabled_reason = ""
    obj.pj_config = _FakePjConfig()
    return obj


def _trans(
    index: int, pre_dst: str = "译文", tone: str = "", post_src: str = "原文"
) -> CSentense:
    t = CSentense(f"src{index}", index=index)
    t.post_src = post_src
    t.pre_dst = pre_dst
    t.proofread_zh = ""
    t.tone_issue = tone
    return t


def _batch_meta(batches: list) -> BatchMetadata:
    return BatchMetadata(id="f.json", batches=batches)


class ParseConfirmResponseTests(unittest.TestCase):
    def test_confirm_keeps_mark_and_updates_reason(self) -> None:
        parser = _make_parser()
        trans = _trans(3, tone="旧原因")
        line = 'tma|{"id": 3, "keep": true, "reason": "标注口语活泼，译文过于书面"}'
        confirm, dismiss = parser._parse_confirm_response(line, [trans])
        self.assertEqual((confirm, dismiss), (1, 0))
        self.assertEqual(trans.tone_issue, "标注口语活泼，译文过于书面")

    def test_confirm_without_reason_keeps_old_mark(self) -> None:
        parser = _make_parser()
        trans = _trans(3, tone="旧原因")
        line = 'tma|{"id": 3, "keep": true}'
        confirm, dismiss = parser._parse_confirm_response(line, [trans])
        self.assertEqual((confirm, dismiss), (1, 0))
        self.assertEqual(trans.tone_issue, "旧原因")

    def test_dismiss_clears_mark(self) -> None:
        parser = _make_parser()
        trans = _trans(5, tone="词语色彩不一致")
        line = '1mj|{"id": 5, "keep": false}'
        confirm, dismiss = parser._parse_confirm_response(line, [trans])
        self.assertEqual((confirm, dismiss), (0, 1))
        self.assertEqual(trans.tone_issue, "")

    def test_garbled_reason_not_overwrite_old_mark(self) -> None:
        parser = _make_parser()
        trans = _trans(3, tone="旧原因")
        line = 'tma|{"id": 3, "keep": true, "reason": "色彩��乱码"}'
        confirm, dismiss = parser._parse_confirm_response(line, [trans])
        self.assertEqual((confirm, dismiss), (1, 0))
        # 乱码 reason 不污染标记，保留第一轮原因
        self.assertEqual(trans.tone_issue, "旧原因")

    def test_keep_anomaly_keeps_mark_fail_safe(self) -> None:
        parser = _make_parser()
        trans = _trans(3, tone="词语色彩不一致")
        # 模型输出字符串 "true" 而非布尔：keep 值异常 → 保留标记
        line = 'tma|{"id": 3, "keep": "true"}'
        confirm, dismiss = parser._parse_confirm_response(line, [trans])
        self.assertEqual((confirm, dismiss), (0, 0))
        self.assertEqual(trans.tone_issue, "词语色彩不一致")

    def test_missing_verdict_keeps_mark_fail_safe(self) -> None:
        parser = _make_parser()
        trans_list = [_trans(2, tone="词语色彩不一致"), _trans(3, tone="词语色彩不一致")]
        # 仅 id=2 获判定，id=3 缺失 → 保留
        line = 'abc|{"id": 2, "keep": false}'
        confirm, dismiss = parser._parse_confirm_response(line, trans_list)
        self.assertEqual((confirm, dismiss), (0, 1))
        self.assertEqual(trans_list[0].tone_issue, "")
        self.assertEqual(trans_list[1].tone_issue, "词语色彩不一致")

    def test_empty_response_all_missing_kept(self) -> None:
        parser = _make_parser()
        trans_list = [_trans(1, tone="词语色彩不一致"), _trans(2, tone="词语色彩不一致")]
        confirm, dismiss = parser._parse_confirm_response("", trans_list)
        self.assertEqual((confirm, dismiss), (0, 0))
        self.assertTrue(all(t.tone_issue == "词语色彩不一致" for t in trans_list))

    def test_unknown_id_skipped(self) -> None:
        parser = _make_parser()
        trans = _trans(1, tone="词语色彩不一致")
        line = 'nnk|{"id": 999, "keep": false}'
        confirm, dismiss = parser._parse_confirm_response(line, [trans])
        self.assertEqual((confirm, dismiss), (0, 0))
        self.assertEqual(trans.tone_issue, "词语色彩不一致")

    def test_non_json_line_skipped(self) -> None:
        parser = _make_parser()
        trans = _trans(1, tone="词语色彩不一致")
        line = "纯文本没有 JSON"
        confirm, dismiss = parser._parse_confirm_response(line, [trans])
        self.assertEqual((confirm, dismiss), (0, 0))
        self.assertEqual(trans.tone_issue, "词语色彩不一致")

    def test_duplicate_id_last_line_wins(self) -> None:
        parser = _make_parser()
        trans = _trans(3, tone="旧原因")
        text = "\n".join(
            [
                'a1b|{"id": 3, "keep": false}',
                'c2d|{"id": 3, "keep": true, "reason": "后行覆盖"}',
            ]
        )
        # 逐行应用：先撤销后确认，两行均计数；最终标记以后行为准
        confirm, dismiss = parser._parse_confirm_response(text, [trans])
        self.assertEqual((confirm, dismiss), (1, 1))
        self.assertEqual(trans.tone_issue, "后行覆盖")

    def test_sparse_by_id_order_irrelevant(self) -> None:
        parser = _make_parser()
        trans_list = [
            _trans(3, tone="词语色彩不一致"),
            _trans(12, tone="词语色彩不一致"),
            _trans(14, tone="词语色彩不一致"),
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
        self.assertEqual(
            [t.tone_issue for t in trans_list], ["词语色彩不一致", "", ""]
        )


class _ToneAgainTestBase:
    """装配 batch_translate 测试替身的公共方法。"""

    def _make_obj(
        self, values: dict, batches: list | None, llm_resp: str = ""
    ) -> ForToneCheckAgain:
        obj = object.__new__(ForToneCheckAgain)
        obj._log_tag = "[色彩复核]"
        obj._disabled_reason = ""
        obj.pj_config = _FakeBatchConfig(values)
        obj.system_prompt = "system"
        obj.trans_prompt = FORGAL_JSON_FORWORDTONE_AGAIN_PROMPT
        obj.target_lang = "Simplified_Chinese"
        obj.eng_type = "ForToneCheckAgain"
        obj._recorded_errors = []
        obj._llm_calls = []
        # 基类属性：_resolve_file_metadata 会访问 file_metadata_map / project_config
        # 与惰性载入标志 _file_metadata_loaded / _file_metadata_by_file
        obj.file_metadata_map = {}
        obj.project_config = None
        obj._file_metadata_loaded = True
        obj._file_metadata_by_file = {}
        obj._resolve_batch_metadata = Mock(
            return_value=None if batches is None else _batch_meta(batches)
        )

        def fake_record(filename, idx_tip, message, model):
            obj._recorded_errors.append((filename, idx_tip, message, model))

        obj._record_round_runtime_error = fake_record

        async def fake_llm(messages, filename, idx_tip, cb):
            obj._llm_calls.append(messages)
            return llm_resp, None

        obj._call_llm = fake_llm
        return obj


class BatchTranslateGuardTests(unittest.IsolatedAsyncioTestCase, _ToneAgainTestBase):
    async def test_no_flagged_sentences_skips_without_llm_call(self) -> None:
        obj = self._make_obj({}, batches=[{"区间": [1, 10], "用词色彩": "口语"}])
        with patch.object(
            ForToneCheckAgain, "_call_llm", new=AsyncMock()
        ) as mock_llm:
            trans_list = [_trans(1, "正常译文", ""), _trans(2, "正常译文二", "")]
            result = await obj.batch_translate("f.json", "c.json", trans_list, 20)
        mock_llm.assert_not_awaited()
        self.assertIs(result, trans_list)

    async def test_no_mark_skips_with_guidance_log(self) -> None:
        # 全文件无 tone_issue：跳过并提示先执行色彩检查
        obj = self._make_obj({}, batches=[{"区间": [1, 10], "用词色彩": "口语"}])
        trans_list = [_trans(1, "正常译文", ""), _trans(2, "正常译文二", "")]
        with patch.object(
            ForToneCheckAgain, "_call_llm", new=AsyncMock()
        ) as mock_llm, self.assertLogs("GalTransl", level="INFO") as cm:
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        mock_llm.assert_not_awaited()
        joined = "\n".join(cm.output)
        self.assertIn("无待复核的命中句", joined)
        self.assertIn("先执行词语色彩检查", joined)

    async def test_mark_without_dst_skips_with_guidance_log(self) -> None:
        # 有标记但译文失效（pre_dst 为空）：提示译文未生成
        obj = self._make_obj({}, batches=[{"区间": [1, 10], "用词色彩": "口语"}])
        trans_list = [_trans(1, "正常译文", ""), _trans(2, "", "词语色彩不一致")]
        with patch.object(
            ForToneCheckAgain, "_call_llm", new=AsyncMock()
        ) as mock_llm, self.assertLogs("GalTransl", level="INFO") as cm:
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        mock_llm.assert_not_awaited()
        joined = "\n".join(cm.output)
        self.assertIn("存在色彩不一致标记但均无有效译文", joined)

    async def test_disabled_skips_and_keeps_old_marks(self) -> None:
        obj = self._make_obj({}, batches=[{"区间": [1, 10], "用词色彩": "口语"}])
        obj._disabled_reason = "主翻译令牌池无可用 token"
        trans_list = [_trans(1, "译文", "词语色彩不一致")]
        with patch.object(
            ForToneCheckAgain, "_call_llm", new=AsyncMock()
        ) as mock_llm:
            result = await obj.batch_translate("f.json", "c.json", trans_list, 20)
        mock_llm.assert_not_awaited()
        self.assertIs(result, trans_list)
        self.assertEqual(trans_list[0].tone_issue, "词语色彩不一致")

    async def test_no_batch_metadata_keeps_marks_and_skips(self) -> None:
        # 无批次元数据（无标注基准）→ 保留既有标记并跳过（与第一轮「清标记后跳过」相反）
        obj = self._make_obj({}, batches=None)
        trans_list = [_trans(1, "译文", "词语色彩不一致")]
        with patch.object(
            ForToneCheckAgain, "_call_llm", new=AsyncMock()
        ) as mock_llm, self.assertLogs("GalTransl", level="INFO") as cm:
            result = await obj.batch_translate("f.json", "c.json", trans_list, 20)
        mock_llm.assert_not_awaited()
        self.assertIs(result, trans_list)
        self.assertEqual(trans_list[0].tone_issue, "词语色彩不一致")
        self.assertIn("无复核依据，保留既有标记", "\n".join(cm.output))

    async def test_batch_without_guide_skips_and_keeps_marks(self) -> None:
        # 本批句子落在无标注区间（[50-60] 与句子 [1,2] 不相交）→ 保留标记、不发请求
        obj = self._make_obj({}, batches=[{"区间": [50, 60], "用词色彩": "口语"}])
        trans_list = [
            _trans(1, "译文一", "词语色彩不一致"),
            _trans(2, "译文二", "词语色彩不一致"),
        ]
        with patch.object(
            ForToneCheckAgain, "_call_llm", new=AsyncMock()
        ) as mock_llm:
            await obj.batch_translate("f.json", "c.json", trans_list, 20)
        mock_llm.assert_not_awaited()
        self.assertEqual(len(obj._llm_calls), 0)
        self.assertTrue(all(t.tone_issue == "词语色彩不一致" for t in trans_list))

    async def test_llm_failure_keeps_marks_and_records_error(self) -> None:
        obj = self._make_obj({}, batches=[{"区间": [1, 100], "用词色彩": "口语"}])

        async def failing_llm(messages, filename, idx_tip, cb):
            raise RuntimeError("boom")

        obj._call_llm = failing_llm
        trans_list = [
            _trans(1, "正常译文", ""),
            _trans(2, "第一轮标记的句子", "词语色彩不一致"),
        ]
        await obj.batch_translate("f.json", "c.json", trans_list, 20)
        # fail-safe：调用失败保留第一轮信号，并上报 1 条运行时错误
        self.assertEqual(trans_list[0].tone_issue, "")
        self.assertEqual(trans_list[1].tone_issue, "词语色彩不一致")
        self.assertEqual(len(obj._recorded_errors), 1)
        self.assertIn("boom", obj._recorded_errors[0][2])

    async def test_mixed_verdict_applied(self) -> None:
        obj = self._make_obj(
            {},
            batches=[{"区间": [1, 100], "用词色彩": "口语"}],
            llm_resp=(
                'abc|{"id": 2, "keep": true, "reason": "标注口语活泼，译文过于书面"}\n'
                'def|{"id": 3, "keep": false}'
            ),
        )
        trans_list = [
            _trans(1, "正常译文", ""),
            _trans(2, "确认句", "词语色彩不一致"),
            _trans(3, "撤销句", "词语色彩不一致"),
        ]
        await obj.batch_translate("f.json", "c.json", trans_list, 20)
        self.assertEqual(trans_list[0].tone_issue, "")
        self.assertEqual(trans_list[1].tone_issue, "标注口语活泼，译文过于书面")
        self.assertEqual(trans_list[2].tone_issue, "")

    async def test_file_metadata_injected_into_user_content(self) -> None:
        obj = self._make_obj(
            {}, batches=[{"区间": [1, 100], "用词色彩": "口语"}], llm_resp=""
        )
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
                "address_map": [
                    {"原文": "お兄ちゃん", "译文": "哥哥", "被称呼者": "華恋", "称呼者": "創"}
                ],
            },
        )()
        trans_list = [_trans(2, "确认句", "词语色彩不一致")]
        with patch.object(
            ForToneCheckAgain, "_resolve_file_metadata", return_value=fake_meta
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


class ToneAgainBatchSplitTests(unittest.IsolatedAsyncioTestCase, _ToneAgainTestBase):
    """验证分批优先级：numPerRequestToneCheck > numPerRequestSemCheck > numPerRequestBetter > 实参。"""

    async def _run_split(self, values: dict, num_arg: int = 20, total: int = 5) -> list:
        batches = [{"区间": [1, 100], "用词色彩": "口语"}]
        obj = self._make_obj(values, batches=batches)
        targets = [_trans(i, tone="词语色彩不一致") for i in range(1, total + 1)]
        await obj.batch_translate("f.json", "c.json", targets, num_arg)
        sizes = []
        for m in obj._llm_calls:
            user = m[-1]["content"]
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

    async def test_tone_check_key_preferred(self) -> None:
        sizes = await self._run_split(
            {"gpt.numPerRequestToneCheck": 2, "gpt.numPerRequestSemCheck": 3}
        )
        self.assertEqual(sizes, [2, 2, 1])

    async def test_falls_back_to_semcheck_key(self) -> None:
        sizes = await self._run_split({"gpt.numPerRequestSemCheck": 3})
        self.assertEqual(sizes, [3, 2])

    async def test_falls_back_to_better_key(self) -> None:
        sizes = await self._run_split({"gpt.numPerRequestBetter": 4})
        self.assertEqual(sizes, [4, 1])

    async def test_falls_back_to_argument(self) -> None:
        sizes = await self._run_split({}, num_arg=2)
        self.assertEqual(sizes, [2, 2, 1])


class ToneAgainPromptInjectionTests(unittest.TestCase):
    """验证 user 提示词：仅注入色彩标注、任务说明与批次 input，不注入其它内容。"""

    def _make_obj(self) -> ForToneCheckAgain:
        obj = object.__new__(ForToneCheckAgain)
        obj.trans_prompt = FORGAL_JSON_FORWORDTONE_AGAIN_PROMPT
        obj.target_lang = "Simplified_Chinese"
        return obj

    def test_injects_guide_task_and_input(self) -> None:
        obj = self._make_obj()
        prompt = obj._build_tonecheck_again_user_content(
            '#01|{"id":1}', "区间[1-10] 用词色彩:口语活泼"
        )
        self.assertIn("<tone_guide>", prompt)
        self.assertIn("口语活泼", prompt)
        self.assertIn("### 任务", prompt)
        self.assertIn('{"id":1}', prompt)
        self.assertIn("Simplified_Chinese", prompt)
        self.assertNotIn("[ToneGuide]", prompt)
        self.assertNotIn("[TargetLang]", prompt)
        self.assertNotIn("[Input]", prompt)

    def test_no_glossary_or_batch_or_history(self) -> None:
        obj = self._make_obj()
        prompt = obj._build_tonecheck_again_user_content('#01|{"id":1}', "标注")
        self.assertNotIn("[Glossary]", prompt)
        self.assertNotIn("[translation_guideline]", prompt)
        self.assertNotIn("[global_prompt]", prompt)
        self.assertNotIn("[history_result]", prompt)
        self.assertNotIn("<batch_metadata>", prompt)
        self.assertNotIn("<translation_guidelines>", prompt)
        self.assertNotIn("<glossary>", prompt)

    def test_metadata_block_injected_before_task(self) -> None:
        obj = self._make_obj()
        metadata_block = (
            "\n<plot_metadata>\n角色: 創、華恋\n剧情: 度假岛\n</plot_metadata>\n"
        )
        prompt = obj._build_tonecheck_again_user_content(
            '#01|{"id":1}', "区间[1-10] 用词色彩:口语", metadata_block
        )
        self.assertLess(prompt.index("<plot_metadata>"), prompt.index("### 任务"))
        self.assertIn("角色: 創、華恋", prompt)
        self.assertIn("Simplified_Chinese", prompt)

    def test_empty_guide_removes_guide_segment(self) -> None:
        # 防御性分支：正常流程已在调用前跳过无标注批
        obj = self._make_obj()
        prompt = obj._build_tonecheck_again_user_content('#01|{"id":1}', "")
        self.assertNotIn("<tone_guide>", prompt)
        self.assertIn("### 任务", prompt)


class ToneAgainRegistrationTests(unittest.TestCase):
    def test_engine_registered(self) -> None:
        from GalTransl.Backend.BaseEngine import ENGINE_MODULE_PATHS, ENGINE_REGISTRY

        self.assertEqual(
            ENGINE_MODULE_PATHS["ForToneCheckAgain"],
            "GalTransl.Backend.ForToneCheckAgain",
        )
        self.assertIn("ForToneCheckAgain", ENGINE_REGISTRY)

    def test_engine_in_token_pool_and_after_trans_whitelist(self) -> None:
        from GalTransl import NEED_OpenAITokenPool
        from GalTransl.Frontend.llm_postprocess import _resolve_after_translation_order

        self.assertIn("ForToneCheckAgain", NEED_OpenAITokenPool)

        class _Cfg:
            def __init__(self, raw) -> None:
                self._raw = raw
                self.keyValues = {"gpt.afterTranslation": raw}

            def getKey(self, key: str):
                return self.keyValues.get(key)

        order = _resolve_after_translation_order(
            _Cfg(["tonecheck", "tonecheckagain"])
        )
        self.assertEqual(order, ["tonecheck", "tonecheckagain"])

    def test_standalone_backend_registered_with_finalize(self) -> None:
        from GalTransl.Frontend.llm_standalone import STANDALONE_BACKENDS

        spec = STANDALONE_BACKENDS["ForToneCheckAgain"]
        self.assertEqual(spec.log_tag, "[色彩复核]")
        # 标记类后端需在写盘前认领 problem
        self.assertTrue(spec.finalize_problems)
        self.assertFalse(spec.needs_fix_params)


if __name__ == "__main__":
    unittest.main()
