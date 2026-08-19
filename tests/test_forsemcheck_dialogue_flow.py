# -*- coding: utf-8 -*-
"""ForSemCheck 语义检测流程回归：find_problems 前必须恢复对话符号，不得误报「本有引号」。

背景（2026-08 口径不一致）：ForSemCheck 的 _semcheck_single_file 在 find_problems
前漏调 postprocess_trans_list（recover_dialogue_symbol），analyse_dialogue 剥离的
「」未补回 post_dst，标点错漏检测把「本有引号」误写进缓存；而侧边栏全量重检
（server._run_problem_detection 含后处理）不报，两侧口径不一致。

本测试走 run_galtransl 真实分派（doLLMTranslate 的 ForSemCheck 分支，真实执行
_semcheck_single_file 全流程），仅 mock 两处网络依赖：
  - ensure_model_available_if_needed（模型可用性网络检查）
  - ForSemCheck.batch_translate（模拟 index=2 命中疑似错误，不真发 HTTP）

断言语义检测写回的缓存：带「」的对话条目不得出现「本有引号」，且
post_dst_preview 已恢复对话符号（与主翻译路径/重检路径口径一致）；
语义检测命中的条目仍正常认领「疑似错误」。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from GalTransl.ConfigHelper import CProjectConfig  # noqa: E402
from GalTransl.Runner import run_galtransl  # noqa: E402


def _mkdtemp_writable(prefix: str) -> str:
    """创建可写临时目录。

    tempfile.mkdtemp 默认以 0o700 模式建目录，在受限沙箱环境下其内部文件
    不可写；改用无 mode 的 os.makedirs + uuid 保证唯一，正常环境行为一致。
    """
    base = tempfile.gettempdir()
    for _ in range(100):
        path = os.path.join(base, f"{prefix}{uuid.uuid4().hex[:10]}")
        try:
            os.makedirs(path)
            return path
        except FileExistsError:
            continue
    raise RuntimeError(f"无法在 {base} 下创建唯一临时目录")

# 最小可跑项目配置：翻译已由主流程完成（预置缓存），语义检测仅标记 + 重跑问题检测
MINI_CONFIG = """# 端到端测试用最小项目配置（ForSemCheck 语义检测流程）
backendSpecific:
  OpenAI-Compatible:
    tokens:
      - token: sk-test
        endpoint: http://127.0.0.1:9999
        modelName: deepseek-chat

plugin:
  filePlugin: file_galtransl_json
  textPlugins: []

common:
  gpt.numPerRequestBetter: 100
  gpt.numPerRequestSemCheck: 20
  gpt.translation_guideline: "Basic.md"
  language: "ja2zh-cn"
  loggingLevel: error
  splitFile: "no"
  workersPerProject: 1

problemAnalyze:
  problemList:
    - 标点错漏
    - 疑似错误

dictionary:
  defaultDictFolder: Dict
  preDict: []
  postDict: []
  gpt.dict: []

proxy:
  enableProxy: false
"""


def _build_mini_project(root: str) -> str:
    """构造临时项目：gt_input 单个对话文件 + 预置已翻译缓存（pre_dst 为裸译文，无「」）。"""
    proj = os.path.join(root, "mini_proj")
    os.makedirs(os.path.join(proj, "gt_input"), exist_ok=True)
    os.makedirs(os.path.join(proj, "transl_cache", "pass3_cache"), exist_ok=True)
    with open(os.path.join(proj, "config.inc.yaml"), "w", encoding="utf-8") as f:
        f.write(MINI_CONFIG)

    lines = [
        {"index": 1, "name": "？？？", "message": "「クルト……いるんでしょ……？」"},
        {"index": 2, "name": "創", "message": "「ふぅ……」"},
        {"index": 3, "name": "", "message": "そうだね、散歩に行こう。"},
    ]
    with open(
        os.path.join(proj, "gt_input", "scene_01.txt.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(lines, f, ensure_ascii=False)

    # 主流程翻译完成后的缓存形态：post_src 已剥离「」（analyse_dialogue），
    # pre_dst 为裸译文（无「」），post_dst_preview 暂缺（语义检测会重写）。
    cache = [
        {
            "index": 1,
            "name": "？？？",
            "pre_src": "「クルト……いるんでしょ……？」",
            "post_src": "クルト……いるんでしょ……？",
            "pre_dst": "库尔特……你在家吗……？",
            "proofread_dst": "",
            "trans_by": "mock",
        },
        {
            "index": 2,
            "name": "創",
            "pre_src": "「ふぅ……」",
            "post_src": "ふぅ……",
            "pre_dst": "呼……",
            "proofread_dst": "",
            "trans_by": "mock",
        },
        {
            "index": 3,
            "name": "",
            "pre_src": "そうだね、散歩に行こう。",
            "post_src": "そうだね、散歩に行こう。",
            "pre_dst": "是啊，去散步吧。",
            "proofread_dst": "",
            "trans_by": "mock",
        },
    ]
    with open(
        os.path.join(proj, "transl_cache", "pass3_cache", "scene_01.txt.json"),
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(cache, f, ensure_ascii=False)
    return proj


async def noop_ensure_model_available(*args, **kwargs):
    return None


async def fake_semcheck_batch_translate(
    self,
    filename: str,
    cache_file_path: str,
    trans_list: list,
    num_pre_request: int,
    retry_failed: bool = False,
    gpt_dic=None,
    proofread: bool = False,
    retran_key: str = "",
    translist_hit=None,
    translist_unhit=None,
):
    """模拟语义检测判定：先清旧标记（幂等），再命中 index=2 置 suspected_error。"""
    for t in trans_list:
        t.suspected_error = ""
    for t in trans_list:
        if t.index == 2:
            t.suspected_error = "译文串行"
    return trans_list


class ForSemCheckDialogueFlowTests(unittest.IsolatedAsyncioTestCase):
    """真实 _semcheck_single_file 流程：对话符号恢复后再检测，不误报「本有引号」。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = _mkdtemp_writable("semcheck_flow_")
        cls._proj = _build_mini_project(cls._tmp)
        cls._cache_path = os.path.join(
            cls._proj, "transl_cache", "pass3_cache", "scene_01.txt.json"
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmp, ignore_errors=True)

    async def test_dialogue_symbols_restored_before_problem_detection(self) -> None:
        cfg = CProjectConfig(self._proj, "config.inc.yaml")
        cfg.non_interactive = True  # 走 server 风格轻量日志
        cfg.select_translator = "ForSemCheck"

        with patch(
            "GalTransl.Frontend.LLMTranslate.ensure_model_available_if_needed",
            new=noop_ensure_model_available,
        ), patch(
            "GalTransl.Backend.ForSemCheck.ForSemCheck.batch_translate",
            new=fake_semcheck_batch_translate,
        ):
            # run_galtransl 内部会调用 doLLMTranslate(cfg)，命中 ForSemCheck 分支，
            # 真实执行 preprocess → 缓存读取 → batch_translate → postprocess → find_problems → 写回
            await run_galtransl(cfg, "ForSemCheck", None)

        with open(self._cache_path, encoding="utf-8") as f:
            saved = json.load(f)
        by_index = {e["index"]: e for e in saved}

        # 对话条目（index=1）：符号已恢复 → 不得误报「本有引号」
        self.assertNotIn("本有引号", by_index[1].get("problem", ""))
        self.assertEqual(by_index[1].get("problem", ""), "")
        # post_dst_preview 恢复对话符号（与主翻译路径/重检路径口径一致）
        self.assertEqual(
            by_index[1].get("post_dst_preview"), "「库尔特……你在家吗……？」"
        )
        # 语义检测命中的条目（index=2）：suspected_error 正常认领为「疑似错误」
        self.assertEqual(by_index[2].get("suspected_error"), "译文串行")
        self.assertIn("疑似错误", by_index[2].get("problem", ""))


if __name__ == "__main__":
    unittest.main()
