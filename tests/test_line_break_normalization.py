import unittest

from GalTransl.Backend.BaseTranslate import BaseTranslate
from GalTransl.Backend.ForGalJsonTranslate import ForGalJsonTranslate
from GalTransl.Backend.utils import (
    canonicalize_newlines,
    detect_line_break_symbol,
    detect_batch_line_break_symbol,
    encode_line_breaks_for_prompt,
    normalize_br_placeholders,
    restore_line_breaks,
)
from GalTransl.CSentense import CSentense


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
    """解析阶段换行归一：缓存里一律真实 \\n（n_symbol 不参与还原目标）。

    输出侧按原文类型还原由 tests/test_output_line_break_restore.py 覆盖。
    """

    def _restore(self, line_dst: str, n_symbol: str, post_src: str = "测试") -> str:
        return _make_normalizer()._normalize_parsed_translation_text(
            line_dst, _FakeTran(post_src), n_symbol
        )

    # ---- <br> / <BR> 占位符还原为真实 \n ----
    def test_br_to_lf(self):
        self.assertEqual(self._restore("行一<br>行二", "\n"), "行一\n行二")

    def test_br_restored_to_lf_regardless_of_source_symbol(self):
        # 源为 CRLF / 字面形态时，缓存仍统一为真实 \n（源类型在输出阶段还原）
        self.assertEqual(self._restore("行一<br>行二", "\r\n"), "行一\n行二")
        self.assertEqual(self._restore("行一<br>行二", "\\r\\n"), "行一\n行二")
        self.assertEqual(self._restore("行一<br>行二", "\\n"), "行一\n行二")

    def test_upper_br_to_lf(self):
        self.assertEqual(self._restore("行一<BR>行二", "\n"), "行一\n行二")
        self.assertEqual(self._restore("行一<BR>行二", "\r\n"), "行一\n行二")

    # ---- 兜底：模型直接吐真实换行符（而非 <br> 占位符）----
    def test_real_lf_kept(self):
        self.assertEqual(self._restore("行一\n行二", "\r\n"), "行一\n行二")

    def test_real_crlf_normalized_to_lf(self):
        self.assertEqual(self._restore("行一\r\n行二", "\n"), "行一\n行二")
        self.assertEqual(self._restore("行一\r\n行二", "\r\n"), "行一\n行二")

    # ---- 制表符占位符 ----
    def test_tab_placeholder_always_restored(self):
        self.assertEqual(self._restore("行一[t]行二", "\n"), "行一\t行二")
        self.assertEqual(self._restore("行一[t]行二", "\r\n"), "行一\t行二")

    # ---- 源无换行（n_symbol 为空）：占位符同样归一为真实 \n ----
    def test_no_symbol_still_normalizes_to_lf(self):
        # 缓存换行口径统一：<br> 与变体、真实换行一律收为真实 \n；
        # 是否属于「多加换行」交由 Problem.py 按 post_src 计数判定
        self.assertEqual(self._restore("行一<br>行二", ""), "行一\n行二")
        self.assertEqual(self._restore("行一<BR>行二", ""), "行一\n行二")
        self.assertEqual(self._restore("行一\n行二", ""), "行一\n行二")
        self.assertEqual(self._restore("行一\r\n行二", ""), "行一\n行二")

    # ---- 源为字面转义串形态时，缓存同样统一为真实 \n ----
    def test_literal_symbol_forms_all_normalize_to_lf(self):
        self.assertEqual(self._restore("a<br>b", "\\r\\n"), "a\nb")
        self.assertEqual(self._restore("a<br>b", "\\n"), "a\nb")
        self.assertEqual(self._restore("a\r\nb", "\\r\\n"), "a\nb")
        self.assertEqual(self._restore("a\nb", "\\n"), "a\nb")

    # ---- 混合场景 ----
    def test_mixed_br_and_real_newline_all_to_lf(self):
        self.assertEqual(self._restore("a<br>b\nc\r\nd", "\r\n"), "a\nb\nc\nd")

    def test_no_double_cr(self):
        # CRLF 不应被处理成 \r\r\n 或残留裸 \r
        self.assertEqual(self._restore("a\r\nb\r\nc", "\r\n"), "a\nb\nc")

    # ---- 编码→还原 契约：还原结果一律等于「源换行归一为 \n」----
    def test_roundtrip_contract_to_lf(self):
        cases = [
            ("行一\n行二", "\n"),
            ("行一\r\n行二", "\r\n"),
            ("行一\\n行二", "\\n"),
            ("行一\\r\\n行二", "\\r\\n"),
            ("行一\t行二", "\n"),
            ("行一\t行二", "\r\n"),
        ]
        for src, n_symbol in cases:
            # 用生产入口编码（不再手写同口径副本，口径变更时本用例随之失败）
            encoded = encode_line_breaks_for_prompt(src, n_symbol)
            restored = self._restore(encoded, n_symbol)
            expected = src.replace("\r\n", "\n").replace("\\r\\n", "\n").replace("\\n", "\n")
            self.assertEqual(
                restored, expected, msg=f"roundtrip failed for n_symbol={n_symbol!r}"
            )


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
        """复用生产编码入口，避免测试内第三份手写同口径实现。"""
        return encode_line_breaks_for_prompt(msg, n_symbol)

    def _roundtrip(self, msg: str, llmp_transform, n_symbol: str) -> str:
        enc = self._encode(msg, n_symbol)
        return _make_normalizer()._normalize_parsed_translation_text(
            llmp_transform(enc), _FakeTran(msg), n_symbol
        )

    def test_br_convention_roundtrip_robust_to_llm_deviation(self):
        # 源只有字面 <br>（四类换行皆无，n_symbol 为空）：解析阶段一律按缓存约定
        # 还原为真实 \n；源约定的还原留给输出侧按原文类型处理
        msgs = ["a<br>b"]
        n_symbol = detect_batch_line_break_symbol(msgs)
        self.assertEqual(n_symbol, "")
        self.assertEqual(self._roundtrip("a<br>b", lambda e: e, n_symbol), "a\nb")
        self.assertEqual(
            self._roundtrip("a<br>b", lambda e: e.replace("<br>", "<BR>"), n_symbol), "a\nb"
        )
        self.assertEqual(
            self._roundtrip("a<br>b", lambda e: e.replace("<br>", "\n"), n_symbol), "a\nb"
        )
        self.assertEqual(
            self._roundtrip("a<br>b", lambda e: e.replace("<br>", "\r\n"), n_symbol), "a\nb"
        )

    def test_crlf_batch_roundtrip_robust_to_llm_deviation(self):
        msgs = ["a\r\nb"]
        n_symbol = detect_batch_line_break_symbol(msgs)
        self.assertEqual(n_symbol, "\r\n")
        # 编码按批级符号（CRLF → <br>），缓存侧统一还原为真实 \n
        self.assertEqual(self._roundtrip("a\r\nb", lambda e: e, n_symbol), "a\nb")
        self.assertEqual(
            self._roundtrip("a\r\nb", lambda e: e.replace("<br>", "<BR>"), n_symbol), "a\nb"
        )
        self.assertEqual(
            self._roundtrip("a\r\nb", lambda e: e.replace("<br>", "\n"), n_symbol), "a\nb"
        )


class BrPlaceholderVariantTests(unittest.TestCase):
    """占位符变体归一：<br> 的各种写法都不应作为普通文本留在译文里。"""

    def test_variants_collected_to_canonical(self):
        for variant in ("<BR>", "<br/>", "<br />", "<Br>", "<br >", "</br>", "< BR >"):
            self.assertEqual(
                normalize_br_placeholders(f"a{variant}b"),
                "a<br>b",
                msg=f"variant {variant!r} 未被归一",
            )

    def test_variants_restored_to_symbol(self):
        for variant in ("<BR>", "<br/>", "<br />", "<Br>", "<br >", "</br>"):
            self.assertEqual(restore_line_breaks(f"a{variant}b", "\n"), "a\nb")
            self.assertEqual(restore_line_breaks(f"a{variant}b", "\r\n"), "a\r\nb")

    def test_variants_normalized_when_target_is_empty(self):
        # 空目标（源非四类换行约定）时保留 <br>，但变体仍收敛为规范写法
        self.assertEqual(restore_line_breaks("a<br/>b", ""), "a<br>b")
        self.assertEqual(restore_line_breaks("a</br>b", ""), "a<br>b")

    def test_plain_text_untouched(self):
        self.assertEqual(normalize_br_placeholders("无占位符的普通译文"), "无占位符的普通译文")
        self.assertEqual(restore_line_breaks("a<br>b", ""), "a<br>b")


class CachedDstToPromptTests(unittest.TestCase):
    """缓存译文（恒真实 \\n）编码为提示词占位符：与输入侧 dst、历史上下文同一口径。"""

    def test_real_newlines_encoded(self):
        self.assertEqual(restore_line_breaks("a\r\nb", ""), "a<br>b")
        self.assertEqual(restore_line_breaks("a\nb", ""), "a<br>b")

    def test_variants_collapsed(self):
        self.assertEqual(restore_line_breaks("a<BR>b\nc", ""), "a<br>b<br>c")

    def test_variant_whitespace_does_not_eat_newline(self):
        # 变体正则只允许空格/Tab 作内部空白：<br 换行 /> 不得被折成一个 <br> 吃掉换行
        self.assertEqual(normalize_br_placeholders("a<br\n/>b"), "a<br\n/>b")

    def test_literal_text_untouched(self):
        # 字面 \n 可能是译文正文（如 Windows 路径），不得误改
        self.assertEqual(restore_line_breaks("C:\\new", ""), "C:\\new")
        self.assertEqual(restore_line_breaks("a\\nb", ""), "a\\nb")

    def test_canonicalize_folds_real_and_placeholder_forms(self):
        # 比较键：真实换行 + 占位符变体折叠为真实 \n
        for text in ("a\nb", "a\r\nb", "a<br>b", "a<BR/>b", "a</br>b"):
            self.assertEqual(canonicalize_newlines(text), "a\nb", msg=repr(text))

    def test_canonicalize_does_not_fold_literal_escape_text(self):
        # 刻意不折叠字面 \n/\r\n：它们可能是正文（路径等），折叠会漏掉真实改动。
        # 代价：字面约定项目的旧缓存可能多写一次 alt_dst（构建输出阶段仍会按原文
        # 类型还原，不污染落盘结果）
        self.assertEqual(canonicalize_newlines("C:\\new"), "C:\\new")
        self.assertEqual(canonicalize_newlines("a\\nb"), "a\\nb")
        self.assertEqual(canonicalize_newlines("a\\r\\nb"), "a\\r\\nb")

    def test_canonicalize_keeps_bare_cr_conservative(self):
        # 裸 \r 不折叠（宁可判「不同」）：与 Problem._newline_count 的计数口径不同
        self.assertEqual(canonicalize_newlines("a\rb"), "a\rb")


class HistoryInjectionLineBreakTests(unittest.TestCase):
    """单轮模式历史注入的换行口径（修复：历史与输入口径不一致）。"""

    def _single(self) -> BaseTranslate:
        obj = object.__new__(BaseTranslate)
        obj.last_translations = {}
        return obj

    def test_apply_history_result_keeps_br(self):
        # 旧行为 history.replace("<br>", "") 会删掉历史译文里的断行
        obj = self._single()
        obj.last_translations = {"f.json": 'old|{"id":1,"dst":"前<br>后"}'}
        out = obj._apply_history_result("[history_result]", "f.json")
        self.assertIn("前<br>后", out)
        self.assertNotIn("前后", out)

    def test_apply_history_result_placeholder_when_empty(self):
        obj = self._single()
        obj.last_translations = {"f.json": ""}
        out = obj._apply_history_result("X[history_result]Y", "f.json")
        self.assertNotIn("[history_result]", out)
        self.assertIn("X", out)

    def test_history_line_encodes_newlines_as_br(self):
        tran = CSentense("原文", index=7)
        tran.pre_dst = "第一行\n第二行\r\n第三行"
        line = ForGalJsonTranslate._format_restore_context_line(
            object.__new__(ForGalJsonTranslate), tran
        )
        self.assertTrue(line.startswith("old|"))
        self.assertEqual(line.count("<br>"), 2)
        self.assertNotIn("\n", line.split("|", 1)[1])
        # 历史行的 dst 是 jsonline，可解析且换行为 <br>
        self.assertIn('"dst": "第一行<br>第二行<br>第三行"', line)


class BrVariantRegressionThroughTranslateTests(unittest.TestCase):
    """变体必须经翻译轮的还原入口生效，而不是仅工具函数可用。"""

    def _restore(self, line_dst: str, n_symbol: str) -> str:
        return _make_normalizer()._normalize_parsed_translation_text(
            line_dst, _FakeTran("测试"), n_symbol
        )

    def test_normalizer_uses_shared_entry(self):
        self.assertEqual(self._restore("行一<br/>行二", "\n"), "行一\n行二")
        self.assertEqual(self._restore("行一</br>行二", "\r\n"), "行一\n行二")
        self.assertEqual(self._restore("行一<Br >行二", ""), "行一\n行二")


if __name__ == "__main__":
    unittest.main()
