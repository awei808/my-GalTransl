"""构建输出对话引号（「」/『』）恢复回归：直接调用 _build_project_output 核实。

背景：构建输出（build-output 端点）此前只读 pre_dst/proofread_dst（无引号），
导致含 name 的对话句输出缺引号；修复后应恢复 post_dst_preview（含引号）与
原句引号补回逻辑。
"""
import json
import os
import shutil
import unittest

from GalTransl import server


WORKSPACE = os.path.join(os.path.dirname(__file__), "..", "_build_dialogue_work")


def _mk_proj(prefix: str):
    pdir = os.path.join(WORKSPACE, prefix)
    if os.path.exists(pdir):
        shutil.rmtree(pdir)
    os.makedirs(os.path.join(pdir, "gt_input"))
    os.makedirs(os.path.join(pdir, "gt_output"))
    os.makedirs(os.path.join(pdir, "transl_cache"))
    return pdir


def _write_json(path, data):
    import orjson
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))


class ExtractDialogueSymbolsTests(unittest.TestCase):
    """_extract_dialogue_symbols 纯函数测试（与 CSentense.analyse_dialogue 剥离规则一致）。"""

    def test_single_pair(self) -> None:
        self.assertEqual(server._extract_dialogue_symbols("「こんにちは」"), ("「", "」"))

    def test_kakko_pair(self) -> None:
        self.assertEqual(server._extract_dialogue_symbols("『やあ』"), ("『", "』"))

    def test_nested_pairs(self) -> None:
        self.assertEqual(server._extract_dialogue_symbols("「「nested」」"), ("「「", "」」"))

    def test_mixed_pairs(self) -> None:
        self.assertEqual(server._extract_dialogue_symbols("「『内』」"), ("「『", "』」"))

    def test_no_symbols(self) -> None:
        self.assertEqual(server._extract_dialogue_symbols("ただの独白。"), ("", ""))

    def test_mismatched_symbols(self) -> None:
        # 首尾不成对（「 开头但 ) 结尾）：不剥离
        self.assertEqual(server._extract_dialogue_symbols("「test)"), ("", ""))

    def test_empty(self) -> None:
        self.assertEqual(server._extract_dialogue_symbols(""), ("", ""))


class BuildOutputDialogueSymbolTests(unittest.TestCase):
    def setUp(self) -> None:
        if not os.path.exists(WORKSPACE):
            os.makedirs(WORKSPACE, exist_ok=True)
        self.pdir = _mk_proj("test_" + self._testMethodName)

    def tearDown(self) -> None:
        if os.path.exists(self.pdir):
            shutil.rmtree(self.pdir, ignore_errors=True)

    def test_dialogue_symbols_should_be_restored_in_output(self) -> None:
        """
        旧分支行为：缓存里 pre_dst 不含引号，但输出 message 应补回 「」。
        因为输入 JSON 的 message 带 「」，输出译文也应带匹配的 「」。
        """
        _write_json(
            os.path.join(self.pdir, "gt_input", "book.json"),
            [
                {"name": "華恋", "message": "「こんにちは」"},       # 对话，带「」
                {"message": "これは独白である。"},                     # 独白，无引号
                {"name": "凛音", "message": "『やあ』"},             # 对话，带『』
                {"name": "華恋", "message": "「「nested」」"},       # 嵌套「」
            ],
        )
        _write_json(
            os.path.join(self.pdir, "transl_cache", "book.json"),
            [
                # pre_src 带引号，post_src 已剥去引号；pre_dst 是译文(无引号)
                {"index": 1, "name": "華恋",
                 "pre_src": "「こんにちは」", "post_src": "こんにちは",
                 "pre_dst": "你好呀", "proofread_dst": ""},
                {"index": 2, "name": "",
                 "pre_src": "これは独白である。", "post_src": "これは独白である。",
                 "pre_dst": "这是独白。", "proofread_dst": ""},
                {"index": 3, "name": "凛音",
                 "pre_src": "『やあ』", "post_src": "やあ",
                 "pre_dst": "嗨", "proofread_dst": ""},
                {"index": 4, "name": "華恋",
                 "pre_src": "「「nested」」", "post_src": "nested",
                 "pre_dst": "嵌套", "proofread_dst": ""},
            ],
        )
        server._build_project_output(self.pdir)
        with open(os.path.join(self.pdir, "gt_output", "book.json"), encoding="utf-8") as f:
            out = json.load(f)

        # 期望：输出应补回引号
        self.assertEqual(out[0]["message"], "「你好呀」",
                         "对话引号「」应补回到输出译文")
        self.assertEqual(out[0]["name"], "華恋")
        self.assertEqual(out[1]["message"], "这是独白。",
                         "独白（无引号）不应添加引号")
        self.assertEqual(out[2]["message"], "『嗨』",
                         "『』应补回到输出译文")
        self.assertEqual(out[3]["message"], "「「嵌套」」",
                         "嵌套引号应完整补回")


if __name__ == "__main__":
    unittest.main()
