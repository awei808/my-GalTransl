"""
改进轮（AI 备选译文）单元测试 —— 针对独立后端 ForImproveTranslation

覆盖：
  - 缓存序列化：CSentense.alt_dst <-> 缓存条目 alt_dst 的往返
  - _parse_fix_response（基类）：按 id 稀疏解析（better -> alt_dst）、乱行容错
  - _build_first_round_content：首轮含改进提示词/术语表/剧情元数据
  - batch_translate（翻译接口）集成：稀疏输出 / 无改进空输出 / 分批 / 解析失败跳过
"""

import asyncio
import os
import re
import tempfile
import unittest
from types import SimpleNamespace, MethodType

import orjson

from GalTransl.Backend.ForImproveTranslation import ForImproveTranslation
from GalTransl.Backend.ForGalJsonMulitChat import FileMetaData
from GalTransl.Cache import _build_cache_obj, get_transCache_from_json
from GalTransl.CSentense import CSentense


def make_token(model_name="test-model"):
    return SimpleNamespace(model_name=model_name, domain="https://example.com")


def make_translator():
    """通过 __new__ 创建实例并打桩改进轮所需的属性，避免触发重量级初始化。"""
    t = ForImproveTranslation.__new__(ForImproveTranslation)
    t.pj_config = SimpleNamespace(
        active_workers=0,
        stop_event=None,
        translation_guideline="",
        getProjectDir=lambda: "",
        getKey=lambda key, default=None: default,
    )
    t.eng_type = "ForImproveTranslation"
    t.enhance_jailbreak = False
    t.system_prompt = "SYSTEM_PROMPT"
    # 首轮 builder 使用 self.trans_prompt（模板可被用户 override），
    # mock 模板含「质量改进评估」标记以覆盖 test_first_round_injects_prompt_glossary_and_metadata 的断言
    t.trans_prompt = "质量改进评估\n[translation_guideline]\n[Glossary]\n[plot_metadata]\n[Input]"
    t.source_lang = "Japanese"
    t.target_lang = "Simplified Chinese"  # 中文目标语言，跳过英文单词检查
    t.conversations = {}
    t._force_first_round_files = set()
    t.file_metadata_map = {}
    t._file_metadata_by_file = {}
    t._file_metadata_loaded = False
    t.project_config = None
    t.multi_round_max_history = 0
    t.last_file_name = ""
    t._last_chatbot_was_stream = False
    t._last_chatbot_model_name = ""
    t.batch_metadata_map = {}
    t._batch_metadata_by_file = {}
    t._batch_metadata_loaded = False
    t._global_prompt = None
    t._global_prompt_loaded = False
    t.opencc = SimpleNamespace(convert=lambda s: s)
    return t


class CacheAltDstSerializationTests(unittest.TestCase):
    """CSentense.alt_dst 与缓存条目 alt_dst 的序列化。"""

    def test_alt_dst_written_when_non_empty(self) -> None:
        tran = CSentense("原文", index=0)
        tran.pre_dst = "译文"
        tran.alt_dst = "备选译文"
        obj = _build_cache_obj(tran, post_save=True)
        self.assertIsNotNone(obj)
        self.assertEqual(obj["alt_dst"], "备选译文")

    def test_alt_dst_omitted_when_empty(self) -> None:
        tran = CSentense("原文", index=0)
        tran.pre_dst = "译文"
        tran.alt_dst = ""
        obj = _build_cache_obj(tran, post_save=True)
        self.assertIsNotNone(obj)
        self.assertNotIn("alt_dst", obj)

    def test_alt_dst_omitted_when_equal_to_pre_dst(self) -> None:
        # 落盘防御：alt_dst 与初译相同不应写入缓存
        tran = CSentense("原文", index=0)
        tran.pre_dst = "译文"
        tran.alt_dst = "译文"
        obj = _build_cache_obj(tran, post_save=True)
        self.assertIsNotNone(obj)
        self.assertNotIn("alt_dst", obj)

    def test_alt_dst_omitted_when_equal_to_proofread_zh(self) -> None:
        # 落盘防御：alt_dst 与校对结果相同不应写入缓存
        tran = CSentense("原文", index=0)
        tran.pre_dst = "初译"
        tran.proofread_zh = "校对"
        tran.alt_dst = "校对"
        obj = _build_cache_obj(tran, post_save=True)
        self.assertIsNotNone(obj)
        self.assertNotIn("alt_dst", obj)

    async def _roundtrip(self, cache_obj: dict) -> CSentense:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f.json")
            with open(path, "wb") as f:
                f.write(orjson.dumps([cache_obj]))
            trans_list = [CSentense(cache_obj["pre_src"], index=cache_obj["index"])]
            await get_transCache_from_json(trans_list, path)
            return trans_list[0]

    def test_roundtrip_loads_alt_dst(self) -> None:
        tran = asyncio.run(
            self._roundtrip(
                {
                    "index": 1,
                    "name": "",
                    "pre_src": "原文",
                    "post_src": "原文",
                    "pre_dst": "译文",
                    "proofread_dst": "",
                    "alt_dst": "备选译文",
                }
            )
        )
        self.assertEqual(tran.alt_dst, "备选译文")
        self.assertEqual(tran.pre_dst, "译文")


class ParseImproveJsonlineTests(unittest.TestCase):
    """基类 _parse_fix_response：按 id 稀疏解析 better -> alt_dst。"""

    def setUp(self) -> None:
        self.t = make_translator()
        self.trans_list = [CSentense(f"原文{i}", index=i) for i in range(3)]
        for tr in self.trans_list:
            tr.pre_dst = f"译{tr.index}"

    def test_sparse_output_matches_by_id(self) -> None:
        result_text = (
            '{"id":0,"better":"备0"}\n'
            + '{"id":2,"better":"备2"}\n'
        )
        success, _found = self.t._parse_fix_response(
            result_text, self.trans_list, ""
        )
        self.assertEqual(success, 2)
        self.assertEqual(self.trans_list[0].alt_dst, "备0")
        self.assertEqual(self.trans_list[1].alt_dst, "")
        self.assertEqual(self.trans_list[2].alt_dst, "备2")
        self.assertEqual(self.trans_list[0].pre_dst, "译0")

    def test_sig_anchored_lines_accepted(self) -> None:
        _, sig_list, _, _ = self.t._build_input_jsonlines(
            self.trans_list, True, "f.json"
        )
        result_text = sig_list[1] + '|{"id":1,"better":"备1"}\n'
        success, _found = self.t._parse_fix_response(
            result_text, self.trans_list, ""
        )
        self.assertEqual(success, 1)
        self.assertEqual(self.trans_list[1].alt_dst, "备1")

    def test_garbage_lines_ignored(self) -> None:
        result_text = (
            'garbage-line\n'
            + 'abc|not-json\n'
            + '{"id":9,"better":"未知"}\n'
            + '{"id":0,"dst":"错误key"}\n'
            + '{"id":1,"better":"备1"}\n'
        )
        success, _found = self.t._parse_fix_response(
            result_text, self.trans_list, ""
        )
        self.assertEqual(success, 1)
        self.assertEqual(self.trans_list[0].alt_dst, "")
        self.assertEqual(self.trans_list[1].alt_dst, "备1")

    def test_empty_or_garbled_better_rejected(self) -> None:
        result_text = '{"id":1,"better":""}\n{"id":2,"better":"\ufffd\x8c"}\n'
        success, _found = self.t._parse_fix_response(
            result_text, self.trans_list, ""
        )
        self.assertEqual(success, 0)
        self.assertTrue(all(tr.alt_dst == "" for tr in self.trans_list))

    def test_better_equal_to_pre_dst_is_rejected(self) -> None:
        # 模型逐字回显初译：better 与 pre_dst 相同应被跳过
        self.trans_list[0].pre_dst = "译文A"
        result_text = '{"id":0,"better":"译文A"}\n{"id":1,"better":"备1"}\n'
        success, _found = self.t._parse_fix_response(
            result_text, self.trans_list, ""
        )
        self.assertEqual(success, 1)
        self.assertEqual(self.trans_list[0].alt_dst, "")
        self.assertEqual(self.trans_list[1].alt_dst, "备1")

    def test_better_equal_to_proofread_zh_is_rejected(self) -> None:
        # 已校对的句子：基准应为 proofread_zh，better 与之相同应跳过
        self.trans_list[0].pre_dst = "初译A"
        self.trans_list[0].proofread_zh = "校对A"
        result_text = '{"id":0,"better":"校对A"}\n{"id":1,"better":"备1"}\n'
        success, _found = self.t._parse_fix_response(
            result_text, self.trans_list, ""
        )
        self.assertEqual(success, 1)
        self.assertEqual(self.trans_list[0].alt_dst, "")
        self.assertEqual(self.trans_list[1].alt_dst, "备1")

    def test_better_differs_only_by_br_vs_newline_is_rejected(self) -> None:
        # 模型按 prompt 用 <br> 换行，pre_dst 用 \r\n：归一化后相同应跳过
        self.trans_list[0].pre_dst = "前\r\n后"
        result_text = '{"id":0,"better":"前<br>后"}\n{"id":1,"better":"备1"}\n'
        success, _found = self.t._parse_fix_response(
            result_text, self.trans_list, "\r\n"
        )
        self.assertEqual(success, 1)
        self.assertEqual(self.trans_list[0].alt_dst, "")
        self.assertEqual(self.trans_list[1].alt_dst, "备1")


class BuildImproveFirstRoundTests(unittest.TestCase):
    def test_first_round_injects_prompt_glossary_and_metadata(self) -> None:
        t = make_translator()
        t.file_metadata_map = {
            "f.json": FileMetaData(character=["创"], costume="", plot="一段剧情", tags=[])
        }
        trans = CSentense("原文", index=0)
        trans.pre_dst = "译文"
        _, _, _, input_src = t._build_input_jsonlines([trans], True, "f.json")
        content = t._build_first_round_content(input_src, "术语表", "f.json")
        self.assertIn("质量改进评估", content)
        self.assertIn("术语表", content)
        self.assertIn("角色: 创", content)
        self.assertIn("一段剧情", content)
        self.assertIn(input_src, content)
        # 改进轮不应残留历史块与翻译提示词占位
        self.assertNotIn("[history_result]", content)
        self.assertNotIn("[Input]", content)

    def test_first_round_omits_metadata_when_missing(self) -> None:
        t = make_translator()
        trans = CSentense("原文", index=0)
        trans.pre_dst = "译文"
        _, _, _, input_src = t._build_input_jsonlines([trans], True, "f.json")
        content = t._build_first_round_content(input_src, "", "f.json")
        self.assertNotIn("<plot_metadata>", content)


class BatchTranslateImproveTests(unittest.IsolatedAsyncioTestCase):
    def _make_better_ask(self, better_indices: set):
        """构造按输入 sig/id 回显 better 的假 ask_chatbot（非流式）。"""

        async def fake(self, **kwargs):
            messages = kwargs.get("messages") or []
            content = ""
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "user":
                    content = m.get("content", "")
            pairs = re.findall(r'([a-z0-9]{3})\|\{"id":\s*(\d+)', content)
            lines = []
            for sig, idx in pairs:
                if int(idx) in better_indices:
                    lines.append(sig + '|{"id": ' + str(idx) + ', "better": "备' + str(idx) + '"}')
            self._last_chatbot_was_stream = False
            return "\n".join(lines), make_token()

        return fake

    def _translator_with_better(self, num_per_request=100):
        t = make_translator()
        t.pj_config.getKey = (
            lambda key, default=None: num_per_request
            if key == "gpt.numPerRequestBetter"
            else default
        )
        return t

    def _make_trans_list(self, n: int):
        trans_list = [CSentense(f"原文{i}", index=i) for i in range(n)]
        for tr in trans_list:
            tr.pre_dst = f"译{tr.index}"
        return trans_list

    async def test_sparse_output(self) -> None:
        t = self._translator_with_better()
        t.ask_chatbot = MethodType(self._make_better_ask({0, 2}), t)
        trans_list = self._make_trans_list(3)
        result = await t.batch_translate("f.json", "f.json", trans_list, 100)
        self.assertIs(result, trans_list)
        self.assertEqual(trans_list[0].alt_dst, "备0")
        self.assertEqual(trans_list[1].alt_dst, "")
        self.assertEqual(trans_list[2].alt_dst, "备2")
        # 正式译文与引擎标记不受影响
        self.assertEqual(trans_list[0].pre_dst, "译0")
        self.assertEqual(trans_list[0].trans_by, "")

    async def test_no_improvement_returns_unchanged(self) -> None:
        t = self._translator_with_better()
        t.ask_chatbot = MethodType(self._make_better_ask(set()), t)
        trans_list = self._make_trans_list(3)
        await t.batch_translate("f.json", "f.json", trans_list, 100)
        self.assertTrue(all(tr.alt_dst == "" for tr in trans_list))

    async def test_batch_splitting_respects_num_per_request(self) -> None:
        t = self._translator_with_better(num_per_request=2)
        t.ask_chatbot = MethodType(self._make_better_ask({0, 1, 2}), t)
        trans_list = self._make_trans_list(3)
        await t.batch_translate("f.json", "f.json", trans_list, 2)
        self.assertEqual([tr.alt_dst for tr in trans_list], ["备0", "备1", "备2"])
        conv = t.conversations.get("f.json", [])
        # system + 首轮 user/assistant + 续轮 user/assistant
        self.assertEqual(conv[0]["role"], "system")
        self.assertEqual(len(conv), 5)

    async def test_parse_failure_skips_batch_without_failed_marker(self) -> None:
        t = self._translator_with_better()

        async def fake_bad(self, **kwargs):
            self._last_chatbot_was_stream = False
            return "not-json-line", make_token()

        t.ask_chatbot = MethodType(fake_bad, t)
        trans_list = self._make_trans_list(2)
        await t.batch_translate("f.json", "f.json", trans_list, 100)
        self.assertTrue(all(tr.alt_dst == "" for tr in trans_list))
        self.assertEqual(trans_list[0].pre_dst, "译0")
        self.assertNotIn("(Failed)", trans_list[0].pre_dst)

    async def test_all_failed_sentences_filtered_out(self) -> None:
        t = self._translator_with_better()
        t.ask_chatbot = MethodType(self._make_better_ask({0}), t)
        trans_list = self._make_trans_list(2)
        trans_list[1].pre_dst = "(Failed)xxx"
        await t.batch_translate("f.json", "f.json", trans_list, 100)
        self.assertEqual(trans_list[0].alt_dst, "备0")
        self.assertEqual(trans_list[1].alt_dst, "")


class ProblemInjectTests(unittest.TestCase):
    """译文问题（problem）注入：类型过滤与 jsonline 注入。"""

    def test_filter_exact_type_match(self) -> None:
        t = make_translator()
        problem = "残留日文：痛い, 独白男他, 比日文长：1.5倍(20字符)"
        out = t._filter_problem_by_types(problem, ["残留日文", "独白男他"])
        self.assertEqual(out, "残留日文：痛い, 独白男他")

    def test_filter_avoids_prefix_false_positive(self) -> None:
        # 白名单「比日文长」不应误匹配「比日文长严格」
        t = make_translator()
        problem = "比日文长严格：1.0倍(5字符)"
        self.assertEqual(t._filter_problem_by_types(problem, ["比日文长"]), "")
        self.assertEqual(
            t._filter_problem_by_types(problem, ["比日文长严格"]),
            "比日文长严格：1.0倍(5字符)",
        )

    def test_filter_empty_problem_returns_empty(self) -> None:
        t = make_translator()
        self.assertEqual(t._filter_problem_by_types("", ["残留日文"]), "")

    def test_filter_aliases_for_inconsistent_types(self) -> None:
        # find_problems 生成的文本前缀与枚举名不一致的类别，应经别名兜底匹配
        t = make_translator()
        problem = "本无括号, 语言不通-非GBK：あ, GPT字典未使用：xxx---yyy, 残留日文：xx"
        out = t._filter_problem_by_types(
            problem, ["标点错漏", "语言不通", "字典使用"]
        )
        self.assertIn("本无括号", out)
        self.assertIn("语言不通-非GBK：あ", out)
        self.assertIn("GPT字典未使用：xxx---yyy", out)
        self.assertNotIn("残留日文：xx", out)

    def test_build_input_jsonlines_no_inject_when_none(self) -> None:
        t = make_translator()
        tr = CSentense("原文", index=0)
        tr.pre_dst = "译文"
        tr.problem = "残留日文：xx"
        _, _, _, input_src = t._build_input_jsonlines(
            [tr], True, "f.json", problem_types=None
        )
        self.assertNotIn("problem", input_src)

    def test_build_input_jsonlines_inject_all_when_empty_whitelist(self) -> None:
        t = make_translator()
        tr = CSentense("原文", index=0)
        tr.pre_dst = "译文"
        tr.problem = "残留日文：xx, 独白男他"
        _, _, _, input_src = t._build_input_jsonlines(
            [tr], True, "f.json", problem_types=[]
        )
        self.assertIn("problem", input_src)
        self.assertIn("残留日文：xx", input_src)
        self.assertIn("独白男他", input_src)

    def test_build_input_jsonlines_inject_by_whitelist(self) -> None:
        t = make_translator()
        from GalTransl.ConfigHelper import CProblemType

        tr = CSentense("原文", index=0)
        tr.pre_dst = "译文"
        tr.problem = "残留日文：xx, 独白男他"
        _, _, _, input_src = t._build_input_jsonlines(
            [tr], True, "f.json", problem_types=[CProblemType.残留日文]
        )
        self.assertIn("残留日文：xx", input_src)
        self.assertNotIn("独白男他", input_src)

    def test_build_input_jsonlines_replaces_line_break_in_dst(self) -> None:
        # 输入侧：dst 应与 src 采用同一换行表示（n_symbol -> <br>）
        t = make_translator()
        tr = CSentense("前\r\n后", index=0)
        tr.pre_dst = "译前\r\n译后"
        _, _, n_symbol, input_src = t._build_input_jsonlines([tr], True, "f.json")
        self.assertEqual(n_symbol, "\r\n")
        self.assertIn("译前<br>译后", input_src)
        self.assertNotIn("译前\\r\\n译后", input_src)

    def test_build_input_jsonlines_dst_uses_proofread_zh_with_replace(self) -> None:
        # 已校对句：基准取 proofread_zh，同样做换行替换
        t = make_translator()
        tr = CSentense("前\r\n后", index=0)
        tr.pre_dst = "初译前\r\n初译后"
        tr.proofread_zh = "校对前\r\n校对后"
        _, _, _, input_src = t._build_input_jsonlines([tr], True, "f.json")
        self.assertIn("校对前<br>校对后", input_src)
        self.assertNotIn("初译前", input_src)

    def test_build_input_jsonlines_no_replace_when_no_line_break(self) -> None:
        # n_symbol 为空（原文单行）时不得改动 dst
        t = make_translator()
        tr = CSentense("单行原文", index=0)
        tr.pre_dst = "单行译文"
        _, _, n_symbol, input_src = t._build_input_jsonlines([tr], True, "f.json")
        self.assertEqual(n_symbol, "")
        self.assertIn("单行译文", input_src)
        self.assertNotIn("<br>", input_src)

    def test_coerce_problem_type_list(self) -> None:
        t = make_translator()
        from GalTransl.ConfigHelper import CProblemType

        self.assertEqual(t._coerce_problem_type_list(None), [])
        self.assertEqual(
            t._coerce_problem_type_list("残留日文, 独白男他"),
            [CProblemType.残留日文, CProblemType.独白男他],
        )
        self.assertEqual(
            t._coerce_problem_type_list(["比日文长"]), [CProblemType.比日文长]
        )
        self.assertEqual(t._coerce_problem_type_list("不存在的类型"), [])


if __name__ == "__main__":
    unittest.main()
