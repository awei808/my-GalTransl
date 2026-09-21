"""H 档位阈值与禁用词表可配（0.5.0 补进 §3.3-4）测试。

覆盖：
  - resolve_h_thresholds：缺失/部分配置/合法自定义/非递增/越界/非数字 →
    合法时返回配置值，任一非法则整组回退 DEFAULT_H_THRESHOLDS（附 warning）
  - is_h_value(value, threshold=None)：不传阈值时行为与旧版一致（>= 0.5）
  - _h_level(h_val, thresholds)：自定义阈值下的档位边界（左闭右开）
  - _format_batch_metadata_block 按配置阈值分档渲染（含禁用词启用开关与截断上限）
  - _group_is_h_scene 与注入侧同用 intimate 阈值（字典分流口径一致）
  - _format_h_forbidden_words(limit) 的非法 limit 回退
  - server_cache._project_h_threshold：按项目目录就近读配置 + mtime 缓存

使用 ForGalJsonTranslate.__new__ 打桩，不触发 BaseTranslate.__init__。
"""

import os
import tempfile
import unittest

import yaml

from GalTransl.Backend.ForGalJsonTranslate import (
    BatchMetadata,
    ForGalJsonTranslate,
    _h_level,
)
from GalTransl.Backend.Prompts import (
    H_BATCH_GUIDE,
    H_INTIMATE_GUIDE,
    H_TENSION_GUIDE,
    NORMAL_BATCH_GUIDE,
)
from GalTransl.Backend.utils import (
    DEFAULT_H_THRESHOLDS,
    is_h_value,
    resolve_h_thresholds,
)


class _Cfg:
    """最小项目配置桩：只实现 getKey（点分键 + 默认值）。"""

    def __init__(self, data=None):
        self.data = dict(data or {})

    def getKey(self, key, default=None):
        return self.data.get(key, default)


def make_translator(project_config=None, h_words=None):
    """通过 __new__ 打桩批次元数据渲染所需属性。"""
    t = ForGalJsonTranslate.__new__(ForGalJsonTranslate)
    t._h_check_words = h_words if h_words is not None else []
    t.project_config = project_config
    return t


def make_batch_metadata(batches):
    return BatchMetadata(id="test", batches=batches)


class ResolveHThresholdsTests(unittest.TestCase):
    def test_no_config_returns_default(self) -> None:
        self.assertEqual(resolve_h_thresholds(None), DEFAULT_H_THRESHOLDS)

    def test_config_without_getkey_returns_default(self) -> None:
        # 既有测试常见的 SimpleNamespace 桩：无 getKey 时必须安全回退
        class _Stub:
            pass

        self.assertEqual(resolve_h_thresholds(_Stub()), DEFAULT_H_THRESHOLDS)

    def test_partial_config_uses_default_for_missing(self) -> None:
        cfg = _Cfg({"internals.hLevels.intimate": 60})
        self.assertEqual(resolve_h_thresholds(cfg), (0.25, 0.60, 0.75))

    def test_all_custom_valid(self) -> None:
        cfg = _Cfg({
            "internals.hLevels.tension": 10,
            "internals.hLevels.intimate": 40,
            "internals.hLevels.explicit": 80,
        })
        self.assertEqual(resolve_h_thresholds(cfg), (0.10, 0.40, 0.80))

    def test_boundary_zero_and_hundred_are_valid(self) -> None:
        cfg = _Cfg({
            "internals.hLevels.tension": 0,
            "internals.hLevels.intimate": 0,
            "internals.hLevels.explicit": 100,
        })
        self.assertEqual(resolve_h_thresholds(cfg), (0.0, 0.0, 1.0))

    def test_equal_thresholds_are_valid(self) -> None:
        # tension == intimate == explicit 合法（三档退化为一档，不报错）
        cfg = _Cfg({
            "internals.hLevels.tension": 50,
            "internals.hLevels.intimate": 50,
            "internals.hLevels.explicit": 50,
        })
        self.assertEqual(resolve_h_thresholds(cfg), (0.5, 0.5, 0.5))

    def test_non_increasing_falls_back_whole_group(self) -> None:
        cfg = _Cfg({
            "internals.hLevels.tension": 50,
            "internals.hLevels.intimate": 40,
            "internals.hLevels.explicit": 75,
        })
        self.assertEqual(resolve_h_thresholds(cfg), DEFAULT_H_THRESHOLDS)

    def test_out_of_range_falls_back_whole_group(self) -> None:
        cfg = _Cfg({
            "internals.hLevels.tension": 25,
            "internals.hLevels.intimate": 50,
            "internals.hLevels.explicit": 120,
        })
        self.assertEqual(resolve_h_thresholds(cfg), DEFAULT_H_THRESHOLDS)

    def test_negative_falls_back_whole_group(self) -> None:
        cfg = _Cfg({"internals.hLevels.tension": -5})
        self.assertEqual(resolve_h_thresholds(cfg), DEFAULT_H_THRESHOLDS)

    def test_non_numeric_falls_back_whole_group(self) -> None:
        cfg = _Cfg({"internals.hLevels.intimate": "abc"})
        self.assertEqual(resolve_h_thresholds(cfg), DEFAULT_H_THRESHOLDS)

    def test_numeric_string_is_accepted(self) -> None:
        # YAML 里写成字符串 "60" 也应可解析
        cfg = _Cfg({"internals.hLevels.intimate": "60"})
        self.assertEqual(resolve_h_thresholds(cfg), (0.25, 0.6, 0.75))

    def test_percent_to_fraction_matches_literal_float(self) -> None:
        # 配置 33（百分比）换算后须与字面量 0.33 完全相等，避免阈值边界静默差 1 档
        cfg = _Cfg({
            "internals.hLevels.tension": 33,
            "internals.hLevels.intimate": 66,
            "internals.hLevels.explicit": 99,
        })
        self.assertEqual(resolve_h_thresholds(cfg), (0.33, 0.66, 0.99))


class IsHValueThresholdTests(unittest.TestCase):
    def test_default_threshold_unchanged(self) -> None:
        self.assertTrue(is_h_value(0.5))
        self.assertTrue(is_h_value(True))
        self.assertFalse(is_h_value(0.499))
        self.assertFalse(is_h_value(None))

    def test_explicit_threshold(self) -> None:
        self.assertFalse(is_h_value(0.7, 0.8))
        self.assertTrue(is_h_value(0.8, 0.8))
        self.assertTrue(is_h_value(0.9, 0.8))

    def test_lower_explicit_threshold(self) -> None:
        self.assertTrue(is_h_value(0.3, 0.25))
        self.assertFalse(is_h_value(0.2, 0.25))


class HLevelWithThresholdsTests(unittest.TestCase):
    def test_default_boundaries(self) -> None:
        self.assertEqual(_h_level(0.249), "normal")
        self.assertEqual(_h_level(0.25), "tension")
        self.assertEqual(_h_level(0.5), "intimate")
        self.assertEqual(_h_level(0.75), "explicit")

    def test_custom_boundaries(self) -> None:
        th = (0.10, 0.40, 0.80)
        self.assertEqual(_h_level(0.05, th), "normal")
        self.assertEqual(_h_level(0.10, th), "tension")
        self.assertEqual(_h_level(0.39, th), "tension")
        self.assertEqual(_h_level(0.40, th), "intimate")
        self.assertEqual(_h_level(0.79, th), "intimate")
        self.assertEqual(_h_level(0.80, th), "explicit")


class BatchMetadataWithConfiguredLevelsTests(unittest.TestCase):
    def _cfg(self, **overrides):
        data = {
            "internals.hLevels.tension": 10,
            "internals.hLevels.intimate": 40,
            "internals.hLevels.explicit": 80,
        }
        data.update(overrides)
        return _Cfg(data)

    def test_level_moves_with_configured_intimate(self) -> None:
        # h=0.45：默认阈值下是 tension，intimate=40% 时应升为 intimate 档并注入禁用词
        bm = make_batch_metadata([
            {"区间": [1, 5], "h": 0.45, "视角": "主视角", "氛围": "情欲", "用词色彩": "露骨"}
        ])
        t = make_translator(project_config=self._cfg(), h_words=["攀上顶峰"])
        out = t._format_batch_metadata_block(bm, 1, 5)
        self.assertIn(H_INTIMATE_GUIDE, out)
        self.assertIn("攀上顶峰", out)
        self.assertNotIn(H_TENSION_GUIDE, out)

    def test_default_config_keeps_legacy_levels(self) -> None:
        # 无自定义阈值（空配置桩）→ 与旧版一致：0.45 仍是 tension 且无禁用词
        bm = make_batch_metadata([
            {"区间": [1, 5], "h": 0.45, "视角": "主视角", "氛围": "暧昧", "用词色彩": "克制"}
        ])
        t = make_translator(project_config=_Cfg(), h_words=["攀上顶峰"])
        out = t._format_batch_metadata_block(bm, 1, 5)
        self.assertIn(H_TENSION_GUIDE, out)
        self.assertNotIn("禁止使用", out)

    def test_forbidden_words_can_be_disabled(self) -> None:
        bm = make_batch_metadata([
            {"区间": [1, 5], "h": 0.9, "视角": "主视角", "氛围": "情欲", "用词色彩": "直白"}
        ])
        t = make_translator(
            project_config=self._cfg(**{"internals.hForbiddenWords.enabled": False}),
            h_words=["攀上顶峰"],
        )
        out = t._format_batch_metadata_block(bm, 1, 5)
        self.assertIn(H_BATCH_GUIDE, out)
        self.assertNotIn("禁止使用", out)
        self.assertNotIn("攀上顶峰", out)

    def test_forbidden_words_limit_truncates(self) -> None:
        words = [f"词{i}" for i in range(5)]
        bm = make_batch_metadata([{"区间": [1, 3], "h": 0.9}])
        t = make_translator(
            project_config=self._cfg(**{"internals.hForbiddenWords.limit": 2}),
            h_words=words,
        )
        out = t._format_batch_metadata_block(bm, 1, 3)
        self.assertIn("词0", out)
        self.assertIn("词1", out)
        self.assertNotIn("词2", out)
        self.assertIn("等词语", out)

    def test_tension_level_has_no_forbidden_words(self) -> None:
        # tension 档（非 H 档位）始终不注入禁用词，与启用开关无关
        bm = make_batch_metadata([{"区间": [1, 3], "h": 0.2}])
        t = make_translator(project_config=self._cfg(), h_words=["攀上顶峰"])
        out = t._format_batch_metadata_block(bm, 1, 3)
        self.assertIn(H_TENSION_GUIDE, out)
        self.assertNotIn("禁止使用", out)

    def test_normal_level_guide_untouched(self) -> None:
        bm = make_batch_metadata([{"区间": [1, 3], "h": 0.05}])
        t = make_translator(project_config=self._cfg())
        out = t._format_batch_metadata_block(bm, 1, 3)
        self.assertIn(NORMAL_BATCH_GUIDE, out)


class HThresholdCachingTests(unittest.TestCase):
    def test_thresholds_resolved_once_and_cached(self) -> None:
        calls = []

        class _CountingCfg(_Cfg):
            def getKey(self, key, default=None):
                calls.append(key)
                return super().getKey(key, default)

        t = make_translator(project_config=_CountingCfg())
        first = t._h_thresholds()
        second = t._h_thresholds()
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 3)  # 仅解析一次（3 个键）

    def test_forbidden_cfg_resolved_once_and_cached(self) -> None:
        calls = []

        class _CountingCfg(_Cfg):
            def getKey(self, key, default=None):
                calls.append(key)
                return super().getKey(key, default)

        t = make_translator(project_config=_CountingCfg())
        t._h_forbidden_cfg()
        t._h_forbidden_cfg()
        self.assertEqual(len(calls), 2)  # enabled + limit

    def test_forbidden_cfg_defaults_when_config_missing(self) -> None:
        t = make_translator(project_config=None)
        self.assertEqual(t._h_forbidden_cfg(), {"enabled": True, "limit": 20})

    def test_invalid_limit_falls_back_to_default(self) -> None:
        t = make_translator(
            project_config=_Cfg({"internals.hForbiddenWords.limit": "abc"})
        )
        self.assertEqual(t._h_forbidden_cfg()["limit"], 20)

    def test_zero_limit_falls_back_to_default(self) -> None:
        t = make_translator(project_config=_Cfg({"internals.hForbiddenWords.limit": 0}))
        self.assertEqual(t._h_forbidden_cfg()["limit"], 20)

    def test_bool_true_is_not_treated_as_limit_one(self) -> None:
        # bool 是 int 的子类，True 不得被当成 1 而截断词表
        t = make_translator(project_config=_Cfg({"internals.hForbiddenWords.limit": True}))
        words = [f"词{i}" for i in range(5)]
        t._h_check_words = words
        out = t._format_h_forbidden_words(t._h_forbidden_cfg()["limit"])
        self.assertIn("词4", out)
        self.assertNotIn("等词语", out)


class FormatHForbiddenWordsLimitTests(unittest.TestCase):
    def _t(self, words):
        return make_translator(project_config=_Cfg(), h_words=words)

    def test_default_limit_is_20(self) -> None:
        words = [f"词{i}" for i in range(21)]
        out = self._t(words)._format_h_forbidden_words()
        self.assertIn("词19", out)
        self.assertNotIn("词20", out)
        self.assertIn("等词语", out)

    def test_explicit_limit(self) -> None:
        words = [f"词{i}" for i in range(5)]
        out = self._t(words)._format_h_forbidden_words(3)
        self.assertIn("词2", out)
        self.assertNotIn("词3", out)

    def test_invalid_limit_falls_back(self) -> None:
        words = [f"词{i}" for i in range(5)]
        for bad in (None, 0, -1):
            out = self._t(words)._format_h_forbidden_words(bad)
            self.assertIn("词4", out, msg=f"limit={bad!r} 应回退默认 20")

    def test_empty_word_list_returns_empty(self) -> None:
        self.assertEqual(self._t([])._format_h_forbidden_words(), "")


class GroupIsHSceneThresholdTests(unittest.TestCase):
    """_group_is_h_scene 与注入侧同用 intimate 阈值（字典分流口径一致）。"""

    def _inst(self, intimate_percent):
        t = make_translator(project_config=_Cfg({
            "internals.hLevels.intimate": intimate_percent
        }))
        bm = make_batch_metadata([{"区间": [1, 10], "h": 0.45}])
        t._resolve_batch_metadata = lambda filename: bm
        t._trans_global_range = lambda group: (1, 10)
        return t

    def test_default_intimate_keeps_0_45_non_h(self) -> None:
        self.assertFalse(self._inst(50)._group_is_h_scene(None, "f.json"))

    def test_lowered_intimate_makes_0_45_h(self) -> None:
        self.assertTrue(self._inst(40)._group_is_h_scene(None, "f.json"))


class ProjectHThresholdTests(unittest.TestCase):
    """server_cache._project_h_threshold：按项目目录就近读配置 + mtime 缓存。"""

    def _write_config(self, project_dir, level_percent, name="config.inc.yaml"):
        with open(os.path.join(project_dir, name), "w", encoding="utf-8") as f:
            yaml.safe_dump(
                {"common": {"gpt": {"numPerRequestTranslate": 10}},
                 "internals": {"hLevels": {"tension": 10, "intimate": level_percent,
                                           "explicit": 90}}},
                f,
                allow_unicode=True,
            )

    def test_missing_config_returns_none(self) -> None:
        from GalTransl.server_cache import _project_h_threshold

        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(_project_h_threshold(d))

    def test_reads_configured_intimate(self) -> None:
        from GalTransl.server_cache import _project_h_threshold

        with tempfile.TemporaryDirectory() as d:
            self._write_config(d, 80)
            self.assertAlmostEqual(_project_h_threshold(d), 0.8)

    def test_falls_back_to_plain_config_yaml(self) -> None:
        from GalTransl.server_cache import _project_h_threshold

        with tempfile.TemporaryDirectory() as d:
            self._write_config(d, 30, name="config.yaml")
            self.assertAlmostEqual(_project_h_threshold(d), 0.3)

    def test_mtime_cache_invalidated_on_change(self) -> None:
        from GalTransl.server_cache import _PROJECT_H_THRESHOLD_CACHE, _project_h_threshold

        with tempfile.TemporaryDirectory() as d:
            self._write_config(d, 80)
            self.assertAlmostEqual(_project_h_threshold(d), 0.8)
            self._write_config(d, 20)
            path = os.path.join(d, "config.inc.yaml")
            os.utime(path, (os.path.getmtime(path) + 10, os.path.getmtime(path) + 10))
            self.assertAlmostEqual(_project_h_threshold(d), 0.2)
            _PROJECT_H_THRESHOLD_CACHE.pop(d, None)


if __name__ == "__main__":
    unittest.main()
