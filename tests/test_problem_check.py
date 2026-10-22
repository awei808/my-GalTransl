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
from GalTransl.CSentense import CSentense


class _NewlineProblemConfig:
    """最小化 projectConfig：仅启用换行数相关检测（丢失换行/多加换行/单句过长）。

    供 find_problems 直测使用，绕开 cache/check 会用译前字典重算 post_src 的问题。
    """

    target_lang = "zh-cn"

    def getProblemAnalyzeArinashiDict(self):
        return {}

    def getProblemAnalyzeConfig(self, key):
        from GalTransl.ConfigHelper import CProblemType

        if key == "problemList":
            return [
                CProblemType.丢失换行,
                CProblemType.多加换行,
                CProblemType.单句过长,
            ]
        return []

    def getAvgSentenceLengthThreshold(self):
        return 20

    def getHSentenceLengthThreshold(self):
        return 25


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

    def _set_problem_config(
        self,
        project_dir: str,
        problems: list,
        threshold: int = 17,
        h_threshold: int = 24,
    ) -> None:
        cfg_path = os.path.join(project_dir, "config.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg["problemAnalyze"] = {
            "problemList": problems,
            "avgSentenceLengthThreshold": threshold,
            "avgSentenceLengthThresholdH": h_threshold,
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)

    def _disable_text_plugins(self, project_dir: str) -> None:
        """关闭项目文本插件：这些用例验证检测逻辑自身，避免插件先改写待检文本。

        默认项目模板启用 text_common_normalfix（会删行尾句号、压掉多余换行、去行首换行符），
        重建路径装载插件后会先规整译文，检测对象随之变化；插件-检测的联动另有用例覆盖。
        """
        cfg_path = os.path.join(project_dir, "config.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg.setdefault("plugin", {})["textPlugins"] = []
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)


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
        # 关闭文本插件：常规修复插件会按原文换行数压掉多余的换行（插件联动见 test_recheck_plugin_consistency）
        self._disable_text_plugins(init["project_dir"])
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

    def test_newline_count_follows_post_src(self) -> None:
        # 计数基准是 post_src（与翻译编码侧同源）：译前字典改了换行时按 post_src 判定。
        # 走 find_problems 直测：cache/check 会在重建时用译前字典重算 post_src，
        # 无法注入 pre_src 与 post_src 不一致的场景。
        from GalTransl.Problem import find_problems

        tran = CSentense("AB", index=1)
        tran.post_src = "A\nB"      # 译前字典补入换行
        tran.pre_dst = "甲乙"
        tran.post_dst = "甲乙"      # 译文丢掉该换行
        find_problems([tran], _NewlineProblemConfig(), None, h_ranges=[], h_check_words=[])
        self.assertIn("丢失换行", tran.problem)

    def test_newline_count_ignores_pre_src_extra_newline(self) -> None:
        # pre_src 比 post_src 多换行时不得据此误报（译前字典删掉了换行）
        from GalTransl.Problem import find_problems

        tran = CSentense("A\n\nB", index=1)
        tran.post_src = "AB"
        tran.pre_dst = "甲乙"
        tran.post_dst = "甲乙"
        find_problems([tran], _NewlineProblemConfig(), None, h_ranges=[], h_check_words=[])
        self.assertNotIn("多加换行", tran.problem)
        self.assertNotIn("丢失换行", tran.problem)

    def test_single_sentence_too_long_gate_uses_post_src(self) -> None:
        # 单句过长的前置门槛「原文有换行」同样看 post_src
        from GalTransl.Problem import find_problems

        tran = CSentense("AB", index=1)
        tran.post_src = "A\nB"      # post_src 有换行 → 门槛成立
        tran.pre_dst = "甲" * 40
        tran.post_dst = "甲" * 40 + "\n" + "乙" * 2
        find_problems([tran], _NewlineProblemConfig(), None, h_ranges=[], h_check_words=[])
        self.assertIn("单句过长", tran.problem)


class LongSentenceNewlineTests(_Base):
    """单句过长（旧名长句丢失换行）：平均分句长度超过 avgSentenceLengthThreshold 才报；h 场景用 avgSentenceLengthThresholdH。"""

    def _write_h_batch(self, project_dir: str, h_ranges: list) -> None:
        """写入 pass3 占位缓存与 pass2_cache 的 H 区间批次文件（cache/check 按此解析 h_ranges）。"""
        cache_dir = os.path.join(project_dir, "transl_cache", "pass3_cache")
        os.makedirs(cache_dir, exist_ok=True)
        with open(os.path.join(cache_dir, "ls.txt.json"), "w", encoding="utf-8") as f:
            json.dump([], f)
        batch_dir = os.path.join(project_dir, "transl_cache", "pass2_cache")
        os.makedirs(batch_dir, exist_ok=True)
        with open(os.path.join(batch_dir, "ls.txt.json.batch.json"), "w", encoding="utf-8") as f:
            json.dump({"批次": [{"区间": [lo, hi], "h": True} for lo, hi in h_ranges]}, f, ensure_ascii=False)

    def test_long_sentence_triggers_when_avg_exceeds_threshold(self) -> None:
        # 译文无换行且整句超长（avg=整句长度 > 17）→ 报"单句过长"
        _, init = self._init_project("ls_long")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["单句过长"], 17)
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
        self.assertIn("单句过长", results[1]["problem"])

    def test_short_sentence_not_triggers(self) -> None:
        # 有换行且平均分句长度 ≤ 阈值 → 不报
        _, init = self._init_project("ls_short")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["单句过长"], 17)
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
        self.assertNotIn("单句过长", results[1]["problem"])

    def test_no_newline_in_src_skips_detection(self) -> None:
        # 原文无换行 → 门控跳过，即使译文超长也不报
        _, init = self._init_project("ls_gate")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["单句过长"], 17)
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
        self.assertNotIn("单句过长", results[1]["problem"])

    def test_real_newline_in_src_triggers(self) -> None:
        # 真实换行（\r\n 控制符）同样触发：译成一行超长句
        _, init = self._init_project("ls_real")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["单句过长"], 10)
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
        self.assertIn("单句过长", results[1]["problem"])

    def test_h_scene_uses_dedicated_threshold_not_regular(self) -> None:
        # h 场景内 avg 超过普通阈值 17 但未超过 h 专用阈值 24 → 不报（证明 h 阈值生效）
        _, init = self._init_project("ls_h_thr")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["单句过长"], 17, 24)
        self._write_h_batch(init["project_dir"], [(1, 3)])
        # 译文无换行，整句长度 18（>17 且 <24）
        entries = [
            {
                "index": 1,
                "name": "",
                "pre_src": "原文第一行\\n原文第二行",
                "post_src": "原文第一行\\n原文第二行",
                "pre_dst": "这段译文长度恰好十八字介于两种阈值之间",
            }
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/ls.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        results = {r["index"]: r for r in body["results"]}
        self.assertNotIn("单句过长", results[1]["problem"])

    def test_h_scene_triggers_when_exceeds_h_threshold(self) -> None:
        # h 场景内 avg 超过 h 专用阈值 24 → 报"单句过长"
        _, init = self._init_project("ls_h_over")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"], ["单句过长"], 17, 24)
        self._write_h_batch(init["project_dir"], [(1, 3)])
        # 译文无换行，整句长度远超 24
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
        self.assertIn("单句过长", results[1]["problem"])


class NewlinePositionTests(_Base):
    """换行位置异常：换行符未紧跟允许标点（含逗号/顿号）、空格/Tab、emoji、颜文字之后才报。"""

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
        # 关闭文本插件：常规修复插件会把行首换行符摘除并记入 left_symbol，行号随之整体前移
        self._disable_text_plugins(init["project_dir"])
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

    def test_break_after_heart_emoji_ok(self) -> None:
        # 爱心 emoji（❤ U+2764 + 变体选择符 U+FE0F）后换行不报
        _, init = self._init_project("np_heart")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        problem = self._check(pid, "最喜欢你了\u2764\ufe0f\\n第二句")
        self.assertNotIn("换行位置异常", problem)

    def test_break_after_music_emoji_ok(self) -> None:
        # 音符 emoji（🎵 U+1F3B5）与 ♪/♫/♥ 后换行不报
        _, init = self._init_project("np_music")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        for tail in ("\U0001F3B5", "♪", "♫", "♥"):
            problem = self._check(pid, "啦啦啦" + tail + "\\n第二句")
            self.assertNotIn("换行位置异常", problem)

    def test_break_after_space_tab_ok(self) -> None:
        # 行尾空格/Tab 后换行不报（真实空格、真实 Tab）
        _, init = self._init_project("np_space")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        problem = self._check(pid, "第一句 \n第二句")
        self.assertNotIn("换行位置异常", problem)
        problem = self._check(pid, "第一句\t\n第二句")
        self.assertNotIn("换行位置异常", problem)

    def test_break_after_em_dash_ok(self) -> None:
        # 破折号（—— U+2014）行尾换行不报（真实与字面换行均覆盖）
        _, init = self._init_project("np_dash")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        problem = self._check(pid, "她说——\n第二句")
        self.assertNotIn("换行位置异常", problem)
        problem = self._check(pid, "她说——\\n第二句")
        self.assertNotIn("换行位置异常", problem)

    def test_break_after_kaomoji_ok(self) -> None:
        # 颜文字结尾（(ノ´Д`)ノ、(T_T)、>_<、www）后换行不报
        _, init = self._init_project("np_kaomoji")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        for tail in ("(ノ´Д`)ノ", "(T_T)", ">_<", "www"):
            problem = self._check(pid, "好开心" + tail + "\\n第二句")
            self.assertNotIn("换行位置异常", problem)

    def test_break_after_cjk_still_reports(self) -> None:
        # 回归：词语中间断行（汉字后换行）仍报
        _, init = self._init_project("np_cjk_reg")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        problem = self._check(pid, "站了起\\n来")
        self.assertIn("换行位置异常：第1行", problem)

    def test_break_after_emoji_not_at_line_end_reports(self) -> None:
        # 回归：emoji 后跟汉字再换行仍报（emoji 未落在行尾）
        _, init = self._init_project("np_emoji_cjk")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        problem = self._check(pid, "\u2764\ufe0f好\\n第二句")
        self.assertIn("换行位置异常：第1行", problem)

    def test_break_after_lone_halfwidth_paren_reports(self) -> None:
        # 回归：单个半角右括号结尾仍报（颜文字需 ≥2 个池内字符，保守不放宽）
        _, init = self._init_project("np_paren")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        problem = self._check(pid, "(半角括号)\\n第二句")
        self.assertIn("换行位置异常：第1行", problem)

    def test_break_after_double_zero_still_reports(self) -> None:
        # 回归：行尾连续数字（00）不是颜文字，仍报（字母数字不构成颜文字结构）
        _, init = self._init_project("np_zero")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        problem = self._check(pid, "第一句00\n第二句")
        self.assertIn("换行位置异常：第1行", problem)

    def test_break_after_double_underscore_still_reports(self) -> None:
        # 回归：行尾连续下划线（__）不是颜文字，仍报
        _, init = self._init_project("np_under")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        problem = self._check(pid, "第一句__\n第二句")
        self.assertIn("换行位置异常：第1行", problem)

    def test_break_after_lone_variation_selector_still_reports(self) -> None:
        # 回归：单独变体选择符（U+FE0F）结尾不是完整 emoji，仍报
        _, init = self._init_project("np_vs")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        problem = self._check(pid, "第一句\ufe0f\n第二句")
        self.assertIn("换行位置异常：第1行", problem)


class FrequentNewlineTests(_Base):
    """频繁换行：译文有效字符少却出现多次换行（仅检测换行符，不按标点切小句）。"""

    def _set_problem_config(self, project_dir: str) -> None:
        cfg_path = os.path.join(project_dir, "config.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg["problemAnalyze"] = {"problemList": ["频繁换行"]}
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
            body={"filename": "pass3_cache/fn.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        return {r["index"]: r for r in body["results"]}[1]["problem"]

    def test_triggers_3_newlines_under_20_chars(self) -> None:
        # <20 字符且 ≥3 次换行 → 报"频繁换行"
        _, init = self._init_project("fn_3n")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        # 有效字符 8（2×4），换行 3 次
        problem = self._check(pid, "好！\n走！\n去！\n回！")
        self.assertIn("频繁换行", problem)

    def test_triggers_2_newlines_under_10_chars(self) -> None:
        # <10 字符且 ≥2 次换行 → 报"频繁换行"
        _, init = self._init_project("fn_2n")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        # 有效字符 6（3×2），换行 2 次
        problem = self._check(pid, "好！\n走！\n去。")
        self.assertIn("频繁换行", problem)

    def test_no_trigger_long_text_many_newlines(self) -> None:
        # 字符数足够多（≥20）→ 即使换行多也不报
        _, init = self._init_project("fn_long")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        # 有效字符 28，换行 4 次
        problem = self._check(
            pid,
            "这是第一句话。\n这是第二句话。\n这是第三句话。\n这是第四句话。",
        )
        self.assertNotIn("频繁换行", problem)

    def test_no_trigger_single_newline_short_text(self) -> None:
        # 短但只有 1 次换行（未达 ≥2）→ 不报
        _, init = self._init_project("fn_1n")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        # 有效字符 4，换行 1 次
        problem = self._check(pid, "好！\n走！")
        self.assertNotIn("频繁换行", problem)

    def test_no_trigger_punctuation_only(self) -> None:
        # 仅自然标点、无换行 → 不报（不再按句末标点切分小句）
        _, init = self._init_project("fn_punct")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        # 有效字符 12，0 次换行
        problem = self._check(pid, "好。走。去。")
        self.assertNotIn("频繁换行", problem)

    def test_no_trigger_wrapped_in_dialogue_symbol(self) -> None:
        # 对话引号「」包裹的短句：recover_dialogue_symbol 补回引号后无换行 → 不报
        # 回归 index 17 误报（旧逻辑把「好！」的」误切成 2 段）
        _, init = self._init_project("fn_wrap")
        pid = init["project_id"]
        self._set_problem_config(init["project_dir"])
        # 有效字符 4，0 次换行
        problem = self._check(pid, "「好！」")
        self.assertNotIn("频繁换行", problem)


    def test_problem_types_catalog_includes_frequent_newline(self) -> None:
        # /api/problem-types 应返回"频繁换行"条目（与 CProblemType 同步）
        _, init = self._init_project("fn_cat")
        pid = init["project_id"]
        status, body = self._req("GET", "/api/problem-types")
        self.assertEqual(status, 200)
        names = [t["name"] for t in body["problem_types"]]
        self.assertIn("频繁换行", names)

    def test_problem_types_response_carries_analyze_defaults(self) -> None:
        # 桌面端表单回退用的 problemAnalyze 阈值默认值随目录一起下发（后端单一真源）
        _, init = self._init_project("pa_defaults")
        pid = init["project_id"]
        status, body = self._req("GET", "/api/problem-types")
        self.assertEqual(status, 200)
        defaults = body["problem_analyze_defaults"]
        self.assertEqual(defaults["avgSentenceLengthThreshold"], 17)
        self.assertEqual(defaults["avgSentenceLengthThresholdH"], 24)
        self.assertEqual(defaults["attributiveMaxLength"], 10)
        self.assertEqual(defaults["adverbialMaxLength"], 12)


class RhythmMonotonyTests(_Base):
    """节奏单一：换行前后相邻两行严格等长、均 >14 字且均不含中英文标点。"""

    def _check(self, pid: str, pre_dst: str, post_src: str = "原文"):
        entries = [
            {
                "index": 1,
                "name": "",
                "pre_src": post_src,
                "post_src": post_src,
                "pre_dst": pre_dst,
            }
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{pid}/cache/check",
            body={"filename": "pass3_cache/rhythm.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        return {r["index"]: r for r in body["results"]}[1]["problem"]

    def _check_rhythm(
        self, name: str, pre_dst: str, problems=None, post_src: str = "原文"
    ):
        _, init = self._init_project(name)
        self._set_problem_config(init["project_dir"], problems or ["节奏单一"])
        # 关闭文本插件：避免插件先规整换行/行尾，干扰检测口径
        self._disable_text_plugins(init["project_dir"])
        return self._check(init["project_id"], pre_dst, post_src)

    def test_equal_long_lines_trigger(self) -> None:
        # 15 字 vs 15 字、均无标点 → 命中
        problem = self._check_rhythm("rhythm_eq", "甲" * 15 + "\n" + "乙" * 15)
        self.assertIn("节奏单一：第1、2行各15字", problem)

    def test_unequal_lengths_not_trigger(self) -> None:
        # 必须严格等长：15 vs 16 不再命中（旧口径按 15% 容差会报）
        problem = self._check_rhythm("rhythm_diff", "甲" * 15 + "\n" + "乙" * 16)
        self.assertNotIn("节奏单一", problem)

    def test_length_boundary(self) -> None:
        # 14 字不满足 >14；15 字命中
        miss = self._check_rhythm("rhythm_len_14", "甲" * 14 + "\n" + "乙" * 14)
        hit = self._check_rhythm("rhythm_len_15", "甲" * 15 + "\n" + "乙" * 15)
        self.assertNotIn("节奏单一", miss)
        self.assertIn("节奏单一", hit)

    def test_chinese_punctuation_not_trigger(self) -> None:
        # 一侧含中文逗号（7+1+7=15 字，与另一行等长）→ 有标点，不命中
        problem = self._check_rhythm(
            "rhythm_zh_punct", "甲" * 7 + "，" + "乙" * 7 + "\n" + "丙" * 15
        )
        self.assertNotIn("节奏单一", problem)

    def test_ascii_punctuation_not_trigger(self) -> None:
        # 半角标点同样视为有标点
        problem = self._check_rhythm(
            "rhythm_ascii_punct", "甲" * 7 + "," + "乙" * 7 + "\n" + "丙" * 15
        )
        self.assertNotIn("节奏单一", problem)

    def test_quote_and_bracket_not_trigger(self) -> None:
        # 引号/括号也属标点
        quoted = self._check_rhythm(
            "rhythm_quote", "「" + "甲" * 14 + "\n" + "乙" * 15
        )
        bracketed = self._check_rhythm(
            "rhythm_bracket", "（" + "甲" * 14 + "\n" + "乙" * 15
        )
        self.assertNotIn("节奏单一", quoted)
        self.assertNotIn("节奏单一", bracketed)

    def test_various_punctuation_not_trigger(self) -> None:
        # 全角标点与日文标点（・/〜）同样视为有标点：各构造「7+1+7=15 字」等长样本
        for name, punct in (
            ("semicolon", "；"),
            ("ellipsis", "…"),
            ("dash", "—"),
            ("fullwidth_quote", "“"),
            ("katakana_middle_dot", "・"),
            ("wave_dash", "〜"),
            ("fullwidth_tilde", "～"),
        ):
            with self.subTest(punct=punct):
                problem = self._check_rhythm(
                    f"rhythm_punct_{name}",
                    "甲" * 7 + punct + "乙" * 7 + "\n" + "丙" * 15,
                )
                self.assertNotIn("节奏单一", problem)

    def test_inner_space_not_treated_as_punctuation(self) -> None:
        # 空格不属于标点：含空格但等长的长句仍参与判定
        problem = self._check_rhythm(
            "rhythm_space", "甲" * 7 + " " + "乙" * 7 + "\n" + "丙" * 15
        )
        self.assertIn("节奏单一", problem)

    def test_emoji_not_treated_as_punctuation(self) -> None:
        # emoji（So 类）不属于标点：含 ♪ 的等长长句仍参与判定
        problem = self._check_rhythm(
            "rhythm_emoji", "甲" * 7 + "♪" + "乙" * 7 + "\n" + "丙" * 15
        )
        self.assertIn("节奏单一", problem)

    def test_long_line_paired_with_short_not_trigger(self) -> None:
        # 一长一短：字数不等，不命中
        problem = self._check_rhythm("rhythm_mixed", "甲" * 15 + "\n" + "乙" * 30)
        self.assertNotIn("节奏单一", problem)

    def test_whitespace_only_line_skipped(self) -> None:
        # 空行两侧没有可直接比较的文本，不按跨空行相邻判定
        problem = self._check_rhythm(
            "rhythm_ws", "甲" * 15 + "\n   \n" + "乙" * 15
        )
        self.assertNotIn("节奏单一", problem)

    def test_surrounding_whitespace_trimmed(self) -> None:
        # 行首尾空格不计入字数：strip 后仍严格等长
        problem = self._check_rhythm(
            "rhythm_trim", "  " + "甲" * 15 + "  \n" + "乙" * 15
        )
        self.assertIn("节奏单一", problem)

    def test_source_equal_does_not_suppress(self) -> None:
        # 源文两行同样等长且无标点也不抑制：只按译文的三条件判定
        problem = self._check_rhythm(
            "rhythm_src_equal",
            "甲" * 15 + "\n" + "乙" * 15,
            post_src="丙" * 15 + "\n" + "丁" * 15,
        )
        self.assertIn("节奏单一", problem)

    def test_single_line_not_trigger(self) -> None:
        problem = self._check_rhythm("rhythm_one", "只有一行没有换行的一句话。")
        self.assertNotIn("节奏单一", problem)

    def test_literal_escape_newline_trigger(self) -> None:
        # 旧缓存/字面转义换行与真实换行同口径
        problem = self._check_rhythm(
            "rhythm_literal", "甲" * 15 + "\\n" + "乙" * 15
        )
        self.assertIn("节奏单一", problem)

    def test_crlf_newline_trigger(self) -> None:
        problem = self._check_rhythm(
            "rhythm_crlf", "甲" * 15 + "\r\n" + "乙" * 15
        )
        self.assertIn("节奏单一", problem)

    def test_blank_line_between_not_trigger(self) -> None:
        # 空行两侧没有可直接比较的文本，不按跨空行相邻判定
        problem = self._check_rhythm(
            "rhythm_blank", "甲" * 15 + "\n\n" + "乙" * 15
        )
        self.assertNotIn("节奏单一", problem)

    def test_multiple_breaks_report_each_pair(self) -> None:
        problem = self._check_rhythm(
            "rhythm_multi",
            "甲" * 15 + "\n" + "乙" * 15 + "\n" + "丙" * 15,
        )
        self.assertIn("第1、2行各15字", problem)
        self.assertIn("第2、3行各15字", problem)

    def test_only_matching_pair_reported(self) -> None:
        # 三行 15/15/16：只报符合条件的前一对
        problem = self._check_rhythm(
            "rhythm_partial",
            "甲" * 15 + "\n" + "乙" * 15 + "\n" + "丙" * 16,
        )
        self.assertIn("第1、2行各15字", problem)
        self.assertNotIn("第2、3行", problem)

    def test_not_enabled_skips_detection(self) -> None:
        problem = self._check_rhythm(
            "rhythm_off",
            "甲" * 15 + "\n" + "乙" * 15,
            problems=["残留日文"],
        )
        self.assertNotIn("节奏单一", problem)

    def test_problem_types_catalog_includes_rhythm_monotony(self) -> None:
        _, init = self._init_project("rhythm_cat")
        status, body = self._req("GET", "/api/problem-types")
        self.assertEqual(status, 200)
        names = [t["name"] for t in body["problem_types"]]
        self.assertIn("节奏单一", names)


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


class LongSentenceLegacyAliasTests(LongSentenceNewlineTests):
    """旧配置写法「长句丢失换行」经别名仍可启用检测，但报出的是新名「单句过长」。"""

    def test_legacy_alias_enables_detection_with_new_name(self) -> None:
        _, init = self._init_project("ls_alias")
        self._set_problem_config(init["project_dir"], ["长句丢失换行"], 17)
        entries = [
            {"index": 1, "name": "", "pre_src": "ああ。\nいい。",
             "post_src": "ああ。\nいい。",
             "pre_dst": "这是一句非常长的中文翻译完全没有换行符来分割整句话。"},
        ]
        status, body = self._req(
            "POST",
            f"/api/projects/{init['project_id']}/cache/check",
            body={"filename": "pass3_cache/alias.txt.json", "entries": entries},
        )
        self.assertEqual(status, 200)
        self.assertIn("单句过长", body["results"][0]["problem"])


class ResidualJapaneseCharsTests(unittest.TestCase):
    """contains_japanese 输出的字符顺序必须稳定（问题文本按首次出现顺序落盘）。"""

    def test_chars_returned_in_first_appearance_order(self) -> None:
        from GalTransl.Utils import contains_japanese

        self.assertEqual(contains_japanese("%p-1;%fＭＳ ゴシック;――"), "ゴシック")
        self.assertEqual(contains_japanese("いくら与鲑鱼子同音"), "いくら")
        # 仅假名计入（汉字不在日文假名区间内）
        self.assertEqual(contains_japanese("吾輩は猫である"), "はである")

    def test_repeated_calls_are_stable(self) -> None:
        from GalTransl.Utils import contains_japanese

        text = "ゴックシのッシゴク"
        self.assertEqual({contains_japanese(text) for _ in range(50)}, {"ゴックシの"})

    def test_no_japanese_returns_empty(self) -> None:
        from GalTransl.Utils import contains_japanese

        self.assertEqual(contains_japanese("测试文本 abc 123"), "")

    def test_dash_blacklist_still_excluded(self) -> None:
        from GalTransl.Utils import contains_japanese

        self.assertEqual(contains_japanese("ゴー・シ"), "ゴシ")


if __name__ == "__main__":
    unittest.main()
