"""译前 / 译后处理与字典探测（0.4.10 从 LLMTranslate.py 抽出）。

职责：
- 译前预处理（preprocess_trans_list：字典加载、文本压缩）；
- 译后处理（postprocess_trans_list）；
- 仅生成字典的流程短路（_run_gendic_flow）；
- GPT 字典非空探测（_has_nonempty_gpt_dict）；
- 单文件 H 区间解析（_resolve_file_h_ranges，延迟导入 server 避免循环依赖）。
"""
from __future__ import annotations

import os
from os.path import abspath, exists as isPathExists, join as joinpath
from typing import Any, Dict, List, Optional, Union

from GalTransl import LOGGER
from GalTransl.i18n import get_text, GT_LANG
from GalTransl.CSentense import CTransList
from GalTransl.Dictionary import CGptDict, CNormalDic, _COMMENT_PREFIXES
from GalTransl.ConfigHelper import initDictList, CProjectConfig




def preprocess_trans_list(
    trans_list: CTransList,
    projectConfig: CProjectConfig,
    pre_dic: CNormalDic,
    tPlugins: Optional[list] = None,
) -> None:
    """翻译前处理：插件before_src → 对话分析 → 预处理字典替换源文 → 预处理字典替换说话人 → 插件after_src"""
    for tran in trans_list:
        if tPlugins:
            for plugin in tPlugins:
                try:
                    tran = plugin.plugin_object.before_src_processed(tran)
                except Exception as e:
                    LOGGER.error(
                        get_text("plugin_execution_failed", GT_LANG, plugin.name, e)
                    )

        if projectConfig.getFilePlugin() in [
            "file_galtransl_json",
            "file_mtbench_aio",
        ]:
            eng = getattr(projectConfig, "select_translator", "") or ""
            if eng.startswith("dump") or eng == "GenDic":
                pass  # 这些模式不需要分析对话
            else:
                tran.analyse_dialogue()

        tran.post_src = pre_dic.do_replace(tran.post_src, tran)

        if projectConfig.getDictCfgSection("usePreDictInName"):
            if isinstance(tran.speaker, str) and isinstance(tran._speaker, str):
                tran.speaker = pre_dic.do_replace(tran.speaker, tran)

        if tPlugins:
            for plugin in tPlugins:
                try:
                    tran = plugin.plugin_object.after_src_processed(tran)
                except Exception as e:
                    LOGGER.error(
                        get_text("plugin_execution_failed", GT_LANG, plugin.name, e)
                    )


def postprocess_trans_list(
    trans_list: CTransList,
    projectConfig: CProjectConfig,
    post_dic: CNormalDic,
    tPlugins: Optional[list] = None,
) -> None:
    """翻译后处理：插件before_dst → 恢复对话符号 → 后处理字典替换译文 → 插件after_dst"""
    for tran in trans_list:
        if tPlugins:
            for plugin in tPlugins:
                try:
                    tran = plugin.plugin_object.before_dst_processed(tran)
                except Exception as e:
                    LOGGER.error(f" 插件 {plugin.name} 执行失败: {e}", exc_info=True)

        tran.recover_dialogue_symbol()
        tran.post_dst = post_dic.do_replace(tran.post_dst, tran)

        if tPlugins:
            for plugin in tPlugins:
                try:
                    tran = plugin.plugin_object.after_dst_processed(tran)
                except Exception as e:
                    LOGGER.error(
                        get_text("plugin_execution_failed", GT_LANG, plugin.name, e)
                    )


async def _run_gendic_flow(
    projectConfig: CProjectConfig, all_jsons: list, gptapi: Any
) -> bool:
    """GenDic 字典生成流程：无论成功、失败还是取消，都确保关闭 gptapi 的 HTTP 客户端。"""
    try:
        LOGGER.info(f"[GenDic] 开始为 {len(all_jsons)} 条文本生成 GPT 字典")
        dic_ok = await gptapi.batch_translate(all_jsons)
        # 与完整流水线阶段 3 一致：仅硬失败（分词模型加载失败）时按 abortOnDicFailure 决定是否中止。
        if not dic_ok:
            abort = projectConfig.getKey("internals.pipeline.abortOnDicFailure", False)
            if abort:
                LOGGER.error("[GenDic] 术语表生成失败，按 abortOnDicFailure 配置中止流水线")
                raise RuntimeError(
                    "术语表生成失败（分词模型加载失败），已按 abortOnDicFailure=true 中止流水线"
                )
            LOGGER.warning("[GenDic] 术语表生成失败，abortOnDicFailure=false 继续")
        else:
            LOGGER.info("[GenDic] GPT 字典生成完成")
        return True
    finally:
        if hasattr(gptapi, "shutdown"):
            await gptapi.shutdown()


# ─────────────────────────────────────────────────────
# 完整翻译流水线编排器
# ─────────────────────────────────────────────────────

def _has_nonempty_gpt_dict(projectConfig: CProjectConfig) -> bool:
    """项目级 gpt 字典是否已有有效条目（任一文件含非空非注释行）。

    校验范围：gpt.dict 配置的全部项目字典 + 生成产物「项目GPT字典-生成.txt」。
    全部为空/缺失时返回 False，表示术语表为空、需要重新生成。
    """
    result_path = joinpath(projectConfig.getProjectDir(), "项目GPT字典-生成.txt")
    dict_cfg = projectConfig.getDictCfgSection()
    gpt_dic_list = dict_cfg.get("gpt.dict", []) if dict_cfg else []
    default_dic_dir = dict_cfg.get("defaultDictFolder", "") if dict_cfg else ""
    dic_paths: list[str] = []
    try:
        dic_paths = initDictList(gpt_dic_list, default_dic_dir, projectConfig.getProjectDir())
    except Exception:
        dic_paths = []
    candidates = [abspath(p) for p in dic_paths]
    if isPathExists(result_path) and abspath(result_path) not in candidates:
        candidates.append(abspath(result_path))
    for p in candidates:
        if not isPathExists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s and not s.lstrip().startswith(_COMMENT_PREFIXES):
                        return True
        except Exception:
            continue
    return False


def _resolve_file_h_ranges(
    proj_dir: str,
    cache_file_path: str,
    projectConfig: CProjectConfig,
) -> list:
    """解析当前缓存文件对应的 H 剧情区间，用于翻译阶段正确区分 H 场景。

    读取 pass2_cache/{输入名}.batch.json 的批次 H 标记并换算为缓存条目 index 口径。
    复用 server.py 的 _resolve_cache_h_ranges；延迟导入避免与 server 的顶层循环依赖。
    无 pass2_cache（独立运行翻译、未跑批注阶段）时返回空列表，调用方据此降级。
    """
    import os

    try:
        from GalTransl.server import _resolve_cache_h_ranges
    except Exception:
        LOGGER.debug("无法导入 _resolve_cache_h_ranges，跳过 H 区间解析")
        return []
    try:
        cache_rel = os.path.relpath(cache_file_path, projectConfig.getCachePath())
        info = _resolve_cache_h_ranges(proj_dir, cache_rel)
        h_ranges = [
            (r["lo"], r["hi"]) for r in info.get("h_ranges", [])
        ]
        if h_ranges:
            LOGGER.debug(f"解析到 H 区间 {h_ranges}（cache={cache_rel}）")
        return h_ranges
    except Exception as e:
        LOGGER.debug(f"解析 H 区间失败，跳过：{e}")
        return []
