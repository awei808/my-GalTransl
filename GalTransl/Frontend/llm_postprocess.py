"""译后处理链路（0.4.10 从 LLMTranslate.py 抽出）。

构成完整的单文件译后流程：
  doLLMTranslSingleChunk（单 chunk 翻译收尾）
    → postprocess_results（问题检测 / 缓存快照 / 输出合并）
      → _resolve_after_translation_order（afterTranslation 顺序解析）
      → _run_after_trans_single_file（逐引擎后处理：改进/换行/日文/禁用词/语义/色彩/复核/修复轮）

注意：_resolve_after_translation_order 被测试 mock.patch，其调用方
postprocess_results 与本函数同模块，故 patch 目标为 GalTransl.Frontend.llm_postprocess
（打 LLMTranslate 会静默打空 —— patch 只作用于调用方查找名字的命名空间）。
"""
from __future__ import annotations

import asyncio
import os
from os import makedirs, sep as os_sep
from os.path import dirname, exists as isPathExists, join as joinpath
from time import time
from typing import Any, Dict, List, Optional, Tuple, Union

from GalTransl import LOGGER
from GalTransl.i18n import get_text, GT_LANG
from GalTransl.Cache import get_transCache_from_json, save_transCache_to_json
from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.CSerialize import save_json, update_json_with_transList
from GalTransl.CSentense import CTransList
from GalTransl.CSplitter import DictionaryCombiner, SplitChunkMetadata
from GalTransl.Problem import find_problems
from GalTransl.TerminalOutput import should_print_translation_logs, terminal_progress
from GalTransl.server_runtime import WORKER_ID_CTX
from GalTransl.Frontend.llm_runtime import (
    _check_stop_requested,
    _pass3_cache_dir,
    _runtime_project_dir,
    _stage_pool,
    _update_runtime,
    ensure_model_available_if_needed,
)
from GalTransl.Frontend.llm_prepost import (
    _resolve_file_h_ranges,
    postprocess_trans_list,
    preprocess_trans_list,
)
from GalTransl.Frontend.llm_standalone import is_standalone_backend


async def doLLMTranslSingleChunk(
    semaphore: asyncio.Semaphore,
    split_chunk: SplitChunkMetadata,
    projectConfig: CProjectConfig,
    gptapi: Any,  # 添加 gptapi 参数
) -> None:
    """处理单个切片(chunk)的翻译流程。

    顺序：
    1. acquire 信号量 → 进入并发窗口
    2. 前处理（插件 before_src → 字典替换 → after_src）
    3. 读缓存判定命中/未命中（含 append 日志合并）
    4. 未命中部分调 gptapi.batch_translate；若启用则做校对
    5. 后处理（恢复符号、post 字典、插件 after_dst）
    6. 如果该文件所有 chunk 都完成，触发 postprocess_results 合并写出+快照缓存
    """

    async with semaphore:
        # 记录当前并发占用（configured - 剩余槽位），DEBUG 级避免刷屏
        LOGGER.debug(
            f"[并发] 获取翻译槽位 当前并发占用 "
            f"{getattr(projectConfig, 'runtime_workers_configured', 0) - getattr(semaphore, '_value', 0)}"
        )
        _check_stop_requested(projectConfig)
        st = time()
        proj_dir = projectConfig.getProjectDir()
        input_dir = projectConfig.getInputPath()
        output_dir = projectConfig.getOutputPath()
        cache_dir = _pass3_cache_dir(projectConfig)
        pre_dic = projectConfig.pre_dic
        post_dic = projectConfig.post_dic
        gpt_dic = projectConfig.gpt_dic
        file_path = split_chunk.file_path
        file_name = (
            file_path.replace(input_dir, "").lstrip(os_sep).replace(os_sep, "-}")
        )  # 多级文件夹
        tPlugins = projectConfig.tPlugins
        eng_type = projectConfig.select_translator

        # 待废弃：以下 chunk 索引来自 splitter 文件级分块，未来由 BatchMetadata 语义段取代
        total_splits = split_chunk.total_chunks
        file_index = split_chunk.chunk_index
        input_file_path = file_path
        output_file_path = input_file_path.replace(input_dir, output_dir)

        cache_file_path = joinpath(
            cache_dir,
            file_name + (f"_{file_index}" if total_splits > 1 else ""),
        )

        part_info = f" (part {file_index+1}/{total_splits})" if total_splits > 1 else ""
        _update_runtime(
            projectConfig,
            current_file=file_name,
            # 当前文件被切分的 chunk/批次序号，供前端 toast 显示“第 N/M 批次”
            current_batch=file_index + 1,
            batch_total=total_splits,
        )
        LOGGER.info(f">>> 开始翻译 (project_dir){split_chunk.file_path.replace(proj_dir,'')}")
        LOGGER.debug(f"文件 {file_name} 分块 {file_index+1}/{total_splits}:")
        LOGGER.debug(f"  开始索引: {split_chunk.start_index}")
        LOGGER.debug(f"  结束索引: {split_chunk.end_index}")
        LOGGER.debug(f"  非交叉大小: {split_chunk.chunk_non_cross_size}")
        LOGGER.debug(f"  实际大小: {split_chunk.chunk_size}")
        LOGGER.debug(f"  交叉数量: {split_chunk.cross_num}")

        # 翻译前处理
        preprocess_trans_list(split_chunk.trans_list, projectConfig, pre_dic, tPlugins)

        translist_hit, translist_unhit = await get_transCache_from_json(
            split_chunk.trans_list,
            cache_file_path,
            retry_failed=projectConfig.getKey("retranslFail"),
            proofread=False,
            retran_key=projectConfig.getKey("retranslKey"),
            eng_type=eng_type,
        )

        if len(translist_hit) > 0:
            projectConfig.bar(len(translist_hit), skipped=True) # 更新进度条

        if len(translist_unhit) > 0:
            _check_stop_requested(projectConfig)
            await ensure_model_available_if_needed(projectConfig)
            # 执行翻译
            await gptapi.batch_translate(
                file_name + (f"_{file_index}" if total_splits > 1 else ""),
                cache_file_path,
                split_chunk.trans_list,
                projectConfig.getKey("gpt.numPerRequestTranslate"),
                retry_failed=projectConfig.getKey("retranslFail"),
                gpt_dic=gpt_dic,
                retran_key=projectConfig.getKey("retranslKey"),
                translist_hit=translist_hit,
                translist_unhit=translist_unhit,
            )

            # 弃用的死代码：旧版 GPT4 自动校对；gpt.enableProofRead 已从默认配置移除且现无 gpt4 引擎，分支永不执行
            if projectConfig.getKey("gpt.enableProofRead"):
                _check_stop_requested(projectConfig)
                if "gpt4" in eng_type:
                    await gptapi.batch_translate(
                        file_name,
                        cache_file_path,
                        split_chunk.trans_list,
                        projectConfig.getKey("gpt.numPerRequestProofRead"),
                        retry_failed=projectConfig.getKey("retranslFail"),
                        gpt_dic=gpt_dic,
                        proofread=True,
                        retran_key=projectConfig.getKey("retranslKey"),
                    )
                else:
                    LOGGER.warning("当前引擎不支持校对，跳过校对步骤")
            gptapi.clean_up()

        # 翻译后处理
        _check_stop_requested(projectConfig)
        postprocess_trans_list(split_chunk.trans_list, projectConfig, post_dic, tPlugins)

        et = time()
        LOGGER.info(
            get_text(
                "file_translation_completed", GT_LANG, file_name, part_info, et - st
            )
        )

        # 登记本 chunk 已完成；只有当"同一文件的全部 chunk"都完成时才做整文件后处理
        split_chunk.update_file_finished_chunk()
        if split_chunk.is_file_finished():
            LOGGER.debug(get_text("file_chunks_completed", GT_LANG, file_name))
            await postprocess_results(
                split_chunk.get_file_finished_chunks(), projectConfig, gptapi
            )

        _update_runtime(projectConfig, current_file=file_name)
async def postprocess_results(
    resultChunks: List[SplitChunkMetadata],
    projectConfig: CProjectConfig,
    gptapi: Any = None,
) -> None:
    """单个文件翻译完成后的收尾工作。

    对每个 chunk 逐一：find_problems 标注问题 → save_transCache_to_json(post_save=True)
    写完整 jsonl 快照（这也是唯一一次把 append 日志合并入主快照的时机）。
    随后合并所有 chunk 的结果，套用 name 替换表并经文件插件写出最终译文。

    若 gpt.afterTranslation 配置为有序数组（或旧字符串组合），会在保存
    快照前先按数组顺序执行对应后处理后端（独立实例、逐文件），把模型给出的
    备选译文写入各句 alt_dst，随快照一并落盘。空列表则跳过。
    """

    proj_dir = projectConfig.getProjectDir()
    input_dir = projectConfig.getInputPath()
    output_dir = projectConfig.getOutputPath()
    cache_dir = _pass3_cache_dir(projectConfig)
    eng_type = projectConfig.select_translator
    gpt_dic = projectConfig.gpt_dic
    name_replaceDict = projectConfig.name_replaceDict
    from GalTransl.Backend.RebuildTranslate import REBUILD_ENGINES

    # 后处理阶段（替代原"向多轮对话追加改进轮"）：整文件翻译+校对完成后，
    # 按 gpt.afterTranslation 配置逐文件调度修复/改进后端（空列表跳过）。
    # 放在保存循环之前，使备选译文随 post_save 快照一并落盘。
    # 重建引擎不执行阶段7（后处理会调用模型，重建只基于现有缓存）。
    _after_order = _resolve_after_translation_order(projectConfig)
    if is_standalone_backend(eng_type):
        # 兜底防线：当前任务本身是后处理后端（手动单独执行），不得再按
        # afterTranslation 连带执行其他后端（防未来新增引擎漏配独立分支）
        if _after_order:
            LOGGER.debug(
                f"[后处理] 当前引擎 {eng_type} 为独立后处理后端，"
                f"跳过 afterTranslation 连带执行：{'+'.join(_after_order)}"
            )
        _after_order = []
    if _after_order and eng_type not in REBUILD_ENGINES:
        _improve_enabled = projectConfig.getKey("internals.pipeline.enableImprove", True)
        if not _improve_enabled:
            LOGGER.debug(
                f"[后处理] 阶段 7 已禁用（enableImprove=false），"
                f"跳过 {len(_after_order)} 个后处理后端：{'+'.join(_after_order)}"
            )
        else:
            await ensure_model_available_if_needed(projectConfig, stage="afterTrans")
            merged_trans = []
            for _chunk in resultChunks:
                merged_trans.extend(_chunk.trans_list)
            _orig_name = (
                resultChunks[0].file_path.replace(input_dir, "")
                .lstrip(os_sep)
                .replace(os_sep, "-}")
            )
            _num_better = projectConfig.getKey("gpt.numPerRequestBetter")
            try:
                _num_better = int(_num_better) if _num_better else 100
            except (TypeError, ValueError):
                _num_better = 100
            # 按配置数组顺序依次执行（数组顺序即执行顺序）
            for _m in _after_order:
                _display = _m if isinstance(_m, str) else "fix"
                _update_runtime(projectConfig, stage=f"AI初步处理-{_display}")
                LOGGER.info(
                    f"[后处理] 开始：{_display}，文件={_orig_name}"
                )
                try:
                    await _run_after_trans_single_file(
                        _m,
                        _orig_name,
                        resultChunks[0].file_path,
                        merged_trans,
                        projectConfig,
                        _num_better,
                    )
                    LOGGER.info(f"[后处理] 完成：{_display}，文件={_orig_name}")
                except Exception as e:
                    from GalTransl.Service import JobCancelledError

                    if isinstance(e, JobCancelledError):
                        raise
                    LOGGER.warning(
                        f"[后处理/{_display}] {resultChunks[0].file_path} 执行失败，已跳过：{e}"
                    )
                    # 上报到控制台"最近错误"
                    try:
                        from GalTransl.server import record_runtime_error

                        record_runtime_error(
                            _runtime_project_dir(projectConfig),
                            kind="api",
                            message=f"[后处理/{_m}] {resultChunks[0].file_path}: {e}",
                        )
                    except Exception as _re:
                        LOGGER.warning(f"[后处理] 错误上报失败：{_re}")

    # 对每个分块执行错误检查和缓存保存
    for i, chunk in enumerate(resultChunks):
        trans_list = chunk.trans_list
        file_path = chunk.file_path
        cache_file_path = joinpath(
            cache_dir,
            file_path.replace(input_dir, "").lstrip(os_sep).replace(os_sep, "-}")
            + (f"_{chunk.chunk_index}" if chunk.total_chunks > 1 else ""),
        )

        # rebuildr 是"只重建输出文件"模式，不应修改缓存；其余引擎正常刷新
        if eng_type == "rebuildr":
            continue

        # 刷新 problem 字段（仅翻译模式；GenDic/dump-name 等不刷新）。
        # 解析该文件 H 区间后传入 find_problems，使 H 场景长句阈值走 getHSentenceLengthThreshold，
        # 而非平均分句阈值；无 pass2_cache 时 h_ranges 为空列表，行为与旧版一致。
        # 注意：翻译阶段不传 h_check_words，H 场景用词不当检测仍由校对阶段重检补上。
        h_ranges = _resolve_file_h_ranges(proj_dir, cache_file_path, projectConfig)
        find_problems(trans_list, projectConfig, gpt_dic, h_ranges=h_ranges)
        # post_save=True → 写完整快照并删除对应 .append 日志（即合并 jsonl）
        await save_transCache_to_json(
            trans_list,
            cache_file_path,
            post_save=True,
            project_dir=_runtime_project_dir(projectConfig),
        )

    # 使用output_combiner合并结果，即使只有一个结果
    all_trans_list, all_json_list = DictionaryCombiner.combine(resultChunks)
    LOGGER.debug(f"合并后总行数: {len(all_trans_list)}")
    file_path = resultChunks[0].file_path
    output_file_path = file_path.replace(input_dir, output_dir)
    save_func = projectConfig.file_save_funcs.get(file_path, save_json)

    # 逐文件输出构建（由独立 build-output 端点触发，校对完成后手动执行）
    # 不再随流水线自动执行，避免 output/ 内容滞后于校对修改。
    if all_trans_list and all_json_list:
        final_result = update_json_with_transList(
            all_trans_list, all_json_list, name_replaceDict
        )
        makedirs(dirname(output_file_path), exist_ok=True)
        save_func(output_file_path, final_result)
        LOGGER.info(f"+++ 结果保存 (project_dir){output_file_path.replace(proj_dir,'')}")
def _resolve_after_translation_order(projectConfig: CProjectConfig) -> list:
    """解析流水线翻译后处理后端配置，返回有序后端条目列表（数组顺序即执行顺序）。

    条目可为字符串 key（improve/brfix/jpfix/banfix/semcheck/semcheckagain/tonecheck/tonecheckagain）或
    统一修复后端对象条目 {"fix": {"types": [...], "injectProblem": ...}}；
    输入模式由所选问题类型自动推导（含需对照原文的类型即用译文+原文，否则仅译文），
    配置中残留的 mode 字段直接忽略。同 key 条目去重保序（fix 条目仅保留第一个）。旧字符串格式
    （none / improve+brfix 组合）仍兼容读取。空列表表示不执行；缺省回退
    gpt.enableBetterTranslation（true→[improve]）以兼容旧项目配置。
    """
    allowed = {
        "improve", "brfix", "jpfix", "banfix",
        "semcheck", "semcheckagain", "tonecheck", "tonecheckagain", "fix",
    }
    raw = projectConfig.getKey("gpt.afterTranslation")

    def _normalize_entry(entry) -> Optional[object]:
        if isinstance(entry, str):
            p = entry.strip().lower()
            return p if p in allowed else None
        if isinstance(entry, dict):
            fix_cfg = entry.get("fix")
            return {"fix": fix_cfg} if isinstance(fix_cfg, dict) else None
        return None

    def _filter_parts(parts: list) -> list:
        seen = set()
        result: list = []
        for part in parts:
            entry = _normalize_entry(part)
            if entry is None:
                continue
            key = entry if isinstance(entry, str) else "fix"
            if key in seen:
                continue
            seen.add(key)
            result.append(entry)
        return result

    if isinstance(raw, list):
        return _filter_parts(raw)
    if isinstance(raw, str):
        mode = raw.strip().lower()
        if not mode or mode == "none":
            return []
        return _filter_parts(mode.split("+"))
    if not raw:
        # 旧项目兼容：enableBetterTranslation 已废弃，true 等价于 improve
        if projectConfig.getKey("gpt.enableBetterTranslation"):
            LOGGER.debug(
                "[后处理] gpt.afterTranslation 缺省，回退 enableBetterTranslation=true→improve"
            )
            return ["improve"]
        return []
    LOGGER.warning(f"[后处理] gpt.afterTranslation 非法值 '{raw}'，回退不执行")
    return []
async def _run_after_trans_single_file(
    mode: Optional[Union[str, dict]],
    orig_name: str,
    file_path: str,
    merged_trans: list,
    projectConfig: CProjectConfig,
    num_better: int,
) -> None:
    """对单个文件执行一种 AI 初步处理后端（improve 改进轮 / brfix 换行修复 / fix 统一修复 / tonecheck 色彩检查 / tonecheckagain 色彩复核等）。

    mode 可为字符串 key 或统一修复后端对象条目 {"fix": {"types": [...], "injectProblem": ...}}。
    直接实例化对应后端类（复用 projectConfig 已载入的 proxyPool/pre_dic/post_dic/
    gpt_dic/file_metadata，不重新 initDictList、不调 ensure_model_available、
    不碰 select_translator）；令牌池用 afterTrans 大阶段独立池（未配置回退主池）。
    用完 shutdown 释放连接。异常 caller 负责捕获：JobCancelledError 上抛，其余由 caller 记录。
    """
    # JobCancelledError 必须在函数内 import：GalTransl.Service 会反向 import 本模块
    # （Service→Runner→LLMTranslate），模块级 import 会触发循环依赖。
    from GalTransl.Service import JobCancelledError
    from GalTransl.Backend.ForImproveTranslation import ForImproveTranslation
    from GalTransl.Backend.ForBRStation import ForBRStation
    from GalTransl.Backend.ForJPResidue import ForJPResidue
    from GalTransl.Backend.ForBanWordFix import ForBanWordFix
    from GalTransl.Backend.ForSemCheck import ForSemCheck
    from GalTransl.Backend.ForSemCheckAgain import ForSemCheckAgain
    from GalTransl.Backend.ForToneCheck import ForToneCheck
    from GalTransl.Backend.ForToneCheckAgain import ForToneCheckAgain
    from GalTransl.Backend.ForFixRound import ForProblemFixRound

    _after_pool = _stage_pool(projectConfig, "afterTrans")
    _api = None
    try:
        if isinstance(mode, dict):
            # 统一修复后端参数化条目：{"fix": {"types": [...], "injectProblem": ...}}
            # 输入模式由所选问题类型自动推导（见 ForProblemFixRound.set_fix_params），
            # 配置中残留的 mode 字段直接忽略
            fix_cfg = (mode or {}).get("fix") or {}
            types = fix_cfg.get("types") or []
            inject_problem = fix_cfg.get("injectProblem", True)
            coerced_types = ForProblemFixRound._coerce_problem_type_list(types)
            if not coerced_types:
                # types 为空：直接告警并跳过，不实例化、不发任何请求
                LOGGER.warning(
                    f"[后处理/fix] 修复问题类型列表为空（types={types}），跳过该后端"
                )
                return
            _api = ForProblemFixRound(
                projectConfig,
                "ForFixRound",
                projectConfig.proxyPool,
                _after_pool,
            )
            include_src = _api.set_fix_params(coerced_types, inject_problem=inject_problem)
            LOGGER.info(
                f"[后处理/fix] 组合修复：types={[t.name for t in coerced_types]}，"
                f"模式={'译文+原文' if include_src else '仅译文'}（自动推导）"
            )
        elif mode == "improve":
            _api = ForImproveTranslation(
                projectConfig,
                "ForImproveTranslation",
                projectConfig.proxyPool,
                _after_pool,
            )
        elif mode == "brfix":
            # 旧字符串格式兼容入口：实例化薄包装子类（单类型语义与旧版一致），
            # 新配置建议迁移到 fix 对象条目（type:'fix', fix:{types, injectProblem}），
            # 输入模式由所选问题类型自动推导，配置中无需 mode 字段
            _api = ForBRStation(
                projectConfig,
                "ForBRStation",
                projectConfig.proxyPool,
                _after_pool,
            )
        elif mode == "jpfix":
            _api = ForJPResidue(
                projectConfig,
                "ForJPResidue",
                projectConfig.proxyPool,
                _after_pool,
            )
        elif mode == "banfix":
            _api = ForBanWordFix(
                projectConfig,
                "ForBanWordFix",
                projectConfig.proxyPool,
                _after_pool,
            )
        elif mode == "semcheck":
            _api = ForSemCheck(
                projectConfig,
                "ForSemCheck",
                projectConfig.proxyPool,
                _after_pool,
            )
        elif mode == "semcheckagain":
            _api = ForSemCheckAgain(
                projectConfig,
                "ForSemCheckAgain",
                projectConfig.proxyPool,
                _after_pool,
            )
        elif mode == "tonecheck":
            _api = ForToneCheck(
                projectConfig,
                "ForToneCheck",
                projectConfig.proxyPool,
                _after_pool,
            )
        elif mode == "tonecheckagain":
            _api = ForToneCheckAgain(
                projectConfig,
                "ForToneCheckAgain",
                projectConfig.proxyPool,
                _after_pool,
            )
        else:
            LOGGER.warning(f"[后处理] 未知模式 '{mode}'，跳过")
            return
        await _api.batch_translate(
            orig_name,
            orig_name + ".json",
            merged_trans,
            num_better,
            gpt_dic=projectConfig.gpt_dic,
        )
    finally:
        if _api is not None:
            # 独立实例用完即关，避免每文件泄漏一个 API 客户端
            await _api.shutdown()
