"""
ForFileMetaData 后端测试

覆盖：
  1. _parse_meta        —— 多种 LLM 输出形态（裸 JSON / 代码块 / <think> / 前后带散文）
  2. _normalize_meta    —— 字段数组化 / 缺失兜底 / id 强制等于文件名
  3. _save_metadata     —— 同 id 替换、异 id 追加、损坏文件安全恢复
  4. _build_glossary_text —— gpt 专名译表注入（造临时字典验证）
  5. 全流程整合         —— 把真实 test 项目拷贝到临时目录，用桩 LLM 逐文件跑
                         batch_translate，断言 gt_input 下每个待译文件都在
                         FileMetaData.json 留有对应条目（含日文名、id 规整、替换不重复）

运行方式（务必从项目根目录，使 load_guideline_file 能找到 translation_guidelines/）：
    cd D:/解包或汉化用/my-galtransl/my-GalTransl
    venv/Scripts/python.exe -m pytest tests/test_forplotmeta.py -v
或使用自带的 __main__ 入口：
    venv/Scripts/python.exe tests/test_forplotmeta.py
"""

import os
import sys
import json
import asyncio
import shutil
import tempfile
import glob
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 必须在导入 ForFileMetaData 之前 patch OpenCC，避免 BaseTranslate 初始化失败
from unittest.mock import patch, MagicMock
_patcher = patch("GalTransl.Backend.BaseTranslate.OpenCC",
                 return_value=MagicMock(convert=lambda s: s))
_patcher.start()

from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.Backend.ForFileMetaData import ForFileMetaData

# 真实 test 翻译项目（仅用于拷贝，不会改动其 gt_input/FileMetaData.json）
TEST_PROJECT = r"D:/解包或汉化用/xp3专用汉化文件夹/gal翻译/test"


class _FakeLLM:
    """确定性桩 LLM：轮转 4 种输出形态，并故意返回错误 id 以检验规整逻辑。"""

    def __init__(self):
        self.calls = 0

    async def __call__(self, messages=None, file_name="", max_retry_count=3, **kw):
        self.calls += 1
        fmt = self.calls % 4
        meta = {
            # 故意写错，验证 _normalize_meta 强制 id == filename
            "id": "SHOULD_BE_OVERWRITTEN",
            "角色": ["創", "凛音"],
            "服装": "魔女教師装扮",
            "剧情": f"这是 {file_name} 的测试剧情摘要。",
            "标签": ["教学", "道具", "正常位"],
        }
        body = json.dumps(meta, ensure_ascii=False)
        if fmt == 0:
            rsp = "```json\n" + body + "\n```"
        elif fmt == 1:
            rsp = body
        elif fmt == 2:
            rsp = "<thinking>let me analyze the script</think>\n" + body
        else:
            rsp = "好的，分析结果如下：\n" + body + "\n希望对你有帮助。"
        return (rsp, None)


class TestForFileMetaData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # load_guideline_file 以 CWD 相对路径查找 translation_guidelines/
        os.chdir(ROOT)

        # 把真实 test 项目完整拷贝到临时目录，避免污染真实 FileMetaData.json
        cls.tmp = tempfile.mkdtemp(prefix="pm_test_")
        shutil.copytree(TEST_PROJECT, cls.tmp, dirs_exist_ok=True)
        pm = os.path.join(cls.tmp, "gt_input", "FileMetaData.json")
        if os.path.exists(pm):
            os.remove(pm)  # 从零开始，验证“全量生成”

        cls.cfg = CProjectConfig(cls.tmp)

        # 绕过网络/真实 OpenAI 客户端初始化
        ForFileMetaData.init_chatbot = lambda self, *a, **k: None

        cls.backend = ForFileMetaData(cls.cfg, "ForFileMetaData", None, None)
        cls.fake = _FakeLLM()
        cls.backend.ask_chatbot = cls.fake

        cls.gt_input = os.path.join(cls.tmp, "gt_input")
        cls.input_files = sorted(
            glob.glob(os.path.join(cls.gt_input, "*.txt.json"))
        )
        cls.input_names = [os.path.basename(f) for f in cls.input_files]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- 1
    def test_parse_meta_variants(self):
        plain = '{"id":"x","角色":["a"],"服装":"","剧情":"","标签":["t"]}'
        self.assertEqual(ForFileMetaData._parse_meta(plain)["id"], "x")

        fenced = "```json\n" + plain + "\n```"
        self.assertEqual(ForFileMetaData._parse_meta(fenced)["id"], "x")

        think = "<thinking>ok</think>\n" + plain
        self.assertEqual(ForFileMetaData._parse_meta(think)["id"], "x")

        prose = "结果如下：\n" + plain + "\n完毕"
        self.assertEqual(ForFileMetaData._parse_meta(prose)["id"], "x")

        self.assertIsNone(ForFileMetaData._parse_meta(""))
        self.assertIsNone(ForFileMetaData._parse_meta("no json here"))
        self.assertIsNone(ForFileMetaData._parse_meta("{not valid"))

    # ---------------------------------------------------------------- 2
    def test_normalize_meta(self):
        raw = {"角色": "創", "标签": "教学", "服装": "x", "剧情": "y"}
        out = ForFileMetaData._normalize_meta(raw, "file.txt.json")
        self.assertEqual(out["id"], "file.txt.json")
        self.assertEqual(out["角色"], ["創"])
        self.assertEqual(out["标签"], ["教学"])
        self.assertEqual(out["服装"], "x")

        out2 = ForFileMetaData._normalize_meta({}, "f2.txt.json")
        self.assertEqual(out2["id"], "f2.txt.json")
        self.assertEqual(out2["角色"], [])
        self.assertEqual(out2["标签"], [])
        self.assertEqual(out2["服装"], "")
        self.assertEqual(out2["剧情"], "")

    # ---------------------------------------------------------------- 3
    def test_save_metadata_merge_and_corruption(self):
        sub = tempfile.mkdtemp()
        try:
            # _save_metadata 写入 projectConfig.getCachePath() + "/pass1_cache/"
            cache_sub = os.path.join(sub, "pass1_cache")
            with patch.object(
                self.backend.pj_config, "getCachePath", return_value=sub
            ):
                os.makedirs(cache_sub, exist_ok=True)
                # 预置一条 id=A 的旧数据
                with open(os.path.join(cache_sub, "FileMetaData.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        [{"id": "A", "角色": [], "服装": "", "剧情": "old", "标签": []}],
                        f, ensure_ascii=False,
                    )
                # 同 id 替换
                self.backend._save_metadata(
                    {"id": "A", "角色": ["創"], "服装": "", "剧情": "new", "标签": ["t"]}
                )
                with open(os.path.join(cache_sub, "FileMetaData.json"), encoding="utf-8") as f:
                    arr = json.load(f)
                self.assertEqual(len(arr), 1)
                self.assertEqual(arr[0]["剧情"], "new")

                # 异 id 追加
                self.backend._save_metadata(
                    {"id": "B", "角色": [], "服装": "", "剧情": "b", "标签": []}
                )
                with open(os.path.join(cache_sub, "FileMetaData.json"), encoding="utf-8") as f:
                    arr = json.load(f)
                self.assertEqual(len(arr), 2)
                self.assertIn("B", [e["id"] for e in arr])

                # 损坏文件：应安全重置为 [meta]，不崩溃
                with open(os.path.join(cache_sub, "FileMetaData.json"), "w", encoding="utf-8") as f:
                    f.write("{ this is not valid json ")
                self.backend._save_metadata(
                    {"id": "C", "角色": [], "服装": "", "剧情": "c", "标签": []}
                )
                with open(os.path.join(cache_sub, "FileMetaData.json"), encoding="utf-8") as f:
                    arr = json.load(f)
                self.assertEqual(len(arr), 1)
                self.assertEqual(arr[0]["id"], "C")
        finally:
            shutil.rmtree(sub, ignore_errors=True)

    # ---------------------------------------------------------------- 4
    def test_glossary_injection(self):
        gdir = tempfile.mkdtemp()
        try:
            with open(os.path.join(gdir, "GPT字典.txt"), "w", encoding="utf-8") as f:
                f.write("華恋\t华恋\n創\t创\n")
            fake_dict_cfg = {"gpt.dict": ["GPT字典.txt"], "defaultDictFolder": gdir}
            with patch.object(
                self.backend.pj_config, "getDictCfgSection", return_value=fake_dict_cfg
            ):
                text = self.backend._build_glossary_text()
            self.assertIn("# Glossary", text)
            self.assertIn("華恋", text)
            self.assertIn("华恋", text)
        finally:
            shutil.rmtree(gdir, ignore_errors=True)

    # ---------------------------------------------------------------- 5
    def test_full_pipeline_completeness(self):
        self.assertGreater(len(self.input_files), 0, "test 项目 gt_input 应有待译文件")

        for f in self.input_files:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            base = os.path.basename(f)
            ok = asyncio.run(self.backend.batch_translate(data, filename=base))
            self.assertTrue(ok, f"batch_translate 失败：{base}")

        from GalTransl import PASS1_CACHE_DIR
        pm_path = os.path.join(self.tmp, "transl_cache", PASS1_CACHE_DIR, "FileMetaData.json")
        self.assertTrue(os.path.exists(pm_path), "未生成 FileMetaData.json")
        with open(pm_path, encoding="utf-8") as f:
            arr = json.load(f)
        ids = [e.get("id") for e in arr]

        # 每条输入文件都有对应元数据
        self.assertEqual(
            set(ids), set(self.input_names), "FileMetaData 的 id 集合与待译文件不一致"
        )
        # 无重复、无遗漏
        self.assertEqual(len(arr), len(self.input_names), "条目数与文件数不符（重复或缺失）")
        # id 被强制规整为文件名（含日文名），而非 LLM 返回的错误 id
        self.assertNotIn("SHOULD_BE_OVERWRITTEN", ids)
        self.assertIn("00_01_アバンタイトル.txt.json", ids)

        # 每条结构正确
        for e in arr:
            self.assertIsInstance(e["角色"], list)
            self.assertIsInstance(e["标签"], list)
            self.assertIsInstance(e["剧情"], str)

        # 重新跑其中一个文件：应“替换”而非“追加”
        one = self.input_names[0]
        with open(os.path.join(self.gt_input, one), encoding="utf-8") as f:
            one_data = json.load(f)
        asyncio.run(self.backend.batch_translate(one_data, filename=one))
        with open(pm_path, encoding="utf-8") as f:
            arr2 = json.load(f)
        self.assertEqual(len(arr2), len(self.input_names), "重跑应替换而非重复")

    # ---------------------------------------------------------------- 6
    def test_prompt_no_leaked_placeholders(self):
        with open(self.input_files[0], encoding="utf-8") as fh:
            data = json.load(fh)
        script = self.backend._build_script_text(data)
        glossary = self.backend._build_glossary_text()
        prompt = self.backend._build_prompt_request(script, glossary)
        self.assertNotIn("[Input]", prompt)
        self.assertNotIn("[Glossary]", prompt)
        self.assertNotIn("[translation_guideline]", prompt)
        # 脚本正文确实进入了提示词
        self.assertIn("夢", prompt)
        # 默认开启注入，且本项目已配置翻译规范 -> 规范块应进入提示词
        guideline = getattr(self.backend.pj_config, "translation_guideline", "") or ""
        if guideline.strip():
            self.assertIn("# 翻译规范（translation_guideline）", prompt)

    # ---------------------------------------------------------------- 7
    def test_guideline_injection_toggle(self):
        """验证 translation_guideline 的可控注入：开启/关闭/空值三种情形。"""
        saved = getattr(self.backend.pj_config, "translation_guideline", "")
        try:
            # 情形 A：开启 + 有规范内容 -> 注入带标题的规范块
            self.backend._inject_guideline = True
            self.backend.pj_config.translation_guideline = "【测试规范】专有名词须保留原文。"
            out = self.backend._build_prompt_request("SCRIPT", "GLOSS")
            self.assertIn("# 翻译规范（translation_guideline）", out)
            self.assertIn("【测试规范】专有名词须保留原文。", out)
            self.assertNotIn("[translation_guideline]", out)
            self.assertIn("SCRIPT", out)
            self.assertIn("GLOSS", out)

            # 情形 B：关闭注入 -> 规范块（含标题）完全不出现
            self.backend._inject_guideline = False
            out2 = self.backend._build_prompt_request("SCRIPT", "GLOSS")
            self.assertNotIn("# 翻译规范（translation_guideline）", out2)
            self.assertNotIn("【测试规范】", out2)

            # 情形 C：开启但规范为空 -> 不留悬挂标题、不留占位符
            self.backend._inject_guideline = True
            self.backend.pj_config.translation_guideline = ""
            out3 = self.backend._build_prompt_request("SCRIPT", "GLOSS")
            self.assertNotIn("# 翻译规范（translation_guideline）", out3)
            self.assertNotIn("[translation_guideline]", out3)
        finally:
            self.backend.pj_config.translation_guideline = saved
            self.backend._inject_guideline = True


if __name__ == "__main__":
    unittest.main(verbosity=2)
