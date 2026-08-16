# -*- coding: utf-8 -*-
"""ForJPResidue 残留日文修复解析逻辑的单元测试。

覆盖（基类 BaseSparseFixRound._parse_fix_response / _record_round_runtime_error）：
  - decode_json_line_part（utils）：尾随垃圾（如 </br>、；）不丢句
  - _parse_fix_response：返回 (success_count, found_count)；无 better 字段时 found=0
  - 多行拼接输入按 id 稀疏解析（id 不连续、顺序无关）
  - better 与当前译文相同仍跳过、不计 success 的行为
  - 输入携带 src、不携带 problem 的口径（解析仅按 id 定位，与 problem 无关）
  - _record_round_runtime_error 以正确签名调用 record_runtime_error
"""
import unittest

from GalTransl.CSentense import CSentense
from GalTransl.Backend.ForJPResidue import ForJPResidue
from GalTransl.Backend.utils import decode_json_line_part


class _FakeOpencc:
    """身份转换替身：_normalize_parsed_translation_text 在中文目标语言下调用 convert。"""

    @staticmethod
    def convert(text):
        return text


class _FakePjConfig:
    """配置替身：_parse_fix_response 仅用 getKey 读取 swap 开关，默认关闭。

    与生产默认行为一致（未配置 gpt.swapFixToCurrent 时不交换），测试只验证
    alt_dst 写入，不验证交换路径，故返回默认 False 即可。

    额外提供 runtime_project_dir / getProjectDir / getKey，供 _record_round_runtime_error
    构造运行态上报的 project_dir 使用（与 ForBRStation 同口径）。
    """

    def __init__(self, project_dir: str = "/fake/project"):
        self.runtime_project_dir = project_dir

    def getKey(self, key, default=None):
        return default

    def getProjectDir(self):
        return self.runtime_project_dir


def _make_parser(target_lang: str = "Chinese (Simplified)"):
    """绕过重型 __init__，仅装配 _parse_fix_response 所需属性。"""
    obj = object.__new__(ForJPResidue)
    obj.target_lang = target_lang
    obj.opencc = _FakeOpencc()
    obj.pj_config = _FakePjConfig()
    return obj


def _trans(index, pre_dst, src=None):
    t = CSentense(f"src{index}", index=index)
    t.post_src = src if src is not None else f"src{index}"
    t.pre_dst = pre_dst
    t.proofread_zh = ""
    return t


class DecodeJsonPartTests(unittest.TestCase):
    def test_clean_json(self):
        self.assertEqual(
            decode_json_line_part('{"id": 1, "better": "好"}'),
            {"id": 1, "better": "好"},
        )

    def test_trailing_br_garbage(self):
        # 模型在 JSON 后误加 </br>，raw_decode 应忽略尾随垃圾
        self.assertEqual(
            decode_json_line_part('{"id": 94, "better": "abc"}</br>'),
            {"id": 94, "better": "abc"},
        )

    def test_trailing_punctuation(self):
        self.assertEqual(
            decode_json_line_part('{"id": 1, "better": "x"}；'),
            {"id": 1, "better": "x"},
        )

    def test_leading_garbage(self):
        # 从首个 { 开始解析，忽略前置思考文字
        self.assertEqual(
            decode_json_line_part('思考：{"id": 1, "better": "y"}'),
            {"id": 1, "better": "y"},
        )

    def test_brace_inside_string_kept(self):
        # better 值内含 } 时不得被错误截断
        s = '{"id": 1, "better": "说}话"}'
        self.assertEqual(decode_json_line_part(s), {"id": 1, "better": "说}话"})

    def test_not_json_returns_none(self):
        self.assertIsNone(decode_json_line_part("纯文本没有 JSON"))
        self.assertIsNone(decode_json_line_part(""))


class ParseJpJsonlineTests(unittest.TestCase):
    def test_trailing_br_garbage_fix_applied(self):
        # 残留日文修复：better 尾随 </br> 仍应写入 alt_dst
        trans = _trans(94, "她像ノシ一样笑了。", src="彼女はノシと笑った")
        parser = _make_parser()
        line = 'p4c|{"id": 94, "better": "她像诺西一样笑了。"}</br>'
        success, found = parser._parse_fix_response(line, [trans], "<br>")
        self.assertEqual(found, 1)
        self.assertEqual(success, 1)
        self.assertEqual(trans.alt_dst, "她像诺西一样笑了。")

    def test_multiple_jsonlines_sparse_by_id(self):
        # 多行合并后按 id 稀疏解析；id 不连续、顺序无关
        trans_list = [_trans(3, "a"), _trans(12, "b"), _trans(14, "c")]
        parser = _make_parser()
        text = "\n".join(
            [
                'tma|{"id": 3, "better": "a1"}',
                '1mj|{"id": 12, "better": "b1"}',
                'tc7|{"id": 14, "better": "c1"}',
            ]
        )
        success, found = parser._parse_fix_response(text, trans_list, "<br>")
        self.assertEqual(found, 3)
        self.assertEqual(success, 3)
        self.assertEqual(trans_list[0].alt_dst, "a1")
        self.assertEqual(trans_list[1].alt_dst, "b1")
        self.assertEqual(trans_list[2].alt_dst, "c1")

    def test_missing_better_field_yields_zero_found(self):
        # 模型回显 {id, dst}（无 better）→ found_count=0，供上报判断
        trans = _trans(1, "旧译文")
        parser = _make_parser()
        text = 'nnk|{"id": 1, "dst": "旧译文"}'
        success, found = parser._parse_fix_response(text, [trans], "<br>")
        self.assertEqual(found, 0)
        self.assertEqual(success, 0)
        self.assertEqual(trans.alt_dst, "")

    def test_better_identical_to_current_skipped_but_found(self):
        # better 与当前译文相同：found_count 仍计，success 为 0
        trans = _trans(3, "读书。", src="本を読む")
        parser = _make_parser()
        text = 'tma|{"id": 3, "better": "读书。"}'
        success, found = parser._parse_fix_response(text, [trans], "<br>")
        self.assertEqual(found, 1)
        self.assertEqual(success, 0)
        self.assertEqual(trans.alt_dst, "")

    def test_no_problem_key_in_input_not_required(self):
        # 解析仅按 id 定位；输入虽不携带 problem，但只要 id 命中即修复
        trans = _trans(7, "魔法を使う", src="魔法を使う")
        parser = _make_parser()
        text = 'abc|{"id": 7, "better": "使用魔法"}'
        success, found = parser._parse_fix_response(text, [trans], "<br>")
        self.assertEqual(found, 1)
        self.assertEqual(success, 1)
        self.assertEqual(trans.alt_dst, "使用魔法")


class RecordRuntimeErrorSignatureTests(unittest.TestCase):
    """_record_round_runtime_error 必须以正确签名调用 record_runtime_error。

    历史遗留：旧实现缺必填的 project_dir 位置参、缺 kind 关键字参，并传了
    不存在的 engine/stage/batch_index，导致 TypeError 被静默吞、上报失效。
    本类验证对齐正确签名（含 project_dir/kind/index_range/level，且不传
    engine/stage/batch_index）。ForBanWordFix 继承同一方法，一并覆盖。
    """

    def _make_reporter(self, project_dir: str = "/fake/project"):
        # 轻量装配：仅注入 _record_round_runtime_error 所需属性
        obj = object.__new__(ForJPResidue)
        obj.pj_config = _FakePjConfig(project_dir)
        obj.get_last_chatbot_model = lambda: "fake-model"
        return obj

    def test_signature_matches_runtime_contract(self):
        captured = {}

        def _fake_record(project_dir, *, kind, message, filename="", index_range="",
                          retry_count=None, model="", sleep_seconds=None, level="error"):
            captured.update(
                project_dir=project_dir, kind=kind, message=message, filename=filename,
                index_range=index_range, model=model, level=level,
            )

        import GalTransl.server as srv_mod

        original = srv_mod.record_runtime_error
        srv_mod.record_runtime_error = _fake_record
        try:
            reporter = self._make_reporter("/proj/a")
            reporter._record_round_runtime_error("f.json", "1~3", "msg", None)
        finally:
            srv_mod.record_runtime_error = original

        self.assertEqual(captured["project_dir"], "/proj/a")
        self.assertEqual(captured["kind"], "parse")
        self.assertEqual(captured["filename"], "f.json")
        self.assertEqual(captured["index_range"], "1~3")
        # model 调用点为 None，应回退到 get_last_chatbot_model()
        self.assertEqual(captured["model"], "fake-model")
        self.assertEqual(captured["level"], "warning")
        # 不得传入不存在的关键字参（签名不含这些形参，传则 TypeError）
        self.assertNotIn("engine", captured)
        self.assertNotIn("stage", captured)
        self.assertNotIn("batch_index", captured)

    def test_ban_word_fix_inherits_fixed_signature(self):
        # ForBanWordFix 继承 _record_round_runtime_error，同样必须签名正确
        from GalTransl.Backend.ForBanWordFix import ForBanWordFix

        captured = {}

        def _fake_record(project_dir, *, kind, message, filename="", index_range="",
                          retry_count=None, model="", sleep_seconds=None, level="error"):
            captured.update(project_dir=project_dir, kind=kind)

        import GalTransl.server as srv_mod

        original = srv_mod.record_runtime_error
        srv_mod.record_runtime_error = _fake_record
        try:
            obj = object.__new__(ForBanWordFix)
            obj.pj_config = _FakePjConfig("/proj/b")
            obj.get_last_chatbot_model = lambda: "fake-model-2"
            obj._record_round_runtime_error("g.json", "4~6", "msg", None)
        finally:
            srv_mod.record_runtime_error = original

        self.assertEqual(captured["project_dir"], "/proj/b")
        self.assertEqual(captured["kind"], "parse")


class FilterProblemAliasTests(unittest.TestCase):
    """_filter_problem_by_types 别名兜底：旧名 h场景用词不当 应被 用词不当 命中。

    find_problems 产出前缀统一为「用词不当：」，但旧配置 CProblemType 别名
    h场景用词不当 的 .name 为「h场景用词不当」。若 allowed 用该 .name 构建，
    必须靠别名兜底命中，避免禁用词修复静默跳过整个文件。
    """

    def test_h_alias_matched_by_new_name(self):
        kept = ForJPResidue._filter_problem_by_types(
            "h场景用词不当：攀上顶峰", [__import__("GalTransl.ConfigHelper", fromlist=["CProblemType"]).CProblemType.用词不当]
        )
        self.assertEqual(kept, "h场景用词不当：攀上顶峰")

    def test_plain_name_still_matches(self):
        # 常规口径不受影响
        kept = ForJPResidue._filter_problem_by_types(
            "用词不当：设备", [__import__("GalTransl.ConfigHelper", fromlist=["CProblemType"]).CProblemType.用词不当]
        )
        self.assertEqual(kept, "用词不当：设备")

    def test_unrelated_type_filtered_out(self):
        kept = ForJPResidue._filter_problem_by_types(
            "残留日文：の, 用词不当：设备",
            [__import__("GalTransl.ConfigHelper", fromlist=["CProblemType"]).CProblemType.用词不当],
        )
        self.assertEqual(kept, "用词不当：设备")


if __name__ == "__main__":
    unittest.main()
