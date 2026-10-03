"""GenDic terms 模式端到端测试：真实本地提取 + mock AI 逐词翻译 + 落盘。

覆盖：基本流程（提取→翻译→写「项目GPT字典-生成.txt」）、缺失词二次补翻、
grounding 丢弃词表外输出行。AI 调用由 _FakeTermsLLM 桩替换，不发真实请求。
"""

import asyncio
import os
import shutil
import tempfile
import threading
import unittest
import uuid
from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock, patch

from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.Backend.GenDic import GenDic, GENDIC_SHARD_DIR_NAME
from GalTransl.Service import JobCancelledError
from GalTransl.server_runtime import WORKER_ID_CTX

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

CONFIG_YAML_LLM = CONFIG_YAML + """\
internals:
  gendic:
    mode: llm
    llm_chunk_size: 500
"""


def _mkdtemp_writable(prefix: str) -> str:
    """创建可写临时目录（与 tests/test_gendic_terms.py 同法）。"""
    base = tempfile.gettempdir()
    for _ in range(100):
        path = os.path.join(base, f"{prefix}{uuid.uuid4().hex[:10]}")
        try:
            os.makedirs(path)
            return path
        except FileExistsError:
            continue
    raise RuntimeError(f"无法在 {base} 下创建唯一临时目录")


def _wipe_gendic_shards(cfg: CProjectConfig) -> None:
    """清空断点续跑分片缓存（transl_cache/gendic_cache）。

    GenDic 现已跨运行持久化批次分片缓存；同项目多用例共享夹具时，
    断言 LLM 交互次数的用例需在每用例开始前清场，否则前一用例的分片会让批次全部命中、LLM 零请求。
    """
    shutil.rmtree(os.path.join(cfg.getCachePath(), GENDIC_SHARD_DIR_NAME), ignore_errors=True)


class _FakeTermsLLM:
    """按调用轮次返回预设 TSV 的桩 LLM。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def __call__(self, prompt=None, system=None, file_name=None, max_retry_count=None, **kw):
        rsp = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return (rsp, None)


class _RecordingLLM:
    """记录每次调用时 WORKER_ID_CTX 槽位的桩 LLM；可选模拟延迟与首次调用失败。"""

    def __init__(self, responses, delay: float = 0.0, fail_first: bool = False):
        self.responses = list(responses)
        self.calls = 0
        self.worker_ids: List[str] = []
        self.delay = delay
        self.fail_first = fail_first

    async def __call__(self, prompt=None, system=None, file_name=None, max_retry_count=None, **kw):
        self.worker_ids.append(WORKER_ID_CTX.get())
        call_index = self.calls
        self.calls += 1
        if self.fail_first and call_index == 0:
            raise RuntimeError("mock llm failure")
        if self.delay:
            await asyncio.sleep(self.delay)
        rsp = self.responses[min(call_index, len(self.responses) - 1)]
        return (rsp, None)


class GenDicTermsE2ETests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        os.chdir(ROOT)
        # 绕过 OpenCC 与真实 OpenAI 客户端初始化（同 test_forbatchmeta 模式）
        cls._opencc_patcher = patch(
            "GalTransl.Backend.BaseEngine.OpenCC",
            return_value=MagicMock(convert=lambda s: s),
        )
        cls._opencc_patcher.start()
        GenDic.init_chatbot = lambda self, *a, **k: None

        cls.tmp = _mkdtemp_writable("gendic_e2e_")
        with open(os.path.join(cls.tmp, "config.yaml"), "w", encoding="utf-8") as f:
            f.write(CONFIG_YAML)
        cls.cfg = CProjectConfig(cls.tmp)
        cls.dic_path = os.path.join(cls.tmp, "项目GPT字典-生成.txt")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        cls._opencc_patcher.stop()

    def setUp(self) -> None:
        _wipe_gendic_shards(self.cfg)

    def _backend(self, responses) -> GenDic:
        backend = GenDic(self.cfg, "GenDic", None, None)
        backend.ask_chatbot = _FakeTermsLLM(responses)
        return backend

    def _input(self) -> list:
        # 词表期望：サキュバス/フィギュア(片假名普通名词≥2)；凛音(人名不收录)；撮影(汉字普通名词)被丢弃
        return [
            {"name": "凛音", "message": "サキュバスのフィギュアを撮影する。"},
            {"name": "凛音", "message": "フィギュア造りが好きだ。"},
            {"name": "凛音", "message": "またサキュバスに会う。"},
        ]

    def _read_dic(self) -> list:
        if not os.path.exists(self.dic_path):
            return []
        with open(self.dic_path, "r", encoding="utf-8") as f:
            return [l for l in f.read().splitlines() if l.strip() and not l.startswith("//")]

    def test_load_existing_generated_terms_tab_fallback(self) -> None:
        # 旧版 Tab 分隔的生成字典（升级残留）→ 归一为 | 后按 src 收集，不产生含 Tab 的垃圾条目
        with open(self.dic_path, "w", encoding="utf-8") as f:
            f.write("// 格式说明行\nサキュバス\t魅魔\t术语\nフィギュア|手办|物品\n")
        backend = self._backend([])
        terms = backend._load_existing_generated_terms(self.dic_path)
        self.assertEqual(terms, {"サキュバス", "フィギュア"})

    def test_load_commented_terms_tab_fallback(self) -> None:
        # 旧版 Tab 分隔的 // 注释停用词 → 归一为 | 后仍能恢复停用状态
        with open(self.dic_path, "w", encoding="utf-8") as f:
            f.write("// 格式说明行\n//淫乱奴隷\t淫乱奴隶\t术语（疑似H）\n")
        backend = self._backend([])
        commented = backend._load_commented_terms_from_generated()
        self.assertEqual(commented, {"淫乱奴隷": ("淫乱奴隶", "术语（疑似H）")})

    async def test_terms_basic_flow_writes_dictionary(self) -> None:
        backend = self._backend([
            "日文原词|中文翻译|备注\n凛音|凛音|人名，女性\nサキュバス|魅魔|术语\nフィギュア|手办|物品\n",
        ])
        ok = await backend.batch_translate(self._input())
        self.assertTrue(ok)
        joined = "\n".join(self._read_dic())
        self.assertNotIn("凛音|凛音", joined)  # 人名不收录
        self.assertIn("サキュバス|魅魔", joined)
        self.assertIn("フィギュア|手办", joined)
        self.assertEqual(getattr(self.cfg, "gendic_added_count", 0), 2)

    async def test_terms_grounding_drops_out_of_table_rows(self) -> None:
        backend = self._backend([
            "日文原词|中文翻译|备注\n凛音|凛音|人名，女性\nホテル|酒店|术语\n",
        ])
        ok = await backend.batch_translate(self._input())
        self.assertTrue(ok)
        joined = "\n".join(self._read_dic())
        self.assertNotIn("凛音", joined)  # 人名词表外 + 人名不收录
        self.assertNotIn("ホテル", joined)  # 词表外行被 grounding 丢弃

    async def test_terms_missing_rows_retried_in_second_pass(self) -> None:
        backend = self._backend([
            "日文原词|中文翻译|备注\n凛音|凛音|人名，女性\nサキュバス|魅魔|术语\n",
            "日文原词|中文翻译|备注\nフィギュア|手办|物品\n",
        ])
        ok = await backend.batch_translate(self._input())
        self.assertTrue(ok)
        joined = "\n".join(self._read_dic())
        self.assertIn("フィギュア|手办", joined)
        self.assertIn("サキュバス|魅魔", joined)
        self.assertGreaterEqual(backend.ask_chatbot.calls, 2)  # 二次补翻确实发生

    async def test_terms_rerun_overwrites_not_appends(self) -> None:
        # 重复运行：生成字典覆盖写（不追加累积重复词条，保证条目数 = 本次结果）
        backend = self._backend([
            "日文原词|中文翻译|备注\n凛音|凛音|人名，女性\nサキュバス|魅魔|术语\nフィギュア|手办|物品\n",
        ])
        ok1 = await backend.batch_translate(self._input())
        self.assertTrue(ok1)
        first_count = len(self._read_dic())
        ok2 = await backend.batch_translate(self._input())
        self.assertTrue(ok2)
        entries = self._read_dic()
        self.assertEqual(len(entries), first_count)  # 不翻倍累积
        self.assertEqual(len({l.split("|")[0] for l in entries}), first_count)  # 无重复词条

    async def test_terms_suspicious_note_commented_in_dictionary(self) -> None:
        # AI 备注标注「疑似H」→ 落盘原文前加 // 注释（防止解析，手动删 // 启用）；
        # 词表外行（思い切り掻き混ぜ）被 grounding 丢弃
        filler = "私はフィギュアの造形が好きで、毎日模型を制作している。" * 100
        inp = [
            {"name": "凛音", "message": "淫乱奴隷を買った。淫乱奴隷だ。淫乱奴隷だ。" + filler},
            {"name": "凛音", "message": "サキュバスに会う。サキュバスだ。" + filler},
        ]
        backend = self._backend([
            "日文原词|中文翻译|备注\nサキュバス|魅魔|术语\n淫乱奴隷|淫乱奴隶|术语（疑似H）\n思い切り掻き混ぜ|使劲搅拌|动词短语（疑似非术语）\n",
        ])
        ok = await backend.batch_translate(inp)
        self.assertTrue(ok)
        with open(self.dic_path, encoding="utf-8") as f:
            raw = f.read()  # 不过滤 // 行（// 正是被注释的疑似词）
        self.assertIn("//淫乱奴隷", raw)          # 疑似H 已注释
        self.assertIn("サキュバス|魅魔", raw)     # 正常词不受影响
        self.assertNotIn("\n淫乱奴隷|", raw)     # 未注释版本不存在
        self.assertNotIn("思い切り掻き混ぜ", raw)  # 词表外行被 grounding 丢弃


class GenDicWorkerDisplayTests(unittest.IsolatedAsyncioTestCase):
    """worker 槽位绑定与运行时显示：workers_active 为真实在飞数、按 worker 分板块推送结果预览。"""

    @classmethod
    def setUpClass(cls):
        os.chdir(ROOT)
        cls._opencc_patcher = patch(
            "GalTransl.Backend.BaseEngine.OpenCC",
            return_value=MagicMock(convert=lambda s: s),
        )
        cls._opencc_patcher.start()
        GenDic.init_chatbot = lambda self, *a, **k: None

        cls.tmp = _mkdtemp_writable("gendic_worker_disp_")
        with open(os.path.join(cls.tmp, "config.yaml"), "w", encoding="utf-8") as f:
            f.write(CONFIG_YAML)
        cls.cfg = CProjectConfig(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        cls._opencc_patcher.stop()

    def setUp(self) -> None:
        _wipe_gendic_shards(self.cfg)

    def _backend(self, llm) -> GenDic:
        backend = GenDic(self.cfg, "GenDic", None, None)
        backend.ask_chatbot = llm
        backend.gendic_batch_size = 1  # 2 个候选词拆 2 批，配合 workersPerProject:2 验证并发
        return backend

    def _input(self) -> list:
        return [
            {"name": "凛音", "message": "サキュバスのフィギュアを撮影する。"},
            {"name": "凛音", "message": "フィギュア造りが好きだ。"},
            {"name": "凛音", "message": "またサキュバスに会う。"},
        ]

    async def test_workers_active_reports_true_inflight(self) -> None:
        captured: List[Dict[str, Any]] = []

        def _fake_update(project_dir, **kwargs):
            captured.append(dict(kwargs))

        llm = _RecordingLLM(
            ["日文原词|中文翻译|备注\nサキュバス|魅魔|术语\nフィギュア|手办|物品\n"], delay=0.05
        )
        backend = self._backend(llm)
        with patch("GalTransl.server.update_runtime_status", side_effect=_fake_update):
            ok = await backend.batch_translate(self._input())
        self.assertTrue(ok)
        actives = [k["workers_active"] for k in captured if "workers_active" in k]
        self.assertTrue(actives)
        # 两批并发时真实在飞 2（旧公式 max(0, wokers-completed) 只会衰减，永远到不了 2）
        self.assertEqual(max(actives), 2)
        self.assertTrue(all(0 <= a <= 2 for a in actives))
        # 槽位池完整归还（泄漏会在后续任务取槽时 IndexError）
        self.assertEqual(sorted(backend._free_worker_slots), [0, 1])

    async def test_worker_slots_and_per_worker_translation_preview(self) -> None:
        pushed: List[Tuple[str, str]] = []

        def _fake_snippets(project_dir, prompt_preview="", translation_preview="", worker_id="", filename=""):
            if translation_preview:
                pushed.append((WORKER_ID_CTX.get(), translation_preview))

        llm = _RecordingLLM(
            ["日文原词|中文翻译|备注\nサキュバス|魅魔|术语\nフィギュア|手办|物品\n"], delay=0.05
        )
        backend = self._backend(llm)
        with patch("GalTransl.Backend.GenDic.set_live_snippets", side_effect=_fake_snippets):
            ok = await backend.batch_translate(self._input())
        self.assertTrue(ok)
        self.assertEqual(llm.calls, 2)
        # 两批并发绑定到两个不同 worker 槽位（未绑定时 contextvar 恒为 "-1"）
        self.assertEqual(set(llm.worker_ids), {"0", "1"})
        run_pushes = [(w, t) for w, t in pushed if w in {"0", "1"}]
        self.assertEqual(len(run_pushes), 2)  # 每批运行中各推送一次结果预览
        joined = "\n".join(t for _, t in run_pushes)
        self.assertIn("サキュバス|魅魔", joined)
        self.assertIn("フィギュア|手办", joined)
        # 最终落盘的整体字典推送（公共 key "-1"）保留
        self.assertIn("-1", [w for w, _ in pushed])
        self.assertEqual(sorted(backend._free_worker_slots), [0, 1])

    async def test_cancellation_restores_worker_slots(self) -> None:
        # 停止请求经 _check_stop_requested 在后续任务边界触发（ask_chatbot 内异常会被
        # llm_translate_terms_batch 吞掉转空结果，非取消传播路径）：槽位池必须完整清理。
        # 4 批任务 + workersPerProject:2 → 前两批完成后第 3 批在任务边界命中停止事件。
        stop_event = threading.Event()

        class _StopSettingLLM:
            async def __call__(self, **kw):
                stop_event.set()
                return ("日文原词|中文翻译|备注\nサキュバス|魅魔|术语\n", None)

        inp = self._input() + [
            {"name": "凛音", "message": "コスプレのマンガを読む。"},
            {"name": "凛音", "message": "マンガのコスプレを見る。"},
        ]
        backend = self._backend(_StopSettingLLM())
        backend.pj_config.stop_event = stop_event
        self.addCleanup(setattr, backend.pj_config, "stop_event", None)
        with self.assertRaises(JobCancelledError):
            await backend.batch_translate(inp)
        self.assertEqual(sorted(backend._free_worker_slots), [0, 1])


class GenDicLlmE2ETests(unittest.IsolatedAsyncioTestCase):
    """LLM 全权模式（mode=llm）：压缩文本切块 → AI 提取 → 简单去重直接进词典（不筛选）。"""

    @classmethod
    def setUpClass(cls):
        os.chdir(ROOT)
        cls._opencc_patcher = patch(
            "GalTransl.Backend.BaseEngine.OpenCC",
            return_value=MagicMock(convert=lambda s: s),
        )
        cls._opencc_patcher.start()
        GenDic.init_chatbot = lambda self, *a, **k: None

        cls.tmp = _mkdtemp_writable("gendic_llm_e2e_")
        with open(os.path.join(cls.tmp, "config.yaml"), "w", encoding="utf-8") as f:
            f.write(CONFIG_YAML_LLM)
        cls.cfg = CProjectConfig(cls.tmp)
        cls.dic_path = os.path.join(cls.tmp, "项目GPT字典-生成.txt")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        cls._opencc_patcher.stop()

    def setUp(self) -> None:
        _wipe_gendic_shards(self.cfg)

    def _backend(self, responses) -> GenDic:
        backend = GenDic(self.cfg, "GenDic", None, None)
        self.assertEqual(backend.gendic_mode, "llm")
        self.assertEqual(backend.gendic_llm_chunk_size, 500)
        backend.ask_chatbot = _FakeTermsLLM(responses)
        return backend

    def _input(self) -> list:
        # 足够长触发多块切分；含术语与拟声/人名
        base = "サキュバスのフィギュアを撮影する。凛音はコスプレイヤーだ。"
        return [{"name": "凛音", "message": base * 40}]

    def _read_dic(self) -> list:
        if not os.path.exists(self.dic_path):
            return []
        with open(self.dic_path, "r", encoding="utf-8") as f:
            return [l for l in f.read().splitlines() if l.strip() and not l.startswith("//")]

    async def test_llm_extract_writes_dictionary_unfiltered(self) -> None:
        # 不筛选词汇（用户决策）：AI 提取的术语（含人名/拟声 note）直接进词典；
        # 仅「（无法翻译）」在解析层跳过
        backend = self._backend([
            "日文原词|中文翻译|备注\nサキュバス|魅魔|术语\n凛音|凛音|人名\nフィギュア|手办|物品\nむー|（无法翻译）|拟声\n",
        ])
        ok = await backend.batch_translate(self._input())
        self.assertTrue(ok)
        joined = "\n".join(self._read_dic())
        self.assertIn("サキュバス|魅魔", joined)
        self.assertIn("凛音|凛音", joined)  # 人名不筛选
        self.assertIn("フィギュア|手办", joined)
        self.assertNotIn("むー", joined)  # （无法翻译）解析层丢弃
        self.assertGreaterEqual(backend.ask_chatbot.calls, 1)

    def _big_input(self) -> list:
        # 互不重复的句子（TextCompressor 仅折叠完全重复行）→ 压缩后仍足够长，切块数 ≥2
        base = "".join(
            f"サキュバス{i}のフィギュアを撮影する。凛音はコスプレイヤー{i}だ。"
            for i in range(400)
        )
        return [{"name": "凛音", "message": base}]

    async def test_llm_worker_slots_bound_and_inflight_capped(self) -> None:
        captured: List[Dict[str, Any]] = []

        def _fake_update(project_dir, **kwargs):
            captured.append(dict(kwargs))

        llm = _RecordingLLM(
            ["日文原词|中文翻译|备注\nサキュバス|魅魔|术语\nフィギュア|手办|物品\n"], delay=0.02
        )
        backend = self._backend([])
        backend.ask_chatbot = llm
        backend.gendic_llm_chunk_size = 1000  # 压缩文本切块 ≥2 才能验证并发
        with patch("GalTransl.server.update_runtime_status", side_effect=_fake_update):
            ok = await backend.batch_translate(self._big_input())
        self.assertTrue(ok)
        self.assertGreaterEqual(llm.calls, 2)
        # 并发块绑定到 0..wokers-1 的不同槽位（未绑定时 contextvar 恒为 "-1"）
        self.assertEqual(len(set(llm.worker_ids)), 2)
        self.assertTrue(set(llm.worker_ids) <= {"0", "1"})
        actives = [k["workers_active"] for k in captured if "workers_active" in k]
        self.assertTrue(actives)
        self.assertEqual(max(actives), 2)
        self.assertTrue(all(0 <= a <= 2 for a in actives))
        self.assertEqual(sorted(backend._free_worker_slots), [0, 1])

    async def test_llm_failed_chunk_counts_into_progress(self) -> None:
        captured: List[Dict[str, Any]] = []
        progress_calls: List[Tuple[int, bool, str]] = []

        def _fake_update(project_dir, **kwargs):
            captured.append(dict(kwargs))

        def _fake_append(task_index, success, message=""):
            progress_calls.append((task_index, success, message))

        llm = _RecordingLLM(
            ["日文原词|中文翻译|备注\nサキュバス|魅魔|术语\n"], fail_first=True
        )
        backend = self._backend([])
        backend.ask_chatbot = llm
        backend.gendic_llm_chunk_size = 1000
        with patch("GalTransl.server.update_runtime_status", side_effect=_fake_update), patch.object(
            backend, "_append_runtime_progress", side_effect=_fake_append
        ):
            ok = await backend.batch_translate(self._big_input())
        self.assertTrue(ok)  # 失败块放弃，其余块仍产出词条
        self.assertGreaterEqual(llm.calls, 2)
        failures = [c for c in progress_calls if not c[1]]
        self.assertEqual(len(failures), 1)  # 失败块记 FAILED 进度行
        files = [k["current_file"] for k in captured if k.get("current_file", "").startswith("已完成")]
        self.assertTrue(files)
        first, total = files[-1].replace("已完成", "").replace("块", "").strip().split("/")
        self.assertEqual(first, total)  # 失败块也计入已完成（旧代码停滞在 n-1/n）


if __name__ == "__main__":
    unittest.main()
