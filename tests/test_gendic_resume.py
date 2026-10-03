"""GenDic 断点续跑（分片缓存）测试：写入、全命中复用、指纹失效、forceRegenDic 跳过、损坏忽略、取消恢复。

分片缓存位于 transl_cache/gendic_cache/，按 模式-b<索引>.json 命名，dict 形状
（进度扫描只把 list 形状 JSON 当句子条目解析，dict 形状避免污染翻译进度）。
AI 调用由桩替换，不发真实请求。
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
import uuid
from typing import List, Tuple
from unittest.mock import MagicMock, patch

from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.Backend.GenDic import GenDic, GENDIC_SHARD_DIR_NAME, GENDIC_SHARD_SCHEMA
from GalTransl.Service import JobCancelledError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_YAML = """\
common:
  language: zh-cn
  workersPerProject: 2
internals:
  gendic:
    mode: terms
backendSpecific:
  OpenAI-Compatible:
    tokens:
      - token: mock-key
        endpoint: http://127.0.0.1:9
        modelName: mock-model
"""

CONFIG_YAML_LLM = """\
common:
  language: zh-cn
  workersPerProject: 2
internals:
  gendic:
    mode: llm
    llm_chunk_size: 500
backendSpecific:
  OpenAI-Compatible:
    tokens:
      - token: mock-key
        endpoint: http://127.0.0.1:9
        modelName: mock-model
"""

CONFIG_YAML_SEGMENTS = """\
common:
  language: zh-cn
  workersPerProject: 2
internals:
  gendic:
    mode: segments
backendSpecific:
  OpenAI-Compatible:
    tokens:
      - token: mock-key
        endpoint: http://127.0.0.1:9
        modelName: mock-model
"""

CONFIG_YAML_FORCE_REGEN = """\
common:
  language: zh-cn
  workersPerProject: 1
internals:
  gendic:
    mode: terms
  pipeline:
    forceRegenDic: true
backendSpecific:
  OpenAI-Compatible:
    tokens:
      - token: mock-key
        endpoint: http://127.0.0.1:9
        modelName: mock-model
"""

FULL_TERMS_RESPONSE = "日文原词|中文翻译|备注\n凛音|凛音|人名，女性\nサキュバス|魅魔|术语\nフィギュア|手办|物品\n"


def _mkdtemp_writable(prefix: str) -> str:
    """创建可写临时目录（与 tests/test_gendic_terms_e2e.py 同法）。"""
    base = tempfile.gettempdir()
    for _ in range(100):
        path = os.path.join(base, f"{prefix}{uuid.uuid4().hex[:10]}")
        try:
            os.makedirs(path)
            return path
        except FileExistsError:
            continue
    raise RuntimeError(f"无法在 {base} 下创建唯一临时目录")


class _FakeTermsLLM:
    """按调用轮次返回预设 TSV 的桩 LLM。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def __call__(self, prompt=None, system=None, file_name=None, max_retry_count=None, **kw):
        rsp = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return (rsp, None)


class _ExplodingLLM:
    """任何调用即抛错的桩：断点全命中时 LLM 不应被请求。"""

    def __init__(self):
        self.calls = 0

    async def __call__(self, **kw):
        self.calls += 1
        raise RuntimeError("断点缓存应命中，LLM 不应被调用")


class _CountingLLM:
    """计数并返回固定响应的桩 LLM。"""

    def __init__(self, response: str):
        self.response = response
        self.calls = 0

    async def __call__(self, prompt=None, system=None, file_name=None, max_retry_count=None, **kw):
        self.calls += 1
        return (self.response, None)


class _CancelOnSecondCallLLM:
    """首次调用返回正常响应；第二次调用先置位停止事件再抛 JobCancelledError。"""

    def __init__(self, response: str, stop_event: threading.Event):
        self.response = response
        self.stop_event = stop_event
        self.calls = 0

    async def __call__(self, prompt=None, system=None, file_name=None, max_retry_count=None, **kw):
        self.calls += 1
        if self.calls >= 2:
            self.stop_event.set()
            raise JobCancelledError()
        return (self.response, None)


class GenDicResumeTestBase(unittest.IsolatedAsyncioTestCase):
    """公共骨架：patch OpenCC/init_chatbot、临时项目、分片缓存与生成字典的每用例清场。"""

    CONFIG = CONFIG_YAML

    @classmethod
    def setUpClass(cls):
        os.chdir(ROOT)
        cls._opencc_patcher = patch(
            "GalTransl.Backend.BaseEngine.OpenCC",
            return_value=MagicMock(convert=lambda s: s),
        )
        cls._opencc_patcher.start()
        GenDic.init_chatbot = lambda self, *a, **k: None

        cls.tmp = _mkdtemp_writable("gendic_resume_")
        with open(os.path.join(cls.tmp, "config.yaml"), "w", encoding="utf-8") as f:
            f.write(cls.CONFIG)
        cls.cfg = CProjectConfig(cls.tmp)
        cls.dic_path = os.path.join(cls.tmp, "项目GPT字典-生成.txt")
        cls.shard_dir = os.path.join(cls.cfg.getCachePath(), GENDIC_SHARD_DIR_NAME)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        cls._opencc_patcher.stop()

    def setUp(self) -> None:
        # 每个用例独立起点：清空分片缓存与生成字典、复位停止事件（cfg/项目目录复用）
        shutil.rmtree(self.shard_dir, ignore_errors=True)
        if os.path.exists(self.dic_path):
            os.remove(self.dic_path)
        self.cfg.stop_event = None

    def _backend(self, llm) -> GenDic:
        backend = GenDic(self.cfg, "GenDic", None, None)
        backend.ask_chatbot = llm
        return backend

    def _input(self) -> list:
        # 词表期望：サキュバス/フィギュア(片假名普通名词≥2)；凛音(人名不收录)
        return [
            {"name": "凛音", "message": "サキュバスのフィギュアを撮影する。"},
            {"name": "凛音", "message": "フィギュア造りが好きだ。"},
            {"name": "凛音", "message": "またサキュバスに会う。"},
        ]

    def _read_dic(self) -> List[str]:
        if not os.path.exists(self.dic_path):
            return []
        with open(self.dic_path, "r", encoding="utf-8") as f:
            return [
                l for l in f.read().splitlines()
                if l.strip() and not l.startswith("//") and not l.startswith("#")
            ]

    def _shard_files(self) -> List[str]:
        if not os.path.isdir(self.shard_dir):
            return []
        return sorted(
            f for f in os.listdir(self.shard_dir) if f.endswith(".json")
        )

    def _read_shard(self, name: str) -> dict:
        with open(os.path.join(self.shard_dir, name), "r", encoding="utf-8") as f:
            return json.load(f)


class GenDicTermsResumeTests(GenDicResumeTestBase):
    """terms 模式断点续跑：批次分片写入 → 重跑全命中零请求；失效/损坏/强制重算。"""

    async def test_first_run_writes_shards_second_run_all_hits(self) -> None:
        backend1 = self._backend(_FakeTermsLLM([FULL_TERMS_RESPONSE]))
        ok1 = await backend1.batch_translate(self._input())
        self.assertTrue(ok1)
        self.assertGreaterEqual(len(self._shard_files()), 1)
        first_dic = self._read_dic()
        self.assertIn("サキュバス|魅魔", "\n".join(first_dic))

        # 模拟崩溃恢复：删除最终字典，重跑时全部批次命中分片，LLM 零请求
        os.remove(self.dic_path)
        llm2 = _ExplodingLLM()
        backend2 = self._backend(llm2)
        ok2 = await backend2.batch_translate(self._input())
        self.assertTrue(ok2)
        self.assertEqual(llm2.calls, 0)
        self.assertGreaterEqual(backend2._shard_hit_count, 1)
        self.assertEqual(self._read_dic(), first_dic)  # 断点恢复后字典与首跑一致

    async def test_fingerprint_mismatch_recomputes(self) -> None:
        backend1 = self._backend(_FakeTermsLLM([FULL_TERMS_RESPONSE]))
        await backend1.batch_translate(self._input())
        shard_name = self._shard_files()[0]
        shard = self._read_shard(shard_name)
        shard["fingerprint"] = "0" * 64  # 伪造过期指纹
        with open(os.path.join(self.shard_dir, shard_name), "w", encoding="utf-8") as f:
            json.dump(shard, f, ensure_ascii=False)

        llm2 = _CountingLLM(FULL_TERMS_RESPONSE)
        backend2 = self._backend(llm2)
        ok = await backend2.batch_translate(self._input())
        self.assertTrue(ok)
        self.assertGreaterEqual(llm2.calls, 1)  # 指纹不匹配 → 重算
        self.assertIn("サキュバス|魅魔", "\n".join(self._read_dic()))

    async def test_force_regen_bypasses_shard_cache(self) -> None:
        backend1 = self._backend(_FakeTermsLLM([FULL_TERMS_RESPONSE]))
        await backend1.batch_translate(self._input())

        llm2 = _CountingLLM(FULL_TERMS_RESPONSE)
        backend2 = self._backend(llm2)
        backend2.gendic_force_regen = True  # forceRegenDic 语义：忽略分片复用
        ok = await backend2.batch_translate(self._input())
        self.assertTrue(ok)
        self.assertGreaterEqual(llm2.calls, 1)
        self.assertEqual(backend2._shard_hit_count, 0)

    async def test_corrupted_shard_ignored_and_rewritten(self) -> None:
        backend1 = self._backend(_FakeTermsLLM([FULL_TERMS_RESPONSE]))
        await backend1.batch_translate(self._input())
        shard_name = self._shard_files()[0]
        with open(os.path.join(self.shard_dir, shard_name), "w", encoding="utf-8") as f:
            f.write("{not json")  # 写一半崩溃的残骸

        llm2 = _CountingLLM(FULL_TERMS_RESPONSE)
        backend2 = self._backend(llm2)
        ok = await backend2.batch_translate(self._input())
        self.assertTrue(ok)
        self.assertGreaterEqual(llm2.calls, 1)
        repaired = self._read_shard(shard_name)  # 重算后分片被有效内容覆盖
        self.assertEqual(repaired["schema"], GENDIC_SHARD_SCHEMA)
        self.assertIn("サキュバス|魅魔", "\n".join(self._read_dic()))

    async def test_shard_files_dict_shaped_and_stale_tmp_cleaned(self) -> None:
        # dict 形状（非 list）：server_runtime 进度扫描只把 list 形状 JSON 当句子条目解析
        os.makedirs(self.shard_dir, exist_ok=True)
        with open(os.path.join(self.shard_dir, "terms-b0.json.tmp"), "w", encoding="utf-8") as f:
            f.write("junk")  # 模拟上次中断残留
        backend1 = self._backend(_FakeTermsLLM([FULL_TERMS_RESPONSE]))
        ok = await backend1.batch_translate(self._input())
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(os.path.join(self.shard_dir, "terms-b0.json.tmp")))
        for name in self._shard_files():
            shard = self._read_shard(name)
            self.assertIsInstance(shard, dict)
            self.assertEqual(shard["schema"], GENDIC_SHARD_SCHEMA)
            self.assertTrue(shard.get("fingerprint"))
            self.assertTrue(shard.get("entries"))

    async def test_cancellation_persists_shards_and_resume_completes(self) -> None:
        # batch_size=1 → 2 个候选词 2 批；workers=1 顺序执行：批次 0 完成落分片，
        # 批次 1 请求时取消 → 部分字典 + 分片保留；重跑只补批次 1，批次 0 零请求。
        stop_event = threading.Event()
        backend1 = self._backend(_CancelOnSecondCallLLM(FULL_TERMS_RESPONSE, stop_event))
        backend1.gendic_batch_size = 1
        backend1.wokers = 1
        backend1.pj_config.stop_event = stop_event
        with self.assertRaises(JobCancelledError):
            await backend1.batch_translate(self._input())
        self.assertTrue(getattr(self.cfg, "gendic_partial_saved", False))
        self.assertEqual(self._shard_files(), ["terms-b0.json"])  # 已完成批次 0 的分片保留
        partial_terms = {l.split("|")[0] for l in self._read_dic()}
        self.assertEqual(len(partial_terms), 1)  # 部分字典只含已完成批次的词

        backend2 = self._backend(_CountingLLM(FULL_TERMS_RESPONSE))
        backend2.gendic_batch_size = 1
        backend2.wokers = 1
        backend2.pj_config.stop_event = None
        ok = await backend2.batch_translate(self._input())
        self.assertTrue(ok)
        self.assertEqual(backend2.ask_chatbot.calls, 1)  # 只补取消的批次 1
        self.assertEqual(backend2._shard_hit_count, 1)
        joined = "\n".join(self._read_dic())
        self.assertIn("サキュバス|魅魔", joined)
        self.assertIn("フィギュア|手办", joined)  # 恢复后字典完整


class GenDicLlmResumeTests(GenDicResumeTestBase):
    """llm 全权模式断点续跑：块分片写入 → 重跑全命中零请求。"""

    CONFIG = CONFIG_YAML_LLM

    def _big_input(self) -> list:
        # 互不重复的句子（TextCompressor 仅折叠完全重复行）→ 压缩后仍足够长，切块数 ≥2
        base = "".join(
            f"サキュバス{i}のフィギュアを撮影する。凛音はコスプレイヤー{i}だ。"
            for i in range(400)
        )
        return [{"name": "凛音", "message": base}]

    async def test_llm_first_run_writes_shards_second_run_all_hits(self) -> None:
        backend1 = self._backend(_FakeTermsLLM([
            "日文原词|中文翻译|备注\nサキュバス|魅魔|术语\nフィギュア|手办|物品\n",
        ]))
        backend1.gendic_llm_chunk_size = 1000
        ok1 = await backend1.batch_translate(self._big_input())
        self.assertTrue(ok1)
        shard_names = [n for n in self._shard_files() if n.startswith("llm-b")]
        self.assertGreaterEqual(len(shard_names), 2)
        first_dic = self._read_dic()

        os.remove(self.dic_path)
        llm2 = _ExplodingLLM()
        backend2 = self._backend(llm2)
        backend2.gendic_llm_chunk_size = 1000
        ok2 = await backend2.batch_translate(self._big_input())
        self.assertTrue(ok2)
        self.assertEqual(llm2.calls, 0)  # 所有块命中断点缓存
        self.assertEqual(self._read_dic(), first_dic)


class GenDicSegmentsResumeTests(GenDicResumeTestBase):
    """segments 模式断点续跑：分片命中 → 词条回放进 dic_counter/votes → 字典完整重建。"""

    CONFIG = CONFIG_YAML_SEGMENTS

    async def test_segments_first_run_writes_shards_second_run_all_hits(self) -> None:
        backend1 = self._backend(_FakeTermsLLM([
            "日文原词|中文翻译|备注\nサキュバス|魅魔|术语\n",
        ]))
        ok1 = await backend1.batch_translate(self._input())
        self.assertTrue(ok1)
        shard_names = [n for n in self._shard_files() if n.startswith("segments-b")]
        self.assertGreaterEqual(len(shard_names), 1)
        first_dic = self._read_dic()
        self.assertIn("サキュバス|魅魔", "\n".join(first_dic))

        # 模拟崩溃恢复：删除最终字典，重跑全部片段命中分片，LLM 零请求
        os.remove(self.dic_path)
        llm2 = _ExplodingLLM()
        backend2 = self._backend(llm2)
        ok2 = await backend2.batch_translate(self._input())
        self.assertTrue(ok2)
        self.assertEqual(llm2.calls, 0)
        self.assertEqual(self._read_dic(), first_dic)  # 回放累积后字典与首跑一致


class GenDicShardUnitTests(GenDicResumeTestBase):
    """分片机制单测：指纹依赖、损坏/脏数据防御、原子写、回放累积。"""

    async def test_fingerprint_depends_on_prompt_and_model_salt(self) -> None:
        backend = self._backend(_ExplodingLLM())
        fp1 = backend._shard_fingerprint("sys", "prompt-a")
        fp2 = backend._shard_fingerprint("sys", "prompt-b")
        self.assertNotEqual(fp1, fp2)
        self.assertEqual(fp1, backend._shard_fingerprint("sys", "prompt-a"))  # 确定性

        # 模型名集合进入指纹：换模型自动失效
        token = type("T", (), {"model_name": "model-x"})()
        backend.tokenProvider = MagicMock(tokens=[(True, token)])
        fp3 = backend._shard_fingerprint("sys", "prompt-a")
        self.assertNotEqual(fp1, fp3)

    async def test_load_shard_rejects_dirty_data(self) -> None:
        backend = self._backend(_ExplodingLLM())
        fp = backend._shard_fingerprint("sys", "p")
        # 空结果从未入缓存：空 entries 视为脏数据
        backend._save_shard("terms", 0, fp, [])
        self.assertEqual(backend._load_shard("terms", 0, fp), [])
        # 非 list entries 视为脏数据
        backend._save_shard("terms", 1, fp, [("あ", "a", "n")])
        path = os.path.join(self.shard_dir, "terms-b1.json")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["entries"] = "garbage"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.assertEqual(backend._load_shard("terms", 1, fp), [])

    async def test_save_shard_atomic_no_tmp_leftover(self) -> None:
        backend = self._backend(_ExplodingLLM())
        fp = backend._shard_fingerprint("sys", "p")
        backend._save_shard("terms", 0, fp, [("サキュバス", "魅魔", "术语")])
        self.assertEqual(self._shard_files(), ["terms-b0.json"])  # 无 .tmp 残留
        cached = backend._load_shard("terms", 0, fp)
        self.assertEqual(cached, [("サキュバス", "魅魔", "术语")])

    async def test_accumulate_segment_entries_replay(self) -> None:
        backend = self._backend(_ExplodingLLM())
        entries: List[Tuple[str, str, str]] = [("サキュバス", "魅魔", "术语")]
        backend._accumulate_segment_entries(entries)
        self.assertEqual(backend.dic_counter["サキュバス"], 1)
        self.assertEqual(backend.dic_list, [["サキュバス", "魅魔", "术语"]])
        # 断点回放（或跨片段重复出现）：计数与投票累积
        backend._accumulate_segment_entries(entries)
        self.assertEqual(backend.dic_counter["サキュバス"], 2)
        self.assertEqual(backend.dic_votes["サキュバス"][("魅魔", "术语")], 2)
        self.assertEqual(len(backend.dic_list), 1)  # 首见入列，不重复

    async def test_force_regen_config_wiring(self) -> None:
        # YAML internals.pipeline.forceRegenDic: true → 引擎跳过分片复用；
        # 与流水线 _run_stage_gen_dic 阶段跳过判定读同一配置键
        tmp2 = _mkdtemp_writable("gendic_resume_fr_")
        self.addCleanup(shutil.rmtree, tmp2, ignore_errors=True)
        with open(os.path.join(tmp2, "config.yaml"), "w", encoding="utf-8") as f:
            f.write(CONFIG_YAML_FORCE_REGEN)
        cfg2 = CProjectConfig(tmp2)
        self.assertTrue(cfg2.getKey("internals.pipeline.forceRegenDic", False))  # 流水线侧读取点
        backend = GenDic(cfg2, "GenDic", None, None)
        self.assertTrue(backend.gendic_force_regen)  # 引擎侧读取点
        self.assertEqual(backend._load_shard("terms", 0, "any"), [])  # force_regen 下分片一律不复用


if __name__ == "__main__":
    unittest.main()
