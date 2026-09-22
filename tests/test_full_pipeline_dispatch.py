"""端到端测试：ForGal-full-pipeline 分支的真实可达性。

背景（0.5.1 修复的缺陷）：`_run_full_pipeline` 在 0.4.10 拆分时迁至
`Frontend/llm_pipeline.py`，但 LLMTranslate 漏了 re-export，只在文件末尾用
PEP 562 模块级 `__getattr__` 兜属性访问。`doLLMTranslate` 是以**全局名**调用它的
（LLMTranslate.py:194），而 `__getattr__` 不参与函数体内全局名查找，于是每次
点「启动流程」必抛 `NameError: name '_run_full_pipeline' is not defined`。

本测试刻意**不 patch** `_run_full_pipeline`（patch 会把符号注入命名空间、
反而掩盖该缺陷），而是真调该分支：把 9 个阶段开关全部关闭，使真实
`_run_full_pipeline` 遍历阶段清单后立即返回，从而低成本覆盖 L194 实跑路径。

覆盖点：
1. `doLLMTranslate` 命中 ForGal-full-pipeline 分支且不抛 NameError；
2. 真实调到了 llm_pipeline 的实现（以真实实现执行、走完阶段遍历）。
"""

import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from unittest.mock import patch  # noqa: E402

from GalTransl.ConfigHelper import CProjectConfig  # noqa: E402
from GalTransl.Frontend import LLMTranslate  # noqa: E402

# 最小项目配置：9 个阶段全部关闭，使真实 _run_full_pipeline 只做遍历与跳过
MINI_CONFIG = """# 端到端测试用最小项目配置（全阶段关闭）
backendSpecific:
  OpenAI-Compatible:
    tokens:
      - token: sk-test
        endpoint: http://127.0.0.1:9999
        modelName: deepseek-chat

plugin:
  filePlugin: file_galtransl_json
  textPlugins:
    - text_common_normalfix

common:
  gpt.numPerRequestTranslate: 10
  workersPerProject: 1
  language: "ja2zh-cn"
  splitFile: "no"
  gpt.translation_guideline: "Basic.md"

internals:
  pipeline:
    enableValidate: false
    enableCompress: false
    enableGlobalPrompt: false
    enableGenDic: false
    enableFileMeta: false
    enablePlotRoute: false
    enableBatchMeta: false
    enableTranslate: false
    enableImprove: false

dictionary:
  defaultDictFolder: Dict
  preDict: []
  gpt.dict: []
  postDict: []

proxy:
  enableProxy: false
"""


def _build_mini_project(root: str) -> str:
    """在 root 下构造含 config.inc.yaml 与单个待译文件的临时项目。"""
    proj = os.path.join(root, "mini_proj")
    os.makedirs(os.path.join(proj, "gt_input"), exist_ok=True)
    with open(os.path.join(proj, "config.inc.yaml"), "w", encoding="utf-8") as f:
        f.write(MINI_CONFIG)
    lines = [
        {"name": "爱丽丝", "message": "今日はいい天気だね。"},
        {"name": "ボブ", "message": "そうだね、散歩に行こう。"},
    ]
    with open(
        os.path.join(proj, "gt_input", "scene_01.txt.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(lines, f, ensure_ascii=False)
    return proj


async def noop_ensure_model_available(*args, **kwargs):
    """替代模型可用性网络检查（本用例只验证分派，不触网）。"""
    return None


class FullPipelineDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_forgal_full_pipeline_branch_runs_without_name_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="full_pipeline_") as tmp:
            proj = _build_mini_project(tmp)
            cfg = CProjectConfig(proj, "config.inc.yaml")
            cfg.non_interactive = True
            cfg.select_translator = "ForGal-full-pipeline"

            with patch(
                "GalTransl.Frontend.LLMTranslate.ensure_model_available_if_needed",
                new=noop_ensure_model_available,
            ):
                result = await LLMTranslate.doLLMTranslate(cfg)

            self.assertTrue(result, "doLLMTranslate 应返回 True")

    def test_full_pipeline_symbol_is_real_module_binding(self) -> None:
        # 顶层必须真实绑定（不能只靠模块级 __getattr__ 兜属性访问）
        self.assertIn("_run_full_pipeline", vars(LLMTranslate))
        from GalTransl.Frontend import llm_pipeline
        self.assertIs(
            vars(LLMTranslate)["_run_full_pipeline"], llm_pipeline._run_full_pipeline
        )


if __name__ == "__main__":
    unittest.main()
