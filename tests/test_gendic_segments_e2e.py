"""GenDic segments 模式回归测试（mode=segments 走旧链路，防分派改动破坏回退路径）。

与 terms 模式 e2e 共用桩 LLM 模式：真实分词/提取 + mock AI 逐段响应 + 落盘断言。
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
  printTranslationLogInTerminal: false
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


def _mkdtemp_writable(prefix: str) -> str:
    base = tempfile.gettempdir()
    for _ in range(100):
        path = os.path.join(base, f"{prefix}{uuid.uuid4().hex[:10]}")
        try:
            os.makedirs(path)
            return path
        except FileExistsError:
            continue
    raise RuntimeError(f"无法在 {base} 下创建唯一临时目录")


class _FakeSegmentsLLM:
    """segments 模式桩：每段返回同一 TSV（多段同词 → 投票聚合路径被覆盖）。"""

    def __init__(self, tsv: str):
        self.tsv = tsv
        self.calls = 0

    async def __call__(self, prompt=None, system=None, file_name=None, max_retry_count=None, **kw):
        self.calls += 1
        return (self.tsv, None)


class GenDicSegmentsE2ETests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        os.chdir(ROOT)
        cls._opencc_patcher = patch(
            "GalTransl.Backend.BaseEngine.OpenCC",
            return_value=MagicMock(convert=lambda s: s),
        )
        cls._opencc_patcher.start()
        GenDic.init_chatbot = lambda self, *a, **k: None

        cls.tmp = _mkdtemp_writable("gendic_seg_e2e_")
        with open(os.path.join(cls.tmp, "config.yaml"), "w", encoding="utf-8") as f:
            f.write(CONFIG_YAML)
        cls.cfg = CProjectConfig(cls.tmp)
        cls.dic_path = os.path.join(cls.tmp, "项目GPT字典-生成.txt")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        cls._opencc_patcher.stop()

    def _backend(self, tsv: str) -> GenDic:
        backend = GenDic(self.cfg, "GenDic", None, None)
        self.assertEqual(backend.gendic_mode, "segments")  # 分派配置生效
        backend.ask_chatbot = _FakeSegmentsLLM(tsv)
        return backend

    def _input(self) -> list:
        # 多段含同词 サキュバス/フィギュア → segments 投票聚合后保留
        return [
            {"name": "凛音", "message": "サキュバスのフィギュアを撮影する。"},
            {"name": "凛音", "message": "またサキュバスに会う。"},
            {"name": "凛音", "message": "フィギュア造りが好きだ。"},
        ]

    def _read_dic(self) -> list:
        if not os.path.exists(self.dic_path):
            return []
        with open(self.dic_path, "r", encoding="utf-8") as f:
            return [l for l in f.read().splitlines() if l.strip() and not l.startswith("#")]

    async def test_segments_mode_uses_legacy_flow_and_writes_dictionary(self) -> None:
        backend = self._backend("サキュバス\t魅魔\t术语\nフィギュア\t手办\t物品\n")
        ok = await backend.batch_translate(self._input())
        self.assertTrue(ok)
        self.assertGreater(backend.ask_chatbot.calls, 0)  # 确实走了分段→AI 链路
        joined = "\n".join(self._read_dic())
        self.assertIn("サキュバス\t魅魔", joined)
        self.assertIn("フィギュア\t手办", joined)
        self.assertGreaterEqual(getattr(self.cfg, "gendic_added_count", 0), 2)


if __name__ == "__main__":
    unittest.main()
