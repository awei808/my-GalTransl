# -*- coding: utf-8 -*-
"""ForNgramCheck 本地 n-gram 语域检测的单元测试。

0.7.0 起该功能入口已下线（前端选项、后处理白名单、流水线步骤、问题类型目录、
配置模板键全部移除），实现与金标保留以便恢复。本文件因此分两类：
  - 下线锁定：入口不再暴露、find_problems 不再认领「语域异常」
  - 底层能力：禁用语义、观察模式打分、索引 IO、缓存字段透传（恢复后仍生效）

n-gram 数学与索引 IO 在 tools/test_ngram_scorer.py 覆盖，此处用合成微索引。
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from GalTransl.CSentense import CSentense
from GalTransl.Cache import _build_cache_obj
from GalTransl.Problem import find_problems
from GalTransl.ConfigHelper import CProblemType, PROBLEM_TYPE_CATALOG_ORDER
from GalTransl import ngram_rules as nr
from GalTransl.Backend.ForNgramCheck import ForNgramCheck
from GalTransl.Backend.ForFixRound import _FIX_SPECS, MODE_SRC_DST


def _trans(index: int, pre_dst: str, proofread_zh: str = "") -> CSentense:
    t = CSentense(f"src{index}", index=index)
    t.post_src = "原文"
    t.pre_dst = pre_dst
    t.proofread_zh = proofread_zh
    t.ngram_issue = ""
    return t


class _Cfg:
    """ForNgramCheck/find_problems 配置替身：dict 键值直读。"""

    def __init__(self, values: dict = None, problem_list: list = None):
        self._values = values or {}
        self._problem_list = problem_list or []

    def getKey(self, key, default=None):
        return self._values.get(key, default)

    def getProblemAnalyzeArinashiDict(self) -> dict:
        return {}

    def getProblemAnalyzeConfig(self, key: str) -> list:
        return self._problem_list if key == "problemList" else []

    def hasProblemAnalyzeConfig(self, key: str) -> bool:
        return True


def _build_tiny_index(index_dir: str) -> None:
    """合成微索引：A 层（pos）口吻词表 + B 层（bg）口吻词表 + h_hint。"""
    os.makedirs(index_dir, exist_ok=True)
    tables = {
        "tierA": {"魔法少女在": 30, "魔法少女": 40, "魔法": 50, "少女": 50, "夜空": 20},
        "tierB": {"新华社报道": 30, "新华社": 40, "报道": 50, "经济": 50, "民生": 20},
    }
    meta_layers = {}
    for name, table in tables.items():
        nr.save_layer_table(index_dir, name, table)
        orders = {}
        for order in (1, 2, 3):
            grams = [kv for kv in table.items() if len(kv[0]) == order]
            orders[str(order)] = {"unique": len(grams), "total": sum(c for _g, c in grams)}
        meta_layers[name] = {
            "role": "native_positive" if name == "tierA" else "native_background",
            "bucket": "pos" if name == "tierA" else "bg",
            "weight": 1.0 if name == "tierA" else 0.5,
            "file": f"{name}.txt",
            "lines": 10,
            "chars": 100,
            "grams": len(table),
            "orders": orders,
        }
    meta = {
        "built_at": "test",
        "corpus_built_at": None,
        "params": {"max_n": 3, "alpha": nr.ALPHA, "top_k": 100, "min_counts": {"1": 1, "2": 1, "3": 1}},
        "h_hint": ["肉棒"],
        "layers": meta_layers,
    }
    nr.write_index_meta(index_dir, meta)


class _IndexMixin:
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.index_dir = os.path.join(cls._tmp.name, "ngram_index")
        _build_tiny_index(cls.index_dir)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class ForNgramCheckInitTests(_IndexMixin, unittest.TestCase):
    """构造期禁用/观察模式语义。"""

    def test_lang_gate(self) -> None:
        cfg = _Cfg({"language": "ja2en", "gpt.ngramIndexPath": self.index_dir,
                    "gpt.ngramThreshold": "0"})
        checker = ForNgramCheck(cfg)
        self.assertIn("目标语言非中文", checker.disabled_reason)

    def test_missing_index_disabled(self) -> None:
        cfg = _Cfg({"language": "ja2zh-cn", "gpt.ngramIndexPath": os.path.join(self._tmp.name, "none")})
        checker = ForNgramCheck(cfg)
        self.assertIn("索引不存在", checker.disabled_reason)

    def test_observe_mode_when_threshold_empty(self) -> None:
        cfg = _Cfg({"language": "ja2zh-cn", "gpt.ngramIndexPath": self.index_dir,
                    "gpt.ngramThreshold": ""})
        checker = ForNgramCheck(cfg)
        self.assertEqual(checker.disabled_reason, "")
        self.assertTrue(checker._observe_only)
        self.assertIsNone(checker._threshold)

    def test_numeric_zero_threshold_is_not_observe(self) -> None:
        # 数字 0 是合法阈值（低于 0 即标记），不能因假值被吞进观察模式
        cfg = _Cfg({"language": "ja2zh-cn", "gpt.ngramIndexPath": self.index_dir,
                    "gpt.ngramThreshold": 0})
        checker = ForNgramCheck(cfg)
        self.assertEqual(checker.disabled_reason, "")
        self.assertFalse(checker._observe_only)
        self.assertEqual(checker._threshold, 0.0)

    def test_valid_config_loads_index(self) -> None:
        cfg = _Cfg({"language": "ja2zh-cn", "gpt.ngramIndexPath": self.index_dir,
                    "gpt.ngramThreshold": "0.04", "gpt.ngramLambda": "1.0"})
        checker = ForNgramCheck(cfg)
        self.assertEqual(checker.disabled_reason, "")
        self.assertFalse(checker._observe_only)
        self.assertEqual(checker._threshold, 0.04)
        self.assertEqual(checker._lam, 1.0)


class ForNgramCheckRunTests(_IndexMixin, unittest.TestCase):
    """batch_translate：标记/清旧标记/观察模式/禁用跳过。"""

    def _run(self, checker: ForNgramCheck, trans_list: list) -> None:
        asyncio.run(checker.batch_translate("test.json", "test.json", trans_list, 20))

    def test_flags_low_score_and_clears_old_marks(self) -> None:
        # 阈值取两句实测分数的中点（微索引下多数 gram 未登录，绝对分数无意义，
        # 只断言「域内分高于域外分 → 只有域外句被标记」的相对方向）
        in_dst = "魔法少女在夜空中飞舞，唱起了古老的咒语之歌。"
        off_dst = "新华社报道经济发展成效，民生保障工作持续推进。"
        index, _err = nr.load_index(self.index_dir)
        mid = (nr.score_text(in_dst, index) + nr.score_text(off_dst, index)) / 2
        cfg = _Cfg({"language": "ja2zh-cn", "gpt.ngramIndexPath": self.index_dir,
                    "gpt.ngramThreshold": str(mid)})
        checker = ForNgramCheck(cfg)
        in_domain = _trans(0, in_dst)
        off_domain = _trans(1, off_dst)
        off_domain.ngram_issue = "旧标记"  # 重跑必须清掉
        self._run(checker, [in_domain, off_domain])
        self.assertEqual(in_domain.ngram_issue, "")
        self.assertNotEqual(off_domain.ngram_issue, "")
        self.assertIn("语域分", off_domain.ngram_issue)
        self.assertIn("低于阈值", off_domain.ngram_issue)

    def test_observe_mode_never_marks(self) -> None:
        cfg = _Cfg({"language": "ja2zh-cn", "gpt.ngramIndexPath": self.index_dir,
                    "gpt.ngramThreshold": ""})
        checker = ForNgramCheck(cfg)
        off_domain = _trans(0, "新华社报道经济发展成效，民生保障工作持续推进。")
        off_domain.ngram_issue = "旧标记"
        self._run(checker, [off_domain])
        # 观察模式：不清也不写标记（不产生副作用）
        self.assertEqual(off_domain.ngram_issue, "旧标记")

    def test_disabled_skip(self) -> None:
        cfg = _Cfg({"language": "ja2zh-cn",
                    "gpt.ngramIndexPath": os.path.join(self._tmp.name, "none")})
        checker = ForNgramCheck(cfg)
        tran = _trans(0, "新华社报道经济发展成效，民生保障工作持续推进。")
        tran.ngram_issue = "旧标记"
        self._run(checker, [tran])
        self.assertEqual(tran.ngram_issue, "旧标记")


class ClaimAndSpecTests(_IndexMixin, unittest.TestCase):
    """find_problems 认领 + 修复指令注册 + 目录同步。"""

    def test_find_problems_no_longer_claims_ngram(self) -> None:
        """入口下线：问题类型枚举里没有「语域异常」，缓存残留标记也不再被认领。"""
        self.assertNotIn("语域异常", [t.name for t in CProblemType])
        cfg = _Cfg(problem_list=[])
        tran = _trans(0, "新华社报道经济发展成效，民生保障工作持续推进。")
        tran.ngram_issue = "语域分 -1.20 低于阈值 0.04"
        find_problems([tran], cfg, None)
        self.assertNotIn("语域异常", tran.problem)

    def test_unknown_problem_type_in_config_is_tolerated(self) -> None:
        """旧项目 problemList 残留已下线类型时不得抛 KeyError（会阻断缓存写盘）。"""
        from GalTransl.ConfigHelper import CProjectConfig
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "config.yaml").write_text(
                "problemAnalyze:\n  problemList:\n    - 语域异常\n    - 残留日文\n",
                encoding="utf-8",
            )
            cfg = CProjectConfig(tmp, "config.yaml")
            types = cfg.getProblemAnalyzeConfig("problemList")
        self.assertEqual([t.name for t in types], ["残留日文"])

    def test_null_problem_analyze_section_is_tolerated(self) -> None:
        """problemAnalyze / problemList 写成 null 或非对象时返回空列表而非抛 TypeError。"""
        from GalTransl.ConfigHelper import CProjectConfig
        import tempfile
        from pathlib import Path

        for body, has_key in (
            ("problemAnalyze:\n", False),
            ("problemAnalyze:\n  problemList:\n", True),
            ("problemAnalyze: [1, 2]\n", False),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                Path(tmp, "config.yaml").write_text(body, encoding="utf-8")
                cfg = CProjectConfig(tmp, "config.yaml")
                self.assertEqual(cfg.getProblemAnalyzeConfig("problemList"), [])
                # 键存在但值为 null 仍算「已配置」（与 hasProblemAnalyzeConfig 原语义一致）
                self.assertEqual(cfg.hasProblemAnalyzeConfig("problemList"), has_key)
                # 端到端：arinashiDict 与四个阈值 getter 也必须能读（否则检测仍中断）
                tran = _trans(0, "这是一句用于问题检测的测试译文内容。")
                find_problems([tran], cfg, None)

    def test_fix_spec_removed(self) -> None:
        names = [getattr(k, "name", str(k)) for k in _FIX_SPECS]
        self.assertNotIn("语域异常", names)
        # 中文不顺保持 src+dst（互不覆盖的回归锁定）
        self.assertEqual(_FIX_SPECS[CProblemType.中文不顺].mode, MODE_SRC_DST)

    def test_catalog_order_sync(self) -> None:
        from GalTransl.server_meta import _PROBLEM_TYPE_CATALOG
        self.assertNotIn("语域异常", PROBLEM_TYPE_CATALOG_ORDER)
        self.assertEqual(PROBLEM_TYPE_CATALOG_ORDER[-1], "中文不顺")
        names = [item["name"] for item in _PROBLEM_TYPE_CATALOG]
        self.assertEqual(names, list(PROBLEM_TYPE_CATALOG_ORDER))


class CacheAndRegistrationTests(unittest.TestCase):
    """缓存落盘/读回 + 流水线注册链。"""

    def test_cache_roundtrip(self) -> None:
        tran = _trans(0, "新华社报道经济发展成效，民生保障工作持续推进。")
        tran.ngram_issue = "语域分 -1.20 低于阈值 0.04"
        obj = _build_cache_obj(tran)
        self.assertEqual(obj.get("ngram_issue"), "语域分 -1.20 低于阈值 0.04")
        # 空标记不落盘（与 fluency_issue 口径一致）
        tran2 = _trans(1, "另一句译文内容。")
        self.assertNotIn("ngram_issue", _build_cache_obj(tran2))

    def test_server_cache_rebuild_keeps_ngram_issue(self) -> None:
        """字段透传保留：入口下线后仍兼容旧缓存，恢复功能时无需改重建逻辑。"""
        from GalTransl.server_cache import _rebuild_trans_list_with_postprocess

        entry = {
            "index": 0,
            "name": "",
            "post_src": "こんにちは",
            "pre_src": "こんにちは",
            "pre_dst": "你好",
            "proofread_dst": "",
            "problem": "语域异常：语域分 -1.20 低于阈值 0.04",
            "ngram_issue": "语域分 -1.20 低于阈值 0.04",
            "fluency_issue": "",
        }
        tran = _rebuild_trans_list_with_postprocess([entry], None, None, None, [])[0]
        self.assertEqual(tran.ngram_issue, "语域分 -1.20 低于阈值 0.04")
        self.assertEqual(tran.fluency_issue, "")

    def test_pipeline_registration_removed(self) -> None:
        from GalTransl.Frontend.llm_postprocess import _resolve_after_translation_order
        from GalTransl.Frontend.pipeline_stages import AFTER_TRANS_STEP_KEYS

        self.assertNotIn("ngramcheck", AFTER_TRANS_STEP_KEYS)

        class _KeyCfg:
            def __init__(self, value):
                self._v = value

            def getKey(self, key, default=None):
                return self._v if key == "gpt.afterTranslation" else default

        order = _resolve_after_translation_order(_KeyCfg(["ngramcheck", "fluencycheck"]))
        self.assertEqual(order, ["fluencycheck"])

    def test_crosscheck_stats(self) -> None:
        import io
        import logging
        from GalTransl.Frontend.llm_postprocess import _log_ngram_fluency_crosscheck

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("GalTransl")
        old_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            lines = [_trans(i, f"句子{i}内容。") for i in range(4)]
            lines[0].fluency_issue = "A：「甲」→「乙」"
            lines[1].fluency_issue = "B：「丙」→「丁」"
            lines[1].ngram_issue = "语域分 -1.00 低于阈值 0.04"
            lines[2].ngram_issue = "语域分 -2.00 低于阈值 0.04"
            _log_ngram_fluency_crosscheck("f.json", lines)
            out = stream.getvalue()
            self.assertIn("AI 通顺标记 2 句", out)
            self.assertIn("N-gram 语域标记 2 句", out)
            self.assertIn("共同命中 1 句", out)
            self.assertIn("仅 N-gram 命中 1 句", out)
            # 仅 fluency 标记（ngram 侧零命中）不打日志——空统计是纯噪声
            stream.truncate(0)
            stream.seek(0)
            _log_ngram_fluency_crosscheck("f.json", [lines[0]])
            self.assertEqual(stream.getvalue(), "")
            # 全空不打日志
            stream.truncate(0)
            stream.seek(0)
            _log_ngram_fluency_crosscheck("f.json", [_trans(9, "干净句子。")])
            self.assertEqual(stream.getvalue(), "")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)


if __name__ == "__main__":
    unittest.main(verbosity=2)
