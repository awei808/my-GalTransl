"""LLM 翻译前端。

该模块把项目配置转化为一轮完整的翻译流水线：
1. 读取输入文件 → 通过文件插件解析为 trans_list
2. 按 splitter 切成多个 chunk，按 name/size 排序
3. 载入字典 / name 替换表 / 初始化后端 gptapi
4. 启动 worker 协程池（带信号量 + 自适应并发调节）消费 chunk 队列
5. 每个 chunk：前处理 → 读缓存命中判定 → 调 gptapi.batch_translate →（可选）校对 → 后处理
6. 文件全部 chunk 完成后：find_problems + 写完整快照缓存(post_save) + 合并输出 + 通过文件插件保存

注：启动时不再做全局 jsonl 合并，仅在单文件完成时通过 `save_transCache_to_json(..., post_save=True)`
重写快照并清理 append 日志。
"""

from typing import List, Dict, Any, Optional, Union, Tuple
from os import makedirs, cpu_count, sep as os_sep,listdir
from os.path import join as joinpath, exists as isPathExists, dirname, basename as os_basename, abspath
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import time
import asyncio
from dataclasses import dataclass

from GalTransl import LOGGER, NEED_OpenAITokenPool, resolve_translator_alias
from GalTransl.i18n import get_text, GT_LANG
from GalTransl.Cache import get_transCache_from_json
from GalTransl.ConfigHelper import initDictList, CProjectConfig
from GalTransl.CSentense import CTransList
from GalTransl.Dictionary import CGptDict, CNormalDic, _COMMENT_PREFIXES
from GalTransl.Problem import find_problems
from GalTransl.Cache import save_transCache_to_json
from GalTransl.server_runtime import WORKER_ID_CTX, record_runtime_notice
from GalTransl.Name import load_name_table, dump_name_table_from_chunks
from GalTransl.CSerialize import update_json_with_transList, save_json
from GalTransl.Dictionary import CNormalDic, CGptDict
from GalTransl.ConfigHelper import CProjectConfig, initDictList
from GalTransl.Utils import get_file_list
from GalTransl.CSplitter import (
    SplitChunkMetadata,
    DictionaryCombiner,
)
from GalTransl.TerminalOutput import should_print_translation_logs, terminal_progress


# 0.4.10 拆分：以下符号的实现已移至子模块，此处 re-export 维持既有引用面
from GalTransl.Frontend.llm_runtime import (  # noqa: E402
    _runtime_project_dir,
    _update_runtime,
    _run_meta_worker_pool,
    _pass3_cache_dir,
    _stage_pool,
    ensure_model_available_if_needed,
    AdaptiveWorkerState,
    auto_tune_workers,
    _check_stop_requested,
    _build_runtime_file_maps,
    _build_meta_file_totals,
    update_progress_title,
)
from GalTransl.Frontend.llm_prepost import (  # noqa: E402
    preprocess_trans_list,
    postprocess_trans_list,
    _run_gendic_flow,
    _has_nonempty_gpt_dict,
    _resolve_file_h_ranges,
)
from GalTransl.Frontend.llm_init import (  # noqa: E402
    init_gptapi,
    fplugins_load_file,
)
from GalTransl.Frontend.llm_postprocess import (  # noqa: E402
    doLLMTranslSingleChunk,
    postprocess_results,
    _resolve_after_translation_order,
    _run_after_trans_single_file,
)
from GalTransl.Frontend.pipeline_stages import (  # noqa: E402
    PIPELINE_STAGES,
    STAGES_BY_KEY,
    stage_display_label,
)
# 必须做真实绑定：下面的 doLLMTranslate 以全局名调用它，模块级 __getattr__ 兜不住
from GalTransl.Frontend.llm_pipeline import _run_full_pipeline  # noqa: E402


async def doLLMTranslate(
    projectConfig: CProjectConfig,
) -> bool:
    """整个项目的翻译入口。

    负责：准备目录/字典/插件/后端 → 载入文件并切块 → 启动 worker 协程池 →
    等所有 chunk 结束后清理自适应调节与进度条相关后台任务。
    单文件完成的后续工作（find_problems / 写缓存快照 / 合并输出）由 `postprocess_results` 触发。
    """

    _check_stop_requested(projectConfig)

    # ---- 1. 基础路径与配置项 ----
    project_dir = projectConfig.getProjectDir()
    input_dir = projectConfig.getInputPath()
    output_dir = projectConfig.getOutputPath()
    cache_dir = _pass3_cache_dir(projectConfig)
    pre_dic_list = projectConfig.getDictCfgSection().get("preDict", [])
    post_dic_list = projectConfig.getDictCfgSection().get("postDict", [])
    gpt_dic_list = projectConfig.getDictCfgSection().get("gpt.dict", [])
    default_dic_dir = projectConfig.getDictCfgSection().get("defaultDictFolder", "")
    # workersPerProject 解析统一走 CProjectConfig.get_workers_per_project（兼容字符串/非法回退 1）
    workersPerProject = projectConfig.get_workers_per_project()
    semaphore = asyncio.Semaphore(workersPerProject)
    adaptive_state = AdaptiveWorkerState(
        max_workers=max(1, workersPerProject),
        effective_workers=max(1, workersPerProject),
    )
    projectConfig.runtime_workers_configured = max(1, workersPerProject)
    projectConfig.runtime_workers_effective = adaptive_state.effective_workers
    projectConfig.runtime_workers_reserved = 0
    fPlugins = projectConfig.fPlugins       # 文件插件（负责 load/save 特定格式）
    tPlugins = projectConfig.tPlugins       # 文本插件（前/后处理钩子）
    eng_type = projectConfig.select_translator  # 选定的后端引擎标识
    input_splitter = projectConfig.input_splitter
    # 清空跨任务残留的"文件已完成 chunk"记录，避免二次运行时误判
    SplitChunkMetadata.clear_file_finished_chunk()
    total_chunks = []
    projectConfig.active_workers = 1
    _update_runtime(
        projectConfig,
        workers_active=0,
        workers_configured=projectConfig.runtime_workers_configured,
    )
    
    makedirs(output_dir, exist_ok=True)
    makedirs(cache_dir, exist_ok=True)

    _check_stop_requested(projectConfig)

    # 语言设置
    if val := projectConfig.getKey("language"):
        sp = val.split("2")
        projectConfig.source_lang = sp[0]
        projectConfig.target_lang = sp[-1]

    # 获取待翻译文件列表
    file_list = get_file_list(projectConfig.getInputPath())
    if not file_list:
        # dump-name / GenDic 等仅基于输入文件的短路流程，空目录不算致命错误，友好返回
        if (
            "dump-name" in eng_type
            or eng_type == "GenDic"
            or eng_type == "ForFileMetaData"
            or eng_type == "ForBatchMetaData"
        ):
            LOGGER.warning(
                f"{projectConfig.getInputPath()} 中没有待翻译的文件，已跳过。"
            )
            return True
        raise RuntimeError(f"{projectConfig.getInputPath()}中没有待翻译的文件")

    # 按文件名自然排序（处理数字部分）
    import re

    def natural_sort_key(s: str) -> list:
        return [
            int(text) if text.isdigit() else text.lower()
            for text in re.split(r"(\d+)", s)
        ]

    file_list.sort(key=natural_sort_key)

    all_jsons = []
    # 按文件收集 json_list，供 ForFileMetaData 等"逐文件生成"引擎使用
    file_json_lists: Dict[str, list] = {}
    # ---- 2. 读取所有文件并切分为 chunk ----
    # 使用线程池并发读文件（IO 密集型），同时通过 fPlugins 解析为 json_list
    file_loader_workers = max(1, min(cpu_count() or 1, 8))
    with ThreadPoolExecutor(max_workers=file_loader_workers) as executor:
        future_to_file = {
            executor.submit(fplugins_load_file, file_path, fPlugins): file_path
            for file_path in file_list
        }
        for future in as_completed(future_to_file):
            _check_stop_requested(projectConfig)
            file_path = future_to_file[future]
            try:
                json_list, save_func = future.result()
                projectConfig.file_save_funcs[file_path] = save_func
                total_chunks.extend(input_splitter.split(json_list, file_path))
                file_json_lists[file_path] = json_list
                if eng_type == "GenDic":
                    all_jsons.extend(json_list)
            except Exception as exc:
                LOGGER.error(get_text("file_processing_error", GT_LANG, file_path, exc))

    # ---- 2.5 完整流水线：ForGal-full-pipeline ----
    if eng_type == "ForGal-full-pipeline":
        _check_stop_requested(projectConfig)
        await ensure_model_available_if_needed(projectConfig)
        await _run_full_pipeline(projectConfig, file_json_lists, file_list)
        return True

    # ---- 2.6 特殊引擎短路：只导出 name 表 / 只生成字典，不进入翻译流程 ----
    if "dump-name" in eng_type:
        _check_stop_requested(projectConfig)
        await dump_name_table_from_chunks(total_chunks, projectConfig)
        return True

    if eng_type == "GenDic":
        _check_stop_requested(projectConfig)
        await ensure_model_available_if_needed(projectConfig)
        gptapi = await init_gptapi(projectConfig)
        return await _run_gendic_flow(projectConfig, all_jsons, gptapi)

    if eng_type == "ForFileMetaData":
        _check_stop_requested(projectConfig)
        await ensure_model_available_if_needed(projectConfig)
        gptapi = await init_gptapi(projectConfig)
        try:
            total = len(file_json_lists)
            LOGGER.info(
                f"[FileMetaData] 开始为 {total} 个文件生成文件级元数据"
            )
            _update_runtime(projectConfig, stage="生成文件级元数据")
            # 上报输入文件总行数：使前端文件进度面板在元数据阶段显示输入文件而非缓存文件
            _update_runtime(
                projectConfig,
                file_totals=_build_meta_file_totals(file_json_lists, input_dir),
            )
            # 载入已有缓存映射，跳过已生成元数据的文件
            existing_fm_map = {}
            try:
                from GalTransl.Backend.metadata import load_file_metadata_map
                existing_fm_map = load_file_metadata_map(projectConfig)
            except Exception as exc:
                LOGGER.debug(f"[FileMetaData] 载入已有缓存失败，将全部重新生成: {exc}")

            # 多 worker 并发生成文件级元数据（绑定 WORKER_ID_CTX，提示词预览按 worker 分板块）
            # workersPerProject 解析统一走 CProjectConfig.get_workers_per_project（兼容字符串/非法回退 1）
            workers_per_project = projectConfig.get_workers_per_project()
            worker_count = max(1, workers_per_project)
            await _run_meta_worker_pool(
                projectConfig, gptapi, file_json_lists,
                existing_map=existing_fm_map,
                worker_count=worker_count,
                tag="FileMetaData", stage_prefix="文件级元数据",
            )
            LOGGER.info("文件级元数据生成完成，已写入 transl_cache/pass1_cache/")

            # 交叉验证：检查 pass1_cache 元数据条目数
            from GalTransl.Backend.metadata import load_file_metadata_map
            try:
                fm_map = load_file_metadata_map(projectConfig)
                fm_count = len(fm_map)
                if fm_count < total:
                    LOGGER.warning(
                        f"[FileMetaData] 交叉验证：{fm_count}/{total} 个文件生成了元数据，"
                        f"缺失 {total - fm_count} 个文件，请检查对应文件的 WARNING 日志"
                    )
                else:
                    LOGGER.info(
                        f"[FileMetaData] 交叉验证：{fm_count}/{total} 个文件全部生成元数据"
                    )
            except Exception as e:
                LOGGER.debug(
                    f"[FileMetaData] 交叉验证读取失败（不影响流程）：{e}"
                )

            _update_runtime(projectConfig, stage="文件级元数据生成完毕")
        finally:
            if hasattr(gptapi, "shutdown"):
                await gptapi.shutdown()
        return True

    if eng_type == "ForPlotRouteMap":
        # 独立运行剧情路线图生成：基于 FileMetaData 剧情摘要 + 用户大纲/结构类型，
        # 输出 PlotRouteMap.json（mermaid 源码 + 文件→路线归属 + 路线剧情摘要）
        _check_stop_requested(projectConfig)
        await ensure_model_available_if_needed(projectConfig)
        gptapi = await init_gptapi(projectConfig)
        try:
            LOGGER.info("[PlotRouteMap] 开始生成剧情路线图")
            _update_runtime(projectConfig, stage="生成剧情路线图")
            structure_type = projectConfig.getKey("internals.plotroute.structureType", "树")
            user_outline = projectConfig.getKey("internals.plotroute.userOutline", "")
            force_regen = projectConfig.getKey(
                "internals.pipeline.forceRegenPlotRoute", False
            )
            ok = await gptapi.batch_translate(
                structure_type=structure_type,
                user_outline=user_outline,
                force_regen=force_regen,
            )
            if not ok:
                LOGGER.warning("[PlotRouteMap] 生成失败或未生成，跳过")
        finally:
            if hasattr(gptapi, "shutdown"):
                await gptapi.shutdown()
        _update_runtime(projectConfig, stage="剧情路线图生成完毕")
        return True

    if eng_type == "ForBatchMetaData":
        # 第二次启动后端：依据文件级剧情元数据将全文划分为翻译区间
        # (批次)，标注视角/氛围/H/用词色彩，写入 transl_cache/pass2_cache/ {filename}.batch.json
        _check_stop_requested(projectConfig)
        await ensure_model_available_if_needed(projectConfig)
        gptapi = await init_gptapi(projectConfig)
        try:
            total = len(file_json_lists)
            LOGGER.info(
                f"[BatchMetaData] 开始为 {total} 个文件划分翻译区间"
            )
            _update_runtime(projectConfig, stage="划分翻译区间")
            # 上报输入文件总行数：使前端文件进度面板在元数据阶段显示输入文件而非缓存文件
            _update_runtime(
                projectConfig,
                file_totals=_build_meta_file_totals(file_json_lists, input_dir),
            )
            # 载入已有缓存映射，跳过已划分批次的文件
            existing_bm_map = {}
            try:
                from GalTransl.Backend.metadata import load_batch_metadata_map
                existing_bm_map = load_batch_metadata_map(projectConfig)
            except Exception as exc:
                LOGGER.debug(f"[BatchMetaData] 载入已有缓存失败，将全部重新生成: {exc}")

            # 多 worker 并发划分翻译区间（绑定 WORKER_ID_CTX，提示词预览按 worker 分板块）
            # workersPerProject 解析统一走 CProjectConfig.get_workers_per_project（兼容字符串/非法回退 1）
            workers_per_project = projectConfig.get_workers_per_project()
            worker_count = max(1, workers_per_project)
            await _run_meta_worker_pool(
                projectConfig, gptapi, file_json_lists,
                existing_map=existing_bm_map,
                worker_count=worker_count,
                tag="BatchMetaData", stage_prefix="批次划分",
            )
            LOGGER.info("批次级元数据生成完成，已写入 transl_cache/pass2_cache/")

            # 交叉验证：检查 pass2_cache 批次元数据条目数
            from GalTransl.Backend.metadata import load_batch_metadata_map
            try:
                bm_map = load_batch_metadata_map(projectConfig)
                bm_count = len(bm_map)
                if bm_count < total:
                    LOGGER.warning(
                        f"[BatchMetaData] 交叉验证：{bm_count}/{total} 个文件划分了批次，"
                        f"缺失 {total - bm_count} 个文件，请检查对应文件的 WARNING 日志"
                    )
                else:
                    LOGGER.info(
                        f"[BatchMetaData] 交叉验证：{bm_count}/{total} 个文件全部划分批次"
                    )
            except Exception as e:
                LOGGER.debug(
                    f"[BatchMetaData] 交叉验证读取失败（不影响流程）：{e}"
                )

            _update_runtime(projectConfig, stage="批次级元数据生成完毕")
        finally:
            if hasattr(gptapi, "shutdown"):
                await gptapi.shutdown()
        return True

    # ---- 2.7b 独立引擎：换行位置异常修复（ForBRStation）/ 残留日文修复（ForJPResidue）/ 禁用词修复（ForBanWordFix）----
    if eng_type in ("ForBRStation", "ForJPResidue", "ForBanWordFix"):
        _check_stop_requested(projectConfig)
        # 按引擎区分日志前缀与运行态阶段名，避免互相误显示
        if eng_type == "ForJPResidue":
            _log_tag, _stage_tag = "[残留日文修复]", "残留日文修复"
        elif eng_type == "ForBanWordFix":
            _log_tag, _stage_tag = "[禁用词修复]", "禁用词修复"
        else:
            _log_tag, _stage_tag = "[换行修复]", "换行位置异常修复"
        await ensure_model_available_if_needed(projectConfig)
        # 载入字典：主流程的字典初始化位于翻译阶段，独立分支需自行加载，
        # 否则 projectConfig.pre_dic 为 None 导致 preprocess_trans_list 崩溃
        projectConfig.pre_dic = CNormalDic(
            initDictList(pre_dic_list, default_dic_dir, project_dir)
        )
        projectConfig.post_dic = CNormalDic(
            initDictList(post_dic_list, default_dic_dir, project_dir)
        )
        projectConfig.gpt_dic = CGptDict(
            initDictList(gpt_dic_list, default_dic_dir, project_dir)
        )
        if projectConfig.getDictCfgSection().get("sortDict", True):
            projectConfig.pre_dic.sort_dic()
            projectConfig.post_dic.sort_dic()
            projectConfig.gpt_dic.sort_dic()
        gptapi = await init_gptapi(projectConfig)
        try:
            total = len(file_json_lists)
            # 复用翻译轮并发数；worker 数 = 文件级并发数（一个 worker 一个文件、文件内串行）
            workers_per_project = projectConfig.get_workers_per_project()
            worker_count = max(1, workers_per_project)
            projectConfig.active_workers = worker_count
            LOGGER.info(
                f"{_log_tag} 开始为 {total} 个文件执行{_stage_tag}，并发 {worker_count} worker"
            )
            _update_runtime(projectConfig, stage=_stage_tag)
            num_better = projectConfig.getKey("gpt.numPerRequestBetter")
            try:
                num_better = int(num_better) if num_better else 100
            except (TypeError, ValueError):
                num_better = 100

            async def _br_single_file(file_path: str, json_list: list) -> None:
                """处理单个文件的换行修复：重建句子、命中缓存、修复并写回备选译文。"""
                _check_stop_requested(projectConfig)
                file_name = (
                    file_path.replace(input_dir, "")
                    .lstrip(os_sep)
                    .replace(os_sep, "-}")
                )
                cache_file_path = joinpath(cache_dir, file_name)
                if not isPathExists(cache_file_path):
                    LOGGER.warning(f"{_log_tag} {file_name} 无缓存译文，跳过")
                    return
                # 从输入 json 重建 CSentense：复用 load_transList（与翻译轮 splitter 一致），
                # 自动处理 name/names/message/index 并链接 prev/next，保证缓存命中匹配
                from GalTransl.Loader import load_transList

                trans_list, _ = load_transList(json_list)
                preprocess_trans_list(
                    trans_list,
                    projectConfig,
                    projectConfig.pre_dic,
                    projectConfig.tPlugins,
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
                # 保存缓存快照（写 alt_dst）：仅当存在有效译文/备选译文时才保存，
                # 避免"无译文"（如缓存未命中）时把已有缓存覆盖成空数组
                has_content = any(
                    t.pre_dst != "" or t.alt_dst != "" or t.proofread_zh != ""
                    for t in trans_list
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
                        f"{_log_tag} {file_name} 无有效译文，跳过缓存保存（保留已有缓存）"
                    )

            # 文件级 worker 池：一个 worker 一个文件、文件内串行，保留单文件多轮对话单链
            file_queue: asyncio.Queue = asyncio.Queue()
            for file_path, json_list in file_json_lists.items():
                file_queue.put_nowait((file_path, json_list))
            for _ in range(worker_count):
                file_queue.put_nowait(None)

            async def _br_worker_loop(worker_index: int) -> None:
                # 绑定 worker 身份，提示词预览按此分板块（与翻译轮 worker 池一致）
                worker_token = WORKER_ID_CTX.set(str(worker_index))
                LOGGER.debug(
                    f"{_log_tag} worker_loop[{worker_index}] 启动, "
                    f"WORKER_ID_CTX={WORKER_ID_CTX.get()!r}"
                )
                try:
                    while True:
                        _check_stop_requested(projectConfig)
                        item = await file_queue.get()
                        if item is None:
                            return
                        file_path, json_list = item
                        await _br_single_file(file_path, json_list)
                finally:
                    WORKER_ID_CTX.reset(worker_token)

            br_tasks = [
                asyncio.create_task(_br_worker_loop(i)) for i in range(worker_count)
            ]
            try:
                await asyncio.gather(*br_tasks)
            except Exception:
                # 任一 worker 抛出未捕获异常（缓存读取/写盘失败等）：取消其余 worker，
                # 避免孤儿任务继续处理导致状态不一致
                for task in br_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*br_tasks, return_exceptions=True)
                raise
            LOGGER.info(f"{_log_tag} {_stage_tag}完成")
            _update_runtime(projectConfig, stage=f"{_stage_tag}完成")
        finally:
            if hasattr(gptapi, "shutdown"):
                await gptapi.shutdown()
        return True

    # ---- 2.7 独立引擎：译文质量改进（ForImproveTranslation）----
    if eng_type == "ForImproveTranslation":
        _check_stop_requested(projectConfig)
        await ensure_model_available_if_needed(projectConfig)
        # 载入字典：主流程的字典初始化位于翻译阶段，独立分支需自行加载，
        # 否则 projectConfig.pre_dic 为 None 导致 preprocess_trans_list 崩溃
        projectConfig.pre_dic = CNormalDic(
            initDictList(pre_dic_list, default_dic_dir, project_dir)
        )
        projectConfig.post_dic = CNormalDic(
            initDictList(post_dic_list, default_dic_dir, project_dir)
        )
        projectConfig.gpt_dic = CGptDict(
            initDictList(gpt_dic_list, default_dic_dir, project_dir)
        )
        if projectConfig.getDictCfgSection().get("sortDict", True):
            projectConfig.pre_dic.sort_dic()
            projectConfig.post_dic.sort_dic()
            projectConfig.gpt_dic.sort_dic()
        gptapi = await init_gptapi(projectConfig)
        try:
            total = len(file_json_lists)
            # 复用翻译轮并发数；worker 数 = 文件级并发数（一个 worker 一个文件、文件内串行）
            workers_per_project = projectConfig.get_workers_per_project()
            worker_count = max(1, workers_per_project)
            projectConfig.active_workers = worker_count
            LOGGER.info(f"[改进轮] 开始为 {total} 个文件执行译文质量改进评估，并发 {worker_count} worker")
            _update_runtime(projectConfig, stage="译文质量改进")
            num_better = projectConfig.getKey("gpt.numPerRequestBetter")
            try:
                num_better = int(num_better) if num_better else 100
            except (TypeError, ValueError):
                num_better = 100

            async def _improve_single_file(file_path: str, json_list: list) -> None:
                """处理单个文件的改进轮：重建句子、命中缓存、评估并写回备选译文。"""
                _check_stop_requested(projectConfig)
                file_name = (
                    file_path.replace(input_dir, "")
                    .lstrip(os_sep)
                    .replace(os_sep, "-}")
                )
                cache_file_path = joinpath(cache_dir, file_name)
                if not isPathExists(cache_file_path):
                    LOGGER.warning(f"[改进轮] {file_name} 无缓存译文，跳过")
                    return
                # 从输入 json 重建 CSentense：复用 load_transList（与翻译轮 splitter 一致），
                # 自动处理 name/names/message/index 并链接 prev/next，保证缓存命中匹配
                from GalTransl.Loader import load_transList

                trans_list, _ = load_transList(json_list)
                preprocess_trans_list(
                    trans_list,
                    projectConfig,
                    projectConfig.pre_dic,
                    projectConfig.tPlugins,
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
                # 保存缓存快照（写 alt_dst）：仅当存在有效译文/备选译文时才保存，
                # 避免"无译文"（如缓存未命中）时把已有缓存覆盖成空数组
                has_content = any(
                    t.pre_dst != "" or t.alt_dst != "" or t.proofread_zh != ""
                    for t in trans_list
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
                        f"[改进轮] {file_name} 无有效译文，跳过缓存保存（保留已有缓存）"
                    )

            # 文件级 worker 池：一个 worker 一个文件、文件内串行，保留单文件多轮对话单链
            file_queue: asyncio.Queue = asyncio.Queue()
            for file_path, json_list in file_json_lists.items():
                file_queue.put_nowait((file_path, json_list))
            for _ in range(worker_count):
                file_queue.put_nowait(None)

            async def _improve_worker_loop(worker_index: int) -> None:
                # 绑定 worker 身份，提示词预览按此分板块（与翻译轮 worker 池一致）
                worker_token = WORKER_ID_CTX.set(str(worker_index))
                LOGGER.debug(
                    f"[改进轮] worker_loop[{worker_index}] 启动, "
                    f"WORKER_ID_CTX={WORKER_ID_CTX.get()!r}"
                )
                try:
                    while True:
                        _check_stop_requested(projectConfig)
                        item = await file_queue.get()
                        if item is None:
                            return
                        file_path, json_list = item
                        await _improve_single_file(file_path, json_list)
                finally:
                    WORKER_ID_CTX.reset(worker_token)

            improve_tasks = [
                asyncio.create_task(_improve_worker_loop(i)) for i in range(worker_count)
            ]
            try:
                await asyncio.gather(*improve_tasks)
            except Exception:
                # 任一 worker 抛出未捕获异常（缓存读取/写盘失败等）：取消其余 worker，
                # 避免孤儿任务继续处理导致状态不一致
                for task in improve_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*improve_tasks, return_exceptions=True)
                raise
            LOGGER.info("[改进轮] 译文质量改进完成")
            _update_runtime(projectConfig, stage="译文质量改进完成")
        finally:
            if hasattr(gptapi, "shutdown"):
                await gptapi.shutdown()
        return True

    # ---- 2.7c 独立引擎：语义差异检测（ForSemCheck）/ 命中句二次复核（ForSemCheckAgain）----
    if eng_type in ("ForSemCheck", "ForSemCheckAgain"):
        _stage_tag = "语义复核" if eng_type == "ForSemCheckAgain" else "语义检测"
        _check_stop_requested(projectConfig)
        await ensure_model_available_if_needed(projectConfig)
        # 载入译前字典：主流程的字典初始化位于翻译阶段，独立分支需自行加载，
        # 否则 projectConfig.pre_dic 为 None 导致 preprocess_trans_list 崩溃
        projectConfig.pre_dic = CNormalDic(
            initDictList(pre_dic_list, default_dic_dir, project_dir)
        )
        projectConfig.post_dic = CNormalDic(
            initDictList(post_dic_list, default_dic_dir, project_dir)
        )
        projectConfig.gpt_dic = CGptDict(
            initDictList(gpt_dic_list, default_dic_dir, project_dir)
        )
        gptapi = await init_gptapi(projectConfig)
        try:
            total = len(file_json_lists)
            worker_count = max(1, projectConfig.get_workers_per_project())
            projectConfig.active_workers = worker_count
            LOGGER.info(
                f"[{_stage_tag}] 开始为 {total} 个文件执行{_stage_tag}，并发 {worker_count} worker"
            )
            _update_runtime(projectConfig, stage=_stage_tag)
            num_better = projectConfig.getKey("gpt.numPerRequestBetter")
            try:
                num_better = int(num_better) if num_better else 100
            except (TypeError, ValueError):
                num_better = 100

            async def _semcheck_single_file(file_path: str, json_list: list) -> None:
                """处理单个文件的语义检测/二次复核：重建句子、命中缓存、执行并写回 suspected_error。"""
                _check_stop_requested(projectConfig)
                file_name = (
                    file_path.replace(input_dir, "")
                    .lstrip(os_sep)
                    .replace(os_sep, "-}")
                )
                cache_file_path = joinpath(cache_dir, file_name)
                if not isPathExists(cache_file_path):
                    LOGGER.warning(f"[{_stage_tag}] {file_name} 无缓存译文，跳过")
                    return
                from GalTransl.Loader import load_transList

                trans_list, _ = load_transList(json_list)
                preprocess_trans_list(
                    trans_list,
                    projectConfig,
                    projectConfig.pre_dic,
                    projectConfig.tPlugins,
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
                # 与主翻译路径一致：先做译文后处理（恢复对话符号/译后字典/dst 插件），
                # 再跑问题检测，避免 post_dst 缺「」导致标点错漏误报「本有引号」。
                postprocess_trans_list(
                    trans_list, projectConfig, projectConfig.post_dic, projectConfig.tPlugins
                )
                # 落盘前重跑 find_problems：让 suspected_error 被认领为「疑似错误」problem
                h_ranges = _resolve_file_h_ranges(
                    project_dir, cache_file_path, projectConfig
                )
                find_problems(trans_list, projectConfig, projectConfig.gpt_dic, h_ranges=h_ranges)
                # 保存缓存快照（写 suspected_error 与 problem）
                has_content = any(
                    t.pre_dst != "" or t.alt_dst != "" or t.proofread_zh != ""
                    for t in trans_list
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
                        f"[{_stage_tag}] {file_name} 无有效译文，跳过缓存保存（保留已有缓存）"
                    )

            file_queue: asyncio.Queue = asyncio.Queue()
            for file_path, json_list in file_json_lists.items():
                file_queue.put_nowait((file_path, json_list))
            for _ in range(worker_count):
                file_queue.put_nowait(None)

            async def _semcheck_worker_loop(worker_index: int) -> None:
                worker_token = WORKER_ID_CTX.set(str(worker_index))
                LOGGER.debug(
                    f"[{_stage_tag}] worker_loop[{worker_index}] 启动, "
                    f"WORKER_ID_CTX={WORKER_ID_CTX.get()!r}"
                )
                try:
                    while True:
                        _check_stop_requested(projectConfig)
                        item = await file_queue.get()
                        if item is None:
                            return
                        file_path, json_list = item
                        await _semcheck_single_file(file_path, json_list)
                finally:
                    WORKER_ID_CTX.reset(worker_token)

            semcheck_tasks = [
                asyncio.create_task(_semcheck_worker_loop(i)) for i in range(worker_count)
            ]
            try:
                await asyncio.gather(*semcheck_tasks)
            except Exception:
                for task in semcheck_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*semcheck_tasks, return_exceptions=True)
                raise
            LOGGER.info(f"[{_stage_tag}] {_stage_tag}完成")
            _update_runtime(projectConfig, stage=f"{_stage_tag}完成")
        finally:
            if hasattr(gptapi, "shutdown"):
                await gptapi.shutdown()
        return True

    # ---- 2.8 独立引擎：仅生成全局游戏分析（ForGlobalPrompt）----
    if eng_type == "ForGlobalPrompt":
        _check_stop_requested(projectConfig)
        await ensure_model_available_if_needed(projectConfig)

        from GalTransl.TextCompressor import TextCompressor
        from GalTransl.DataValidator import (
            validate_input_json,
            validate_global_prompt,
        )
        from GalTransl.Backend.ForGlobalPrompt import (
            ForGlobalPrompt,
            load_global_prompt,
        )

        # 阶段 0：输入数据校验
        LOGGER.info("[GlobalPrompt] 阶段 0/2：输入数据校验")
        _update_runtime(projectConfig, stage="输入数据校验")
        all_valid = True
        for file_path, json_list in file_json_lists.items():
            result = validate_input_json(json_list, file_path)
            if not result["valid"]:
                for err in result["errors"]:
                    LOGGER.error(f"[校验失败] {file_path}: {err}")
                all_valid = False
            for warn in result["warnings"]:
                LOGGER.warning(f"[校验警告] {file_path}: {warn}")
        if not all_valid:
            raise RuntimeError(
                "输入数据校验失败，全局分析中止。请修复上述错误后重试。"
            )
        LOGGER.info("[GlobalPrompt] 阶段 0 完成：所有输入文件校验通过")

        # 阶段 1：文本压缩（产出 {file_path: compressed_text} 字典）
        LOGGER.info("[GlobalPrompt] 阶段 1/2：文本无损压缩")
        _update_runtime(projectConfig, stage="文本无损压缩")
        max_chars = projectConfig.getKey(
            "internals.pipeline.maxInputChars", 950000
        )
        compressor = TextCompressor(max_chars=max_chars)
        compressed_texts: Dict[str, str] = {}
        for file_path, json_list in file_json_lists.items():
            compressed = compressor.compress({file_path: json_list})
            compressed_texts[file_path] = compressed

        # 阶段 2：全局游戏分析
        LOGGER.info("[GlobalPrompt] 阶段 2/2：全局游戏分析")
        _update_runtime(projectConfig, stage="生成全局游戏分析")
        gptapi_global = ForGlobalPrompt(
            projectConfig, "ForGlobalPrompt",
            projectConfig.proxyPool, projectConfig.tokenPool,
        )
        try:
            external_info = projectConfig.getKey("externals.gameInfo", "") or ""
            success = await gptapi_global.batch_translate(
                compressed_texts, external_info=external_info
            )
            if not success:
                LOGGER.error("[GlobalPrompt] 全局游戏分析生成失败")
                raise RuntimeError("全局游戏分析生成失败")

            # 校验 GlobalPrompt.json
            global_prompt = load_global_prompt(projectConfig)
            if global_prompt is None:
                raise RuntimeError("GlobalPrompt.json 不存在或格式错误")
            gp_validation = validate_global_prompt(global_prompt)
            if not gp_validation["valid"]:
                for err in gp_validation["errors"]:
                    LOGGER.error(f"[GlobalPrompt] 内容校验失败: {err}")
                raise RuntimeError("GlobalPrompt 内容校验失败")
            for warn in gp_validation.get("warnings", []):
                LOGGER.warning(f"[GlobalPrompt] 警告: {warn}")

            char_count = len(global_prompt.get("角色列表", []))
            LOGGER.info(
                f"[GlobalPrompt] 全局分析已生成，{char_count} 个角色，"
                f"已写入 transl_cache/pass0_cache/GlobalPrompt.json"
            )
            _update_runtime(projectConfig, stage="全局游戏分析生成完毕")
        finally:
            if hasattr(gptapi_global, "shutdown"):
                await gptapi_global.shutdown()
        return True

    # 3. 根据 sortBy 决定 chunk 顺序：name（文件名自然序）或 size（大 chunk 优先）
    soryBy = projectConfig.getKey("sortBy", "name")
    if soryBy == "name":
        # 按文件分组chunks，保持文件内部的顺序
        file_chunks = {}
        for chunk in total_chunks:
            if chunk.file_path not in file_chunks:
                file_chunks[chunk.file_path] = []
            file_chunks[chunk.file_path].append(chunk)

        # 确保每个文件内的chunks按索引排序
        for file_path in file_chunks:
            file_chunks[file_path].sort(key=lambda x: x.chunk_index)

        # 按照file_list的顺序处理文件，保持文件间的顺序
        ordered_chunks = []
        for file_path in file_list:
            if file_path in file_chunks:
                ordered_chunks.extend(file_chunks[file_path])
    elif soryBy == "size":
        total_chunks.sort(key=lambda x: x.chunk_size, reverse=True)
        ordered_chunks = total_chunks

    total_lines = sum([len(chunk.trans_list) for chunk in ordered_chunks])
    runtime_file_totals, runtime_cache_map = _build_runtime_file_maps(ordered_chunks, input_dir)
    _update_runtime(projectConfig, file_totals=runtime_file_totals, cache_file_display_map=runtime_cache_map)

    # ---- 4. name 替换表（首次运行时自动生成）----
    name_replaceDict_path_xlsx = joinpath(
        projectConfig.getProjectDir(), "name替换表.xlsx"
    )
    name_replaceDict_path_csv = joinpath(
        projectConfig.getProjectDir(), "name替换表.csv"
    )
    name_replaceDict_firstime = False
    if not isPathExists(name_replaceDict_path_csv) and not isPathExists(
        name_replaceDict_path_xlsx
    ):
        await dump_name_table_from_chunks(total_chunks, projectConfig)
        name_replaceDict_firstime = True
    
    # ---- 5. 载入字典（pre/post/gpt）----
    projectConfig.pre_dic = CNormalDic(
        initDictList(pre_dic_list, default_dic_dir, project_dir)
    )
    projectConfig.post_dic = CNormalDic(
        initDictList(post_dic_list, default_dic_dir, project_dir)
    )
    projectConfig.gpt_dic = CGptDict(
        initDictList(gpt_dic_list, default_dic_dir, project_dir)
    )

    if projectConfig.getDictCfgSection().get("sortDict", True):
        projectConfig.pre_dic.sort_dic()
        projectConfig.post_dic.sort_dic()
        projectConfig.gpt_dic.sort_dic()

    # 载入name替换表
    if isPathExists(name_replaceDict_path_csv):
        projectConfig.name_replaceDict = load_name_table(
            name_replaceDict_path_csv, name_replaceDict_firstime,total_chunks,projectConfig
        )
    elif isPathExists(name_replaceDict_path_xlsx):
        projectConfig.name_replaceDict = load_name_table(
            name_replaceDict_path_xlsx, name_replaceDict_firstime,total_chunks,projectConfig
        )

    # ---- 6. 初始化共享的 gptapi 实例（所有 worker 共用同一实例）----
    gptapi = await init_gptapi(projectConfig)

    title_update_task = None  # 初始化任务变量
    auto_tune_task = None
    # 自适应降并发时通过 acquire 占住的槽位数；恢复时再 release
    reserved_permits = 0

    async def set_effective_workers(target: int) -> None:
        """把 effective_workers 调整到 target：
        - 降低：acquire (current-target) 个槽位记为 reserved_permits
        - 提升：release 之前 reserved 的槽位
        通过"预占信号量"而不是直接改 semaphore，避免破坏 asyncio.Semaphore 的内部状态。
        """
        nonlocal reserved_permits

        target = max(1, min(adaptive_state.max_workers, int(target)))
        current = adaptive_state.max_workers - reserved_permits
        if target == current:
            return

        if target < current:
            need_reserve = current - target
            for _ in range(need_reserve):
                _check_stop_requested(projectConfig)
                await semaphore.acquire()
                reserved_permits += 1
        else:
            release_count = min(target - current, reserved_permits)
            for _ in range(release_count):
                semaphore.release()
                reserved_permits -= 1

        adaptive_state.effective_workers = adaptive_state.max_workers - reserved_permits
        projectConfig.runtime_workers_effective = adaptive_state.effective_workers
        projectConfig.runtime_workers_reserved = reserved_permits

    # ---- 7. 进入翻译阶段：进度条 + worker 协程池 ----
    with terminal_progress(
        should_print_translation_logs(projectConfig),
        total=total_lines, title="翻译进度", unit=" line", enrich_print=False, dual_line=True,length=30
    ) as bar:
        projectConfig.bar = bar

        # 启动后台任务来更新进度条标题
        title_update_task = asyncio.create_task(
            update_progress_title(bar, semaphore, workersPerProject, projectConfig)
        )

        enable_auto_workers = bool(projectConfig.getKey("autoAdjustWorkers", False))
        if enable_auto_workers and workersPerProject > 1:
            auto_tune_task = asyncio.create_task(
                auto_tune_workers(projectConfig, adaptive_state, set_effective_workers)
            )

        # 用队列 + 哨兵 None 驱动 worker，避免每个 worker 去算自己的分片
        worker_count = max(1, workersPerProject)
        chunk_queue: asyncio.Queue[Optional[SplitChunkMetadata]] = asyncio.Queue()
        for chunk in ordered_chunks:
            _check_stop_requested(projectConfig)
            chunk_queue.put_nowait(chunk)

        # 每个 worker 取到 None 即退出
        for _ in range(worker_count):
            chunk_queue.put_nowait(None)

        async def worker_loop(worker_index: int = 0):
            # 每个 worker task 独立 contextvars 上下文，提示词推送按此隔离板块
            worker_token = WORKER_ID_CTX.set(str(worker_index))
            LOGGER.debug(f"[prompt-preview] worker_loop[{worker_index}] 启动, WORKER_ID_CTX={WORKER_ID_CTX.get()!r}")
            try:
                while True:
                    _check_stop_requested(projectConfig)
                    split_chunk = await chunk_queue.get()
                    if split_chunk is None:
                        return
                    await doLLMTranslSingleChunk(
                        semaphore,
                        split_chunk=split_chunk,
                        projectConfig=projectConfig,
                        gptapi=gptapi,  # 传递共享的 gptapi 实例
                    )
            finally:
                WORKER_ID_CTX.reset(worker_token)

        worker_tasks = [
            asyncio.create_task(worker_loop(worker_index=i))
            for i in range(worker_count)
        ]

        try:
            await asyncio.gather(*worker_tasks)
        except Exception:
            for worker_task in worker_tasks:
                if not worker_task.done():
                    worker_task.cancel()
            await asyncio.gather(*worker_tasks, return_exceptions=True)
            raise
        finally:
            for worker_task in worker_tasks:
                if not worker_task.done():
                    worker_task.cancel()

        try:
            await asyncio.gather(*worker_tasks, return_exceptions=True)
        finally:
            if auto_tune_task:
                auto_tune_task.cancel()
                try:
                    await auto_tune_task
                except asyncio.CancelledError:
                    pass
            if reserved_permits > 0:
                await set_effective_workers(adaptive_state.max_workers)

            # 确保无论 gather 成功还是失败，都取消标题更新任务
            if title_update_task:
                title_update_task.cancel()
                # 等待任务实际被取消（可选，但有助于确保清理）
                try:
                    await title_update_task
                except asyncio.CancelledError:
                    pass  # 捕获预期的取消错误

            shutdown_callable = getattr(gptapi, "shutdown", None)
            if callable(shutdown_callable):
                try:
                    await shutdown_callable()
                except Exception as ex:
                    LOGGER.warning(f"关闭模型客户端时出错: {str(ex)}")



async def _run_translation_phase(
    projectConfig: CProjectConfig,
    file_json_lists: dict,
    file_list: list,
) -> None:
    """
    执行翻译阶段（流水线阶段 6）。

    复用现有的翻译流程核心逻辑：
    - 切块 → worker 协程池 → 翻译每个 chunk → 后处理 → 输出
    """
    import os
    from os.path import join as joinpath, exists as isPathExists, dirname, basename as os_basename, abspath

    _check_stop_requested(projectConfig)

    # 清空跨任务残留的"文件已完成 chunk"记录，避免二次运行时误判
    SplitChunkMetadata.clear_file_finished_chunk()

    project_dir = projectConfig.getProjectDir()
    input_dir = projectConfig.getInputPath()
    output_dir = projectConfig.getOutputPath()
    cache_dir = _pass3_cache_dir(projectConfig)

    eng_type = projectConfig.select_translator
    fPlugins = projectConfig.fPlugins
    tPlugins = projectConfig.tPlugins
    input_splitter = projectConfig.input_splitter
    # workersPerProject 解析统一走 CProjectConfig.get_workers_per_project（兼容字符串/非法回退 1）
    workersPerProject = projectConfig.get_workers_per_project()

    pre_dic_list = projectConfig.getDictCfgSection().get("preDict", [])
    post_dic_list = projectConfig.getDictCfgSection().get("postDict", [])
    gpt_dic_list = projectConfig.getDictCfgSection().get("gpt.dict", [])
    default_dic_dir = projectConfig.getDictCfgSection().get("defaultDictFolder", "")

    # 切块
    total_chunks = []
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from os import cpu_count
    loader_workers = max(1, min(cpu_count() or 1, 8))
    with ThreadPoolExecutor(max_workers=loader_workers) as executor:
        future_to_file = {
            executor.submit(fplugins_load_file, fp, fPlugins): fp
            for fp in file_list
        }
        for future in as_completed(future_to_file):
            fp = future_to_file[future]
            try:
                jl, sf = future.result()
                projectConfig.file_save_funcs[fp] = sf
                total_chunks.extend(input_splitter.split(jl, fp))
            except Exception as exc:
                LOGGER.error(
                    f"处理文件 {os.path.basename(fp)} 时发生错误: {exc}"
                )

    # 排序
    soryBy = projectConfig.getKey("sortBy", "name")
    if soryBy == "name":
        file_chunks = {}
        for chunk in total_chunks:
            if chunk.file_path not in file_chunks:
                file_chunks[chunk.file_path] = []
            file_chunks[chunk.file_path].append(chunk)
        for fp in file_chunks:
            file_chunks[fp].sort(key=lambda x: x.chunk_index)
        ordered_chunks = []
        for fp in file_list:
            if fp in file_chunks:
                ordered_chunks.extend(file_chunks[fp])
    else:
        total_chunks.sort(key=lambda x: x.chunk_size, reverse=True)
        ordered_chunks = total_chunks

    total_lines = sum(len(chunk.trans_list) for chunk in ordered_chunks)
    runtime_file_totals, runtime_cache_map = _build_runtime_file_maps(
        ordered_chunks, input_dir
    )
    _update_runtime(
        projectConfig,
        file_totals=runtime_file_totals,
        cache_file_display_map=runtime_cache_map,
    )

    # name 替换表
    name_replaceDict_path_csv = joinpath(project_dir, "name替换表.csv")
    name_replaceDict_path_xlsx = joinpath(project_dir, "name替换表.xlsx")
    name_replaceDict_firstime = False
    if not isPathExists(name_replaceDict_path_csv) and not isPathExists(
        name_replaceDict_path_xlsx
    ):
        from GalTransl.Name import dump_name_table_from_chunks
        await dump_name_table_from_chunks(total_chunks, projectConfig)
        name_replaceDict_firstime = True

    # 字典
    from GalTransl.ConfigHelper import initDictList
    from GalTransl.Dictionary import CNormalDic, CGptDict
    projectConfig.pre_dic = CNormalDic(
        initDictList(pre_dic_list, default_dic_dir, project_dir)
    )
    projectConfig.post_dic = CNormalDic(
        initDictList(post_dic_list, default_dic_dir, project_dir)
    )
    projectConfig.gpt_dic = CGptDict(
        initDictList(gpt_dic_list, default_dic_dir, project_dir)
    )
    if projectConfig.getDictCfgSection().get("sortDict", True):
        projectConfig.pre_dic.sort_dic()
        projectConfig.post_dic.sort_dic()
        projectConfig.gpt_dic.sort_dic()

    if isPathExists(name_replaceDict_path_csv):
        from GalTransl.Name import load_name_table
        projectConfig.name_replaceDict = load_name_table(
            name_replaceDict_path_csv, name_replaceDict_firstime,
            total_chunks, projectConfig,
        )
    elif isPathExists(name_replaceDict_path_xlsx):
        from GalTransl.Name import load_name_table
        projectConfig.name_replaceDict = load_name_table(
            name_replaceDict_path_xlsx, name_replaceDict_firstime,
            total_chunks, projectConfig,
        )

    # 初始化 gptapi：流水线翻译阶段固定用 ForGal-json-translate
    saved_translator = projectConfig.select_translator
    projectConfig.select_translator = "ForGal-json-translate"
    try:
        await ensure_model_available_if_needed(projectConfig, stage="translate")
        gptapi = await init_gptapi(
            projectConfig, token_pool=_stage_pool(projectConfig, "translate")
        )
    finally:
        projectConfig.select_translator = saved_translator

    # 并发控制
    semaphore = asyncio.Semaphore(workersPerProject)
    adaptive_state = AdaptiveWorkerState(
        max_workers=max(1, workersPerProject),
        effective_workers=max(1, workersPerProject),
    )
    projectConfig.runtime_workers_configured = max(1, workersPerProject)
    projectConfig.runtime_workers_effective = adaptive_state.effective_workers
    projectConfig.runtime_workers_reserved = 0

    # 进度条 + worker 协程池
    from GalTransl.TerminalOutput import should_print_translation_logs, terminal_progress

    with terminal_progress(
        should_print_translation_logs(projectConfig),
        total=total_lines, title="翻译进度", unit=" line",
        enrich_print=False, dual_line=True, length=30,
    ) as bar:
        projectConfig.bar = bar

        title_update_task = asyncio.create_task(
            update_progress_title(
                bar, semaphore, workersPerProject, projectConfig
            )
        )

        enable_auto_workers = bool(
            projectConfig.getKey("autoAdjustWorkers", False)
        )
        auto_tune_task = None
        reserved_permits = 0

        async def set_effective_workers(target: int) -> None:
            nonlocal reserved_permits
            target = max(1, min(adaptive_state.max_workers, int(target)))
            current = adaptive_state.max_workers - reserved_permits
            if target == current:
                return
            if target < current:
                need_reserve = current - target
                for _ in range(need_reserve):
                    _check_stop_requested(projectConfig)
                    await semaphore.acquire()
                    reserved_permits += 1
            else:
                release_count = min(target - current, reserved_permits)
                for _ in range(release_count):
                    semaphore.release()
                    reserved_permits -= 1
            adaptive_state.effective_workers = (
                adaptive_state.max_workers - reserved_permits
            )
            projectConfig.runtime_workers_effective = (
                adaptive_state.effective_workers
            )
            projectConfig.runtime_workers_reserved = reserved_permits

        if enable_auto_workers and workersPerProject > 1:
            auto_tune_task = asyncio.create_task(
                auto_tune_workers(
                    projectConfig, adaptive_state, set_effective_workers
                )
            )

        worker_count = max(1, workersPerProject)
        chunk_queue: asyncio.Queue = asyncio.Queue()
        for chunk in ordered_chunks:
            _check_stop_requested(projectConfig)
            chunk_queue.put_nowait(chunk)
        for _ in range(worker_count):
            chunk_queue.put_nowait(None)

        async def worker_loop(worker_index: int = 0):
            # 与 doLLMTranslate 中 worker 池一致：绑定 worker 身份，提示词按此分板块
            worker_token = WORKER_ID_CTX.set(str(worker_index))
            LOGGER.debug(f"[prompt-preview] pipeline worker_loop[{worker_index}] 启动, WORKER_ID_CTX={WORKER_ID_CTX.get()!r}")
            try:
                while True:
                    _check_stop_requested(projectConfig)
                    split_chunk = await chunk_queue.get()
                    if split_chunk is None:
                        return
                    await doLLMTranslSingleChunk(
                        semaphore,
                        split_chunk=split_chunk,
                        projectConfig=projectConfig,
                        gptapi=gptapi,
                    )
            finally:
                WORKER_ID_CTX.reset(worker_token)

        worker_tasks = [
            asyncio.create_task(worker_loop(worker_index=i))
            for i in range(worker_count)
        ]

        try:
            await asyncio.gather(*worker_tasks)
        except Exception:
            for worker_task in worker_tasks:
                if not worker_task.done():
                    worker_task.cancel()
            await asyncio.gather(*worker_tasks, return_exceptions=True)
            raise
        finally:
            for worker_task in worker_tasks:
                if not worker_task.done():
                    worker_task.cancel()

        try:
            await asyncio.gather(*worker_tasks, return_exceptions=True)
        finally:
            if auto_tune_task:
                auto_tune_task.cancel()
                try:
                    await auto_tune_task
                except asyncio.CancelledError:
                    pass
            if reserved_permits > 0:
                await set_effective_workers(adaptive_state.max_workers)
            if title_update_task:
                title_update_task.cancel()
                try:
                    await title_update_task
                except asyncio.CancelledError:
                    pass
            shutdown_callable = getattr(gptapi, "shutdown", None)
            if callable(shutdown_callable):
                try:
                    await shutdown_callable()
                except Exception as ex:
                    LOGGER.warning(f"关闭模型客户端时出错: {str(ex)}")
