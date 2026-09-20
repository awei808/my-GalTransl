# -*- coding: utf-8 -*-
"""全局提示词按需注入（0.4.4）单元测试。

覆盖三层：
  1. metadata.select_global_characters 纯函数：三级匹配（精确/别名/子串兜底）、
     单字防误伤、无法确定相关集时回退 None；
  2. ForGlobalPrompt._format_global_prompt_as_context 的 characters 参数
     （None=全量 / 子集 / 空列表省略角色段）；
  3. 链路集成：翻译轮首轮（lazy）按需注入、开关关闭回退全量、零命中回退全量、
     修复轮首轮同样按需、批次划分（direct 链路）保持全量。
"""
import unittest
from types import SimpleNamespace

from GalTransl.Backend.ForGlobalPrompt import _format_global_prompt_as_context
from GalTransl.Backend.ForJPResidue import ForJPResidue
from GalTransl.Backend.context import format_global_prompt_with_route_direct
from GalTransl.Backend.metadata import FileMetaData, select_global_characters
from GalTransl.Backend.ForGalJsonTranslate import ForGalJsonTranslate
from GalTransl.CSentense import CSentense


def make_gp() -> dict:
    """三个角色的全局分析样例：凛音（精确命中）、创（别名 list）、校长（不应注入）。"""
    return {
        "剧情概述": "全局剧情概述",
        "角色列表": [
            {"名称": "凛音", "形象": "凛音形象", "语气": "", "说话风格": "", "关系": ""},
            {"名称": ["创", "ソラ"], "形象": "创形象", "语气": "", "说话风格": "", "关系": ""},
            {"名称": "校长", "形象": "校长形象", "语气": "", "说话风格": "", "关系": ""},
        ],
        "世界观设定": "世界观文本",
        "行文风格": "行文风格文本",
        "题材标签": ["学园"],
    }


def make_md(character) -> FileMetaData:
    return FileMetaData(id="f.json", character=character, costume="", plot="", tags=[])


class SelectGlobalCharactersTests(unittest.TestCase):
    """select_global_characters：三级匹配与回退约定。"""

    def test_exact_match_keeps_gp_order(self) -> None:
        selected = select_global_characters(make_gp()["角色列表"], make_md(["校长", "凛音"]))
        self.assertEqual(
            [ch["名称"] for ch in selected], ["凛音", "校长"]
        )

    def test_alias_list_match(self) -> None:
        selected = select_global_characters(make_gp()["角色列表"], make_md(["ソラ"]))
        self.assertEqual([ch["形象"] for ch in selected], ["创形象"])

    def test_substring_fallback_on_zero_exact_hit(self) -> None:
        gp = [{"名称": "远坂凛音", "形象": "全名形象"}]
        selected = select_global_characters(gp, make_md(["凛音"]))
        self.assertEqual([ch["形象"] for ch in selected], ["全名形象"])

    def test_single_char_not_substring_matched(self) -> None:
        # 防单字误伤：文件名单「春」不得因子串兜底命中「春子」
        gp = [{"名称": "春子", "形象": "春子形象"}]
        self.assertIsNone(select_global_characters(gp, make_md(["春"])))

    def test_casefold_match_for_latin_names(self) -> None:
        gp = [{"名称": "Rin", "形象": "Rin形象"}]
        selected = select_global_characters(gp, make_md(["rin"]))
        self.assertEqual([ch["形象"] for ch in selected], ["Rin形象"])

    def test_no_file_roles_returns_none(self) -> None:
        self.assertIsNone(select_global_characters(make_gp()["角色列表"], make_md([])))

    def test_empty_or_invalid_gp_characters_returns_none(self) -> None:
        self.assertIsNone(select_global_characters([], make_md(["凛音"])))
        self.assertIsNone(select_global_characters("not-a-list", make_md(["凛音"])))
        self.assertIsNone(select_global_characters([{"形象": "无名"}], make_md(["凛音"])))

    def test_zero_hit_returns_none(self) -> None:
        self.assertIsNone(
            select_global_characters(make_gp()["角色列表"], make_md(["路人甲"]))
        )


class FormatGlobalPromptCharactersParamTests(unittest.TestCase):
    """_format_global_prompt_as_context 的 characters 参数。"""

    def test_none_keeps_full_characters(self) -> None:
        text = _format_global_prompt_as_context(make_gp())
        for profile in ("凛音形象", "创形象", "校长形象"):
            self.assertIn(profile, text)

    def test_subset_renders_only_selected(self) -> None:
        gp = make_gp()
        selected = select_global_characters(gp["角色列表"], make_md(["凛音"]))
        text = _format_global_prompt_as_context(gp, characters=selected)
        self.assertIn("凛音形象", text)
        self.assertNotIn("校长形象", text)
        # 非角色段不受影响
        self.assertIn("全局剧情概述", text)
        self.assertIn("世界观文本", text)

    def test_empty_list_omits_character_section(self) -> None:
        text = _format_global_prompt_as_context(make_gp(), characters=[])
        self.assertNotIn("# 角色设定", text)


def make_engine():
    """打桩翻译后端实例：不触发真实初始化，GlobalPrompt/路线图预置为已加载。"""
    t = ForGalJsonTranslate.__new__(ForGalJsonTranslate)
    t.pj_config = SimpleNamespace(
        active_workers=0,
        stop_event=None,
        translation_guideline="",
        global_prompt=None,
        getProjectDir=lambda: "",
        getKey=lambda key, default=None: default,
    )
    t.eng_type = "ForGal-json-translate"
    t.system_prompt = "SYSTEM"
    t.trans_prompt = "[global_prompt]\n[plot_metadata]\n[Input]"
    t.source_lang = "Japanese"
    t.target_lang = "English"
    t.conversations = {}
    t._force_first_round_files = set()
    t.file_metadata_map = {}
    t._file_metadata_by_file = {}
    t._file_metadata_loaded = True
    t.project_config = None
    t.last_translations = {}
    t.batch_metadata_map = {}
    t._batch_metadata_by_file = {}
    t._batch_metadata_loaded = True
    t._global_prompt = make_gp()
    t._global_prompt_loaded = True
    t._plot_route_map = None
    t._plot_route_map_loaded = True
    return t


def make_first_round_content(t, filename="f.json") -> str:
    trans = CSentense("原文", index=0)
    _, _, _, input_src = t._build_input_jsonlines([trans], False, filename)
    t.conversations[filename] = [{"role": "system", "content": t.system_prompt}]
    return t._build_round_user_content(
        t.conversations[filename], input_src, "", filename, is_first_round=True
    )


class TranslateRoundSelectiveTests(unittest.TestCase):
    """翻译轮首轮（lazy 链路）：按需注入与各回退场景。"""

    def test_first_round_injects_only_matched_profiles(self) -> None:
        t = make_engine()
        t.file_metadata_map = {"f.json": make_md(["凛音"])}
        content = make_first_round_content(t)
        self.assertIn("凛音形象", content)
        self.assertNotIn("校长形象", content)
        self.assertIn("世界观文本", content)

    def test_switch_off_falls_back_to_full(self) -> None:
        t = make_engine()
        t.pj_config.getKey = lambda key, default=None: (
            False if key == "internals.globalprompt.selective_characters" else default
        )
        t.file_metadata_map = {"f.json": make_md(["凛音"])}
        content = make_first_round_content(t)
        self.assertIn("校长形象", content)

    def test_no_file_metadata_falls_back_to_full(self) -> None:
        t = make_engine()
        content = make_first_round_content(t)
        self.assertIn("校长形象", content)

    def test_zero_hit_falls_back_to_full(self) -> None:
        t = make_engine()
        t.file_metadata_map = {"f.json": make_md(["路人甲"])}
        content = make_first_round_content(t)
        self.assertIn("校长形象", content)

    def test_no_global_prompt_returns_empty_block(self) -> None:
        t = make_engine()
        t._global_prompt = None
        t.file_metadata_map = {"f.json": make_md(["凛音"])}
        content = make_first_round_content(t)
        self.assertNotIn("凛音形象", content)


class FixRoundSelectiveTests(unittest.TestCase):
    """修复轮首轮：与翻译轮共用 lazy 链路，同样按需注入。"""

    def _make_fix_engine(self):
        t = ForJPResidue.__new__(ForJPResidue)
        t.pj_config = SimpleNamespace(
            active_workers=0,
            stop_event=None,
            translation_guideline="",
            global_prompt=None,
            getProjectDir=lambda: "",
            getKey=lambda key, default=None: default,
        )
        t.eng_type = "ForJPResidue"
        t.system_prompt = "SYSTEM"
        t.trans_prompt = "[global_prompt]\n[plot_metadata]\n[Input]"
        t.source_lang = "Japanese"
        t.target_lang = "English"
        t.conversations = {}
        t.file_metadata_map = {"f.json": make_md(["凛音"])}
        t._file_metadata_by_file = {}
        t._file_metadata_loaded = True
        t.project_config = None
        t.last_translations = {}
        t._global_prompt = make_gp()
        t._global_prompt_loaded = True
        t._plot_route_map = None
        t._plot_route_map_loaded = True
        return t

    def test_fix_round_first_content_injects_only_matched(self) -> None:
        t = self._make_fix_engine()
        content = t._build_first_round_content("INPUT_SRC", "", "f.json")
        self.assertIn("凛音形象", content)
        self.assertNotIn("校长形象", content)


class BatchMetaStaysFullTests(unittest.TestCase):
    """批次划分（direct 链路）不参与按需筛选：即使有匹配元数据也保持全量。"""

    def test_direct_path_keeps_all_characters(self) -> None:
        t = make_engine()
        t.file_metadata_map = {"f.json": make_md(["凛音"])}
        # direct 路线块：无 getCachePath 的桩配置会异常并被吞掉，返回空路线块
        block = format_global_prompt_with_route_direct(t, "BatchMetaData", "f.json")
        self.assertIn("凛音形象", block)
        self.assertIn("校长形象", block)


if __name__ == "__main__":
    unittest.main()
