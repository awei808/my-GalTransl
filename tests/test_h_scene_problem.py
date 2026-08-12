"""H 剧情区间「h场景用词不当」检测：仅对 H 区间内译文命中禁词才标记。"""
import importlib
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import yaml

from GalTransl import server as _server_mod


def _start_server(workspace_root: str):
    os.environ["GALTRANSL_WORKSPACE_ROOT"] = workspace_root
    os.environ.pop("GALTRANSL_API_TOKEN", None)
    importlib.reload(_server_mod)
    registry = _server_mod.JobRegistry()
    srv = _server_mod.ThreadingHTTPServer(
        ("127.0.0.1", 0), _server_mod.build_handler(registry)
    )
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp()
        cls.server, cls.port = _start_server(cls.tmp)
        cls.root = cls.tmp

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _req(self, method: str, path: str, body=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode() or "{}")

    def _init_project(self, name: str):
        return self._req("POST", "/api/projects/init", body={"name": name})


class HSceneProblemTests(_Base):
    def _setup_h_project(
        self,
        name: str,
        words: list,
        h_ranges: list,
        enable: bool = True,
        write_batch: bool = True,
    ):
        """初始化项目：覆盖 problemAnalyze 为 h场景用词不当、hCheckDict 指向项目内词库、写 batch.json。"""
        _, init = self._init_project(name)
        pid = init["project_id"]
        pdir = init["project_dir"]
        cfg_path = os.path.join(pdir, "config.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg["problemAnalyze"] = {"problemList": ["h场景用词不当"] if enable else []}
        cfg["dictionary"] = {
            "defaultDictFolder": "Dict",
            "hCheckDict": ["(project_dir)hwords.txt"],
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)
        with open(os.path.join(pdir, "hwords.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(words) + "\n")
        if write_batch:
            # _resolve_cache_h_ranges 要求缓存文件本身存在才解析区间，先写占位
            cache_dir = os.path.join(pdir, "transl_cache", "pass3_cache")
            os.makedirs(cache_dir, exist_ok=True)
            with open(os.path.join(cache_dir, "h.txt.json"), "w", encoding="utf-8") as f:
                json.dump([], f)
            batch_dir = os.path.join(pdir, "transl_cache", "pass2_cache")
            os.makedirs(batch_dir, exist_ok=True)
            with open(os.path.join(batch_dir, "h.txt.json.batch.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {"批次": [{"区间": [lo, hi], "h": True} for lo, hi in h_ranges]},
                    f,
                    ensure_ascii=False,
                )
        return pid

    def _check(self, pid: str, entries: list, filename: str = "pass3_cache/h.txt.json"):
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": filename, "entries": entries},
        )
        self.assertEqual(status, 200)
        return {r["index"]: r["problem"] for r in body["results"]}

    def test_hit_inside_h_range(self) -> None:
        # H 区间内译文含禁词 → 标记 h场景用词不当
        pid = self._setup_h_project("h_hit", ["攀上顶峰", "攀上了顶峰"], [(1, 3)])
        problems = self._check(pid, [
            {"index": 1, "name": "", "pre_src": "x", "post_src": "x", "pre_dst": "我们攀上了顶峰。"},
            {"index": 2, "name": "", "pre_src": "x", "post_src": "x", "pre_dst": "普通台词。"},
        ])
        self.assertIn("h场景用词不当", problems[1])
        self.assertNotIn("h场景用词不当", problems[2])

    def test_no_hit_outside_h_range(self) -> None:
        # 禁词出现在区间外 → 不标记
        pid = self._setup_h_project("h_out", ["攀上顶峰"], [(5, 6)])
        problems = self._check(pid, [
            {"index": 1, "name": "", "pre_src": "x", "post_src": "x", "pre_dst": "我们攀上了顶峰。"},
        ])
        self.assertEqual(problems[1], "")

    def test_hit_via_proofread_dst_only(self) -> None:
        # 仅校对译文（proofread_dst）含禁词、主译文不含 → 仍标记
        pid = self._setup_h_project("h_proof", ["攀上顶峰", "攀上了顶峰"], [(1, 2)])
        problems = self._check(pid, [
            {"index": 1, "name": "", "pre_src": "x", "post_src": "x",
             "pre_dst": "我们走完了全程。", "proofread_dst": "我们攀上了顶峰。"},
        ])
        self.assertIn("h场景用词不当", problems[1])

    def test_hit_via_pre_dst_only(self) -> None:
        # 主译文含禁词、校对译文不含 → 仍标记
        pid = self._setup_h_project("h_pre", ["攀上顶峰", "攀上了顶峰"], [(1, 2)])
        problems = self._check(pid, [
            {"index": 1, "name": "", "pre_src": "x", "post_src": "x",
             "pre_dst": "我们攀上了顶峰。", "proofread_dst": "我们走完了全程。"},
        ])
        self.assertIn("h场景用词不当", problems[1])

    def test_multiple_words_merged_one_problem(self) -> None:
        # 同时命中多个词 → 合并为一条 problem，词以、连接
        pid = self._setup_h_project("h_multi", ["攀上顶峰", "攀上了顶峰"], [(1, 2)])
        problems = self._check(pid, [
            {"index": 1, "name": "", "pre_src": "x", "post_src": "x", "pre_dst": "攀上顶峰，攀上了顶峰。"},
        ])
        self.assertEqual(problems[1].count("h场景用词不当"), 1)
        self.assertIn("攀上顶峰", problems[1])
        self.assertIn("攀上了顶峰", problems[1])

    def test_no_batch_file_skips_detection(self) -> None:
        # batch.json 不存在 → h_ranges 空，即使区间内也不标记
        pid = self._setup_h_project("h_nobatch", ["攀上顶峰"], [(1, 3)], write_batch=False)
        problems = self._check(pid, [
            {"index": 1, "name": "", "pre_src": "x", "post_src": "x", "pre_dst": "攀上了顶峰。"},
        ])
        self.assertEqual(problems[1], "")

    def test_disabled_problem_type_skips_detection(self) -> None:
        # problemList 未配置 h场景用词不当 → 不检测
        pid = self._setup_h_project("h_disabled", ["攀上顶峰"], [(1, 3)], enable=False)
        problems = self._check(pid, [
            {"index": 1, "name": "", "pre_src": "x", "post_src": "x", "pre_dst": "攀上了顶峰。"},
        ])
        self.assertEqual(problems[1], "")

    def test_skip_check_entry_not_checked(self) -> None:
        # skip_check 条目即使命中也不标记
        pid = self._setup_h_project("h_skip", ["攀上顶峰"], [(1, 2)])
        problems = self._check(pid, [
            {"index": 1, "name": "", "pre_src": "x", "post_src": "x",
             "pre_dst": "攀上了顶峰。", "skip_check": True},
        ])
        self.assertEqual(problems[1], "")

    def test_empty_h_dict_skips_detection(self) -> None:
        # 词库为空 → 不标记
        pid = self._setup_h_project("h_empty", [], [(1, 2)])
        problems = self._check(pid, [
            {"index": 1, "name": "", "pre_src": "x", "post_src": "x", "pre_dst": "攀上了顶峰。"},
        ])
        self.assertEqual(problems[1], "")

    def test_recheck_detects_h_word_inside_h_range(self) -> None:
        # 自动重检（recheck_pass3_cache_files）对 H 区间内禁词写回 problem。
        # 回归：recheck 传 cache_name 必须带 pass3_cache/ 前缀，传 basename 会解析不出 H 区间。
        from GalTransl.server import _load_rebuild_deps, recheck_pass3_cache_files

        # 独立项目，避免 _setup_h_project 的占位缓存干扰
        _, init = self._init_project("h_recheck2")
        pdir = init["project_dir"]
        cfg_path = os.path.join(pdir, "config.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg["problemAnalyze"] = {"problemList": ["h场景用词不当"]}
        cfg["dictionary"] = {
            "defaultDictFolder": "Dict",
            "hCheckDict": ["(project_dir)hwords.txt"],
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)
        with open(os.path.join(pdir, "hwords.txt"), "w", encoding="utf-8") as f:
            f.write("攀上顶峰\n攀上了顶峰\n")

        # 写入真实缓存条目（index 1 在 H 区间内，含禁词）+ batch H 区间
        entry = {
            "index": 1, "name": "", "pre_src": "x", "post_src": "x",
            "pre_dst": "我们攀上了顶峰。", "proofread_dst": "",
        }
        cache_path = os.path.join(pdir, "transl_cache", "pass3_cache", "h.txt.json")
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump([entry], f, ensure_ascii=False)
        batch_dir = os.path.join(pdir, "transl_cache", "pass2_cache")
        os.makedirs(batch_dir, exist_ok=True)
        with open(os.path.join(batch_dir, "h.txt.json.batch.json"), "w", encoding="utf-8") as f:
            json.dump({"批次": [{"区间": [1, 3], "h": True}]}, f, ensure_ascii=False)

        deps = _load_rebuild_deps(pdir, "config.yaml")
        proj_config, pre_dic, post_dic, gpt_dic, tPlugins, h_words = deps
        self.assertEqual(h_words, ["攀上顶峰", "攀上了顶峰"])

        # 仅重检目标文件，避开 init 可能创建的其他 pass3 文件
        n = recheck_pass3_cache_files(
            os.path.join(pdir, "transl_cache"),
            proj_config, pre_dic, post_dic, gpt_dic, tPlugins, h_words,
            target_files=[cache_path],
        )
        self.assertEqual(n, 1)
        with open(cache_path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertIn("h场景用词不当", saved[0].get("problem", ""))


class LoadHCheckWordsTests(unittest.TestCase):
    """load_h_check_words 词库解析：取首列、跳过注释/空行/分隔线、去重。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def test_parse_first_column_and_skip_comments(self) -> None:
        from GalTransl.Problem import load_h_check_words

        fp = os.path.join(self.tmp, "words.txt")
        with open(fp, "w", encoding="utf-8") as f:
            f.write("// 注释行\n\n# 注释\n攀上顶峰|H场景中不应使用\n攀上了顶峰\n=====\n普通词|备注\n")
        words = load_h_check_words([fp])
        self.assertEqual(words, ["攀上顶峰", "攀上了顶峰", "普通词"])

    def test_deduplicate_and_missing_file(self) -> None:
        from GalTransl.Problem import load_h_check_words

        fp = os.path.join(self.tmp, "words.txt")
        with open(fp, "w", encoding="utf-8") as f:
            f.write("攀上顶峰\n攀上顶峰\n攀上了顶峰\n")
        words = load_h_check_words([fp, os.path.join(self.tmp, "missing.txt")])
        self.assertEqual(words, ["攀上顶峰", "攀上了顶峰"])


class HCategoryTests(_Base):
    """h 类别贯通：公共字典归类、config key 映射、项目 /dictionary 响应。"""

    def test_common_dict_categorize_h_file(self) -> None:
        from GalTransl.server import _categorize_common_dict_file, _dict_category_config_key

        self.assertEqual(_categorize_common_dict_file("02H场景用词检测.txt"), "h")
        self.assertEqual(_categorize_common_dict_file("项目H场景用词检测.txt"), "h")
        # 不含 h 特征词仍归 pre，避免误伤普通字典
        self.assertEqual(_categorize_common_dict_file("01H字典_矫正_译前.txt"), "pre")
        self.assertEqual(_dict_category_config_key("h"), "hCheckDict")

    def test_project_dictionary_response_has_h_files(self) -> None:
        # 项目配置 hCheckDict → GET /dictionary 返回 h_dict_files 与内容
        _, init = self._req("POST", "/api/projects/init", body={"name": "h_cat2"})
        pdir = init["project_dir"]
        cfg_path = os.path.join(pdir, "config.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg["dictionary"] = {
            "defaultDictFolder": "Dict",
            "hCheckDict": ["(project_dir)hwords.txt"],
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)
        with open(os.path.join(pdir, "hwords.txt"), "w", encoding="utf-8") as f:
            f.write("攀上顶峰\n")
        status, body = self._req("GET", f"/api/projects/{init['project_id']}/dictionary")
        self.assertEqual(status, 200)
        self.assertEqual(body["h_dict_files"], ["(project_dir)hwords.txt"])
        self.assertIn("(project_dir)hwords.txt", body["dict_contents"])


if __name__ == "__main__":
    unittest.main()
