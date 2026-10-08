"""输出侧换行还原：桌面端构建（server_cache）与 CLI/流水线输出（CSerialize）口径一致。"""

import json
import os
import tempfile
import unittest

from GalTransl.CSentense import CSentense
from GalTransl.CSerialize import save_transList_to_json_cn, update_json_with_transList
from GalTransl.Utils import get_n_symbol, restore_output_line_breaks


class RestoreOutputLineBreaksTests(unittest.TestCase):
    def test_real_lf_output_restored_to_source_crlf(self) -> None:
        # 缓存约定：译文一律真实 \n；原文为 CRLF 时输出应还原为 CRLF
        self.assertEqual(
            restore_output_line_breaks("行一\n行二", "原文\r\n原文"),
            "行一\r\n行二",
        )

    def test_real_lf_output_kept_when_source_is_lf(self) -> None:
        self.assertEqual(
            restore_output_line_breaks("行一\n行二", "原文\n原文"),
            "行一\n行二",
        )

    def test_real_lf_output_restored_to_source_literal(self) -> None:
        # 原文用字面 \n（两字符）时，输出应为字面形式
        self.assertEqual(
            restore_output_line_breaks("行一\n行二", "原文\\n原文"),
            "行一\\n行二",
        )

    def test_source_without_newline_keeps_dst_untouched(self) -> None:
        # 原文无换行：不凭空改写（哪怕译文带换行，交由问题检测处理）
        self.assertEqual(
            restore_output_line_breaks("行一\n行二", "原文没有换行"),
            "行一\n行二",
        )

    def test_source_with_literal_br_convention_is_out_of_scope(self) -> None:
        # 已知边界（既定范围）：源以字面 <br> 当换行符时，get_n_symbol 认不出，
        # 译文的真实 \n 会原样落盘（不再还原成 <br>）。本用例把该取舍固化为契约，
        # 若将来决定支持该约定，需同时改 get_n_symbol 与 Problem._newline_count。
        self.assertEqual(
            restore_output_line_breaks("甲\n乙", "甲<br>乙"),
            "甲\n乙",
        )

    def test_crlf_in_dst_normalized_before_restore(self) -> None:
        # 译文侧若残留 CRLF，先归一为 \n 再还原，避免出现 \r\r\n
        self.assertEqual(
            restore_output_line_breaks("行一\r\n行二", "原文\r\n原文"),
            "行一\r\n行二",
        )
        self.assertEqual(
            restore_output_line_breaks("行一\r\n行二", "原文\n原文"),
            "行一\n行二",
        )

    def test_idempotent(self) -> None:
        once = restore_output_line_breaks("行一\n行二", "原文\r\n原文")
        self.assertEqual(restore_output_line_breaks(once, "原文\r\n原文"), once)

    def test_get_n_symbol_priority_unchanged(self) -> None:
        # 共享函数依赖的符号优先级保持不变（真实 CRLF > 真实 LF > 字面 CRLF > 字面 LF）
        self.assertEqual(get_n_symbol("a\r\nb\nc")[0], "\r\n")
        self.assertEqual(get_n_symbol("a\nb")[0], "\n")
        self.assertEqual(get_n_symbol("a\\r\\nb")[0], "\\r\\n")
        self.assertEqual(get_n_symbol("a\\nb")[0], "\\n")
        self.assertEqual(get_n_symbol("ab"), [])


class CliOutputMatchesBuildOutputTests(unittest.TestCase):
    """两条导出路径对同一缓存内容必须给出相同换行结果。"""

    def _tran(self, pre_src: str, post_dst: str) -> CSentense:
        tran = CSentense(pre_src, index=1)
        tran.pre_dst = post_dst
        tran.post_dst = post_dst
        return tran

    def test_update_json_with_transList_restores_line_breaks(self) -> None:
        tran = self._tran("原文\r\n原文", "行一\n行二")
        old_json = [{"message": "原文\r\n原文"}]
        out = update_json_with_transList([tran], old_json)
        self.assertEqual(out[0]["message"], "行一\r\n行二")

    def test_update_json_with_transList_literal_source(self) -> None:
        tran = self._tran("原文\\n原文", "行一\n行二")
        out = update_json_with_transList([tran], [{"message": "原文\\n原文"}])
        self.assertEqual(out[0]["message"], "行一\\n行二")

    def test_save_transList_to_json_cn_restores_line_breaks(self) -> None:
        tran = self._tran("原文\r\n原文", "行一\n行二")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.json")
            save_transList_to_json_cn([tran], path)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        self.assertEqual(data[0]["message"], "行一\r\n行二")

    def test_cli_and_build_share_same_result(self) -> None:
        # 构建输出侧调用的是同一函数，这里直接比对两条路径的产物
        for src, dst in (
            ("原文\r\n原文", "甲\n乙"),
            ("原文\n原文", "甲\r\n乙"),
            ("原文\\n原文", "甲\n乙"),
            ("原文没有换行", "甲\n乙"),
        ):
            tran = self._tran(src, dst)
            cli = update_json_with_transList([tran], [{"message": src}])[0]["message"]
            build = restore_output_line_breaks(dst, src)
            self.assertEqual(cli, build, msg=f"src={src!r} dst={dst!r}")


if __name__ == "__main__":
    unittest.main()
