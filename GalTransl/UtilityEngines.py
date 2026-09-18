"""工具引擎：不调用模型的实用流程（全部重检 / 批次划分预检 / 构建输出）。

经 ``-t`` 触发（如 ``python -m GalTransl -p 项目目录 -t recheck``），在 Runner
挂载任务日志后短路执行，不进入翻译流水线、不初始化代理与令牌池。对 server
复用函数的引用沿用 Service 的延迟导入模式，避免顶层循环导入。
"""
from __future__ import annotations

import os
from typing import Any

from GalTransl import LOGGER, INPUT_FOLDERNAME, PASS3_CACHE_DIR

# 工具引擎名集合：Runner 据此短路分派，server /api/translators 据此隐藏
UTILITY_ENGINES = ("recheck", "check-batch-size", "build-output")


def is_utility_engine(translator: str) -> bool:
    """判断是否为工具引擎（不进入翻译流水线、不调用模型）。"""
    return translator in UTILITY_ENGINES


def run_utility_engine(cfg: Any, translator: str) -> None:
    """执行工具引擎；失败直接抛异常，由 Runner / Service 的既有错误路径接管。"""
    if translator == "recheck":
        _run_recheck(cfg)
    elif translator == "check-batch-size":
        _run_check_batch_size(cfg)
    elif translator == "build-output":
        _run_build_output(cfg)
    else:
        raise ValueError(f"未知工具引擎: {translator}")


def _run_recheck(cfg: Any) -> None:
    """全部重检：对 pass3_cache 全部缓存重跑问题检测并写回 problem / post_dst_preview。"""
    from GalTransl.server import _load_rebuild_deps, recheck_pass3_cache_files

    proj_config, pre_dic, post_dic, gpt_dic, tPlugins, h_words, forbidden_words = _load_rebuild_deps(
        cfg.getProjectDir(), cfg.config_name
    )
    if proj_config is None:
        raise RuntimeError("项目配置或字典加载失败，无法执行重检")
    rechecked = recheck_pass3_cache_files(
        cfg.getCachePath(),
        proj_config,
        pre_dic,
        post_dic,
        gpt_dic,
        tPlugins,
        h_words,
        forbidden_words,
    )
    LOGGER.info(f"[recheck]全部重检完成：{rechecked} 个缓存文件已重检写回")
    _log_problem_summary(cfg.getCachePath())


def _log_problem_summary(cache_dir: str) -> None:
    """重检后按文件输出剩余问题计数，便于后续校对定位。"""
    import orjson

    pass3_dir = os.path.join(cache_dir, PASS3_CACHE_DIR)
    if not os.path.isdir(pass3_dir):
        return
    total = 0
    for name in sorted(os.listdir(pass3_dir)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(pass3_dir, name), "rb") as f:
                entries = orjson.loads(f.read())
        except Exception as exc:
            LOGGER.warning(f"[recheck]统计问题读取缓存失败：{name}: {exc}")
            continue
        count = sum(1 for e in entries if isinstance(e, dict) and e.get("problem"))
        if count:
            LOGGER.info(f"[recheck]{name}: 剩余 {count} 条问题")
        total += count
    LOGGER.info(f"[recheck]剩余问题条目合计：{total}")


def _run_check_batch_size(cfg: Any) -> None:
    """批次划分预检：计算最大可自然划分行数并列出行数超限的待翻译文件。"""
    from GalTransl.server import _check_batch_size

    input_dir = os.path.join(cfg.getProjectDir(), INPUT_FOLDERNAME)
    if not os.path.isdir(input_dir):
        raise RuntimeError(f"待翻译目录不存在：{input_dir}")
    result = _check_batch_size(cfg.getProjectDir(), cfg.config_name)
    LOGGER.info(
        f"[check-batch-size]最大可自然划分行数：{result.get('max_natural_lines', 0)}"
        "（0.9 × internals.forbatchmeta.max_batch_size × max_batches）"
    )
    oversize = result.get("oversize_files", [])
    if not oversize:
        LOGGER.info("[check-batch-size]所有待翻译文件均未超限，可正常划分批次")
        return
    LOGGER.warning(f"[check-batch-size]{len(oversize)} 个文件超过最大可自然划分行数：")
    for item in oversize:
        LOGGER.warning(f"[check-batch-size]  - {item.get('filename', '')}: {item.get('lines', 0)} 行")
    LOGGER.warning(
        "[check-batch-size]预检适用于 ForGal-full-pipeline / ForBatchMetaData；"
        "可调大 internals.forbatchmeta.max_batch_size / max_batches，或拆分超限原文件"
    )


def _run_build_output(cfg: Any) -> None:
    """构建输出：构建前校验仅提示不阻断，随后从缓存重建 gt_output 结果文件。"""
    from GalTransl.server import _build_project_output, _validate_build

    validation = _validate_build(cfg.getProjectDir())
    if not validation.get("ok", False):
        for name in validation.get("missing_files", []):
            LOGGER.warning(f"[build-output]校验：待翻译文件没有对应缓存 {name}")
        for issue in validation.get("content_issues", []):
            LOGGER.warning(f"[build-output]校验：{issue.get('file', '')} {issue.get('issue', '')}")
        LOGGER.warning("[build-output]构建前校验未完全通过（仅提示，不阻断构建）")
    result = _build_project_output(cfg.getProjectDir())
    if not result.get("success", False):
        raise RuntimeError(str(result.get("error", "构建输出失败")))
    for error in result.get("errors", []):
        LOGGER.error(f"[build-output]{error}")
    built_files = result.get("built_files", [])
    total_built = result.get("total_built", 0)
    if total_built == 0 and result.get("errors"):
        # CLI 语义：一个都没构建出来且有错误时按任务失败处理，让退出码可感知
        raise RuntimeError(f"构建输出失败：{len(result['errors'])} 个缓存文件未能构建")
    LOGGER.info(f"[build-output]构建输出完成：{total_built} 个文件 -> {built_files}")
