"""元数据文本检索（server_search.search_metadata）的单测。

覆盖：pass1/pass2 遍历与命中、scope 过滤、空目录/空查询的空结果、
非法 scope 报错、max_results 截断与 matched_files 汇总。
"""
import json
import os
import tempfile
import unittest


class SearchMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        from GalTransl.server_search import search_metadata

        self.search_metadata = search_metadata
        self.project_dir = tempfile.mkdtemp(prefix="gt_meta_search_")
        pass1 = os.path.join(self.project_dir, "transl_cache", "pass1_cache")
        pass2 = os.path.join(self.project_dir, "transl_cache", "pass2_cache")
        os.makedirs(pass1)
        os.makedirs(pass2)
        with open(os.path.join(pass1, "01_school.meta.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "id": "01_school.json",
                    "角色": ["白河琴美", "男主"],
                    "服装": "校服",
                    "剧情": "男主在图书馆与琴美相遇，两人讨论起古典音乐的往事。",
                    "标签": ["日常", "音乐"],
                    "称呼映射": [{"被称呼者": "琴美", "原文": "琴美さん", "译文": "琴美同学"}],
                },
                f,
                ensure_ascii=False,
            )
        with open(os.path.join(pass1, "02_confession.meta.json"), "w", encoding="utf-8") as f:
            json.dump(
                {"id": "02_confession.json", "角色": ["琴美"], "剧情": "琴美在天台告白。", "标签": ["情感"]},
                f,
                ensure_ascii=False,
            )
        with open(os.path.join(pass2, "01_school.batch.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "id": "01_school.json",
                    "批次": [
                        {"区间": [1, 30], "视角": "男主", "氛围": "平静", "h": 0.0, "用词色彩": "日常"},
                        {"区间": [31, 60], "视角": "琴美", "氛围": "忧伤", "h": 0.2, "用词色彩": "文艺"},
                    ],
                },
                f,
                ensure_ascii=False,
            )

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.project_dir, ignore_errors=True)

    def test_matches_filemeta_plot_field_and_reports_matched_files(self) -> None:
        result = self.search_metadata(self.project_dir, "图书馆")
        self.assertEqual(result["matched_files"], ["01_school.json"])
        self.assertTrue(any(m["field"] == "剧情" and "图书馆" in m["snippet"] for m in result["matches"]))
        # scope=all 扫描 pass1 全部 2 个 + pass2 全部 1 个
        self.assertEqual(result["total_files"], 3)

    def test_scope_batchmeta_only_searches_pass2(self) -> None:
        result = self.search_metadata(self.project_dir, "忧伤", scope="batchmeta")
        self.assertEqual(result["matched_files"], ["01_school.json"])
        self.assertTrue(all(m["type"] == "batchmeta" for m in result["matches"]))
        # 文件元数据中不存在「忧伤」
        self.assertEqual(self.search_metadata(self.project_dir, "忧伤", scope="filemeta")["matches"], [])

    def test_scope_filemeta_excludes_batchmeta(self) -> None:
        result = self.search_metadata(self.project_dir, "琴美", scope="filemeta")
        self.assertEqual(result["matched_files"], ["01_school.json", "02_confession.json"])
        self.assertTrue(all(m["type"] == "filemeta" for m in result["matches"]))

    def test_nested_list_dict_fields_are_searchable(self) -> None:
        result = self.search_metadata(self.project_dir, "琴美さん")
        self.assertTrue(any("称呼映射" in m["field"] for m in result["matches"]))
        result2 = self.search_metadata(self.project_dir, "文艺", scope="batchmeta")
        self.assertTrue(any("批次" in m["field"] for m in result2["matches"]))

    def test_missing_directories_return_empty_result(self) -> None:
        empty_dir = tempfile.mkdtemp(prefix="gt_meta_empty_")
        try:
            result = self.search_metadata(empty_dir, "任何词")
            self.assertEqual(result["matches"], [])
            self.assertEqual(result["total_files"], 0)
        finally:
            import shutil

            shutil.rmtree(empty_dir, ignore_errors=True)

    def test_empty_query_returns_empty_result(self) -> None:
        result = self.search_metadata(self.project_dir, "  ")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["matched_files"], [])

    def test_invalid_scope_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            self.search_metadata(self.project_dir, "词", scope="globalprompt")

    def test_max_results_truncates(self) -> None:
        result = self.search_metadata(self.project_dir, "琴美", max_results=2)
        self.assertEqual(len(result["matches"]), 2)
        self.assertTrue(result["truncated"])

    def test_non_dict_entry_is_skipped_without_error(self) -> None:
        pass1 = os.path.join(self.project_dir, "transl_cache", "pass1_cache")
        with open(os.path.join(pass1, "03_bad.meta.json"), "w", encoding="utf-8") as f:
            f.write("[]")
        result = self.search_metadata(self.project_dir, "琴美")
        self.assertEqual(result["matched_files"], ["01_school.json", "02_confession.json"])


if __name__ == "__main__":
    unittest.main()
