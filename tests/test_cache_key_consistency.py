"""缓存键口径一致性（M12）与 name 键缺失容错（M13）回归测试。

M12：快照侧建键跳过 post_src 为空的条目（与 _build_cache_key_for_tran / trans 侧读取一致），
消除旧版/手工缓存中空 post_src 条目导致的键分裂（同一逻辑句出现两个缓存键）。
M13：缓存条目缺 name 键时快照建键不再 KeyError（原实现被外层 except 吞成整文件读取失败）。
"""

import json
import os
import tempfile
import unittest

from GalTransl.Cache import (
    _build_cache_dict_from_snapshot,
    _build_cache_key_for_tran,
    get_transCache_from_json,
)
from GalTransl.CSentense import CSentense
from GalTransl.Loader import load_transList


class CacheKeyConsistencyTests(unittest.TestCase):
    def _snapshot_with_empty_neighbor(self) -> list:
        # 快照含中间的空 post_src 条目 E（模拟旧版/手工缓存）
        return [
            {"index": 1, "name": "A", "pre_src": "A文", "post_src": "A文", "pre_dst": "A译"},
            {"index": 2, "name": "", "pre_src": "E文", "post_src": "", "pre_dst": ""},
            {"index": 3, "name": "B", "pre_src": "B文", "post_src": "B文", "pre_dst": "B译"},
        ]

    def test_snapshot_key_skips_empty_post_src_neighbor(self) -> None:
        # M12：A 的快照键应跳过空 post_src 的 E 取 B 作后句，与 trans 侧键一致
        cache_dict, _ = _build_cache_dict_from_snapshot(self._snapshot_with_empty_neighbor())

        a = CSentense("A文", "A", 1)
        a.post_src = "A文"
        e = CSentense("E文", "", 2)
        e.post_src = ""
        b = CSentense("B文", "B", 3)
        b.post_src = "B文"
        a.next_tran = e
        e.prev_tran = a
        e.next_tran = b
        b.prev_tran = e

        key_a = _build_cache_key_for_tran(a)
        self.assertIn(key_a, cache_dict)  # 修复前快照键含 E（"NoneAA文E文"）→ 不在 dict 中
        self.assertEqual(cache_dict[key_a]["index"], 1)

    def test_snapshot_key_of_empty_entry_matches_trans_key(self) -> None:
        # M12：E 自身的键两侧一致（A/B 邻居 post_src 非空，无需跳过）
        cache_dict, _ = _build_cache_dict_from_snapshot(self._snapshot_with_empty_neighbor())

        a = CSentense("A文", "A", 1)
        a.post_src = "A文"
        e = CSentense("E文", "", 2)
        e.post_src = ""
        b = CSentense("B文", "B", 3)
        b.post_src = "B文"
        a.next_tran = e
        e.prev_tran = a
        e.next_tran = b
        b.prev_tran = e

        key_e = _build_cache_key_for_tran(e)
        self.assertIn(key_e, cache_dict)


class CacheReadMissingNameTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_name_key_does_not_break_cache_read(self) -> None:
        # M13：缺 name 键的缓存条目不再导致整个缓存读取失败
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "a.json")
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(
                    [{"index": 1, "pre_src": "A", "post_src": "A", "pre_dst": "A译"}],
                    f,
                )
            trans_list, _ = load_transList([{"index": 1, "message": "A"}])
            hit, unhit = await get_transCache_from_json(trans_list, cache_path)
            self.assertEqual(len(hit), 1)
            self.assertEqual(hit[0].pre_dst, "A译")
            self.assertEqual(len(unhit), 0)


if __name__ == "__main__":
    unittest.main()
