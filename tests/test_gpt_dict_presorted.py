# -*- coding: utf-8 -*-
"""CGptDict 场景预切分缓存（_get_scene_presorted）单元测试。

验证：
  - 预切分缓存内容与逐次现算结果逐项相等（全量 / h组 / 非h组 / h+非h组合）；
  - check_dic_use 使用缓存后 all / nh / h 三场景输出与原逐句现算口径一致
    （含正则词条、长词优先消费去重）；
  - gen_prompt 使用缓存后 h/非 h 重合词条 h 优先语义不变；
  - sort_dic / load_dic 变更词条后缓存失效，新词条参与检查。
"""
import os
import tempfile
import unittest

from GalTransl.CSentense import CSentense
from GalTransl.Dictionary import CGptDict, _sorted_by_len_desc


def _write_dic(tmp: str, name: str, content: str) -> str:
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class PresortedCacheTests(unittest.TestCase):
    """预切分缓存的正确性、场景语义与失效"""

    def _make_gd(self, tmp: str) -> CGptDict:
        # h 字典（文件名含 _h）：魔剣→魔剑；非 h 字典：剣→圣剑 + 正则词条
        h_path = _write_dic(tmp, "gpt_h.txt", "魔剣|魔剑|h注释\n")
        nh_path = _write_dic(tmp, "gpt.txt", "剣|圣剑\nre:厄[災]+|厄X\n")
        return CGptDict([h_path, nh_path])

    def _tran(self, src: str, dst: str) -> CSentense:
        tran = CSentense(pre_src=src)
        tran.post_src = src
        tran.pre_dst = dst
        tran.post_dst = dst
        return tran

    def test_presorted_matches_fresh_compute(self) -> None:
        gd = self._make_gd(tempfile.mkdtemp())
        all_sorted, h_sorted, nh_sorted, h_combined = gd._get_scene_presorted()
        self.assertEqual(all_sorted, _sorted_by_len_desc(gd._dic_list))
        self.assertEqual(
            h_sorted, _sorted_by_len_desc([d for d in gd._dic_list if gd._is_h_dict(d)])
        )
        self.assertEqual(
            nh_sorted, _sorted_by_len_desc([d for d in gd._dic_list if not gd._is_h_dict(d)])
        )
        self.assertEqual(h_combined, h_sorted + nh_sorted)

    def test_repeated_calls_use_cache_same_result(self) -> None:
        gd = self._make_gd(tempfile.mkdtemp())
        tran = self._tran("魔剣と厄災", "别的")
        first = gd.check_dic_use("别的", tran)
        self.assertIsNotNone(gd._scene_presorted)  # 首次调用后缓存已生成
        self.assertIn("厄[災]+---厄X", first)
        self.assertIn("魔剣---魔剑", first)
        self.assertNotIn("剣---圣剑", first)  # 长词优先消费，短词不重复报
        second = gd.check_dic_use("别的", tran)
        self.assertEqual(first, second)

    def test_check_dic_use_scene_nh_skips_h_dict(self) -> None:
        gd = self._make_gd(tempfile.mkdtemp())
        tran = self._tran("魔剣", "别的")
        out = gd.check_dic_use("别的", tran, scene="nh")
        # nh 场景只检查非 h 字典：h 词条「魔剣」不参与，「剣」如实报告
        self.assertNotIn("魔剣---魔剑", out)
        self.assertIn("剣---圣剑", out)

    def test_check_dic_use_scene_h_checks_h_first(self) -> None:
        gd = self._make_gd(tempfile.mkdtemp())
        tran = self._tran("魔剣", "别的")
        out = gd.check_dic_use("别的", tran, scene="h")
        # h 场景：h 词条先检查并消费其覆盖范围，短的非 h 重叠词不再误报
        self.assertIn("魔剣---魔剑", out)
        self.assertNotIn("剣---圣剑", out)

    def test_gen_prompt_scene_h_priority_unchanged(self) -> None:
        # h/非 h 重合词条（同搜索词）：scene=h 时只取 h 的译文
        tmp = tempfile.mkdtemp()
        h_path = _write_dic(tmp, "gpt_h.txt", "魔剣|魔剑\n")
        nh_path = _write_dic(tmp, "gpt.txt", "魔剣|魔剑NH\n")
        gd = CGptDict([h_path, nh_path])
        tran = self._tran("魔剣", "魔剑")
        prompt = gd.gen_prompt([tran], type="gpt", scene="h")
        self.assertIn("| 魔剣 | 魔剑 |", prompt)
        self.assertNotIn("魔剑NH", prompt)

    def test_sort_dic_invalidates_cache(self) -> None:
        gd = self._make_gd(tempfile.mkdtemp())
        gd._get_scene_presorted()
        self.assertIsNotNone(gd._scene_presorted)
        gd.sort_dic()
        self.assertIsNone(gd._scene_presorted)

    def test_load_dic_after_init_invalidates_and_participates(self) -> None:
        tmp = tempfile.mkdtemp()
        gd = self._make_gd(tmp)
        gd._get_scene_presorted()
        extra = _write_dic(tmp, "gpt_extra.txt", "追加词|追加译\n")
        gd.load_dic(extra)
        self.assertIsNone(gd._scene_presorted)  # 初始化后加载已使缓存失效
        tran = self._tran("追加词", "别的")
        out = gd.check_dic_use("别的", tran)
        self.assertIn("追加词---追加译", out)


if __name__ == "__main__":
    unittest.main()
