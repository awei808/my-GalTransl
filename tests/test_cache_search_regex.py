"""缓存全项目搜索的正则模式：options.re 开关、非法正则 400、子串模式回归。"""
import unittest

try:
    from test_problem_check import _Base
except ImportError:  # unittest 直跑（tests.test_xxx）时以包路径导入
    from tests.test_problem_check import _Base


class CacheSearchRegexTests(_Base):
    def _write_entries(self, project_dir: str) -> None:
        self._write_cache(
            project_dir,
            "pass3_cache/regex_case.txt.json",
            [
                {"index": 1, "name": "爱丽丝", "pre_src": " numbering 一二一 ",
                 "pre_dst": "第12条命令", "problem": ""},
                {"index": 2, "name": "", "pre_src": "plain text",
                 "pre_dst": "第条命令", "problem": "残留日文"},
            ],
        )

    def _search(self, pid: str, body: dict):
        return self._req("POST", f"/api/projects/{pid}/cache/search", body=body)

    def test_regex_matches_where_literal_does_not(self) -> None:
        _, init = self._init_project("regex_hit")
        self._write_entries(init["project_dir"])
        status, body = self._search(
            init["project_id"],
            {"query": r"第\d+条", "field": "all", "options": {"re": True}},
        )
        self.assertEqual(status, 200)
        # \d+ 只命中"第12条命令"，不命中"第条命令"（字面匹配两者都不命中）
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["results"][0]["pre_dst"], "第12条命令")

    def test_speaker_field_supports_regex(self) -> None:
        _, init = self._init_project("regex_speaker")
        self._write_entries(init["project_dir"])
        status, body = self._search(
            init["project_id"],
            {"query": r"爱.丝", "field": "all", "options": {"re": True}},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 1)

    def test_invalid_regex_returns_400(self) -> None:
        _, init = self._init_project("regex_invalid")
        self._write_entries(init["project_dir"])
        status, body = self._search(
            init["project_id"],
            {"query": "第(\\d+", "field": "all", "options": {"re": True}},
        )
        self.assertEqual(status, 400)
        self.assertIn("正则", body.get("error", ""))

    def test_substring_mode_still_works_without_options(self) -> None:
        _, init = self._init_project("regex_plain")
        self._write_entries(init["project_dir"])
        status, body = self._search(
            init["project_id"],
            {"query": "第12条", "field": "dst"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 1)
        # 正则元数据不被当作模式：字面查找 r"第\d+条" 无命中
        status, body = self._search(
            init["project_id"],
            {"query": r"第\d+条", "field": "dst"},
        )
        self.assertEqual(body["total"], 0)


if __name__ == "__main__":
    unittest.main()
