"""重建引擎（rebuildr / rebuilda）测试：注册、缓存不完整报错与端到端重建。"""

import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace

import orjson

from GalTransl.Backend.BaseEngine import ENGINE_REGISTRY
from GalTransl.Backend.RebuildTranslate import CRebuildTranslate, REBUILD_ENGINES
from GalTransl.Service import JobSpec, run_job_async


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _read_json(path: str):
    return orjson.loads(_read_bytes(path))


def _write_config(proj: str) -> None:
    # Runner 初始化会读取 plugin.textPlugins / plugin.filePlugin，最小配置需带上
    with open(os.path.join(proj, "config.yaml"), "w", encoding="utf-8") as f:
        f.write(
            "common: {}\nplugin:\n  filePlugin: file_galtransl_json\n  textPlugins: []\n"
        )


INPUT_LINES = [
    {"message": "あいうえお"},
    {"message": "かきくけこ"},
]

CACHE_ENTRIES = [
    {
        "index": 1,
        "name": "",
        "pre_src": "あいうえお",
        "post_src": "あいうえお",
        "pre_dst": "你好世界呀",
        "trans_by": "model-x",
    },
    {
        "index": 2,
        "name": "",
        "pre_src": "かきくけこ",
        "post_src": "かきくけこ",
        "pre_dst": "今天天气真好",
        "trans_by": "model-x",
    },
]

_CACHE_PATH_PARTS = ("transl_cache", "pass3_cache", "demo.json")


class RebuildEngineUnitTests(unittest.TestCase):
    def _engine(self, eng_type: str) -> CRebuildTranslate:
        return CRebuildTranslate(SimpleNamespace(), eng_type)

    def test_engines_registered(self) -> None:
        self.assertEqual(REBUILD_ENGINES, ("rebuildr", "rebuilda"))
        for name in REBUILD_ENGINES:
            self.assertIn(name, ENGINE_REGISTRY)
            engine = ENGINE_REGISTRY[name](SimpleNamespace(), name, None, None)
            self.assertIsInstance(engine, CRebuildTranslate)
            self.assertEqual(engine.eng_type, name)

    def test_batch_translate_raises_on_unhit(self) -> None:
        unhit = [SimpleNamespace(index=3, pre_src="「こんにちは」")]
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(
                self._engine("rebuildr").batch_translate(
                    "a.json", "cache", [], 10, translist_unhit=unhit
                )
            )
        message = str(ctx.exception)
        self.assertIn("缓存不完整", message)
        self.assertIn("#3", message)
        self.assertIn("a.json", message)

    def test_batch_translate_passes_when_all_hit(self) -> None:
        trans = [SimpleNamespace(index=1, pre_src="あ")]
        result = asyncio.run(
            self._engine("rebuilda").batch_translate(
                "a.json", "cache", trans, 10, translist_unhit=[]
            )
        )
        self.assertIs(result, trans)

    def test_unhit_examples_truncated(self) -> None:
        unhit = [SimpleNamespace(index=i, pre_src="あ" * 40) for i in range(8)]
        message = self._engine("rebuildr")._incomplete_message("a.json", unhit)
        self.assertIn("等 8 句", message)
        self.assertIn("…", message)


class RebuildEngineEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def _run_rebuild(self, proj: str, translator: str):
        return await run_job_async(
            JobSpec(
                project_dir=proj,
                config_file_name="config.yaml",
                translator=translator,
                job_id=f"rebuild-e2e-{translator}",
            )
        )

    async def test_rebuildr_rebuilds_output_without_touching_cache(self) -> None:
        with tempfile.TemporaryDirectory() as proj:
            _write_config(proj)
            _write_json(os.path.join(proj, "gt_input", "demo.json"), INPUT_LINES)
            cache_path = os.path.join(proj, *_CACHE_PATH_PARTS)
            _write_json(cache_path, CACHE_ENTRIES)
            cache_before = _read_bytes(cache_path)

            state = await self._run_rebuild(proj, "rebuildr")

            self.assertTrue(state.success, state.error)
            out = _read_json(os.path.join(proj, "gt_output", "demo.json"))
            self.assertEqual(out[0]["message"], "你好世界呀")
            self.assertEqual(out[1]["message"], "今天天气真好")
            self.assertEqual(_read_bytes(cache_path), cache_before)

    async def test_rebuilda_refreshes_cache_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as proj:
            _write_config(proj)
            _write_json(os.path.join(proj, "gt_input", "demo.json"), INPUT_LINES)
            cache_path = os.path.join(proj, *_CACHE_PATH_PARTS)
            # 预置旧 problem：rebuilda 重跑 find_problems 后应被清空
            entries = [dict(CACHE_ENTRIES[0], problem="残留日文：あいうえお"), CACHE_ENTRIES[1]]
            _write_json(cache_path, entries)

            state = await self._run_rebuild(proj, "rebuilda")

            self.assertTrue(state.success, state.error)
            out = _read_json(os.path.join(proj, "gt_output", "demo.json"))
            self.assertEqual(out[0]["message"], "你好世界呀")
            written = _read_json(cache_path)
            self.assertNotIn("problem", written[0])
            self.assertEqual(written[0]["pre_dst"], "你好世界呀")
            self.assertEqual(written[1]["pre_dst"], "今天天气真好")

    async def test_rebuild_fails_when_cache_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as proj:
            _write_config(proj)
            _write_json(os.path.join(proj, "gt_input", "demo.json"), INPUT_LINES)
            cache_path = os.path.join(proj, *_CACHE_PATH_PARTS)
            _write_json(cache_path, [CACHE_ENTRIES[0]])  # 缺第 2 句

            state = await self._run_rebuild(proj, "rebuildr")

            self.assertFalse(state.success)
            self.assertIn("缓存不完整", state.error)
            self.assertFalse(os.path.isfile(os.path.join(proj, "gt_output", "demo.json")))
            # 缓存必须原样保留
            self.assertEqual(_read_json(cache_path)[0]["pre_dst"], "你好世界呀")


if __name__ == "__main__":
    unittest.main()
