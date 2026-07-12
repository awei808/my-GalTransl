"""
ForGalJsonMulitChat 单元测试

覆盖：
  - 模块级 detect_line_break_symbol
  - PlotMetadata / _format_plot_metadata_block
  - _encode_sig_jsonline / _build_input_jsonlines
  - _build_round_user_content（首轮 / 续轮）
  - _parse_jsonline_result_line（逐行校验）
  - _parse_non_stream_text / _parse_stream_lines
  - _handle_parse_result（空响应 / 成功 / </think> + 代码块 / 流式）
  - 对话生命周期（_ensure_conversation / _trim_conversation / reset_conversation / set_plot_metadata）
  - __init__ 的 multi_round_max_history 配置解析（回归）
  - translate 集成（非流式成功 / 流式成功 / 校对模式 / 整批失败兜底）

为绕过重量级 BaseTranslate.__init__ 与 OpenAI 客户端初始化，统一使用
ForGalJsonMulitChat.__new__ 创建实例并手动打桩所需属性；网络调用通过
替换 translator.ask_chatbot 进行 mock。
"""

import json
import re
import unittest
from types import SimpleNamespace, MethodType
from unittest.mock import patch

from GalTransl.Backend.BaseTranslate import BaseTranslate
from GalTransl.Backend.ForGalJsonMulitChat import (
    ForGalJsonMulitChat,
    PlotMetadata,
    detect_line_break_symbol,
)
from GalTransl.Backend.Prompts import FORGAL_JSON_TRANS_PROMPT
from GalTransl.CSentense import CSentense


def make_token(model_name="test-model"):
    return SimpleNamespace(model_name=model_name, domain="https://example.com")


def make_translator(proofread_target_lang="English"):
    """
    通过 __new__ 创建实例并打桩 translate 流程所需的全部属性，
    不触发 BaseTranslate.__init__ / init_chatbot（避免网络与配置依赖）。
    """
    t = ForGalJsonMulitChat.__new__(ForGalJsonMulitChat)
    t.pj_config = SimpleNamespace(
        active_workers=0,
        stop_event=None,
        translation_guideline="",
        getProjectDir=lambda: "",
    )
    t.eng_type = "ForGal-json-multi-chat"
    t.enhance_jailbreak = False
    t.system_prompt = "SYSTEM_PROMPT"
    t.trans_prompt = "[translation_guideline]\n[Glossary]\n[plot_metadata]\n[Input]"
    t.source_lang = "Japanese"
    t.target_lang = proofread_target_lang  # 非中文，跳过 opencc
    t.conversations = {}
    t._force_first_round_files = set()
    t.plot_metadata_map = {}
    t._plot_metadata_by_file = {}
    t._plot_metadata_loaded = False
    t.project_config = None
    t.multi_round_max_history = 0
    t.last_file_name = ""
    t._last_chatbot_was_stream = False
    t._last_chatbot_model_name = ""
    return t


class DetectLineBreakSymbolTests(unittest.TestCase):
    def test_literal_crlf_has_highest_priority(self):
        # 字面转义 \\r\\n 优先于真实 CRLF
        self.assertEqual(detect_line_break_symbol("a\\r\\nb\r\nc"), "\\r\\n")

    def test_actual_crlf(self):
        self.assertEqual(detect_line_break_symbol("a\rb"), "")  # 无
        self.assertEqual(detect_line_break_symbol("a\r\nb"), "\r\n")

    def test_literal_lf_priority_over_actual_lf(self):
        # 字面 \\n 优先于真实 \n
        self.assertEqual(detect_line_break_symbol("a\\nb\nc"), "\\n")

    def test_actual_lf(self):
        self.assertEqual(detect_line_break_symbol("a\nb"), "\n")

    def test_no_symbol(self):
        self.assertEqual(detect_line_break_symbol("a\tb c"), "")

    def test_combined_literal_crlf_and_literal_lf(self):
        # 同时存在字面 \\r\\n 与字面 \\n 时，应判定为字面 \\r\\n
        self.assertEqual(detect_line_break_symbol("x\\r\\ny\\nz"), "\\r\\n")


class PlotMetadataTests(unittest.TestCase):
    def test_construction_and_repr(self):
        md = PlotMetadata(
            id="scene-01",
            character=["Alice", "Bob"],
            costume="红裙与披风",
            plot="summary",
            tags=["战斗", "日常"],
        )
        self.assertEqual(md.id, "scene-01")
        self.assertEqual(md.character, ["Alice", "Bob"])
        self.assertEqual(md.costume, "红裙与披风")
        self.assertEqual(md.plot, "summary")
        self.assertEqual(md.tags, ["战斗", "日常"])
        self.assertIn("scene-01", repr(md))

    def test_construction_defaults_id_empty(self):
        md = PlotMetadata(
            character=["Alice"], costume="", plot="s", tags=["x"]
        )
        self.assertEqual(md.id, "")
        self.assertEqual(md.character, ["Alice"])

    def test_construction_normalizes_none_tags_to_list(self):
        md = PlotMetadata(character="爱丽丝", costume="红裙", plot="剧情", tags=None)
        self.assertEqual(md.tags, [])

    def test_construction_normalizes_none_id_to_empty(self):
        md = PlotMetadata(id=None, character="爱丽丝", costume="红裙", plot="剧情", tags=[])
        self.assertEqual(md.id, "")

    def test_format_block(self):
        t = make_translator()
        md = PlotMetadata(
            id="scene-01",
            character=["爱丽丝"],
            costume="白色连衣裙",
            plot="一段剧情",
            tags=["战斗"],
        )
        block = t._format_plot_metadata_block(md)
        self.assertIn("<plot_metadata>", block)
        self.assertIn("id: scene-01", block)
        self.assertIn("角色: 爱丽丝", block)
        self.assertIn("服装: 白色连衣裙", block)
        self.assertIn("剧情: 一段剧情", block)
        self.assertIn("标签: 战斗", block)
        self.assertIn("请参考上述", block)

    def test_format_block_accepts_scalar_角色_and_剧情(self):
        t = make_translator()
        md = PlotMetadata(
            character=" solo 主角", costume="披风", plot="单线剧情", tags="冒险"
        )
        block = t._format_plot_metadata_block(md)
        self.assertIn("角色: solo 主角", block)
        self.assertIn("标签: 冒险", block)

    def test_format_block_omits_empty_id(self):
        t = make_translator()
        md = PlotMetadata(character=["爱丽丝"], costume="", plot="s", tags=[])
        block = t._format_plot_metadata_block(md)
        self.assertNotIn("id:", block)

    def test_format_block_empty_lists(self):
        t = make_translator()
        md = PlotMetadata(character=[], costume="", plot="", tags=[])
        block = t._format_plot_metadata_block(md)
        self.assertIn("角色: 无", block)
        self.assertIn("服装: 无", block)
        self.assertIn("剧情: 无", block)
        self.assertIn("标签: 无", block)


class EncodeSigJsonlineTests(unittest.TestCase):
    def test_format(self):
        t = make_translator()
        obj = {"id": 0, "name": "x", "src": "こんにちは"}
        out = t._encode_sig_jsonline("abc", obj)
        self.assertEqual(out, 'abc|{"id": 0, "name": "x", "src": "こんにちは"}')

    def test_ensure_ascii_false(self):
        t = make_translator()
        obj = {"id": 1, "src": "中文"}
        out = t._encode_sig_jsonline("zzz", obj)
        self.assertIn("中文", out)


class BuildInputJsonlinesTests(unittest.TestCase):
    def _build(self, trans_list, proofread=False):
        t = make_translator()
        return t, t._build_input_jsonlines(trans_list, proofread, "f.json")

    def test_speaker_newline_and_tab_stripped(self):
        # 说话人含换行/制表符应被清洗，避免破坏 jsonline
        trans = CSentense("你好", speaker="旁白\n\tA", index=0)
        t, (input_list, sig_list, n_symbol, input_src) = self._build([trans])
        obj = json.loads(input_list[0].split("|", 1)[1])
        self.assertNotIn("\n", obj["name"])
        self.assertNotIn("\t", obj["name"])
        self.assertNotIn("\r", obj["name"])

    def test_tab_and_newline_replaced(self):
        # \t -> [t]，\n -> <br>
        trans = CSentense("一行\t二行\n三行", speaker="", index=0)
        t, (input_list, sig_list, n_symbol, input_src) = self._build([trans])
        self.assertEqual(n_symbol, "\n")
        obj = json.loads(input_list[0].split("|", 1)[1])
        self.assertIn("[t]", obj["src"])
        self.assertIn("<br>", obj["src"])

    def test_sig_unique_and_length(self):
        trans_list = [CSentense(f"句{i}", index=i) for i in range(10)]
        t, (input_list, sig_list, n_symbol, input_src) = self._build(trans_list)
        self.assertEqual(len(sig_list), 10)
        self.assertEqual(len(set(sig_list)), 10)
        for s in sig_list:
            self.assertEqual(len(s), 3)
        # 每行都以对应 sig 开头
        for raw, sig in zip(input_list, sig_list):
            self.assertTrue(raw.startswith(sig + "|"))

    def test_no_speaker_removes_name(self):
        trans = CSentense("旁白句", speaker="", index=0)
        t, (input_list, sig_list, n_symbol, input_src) = self._build([trans])
        obj = json.loads(input_list[0].split("|", 1)[1])
        self.assertNotIn("name", obj)

    def test_proofread_includes_dst(self):
        trans = CSentense("原文", speaker="", index=0)
        trans.pre_dst = "已有译文"
        trans.proofread_zh = ""  # 使用 pre_dst
        t, (input_list, sig_list, n_symbol, input_src) = self._build([trans], proofread=True)
        obj = json.loads(input_list[0].split("|", 1)[1])
        self.assertIn("dst", obj)
        self.assertEqual(obj["dst"], "已有译文")

    def test_combined_newline_detected_once(self):
        # 仅第二句含换行符，整批应仍能检测到 \n（仅在批首判定一次）
        t_list = [
            CSentense("第一句", index=0),
            CSentense("第二句\n含换行", index=1),
        ]
        t, (input_list, sig_list, n_symbol, input_src) = self._build(t_list)
        self.assertEqual(n_symbol, "\n")


class BuildRoundUserContentTests(unittest.TestCase):
    def test_first_round_includes_prompt_and_metadata(self):
        t = make_translator()
        t.plot_metadata_map = {
            "f.json": PlotMetadata(character=["角色"], costume="", plot="剧情", tags=[])
        }
        trans = CSentense("原文", index=0)
        _, _, _, input_src = t._build_input_jsonlines([trans], False, "f.json")
        # 伪装为「首轮」：仅 [system]
        t.conversations["f.json"] = [{"role": "system", "content": t.system_prompt}]
        content = t._build_round_user_content(
            t.conversations["f.json"], input_src, "", "f.json", is_first_round=True
        )
        # 首轮应含翻译提示词占位替换结果、待译内容、剧情元数据
        self.assertIn(input_src, content)
        self.assertIn("剧情", content)
        self.assertIn("角色", content)
        # 顺序断言：剧情元数据（[plot_metadata] 占位符）必须位于待译内容（[Input]）之前，
        # 即「翻译规范之后、input 之前」的注入顺序。
        self.assertLess(content.index("角色"), content.index(input_src))
        self.assertLess(content.index("剧情"), content.index(input_src))

    def test_first_round_has_no_history_block_when_empty(self):
        # 回归：无历史翻译时，首轮提示词不应残留 <history_result>None</history_result> 这类
        # 历史记录相关内容，整块应被移除。使用真实模板才能复现该块。
        t = make_translator()
        t.trans_prompt = FORGAL_JSON_TRANS_PROMPT
        t.last_translations = {}  # 无历史
        trans = CSentense("原文", index=0)
        _, _, _, input_src = t._build_input_jsonlines([trans], False, "f.json")
        t.conversations["f.json"] = [{"role": "system", "content": t.system_prompt}]
        content = t._build_round_user_content(
            t.conversations["f.json"], input_src, "", "f.json", is_first_round=True
        )
        self.assertNotIn("<history_result>", content)
        self.assertNotIn("[history_result]", content)
        self.assertNotIn("None", content)  # 不应残留占位 None

    def test_history_block_kept_when_present(self):
        # 反向守护：存在历史翻译时，<history_result> 块应保留且填充真实内容。
        t = make_translator()
        t.trans_prompt = FORGAL_JSON_TRANS_PROMPT
        t.last_translations = {"f.json": "ahr|{\"id\":1,\"dst\":\"历史译文\"}"}
        trans = CSentense("原文", index=0)
        _, _, _, input_src = t._build_input_jsonlines([trans], False, "f.json")
        t.conversations["f.json"] = [{"role": "system", "content": t.system_prompt}]
        content = t._build_round_user_content(
            t.conversations["f.json"], input_src, "", "f.json", is_first_round=True
        )
        self.assertIn("<history_result>", content)
        self.assertIn("历史译文", content)

    def test_subsequent_round_is_jsonline_only(self):
        t = make_translator()
        trans = CSentense("原文", index=0)
        _, _, _, input_src = t._build_input_jsonlines([trans], False, "f.json")
        # 续轮：历史含 system + user + assistant
        conv = [
            {"role": "system", "content": t.system_prompt},
            {"role": "user", "content": "首轮内容"},
            {"role": "assistant", "content": "首轮回复"},
        ]
        content = t._build_round_user_content(
            conv, input_src, "", "f.json", is_first_round=False
        )
        # 续轮不应重复翻译要求文本，应直接是待译 jsonline
        self.assertIn(input_src, content)
        self.assertNotIn("SYSTEM_PROMPT", content)

    def test_subsequent_round_with_gptdict(self):
        t = make_translator()
        trans = CSentense("原文", index=0)
        _, _, _, input_src = t._build_input_jsonlines([trans], False, "f.json")
        conv = [
            {"role": "system", "content": t.system_prompt},
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
        ]
        content = t._build_round_user_content(
            conv, input_src, "术语表内容", "f.json", is_first_round=False
        )
        self.assertIn("术语表内容", content)
        self.assertIn(input_src, content)


class ParseJsonlineResultLineTests(unittest.TestCase):
    def setUp(self):
        self.t = make_translator()
        self.trans_list = [CSentense(f"原文{i}", index=i) for i in range(3)]
        _, self.sig_list, _, _ = self.t._build_input_jsonlines(
            self.trans_list, False, "f.json"
        )

    def _line(self, sig_idx, obj):
        return self.sig_list[sig_idx] + "|" + json.dumps(obj, ensure_ascii=False)

    def test_valid_line(self):
        line = self._line(0, {"id": 0, "dst": "译0"})
        res, err = self.t._parse_jsonline_result_line(
            line, self.trans_list, "m", "", "dst",
            {"i": -1, "success_count": 0}, [], filename="f.json",
            emit_runtime_success=False, sig_list=self.sig_list,
        )
        self.assertTrue(res, err)
        self.assertEqual(self.trans_list[0].pre_dst, "译0")

    def test_missing_sig_prefix(self):
        # 没有 sig| 前缀
        line = '{"id": 0, "dst": "译0"}'
        res, err = self.t._parse_jsonline_result_line(
            line, self.trans_list, "m", "", "dst",
            {"i": -1, "success_count": 0}, [], filename="f.json",
            emit_runtime_success=False, sig_list=self.sig_list,
        )
        self.assertFalse(res)
        self.assertIn("sig", err)

    def test_bad_json(self):
        line = self.sig_list[0] + "|{not json"
        res, err = self.t._parse_jsonline_result_line(
            line, self.trans_list, "m", "", "dst",
            {"i": -1, "success_count": 0}, [], filename="f.json",
            emit_runtime_success=False, sig_list=self.sig_list,
        )
        self.assertFalse(res)
        self.assertIn("json", err)

    def test_wrong_sig(self):
        wrong = "zzz" if self.sig_list[0] != "zzz" else "aaa"
        line = wrong + '|{"id": 0, "dst": "译0"}'
        res, err = self.t._parse_jsonline_result_line(
            line, self.trans_list, "m", "", "dst",
            {"i": -1, "success_count": 0}, [], filename="f.json",
            emit_runtime_success=False, sig_list=self.sig_list,
        )
        self.assertFalse(res)
        self.assertIn("串行", err)

    def test_wrong_id(self):
        line = self._line(0, {"id": 99, "dst": "译0"})
        res, err = self.t._parse_jsonline_result_line(
            line, self.trans_list, "m", "", "dst",
            {"i": -1, "success_count": 0}, [], filename="f.json",
            emit_runtime_success=False, sig_list=self.sig_list,
        )
        self.assertFalse(res)
        self.assertIn("id", err)

    def test_missing_key(self):
        line = self._line(0, {"id": 0, "name": "x"})
        res, err = self.t._parse_jsonline_result_line(
            line, self.trans_list, "m", "", "dst",
            {"i": -1, "success_count": 0}, [], filename="f.json",
            emit_runtime_success=False, sig_list=self.sig_list,
        )
        self.assertFalse(res)
        self.assertIn("dst", err)

    def test_empty_dst_when_src_nonempty(self):
        line = self._line(0, {"id": 0, "dst": ""})
        res, err = self.t._parse_jsonline_result_line(
            line, self.trans_list, "m", "", "dst",
            {"i": -1, "success_count": 0}, [], filename="f.json",
            emit_runtime_success=False, sig_list=self.sig_list,
        )
        self.assertFalse(res)
        self.assertIn("空白", err)

    def test_mojibake_rejected(self):
        line = self._line(0, {"id": 0, "dst": "译0�"})
        res, err = self.t._parse_jsonline_result_line(
            line, self.trans_list, "m", "", "dst",
            {"i": -1, "success_count": 0}, [], filename="f.json",
            emit_runtime_success=False, sig_list=self.sig_list,
        )
        self.assertFalse(res)
        self.assertIn("乱码", err)

    def test_br_normalization(self):
        # n_symbol="\n"，解析后 <br> 应还原为真实换行
        line = self._line(0, {"id": 0, "dst": "第一行<br>第二行"})
        res, err = self.t._parse_jsonline_result_line(
            line, self.trans_list, "m", "\n", "dst",
            {"i": -1, "success_count": 0}, [], filename="f.json",
            emit_runtime_success=False, sig_list=self.sig_list,
        )
        self.assertTrue(res, err)
        self.assertIn("\n", self.trans_list[0].pre_dst)
        self.assertNotIn("<br>", self.trans_list[0].pre_dst)


class ParseNonStreamTextTests(unittest.TestCase):
    def setUp(self):
        self.t = make_translator()
        self.trans_list = [CSentense(f"原文{i}", index=i) for i in range(3)]
        _, self.sig_list, _, _ = self.t._build_input_jsonlines(
            self.trans_list, False, "f.json"
        )

    def _resp(self):
        lines = [
            self.sig_list[i] + f'|{{"id": {i}, "dst": "译{i}"}}'
            for i in range(3)
        ]
        return "\n".join(lines)

    def test_full_parse(self):
        res, cnt, err, i = self.t._parse_non_stream_text(
            self._resp(), self.trans_list, make_token(), "", "dst",
            self.sig_list, "f.json",
        )
        self.assertEqual(cnt, 3)
        self.assertEqual(err, "")
        self.assertEqual(len(res), 3)
        self.assertEqual(self.trans_list[0].pre_dst, "译0")

    def test_partial_parse_breaks_on_error(self):
        # 第 2 行（index=1）故意给错 key
        bad = self.sig_list[1] + '|{"id": 1}'
        lines = [
            self.sig_list[0] + '|{"id": 0, "dst": "译0"}',
            bad,
            self.sig_list[2] + '|{"id": 2, "dst": "译2"}',
        ]
        res, cnt, err, i = self.t._parse_non_stream_text(
            "\n".join(lines), self.trans_list, make_token(), "", "dst",
            self.sig_list, "f.json",
        )
        self.assertEqual(cnt, 1)
        self.assertTrue(err)  # 应有错误信息
        self.assertEqual(len(res), 1)

    def test_empty_text(self):
        res, cnt, err, i = self.t._parse_non_stream_text(
            "", self.trans_list, make_token(), "", "dst",
            self.sig_list, "f.json",
        )
        self.assertEqual(cnt, 0)
        self.assertEqual(res, [])


class ParseStreamLinesTests(unittest.TestCase):
    def setUp(self):
        self.t = make_translator()
        self.trans_list = [CSentense(f"原文{i}", index=i) for i in range(3)]
        _, self.sig_list, _, _ = self.t._build_input_jsonlines(
            self.trans_list, False, "f.json"
        )

    def test_accumulates_until_error(self):
        cursor = {"i": -1, "success_count": 0, "started": False}
        parsed = []
        # 前两行正常
        ok = self.t._parse_stream_lines(
            [self.sig_list[0] + '|{"id": 0, "dst": "译0"}',
             self.sig_list[1] + '|{"id": 1, "dst": "译1"}'],
            False,
            trans_list=self.trans_list, n_symbol="", sig_list=self.sig_list,
            key_name="dst", emit_runtime_success=False, filename="f.json",
            parsed_result_trans_list=parsed, cursor=cursor,
        )
        self.assertTrue(ok)
        self.assertEqual(len(parsed), 2)
        # 第三行错误 -> 返回 False 并写入 error
        ok2 = self.t._parse_stream_lines(
            [self.sig_list[2] + '|{"id": 2}'],
            False,
            trans_list=self.trans_list, n_symbol="", sig_list=self.sig_list,
            key_name="dst", emit_runtime_success=False, filename="f.json",
            parsed_result_trans_list=parsed, cursor=cursor,
        )
        self.assertFalse(ok2)
        self.assertTrue(cursor.get("error"))

    def test_skips_markdown_fences_and_preamble(self):
        cursor = {"i": -1, "success_count": 0, "started": False}
        parsed = []
        ok = self.t._parse_stream_lines(
            ["```jsonline", "这里是前言 " + self.sig_list[0] + '|{"id": 0, "dst": "译0"}'],
            False,
            trans_list=self.trans_list, n_symbol="", sig_list=self.sig_list,
            key_name="dst", emit_runtime_success=False, filename="f.json",
            parsed_result_trans_list=parsed, cursor=cursor,
        )
        self.assertTrue(ok)
        self.assertEqual(len(parsed), 1)

    def test_returns_false_once_error_set(self):
        cursor = {"i": 0, "success_count": 1, "started": True, "error": "boom"}
        parsed = []
        ok = self.t._parse_stream_lines(
            ["whatever"], False,
            trans_list=self.trans_list, n_symbol="", sig_list=self.sig_list,
            key_name="dst", emit_runtime_success=False, filename="f.json",
            parsed_result_trans_list=parsed, cursor=cursor,
        )
        self.assertFalse(ok)


class HandleParseResultTests(unittest.TestCase):
    def setUp(self):
        self.t = make_translator()
        self.trans_list = [CSentense(f"原文{i}", index=i) for i in range(3)]
        _, self.sig_list, _, _ = self.t._build_input_jsonlines(
            self.trans_list, False, "f.json"
        )

    def test_empty_response_triggers_fallback(self):
        res = self.t._handle_parse_result(
            raw_resp="", token=make_token(), trans_list=self.trans_list,
            n_symbol="", sig_list=self.sig_list, is_stream=False,
            stream_error_msg="", stream_cursor={"i": -1},
            stream_parsed_list=[], idx_tip="0~2", filename="f.json",
            proofread=False, call_messages=[], prefill_used=False,
        )
        self.assertEqual(len(res), 3)
        self.assertTrue(all("(Failed)" in tr.pre_dst for tr in res[1]))
        self.assertEqual(res[2], 0)  # real_success == 0
        # 关键不变量：返回的 success_count 必须与结果列表长度一致，
        # 否则上层 batch 推进会错位（跳过/重复句子）
        self.assertEqual(res[0], len(res[1]))

    def test_stream_partial_failure_consistent(self):
        # 流式：前 2 句成功，第 3 句解析失败 -> 部分成功 + 1 句兜底
        parsed = []
        cursor = {"i": -1, "success_count": 0, "started": True}
        for i in range(2):
            ok = self.t._parse_stream_lines(
                [self.sig_list[i] + f'|{{"id": {i}, "dst": "译{i}"}}'],
                False, trans_list=self.trans_list, n_symbol="", sig_list=self.sig_list,
                key_name="dst", emit_runtime_success=False, filename="f.json",
                parsed_result_trans_list=parsed, cursor=cursor,
            )
            self.assertTrue(ok)
        ok2 = self.t._parse_stream_lines(
            [self.sig_list[2] + '|{"id": 2}'],
            False, trans_list=self.trans_list, n_symbol="", sig_list=self.sig_list,
            key_name="dst", emit_runtime_success=False, filename="f.json",
            parsed_result_trans_list=parsed, cursor=cursor,
        )
        self.assertFalse(ok2)
        cnt, res, real = self.t._handle_parse_result(
            raw_resp="ignored", token=make_token(), trans_list=self.trans_list,
            n_symbol="", sig_list=self.sig_list, is_stream=True,
            stream_error_msg=cursor.get("error", ""), stream_cursor=cursor,
            stream_parsed_list=parsed, idx_tip="0~2", filename="f.json",
            proofread=False, call_messages=[], prefill_used=False,
        )
        self.assertEqual(real, 2)            # 真实成功 2 句
        self.assertEqual(len(res), 3)     # 2 成功 + 1 兜底
        self.assertEqual(cnt, len(res))   # 关键不变量：num 与结果数一致
        self.assertTrue("(Failed)" in res[2].pre_dst)

    def test_non_stream_success(self):
        lines = [
            self.sig_list[i] + f'|{{"id": {i}, "dst": "译{i}"}}'
            for i in range(3)
        ]
        raw = "\n".join(lines)
        cnt, res, real = self.t._handle_parse_result(
            raw_resp=raw, token=make_token(), trans_list=self.trans_list,
            n_symbol="", sig_list=self.sig_list, is_stream=False,
            stream_error_msg="", stream_cursor={"i": -1},
            stream_parsed_list=[], idx_tip="0~2", filename="f.json",
            proofread=False, call_messages=[], prefill_used=False,
        )
        self.assertEqual(cnt, 3)
        self.assertEqual(real, 3)
        self.assertEqual(len(res), 3)
        # 成功路径应回写对话历史
        self.assertIn("f.json", self.t.conversations)

    def test_think_block_and_code_fence_extraction(self):
        # 含 </think> 与 ```json 代码块，应正确提取并解析
        inner = "\n".join(
            self.sig_list[i] + f'|{{"id": {i}, "dst": "译{i}"}}'
            for i in range(3)
        )
        raw = "好的，我来翻译：</think>\n```json\n" + inner + "\n```"
        cnt, res, real = self.t._handle_parse_result(
            raw_resp=raw, token=make_token(), trans_list=self.trans_list,
            n_symbol="", sig_list=self.sig_list, is_stream=False,
            stream_error_msg="", stream_cursor={"i": -1},
            stream_parsed_list=[], idx_tip="0~2", filename="f.json",
            proofread=False, call_messages=[], prefill_used=False,
        )
        self.assertEqual(cnt, 3)
        self.assertEqual(real, 3)

    def test_streaming_success(self):
        # 模拟流式已边收边解析，传入 stream_parsed_list 与 cursor
        parsed = []
        cursor = {"i": -1, "success_count": 0, "started": True}
        for i in range(3):
            ok = self.t._parse_stream_lines(
                [self.sig_list[i] + f'|{{"id": {i}, "dst": "译{i}"}}'],
                False,
                trans_list=self.trans_list, n_symbol="", sig_list=self.sig_list,
                key_name="dst", emit_runtime_success=False, filename="f.json",
                parsed_result_trans_list=parsed, cursor=cursor,
            )
            self.assertTrue(ok)
        cnt, res, real = self.t._handle_parse_result(
            raw_resp="\n".join(
                self.sig_list[i] + f'|{{"id": {i}, "dst": "译{i}"}}'
                for i in range(3)
            ),
            token=make_token(), trans_list=self.trans_list,
            n_symbol="", sig_list=self.sig_list, is_stream=True,
            stream_error_msg="", stream_cursor=cursor,
            stream_parsed_list=parsed, idx_tip="0~2", filename="f.json",
            proofread=False, call_messages=[], prefill_used=False,
        )
        self.assertEqual(cnt, 3)
        self.assertEqual(real, 3)
        self.assertEqual(len(res), 3)


class ConversationLifecycleTests(unittest.TestCase):
    def test_ensure_conversation_creates_system(self):
        t = make_translator()
        conv = t._ensure_conversation("a.json")
        self.assertEqual(len(conv), 1)
        self.assertEqual(conv[0]["role"], "system")

    def test_ensure_conversation_returns_same_object(self):
        t = make_translator()
        conv1 = t._ensure_conversation("a.json")
        conv1.append({"role": "user", "content": "x"})
        conv2 = t._ensure_conversation("a.json")
        self.assertIs(conv1, conv2)

    def test_trim_keeps_head_when_no_trim(self):
        t = make_translator()
        t.multi_round_max_history = 0
        msgs = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        self.assertIs(t._trim_conversation(msgs), msgs)

    def test_trim_keeps_system_and_first_user(self):
        t = make_translator()
        t.multi_round_max_history = 1
        msgs = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
            {"role": "assistant", "content": "a3"},
        ]
        trimmed = t._trim_conversation(msgs)
        self.assertEqual(trimmed[0]["role"], "system")
        self.assertEqual(trimmed[1]["content"], "u1")
        # max_turns=1 -> 保留 2 条 tail
        self.assertEqual(len(trimmed), 4)
        self.assertEqual(trimmed[-1]["content"], "a3")

    def test_reset_specific_and_all(self):
        t = make_translator()
        t._ensure_conversation("a.json")
        t._ensure_conversation("b.json")
        t._force_first_round_files.add("a.json")
        t.reset_conversation("a.json")
        self.assertNotIn("a.json", t.conversations)
        self.assertIn("b.json", t.conversations)
        self.assertNotIn("a.json", t._force_first_round_files)
        t.reset_conversation()
        self.assertEqual(t.conversations, {})
        self.assertEqual(t._force_first_round_files, set())

    def test_set_plot_metadata(self):
        t = make_translator()
        md = PlotMetadata(character=[], costume="", plot="s", tags=[])
        t.set_plot_metadata(md, "a.json")
        self.assertIs(t.plot_metadata_map["a.json"], md)


class InitMultiRoundTests(unittest.TestCase):
    """回归：multi_round_max_history 的解析必须允许 0（不裁剪）。"""

    class _MockConfig:
        def __init__(self, values):
            self._values = values

        def getKey(self, key, default=None):
            return self._values.get(key, default)

    def _make(self, multi_round_value):
        config = self._MockConfig({
            "gpt.enhance_jailbreak": False,
            "gpt.multiRoundMaxHistory": multi_round_value,
        })
        token_pool = SimpleNamespace(get_available_token=lambda: [])
        with patch.object(BaseTranslate, "__init__", lambda self, *a, **k: None), \
             patch.object(BaseTranslate, "init_chatbot", lambda self, *a, **k: None), \
             patch.object(ForGalJsonMulitChat, "_apply_internal_prompt_template_overrides",
                          lambda self: None), \
             patch.object(ForGalJsonMulitChat, "_set_temp_type", lambda self, *a, **k: None):
            t = ForGalJsonMulitChat(config, "eng", None, token_pool)
        return t

    def test_missing_key_defaults_to_no_trim(self):
        t = self._make(None)
        self.assertEqual(t.multi_round_max_history, 0)

    def test_zero_means_no_trim(self):
        t = self._make(0)
        self.assertEqual(t.multi_round_max_history, 0)

    def test_zero_string_means_no_trim(self):
        t = self._make("0")
        self.assertEqual(t.multi_round_max_history, 0)

    def test_positive_int(self):
        t = self._make(3)
        self.assertEqual(t.multi_round_max_history, 3)

    def test_positive_string(self):
        t = self._make("5")
        self.assertEqual(t.multi_round_max_history, 5)

    def test_garbage_falls_back_to_no_trim(self):
        t = self._make("abc")
        self.assertEqual(t.multi_round_max_history, 0)


class TranslateIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def _make(self):
        return make_translator()

    def _fake_ask(self, proofread=False):
        async def fake(self, **kwargs):
            messages = kwargs.get("messages") or []
            content = ""
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "user":
                    content = m.get("content", "")
            pairs = re.findall(r'([a-z0-9]{3})\|\{"id":\s*(\d+)', content)
            lines = []
            for sig, idx in pairs:
                if proofread:
                    lines.append(sig + '|{"id": ' + str(idx) + ', "newdst": "校' + str(idx) + '"}')
                else:
                    lines.append(sig + '|{"id": ' + str(idx) + ', "dst": "译' + str(idx) + '"}')
            self._last_chatbot_was_stream = False
            return "\n".join(lines), make_token()
        return fake

    async def test_non_stream_success(self):
        t = self._make()
        t.ask_chatbot = MethodType(self._fake_ask(False), t)
        trans_list = [CSentense(f"原文{i}", index=i) for i in range(3)]
        cnt, res = await t.translate(trans_list, filename="f.json")
        self.assertEqual(cnt, 3)
        self.assertEqual(len(res), 3)
        self.assertEqual(trans_list[0].pre_dst, "译0")
        self.assertEqual(trans_list[2].pre_dst, "译2")

    async def test_stream_success(self):
        t = self._make()

        async def fake_stream(self, **kwargs):
            cb = kwargs.get("stream_line_callback")
            messages = kwargs.get("messages") or []
            content = ""
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "user":
                    content = m.get("content", "")
            pairs = re.findall(r'([a-z0-9]{3})\|\{"id":\s*(\d+)', content)
            self._last_chatbot_was_stream = True
            if cb:
                for sig, idx in pairs:
                    cb([sig + '|{"id": ' + str(idx) + ', "dst": "译' + str(idx) + '"}'], False)
                cb([""], True)
            return "\n".join(
                sig + '|{"id": ' + str(idx) + ', "dst": "译' + str(idx) + '"}'
                for sig, idx in pairs
            ), make_token()

        t.ask_chatbot = MethodType(fake_stream, t)
        trans_list = [CSentense(f"原文{i}", index=i) for i in range(3)]
        cnt, res = await t.translate(trans_list, filename="f.json")
        self.assertEqual(cnt, 3)
        self.assertEqual(trans_list[1].pre_dst, "译1")

    async def test_proofread_mode_uses_newdst(self):
        t = self._make()
        t.ask_chatbot = MethodType(self._fake_ask(True), t)
        trans_list = [CSentense(f"原文{i}", index=i) for i in range(2)]
        for tr in trans_list:
            tr.pre_dst = "旧译" + str(tr.index)
        cnt, res = await t.translate(trans_list, proofread=True, filename="f.json")
        self.assertEqual(cnt, 2)
        self.assertEqual(trans_list[0].pre_dst, "校0")
        self.assertEqual(trans_list[1].pre_dst, "校1")

    async def test_full_failure_fallback(self):
        t = self._make()

        async def fake_empty(self, **kwargs):
            self._last_chatbot_was_stream = False
            return "", make_token()

        t.ask_chatbot = MethodType(fake_empty, t)
        trans_list = [CSentense(f"原文{i}", index=i) for i in range(3)]
        cnt, res = await t.translate(trans_list, filename="f.json")
        # 整批失败后兜底：返回全部句子（含 (Failed) 标记）
        self.assertEqual(len(res), 3)
        self.assertTrue(all("(Failed)" in tr.pre_dst for tr in res))


class PlotMetadataFileIntegrationTests(unittest.TestCase):
    """PlotMetadata.json 的导入/排除/载入回归。"""

    def test_get_file_list_excludes_plotmetadata_json(self):
        from GalTransl.Utils import get_file_list
        import tempfile, os
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "PlotMetadata.json"), "w", encoding="utf-8") as f:
                f.write("{}")
            with open(os.path.join(d, "scene1.json"), "w", encoding="utf-8") as f:
                f.write("[]")
            fl = get_file_list(d)
            names = [os.path.basename(p) for p in fl]
            self.assertIn("scene1.json", names)
            self.assertNotIn("PlotMetadata.json", names)
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_load_plot_metadata_parses_new_schema(self):
        from GalTransl.Backend.ForGalJsonMulitChat import load_plot_metadata
        import tempfile, os, json as _json
        d = tempfile.mkdtemp()
        try:
            payload = {
                "id": "scene-01",
                "角色": ["爱丽丝", "波波"],
                "服装": "红裙",
                "剧情": "迷雾森林冒险",
                "标签": ["奇幻", "冒险"],
            }
            with open(os.path.join(d, "PlotMetadata.json"), "w", encoding="utf-8") as f:
                _json.dump(payload, f, ensure_ascii=False)
            cfg = SimpleNamespace(getInputPath=lambda: d)
            md = load_plot_metadata(cfg)
            self.assertIsNotNone(md)
            self.assertEqual(md.id, "scene-01")
            self.assertEqual(md.character, ["爱丽丝", "波波"])
            self.assertEqual(md.costume, "红裙")
            self.assertEqual(md.plot, "迷雾森林冒险")
            self.assertEqual(md.tags, ["奇幻", "冒险"])
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_load_plot_metadata_missing_file_returns_none(self):
        from GalTransl.Backend.ForGalJsonMulitChat import load_plot_metadata
        import tempfile, os
        d = tempfile.mkdtemp()
        try:
            cfg = SimpleNamespace(getInputPath=lambda: d)
            self.assertIsNone(load_plot_metadata(cfg))
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_load_plot_metadata_scalar_角色_and_剧情(self):
        from GalTransl.Backend.ForGalJsonMulitChat import load_plot_metadata
        import tempfile, os, json as _json
        d = tempfile.mkdtemp()
        try:
            payload = {"角色": "单人主角", "服装": "", "剧情": "单线", "标签": "冒险"}
            with open(os.path.join(d, "PlotMetadata.json"), "w", encoding="utf-8") as f:
                _json.dump(payload, f, ensure_ascii=False)
            cfg = SimpleNamespace(getInputPath=lambda: d)
            md = load_plot_metadata(cfg)
            self.assertEqual(md.character, ["单人主角"])
            self.assertEqual(md.tags, ["冒险"])
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_load_plot_metadata_non_dict_root_returns_none(self):
        from GalTransl.Backend.ForGalJsonMulitChat import load_plot_metadata
        import tempfile, os
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "PlotMetadata.json"), "w", encoding="utf-8") as f:
                f.write("[]")
            cfg = SimpleNamespace(getInputPath=lambda: d)
            self.assertIsNone(load_plot_metadata(cfg))
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_loaded_metadata_injected_into_first_round_block(self):
        # 端到端：PlotMetadata.json -> 用 chunk 的批次文件名 set_plot_metadata
        # -> 首轮 user 内容包含 <plot_metadata> 与角色/剧情。
        from GalTransl.Backend.ForGalJsonMulitChat import load_plot_metadata
        import tempfile, os, json as _json
        d = tempfile.mkdtemp()
        try:
            payload = {"id": "scene-01", "角色": ["爱丽丝"], "服装": "红裙", "剧情": "森林冒险", "标签": ["奇幻"]}
            with open(os.path.join(d, "PlotMetadata.json"), "w", encoding="utf-8") as f:
                _json.dump(payload, f, ensure_ascii=False)
            cfg = SimpleNamespace(getInputPath=lambda: d)
            md = load_plot_metadata(cfg)
            self.assertIsNotNone(md)
            t = make_translator()
            batch_file_name = "scene1_0"  # 与 doLLMTranslSingleChunk 中使用的批次文件名一致
            if hasattr(t, "set_plot_metadata"):
                t.set_plot_metadata(md, batch_file_name)
            content = t._build_round_user_content(
                conv=[{"role": "system", "content": t.system_prompt}],
                input_src='aaa|{"id":0,"src":"こんにちは"}',
                gptdict="",
                filename=batch_file_name,
                is_first_round=True,
            )
            self.assertIn("<plot_metadata>", content)
            self.assertIn("id: scene-01", content)
            self.assertIn("森林冒险", content)
            self.assertIn("爱丽丝", content)
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_load_plot_metadata_map_parses_array(self):
        # 真实数据格式：JSON 数组，每项 id 对应一个待翻译文件名
        from GalTransl.Backend.ForGalJsonMulitChat import load_plot_metadata_map
        import tempfile, os, json as _json
        d = tempfile.mkdtemp()
        try:
            payload = [
                {"id": "a.txt.json", "角色": ["甲"], "服装": "红", "剧情": "战斗", "标签": ["动作"]},
                {"id": "b.txt.json", "角色": ["乙"], "服装": "蓝", "剧情": "逃亡", "标签": ["悬疑"]},
            ]
            with open(os.path.join(d, "PlotMetadata.json"), "w", encoding="utf-8") as f:
                _json.dump(payload, f, ensure_ascii=False)
            cfg = SimpleNamespace(getInputPath=lambda: d)
            mp = load_plot_metadata_map(cfg)
            self.assertEqual(len(mp), 2)
            self.assertEqual(mp["a.txt.json"].character, ["甲"])
            self.assertEqual(mp["a.txt.json"].plot, "战斗")
            self.assertEqual(mp["b.txt.json"].costume, "蓝")
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_load_plot_metadata_map_finds_file_one_level_up(self):
        # 文件不在 gt_input 自身，而在其上层目录时也能定位
        from GalTransl.Backend.ForGalJsonMulitChat import load_plot_metadata_map
        import tempfile, os, json as _json
        base = tempfile.mkdtemp()
        gt = os.path.join(base, "gt_input")
        os.makedirs(gt)
        try:
            payload = [{"id": "x.txt.json", "角色": ["丙"], "服装": "", "剧情": "p", "标签": []}]
            with open(os.path.join(base, "PlotMetadata.json"), "w", encoding="utf-8") as f:
                _json.dump(payload, f, ensure_ascii=False)
            cfg = SimpleNamespace(getInputPath=lambda: gt)
            mp = load_plot_metadata_map(cfg)
            self.assertIn("x.txt.json", mp)
        finally:
            import shutil
            shutil.rmtree(base, ignore_errors=True)

    def test_load_plot_metadata_map_from_sample_project(self):
        # 以官方 sampleProject 为数据来源，验证「项目根目录」定位逻辑：
        # PlotMetadata.json 位于翻译项目根（gt_input 的父目录），与 gt_input 同级。
        from GalTransl.Backend.ForGalJsonMulitChat import (
            load_plot_metadata_map,
            PlotMetadata,
        )
        import os

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample_dir = os.path.join(repo_root, "sampleProject")
        self.assertTrue(os.path.isdir(sample_dir), f"找不到 sampleProject: {sample_dir}")
        # getInputPath 返回项目内的 gt_input 子目录（即使该目录尚不存在，
        # 定位逻辑只需其父目录=项目根来发现 PlotMetadata.json）
        gt_input = os.path.join(sample_dir, "gt_input")
        cfg = SimpleNamespace(getInputPath=lambda: gt_input)

        mp = load_plot_metadata_map(cfg)
        self.assertGreater(len(mp), 0, "未从 sampleProject 载入任何剧情元数据")
        self.assertIn("demo-scene-01", mp)

        md = mp["demo-scene-01"]
        self.assertIsInstance(md, PlotMetadata)
        self.assertEqual(md.id, "demo-scene-01")
        self.assertIn("爱丽丝", md.character)
        self.assertIn("红色连衣裙", md.costume)
        self.assertIn("迷雾森林", md.plot)
        self.assertIn("奇幻", md.tags)

        # 与注入逻辑联调：分批文件名 demo-scene-01_0 应匹配到 demo-scene-01
        t = make_translator()
        t.project_config = cfg
        t.plot_metadata_map = {}
        t._plot_metadata_loaded = False
        resolved = t._resolve_plot_metadata("demo-scene-01_0")
        self.assertIsInstance(resolved, PlotMetadata)
        self.assertEqual(resolved.id, "demo-scene-01")

        content = t._build_round_user_content(
            conv=[{"role": "system", "content": t.system_prompt}],
            input_src='aaa|{"id":0,"src":"こんにちは"}',
            gptdict="",
            filename="demo-scene-01_0",
            is_first_round=True,
        )
        self.assertIn("<plot_metadata>", content)
        self.assertIn("爱丽丝", content)
        self.assertIn("红色连衣裙", content)

    def test_resolve_plot_metadata_chunk_suffix_and_priority(self):
        # 分批文件名 ``file_0`` 应能匹配 ``file``；显式注入优先于自动载入
        from GalTransl.Backend.ForGalJsonMulitChat import PlotMetadata
        cfg = SimpleNamespace(getInputPath=lambda: "")
        t = make_translator()
        t.project_config = cfg
        t.plot_metadata_map = {}
        t._plot_metadata_by_file = {
            "a.txt.json": PlotMetadata(id="a.txt.json", character=["甲"], costume="红", plot="战斗", tags=[]),
        }
        t._plot_metadata_loaded = True
        # 分批后缀命中
        md = t._resolve_plot_metadata("a.txt.json_0")
        self.assertIsNotNone(md)
        self.assertEqual(md.id, "a.txt.json")
        # 显式注入优先
        explicit = PlotMetadata(id="EX", character=["z"], costume="", plot="", tags=[])
        t.set_plot_metadata(explicit, "a.txt.json_0")
        self.assertIs(t._resolve_plot_metadata("a.txt.json_0"), explicit)
        # 无匹配返回 None
        self.assertIsNone(t._resolve_plot_metadata("nope.txt.json"))


class RedundantNameFieldRegressionTests(unittest.TestCase):
    """回归：模型在 dst 后重复追加 name 等字段（畸形但本身合法的 jsonline）
    不应再污染译文（原 22 处 "name" 污染 bug，根因在 Utils.fix_quotes）。

    端到端跑后端解析链路（_handle_parse_result -> fix_quotes -> 逐行解析），
    验证 pre_dst 干净、成功计数正确。对应真实缓存文件 03_kar_hatu01.txt.json。
    """

    def setUp(self):
        self.t = make_translator()
        self.trans_list = [CSentense(f"原文{i}", index=i) for i in range(5)]
        _, self.sig_list, _, _ = self.t._build_input_jsonlines(
            self.trans_list, False, "f.json"
        )

    def _run(self, raw):
        cnt, res, real = self.t._handle_parse_result(
            raw_resp=raw, token=make_token(), trans_list=self.trans_list,
            n_symbol="", sig_list=self.sig_list, is_stream=False,
            stream_error_msg="", stream_cursor={"i": -1},
            stream_parsed_list=[], idx_tip="0~4", filename="f.json",
            proofread=False, call_messages=[], prefill_used=False,
        )
        return cnt, res, real

    def test_redundant_name_field_not_polluted(self):
        # 复刻真实 bug：index=3 的畸形行，dst 后重复 name 字段
        malformed = (
            self.sig_list[3]
            + '|{"id":3,"name":"創","src":"S","dst":"（偶尔像这样，也挺不错的……）", "name": "創"}'
        )
        lines = [
            self.sig_list[0] + '|{"id":0,"dst":"译0"}',
            self.sig_list[1] + '|{"id":1,"dst":"译1"}',
            self.sig_list[2] + '|{"id":2,"dst":"译2"}',
            malformed,
            self.sig_list[4] + '|{"id":4,"dst":"译4"}',
        ]
        cnt, res, real = self._run("\n".join(lines))
        self.assertEqual(cnt, 5)
        self.assertEqual(real, 5)
        self.assertEqual(len(res), 5)
        # 关键断言：dst 未被吞入后续字段，译文保持干净
        self.assertEqual(res[3].pre_dst, "（偶尔像这样，也挺不错的……）")
        self.assertNotIn("name", res[3].pre_dst)
        self.assertNotIn("創", res[3].pre_dst)
        # 旧 bug 的污染形态（弯引号 + 拼接的 name 片段）绝不可出现
        self.assertNotIn("“", res[3].pre_dst)
        self.assertNotIn("”", res[3].pre_dst)

    def test_multiple_redundant_name_fields_all_clean(self):
        # 模拟「22 处」同类错误：多行都带冗余 name 字段，全部应干净解析
        malformed_indices = [1, 3, 4]
        lines = []
        for i in range(5):
            if i in malformed_indices:
                lines.append(
                    self.sig_list[i]
                    + f'|{{"id":{i},"name":"X","src":"S","dst":"译{i}", "name": "X"}}'
                )
            else:
                lines.append(self.sig_list[i] + f'|{{"id":{i},"dst":"译{i}"}}')
        cnt, res, real = self._run("\n".join(lines))
        self.assertEqual(cnt, 5)
        self.assertEqual(real, 5)
        self.assertEqual(len(res), 5)
        for i in malformed_indices:
            self.assertEqual(res[i].pre_dst, f"译{i}")
            self.assertNotIn("name", res[i].pre_dst)

    def test_unescaped_quotes_in_dst_still_repaired_end_to_end(self):
        # ② 的端到端验证：dst 内未转义引号（真正解析失败）仍被修复为弯引号，
        # 而不是被错误吞字段或静默污染。
        broken = self.sig_list[0] + '|{"id":0,"dst":"他说"你好"然后离开了"}'
        cnt, res, real = self._run(broken)
        self.assertEqual(cnt, 1)
        self.assertEqual(real, 1)
        self.assertIn("“你好”", res[0].pre_dst)
        self.assertNotIn('"你好"', res[0].pre_dst)


class TranslationPromptTemplateTests(unittest.TestCase):
    """回归：多轮对话模式提示词已按 Ciallo 格式改造
    （中文 process_requirements、完整示例、控制码/注音规则、<history_result> 占位、占位符顺序）。"""

    @classmethod
    def setUpClass(cls):
        from GalTransl.Backend.Prompts import FORGAL_JSON_TRANS_PROMPT
        cls.T = FORGAL_JSON_TRANS_PROMPT

    def test_process_requirements_in_chinese(self):
        self.assertIn("输入格式", self.T)
        self.assertIn("src 字段判定", self.T)
        self.assertIn("符号与格式保留", self.T)
        self.assertIn("输出格式", self.T)

    def test_contains_complete_example(self):
        self.assertIn("完整示例", self.T)
        self.assertIn("#01|", self.T)
        self.assertIn("%p-1;", self.T)
        self.assertIn("%fuser;", self.T)

    def test_control_code_and_phonetic_rules(self):
        # 控制码原样保留
        self.assertIn("%p-1;", self.T)
        self.assertIn("%p;", self.T)
        self.assertIn("%fＭＳ ゴシック;", self.T)
        self.assertIn("%fuser;", self.T)
        # [] 内注音可直接删除
        self.assertIn("注音", self.T)

    def test_history_result_placeholder_present(self):
        self.assertIn("<history_result>", self.T)
        self.assertIn("[history_result]", self.T)

    def test_placeholder_ordering(self):
        # 翻译规范 -> 术语表 -> 剧情元数据 -> 输入
        gi = self.T.index("[translation_guideline]")
        gl = self.T.index("[Glossary]")
        pm = self.T.index("[plot_metadata]")
        ip = self.T.index("[Input]")
        self.assertLess(gi, gl)
        self.assertLess(gl, pm)
        self.assertLess(pm, ip)
        # 历史上下文位于输入之前
        self.assertLess(self.T.index("[history_result]"), ip)

    def test_recipe_line_present(self):
        self.assertIn("输出配方", self.T)
        self.assertIn('"dst": string', self.T)


if __name__ == "__main__":
    unittest.main()
