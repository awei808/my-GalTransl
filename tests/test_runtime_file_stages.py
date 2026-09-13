"""文件进度阶段化回归测试：

- get_progress 按 input 文件前缀检测 pass1/pass2/pass3 阶段完成状态（校对预留）；
- file_totals 缺失（后端重启）时用缓存条目数回退进度分母；
- pass1/pass2 元数据缓存不再混入句子条目统计；
- _stage_listing 按项目键控并在 reset_project 时清除。
"""
import os
import tempfile
import unittest

import orjson

from GalTransl.server import RuntimeProgressCache
from GalTransl.server_runtime import _normalize_project_dir


def _make_cache_entry(index: int, name: str, pre_src: str, pre_dst: str) -> dict:
    return {
        "index": index,
        "name": name,
        "pre_src": pre_src,
        "post_src": pre_src,
        "pre_dst": pre_dst,
        "proofread_dst": "",
        "trans_by": "test",
        "proofread_by": "",
    }


class RuntimeFileStageTests(unittest.TestCase):
    def _make_project(self, tmpdir: str) -> str:
        cache_dir = os.path.join(tmpdir, "transl_cache")
        os.makedirs(os.path.join(cache_dir, "pass1_cache"))
        os.makedirs(os.path.join(cache_dir, "pass2_cache"))
        os.makedirs(os.path.join(cache_dir, "pass3_cache"))
        os.makedirs(os.path.join(tmpdir, "gt_input"))
        return cache_dir

    def _write_json(self, path: str, entries: list) -> None:
        with open(path, "wb") as f:
            f.write(orjson.dumps(entries, option=orjson.OPT_INDENT_2))

    def test_stages_detect_pass1_pass2_pass3(self) -> None:
        """meta/batch 缓存存在且翻译完成 → 三阶段 done；仅有 pass1 的文件只亮 meta。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = self._make_project(tmpdir)
            input_dir = os.path.join(tmpdir, "gt_input")
            # 文件 a：三阶段全有
            with open(os.path.join(input_dir, "a.json"), "w", encoding="utf-8") as f:
                f.write("[]")
            with open(os.path.join(cache_dir, "pass1_cache", "a.json.meta.json"), "wb") as f:
                f.write(b"{}")
            with open(os.path.join(cache_dir, "pass2_cache", "a.json.batch.json"), "wb") as f:
                f.write(b"{}")
            self._write_json(
                os.path.join(cache_dir, "pass3_cache", "a.json"),
                [_make_cache_entry(0, "", "A", "译A"), _make_cache_entry(1, "", "B", "译B")],
            )
            # 文件 b：仅有文件级元数据
            with open(os.path.join(input_dir, "b.json"), "w", encoding="utf-8") as f:
                f.write("[]")
            with open(os.path.join(cache_dir, "pass1_cache", "b.json.meta.json"), "wb") as f:
                f.write(b"{}")

            result = RuntimeProgressCache().get_progress(tmpdir, file_totals={}, cache_file_display_map={})

        files = {f["filename"]: f for f in result["files"]}
        stages_a = {s["key"]: s["done"] for s in files["a.json"]["stages"]}
        self.assertTrue(stages_a["meta"])
        self.assertTrue(stages_a["batch"])
        self.assertTrue(stages_a["trans"])
        # 校对阶段预留：接口存在但恒为 False
        self.assertIn("proofread", stages_a)
        self.assertFalse(stages_a["proofread"])

        self.assertIn("b.json", files)
        stages_b = {s["key"]: s["done"] for s in files["b.json"]["stages"]}
        self.assertTrue(stages_b["meta"])
        self.assertFalse(stages_b["batch"])
        self.assertFalse(stages_b["trans"])

    def test_total_falls_back_to_cache_entry_count(self) -> None:
        """后端重启后 file_totals 为空：用 pass3 缓存条目数回退分母。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = self._make_project(tmpdir)
            self._write_json(
                os.path.join(cache_dir, "pass3_cache", "a.json"),
                [
                    _make_cache_entry(0, "", "A", "译A"),
                    _make_cache_entry(1, "", "B", "译B"),
                    _make_cache_entry(2, "", "C", ""),
                ],
            )

            result = RuntimeProgressCache().get_progress(tmpdir, file_totals={}, cache_file_display_map={})

        files = {f["filename"]: f for f in result["files"]}
        info = files["a.json"]
        self.assertEqual(info["total"], 3)
        self.assertEqual(info["translated"], 2)
        stages = {s["key"]: s["done"] for s in info["stages"]}
        self.assertFalse(stages["trans"])

    def test_metadata_caches_not_counted_as_sentence_files(self) -> None:
        """pass1/pass2 元数据缓存不应以 .meta.json/.batch.json 的名字混入文件列表。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = self._make_project(tmpdir)
            self._write_json(os.path.join(cache_dir, "pass1_cache", "a.json.meta.json"), [{"plot": "x"}])
            self._write_json(os.path.join(cache_dir, "pass2_cache", "a.json.batch.json"), [{"批次": []}])

            result = RuntimeProgressCache().get_progress(tmpdir, file_totals={}, cache_file_display_map={})

        names = [f["filename"] for f in result["files"]]
        self.assertNotIn("a.json.meta.json", names)
        self.assertNotIn("a.json.batch.json", names)

    def test_metadata_control_files_excluded_from_input_listing(self) -> None:
        """FileMetaData.json 等元数据控制文件不是源文件，不应出现在文件进度列表。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._make_project(tmpdir)
            input_dir = os.path.join(tmpdir, "gt_input")
            with open(os.path.join(input_dir, "a.json"), "w", encoding="utf-8") as f:
                f.write("[]")
            for control in ("FileMetaData.json", "PlotMetadata.json", "BatchMetadata.json"):
                with open(os.path.join(input_dir, control), "w", encoding="utf-8") as f:
                    f.write("{}")

            result = RuntimeProgressCache().get_progress(tmpdir, file_totals={}, cache_file_display_map={})

        names = [f["filename"] for f in result["files"]]
        self.assertIn("a.json", names)
        for control in ("FileMetaData.json", "PlotMetadata.json", "BatchMetadata.json"):
            self.assertNotIn(control, names)

    def test_nested_input_display_name_restored_from_flattened_cache(self) -> None:
        """子目录输入文件的 pass3 缓存名（-} 扁平化）应还原为 / 分隔显示名。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = self._make_project(tmpdir)
            sub = os.path.join(tmpdir, "gt_input", "sub")
            os.makedirs(sub)
            with open(os.path.join(sub, "a.json"), "w", encoding="utf-8") as f:
                f.write("[]")
            self._write_json(
                os.path.join(cache_dir, "pass3_cache", "sub-}a.json"),
                [_make_cache_entry(0, "", "A", "译A")],
            )

            result = RuntimeProgressCache().get_progress(tmpdir, file_totals={}, cache_file_display_map={})

        files = {f["filename"]: f for f in result["files"]}
        self.assertIn("sub/a.json", files)
        self.assertEqual(files["sub/a.json"]["translated"], 1)

    def test_stage_listing_cache_is_project_scoped(self) -> None:
        """_stage_listing 按项目目录键控，reset_project 时一并清除。"""
        cache = RuntimeProgressCache()
        with tempfile.TemporaryDirectory() as tmpdir_a:
            with tempfile.TemporaryDirectory() as tmpdir_b:
                cache_dir_a = self._make_project(tmpdir_a)
                cache_dir_b = self._make_project(tmpdir_b)
                for cache_dir in (cache_dir_a, cache_dir_b):
                    self._write_json(os.path.join(cache_dir, "pass1_cache", "a.json.meta.json"), [{}])

                meta_a, batch_a = cache._get_meta_stage_listing(tmpdir_a, cache_dir_a)
                meta_b, batch_b = cache._get_meta_stage_listing(tmpdir_b, cache_dir_b)
                self.assertIn("a.json", meta_a)
                self.assertIn("a.json", meta_b)
                self.assertEqual(len(cache._stage_listing), 2)
                # 删除 B 的元数据缓存后 mtime 变化，B 重新扫描而 A 的缓存不受影响
                os.remove(os.path.join(cache_dir_b, "pass1_cache", "a.json.meta.json"))
                meta_b2, _ = cache._get_meta_stage_listing(tmpdir_b, cache_dir_b)
                self.assertNotIn("a.json", meta_b2)
                meta_a2, _ = cache._get_meta_stage_listing(tmpdir_a, cache_dir_a)
                self.assertIn("a.json", meta_a2)

            cache.reset_project(tmpdir_a)
            self.assertNotIn(
                _normalize_project_dir(tmpdir_a),
                cache._stage_listing,
            )


if __name__ == "__main__":
    unittest.main()
