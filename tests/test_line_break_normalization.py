import unittest

from GalTransl.Backend.BaseTranslate import BaseTranslate
from GalTransl.Backend.ForGalJsonMulitChat import (
    detect_line_break_symbol,
    detect_batch_line_break_symbol,
)


class _FakeTran:
    """最小替身：_normalize_parsed_translation_text 在校验换行符时仅依赖 post_src。"""

    def __init__(self, post_src: str = "测试"):
        self.post_src = post_src


def _make_normalizer(target_lang: str = "en") -> BaseTranslate:
    """绕过重型 __init__，仅装配 _normalize_parsed_translation_text 所需的属性。

    方法仅读取 self.target_lang / self.opencc 与传入的 current_tran.post_src，
    因此无需实例化整个翻译后端即可单测换行符还原逻辑。
    """
    obj = object.__new__(BaseTranslate)
    obj.target_lang = target_lang
    obj.opencc = None
    return obj


class LineBreakDetectionTests(unittest.TestCase):
    def test_detects_single_symbol(self):
        self.assertEqual(detect_line_break_symbol("a\\r\\nb"), "\\r\\n")
        self.assertEqual(detect_line_break_symbol("a\r\nb"), "\r\n")
        self.assertEqual(detect_line_break_symbol("a\\nb"), "\\n")
        self.assertEqual(detect_line_break_symbol("a\nb"), "\n")
        self.assertEqual(detect_line_break_symbol("ab"), "")

    def test_detection_priority(self):
        # 字面 \\r\\n 优先于真实 \\r\\n
        self.assertEqual(detect_line_break_symbol("x\\r\\ny\r\nz"), "\\r\\n")
        # 真实 \\r\\n 优先于字面 \\n
        self.assertEqual(detect_line_break_symbol("x\r\ny\\nz"), "\r\n")
        # 字面 \\n 优先于真实 \\n
        self.assertEqual(detect_line_break_symbol("x\\ny\nz"), "\\n")


class LineBreakRestoreTests(unittest.TestCase):
    def _restore(self, line_dst: str, n_symbol: str, post_src: str = "测试") -> str:
        return _make_normalizer()._normalize_parsed_translation_text(
            line_dst, _FakeTran(post_src), n_symbol
        )

    # ---- <br> / <BR> 占位符还原 ----
    def test_br_to_lf(self):
        self.assertEqual(self._restore("行一<br>行二", "\n"), "行一\n行二")

    def test_br_to_crlf(self):
        self.assertEqual(self._restore("行一<br>行二", "\r\n"), "行一\r\n行二")

    def test_upper_br_to_lf(self):
        self.assertEqual(self._restore("行一<BR>行二", "\n"), "行一\n行二")

    def test_upper_br_to_crlf(self):
        self.assertEqual(self._restore("行一<BR>行二", "\r\n"), "行一\r\n行二")

    # ---- 兜底：模型直接吐真实换行符（而非 <br> 占位符）----
    def test_real_lf_normalized_to_crlf(self):
        self.assertEqual(self._restore("行一\n行二", "\r\n"), "行一\r\n行二")

    def test_real_crlf_normalized_to_lf(self):
        self.assertEqual(self._restore("行一\r\n行二", "\n"), "行一\n行二")

    # ---- 制表符占位符 ----
    def test_tab_placeholder_always_restored(self):
        self.assertEqual(self._restore("行一[t]行二", "\n"), "行一\t行二")
        self.assertEqual(self._restore("行一[t]行二", "\r\n"), "行一\t行二")

    # ---- 无换行符标记：原样保留 ----
    def test_no_symbol_normalizes_variants_to_br(self):
        # n_symbol 为空（源以 <br> 为换行约定或不含换行）时：
        # <br> 原样保留；<BR> 与真实换行统一收口为 <br>（交换格式标准占位符），
        # 保证 LLM 输出即便偏离 <br> 也能规范回源约定。
        self.assertEqual(self._restore("行一<br>行二", ""), "行一<br>行二")
        self.assertEqual(self._restore("行一<BR>行二", ""), "行一<br>行二")
        self.assertEqual(self._restore("行一\n行二", ""), "行一<br>行二")
        self.assertEqual(self._restore("行一\r\n行二", ""), "行一<br>行二")

    # ---- 字面转义串形态（源文本本就是 \r\n / \n 字面文本）----
    def test_literal_crlf_symbol_restores_to_literal(self):
        self.assertEqual(self._restore("a<br>b", "\\r\\n"), "a\\r\\nb")

    def test_literal_lf_symbol_restores_to_literal(self):
        self.assertEqual(self._restore("a<br>b", "\\n"), "a\\nb")

    # ---- 兜底扩展：字面转义串形态的 n_symbol，模型误输真实换行也应归一化 ----
    def test_literal_crlf_symbol_normalizes_real_crlf_to_literal(self):
        self.assertEqual(self._restore("a\r\nb", "\\r\\n"), "a\\r\\nb")

    def test_literal_crlf_symbol_normalizes_real_lf_to_literal(self):
        self.assertEqual(self._restore("a\nb", "\\r\\n"), "a\\r\\nb")

    def test_literal_lf_symbol_normalizes_real_lf_to_literal(self):
        self.assertEqual(self._restore("a\nb", "\\n"), "a\\nb")

    def test_literal_lf_symbol_normalizes_real_crlf_to_literal(self):
        self.assertEqual(self._restore("a\r\nb", "\\n"), "a\\nb")

    # ---- 混合场景 ----
    def test_mixed_br_and_real_newline_all_to_crlf(self):
        self.assertEqual(self._restore("a<br>b\nc\r\nd", "\r\n"), "a\r\nb\r\nc\r\nd")

    def test_crlf_fallback_idempotent(self):
        # 已是正确的 CRLF 不应变成 \r\r\n
        self.assertEqual(self._restore("a\r\nb\r\nc", "\r\n"), "a\r\nb\r\nc")

    # ---- 编码→还原 契约（结构层面往返一致）----
    def test_roundtrip_contract(self):
        cases = [
            ("行一\n行二", "\n"),
            ("行一\r\n行二", "\r\n"),
            ("行一\\n行二", "\\n"),
            ("行一\\r\\n行二", "\\r\\n"),
            ("行一\t行二", "\n"),
            ("行一\t行二", "\r\n"),
        ]
        for src, n_symbol in cases:
            encoded = src.replace("\t", "[t]")
            if n_symbol:
                encoded = encoded.replace(n_symbol, "<br>")
            restored = self._restore(encoded, n_symbol)
            self.assertEqual(restored, src, msg=f"roundtrip failed for n_symbol={n_symbol!r}")


class BatchDetectAndFlowTests(unittest.TestCase):
    """针对「拼接分隔符污染检测」回归：逐句取首命中，而非 "\n".join 后检测。"""

    def test_br_convention_batch_not_polluted_by_join(self):
        # 关键回归：整批均为 <br> 约定（无真实换行），绝不能被 join 分隔符误判成 \n
        msgs = ["自販機にお札を投入して飲み物を選択すると、<br>冷えた水が吐き出された。",
                "俺はそれを手に壁にもたれかかり<br>会場前の様子を眺める。"]
        self.assertEqual(detect_batch_line_break_symbol(msgs), "")

    def test_crlf_batch_detects_crlf(self):
        msgs = ["a\r\nb", "c\r\nd"]
        self.assertEqual(detect_batch_line_break_symbol(msgs), "\r\n")

    def test_first_message_without_newline_still_detected(self):
        # 首句无换行、后续句含 <br> 约定时，仍应正确识别为 <br> 源（返回 ""），
        # 且不会被 join 分隔符误判成 \n（旧 "x\n".join 写法会漏判/误判）
        msgs = ["无任何换行的句子。", "a<br>b"]
        self.assertEqual(detect_batch_line_break_symbol(msgs), "")

    def test_first_message_without_newline_later_has_real_crlf(self):
        # 首句无换行、后续句含真实 CRLF 时，正确识别为 \r\n（验证后续句被扫描）
        msgs = ["无任何换行的句子。", "a\r\nb"]
        self.assertEqual(detect_batch_line_break_symbol(msgs), "\r\n")

    def _encode(self, msg: str, n_symbol: str) -> str:
        s = msg.replace("\t", "[t]")
        if n_symbol:
            s = s.replace(n_symbol, "<br>")
        return s

    def _roundtrip(self, msg: str, llmp_transform, n_symbol: str) -> str:
        enc = self._encode(msg, n_symbol)
        return _make_normalizer()._normalize_parsed_translation_text(
            llmp_transform(enc), _FakeTran(msg), n_symbol
        )

    def test_br_convention_roundtrip_robust_to_llm_deviation(self):
        # <br> 约定源：LLM 即便返回 <br>/<BR>/真实换行，都应规范回 <br>
        msgs = ["a<br>b"]
        n_symbol = detect_batch_line_break_symbol(msgs)
        self.assertEqual(n_symbol, "")
        self.assertEqual(self._roundtrip("a<br>b", lambda e: e, n_symbol), "a<br>b")
        self.assertEqual(
            self._roundtrip("a<br>b", lambda e: e.replace("<br>", "<BR>"), n_symbol), "a<br>b"
        )
        self.assertEqual(
            self._roundtrip("a<br>b", lambda e: e.replace("<br>", "\n"), n_symbol), "a<br>b"
        )
        self.assertEqual(
            self._roundtrip("a<br>b", lambda e: e.replace("<br>", "\r\n"), n_symbol), "a<br>b"
        )

    def test_crlf_batch_roundtrip_robust_to_llm_deviation(self):
        msgs = ["a\r\nb"]
        n_symbol = detect_batch_line_break_symbol(msgs)
        self.assertEqual(n_symbol, "\r\n")
        self.assertEqual(self._roundtrip("a\r\nb", lambda e: e, n_symbol), "a\r\nb")
        self.assertEqual(
            self._roundtrip("a\r\nb", lambda e: e.replace("<br>", "<BR>"), n_symbol), "a\r\nb"
        )
        self.assertEqual(
            self._roundtrip("a\r\nb", lambda e: e.replace("<br>", "\n"), n_symbol), "a\r\nb"
        )


if __name__ == "__main__":
    unittest.main()
