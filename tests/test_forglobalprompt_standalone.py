"""端到端测试：ForGlobalPrompt 作为独立翻译器被选中时应能正确运行。

验证点：
1. 选中 ForGlobalPrompt 不再崩溃（旧 bug：TypeError unexpected keyword argument 'retry_failed'）。
2. 走专用分支后正确产出 transl_cache/pass0_cache/GlobalPrompt.json 并 return True。
3. 产出的 GlobalPrompt.json 通过 validate_global_prompt 必填校验。

通过 run_galtransl 入口复用真实的插件/splitter/pool 初始化与 doLLMTranslate 分派，
仅 mock 两处网络依赖：
  - ensure_model_available_if_needed（模型可用性网络检查）
  - ForGlobalPrompt.ask_chatbot（LLM 实际调用，返回一段合法的全局分析 JSON）
"""

import asyncio
import json
import os
import sys

REPO_ROOT = r"D:\解包或汉化用\my-galtransl\my-GalTransl"
TEST2 = r"D:\解包或汉化用\xp3专用汉化文件夹\gal翻译\test2"

sys.path.insert(0, REPO_ROOT)

from unittest.mock import patch  # noqa: E402

from GalTransl.ConfigHelper import CProjectConfig  # noqa: E402
from GalTransl.Runner import run_galtransl  # noqa: E402
from GalTransl.DataValidator import validate_global_prompt  # noqa: E402

VALID_GLOBAL_PROMPT = json.dumps(
    {
        "游戏名称": "测试游戏",
        "剧情概述": "这是一段用于端到端测试的剧情概述。",
        "角色列表": [{"名称": "爱丽丝", "描述": "本作女主角"}],
        "世界观设定": "一个架空的奇幻世界",
        "行文风格": "口语化、轻小说风格",
        "题材标签": ["RPG", "奇幻"],
    },
    ensure_ascii=False,
)


async def fake_ask_chatbot(self, *args, **kwargs):
    # 与真实 ask_chatbot 一致的返回形态：(response_text, token_usage)
    return (VALID_GLOBAL_PROMPT, None)


async def noop_ensure_model_available(*args, **kwargs):
    return None


async def main() -> None:
    gp_path = os.path.join(TEST2, "transl_cache", "pass0_cache", "GlobalPrompt.json")
    # 先清掉旧产物，确保断言反映的是本次运行真实生成
    if os.path.exists(gp_path):
        os.remove(gp_path)

    cfg = CProjectConfig(TEST2, "config.yaml")
    cfg.non_interactive = True  # 走 server 风格轻量日志
    cfg.select_translator = "ForGlobalPrompt"

    with patch(
        "GalTransl.Frontend.LLMTranslate.ensure_model_available_if_needed",
        new=noop_ensure_model_available,
    ), patch(
        "GalTransl.Backend.ForGlobalPrompt.ForGlobalPrompt.ask_chatbot",
        new=fake_ask_chatbot,
    ):
        # run_galtransl 内部会调用 doLLMTranslate(cfg)，命中新增的 ForGlobalPrompt 分支
        await run_galtransl(cfg, "ForGlobalPrompt", None)

    assert os.path.exists(gp_path), f"[FAIL] 未生成 GlobalPrompt.json: {gp_path}"
    with open(gp_path, encoding="utf-8") as f:
        data = json.load(f)

    val = validate_global_prompt(data)
    assert val["valid"], f"[FAIL] GlobalPrompt 内容校验未通过: {val['errors']}"
    assert data.get("游戏名称") == "测试游戏"
    assert len(data.get("角色列表", [])) >= 1

    print("[PASS] ForGlobalPrompt 独立模式：doLLMTranslate 正确分派，")
    print(f"       生成并通过校验的 GlobalPrompt.json -> {gp_path}")
    print(f"       角色数: {len(data.get('角色列表', []))}")


if __name__ == "__main__":
    asyncio.run(main())
