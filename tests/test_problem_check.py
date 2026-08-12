"""问题检测：POST /api/projects/:id/cache/check（只检测不落盘）与 cache/save rebuild 的 problem 保护。"""
import importlib
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import yaml

from GalTransl import server as _server_mod


def _start_server(workspace_root: str, token: str = ""):
    os.environ["GALTRANSL_WORKSPACE_ROOT"] = workspace_root
    if token:
        os.environ["GALTRANSL_API_TOKEN"] = token
    else:
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

    def _write_cache(self, project_dir: str, rel: str, entries: list) -> str:
        import orjson

        fp = os.path.join(project_dir, "transl_cache", rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "wb") as f:
            f.write(orjson.dumps(entries, option=orjson.OPT_INDENT_2))
        return fp


class CacheCheckTests(_Base):
    def test_cache_check_detects_residual_japanese(self) -> None:
        # 默认配置启用"残留日文"：译文含假名时返回对应 problem；skip_check 条目不参与检测
        _, init = self._init_project("cc_jp")
        pid = init["project_id"]
        entries = [
            {"index": 1, "name": "", "pre_src": "测试", "post_src": "测试", "pre_dst": "これはテストです"},
            {"index": 2, "name": "", "pre_src": "你好", "post_src": "你好", "pre_dst": "你好", "skip_check": True},
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/cc.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        results = {r["index"]: r for r in body["results"]}
        self.assertIn("残留日文", results[1]["problem"])
        self.assertEqual(results[2]["problem"], "")

    def test_cache_check_invalid_path_rejected(self) -> None:
        _, init = self._init_project("cc_bad")
        pid = init["project_id"]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "../evil.json", "entries": []},
        )
        self.assertEqual(status, 400)

    def test_cache_check_config_missing_returns_failure(self) -> None:
        # 配置缺失时返回 success=False 且不崩溃
        _, init = self._init_project("cc_nocfg2")
        pid = init["project_id"]
        pdir = init["project_dir"]
        os.remove(os.path.join(pdir, "config.yaml"))
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/x.json", "entries": []},
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["success"])
        self.assertEqual(body["results"], [])

    def test_cache_check_empty_problem_list_skips_detection(self) -> None:
        # problemList 显式配置为空列表 → 不检测任何问题（不回退旧版 GPT35 段）
        _, init = self._init_project("cc_empty")
        pid = init["project_id"]
        pdir = init["project_dir"]
        cfg_path = os.path.join(pdir, "config.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg["problemAnalyze"] = {"problemList": []}
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)
        entries = [
            {"index": 1, "name": "", "pre_src": "测试", "post_src": "测试", "pre_dst": "これはテストです"}
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/x.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        results = {r["index"]: r for r in body["results"]}
        self.assertEqual(results[1]["problem"], "")

    def test_cache_check_legacy_gpt35_fallback(self) -> None:
        # problemList 键缺失、但存在旧版 GPT35 段 → 回退按 GPT35 清单检测
        _, init = self._init_project("cc_legacy")
        pid = init["project_id"]
        pdir = init["project_dir"]
        cfg_path = os.path.join(pdir, "config.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg["problemAnalyze"] = {"GPT35": ["残留日文"]}
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)
        entries = [
            {"index": 1, "name": "", "pre_src": "测试", "post_src": "测试", "pre_dst": "これはテストです"}
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/x.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        results = {r["index"]: r for r in body["results"]}
        self.assertIn("残留日文", results[1]["problem"])


class CacheSaveProblemProtectionTests(_Base):
    def test_cache_save_preserves_problem_when_config_missing(self) -> None:
        # 配置缺失 → rebuild 跳过，已有 problem 不得被删除
        _, init = self._init_project("cc_nocfg")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "pass3_cache/cc.txt.json"
        entries = [
            {"index": 1, "name": "", "pre_src": "测试", "post_src": "测试", "pre_dst": "これはテストです", "problem": "残留日文"}
        ]
        self._write_cache(pdir, rel, entries)
        os.remove(os.path.join(pdir, "config.yaml"))
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/save",
            body={"filename": rel, "entries": entries},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        with open(os.path.join(pdir, "transl_cache", rel), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved[0]["problem"], "残留日文")

    def test_cache_save_preserves_problem_when_detection_fails(self) -> None:
        # find_problems 抛异常 → 保留已有 problem，不误删
        _, init = self._init_project("cc_fail")
        pid = init["project_id"]
        pdir = init["project_dir"]
        rel = "pass3_cache/cc.txt.json"
        entries = [
            {"index": 1, "name": "", "pre_src": "测试", "post_src": "测试", "pre_dst": "これはテストです", "problem": "残留日文"}
        ]
        self._write_cache(pdir, rel, entries)
        with mock.patch("GalTransl.Problem.find_problems", side_effect=RuntimeError("boom")):
            status, body = self._req(
                "POST",
                f"/api/projects/{pid}/cache/save",
                body={"filename": rel, "entries": entries},
            )
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        with open(os.path.join(pdir, "transl_cache", rel), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved[0]["problem"], "残留日文")


class NewlineDetectionTests(_Base):
    def test_newline_count_mixed(self) -> None:
        # 真实 \r\n/\n/\r 与字面转义均计 1 次，CRLF 不重复计
        from GalTransl.Problem import _newline_count

        self.assertEqual(_newline_count("a\r\nb"), 1)
        self.assertEqual(_newline_count("a\nb\nc"), 2)
        self.assertEqual(_newline_count("a\\r\\nb"), 1)
        self.assertEqual(_newline_count("a\\nb"), 1)
        self.assertEqual(_newline_count("a\rb"), 1)
        self.assertEqual(_newline_count("a\r\nb\nc\\n"), 3)

    def test_cache_check_detects_extra_newlines_crlf_src_lf_dst(self) -> None:
        # 原文 CRLF、译文 LF 且换行更多：应报"多加换行"（修复前因换行符类型不同而漏报）
        _, init = self._init_project("nl_extra")
        pid = init["project_id"]
        entries = [
            {
                "index": 1,
                "name": "",
                "pre_src": "実在するモデルを使って、どうポーズを取らせれば、\r\nより魅力的に見せることが出来るのか。",
                "post_src": "実在するモデルを使って、どうポーズを取らせれば、\r\nより魅力的に見せることが出来るのか。",
                "pre_dst": "怎样让真实的模特摆出姿势，\n\n\n才能看起来更有魅力。\n",
            }
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/nl.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        results = {r["index"]: r for r in body["results"]}
        self.assertIn("多加换行", results[1]["problem"])

    def test_cache_check_no_newline_false_positive_same_count(self) -> None:
        # 原文与译文换行数量一致（符号类型不同）：不应报换行问题
        _, init = self._init_project("nl_same")
        pid = init["project_id"]
        entries = [
            {"index": 1, "name": "", "pre_src": "A\r\nB", "post_src": "A\r\nB", "pre_dst": "甲\n乙"}
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/nl.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        results = {r["index"]: r for r in body["results"]}
        self.assertNotIn("多加换行", results[1]["problem"])
        self.assertNotIn("丢失换行", results[1]["problem"])


class LongSentenceNewlineTests(_Base):
    """长句丢失换行：平均分句长度超过 avgSentenceLengthThreshold 才报。"""

    def _set_problem_config(self, project_dir: str, problems: list, threshold: int = 17) -> None:
        cfg_path = os.path.join(project_dir, "config.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg["problemAnalyze"] = {
            "problemList": problems,
            "avgSentenceLengthThreshold": threshold,
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)

    def test_long_sentence_triggers_when_avg_exceeds_threshold(self) -> None:
        # 译文无换行且整句超长（avg=整句长度 > 17）→ 报"长句丢失换行"
        _, init = self._init_project("ls_long")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["长句丢失换行"], 17)
        entries = [
            {
                "index": 1,
                "name": "",
                "pre_src": "原文第一行\\n原文第二行",
                "post_src": "原文第一行\\n原文第二行",
                "pre_dst": "这是一句非常非常长的中文句子它把所有内容都压缩在一起没有任何换行完全看不出原来的段落结构",
            }
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/ls.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        results = {r["index"]: r for r in body["results"]}
        self.assertIn("长句丢失换行", results[1]["problem"])

    def test_short_sentence_not_triggers(self) -> None:
        # 有换行且平均分句长度 ≤ 阈值 → 不报
        _, init = self._init_project("ls_short")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["长句丢失换行"], 17)
        entries = [
            {
                "index": 1,
                "name": "",
                "pre_src": "原文第一行\\n原文第二行\\n原文第三行",
                "post_src": "原文第一行\\n原文第二行\\n原文第三行",
                "pre_dst": "短句。\\n短句。\\n短句。",
            }
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/ls.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        results = {r["index"]: r for r in body["results"]}
        self.assertNotIn("长句丢失换行", results[1]["problem"])

    def test_no_newline_in_src_skips_detection(self) -> None:
        # 原文无换行 → 门控跳过，即使译文超长也不报
        _, init = self._init_project("ls_gate")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["长句丢失换行"], 17)
        entries = [
            {
                "index": 1,
                "name": "",
                "pre_src": "原文根本没有换行",
                "post_src": "原文根本没有换行",
                "pre_dst": "这是一句非常非常长的中文句子它把所有内容都压缩在一起没有任何换行完全看不出原来的段落结构",
            }
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/ls.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        results = {r["index"]: r for r in body["results"]}
        self.assertNotIn("长句丢失换行", results[1]["problem"])

    def test_real_newline_in_src_triggers(self) -> None:
        # 真实换行（\r\n 控制符）同样触发：译成一行超长句
        _, init = self._init_project("ls_real")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["长句丢失换行"], 10)
        entries = [
            {
                "index": 1,
                "name": "",
                "pre_src": "コスタリアは無人島を\r\n丸ごと開発して作られた\r\n日本最大の離島コスプレリゾートだ。",
                "post_src": "コスタリアは無人島を\r\n丸ごと開発して作られた\r\n日本最大の離島コスプレリゾートだ。",
                "pre_dst": "既然号称Cosplay度假区，整座岛都被打造得能够作为摄影棚发挥作用，备有各种情境与类型的舞台。",
            }
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/ls.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        results = {r["index"]: r for r in body["results"]}
        self.assertIn("长句丢失换行", results[1]["problem"])


class NewlinePositionTests(_Base):
    """换行位置异常：换行符未紧跟允许标点（含逗号/顿号）之后才报。"""

    def _set_problem_config(self, project_dir: str) -> None:
        cfg_path = os.path.join(project_dir, "config.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg["problemAnalyze"] = {"problemList": ["换行位置异常"]}
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)

    def _check(self, pid: str, pre_dst: str):
        entries = [
            {
                "index": 1,
                "name": "",
                "pre_src": "原文",
                "post_src": "原文",
                "pre_dst": pre_dst,
            }
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/np.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        return {r["index"]: r for r in body["results"]}[1]["problem"]

    def test_break_after_punctuation_not_reported(self) -> None:
        # 句号/逗号/顿号后换行均不报
        _, init = self._init_project("np_ok")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        problem = self._check(pid, "第一句。\\n第二句，\\n第三顿、\\n第四句。")
        self.assertNotIn("换行位置异常", problem)

    def test_break_after_hanzi_reports(self) -> None:
        # 汉字后直接换行 → 报第1行
        _, init = self._init_project("np_bad1")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        problem = self._check(pid, "没有标点就换行\\n第二句")
        self.assertIn("换行位置异常：第1行", problem)

    def test_multiple_bad_lines_reported(self) -> None:
        # 两处汉字后换行 → 行号列表正确
        _, init = self._init_project("np_bad2")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        problem = self._check(pid, "句号。\\n汉字换行\\n句号。\\n汉字换行\\n末尾")
        self.assertIn("换行位置异常：第2、4行", problem)

    def test_leading_and_consecutive_newlines_skipped(self) -> None:
        # 行首换行跳过；连续换行中前段为空的跳过；正常汉字后换行仍上报
        _, init = self._init_project("np_skip")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        problem = self._check(pid, "\\n句号。\\n\\n汉字后换行\\n末尾")
        self.assertIn("换行位置异常：第4行", problem)

    def test_real_newline_after_hanzi_reports(self) -> None:
        # 真实换行（\n 控制符）同样识别：汉字后换行报异常
        _, init = self._init_project("np_real")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        problem = self._check(pid, "没有标点就换行\n第二句")
        self.assertIn("换行位置异常：第1行", problem)

    def test_real_newline_after_punctuation_ok(self) -> None:
        # 真实换行（\n 控制符）在句号后 → 不报
        _, init = self._init_project("np_real_ok")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        problem = self._check(pid, "第一句。\n第二句，\n第三顿、\n第四句。")
        self.assertNotIn("换行位置异常", problem)


class ModifierLengthTests(_Base):
    """定语过长（是……的）/ 状语过长（在……中/里、……地）检测。"""

    def _set_problem_config(
        self,
        project_dir: str,
        problems: list,
        attr: int = 10,
        adv: int = 12,
    ) -> None:
        cfg_path = os.path.join(project_dir, "config.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg["problemAnalyze"] = {
            "problemList": problems,
            "attributiveMaxLength": attr,
            "adverbialMaxLength": adv,
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)

    def _check(self, pid: str, pre_dst: str):
        entries = [
            {
                "index": 1,
                "name": "",
                "pre_src": "原文",
                "post_src": "原文",
                "pre_dst": pre_dst,
            }
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/mod.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        return {r["index"]: r for r in body["results"]}[1]["problem"]

    def _check_multi(self, pid: str, pre_dsts: list):
        entries = [
            {
                "index": i + 1,
                "name": "",
                "pre_src": "原文",
                "post_src": "原文",
                "pre_dst": txt,
            }
            for i, txt in enumerate(pre_dsts)
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/mod.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        return {r["index"]: r["problem"] for r in body["results"]}

    def test_per_entry_detection_in_list(self) -> None:
        # 多条目列表：每条都应独立检测（回归：曾误放循环外只检最后一条）
        _, init = self._init_project("mod_multi")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["定语过长", "状语过长"], 6, 12)
        problems = self._check_multi(
            pid,
            [
                "这是我做的。",  # 短，不报
                "这是我在昨天放学后于图书馆遇到的人。",  # 定语过长
                "慢慢地，他走了。",  # 短状语，不报
                "在昨天放学后那个飘着细雨的黄昏小镇里，他站了很久。",  # 状语过长
            ],
        )
        self.assertNotIn("定语过长", problems[1])
        self.assertNotIn("状语过长", problems[1])
        self.assertIn("定语过长", problems[2])
        self.assertIn("状语过长", problems[4])

    def test_attributive_triggers_when_exceeds(self) -> None:
        # 「是……的」中间定语超 6 字 → 报"定语过长"
        _, init = self._init_project("mod_attr")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["定语过长"], 6, 12)
        problem = self._check(pid, "这是我在昨天放学后于图书馆遇到的人。")
        self.assertIn("定语过长", problem)

    def test_no_duplicate_when_post_dst_equals_pre_dst(self) -> None:
        # 无校对时 post_dst == pre_dst，仅检测 post_dst 应避免重复写入
        _, init = self._init_project("mod_dup")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["定语过长", "状语过长"], 6, 12)
        problem = self._check(pid, "在昨天放学后那个飘着细雨的黄昏小镇里，他站了很久。")
        self.assertEqual(problem.count("状语过长"), 1)
        problem2 = self._check(pid, "这是我在昨天放学后于图书馆遇到的人。")
        self.assertEqual(problem2.count("定语过长"), 1)

    def test_literal_escape_newline_not_cross_line(self) -> None:
        # 字面转义换行（"\\n"）应被归一化为行边界，避免跨行吞并
        _, init = self._init_project("mod_esc")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["定语过长"], 6, 12)
        # 字面转义换行分隔的两个短「是…的」，各自不超阈值，不应误报/跨行吞并
        problem = self._check(pid, "这是书。\\n笔是我的。")
        self.assertNotIn("定语过长", problem)

    def test_attributive_not_triggers_when_short(self) -> None:
        # 「是……的」中间定语 ≤ 6 字 → 不报
        _, init = self._init_project("mod_attr_ok")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["定语过长"], 6, 12)
        problem = self._check(pid, "这是我做的。")
        self.assertNotIn("定语过长", problem)

    def test_adverbial_in_triggers_when_exceeds(self) -> None:
        # 「在……中/里」中间超 12 字 → 报"状语过长"
        _, init = self._init_project("mod_adv_in")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["状语过长"], 6, 12)
        problem = self._check(pid, "在昨天放学后那个飘着细雨的黄昏小镇里，他站了很久。")
        self.assertIn("状语过长", problem)

    def test_adverbial_de_triggers_when_exceeds(self) -> None:
        # 「……地」方式状语超 12 字 → 报"状语过长"
        _, init = self._init_project("mod_adv_de")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["状语过长"], 6, 12)
        problem = self._check(pid, "怀着忐忑不安又充满期待的矛盾心情地，他推开了门。")
        self.assertIn("状语过长", problem)

    def test_adverbial_not_triggers_when_short(self) -> None:
        # 短状语（"慢慢地，"仅 3 字）→ 不报
        _, init = self._init_project("mod_adv_ok")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["状语过长"], 6, 12)
        problem = self._check(pid, "慢慢地，他走了。")
        self.assertNotIn("状语过长", problem)

    def test_cross_line_not_false_positive(self) -> None:
        # 跨换行不应被贪婪吞并成一条（"是A的\nB是C的"各自短 → 不报）
        _, init = self._init_project("mod_xline")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["定语过长", "状语过长"], 6, 12)
        problem = self._check(pid, "这是书。\n笔是我的。")
        self.assertNotIn("定语过长", problem)
        self.assertNotIn("状语过长", problem)


if __name__ == "__main__":
    unittest.main()
