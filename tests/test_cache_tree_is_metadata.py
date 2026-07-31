"""回归测试：缓存目录树 is_metadata 判断。

pass0/1/2_cache 是元数据（GlobalPrompt / *.meta.json / *.batch.json），
pass3_cache 是翻译缓存（*.txt.json），不应标记为元数据。
"""

import os
import tempfile
import unittest

from GalTransl.server import _build_cache_tree


class CacheTreeIsMetadataTests(unittest.TestCase):
    """回归：pass3_cache 文件不再被误标为元数据（修复 441-444 行的目录列表）"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        for d in ("pass0_cache", "pass1_cache", "pass2_cache", "pass3_cache"):
            os.makedirs(os.path.join(self.root, d), exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, rel: str) -> str:
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("[]")
        return path

    def _find_file(self, nodes: list, rel: str):
        for n in nodes:
            if n.get("path") == rel:
                return n
            for c in n.get("children", []):
                r = self._find_file([c], rel)
                if r:
                    return r
        return None

    def test_pass3_file_is_not_metadata(self) -> None:
        """pass3_cache 翻译缓存不应标记为元数据（本次修复的回归锚点）"""
        self._write("pass3_cache/00_02.txt.json")
        nodes = _build_cache_tree(self.root)
        n = self._find_file(nodes, "pass3_cache/00_02.txt.json")
        self.assertIsNotNone(n, "pass3 文件节点未找到")
        self.assertFalse(n["is_metadata"])

    def test_pass0_file_is_metadata(self) -> None:
        """pass0_cache 全局提示词是元数据"""
        self._write("pass0_cache/GlobalPrompt.json")
        nodes = _build_cache_tree(self.root)
        n = self._find_file(nodes, "pass0_cache/GlobalPrompt.json")
        self.assertIsNotNone(n)
        self.assertTrue(n["is_metadata"])

    def test_pass1_file_is_metadata(self) -> None:
        """pass1_cache 文件级元数据"""
        self._write("pass1_cache/00_02.meta.json")
        nodes = _build_cache_tree(self.root)
        n = self._find_file(nodes, "pass1_cache/00_02.meta.json")
        self.assertIsNotNone(n)
        self.assertTrue(n["is_metadata"])

    def test_pass2_file_is_metadata(self) -> None:
        """pass2_cache 批次级元数据"""
        self._write("pass2_cache/00_02.batch.json")
        nodes = _build_cache_tree(self.root)
        n = self._find_file(nodes, "pass2_cache/00_02.batch.json")
        self.assertIsNotNone(n)
        self.assertTrue(n["is_metadata"])

    def test_root_file_is_not_metadata(self) -> None:
        """缓存根目录下普通文件不是元数据"""
        self._write("config.json")
        nodes = _build_cache_tree(self.root)
        n = self._find_file(nodes, "config.json")
        self.assertIsNotNone(n)
        self.assertFalse(n["is_metadata"])


if __name__ == "__main__":
    unittest.main()
