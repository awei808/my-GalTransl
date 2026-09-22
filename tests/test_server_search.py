"""server_search 检索层单测（0.5.1 MCP 外部 agent 接入）。"""
import json
import os
import re
import tempfile
import unittest

from GalTransl.server_search import (
    DEFAULT_MAX_RESULTS,
    HARD_MAX_RESULTS,
    _clamp_max_results,
    search_cache_entries,
    search_dict_entries,
    search_source_scripts,
)


def _write_json(path: str, payload: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class ClampMaxResultsTests(unittest.TestCase):
    def test_zero_and_negative_yield_zero(self) -> None:
        self.assertEqual(_clamp_max_results(0), 0)
        self.assertEqual(_clamp_max_results(-5), 0)

    def test_invalid_value_falls_back_to_default(self) -> None:
        self.assertEqual(_clamp_max_results("abc"), DEFAULT_MAX_RESULTS)
        self.assertEqual(_clamp_max_results(None), DEFAULT_MAX_RESULTS)
        self.assertEqual(_clamp_max_results(""), DEFAULT_MAX_RESULTS)

    def test_hard_cap_applies(self) -> None:
        self.assertEqual(_clamp_max_results(99999), HARD_MAX_RESULTS)
        self.assertEqual(_clamp_max_results(50), 50)
        self.assertEqual(_clamp_max_results("50"), 50)


class SearchCacheEntriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = self._tmp.name
        entries = [
            {"index": 0, "pre_src": "こんにちは", "pre_dst": "你好", "name": "創"},
            {"index": 1, "pre_src": "さようなら", "pre_dst": "再见", "problem": "残留日文"},
            {"index": 2, "names": ["凛音", "りんね"], "pre_src": "おはよう"},
            {"index": 3, "pre_src": "テスト1"},
            {"index": 4, "pre_src": "テスト2"},
            {"index": 5, "pre_src": "テスト3"},
        ]
        _write_json(os.path.join(self.project, "transl_cache", "test.json"), entries)
        # 元数据（pass1 *.meta.json）不得进入检索范围
        _write_json(
            os.path.join(self.project, "transl_cache", "pass1_cache", "a.meta.json"),
            [{"pre_src": "こんにちは"}],
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_scope_and_source_hit(self) -> None:
        result = search_cache_entries(self.project, "こんにちは")
        self.assertEqual(result["scope"], "cache")
        self.assertEqual(result["total"], 1)
        self.assertTrue(result["results"][0]["match_src"])

    def test_metadata_files_are_excluded(self) -> None:
        result = search_cache_entries(self.project, "こんにちは")
        self.assertEqual([item["filename"] for item in result["results"]], ["test.json"])

    def test_field_dst_filters_source_hits(self) -> None:
        self.assertEqual(search_cache_entries(self.project, "你好", field="dst")["total"], 1)
        self.assertEqual(search_cache_entries(self.project, "你好", field="src")["total"], 0)

    def test_field_problem(self) -> None:
        result = search_cache_entries(self.project, "残留", field="problem")
        self.assertEqual(result["total"], 1)
        self.assertTrue(result["results"][0]["match_problem"])

    def test_names_list_matched_while_speaker_keeps_raw_value(self) -> None:
        result = search_cache_entries(self.project, "りんね")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["speaker"], ["凛音", "りんね"])
        self.assertTrue(result["results"][0]["match_speaker"])

    def test_regex_mode(self) -> None:
        self.assertEqual(search_cache_entries(self.project, "^こ", use_regex=True)["total"], 1)

    def test_invalid_regex_raises_re_error(self) -> None:
        with self.assertRaises(re.error):
            search_cache_entries(self.project, "(", use_regex=True)

    def test_empty_query_returns_empty_result(self) -> None:
        result = search_cache_entries(self.project, "   ")
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["results"], [])
        self.assertFalse(result["truncated"])

    def test_truncation_flag_and_totals(self) -> None:
        result = search_cache_entries(self.project, "テスト", max_results=2)
        self.assertEqual(result["total"], 3)
        self.assertEqual(len(result["results"]), 2)
        self.assertTrue(result["truncated"])

    def test_max_results_zero_returns_no_rows(self) -> None:
        result = search_cache_entries(self.project, "テスト", max_results=0)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["results"], [])

    def test_missing_cache_dir_is_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as empty_project:
            result = search_cache_entries(empty_project, "こんにちは")
            self.assertEqual(result["total"], 0)

    def test_broken_cache_file_is_skipped_not_raised(self) -> None:
        _write_text(os.path.join(self.project, "transl_cache", "broken.json"), "{ not json")
        result = search_cache_entries(self.project, "こんにちは")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["skipped_files"], 1)


class SearchSourceScriptsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = self._tmp.name
        scripts = [
            {"message": "俺は夢を見ているのだろうか。"},
            {"name": "創", "message": "「クルト……いるんでしょ……？」"},
            {"message": "テスト行"},
        ]
        _write_json(os.path.join(self.project, "gt_input", "00_01_導入.txt.json"), scripts)
        _write_json(
            os.path.join(self.project, "gt_input", "sub", "10_02.txt.json"),
            [{"message": "サブディレクトリの行"}],
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_message_hit_keeps_array_index(self) -> None:
        result = search_source_scripts(self.project, "夢を見て")
        self.assertEqual(result["scope"], "script")
        self.assertEqual(result["total"], 1)
        item = result["results"][0]
        self.assertEqual(item["filename"], "00_01_導入.txt.json")
        self.assertEqual(item["index"], 0)
        self.assertTrue(item["match_message"])

    def test_speaker_hit_reports_index_two(self) -> None:
        result = search_source_scripts(self.project, "創")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["index"], 1)
        self.assertEqual(result["results"][0]["speaker"], "創")

    def test_nested_directory_is_scanned(self) -> None:
        result = search_source_scripts(self.project, "サブディレクトリ")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["filename"], "sub/10_02.txt.json")

    def test_works_without_cache(self) -> None:
        self.assertFalse(os.path.isdir(os.path.join(self.project, "transl_cache")))
        self.assertEqual(search_source_scripts(self.project, "テスト")["total"], 1)

    def test_regex_mode(self) -> None:
        self.assertEqual(search_source_scripts(self.project, "^俺は", use_regex=True)["total"], 1)


class SearchDictEntriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = self._tmp.name
        # 注入不存在的公共字典目录：本类只覆盖项目字典，避免读到真实环境 Dict/
        self.common_dir = os.path.join(self.project, "_no_such_common_dict")
        _write_text(
            os.path.join(self.project, "config.yaml"),
            "dictionary:\n"
            "  preDict:\n"
            "  - (project_dir)项目字典_译前.txt\n"
            "  gpt.dict:\n"
            "  - (project_dir)项目GPT字典.txt\n"
            "  - (project_dir)项目GPT字典_h.txt\n"
            "  postDict:\n"
            "  - (project_dir)项目字典_译后.txt\n"
            "  forbiddenDictH:\n"
            "  - (project_dir)项目禁用词_h.txt\n"
            "  forbiddenDictNonH:\n"
            "  - (project_dir)项目禁用词_非h.txt\n",
        )
        _write_text(
            os.path.join(self.project, "项目字典_译前.txt"),
            "こんにちは|你好\n"
            "// こんにちは|注释行不应命中\n",
        )
        _write_text(os.path.join(self.project, "项目GPT字典.txt"), "テスト|测试|备注\n")
        _write_text(os.path.join(self.project, "项目GPT字典_h.txt"), "きもち|心情\n")
        _write_text(os.path.join(self.project, "项目字典_译后.txt"), "ありがとう|谢谢\n")
        _write_text(os.path.join(self.project, "项目禁用词_h.txt"), "えっち|禁用词h\n")
        _write_text(os.path.join(self.project, "项目禁用词_非h.txt"), "ばか|禁用词非h\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _search(self, query: str, **kwargs: object) -> dict:
        kwargs.setdefault("common_dict_dir", self.common_dir)
        return search_dict_entries(self.project, query, **kwargs)

    def test_pre_dict_hit_carries_src_and_dst(self) -> None:
        result = self._search("こんにちは")
        self.assertEqual(result["scope"], "dict")
        self.assertEqual(result["total"], 1)
        item = result["results"][0]
        self.assertEqual(item["origin"], "project")
        self.assertEqual(item["file"], "项目字典_译前.txt")
        self.assertEqual(item["category"], "pre")
        self.assertEqual(item["src"], "こんにちは")
        self.assertEqual(item["dst"], "你好")
        self.assertEqual(item["line_no"], 1)

    def test_comment_line_is_not_matched(self) -> None:
        result = self._search("注释行")
        self.assertEqual(result["total"], 0)

    def test_gpt_dict_nh_category_and_note(self) -> None:
        result = self._search("テスト")
        self.assertEqual(result["total"], 1)
        item = result["results"][0]
        # 项目 GPT 字典按文件名后缀细分 h/非h；无 _h 后缀即归非h
        self.assertEqual(item["category"], "gptnh")
        self.assertEqual(item["row_type"], "gpt")
        self.assertEqual(item["note"], "备注")

    def test_gpt_dict_h_category(self) -> None:
        result = self._search("きもち")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["category"], "gpth")

    def test_post_dict_category(self) -> None:
        result = self._search("ありがとう")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["category"], "post")

    def test_forbidden_h_category(self) -> None:
        result = self._search("えっち")
        self.assertEqual(result["total"], 1)
        item = result["results"][0]
        self.assertEqual(item["category"], "forbiddenh")
        self.assertEqual(item["row_type"], "forbidden")

    def test_forbidden_non_h_category(self) -> None:
        result = self._search("ばか")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["category"], "forbiddennh")

    def test_direction_zh2jp_matches_dst_only(self) -> None:
        self.assertEqual(self._search("你好", direction="zh2jp")["total"], 1)
        self.assertEqual(self._search("你好", direction="jp2zh")["total"], 0)

    def test_direction_any_matches_both_ways(self) -> None:
        self.assertEqual(self._search("你好")["total"], 1)
        self.assertEqual(self._search("こんにちは")["total"], 1)

    def test_invalid_direction_raises(self) -> None:
        with self.assertRaises(ValueError):
            self._search("你好", direction="zh2en")

    def test_missing_config_raises_file_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as empty_project:
            with self.assertRaises(FileNotFoundError):
                search_dict_entries(empty_project, "你好")

    def test_empty_query_returns_empty_result(self) -> None:
        result = self._search("")
        self.assertEqual(result["total"], 0)
        self.assertFalse(result["truncated"])

    def test_truncation_flag(self) -> None:
        result = self._search("こんにちは", max_results=0)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"], [])
        self.assertTrue(result["truncated"])


class CommonDictSearchTests(unittest.TestCase):
    """公共 Dict/ 目录检索：分类由文件名推断，结果 origin 标记为 common。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = self._tmp.name
        self.common_dir = os.path.join(self.project, "Dict")
        _write_text(os.path.join(self.project, "config.yaml"), "dictionary:\n  preDict: []\n")
        _write_text(os.path.join(self.common_dir, "00通用字典_译前.txt"), "せんぱい|前辈\n")
        _write_text(os.path.join(self.common_dir, "项目禁用词_h.txt"), "えっち|禁止\n")
        _write_text(os.path.join(self.common_dir, "10GPT字典.txt"), "テスト|测试\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_common_dict_hit_reports_origin_common(self) -> None:
        result = search_dict_entries(self.project, "せんぱい", common_dict_dir=self.common_dir)
        self.assertEqual(result["total"], 1)
        item = result["results"][0]
        self.assertEqual(item["origin"], "common")
        self.assertEqual(item["file"], "00通用字典_译前.txt")
        self.assertEqual(item["category"], "pre")

    def test_common_dict_category_inferred_from_filename(self) -> None:
        self.assertEqual(
            search_dict_entries(self.project, "えっち", common_dict_dir=self.common_dir)["results"][0]["category"],
            "forbiddenh",
        )
        self.assertEqual(
            search_dict_entries(self.project, "テスト", common_dict_dir=self.common_dir)["results"][0]["category"],
            "gptnh",
        )

    def test_include_common_false_skips_common_dir(self) -> None:
        result = search_dict_entries(
            self.project,
            "せんぱい",
            include_common=False,
            common_dict_dir=self.common_dir,
        )
        self.assertEqual(result["total"], 0)

    def test_missing_common_dir_is_tolerated(self) -> None:
        result = search_dict_entries(
            self.project,
            "せんぱい",
            common_dict_dir=os.path.join(self.project, "no_such_dict_dir"),
        )
        self.assertEqual(result["total"], 0)

    def test_common_dir_absent_does_not_create_directory(self) -> None:
        absent = os.path.join(self.project, "absent_dict_dir")
        search_dict_entries(self.project, "せんぱい", common_dict_dir=absent)
        self.assertFalse(os.path.isdir(absent))


if __name__ == "__main__":
    unittest.main()
