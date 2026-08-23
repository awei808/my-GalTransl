"""GenDic terms 模式提取层单元测试（设计文档 gendic_terms_mode_design.md 第七节）。

覆盖：POS 白名单过滤、字符构成过滤、拟声组合判定、单字名字/占位符处理、
固有名詞下探 1 次、片假名普通名词频次门槛、汉字普通名词默认丢弃/白名单/字典匹配、
gendic 配置读取（internals 段展平）与默认模板。
"""

import os
import tempfile
import unittest
import uuid
from unittest.mock import MagicMock, patch

from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.DefaultProjectConfig import DEFAULT_PROJECT_CONFIG_YAML
from GalTransl.Backend.Prompts import GENDIC_TERMS_PROMPT, H_WORDS_LIST
from GalTransl.Backend.GenDic import (
    GenDic,
    extract_terms_from_tokens,
    _is_onomatopoeia,
    _is_placeholder_name,
    _is_pure_kana,
    _is_term_droppable,
)

def _mkdtemp_writable(prefix: str) -> str:
    """创建可写临时目录：tempfile.mkdtemp 默认 0o700 在受限沙箱下内部文件不可写，
    改用不带 mode 的 os.makedirs（与 tests/test_forsemcheck_e2e.py 同法）。"""
    base = tempfile.gettempdir()
    for _ in range(100):
        path = os.path.join(base, f"{prefix}{uuid.uuid4().hex[:10]}")
        try:
            os.makedirs(path)
            return path
        except FileExistsError:
            continue
    raise RuntimeError(f"无法在 {base} 下创建唯一临时目录")


def _tokens(*pairs: tuple) -> list:
    """构造 (surface, pos_tag0) token 流。"""
    return [(s, t) for s, t in pairs]


def _extract(tokens, name_set=(), name_counter=None, existing=None, allow=None, skip_existing=True, ban_words=None):
    return extract_terms_from_tokens(
        tokens,
        set(name_set),
        dict(name_counter or {}),
        existing_dict_map=existing,
        han_allowlist=set(allow or []),
        skip_existing=skip_existing,
        ban_words=set(ban_words or []),
    )


class TermsOnomatopoeiaTests(unittest.TestCase):
    """拟声组合条件：纯假名 + 叠音重复子串。"""

    def test_abab_and_aa_on_matopoeia_detected(self) -> None:
        for w in ("ドキドキ", "イクイク", "ククク", "パンパン", "ビクビク", "パパ"):
            self.assertTrue(_is_onomatopoeia(w), f"{w} 应判定为拟声")

    def test_common_katakana_not_misjudged(self) -> None:
        # 含长音/小字但无叠音的普通词，不单凭"含长音"判定为拟声
        for w in ("スイートルーム", "フィギュア", "サキュバス", "ハリガタ", "ドア", "ホテル"):
            self.assertFalse(_is_onomatopoeia(w), f"{w} 不应误判为拟声")


class TermsPlaceholderTests(unittest.TestCase):
    def test_symbol_placeholders(self) -> None:
        for n in ("？？？", "？？？？", "…", "---", ""):
            self.assertTrue(_is_placeholder_name(n), f"{n!r} 应为占位符")

    def test_real_names_not_placeholder(self) -> None:
        for n in ("創", "凛音", "ファンＡ", "華恋＆凛音", "会場スタッフ"):
            self.assertFalse(_is_placeholder_name(n), f"{n!r} 不应为占位符")


class TermsPureKanaTests(unittest.TestCase):
    def test_pure_kana_ranges_include_voiced(self) -> None:
        # Unicode 范围判定必须覆盖浊音/半浊音（此前手写集合漏掉的回归点）
        self.assertTrue(_is_pure_kana("ペニス"))
        self.assertTrue(_is_pure_kana("フィギュア"))
        self.assertTrue(_is_pure_kana("サキュバス"))
        self.assertFalse(_is_pure_kana("時間"))
        self.assertFalse(_is_pure_kana("abc"))


class TermsDropRuleTests(unittest.TestCase):
    """落盘前过滤（真实测试暴露）：拟声 note / H 词表 / NULL / 空 note 的未翻译回显。"""

    def test_onomatopoeia_note_dropped(self) -> None:
        self.assertTrue(_is_term_droppable("ビク", "ビク", "拟声词"))
        self.assertTrue(_is_term_droppable("ノック", "敲门", "拟声词/动作"))
        self.assertFalse(_is_term_droppable("ノック", "敲门", "术语"))

    def test_h_word_dropped(self) -> None:
        self.assertTrue(_is_term_droppable("ちんぽ", "鸡巴", "术语"))
        self.assertFalse(_is_term_droppable("サキュバス", "魅魔", "术语"))

    def test_h_word_list_extended_with_katakana_h_terms(self) -> None:
        # 2026-08 实测补充：社区 H 词表缺失的常用片假名 H 词（セックス/ペニス/マラ 等）
        for w in ("セックス", "クリトリス", "オナニー", "パイズリ", "イキ", "ペニス", "ディック", "マラ",
                  "フェラチオ", "チンポ", "バイブ", "イラマチオ", "クンニ", "手コキ", "足コキ", "フェラ", "オナホ"):
            self.assertIn(w, H_WORDS_LIST)
            self.assertTrue(_is_term_droppable(w, "示例", "术语"))

    def test_h_word_list_extended_with_han_h_terms(self) -> None:
        # 汉字 H 词补充（2026-08 实测：膣内全体/思い切り射精 组合漏网后修复）
        for w in ("膣内", "射精", "勃起", "挿入", "絶頂", "陰茎", "乳房", "乳首", "性感帯"):
            self.assertIn(w, H_WORDS_LIST)
            self.assertTrue(_is_term_droppable(w, "示例", "术语"))

    def test_null_src_dropped(self) -> None:
        self.assertTrue(_is_term_droppable("NULL", "NULL", "NULL"))

    def test_untranslated_echo_with_empty_note_dropped(self) -> None:
        self.assertTrue(_is_term_droppable("マラ", "マラ", ""))
        # 有 note 的回显（如 凛音 人名）或翻译不同 保留
        self.assertFalse(_is_term_droppable("凛音", "凛音", "人名，女性"))
        self.assertFalse(_is_term_droppable("本州", "本州", "地名（岛屿）"))
        self.assertFalse(_is_term_droppable("サキュバス", "魅魔", ""))


class TermsExtractRuleTests(unittest.TestCase):
    """第七节收词规则。"""

    def test_proper_noun_included_at_freq_1(self) -> None:
        # 固有名詞 freq-1 含汉字（本州）仍收录；人名 POS（クルト）直接被排除（不计数）
        terms, stats = _extract(_tokens(("本州", "名詞-固有名詞-地域-一般")))
        self.assertIn(("本州", 1, "固有名詞"), terms)
        self.assertEqual(stats["固有名詞"], 1)
        terms2, stats2 = _extract(_tokens(("クルト", "名詞-固有名詞-人名-一般")))
        self.assertNotIn("クルト", [t[0] for t in terms2])
        self.assertEqual(stats2["低频假名固有名詞丢弃"], 0)  # 人名 POS 在提取层直接排除

    def test_katakana_common_noun_needs_freq_2(self) -> None:
        terms, _ = _extract(_tokens(("フィギュア", "名詞-普通名詞-一般")))
        self.assertNotIn("フィギュア", [t[0] for t in terms])
        terms, _ = _extract(_tokens(
            ("フィギュア", "名詞-普通名詞-一般"), ("フィギュア", "名詞-普通名詞-一般")
        ))
        self.assertIn(("フィギュア", 2, "片假名普通名词"), terms)

    def test_katakana_common_noun_dict_match_skipped_by_default(self) -> None:
        # 已有字典词默认跳过（不重复发送 AI）；skip_existing=False 保留"字典匹配降频"门槛行为
        terms, stats = _extract(
            _tokens(("フィギュア", "名詞-普通名詞-一般")),
            existing={"フィギュア": ("手办", "")},
        )
        self.assertNotIn("フィギュア", [t[0] for t in terms])
        self.assertEqual(stats["已有字典跳过"], 1)
        terms2, _ = _extract(
            _tokens(("フィギュア", "名詞-普通名詞-一般")),
            existing={"フィギュア": ("手办", "")},
            skip_existing=False,
        )
        self.assertIn(("フィギュア", 1, "片假名普通名词"), terms2)

    def test_han_common_noun_dropped_by_default(self) -> None:
        terms, stats = _extract(_tokens(
            ("時間", "名詞-普通名詞-一般"), ("時間", "名詞-普通名詞-一般")
        ))
        self.assertNotIn("時間", [t[0] for t in terms])
        self.assertEqual(stats["汉字丢弃"], 1)

    def test_han_common_noun_allowlist(self) -> None:
        terms, stats = _extract(
            _tokens(("膣内", "名詞-普通名詞-一般"), ("膣内", "名詞-普通名詞-一般")),
            allow=["膣内"],
        )
        self.assertIn(("膣内", 2, "汉字词(白名单/字典)"), terms)
        self.assertEqual(stats["汉字丢弃"], 0)

    def test_existing_dict_term_skipped_by_default(self) -> None:
        # 已有 GPT 字典词不重复发送 AI（用户决策），直接跳过并计数
        terms, stats = _extract(
            _tokens(("膣内", "名詞-普通名詞-一般"), ("膣内", "名詞-普通名詞-一般")),
            existing={"膣内": ("体内", "")},
        )
        self.assertNotIn("膣内", [t[0] for t in terms])
        self.assertEqual(stats["已有字典跳过"], 1)

    def test_existing_dict_term_kept_when_skip_existing_false(self) -> None:
        # skip_existing=False 时保留门槛行为（existing 词仍可收录）
        terms, _ = _extract(
            _tokens(("膣内", "名詞-普通名詞-一般"), ("膣内", "名詞-普通名詞-一般")),
            existing={"膣内": ("体内", "")},
            skip_existing=False,
        )
        self.assertIn(("膣内", 2, "汉字词(白名单/字典)"), terms)

    def test_proper_noun_name_pos_excluded(self) -> None:
        # 人名不收录（用户决策：全局分析/name 人名替换表已覆盖）：名詞-固有名詞-人名 POS 直接排除
        terms, stats = _extract(
            _tokens(("凛音", "名詞-固有名詞-人名-一般"), ("凛音", "名詞-固有名詞-人名-一般"))
        )
        self.assertNotIn("凛音", [t[0] for t in terms])

    def test_single_char_name_not_included(self) -> None:
        # 单字名（創）不再靠 name_counter 加权保底进词表（人名不收录）
        terms, stats = _extract([], name_set=["創"], name_counter={"創": 8536})
        self.assertNotIn("創", [t[0] for t in terms])
        self.assertEqual(stats["固有名詞"], 0)

    def test_ban_words_filtered(self) -> None:
        # 太过平常的词汇（代词/语气词）黑名单过滤，不发送 AI
        terms, stats = _extract(
            _tokens(
                ("キミ", "名詞-固有名詞-一般"),
                ("ダメ", "名詞-普通名詞-一般"),
            ),
            ban_words=["キミ", "ダメ"],
        )
        srcs = [t[0] for t in terms]
        self.assertNotIn("キミ", srcs)
        self.assertNotIn("ダメ", srcs)
        self.assertEqual(stats["黑名单丢弃"], 2)

    def test_proper_noun_freq1_pure_kana_dropped(self) -> None:
        # 固有名詞 freq-1 且纯假名：vaporetto 误判的平常词（キサ/ヒック 等），丢弃
        terms, stats = _extract(_tokens(
            ("キサ", "名詞-固有名詞-一般"),
            ("ヒック", "名詞-固有名詞-一般"),
        ))
        srcs = [t[0] for t in terms]
        self.assertNotIn("キサ", srcs)
        self.assertNotIn("ヒック", srcs)
        self.assertEqual(stats["低频假名固有名詞丢弃"], 2)
        # 汉字低频固有名詞（本州）保留；freq≥2 纯假名固有名詞（カウパー）保留
        terms2, _ = _extract(_tokens(
            ("本州", "名詞-固有名詞-地域-一般"),
            ("カウパー", "名詞-固有名詞-一般"), ("カウパー", "名詞-固有名詞-一般"),
        ))
        srcs2 = [t[0] for t in terms2]
        self.assertIn("本州", srcs2)
        self.assertIn("カウパー", srcs2)

    def test_single_char_name_included(self) -> None:
        # 单字名（創）不再加权进词表（人名不收录：name_counter 已不使用）
        terms, stats = _extract([], name_set=["創"], name_counter={"創": 8536})
        self.assertNotIn("創", [t[0] for t in terms])
        self.assertEqual(stats["固有名詞"], 0)

    def test_katakana_dash_string_excluded(self) -> None:
        terms, _ = _extract(_tokens(
            ("ーーー", "名詞-普通名詞-一般"), ("ーーー", "名詞-普通名詞-一般")
        ))
        self.assertNotIn("ーーー", [t[0] for t in terms])

    def test_onomatopoeia_token_excluded(self) -> None:
        terms, _ = _extract(_tokens(
            ("ドキドキ", "名詞-普通名詞-一般"), ("ドキドキ", "名詞-普通名詞-一般")
        ))
        self.assertNotIn("ドキドキ", [t[0] for t in terms])

    def test_pos_filter_excludes_non_noun(self) -> None:
        tokens = _tokens(
            ("を", "助詞-格助詞"),
            ("行く", "動詞-一般"),
            ("うわっ", "感動詞-一般"),
            ("ゆっくり", "副詞"),
            ("彼", "代名詞"),
            ("三", "名詞-数詞"),
            ("さ", "接尾辞-名詞的-一般"),
            ("。", "補助記号-句点"),
        )
        terms, _ = _extract(tokens)
        self.assertEqual(terms, [])

    def test_proper_noun_subbranch_katakana(self) -> None:
        # 固有名詞子分支：非人名的片假名专名（如 カウパー 判为一般专名）freq≥2 收录；
        # 人名 POS（クルト）排除
        terms, _ = _extract(_tokens(
            ("カウパー", "名詞-固有名詞-一般"), ("カウパー", "名詞-固有名詞-一般")
        ))
        self.assertIn(("カウパー", 2, "固有名詞"), terms)
        terms2, _ = _extract(_tokens(
            ("クルト", "名詞-固有名詞-人名-一般"), ("クルト", "名詞-固有名詞-人名-一般")
        ))
        self.assertNotIn("クルト", [t[0] for t in terms2])


class TermsCompoundExtractTests(unittest.TestCase):
    """复合词（2-3 token）提取：名詞+名詞 / 名詞+连接记号+名詞。"""

    def test_compound_2gram_collected(self) -> None:
        tokens = _tokens(
            ("ロール", "名詞-普通名詞-一般"), ("プレイ", "名詞-普通名詞-一般"),
            ("ロール", "名詞-普通名詞-一般"), ("プレイ", "名詞-普通名詞-一般"),
        )
        terms, stats = _extract(tokens)
        self.assertIn(("ロールプレイ", 2, "复合词"), terms)
        self.assertEqual(stats["复合词"], 1)

    def test_compound_3gram_mark_collected(self) -> None:
        tokens = _tokens(
            ("オバ", "名詞-普通名詞-一般"), ("★", "補助記号-一般"), ("グラ", "名詞-普通名詞-一般"),
            ("オバ", "名詞-普通名詞-一般"), ("★", "補助記号-一般"), ("グラ", "名詞-普通名詞-一般"),
        )
        terms, stats = _extract(tokens)
        self.assertIn(("オバ★グラ", 2, "复合词"), terms)

    def test_compound_quote_mark_not_collected(self) -> None:
        # 「」引用号不参与 3-gram 组合（华恋「凛音 噪音）
        tokens = _tokens(
            ("華恋", "名詞-固有名詞-人名-一般"), ("「", "補助記号-一般"), ("凛音", "名詞-固有名詞-人名-一般"),
            ("華恋", "名詞-固有名詞-人名-一般"), ("「", "補助記号-一般"), ("凛音", "名詞-固有名詞-人名-一般"),
        )
        terms, _ = _extract(tokens)
        self.assertNotIn("華恋「凛音", [t[0] for t in terms])

    def test_compound_freq1_not_collected(self) -> None:
        tokens = _tokens(
            ("フィギュア", "名詞-普通名詞-一般"), ("製作", "名詞-普通名詞-一般"),
        )
        terms, _ = _extract(tokens)
        self.assertNotIn("フィギュア製作", [t[0] for t in terms])

    def test_compound_h_component_filtered(self) -> None:
        # 组合含 H 词成分（エロ）→ 组合不收集；单 token エロ 留待落盘层 H 过滤
        tokens = _tokens(
            ("エロ", "名詞-普通名詞-一般"), ("フィギュア", "名詞-普通名詞-一般"),
            ("エロ", "名詞-普通名詞-一般"), ("フィギュア", "名詞-普通名詞-一般"),
        )
        terms, _ = _extract(tokens)
        srcs = [t[0] for t in terms]
        self.assertNotIn("エロフィギュア", srcs)
        self.assertIn("フィギュア", srcs)  # 非 H 成分独立保留

    def test_compound_han_h_component_filtered(self) -> None:
        # 汉字 H 成分（膣内/射精）组合也过滤（2026-08 实测：膣内全体/思い切り射精 漏网后修复）
        tokens = _tokens(
            ("膣内", "名詞-普通名詞-一般"), ("全体", "名詞-普通名詞-一般"),
            ("膣内", "名詞-普通名詞-一般"), ("全体", "名詞-普通名詞-一般"),
        )
        terms, _ = _extract(tokens)
        self.assertNotIn("膣内全体", [t[0] for t in terms])
        tokens2 = _tokens(
            ("思い切り", "名詞-普通名詞-一般"), ("射精", "名詞-普通名詞-一般"),
            ("思い切り", "名詞-普通名詞-一般"), ("射精", "名詞-普通名詞-一般"),
        )
        terms2, _ = _extract(tokens2)
        self.assertNotIn("思い切り射精", [t[0] for t in terms2])

    def test_compound_priority_removes_fragment(self) -> None:
        # 组合优先：オバ/グラ 只作为组合成分出现 → 碎片剔除；独立频次足则保留
        tokens = _tokens(
            ("オバ", "名詞-普通名詞-一般"), ("★", "補助記号-一般"), ("グラ", "名詞-普通名詞-一般"),
            ("オバ", "名詞-普通名詞-一般"), ("★", "補助記号-一般"), ("グラ", "名詞-普通名詞-一般"),
        )
        terms, stats = _extract(tokens)
        srcs = [t[0] for t in terms]
        self.assertIn("オバ★グラ", srcs)
        self.assertNotIn("オバ", srcs)
        self.assertNotIn("グラ", srcs)
        self.assertEqual(stats["组合覆盖剔除"], 2)

    def test_compound_priority_keeps_independent_word(self) -> None:
        # フィギュア 大量独立出现 + 少量组合 → 独立频次仍足，保留
        tokens = _tokens(
            ("フィギュア", "名詞-普通名詞-一般"), ("造り", "名詞-普通名詞-一般"), ("を", "助詞-格助詞"),
            ("フィギュア", "名詞-普通名詞-一般"), ("造り", "名詞-普通名詞-一般"), ("を", "助詞-格助詞"),
            ("フィギュア", "名詞-普通名詞-一般"), ("造り", "名詞-普通名詞-一般"), ("を", "助詞-格助詞"),
            ("フィギュア", "名詞-普通名詞-一般"), ("を", "助詞-格助詞"),
            ("フィギュア", "名詞-普通名詞-一般"), ("だ", "助動詞"),
        )
        terms, _ = _extract(tokens)
        srcs = [t[0] for t in terms]
        self.assertIn("フィギュア造り", srcs)
        self.assertIn("フィギュア", srcs)  # 独立 2 次（5-3）≥2 保留
        self.assertNotIn("造り", srcs)  # 造り 独立 0，剔除（动词连用碎片）


class TermsAlphaAbbrTests(unittest.TestCase):
    """大写字母组合（含全角）提取：ＣＦ/ＳＮＳ 等关键设定缩写。"""

    def test_alpha_abbr_fullwidth_collected(self) -> None:
        tokens = _tokens(
            ("ＣＦ", "名詞-普通名詞-一般"), ("ＣＦ", "名詞-普通名詞-一般"),
        )
        terms, stats = _extract(tokens)
        self.assertIn(("ＣＦ", 2, "字母组合"), terms)
        self.assertEqual(stats["字母组合"], 1)

    def test_alpha_abbr_halfwidth_collected(self) -> None:
        tokens = _tokens(
            ("AV", "名詞-普通名詞-一般"), ("AV", "名詞-普通名詞-一般"),
        )
        terms, _ = _extract(tokens)
        self.assertIn(("AV", 2, "字母组合"), terms)

    def test_alpha_abbr_control_code_filtered(self) -> None:
        # %p/%f 控制码（%fＭＳ）不收集
        tokens = _tokens(
            ("%fＭＳ", "名詞-普通名詞-一般"), ("%fＭＳ", "名詞-普通名詞-一般"),
        )
        terms, _ = _extract(tokens)
        self.assertNotIn("%fＭＳ", [t[0] for t in terms])

    def test_alpha_abbr_mixed_case_name_suffix_filtered(self) -> None:
        # Ｊｒ（全角大写+小写）不是纯大写组合，不收集
        tokens = _tokens(
            ("Ｊｒ", "名詞-固有名詞-人名-一般"), ("Ｊｒ", "名詞-固有名詞-人名-一般"),
        )
        terms, _ = _extract(tokens)
        self.assertNotIn("Ｊｒ", [t[0] for t in terms])


class TermsLlmExtractTests(unittest.TestCase):
    """LLM 全权模式（gendic.mode=llm）：AI 从文本块直接提取术语（含翻译）。"""

    def test_parse_llm_extract_response_basic(self) -> None:
        rsp = (
            "```tsv\n日文原词\t中文翻译\t备注\n"
            "サキュバス\t魅魔\t术语\n"
            "フィギュア\t手办\t物品\n"
            "```\n"
        )
        entries = GenDic._parse_llm_extract_response(rsp)
        self.assertEqual(
            entries,
            [("サキュバス", "魅魔", "术语"), ("フィギュア", "手办", "物品")],
        )

    def test_parse_llm_extract_response_skips_untranslatable_and_header(self) -> None:
        rsp = (
            "日文原词\t中文翻译\t备注\n"
            "むー\t（无法翻译）\t拟声\n"
            "凛音\t凛音\t人名，女性\n"
            "クルト\tクルト\t\n"  # 空 note 回显
        )
        entries = GenDic._parse_llm_extract_response(rsp)
        self.assertEqual(entries, [("凛音", "凛音", "人名，女性"), ("クルト", "クルト", "")])

    def test_parse_llm_extract_response_long_note_truncated(self) -> None:
        rsp = "オバ★グラ\t欧巴格拉\t" + "很长的备注" * 10 + "\n"
        entries = GenDic._parse_llm_extract_response(rsp)
        self.assertEqual(len(entries), 1)
        self.assertLessEqual(len(entries[0][2]), 20)


class TermsParseResponseTests(unittest.TestCase):
    """解析容错：按输入词匹配 + grounding 防幻觉。"""

    def _parse(self, rsp: str, words):
        return GenDic._parse_terms_response(rsp, words)

    def test_exact_match_all(self) -> None:
        rsp = "日文原词\t中文翻译\t备注\nフィギュア\t手办\t物品\nサキュバス\t魅魔\t术语\n"
        matched, extra = self._parse(rsp, ["フィギュア", "サキュバス"])
        self.assertEqual(matched, {"フィギュア": ("手办", "物品"), "サキュバス": ("魅魔", "术语")})
        self.assertEqual(extra, [])

    def test_missing_row_collected(self) -> None:
        rsp = "フィギュア\t手办\t物品\n"
        matched, _ = self._parse(rsp, ["フィギュア", "サキュバス"])
        self.assertIn("フィギュア", matched)
        self.assertNotIn("サキュバス", matched)

    def test_extra_row_outside_terms_dropped(self) -> None:
        # grounding：输出词不在输入词表 → 丢弃
        rsp = "フィギュア\t手办\t物品\nホテル\t酒店\t术语\n"
        matched, extra = self._parse(rsp, ["フィギュア"])
        self.assertEqual(matched, {"フィギュア": ("手办", "物品")})
        self.assertEqual(extra, ["ホテル\t酒店\t术语"])

    def test_normalized_match_fullwidth_space(self) -> None:
        rsp = "フィギュア\t手办\t物品\n"
        matched, _ = self._parse(rsp, ["フィギュア　"])  # 输入带全角空格，输出不带
        self.assertIn("フィギュア　", matched)

    def test_merged_row_dropped_when_not_match(self) -> None:
        # AI 合并两词为一行：日文与任一输入词不一致 → 按 grounding 丢弃该行
        rsp = "フィギュアサキュバス\t手办魅魔\t合并\n"
        matched, extra = self._parse(rsp, ["フィギュア", "サキュバス"])
        self.assertEqual(matched, {})
        self.assertEqual(len(extra), 1)

    def test_untranslatable_marker_skipped(self) -> None:
        rsp = "フィギュア\t（无法翻译）\t词汇过新\n"
        matched, _ = self._parse(rsp, ["フィギュア"])
        self.assertEqual(matched, {})

    def test_code_fence_and_header_skipped(self) -> None:
        rsp = "```tsv\n日文原词\t中文翻译\t备注\nフィギュア\t手办\t物品\n```\n"
        matched, _ = self._parse(rsp, ["フィギュア"])
        self.assertEqual(matched, {"フィギュア": ("手办", "物品")})

    def test_long_note_truncated(self) -> None:
        rsp = "フィギュア\t手办\t" + "很长的备注" * 10 + "\n"
        matched, _ = self._parse(rsp, ["フィギュア"])
        note = matched["フィギュア"][1]
        self.assertLessEqual(len(note), 20)


class TermsSortTruncateTests(unittest.TestCase):
    def test_category_priority_order(self) -> None:
        terms = [
            ("サキュバス", 29, "片假名普通名词"),
            ("凛音", 11118, "固有名詞"),
            ("クルト", 12, "固有名詞"),
        ]
        ordered = GenDic._sort_and_truncate_terms(terms)
        # 固有名詞优先；同级按频次降序（凛音 11118 > クルト 12）
        self.assertEqual([t[0] for t in ordered], ["凛音", "クルト", "サキュバス"])

    def test_compound_and_abbr_rank_with_katakana(self) -> None:
        # 复合词/字母组合与片假名普通名词同级，按频次竞争
        terms = [
            ("ロールプレイ", 132, "复合词"),
            ("ペニス", 552, "片假名普通名词"),
            ("ＣＦ", 44, "字母组合"),
            ("日本", 3, "固有名詞"),
        ]
        ordered = GenDic._sort_and_truncate_terms(terms, max_terms=3)
        self.assertEqual([t[0] for t in ordered], ["日本", "ペニス", "ロールプレイ"])

    def test_truncate_keeps_proper_nouns(self) -> None:
        terms = [
            ("詞A", 1, "片假名普通名词"),
            ("詞B", 2, "片假名普通名词"),
            ("专名", 1, "固有名詞"),
        ]
        ordered = GenDic._sort_and_truncate_terms(terms, max_terms=3)
        names = {t[0] for t in ordered}
        self.assertIn("专名", names)
        # 普通术语只留 2 个（budget = 3 - 1 = 2，取频次最高的 詞B 与 詞A）
        self.assertEqual(len(ordered), 3)
        self.assertIn("詞A", names)
        self.assertIn("詞B", names)

    def test_truncate_hard_cap_when_proper_nouns_exceed_limit(self) -> None:
        # 保底类别本身超上限时按类别优先级+频次截断（硬上限：生成字典总条目 ≤ max_terms）
        terms = [
            (f"固{i}", 100 - i, "固有名詞") for i in range(5)
        ] + [
            (f"詞{i}", 50 - i, "片假名普通名词") for i in range(3)
        ]
        ordered = GenDic._sort_and_truncate_terms(terms, max_terms=5)
        self.assertEqual(len(ordered), 5)
        # 硬上限：固有名詞 5 个本身超限 → 按频次保留前 5（固0~固4）
        self.assertEqual([t[0] for t in ordered], ["固0", "固1", "固2", "固3", "固4"])
        self.assertNotIn("詞0", [t[0] for t in ordered])

    def test_no_truncate_when_zero(self) -> None:
        terms = [("詞A", 1, "片假名普通名词")] * 1
        self.assertEqual(len(GenDic._sort_and_truncate_terms(terms, max_terms=0)), 1)


class TermsContextTests(unittest.TestCase):
    def test_find_contexts_returns_multiple_deduped_sentences(self) -> None:
        json_list = [
            {"message": "冒頭の文。"},
            {"message": "サキュバスが現れた。"},
            {"message": "またサキュバスだ。"},
            {"message": "同じ文。"},
            {"message": "サキュバスと戦う。"},
        ]
        ctx = GenDic._find_contexts("サキュバス", json_list, max_samples=2)
        self.assertEqual(ctx, ["サキュバスが現れた。", "またサキュバスだ。"])

    def test_find_contexts_dedupes_same_sentence(self) -> None:
        json_list = [
            {"message": "サキュバスだ。"},
            {"message": "サキュバスだ。"},
            {"message": "サキュバスだ。"},
        ]
        self.assertEqual(GenDic._find_contexts("サキュバス", json_list, max_samples=3), ["サキュバスだ。"])

    def test_find_contexts_no_match_returns_empty(self) -> None:
        self.assertEqual(GenDic._find_contexts("無い語", [{"message": "別の文。"}], max_samples=3), [])

    def test_find_contexts_respects_max_samples_upper_bound(self) -> None:
        json_list = [{"message": f"語{i}。"} for i in range(12)]
        self.assertEqual(len(GenDic._find_contexts("語", json_list, max_samples=10)), 10)


class TermsPromptFormatTests(unittest.TestCase):
    def test_prompt_placeholders_replaceable(self) -> None:
        prompt = (
            GENDIC_TERMS_PROMPT.replace("{terms}", "1. フィギュア\n2. サキュバス")
            .replace("{context_hint}", "1. フィギュア：彼女のフィギュアを撮影する。")
        )
        self.assertNotIn("{terms}", prompt)
        self.assertNotIn("{context_hint}", prompt)
        self.assertIn("1. フィギュア", prompt)
        self.assertIn("彼女のフィギュアを撮影する。", prompt)


class TermsConfigTests(unittest.TestCase):
    """internals.gendic 配置读取（递归展平为点分键）。"""

    def _project(self, yaml_text: str) -> CProjectConfig:
        tmp = _mkdtemp_writable("gendic_cfg_")
        with open(os.path.join(tmp, "config.yaml"), "w", encoding="utf-8") as f:
            f.write(yaml_text)
        return CProjectConfig(tmp)

    def test_gendic_keys_loaded_from_internals(self) -> None:
        cfg = self._project(
            "common:\n  language: zh-cn\ninternals:\n"
            "  gendic:\n    mode: segments\n    batch_size: 30\n"
            "    context: off\n    max_terms: 0\n"
        )
        # internals 段递归展平为点分键，键带 internals. 前缀；YAML 的 on/off 解析为 bool
        self.assertEqual(cfg.getKey("internals.gendic.mode"), "segments")
        self.assertEqual(cfg.getKey("internals.gendic.batch_size"), 30)
        self.assertFalse(cfg.getKey("internals.gendic.context"))  # off -> False
        self.assertEqual(cfg.getKey("internals.gendic.max_terms"), 0)

    def test_llm_mode_and_chunk_size_config(self) -> None:
        with patch("GalTransl.Backend.BaseEngine.OpenCC", return_value=MagicMock(convert=lambda s: s)), \
                patch("GalTransl.Backend.GenDic.GenDic.init_chatbot", lambda self, *a, **k: None):
            cfg = self._project(
                "common:\n  language: zh-cn\nbackendSpecific:\n  OpenAI-Compatible:\n    tokens: []\n"
                "internals:\n  gendic:\n    mode: llm\n    llm_chunk_size: 4000\n"
            )
            backend = GenDic(cfg, "GenDic", None, None)
            self.assertEqual(backend.gendic_mode, "llm")
            self.assertEqual(backend.gendic_llm_chunk_size, 4000)
            # 缺省回退默认值（llm + 6000）
            cfg2 = self._project(
                "common:\n  language: zh-cn\nbackendSpecific:\n  OpenAI-Compatible:\n    tokens: []\n"
            )
            backend2 = GenDic(cfg2, "GenDic", None, None)
            self.assertEqual(backend2.gendic_mode, "llm")
            self.assertEqual(backend2.gendic_llm_chunk_size, 6000)

    def test_default_template_contains_gendic_section(self) -> None:
        self.assertIn("gendic:", DEFAULT_PROJECT_CONFIG_YAML)
        self.assertIn("mode: llm", DEFAULT_PROJECT_CONFIG_YAML)
        self.assertIn("max_terms: 128", DEFAULT_PROJECT_CONFIG_YAML)
        self.assertIn("context_samples: 3", DEFAULT_PROJECT_CONFIG_YAML)

    def test_context_samples_parsed_and_clamped(self) -> None:
        with patch("GalTransl.Backend.BaseEngine.OpenCC", return_value=MagicMock(convert=lambda s: s)), \
                patch("GalTransl.Backend.GenDic.GenDic.init_chatbot", lambda self, *a, **k: None):
            cfg = self._project(
                "common:\n  language: zh-cn\nbackendSpecific:\n  OpenAI-Compatible:\n    tokens: []\n"
                "internals:\n  gendic:\n    context_samples: 7\n"
            )
            backend = GenDic(cfg, "GenDic", None, None)
            self.assertEqual(backend.gendic_context_samples, 7)
            # 缺省回退 3
            cfg2 = self._project(
                "common:\n  language: zh-cn\nbackendSpecific:\n  OpenAI-Compatible:\n    tokens: []\n"
            )
            backend2 = GenDic(cfg2, "GenDic", None, None)
            self.assertEqual(backend2.gendic_context_samples, 3)
            # 超范围（1 / 99）钳制回退 3
            for bad in ("1", "99"):
                cfg3 = self._project(
                    "common:\n  language: zh-cn\nbackendSpecific:\n  OpenAI-Compatible:\n    tokens: []\n"
                    f"internals:\n  gendic:\n    context_samples: {bad}\n"
                )
                backend3 = GenDic(cfg3, "GenDic", None, None)
                self.assertEqual(backend3.gendic_context_samples, 3, f"{bad} 应回退 3")

    def test_missing_gendic_section_falls_back(self) -> None:
        cfg = self._project("common:\n  language: zh-cn\n")
        self.assertIsNone(cfg.getKey("internals.gendic.mode"))
        # 缺省时由 GenDic 读取端回退默认值（默认 llm），此处仅验证键缺失不抛错

    def test_han_allowlist_converted_to_set(self) -> None:
        # YAML 列表 → gendic_han_allowlist 集合；无配置时为空集（H 术语不收录默认）
        with patch("GalTransl.Backend.BaseEngine.OpenCC", return_value=MagicMock(convert=lambda s: s)), \
                patch("GalTransl.Backend.GenDic.GenDic.init_chatbot", lambda self, *a, **k: None):
            cfg = self._project(
                "common:\n  language: zh-cn\nbackendSpecific:\n  OpenAI-Compatible:\n    tokens: []\n"
                "internals:\n  gendic:\n    han_allowlist:\n      - 射精\n      - 膣内\n"
            )
            backend = GenDic(cfg, "GenDic", None, None)
            self.assertEqual(backend.gendic_han_allowlist, {"射精", "膣内"})

            cfg2 = self._project(
                "common:\n  language: zh-cn\nbackendSpecific:\n  OpenAI-Compatible:\n    tokens: []\n"
            )
            backend2 = GenDic(cfg2, "GenDic", None, None)
            self.assertEqual(backend2.gendic_han_allowlist, set())


if __name__ == "__main__":
    unittest.main()
