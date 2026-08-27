"""GenDic terms 模式端到端测试：真实本地提取 + mock AI 逐词翻译 + 落盘。

覆盖：基本流程（提取→翻译→写「项目GPT字典-生成.txt」）、缺失词二次补翻、
grounding 丢弃词表外输出行。AI 调用由 _FakeTermsLLM 桩替换，不发真实请求。
"""

import os
import shutil
import tempfile
import unittest
import uuid
from unittest.mock import MagicMock, patch

from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.Backend.GenDic import GenDic

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


class _FakeTermsLLM:
    """按调用轮次返回预设 TSV 的桩 LLM。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def __call__(self, prompt=None, system=None, file_name=None, max_retry_count=None, **kw):
        rsp = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
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


if __name__ == "__main__":
    unittest.main()
