"""构建输出 name 字段回归：直接调用 _build_project_output 核实。

覆盖：name 字段保留并应用替换表、proofread_dst 优先、对话引号恢复、
names 列表字段替换、独白（无 name）不受影响。
"""
import json
import os
import shutil
import unittest

from GalTransl import server

WORKSPACE = os.path.join(os.path.dirname(__file__), "..", "_build_output_name_work")


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


def _write_name_table(pdir: str, rows: list):
    with open(os.path.join(pdir, "name替换表.csv"), "w", encoding="utf-8-sig", newline="") as f:
        f.write("SRC_Name,DST_Name,Count\n")
        for src, dst in rows:
            f.write(f"{src},{dst},1\n")


class BuildOutputNameFieldDirectTests(unittest.TestCase):
    def setUp(self) -> None:
        if not os.path.exists(WORKSPACE):
            os.makedirs(WORKSPACE, exist_ok=True)
        self.pdir = _mk_proj("test_" + self._testMethodName)

    def tearDown(self) -> None:
        if os.path.exists(self.pdir):
            shutil.rmtree(self.pdir, ignore_errors=True)

    def test_name_preserved_message_translated_and_quote_restored(self) -> None:
        # 对话句：name 保留、message 替换为译文、对话引号「」补回
        _write_json(
            os.path.join(self.pdir, "gt_input", "book.json"),
            [
                {"name": "華恋", "message": "「こんにちは」"},
                {"message": "旁白。"},
                {"name": "凛音", "message": "「ありがとう」"},
            ],
        )
        _write_json(
            os.path.join(self.pdir, "transl_cache", "book.json"),
            [
                {"index": 1, "name": "華恋", "pre_src": "「こんにちは」",
                 "post_src": "こんにちは", "pre_dst": "你好呀",
                 "post_dst_preview": "「你好呀」"},
                {"index": 2, "name": "", "pre_src": "旁白。",
                 "post_src": "旁白。", "pre_dst": "旁白。"},
                {"index": 3, "name": "凛音", "pre_src": "「ありがとう」",
                 "post_src": "ありがとう", "pre_dst": "谢谢",
                 "post_dst_preview": "「谢谢」"},
            ],
        )
        result = server._build_project_output(self.pdir)
        self.assertTrue(result["success"], result)
        with open(os.path.join(self.pdir, "gt_output", "book.json"), encoding="utf-8") as f:
            out = json.load(f)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0], {"name": "華恋", "message": "「你好呀」"})
        self.assertEqual(out[1], {"message": "旁白。"})
        self.assertEqual(out[2], {"name": "凛音", "message": "「谢谢」"})

    def test_proofread_dst_takes_priority(self) -> None:
        # proofread_dst 优先，且需补对话引号
        _write_json(
            os.path.join(self.pdir, "gt_input", "book.json"),
            [{"name": "華恋", "message": "「こんにちは」"}],
        )
        _write_json(
            os.path.join(self.pdir, "transl_cache", "book.json"),
            [
                {
                    "index": 1,
                    "name": "華恋",
                    "pre_src": "「こんにちは」",
                    "post_src": "こんにちは",
                    "pre_dst": "初译",
                    "proofread_dst": "校对后译文",
                    "post_dst_preview": "「初译」",
                }
            ],
        )
        server._build_project_output(self.pdir)
        with open(os.path.join(self.pdir, "gt_output", "book.json"), encoding="utf-8") as f:
            out = json.load(f)
        self.assertEqual(out[0], {"name": "華恋", "message": "「校对后译文」"})

    def test_post_dst_preview_preferred_when_matches_pre_dst(self) -> None:
        # 无校对且 preview 剥引号后与 pre_dst 一致：post_dst_preview（含后处理）直接采用
        _write_json(
            os.path.join(self.pdir, "gt_input", "book.json"),
            [{"name": "華恋", "message": "「こんにちは」"}],
        )
        _write_json(
            os.path.join(self.pdir, "transl_cache", "book.json"),
            [
                {
                    "index": 1,
                    "name": "華恋",
                    "pre_src": "「こんにちは」",
                    "post_src": "こんにちは",
                    "pre_dst": "后处理译文",
                    "post_dst_preview": "「后处理译文」",
                }
            ],
        )
        server._build_project_output(self.pdir)
        with open(os.path.join(self.pdir, "gt_output", "book.json"), encoding="utf-8") as f:
            out = json.load(f)
        self.assertEqual(out[0], {"name": "華恋", "message": "「后处理译文」"})

    def test_stale_post_dst_preview_falls_back_to_edited_pre_dst(self) -> None:
        # 用户在校对页修改 pre_dst 后，preview 是过期快照（内文不一致）：
        # 构建输出必须用当前 pre_dst（补引号），不能丢用户校对修改
        _write_json(
            os.path.join(self.pdir, "gt_input", "book.json"),
            [{"name": "華恋", "message": "「こんにちは」"}],
        )
        _write_json(
            os.path.join(self.pdir, "transl_cache", "book.json"),
            [
                {
                    "index": 1,
                    "name": "華恋",
                    "pre_src": "「こんにちは」",
                    "post_src": "こんにちは",
                    "pre_dst": "用户修改后的译文",
                    "post_dst_preview": "「翻译时旧译文」",
                }
            ],
        )
        server._build_project_output(self.pdir)
        with open(os.path.join(self.pdir, "gt_output", "book.json"), encoding="utf-8") as f:
            out = json.load(f)
        self.assertEqual(out[0], {"name": "華恋", "message": "「用户修改后的译文」"})

    def test_preview_crlf_still_matches_lf_pre_dst(self) -> None:
        # 回归：pre_dst 恒为真实 \n，而 preview 经插件（text_common_normalfix 对 CRLF
        # 源整段改回 CRLF）后是 CRLF——新鲜度比较必须先归一换行，否则新快照被误判过期。
        # 这里直测纯函数 _pick_output_dst（端到端产物在两条分支下相同，无法区分）。
        picked, needs_quotes = server._pick_output_dst(
            {
                "pre_dst": "你好\n再见",
                "post_dst_preview": "「你好\r\n再见」",
            }
        )
        self.assertFalse(needs_quotes, "preview 应被采纳（needs_quotes=False）")
        self.assertEqual(picked, "「你好\r\n再见」")

    def test_stale_preview_still_rejected_after_newline_folding(self) -> None:
        # 归一化只是把换行形态抹平，内文真有差异时仍须判过期
        picked, needs_quotes = server._pick_output_dst(
            {
                "pre_dst": "用户改后的译文",
                "post_dst_preview": "「翻译时旧译文」",
            }
        )
        self.assertTrue(needs_quotes)
        self.assertEqual(picked, "用户改后的译文")

    def test_user_edit_to_literal_br_text_still_stale(self) -> None:
        # 若用户把真实换行改成字面 <br> 文本，这属于真实编辑：不得因换行归一而被判
        # 「未改动」（否则过期 preview 会覆盖用户改动）。故比较只归真实 CRLF，不折 <br>
        picked, needs_quotes = server._pick_output_dst(
            {
                "pre_dst": "甲<br>乙",
                "post_dst_preview": "「甲\n乙」",
            }
        )
        self.assertTrue(needs_quotes)
        self.assertEqual(picked, "甲<br>乙")

    def test_name_replacement_table_applied(self) -> None:
        # name 替换表：華恋→华恋、創→创
        _write_name_table(self.pdir, [("華恋", "华恋"), ("創", "创")])
        _write_json(
            os.path.join(self.pdir, "gt_input", "book.json"),
            [
                {"name": "創", "message": "「こんにちは」"},
                {"name": "華恋", "message": "「ありがとう」"},
            ],
        )
        _write_json(
            os.path.join(self.pdir, "transl_cache", "book.json"),
            [
                {"index": 1, "name": "創", "pre_src": "「こんにちは」",
                 "post_src": "こんにちは", "pre_dst": "你好",
                 "post_dst_preview": "「你好」"},
                {"index": 2, "name": "華恋", "pre_src": "「ありがとう」",
                 "post_src": "ありがとう", "pre_dst": "谢谢",
                 "post_dst_preview": "「谢谢」"},
            ],
        )
        server._build_project_output(self.pdir)
        with open(os.path.join(self.pdir, "gt_output", "book.json"), encoding="utf-8") as f:
            out = json.load(f)
        self.assertEqual(out[0]["name"], "创")
        self.assertEqual(out[1]["name"], "华恋")

    def test_names_list_field_replaced(self) -> None:
        # names 列表字段逐元素替换
        _write_name_table(self.pdir, [("A", "甲"), ("B", "乙")])
        _write_json(
            os.path.join(self.pdir, "gt_input", "book.json"),
            [{"names": ["A", "B"], "message": "hello"}],
        )
        _write_json(
            os.path.join(self.pdir, "transl_cache", "book.json"),
            [
                {"index": 1, "name": ["A", "B"], "pre_src": "hello",
                 "post_src": "hello", "pre_dst": "你好",
                 "post_dst_preview": "你好"},
            ],
        )
        server._build_project_output(self.pdir)
        with open(os.path.join(self.pdir, "gt_output", "book.json"), encoding="utf-8") as f:
            out = json.load(f)
        self.assertEqual(out[0]["names"], ["甲", "乙"])
        self.assertEqual(out[0]["message"], "你好")


if __name__ == "__main__":
    unittest.main()
