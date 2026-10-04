import os
import shutil
import tempfile
import unittest

from GalTransl.Utils import get_file_list


def _touch(base_dir: str, rel_path: str) -> None:
    """在 base_dir 下按 / 分隔的相对路径创建空文件。"""
    path = os.path.join(base_dir, *rel_path.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("[]")


class GetFileListSkipTests(unittest.TestCase):
    """get_file_list 对 _/. 开头子目录的跳过，及元数据控制文件过滤的既有行为。"""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="gt_file_list_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _relative_names(self) -> set:
        return {
            os.path.relpath(p, self.root).replace(os.sep, "/")
            for p in get_file_list(self.root)
        }

    def test_underscore_prefixed_subdir_is_skipped(self) -> None:
        _touch(self.root, "a.json")
        _touch(self.root, "_excluded/index-talk.tsv")
        _touch(self.root, "_excluded/b.json")
        self.assertEqual(self._relative_names(), {"a.json"})

    def test_underscore_prefixed_nested_dirs_pruned(self) -> None:
        _touch(self.root, "a.json")
        _touch(self.root, "_excluded/deep/nested/c.json")
        self.assertEqual(self._relative_names(), {"a.json"})

    def test_dot_dirs_skipped(self) -> None:
        _touch(self.root, "a.json")
        _touch(self.root, ".git/objects/pack")
        self.assertEqual(self._relative_names(), {"a.json"})

    def test_metadata_control_files_skipped(self) -> None:
        _touch(self.root, "FileMetaData.json")
        _touch(self.root, "PlotMetadata.json")
        _touch(self.root, "BatchMetadata.json")
        _touch(self.root, "real.json")
        self.assertEqual(self._relative_names(), {"real.json"})

    def test_normal_nested_dirs_kept(self) -> None:
        _touch(self.root, "a.json")
        _touch(self.root, "sub/b.json")
        _touch(self.root, "sub/deep/c.json")
        self.assertEqual(
            self._relative_names(), {"a.json", "sub/b.json", "sub/deep/c.json"}
        )


if __name__ == "__main__":
    unittest.main()
