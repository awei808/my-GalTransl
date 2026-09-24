"""独立后处理后端的表驱动执行器（0.5.2）。

原先「单独执行某个后处理后端」依赖 LLMTranslate.doLLMTranslate 里逐段硬编码的
eng_type 短路分支，ForFixRound / ForToneCheck 漏加分支后落入主翻译流程，最终触发
postprocess_results 的 gpt.afterTranslation 循环，连带执行全部后处理后端。

本模块把同构分支收敛为一张引擎表（STANDALONE_BACKENDS）+ 一个执行器，新增后端
只需在表里加一行。执行语义与既有独立分支保持一致：
  载入字典 → 逐文件（worker 池）重建句子 → 命中缓存 → 调用后端 → 写回缓存快照。
不写 gt_output（由「构建输出」单独触发），不触发 afterTranslation 后处理链。

注意：ensure_model_available / init_gptapi 由调用方注入。测试用
mock.patch("GalTransl.Frontend.LLMTranslate.<name>") 替换它们，而 patch 只作用于
调用方查找该名字的命名空间，在本模块内直接 import 会被静默打空。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from os import sep as os_sep
from os.path import exists as isPathExists, join as joinpath
from typing import Any, Awaitable, Callable, Dict

from GalTransl import LOGGER
from GalTransl.Cache import get_transCache_from_json, save_transCache_to_json
from GalTransl.ConfigHelper import CProjectConfig, initDictList
from GalTransl.Dictionary import CGptDict, CNormalDic
from GalTransl.Frontend.llm_prepost import (
    _resolve_file_h_ranges,
    postprocess_trans_list,
    preprocess_trans_list,
)
from GalTransl.Frontend.llm_runtime import (
    _check_stop_requested,
    _pass3_cache_dir,
    _runtime_project_dir,
    _update_runtime,
)
from GalTransl.Problem import find_problems
from GalTransl.server_runtime import WORKER_ID_CTX


@dataclass(frozen=True)
class StandaloneBackendSpec:
    """单个可独立执行的后处理后端规格。

    Args:
        log_tag: 日志前缀，如 "[改进轮]"。
        stage_tag: 运行态阶段名（前端进度显示）。
        sort_dict: 是否在载入后对 pre/post/gpt 字典排序。
        finalize_problems: 是否在写盘前补 postprocess_trans_list + find_problems
            （标记类后端需要，使 tone_issue/suspected_error 被认领为 problem）。
        needs_fix_params: 是否为统一修复后端，需先注入问题类型参数。
    """

    log_tag: str
    stage_tag: str
    sort_dict: bool = True
    finalize_problems: bool = False
    needs_fix_params: bool = False


# 可独立执行的后处理后端（eng_type -> 规格）；新增后端在此加一行即可
STANDALONE_BACKENDS: Dict[str, StandaloneBackendSpec] = {
    "ForBRStation": StandaloneBackendSpec("[换行修复]", "换行位置异常修复"),
    "ForJPResidue": StandaloneBackendSpec("[残留日文修复]", "残留日文修复"),
    "ForBanWordFix": StandaloneBackendSpec("[禁用词修复]", "禁用词修复"),
    "ForImproveTranslation": StandaloneBackendSpec("[改进轮]", "译文质量改进"),
    "ForSemCheck": StandaloneBackendSpec(
        "[语义检测]", "语义检测", finalize_problems=True
    ),
    "ForSemCheckAgain": StandaloneBackendSpec(
        "[语义复核]", "语义复核", finalize_problems=True
    ),
    "ForToneCheck": StandaloneBackendSpec(
        "[色彩检查]", "词语色彩一致性检查", finalize_problems=True
    ),
    "ForFixRound": StandaloneBackendSpec(
        "[问题修复]", "统一问题修复", needs_fix_params=True
    ),
}


def is_standalone_backend(eng_type: str) -> bool:
    """判断引擎是否属于可独立执行的后处理后端。"""
    return eng_type in STANDALONE_BACKENDS


def _resolve_fix_round_types(projectConfig: CProjectConfig) -> list:
    """解析统一修复后端的问题类型：gpt.fixRoundTypes 优先，空则回退 problemList 全部。

    与 afterTranslation 的 fix 条目不同，独立执行没有条目级 types 可读，故用
    专用键 gpt.fixRoundTypes；为空时回退 problemAnalyze.problemList（与
    ForProblemFixRound._ensure_problem_types_configured 的惰性回退口径一致）。
    """
    from GalTransl.Backend.ForFixRound import ForProblemFixRound

    types = ForProblemFixRound._coerce_problem_type_list(
        projectConfig.getKey("gpt.fixRoundTypes", None)
    )
    if types:
        return types
    try:
        all_types = projectConfig.getProblemAnalyzeConfig("problemList")
    except (TypeError, KeyError) as e:
        # problemList 写成空值（YAML `problemList:` 无条目）时解析为 None，
        # getProblemAnalyzeConfig 会抛 TypeError；此处兜住并视为未配置
        LOGGER.warning(f"[问题修复] 读取 problemAnalyze.problemList 失败：{e}")
        all_types = []
    if all_types:
        LOGGER.warning(
            f"[问题修复] 未指定 gpt.fixRoundTypes，回退 problemAnalyze.problemList "
            f"全部 {len(all_types)} 类：{[t.name for t in all_types]}"
        )
    return all_types


def _load_dicts(projectConfig: CProjectConfig, spec: StandaloneBackendSpec) -> None:
    """按独立分支口径载入 pre/post/gpt 字典。

    主流程的字典初始化位于翻译阶段，独立分支需自行加载，否则
    projectConfig.pre_dic 为 None 会导致 preprocess_trans_list 崩溃。
    """
    cfg_section = projectConfig.getDictCfgSection()
    default_dic_dir = cfg_section.get("defaultDictFolder", "")
    project_dir = projectConfig.getProjectDir()
    projectConfig.pre_dic = CNormalDic(
        initDictList(cfg_section.get("preDict", []), default_dic_dir, project_dir)
    )
    projectConfig.post_dic = CNormalDic(
        initDictList(cfg_section.get("postDict", []), default_dic_dir, project_dir)
    )
    projectConfig.gpt_dic = CGptDict(
        initDictList(cfg_section.get("gpt.dict", []), default_dic_dir, project_dir)
    )
    if spec.sort_dict and cfg_section.get("sortDict", True):
        projectConfig.pre_dic.sort_dic()
        projectConfig.post_dic.sort_dic()
        projectConfig.gpt_dic.sort_dic()


async def _process_single_file(
    file_path: str,
    json_list: list,
    projectConfig: CProjectConfig,
    spec: StandaloneBackendSpec,
    eng_type: str,
    gptapi: Any,
    num_better: int,
    input_dir: str,
    cache_dir: str,
    project_dir: str,
) -> None:
    """处理单个文件：重建句子 → 命中缓存 → 调用后端 → 写回缓存快照。"""
    _check_stop_requested(projectConfig)
    file_name = (
        file_path.replace(input_dir, "").lstrip(os_sep).replace(os_sep, "-}")
    )
    cache_file_path = joinpath(cache_dir, file_name)
    if not isPathExists(cache_file_path):
        LOGGER.warning(f"{spec.log_tag} {file_name} 无缓存译文，跳过")
        return
    # 从输入 json 重建 CSentense：复用 load_transList（与翻译轮 splitter 一致），
    # 自动处理 name/names/message/index 并链接 prev/next，保证缓存命中匹配
    from GalTransl.Loader import load_transList

    trans_list, _ = load_transList(json_list)
    preprocess_trans_list(
        trans_list, projectConfig, projectConfig.pre_dic, projectConfig.tPlugins
    )
    await get_transCache_from_json(
        trans_list,
        cache_file_path,
        retry_failed=False,
        proofread=False,
        retran_key="",
        eng_type=eng_type,
    )
    _update_runtime(projectConfig, current_file=file_name)
    await gptapi.batch_translate(
        file_name,
        cache_file_path,
        trans_list,
        num_better,
        gpt_dic=projectConfig.gpt_dic,
    )
    if spec.finalize_problems:
        # 与主翻译路径一致：先恢复对话符号/译后字典，再跑问题检测，
        # 避免 post_dst 缺「」导致标点错漏误报「本有引号」
        postprocess_trans_list(
            trans_list, projectConfig, projectConfig.post_dic, projectConfig.tPlugins
        )
        h_ranges = _resolve_file_h_ranges(project_dir, cache_file_path, projectConfig)
        find_problems(
            trans_list, projectConfig, projectConfig.gpt_dic, h_ranges=h_ranges
        )
    # 仅当存在有效译文/备选译文时才保存，避免缓存未命中时把已有缓存覆盖成空数组
    has_content = any(
        t.pre_dst != "" or t.alt_dst != "" or t.proofread_zh != "" for t in trans_list
    )
    if has_content:
        await save_transCache_to_json(
            trans_list,
            cache_file_path,
            post_save=True,
            project_dir=_runtime_project_dir(projectConfig),
        )
    else:
        LOGGER.warning(
            f"{spec.log_tag} {file_name} 无有效译文，跳过缓存保存（保留已有缓存）"
        )


async def _run_file_worker_pool(
    file_json_lists: Dict[str, list],
    worker_count: int,
    projectConfig: CProjectConfig,
    spec: StandaloneBackendSpec,
    eng_type: str,
    gptapi: Any,
    num_better: int,
    input_dir: str,
    cache_dir: str,
    project_dir: str,
) -> None:
    """文件级 worker 池：一个 worker 一个文件、文件内串行，保留单文件多轮对话单链。"""
    file_queue: asyncio.Queue = asyncio.Queue()
    for file_path, json_list in file_json_lists.items():
        file_queue.put_nowait((file_path, json_list))
    for _ in range(worker_count):
        file_queue.put_nowait(None)

    async def _worker_loop(worker_index: int) -> None:
        # 绑定 worker 身份，提示词预览按此分板块（与翻译轮 worker 池一致）
        worker_token = WORKER_ID_CTX.set(str(worker_index))
        LOGGER.debug(
            f"{spec.log_tag} worker_loop[{worker_index}] 启动, "
            f"WORKER_ID_CTX={WORKER_ID_CTX.get()!r}"
        )
        try:
            while True:
                _check_stop_requested(projectConfig)
                item = await file_queue.get()
                if item is None:
                    return
                file_path, json_list = item
                await _process_single_file(
                    file_path,
                    json_list,
                    projectConfig,
                    spec,
                    eng_type,
                    gptapi,
                    num_better,
                    input_dir,
                    cache_dir,
                    project_dir,
                )
        finally:
            WORKER_ID_CTX.reset(worker_token)

    tasks = [asyncio.create_task(_worker_loop(i)) for i in range(worker_count)]
    try:
        await asyncio.gather(*tasks)
    except Exception:
        # 任一 worker 抛出未捕获异常（缓存读取/写盘失败等）：取消其余 worker，
        # 避免孤儿任务继续处理导致状态不一致
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def run_standalone_backend(
    projectConfig: CProjectConfig,
    eng_type: str,
    file_json_lists: Dict[str, list],
    ensure_model_available: Callable[..., Awaitable[Any]],
    init_gptapi: Callable[..., Awaitable[Any]],
) -> bool:
    """单独执行一个后处理后端（不触发 afterTranslation 后处理链）。

    Args:
        projectConfig: 项目配置。
        eng_type: 引擎标识，须存在于 STANDALONE_BACKENDS。
        file_json_lists: 输入文件路径 -> 已解析的 json_list。
        ensure_model_available: 模型可用性检查协程（由调用方注入以便测试替换）。
        init_gptapi: 后端实例构造协程（由调用方注入以便测试替换）。

    Returns:
        True 表示已处理（含无文件/无类型等跳过情形）。
    """
    spec = STANDALONE_BACKENDS.get(eng_type)
    if spec is None:
        raise ValueError(f"{eng_type} 不是可独立执行的后处理后端")

    _check_stop_requested(projectConfig)
    # 统一修复后端的问题类型先解析：为空则直接跳过，避免白建一个 API 客户端再丢弃
    fix_types = None
    if spec.needs_fix_params:
        fix_types = _resolve_fix_round_types(projectConfig)
        if not fix_types:
            LOGGER.warning(
                f"{spec.log_tag} 未配置 gpt.fixRoundTypes 且 problemAnalyze.problemList "
                f"为空，跳过该后端"
            )
            return True

    await ensure_model_available(projectConfig)
    _load_dicts(projectConfig, spec)
    gptapi = await init_gptapi(projectConfig)
    try:
        if fix_types:
            # injectProblem 固定 True，与 afterTranslation 的 fix 条目默认值一致
            # （gpt.enableProblemInject 是「改进轮」专用键，不在此复用，避免双语义）
            include_src = gptapi.set_fix_params(fix_types, inject_problem=True)
            LOGGER.info(
                f"{spec.log_tag} 组合修复：types={[t.name for t in fix_types]}，"
                f"模式={'译文+原文' if include_src else '仅译文'}（自动推导）"
            )

        input_dir = projectConfig.getInputPath()
        project_dir = projectConfig.getProjectDir()
        cache_dir = _pass3_cache_dir(projectConfig)
        total = len(file_json_lists)
        worker_count = max(1, projectConfig.get_workers_per_project())
        projectConfig.active_workers = worker_count
        LOGGER.info(
            f"{spec.log_tag} 开始为 {total} 个文件执行{spec.stage_tag}，"
            f"并发 {worker_count} worker"
        )
        _update_runtime(projectConfig, stage=spec.stage_tag)
        num_better = projectConfig.getKey("gpt.numPerRequestBetter")
        try:
            num_better = int(num_better) if num_better else 100
        except (TypeError, ValueError):
            num_better = 100

        await _run_file_worker_pool(
            file_json_lists,
            worker_count,
            projectConfig,
            spec,
            eng_type,
            gptapi,
            num_better,
            input_dir,
            cache_dir,
            project_dir,
        )
        LOGGER.info(f"{spec.log_tag} {spec.stage_tag}完成")
        _update_runtime(projectConfig, stage=f"{spec.stage_tag}完成")
    finally:
        if hasattr(gptapi, "shutdown"):
            await gptapi.shutdown()
    return True
