"""ForGalJsonMulitChat 批次级元数据的 H/非 H 差异化指导测试。

覆盖：
  - 纯 H 区间 → 渲染 H 段（含 H_BATCH_GUIDE + 词库禁用提示）
  - 纯非 H 区间 → 仅渲染常规段（NORMAL_BATCH_GUIDE，无 H 段）
  - 词库 ≤20 词全量注入；>20 词截断为"…………等词语"
  - 词库为空/未配置 → H 段无禁用词提示
  - 无相交区间 → 返回空串
  - _resolve_h_check_words 从项目配置 hCheckDict 惰性加载

使用 ForGalJsonMulitChat.__new__ 打桩，不触发 BaseTranslate.__init__。
"""

import json
import os
import tempfile
import unittest

import yaml

from GalTransl.Backend.ForGalJsonMulitChat import (
    BatchMetadata,
    ForGalJsonMulitChat,
)
from GalTransl.Backend.Prompts import H_BATCH_GUIDE, NORMAL_BATCH_GUIDE


def make_translator(h_words=None, project_config=None):
    """通过 __new__ 打桩批次元数据渲染所需属性。"""
    t = ForGalJsonMulitChat.__new__(ForGalJsonMulitChat)
    t._h_check_words = h_words  # 直接注入词库缓存（None 时走 project_config 加载）
    t.project_config = project_config
    return t


def make_batch_metadata(batches):
    return BatchMetadata(id="test", batches=batches)


def format_block(t, batch_metadata, lo, hi):
    return t._format_batch_metadata_block(batch_metadata, lo, hi)


class BatchMetadataHGuideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.t = make_translator(h_words=["攀上顶峰", "攀上了顶峰"])

    def test_h_only_range_renders_h_guide(self) -> None:
        bm = make_batch_metadata([{"区间": [1, 5], "h": True, "视角": "主视角", "氛围": "紧张", "用词色彩": "直白"}])
        out = format_block(self.t, bm, 1, 5)
        self.assertIn(H_BATCH_GUIDE, out)
        self.assertIn("H:是", out)
        self.assertIn("攀上顶峰", out)  # 词库全量注入
        self.assertNotIn(NORMAL_BATCH_GUIDE, out)

    def test_normal_only_range_renders_normal_guide(self) -> None:
        bm = make_batch_metadata([{"区间": [1, 5], "h": False, "视角": "主视角", "氛围": "日常", "用词色彩": "平淡"}])
        out = format_block(self.t, bm, 1, 5)
        self.assertIn(NORMAL_BATCH_GUIDE, out)
        self.assertNotIn(H_BATCH_GUIDE, out)

    def test_no_intersection_returns_empty(self) -> None:
        bm = make_batch_metadata([{"区间": [1, 5], "h": True}])
        out = format_block(self.t, bm, 10, 20)
        self.assertEqual(out, "")

    def test_word_list_full_injection_when_leq_20(self) -> None:
        words = [f"词{i}" for i in range(20)]
        t = make_translator(h_words=words)
        bm = make_batch_metadata([{"区间": [1, 3], "h": True}])
        out = format_block(t, bm, 1, 3)
        self.assertIn("词0", out)
        self.assertIn("词19", out)
        self.assertNotIn("…………等词语", out)

    def test_word_list_truncated_when_gt_20(self) -> None:
        words = [f"词{i}" for i in range(25)]
        t = make_translator(h_words=words)
        bm = make_batch_metadata([{"区间": [1, 3], "h": True}])
        out = format_block(t, bm, 1, 3)
        self.assertIn("词0", out)
        self.assertIn("词19", out)
        self.assertNotIn("词20", out)  # 第 21 个被截断
        self.assertIn("…………等词语", out)

    def test_no_words_no_forbidden_sentence(self) -> None:
        t = make_translator(h_words=[])
        bm = make_batch_metadata([{"区间": [1, 3], "h": True}])
        out = format_block(t, bm, 1, 3)
        self.assertIn(H_BATCH_GUIDE, out)
        self.assertNotIn("禁止使用", out)

    def test_mixed_ranges_renders_both(self) -> None:
        # 兼容处理：即使出现混合区间也分别渲染两段（实际流程无混合区间）
        bm = make_batch_metadata([
            {"区间": [1, 3], "h": True},
            {"区间": [4, 6], "h": False},
        ])
        out = format_block(self.t, bm, 1, 6)
        self.assertIn(H_BATCH_GUIDE, out)
        self.assertIn(NORMAL_BATCH_GUIDE, out)


class ResolveHCheckWordsTests(unittest.TestCase):
    """_resolve_h_check_words 从项目配置 forbiddenDictH 惰性加载。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        # 构造一个最小 CProjectConfig 打桩：getDictCfgSection 返回含 forbiddenDictH 的 dict
        self.pdir = os.path.join(self.tmp, "proj")
        os.makedirs(self.pdir, exist_ok=True)
        with open(os.path.join(self.pdir, "config.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(
                {
                    "dictionary": {
                        "defaultDictFolder": "Dict",
                        "forbiddenDictH": ["(project_dir)hwords.txt"],
                    }
                },
                f,
                allow_unicode=True,
            )
        with open(os.path.join(self.pdir, "hwords.txt"), "w", encoding="utf-8") as f:
            f.write("攀上顶峰\n攀上了顶峰\n")

    def _make_cfg(self):
        # 只打桩 ForGalJsonMulitChat 需要访问的 project_config 接口
        return SimpleNamespaceWithDict(self.pdir)

    def test_lazy_load_from_project_config(self) -> None:
        t = make_translator(h_words=None, project_config=self._make_cfg())
        words = t._resolve_h_check_words()
        self.assertEqual(words, ["攀上顶峰", "攀上了顶峰"])
        # 惰性：第二次调用不重复加载
        self.assertEqual(t._resolve_h_check_words(), words)

    def test_missing_file_returns_empty(self) -> None:
        pdir2 = os.path.join(self.tmp, "proj2")
        os.makedirs(pdir2, exist_ok=True)
        with open(os.path.join(pdir2, "config.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(
                {
                    "dictionary": {
                        "defaultDictFolder": "Dict",
                        "forbiddenDictH": ["(project_dir)missing.txt"],
                    }
                },
                f,
                allow_unicode=True,
            )
        t = make_translator(h_words=None, project_config=SimpleNamespaceWithDict(pdir2))
        self.assertEqual(t._resolve_h_check_words(), [])


class SimpleNamespaceWithDict:
    """模拟 CProjectConfig 供 _resolve_h_check_words 使用的最小打桩。"""

    def __init__(self, project_dir: str):
        self._pdir = project_dir
        self._dict_cfg = None

    def getDictCfgSection(self):
        if self._dict_cfg is None:
            cfg_path = os.path.join(self._pdir, "config.yaml")
            with open(cfg_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            self._dict_cfg = cfg.get("dictionary", {}) or {}
        return self._dict_cfg

    def getProjectDir(self):
        return self._pdir


if __name__ == "__main__":
    unittest.main()
