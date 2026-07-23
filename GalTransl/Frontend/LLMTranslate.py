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
from os.path import join as joinpath, exists as isPathExists, dirname, basename as os_basename
from venv import logger
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import time
import asyncio
from dataclasses import dataclass

from GalTransl import LOGGER, NEED_OpenAITokenPool
from GalTransl.i18n import get_text, GT_LANG
from GalTransl.Cache import get_transCache_from_json
from GalTransl.ConfigHelper import initDictList, CProjectConfig
from GalTransl.CSentense import CTransList
from GalTransl.Dictionary import CGptDict, CNormalDic
from GalTransl.Problem import find_problems
from GalTransl.Cache import save_transCache_to_json
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


def _runtime_project_dir(projectConfig: CProjectConfig) -> str:
    """取当前运行时使用的项目目录（桌面端/服务端会覆盖为实际工作目录）。"""
    return getattr(projectConfig, "runtime_project_dir", projectConfig.getProjectDir())


def _update_runtime(projectConfig: CProjectConfig, **kwargs: Any) -> None:
    """向 server 运行时状态上报进度信息（桌面端订阅用）。

    服务端未启动时静默失败，不影响 CLI 运行。
    """
    try:
        from GalTransl.server import update_runtime_status
        update_runtime_status(_runtime_project_dir(projectConfig), **kwargs)
    except Exception:
        return


def _pass3_cache_dir(projectConfig: CProjectConfig) -> str:
    """返回 Pass 3 翻译缓存目录（transl_cache/pass3_cache）。"""
    from GalTransl import PASS3_CACHE_DIR
    from os.path import join as joinpath
    return joinpath(projectConfig.getCachePath(), PASS3_CACHE_DIR)


async def ensure_model_available_if_needed(projectConfig: CProjectConfig) -> None:
    """在真正需要调用模型前，按需执行一次可用性检查。"""
    translator = getattr(projectConfig, "select_translator", "")
    if not any(x in translator for x in NEED_OpenAITokenPool):
        return

    check_available = projectConfig.getBackendConfigSection("OpenAI-Compatible").get(
        "checkAvailable", True
    )
    if not check_available:
        return

    if getattr(projectConfig, "_model_availability_checked", False):
        return

    model_check_lock = getattr(projectConfig, "_model_check_lock", None)
    if model_check_lock is None:
        model_check_lock = asyncio.Lock()
        setattr(projectConfig, "_model_check_lock", model_check_lock)

    async with model_check_lock:
        if getattr(projectConfig, "_model_availability_checked", False):
            return

        token_pool = getattr(projectConfig, "tokenPool", None)
        if token_pool is None:
            return

        _check_stop_requested(projectConfig)
        proxy_pool = getattr(projectConfig, "proxyPool", None)
        _update_runtime(projectConfig, stage="检查模型可用性")
        try:
            await token_pool.checkTokenAvailablity(
                proxy_pool.getProxy() if proxy_pool else None,
                translator,
            )
            token_pool.getToken()
            setattr(projectConfig, "_model_availability_checked", True)
        finally:
            _update_runtime(projectConfig, stage="")


@dataclass
class AdaptiveWorkerState:
    """自适应并发状态。

    - max_workers: 用户在配置中指定的并发上限，运行期间不变。
    - effective_workers: 当前实际允许的并发数，会被 auto_tune_workers 动态调整。
    """
    max_workers: int
    effective_workers: int


async def auto_tune_workers(
    projectConfig: CProjectConfig,
    adaptive_state: AdaptiveWorkerState,
    apply_limit: Any,
) -> None:
    """后台自适应并发调节任务。

    基于最近 30s 的请求健康度（429 比例 / 平均延迟）上下调 effective_workers：
    - 429 比例高 或 延迟高 → 减 1（最低 1）
    - 两者都低 → 加 1（不超过 max_workers）
    通过 apply_limit 回调去 acquire/release 信号量槽位，实现软限流。
    """
    metrics = getattr(projectConfig, "request_health_metrics", None)
    if metrics is None:
        return

    while True:
        await asyncio.sleep(3.0)
        snapshot = metrics.snapshot(window_seconds=30.0)
        total = int(snapshot.get("total", 0))
        if total < 8:
            # 样本不足，避免噪声触发调整
            continue

        ratio_429 = float(snapshot.get("rate_limited_ratio", 0.0))
        avg_latency = float(snapshot.get("avg_latency", 0.0))
        current = adaptive_state.effective_workers
        target = current

        if ratio_429 >= 0.18 or avg_latency >= 12.0:
            target = max(1, current - 1)
        elif ratio_429 <= 0.05 and avg_latency <= 6.0:
            target = min(adaptive_state.max_workers, current + 1)

        if target != current:
            await apply_limit(target)


def _check_stop_requested(projectConfig: CProjectConfig) -> None:
    """协作式取消检查点：若桌面端/服务端触发 stop_event，则抛出 JobCancelledError 中止当前任务。

    在各关键步骤（IO 前、进入循环、chunk 处理前等）调用，避免写到一半被硬中断。
    """
    stop_event = getattr(projectConfig, "stop_event", None)
    if stop_event is not None and stop_event.is_set():
        from GalTransl.Service import JobCancelledError

        raise JobCancelledError()


def _build_runtime_file_maps(ordered_chunks: list[SplitChunkMetadata], input_dir: str) -> tuple[dict[str, int], dict[str, str]]:
    """构造两个给前端使用的映射：

    - file_totals: {显示名: 该文件总行数}，用于前端展示每个文件的进度分母。
    - cache_file_display_map: {缓存文件名(.json): 显示名}，用于把缓存回写事件关联到对应文件。
    """
    file_totals: dict[str, int] = {}
    cache_file_display_map: dict[str, str] = {}

    for chunk in ordered_chunks:
        display_name = chunk.file_path.replace(input_dir, "").lstrip(os_sep).replace(os_sep, "/")
        file_totals.setdefault(display_name, 0)
        non_cross_start = max(0, int(chunk.cross_num or 0))
        non_cross_end = min(non_cross_start + int(chunk.chunk_non_cross_size or 0), len(chunk.json_list))
        progress_countable = 0
        for row in chunk.json_list[non_cross_start:non_cross_end]:
            if not isinstance(row, dict):
                continue
            message = str(row.get("message", "") or "").strip()
            if not message:
                continue
            progress_countable += 1
        file_totals[display_name] += progress_countable
        cache_key = display_name.replace("/", "-}")
        if chunk.total_chunks > 1:
            cache_key = f"{cache_key}_{chunk.chunk_index}"

        if not cache_key.endswith(".json"):
            cache_key = f"{cache_key}.json"
        cache_file_display_map[cache_key] = display_name

    return file_totals, cache_file_display_map


async def update_progress_title(
    bar: Any, semaphore: asyncio.Semaphore, workersPerProject: int, projectConfig: CProjectConfig
) -> None:
    """异步任务，用于动态更新 alive_bar 的标题以显示活动工作线程数。"""
    base_title = "翻译进度"
    is_interactive = should_print_translation_logs(projectConfig)
    while True:
        try:
            # 计算当前活动任务数（_value 变化：acquire 减少，release 增加）
            reserved_workers = int(getattr(projectConfig, "runtime_workers_reserved", 0))
            active_workers = workersPerProject - semaphore._value - reserved_workers
            # 确保 active_workers 不会是负数（以防万一）
            active_workers = max(0, active_workers)
            configured_workers = int(
                getattr(projectConfig, "runtime_workers_configured", workersPerProject)
            )
            configured_workers = max(1, configured_workers)
            if active_workers == 0:
                projectConfig.active_workers = configured_workers
            else:
                projectConfig.active_workers = active_workers
            _update_runtime(
                projectConfig,
                workers_active=active_workers,
                workers_configured=configured_workers,
            )
            # 更新标题（仅 CLI 模式有 bar）
            if is_interactive:
                new_title = f"{base_title} [{active_workers}/{configured_workers} 并发]"
                bar.title(new_title)

            # 每隔一段时间更新一次，避免过于频繁
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            # 当任务被取消时，设置最终标题并退出循环
            if is_interactive:
                bar.title(f"{base_title} [处理完成]")
            break
        except Exception as e:
            # 记录任何其他异常并停止更新
            LOGGER.error(f"更新进度条标题时出错: {e}")
            bar.title(f"{base_title} [更新出错]")
            break


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
    pre_dic_list = projectConfig.getDictCfgSection()["preDict"]
    post_dic_list = projectConfig.getDictCfgSection()["postDict"]
    gpt_dic_list = projectConfig.getDictCfgSection()["gpt.dict"]
    default_dic_dir = projectConfig.getDictCfgSection()["defaultDictFolder"]
    # 兼容 YAML 中写成字符串（如 workersPerProject: '4'）的情况，统一强转为 int
    _workers_raw = projectConfig.getKey("workersPerProject")
    workersPerProject = int(_workers_raw) if _workers_raw is not None else 1
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
    # 载入 gt_input 中的 FileMetaData.json
    from GalTransl.Backend.ForGalJsonMulitChat import load_file_metadata
    projectConfig.file_metadata = load_file_metadata(projectConfig)
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
        LOGGER.info(f"[GenDic] 开始为 {len(all_jsons)} 条文本生成 GPT 字典")
        await gptapi.batch_translate(all_jsons)
        LOGGER.info("[GenDic] GPT 字典生成完成")
        return True

    if eng_type == "ForFileMetaData":
        _check_stop_requested(projectConfig)
        await ensure_model_available_if_needed(projectConfig)
        gptapi = await init_gptapi(projectConfig)
        total = len(file_json_lists)
        LOGGER.info(
            f"[FileMetaData] 开始为 {total} 个文件生成文件级元数据"
        )
        _update_runtime(projectConfig, stage="生成文件级元数据")
        for i, (file_path, jsons) in enumerate(file_json_lists.items(), 1):
            fname = os_basename(file_path)
            LOGGER.debug(
                f"[FileMetaData] ({i}/{total}) 开始处理 {fname}，"
                f"共 {len(jsons)} 句"
            )
            _update_runtime(projectConfig, current_file=fname,
                            stage=f"({i}/{total}) {fname}")
            await gptapi.batch_translate(jsons, filename=fname)
            LOGGER.debug(f"[FileMetaData] ({i}/{total}) {fname} 处理完成")
        LOGGER.info("文件级元数据生成完成，已写入 transl_cache/pass1_cache/")

        # 交叉验证：检查 FileMetaData.json 条目数
        from GalTransl.Backend.ForGalJsonMulitChat import load_file_metadata_map
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
        return True

    if eng_type == "ForBatchMetaData":
        # 第二次启动后端：依据文件级剧情元数据将全文划分为翻译区间
        # (批次)，标注视角/氛围/H/用词色彩，写入 transl_cache/pass2_cache/BatchMetadata.json
        _check_stop_requested(projectConfig)
        await ensure_model_available_if_needed(projectConfig)
        gptapi = await init_gptapi(projectConfig)
        total = len(file_json_lists)
        LOGGER.info(
            f"[BatchMetaData] 开始为 {total} 个文件划分翻译区间"
        )
        _update_runtime(projectConfig, stage="划分翻译区间")
        for i, (file_path, jsons) in enumerate(file_json_lists.items(), 1):
            fname = os_basename(file_path)
            LOGGER.debug(
                f"[BatchMetaData] ({i}/{total}) 开始处理 {fname}，"
                f"共 {len(jsons)} 句"
            )
            _update_runtime(projectConfig, current_file=fname,
                            stage=f"({i}/{total}) {fname}")
            await gptapi.batch_translate(jsons, filename=fname)
            LOGGER.debug(f"[BatchMetaData] ({i}/{total}) {fname} 处理完成")
        LOGGER.info("批次级元数据生成完成，已写入 transl_cache/pass2_cache/")

        # 交叉验证：检查 BatchMetadata.json 条目数
        from GalTransl.Backend.ForGalJsonMulitChat import load_batch_metadata_map
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
        return True

    # ---- 2.7 独立引擎：仅生成全局游戏分析（ForGlobalPrompt）----
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
            "internals.pipeline.maxInputChars", 80000
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

        async def worker_loop():
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

        worker_tasks = [
            asyncio.create_task(worker_loop())
            for _ in range(worker_count)
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


# ─────────────────────────────────────────────────────
# 完整翻译流水线编排器
# ─────────────────────────────────────────────────────

async def _run_full_pipeline(
    projectConfig: CProjectConfig,
    file_json_lists: dict,  # {file_path: json_list}
    file_list: list,
) -> None:
    """
    完整翻译流水线：按顺序执行所有阶段，每阶段输出经过校验后才进入下一阶段。

    阶段：
      0. 输入数据校验
      1. TextCompressor 压缩全文
      2. ForGlobalPrompt 生成全局游戏分析
      3. GenDic 构建术语表（可跳过，如果已有）
      4. ForFileMetaData 逐文件生成文件级元数据（可跳过，如果已有）
      5. ForBatchMetaData 逐文件划分翻译区间（可跳过，如果已有）
      6. ForGalJsonMulitChat 翻译（按 chunk 缓存命中跳过）
    """
    import os

    _check_stop_requested(projectConfig)
    _update_runtime(projectConfig, stage="完整流水线启动")

    eng_type = projectConfig.select_translator

    # ── 阶段 0：输入数据校验 ──
    LOGGER.info("=" * 50)
    LOGGER.info("[流水线] 阶段 0/6：输入数据校验")
    _update_runtime(projectConfig, stage="输入数据校验")

    from GalTransl.DataValidator import validate_input_json

    all_valid = True
    for file_path, json_list in file_json_lists.items():
        result = validate_input_json(json_list, file_path)
        if not result["valid"]:
            for err in result["errors"]:
                LOGGER.error(f"[校验失败] {file_path}: {err}")
            all_valid = False
        for warn in result["warnings"]:
            LOGGER.warning(f"[校验警告] {file_path}: {warn}")
        stats = result["stats"]
        LOGGER.info(
            f"[校验通过] {os.path.basename(file_path)}: "
            f"{stats['total_items']} 条，"
            f"name={stats['items_with_name']}，"
            f"无name={stats['items_without_name']}"
        )
    if not all_valid:
        raise RuntimeError(
            "输入数据校验失败，流水线中止。请修复上述错误后重试。"
        )
    LOGGER.info("[流水线] 阶段 0 完成：所有输入文件校验通过")

    # ── 阶段 1：文本压缩 ──
    LOGGER.info("[流水线] 阶段 1/6：文本无损压缩")
    _update_runtime(projectConfig, stage="文本无损压缩")

    from GalTransl.TextCompressor import TextCompressor

    max_chars = projectConfig.getKey("internals.pipeline.maxInputChars", 80000)
    compressor = TextCompressor(max_chars=max_chars)

    # 逐文件压缩（保留文件边界，供 ForGlobalPrompt 按文件注入上下文）
    compressed_texts: Dict[str, str] = {}
    for file_path, json_list in file_json_lists.items():
        compressed = compressor.compress(
            {file_path: json_list},
        )
        compressed_texts[file_path] = compressed

    # 全局压缩（所有文件合并，供完整性校验用）
    all_compressed_text = compressor.compress(
        file_json_lists,
    )

    # 校验压缩完整性：确保所有 message 和 name 完整保留
    verify_result = compressor.verify_compression(
        file_json_lists, all_compressed_text
    )
    if not verify_result.get("all_present", False):
        missing = verify_result.get("missing_messages", [])
        lost_names = verify_result.get("lost_names", [])
        if missing:
            LOGGER.error(
                f"[压缩错误] {len(missing)} 条 message 丢失！"
                f"示例：{missing[0][:80] if missing else ''}"
            )
        if lost_names:
            LOGGER.error(
                f"[压缩错误] 丢失角色名：{', '.join(lost_names[:10])}"
            )
        raise RuntimeError("文本压缩完整性校验失败，流水线中止")

    LOGGER.info(
        f"[流水线] 阶段 1 完成：文本压缩完毕，"
        f"压缩后 {len(all_compressed_text)} 字符 "
        f"全部 message 和角色名校验通过"
    )

    # ── 阶段 2：全局提示词生成 ──
    LOGGER.info("[流水线] 阶段 2/6：全局游戏分析")
    _update_runtime(projectConfig, stage="生成全局游戏分析")

    from GalTransl.Backend.ForGlobalPrompt import (
        ForGlobalPrompt,
        load_global_prompt,
        _find_global_prompt_path,
    )
    from GalTransl.DataValidator import validate_global_prompt

    gp_path = _find_global_prompt_path(projectConfig)
    force_regen_gp = projectConfig.getKey(
        "internals.pipeline.forceRegenGlobal", False
    )

    if os.path.exists(gp_path) and not force_regen_gp:
        LOGGER.info("[流水线] 阶段 2 跳过：全局分析已存在")
        success = True
    else:
        gptapi_global = ForGlobalPrompt(
            projectConfig, "ForGlobalPrompt",
            projectConfig.proxyPool, projectConfig.tokenPool,
        )
        external_info = projectConfig.getKey("externals.gameInfo", "") or ""
        success = await gptapi_global.batch_translate(
            compressed_texts, external_info=external_info
        )
        if not success:
            LOGGER.error("[流水线] 全局游戏分析生成失败，流水线中止")
            raise RuntimeError("全局游戏分析生成失败")

    # 校验 GlobalPrompt.json（跳过或重新生成后均需读取，供后续阶段复用）
    global_prompt = load_global_prompt(projectConfig)
    if global_prompt is None:
        LOGGER.error(
            "[流水线] GlobalPrompt.json 校验失败，流水线中止"
        )
        raise RuntimeError("GlobalPrompt.json 不存在或格式错误")

    gp_validation = validate_global_prompt(global_prompt)
    if not gp_validation["valid"]:
        for err in gp_validation["errors"]:
            LOGGER.error(
                f"[流水线] GlobalPrompt 内容校验失败: {err}"
            )
        raise RuntimeError("GlobalPrompt 内容校验失败")
    for warn in gp_validation.get("warnings", []):
        LOGGER.warning(f"[流水线] GlobalPrompt 警告: {warn}")

    # 注入全局提示词到 projectConfig，供后续阶段复用
    projectConfig.global_prompt = global_prompt

    char_count = len(global_prompt.get("角色列表", []))
    LOGGER.info(
        f"[流水线] 阶段 2 完成：全局分析已生成，{char_count} 个角色"
    )

    # ── 阶段 3：术语表构建（GenDic）──
    LOGGER.info("[流水线] 阶段 3/6：术语表构建")
    _update_runtime(projectConfig, stage="构建术语表")

    dict_path = os.path.join(
        projectConfig.getProjectDir(), "项目GPT字典-生成.txt"
    )
    force_regen = projectConfig.getKey(
        "internals.pipeline.forceRegenDic", False
    )

    if os.path.exists(dict_path) and not force_regen:
        LOGGER.info("[流水线] 阶段 3 跳过：术语表已存在")
    else:
        from GalTransl.Backend.GenDic import GenDic

        gptapi_dic = GenDic(
            projectConfig, "GenDic",
            projectConfig.proxyPool, projectConfig.tokenPool,
        )
        all_jsons = []
        for json_list in file_json_lists.values():
            all_jsons.extend(json_list)
        await gptapi_dic.batch_translate(all_jsons)
        LOGGER.info("[流水线] 阶段 3 完成：术语表已生成")

    # ── 阶段 4：文件级元数据生成 ──
    LOGGER.info("[流水线] 阶段 4/6：文件级剧情元数据")
    _update_runtime(projectConfig, stage="生成文件级元数据")

    from GalTransl.Backend.ForFileMetaData import ForFileMetaData
    from GalTransl.Backend.ForGalJsonMulitChat import load_file_metadata_map

    gptapi_filemeta = ForFileMetaData(
        projectConfig, "ForFileMetaData",
        projectConfig.proxyPool, projectConfig.tokenPool,
    )
    # ForFileMetaData 会通过 projectConfig.global_prompt 自动使用全局分析
    # 已存在的文件级元数据映射：用于「已存在则跳过」，避免覆盖用户手改/既有产物
    existing_fm_map = load_file_metadata_map(projectConfig)
    force_regen_fm = projectConfig.getKey(
        "internals.pipeline.forceRegenFileMeta", False
    )
    total_files = len(file_json_lists)
    skipped_files = 0
    for i, (file_path, jsons) in enumerate(file_json_lists.items(), 1):
        fname = os.path.basename(file_path)
        LOGGER.info(f"[FileMetaData] ({i}/{total_files}) {fname}")
        _update_runtime(
            projectConfig, current_file=fname,
            stage=f"文件级元数据 ({i}/{total_files})"
        )
        # 该文件产物已存在且未强制重生成 → 跳过，避免覆盖
        if fname in existing_fm_map and not force_regen_fm:
            LOGGER.info(
                f"[流水线] 阶段 4 跳过：{fname} 文件级元数据已存在"
            )
            skipped_files += 1
            continue
        await gptapi_filemeta.batch_translate(jsons, filename=fname)

    # 交叉验证 FileMetaData 条目数
    fm_map = load_file_metadata_map(projectConfig)
    fm_count = len(fm_map)
    if fm_count < total_files:
        LOGGER.warning(
            f"[流水线] 阶段 4 警告：{fm_count}/{total_files} 个文件"
            f"生成了元数据，缺失 {total_files - fm_count} 个"
        )
    else:
        LOGGER.info(
            f"[流水线] 阶段 4 完成：{fm_count}/{total_files} 个文件"
        )
    if skipped_files:
        LOGGER.info(
            f"[流水线] 阶段 4 跳过 {skipped_files} 个已存在文件级元数据的文件"
        )
    # 同时关闭 ForFileMetaData 后端
    if hasattr(gptapi_filemeta, "shutdown"):
        await gptapi_filemeta.shutdown()

    # ── 阶段 5：批次级元数据生成 ──
    LOGGER.info("[流水线] 阶段 5/6：翻译区间划分")
    _update_runtime(projectConfig, stage="划分翻译区间")

    from GalTransl.Backend.ForBatchMetaData import ForBatchMetaData
    from GalTransl.Backend.ForGalJsonMulitChat import load_batch_metadata_map

    gptapi_batchmeta = ForBatchMetaData(
        projectConfig, "ForBatchMetaData",
        projectConfig.proxyPool, projectConfig.tokenPool,
    )
    # ForBatchMetaData 会写入 transl_cache/pass2_cache/BatchMetadata.json
    # 已存在的批次级元数据映射：用于「已存在则跳过」，避免覆盖用户手改/既有产物
    existing_bm_map = load_batch_metadata_map(projectConfig)
    force_regen_bm = projectConfig.getKey(
        "internals.pipeline.forceRegenBatchMeta", False
    )
    skipped_batches = 0
    for i, (file_path, jsons) in enumerate(file_json_lists.items(), 1):
        fname = os.path.basename(file_path)
        LOGGER.info(f"[BatchMetaData] ({i}/{total_files}) {fname}")
        _update_runtime(
            projectConfig, current_file=fname,
            stage=f"批次划分 ({i}/{total_files})"
        )
        # 该文件产物已存在且未强制重生成 → 跳过，避免覆盖
        if fname in existing_bm_map and not force_regen_bm:
            LOGGER.info(
                f"[流水线] 阶段 5 跳过：{fname} 批次级元数据已存在"
            )
            skipped_batches += 1
            continue
        await gptapi_batchmeta.batch_translate(jsons, filename=fname)

    # 交叉验证 BatchMetadata 条目数
    bm_map = load_batch_metadata_map(projectConfig)
    bm_count = len(bm_map)
    if bm_count < total_files:
        LOGGER.warning(
            f"[流水线] 阶段 5 警告：{bm_count}/{total_files} 个文件"
            f"划分了批次，缺失 {total_files - bm_count} 个"
        )
    else:
        LOGGER.info(
            f"[流水线] 阶段 5 完成：{bm_count}/{total_files} 个文件"
        )
    if skipped_batches:
        LOGGER.info(
            f"[流水线] 阶段 5 跳过 {skipped_batches} 个已存在批次级元数据的文件"
        )
    if hasattr(gptapi_batchmeta, "shutdown"):
        await gptapi_batchmeta.shutdown()

    # ── 阶段 6：翻译（ForGalJsonMulitChat）──
    LOGGER.info("[流水线] 阶段 6/6：翻译执行")
    _update_runtime(projectConfig, stage="翻译执行中")

    # 翻译阶段复用现有的翻译流程：
    # 重新进入 doLLMTranslate 的下半部分逻辑
    # 由于我们已经在 doLLMTranslate 内部，设置标志跳过前处理
    # 直接执行翻译阶段的核心流程
    await _run_translation_phase(
        projectConfig, file_json_lists, file_list
    )

    LOGGER.info("=" * 50)
    LOGGER.info("[流水线] 全部 6 个阶段完成！")
    _update_runtime(projectConfig, stage="流水线完成")


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
    from os.path import join as joinpath, exists as isPathExists, dirname, basename as os_basename

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
    # 兼容 YAML 中写成字符串（如 workersPerProject: '4'）的情况，统一强转为 int
    _workers_raw = projectConfig.getKey("workersPerProject")
    workersPerProject = int(_workers_raw) if _workers_raw is not None else 1

    pre_dic_list = projectConfig.getDictCfgSection()["preDict"]
    post_dic_list = projectConfig.getDictCfgSection()["postDict"]
    gpt_dic_list = projectConfig.getDictCfgSection()["gpt.dict"]
    default_dic_dir = projectConfig.getDictCfgSection()["defaultDictFolder"]

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

    # 初始化 gptapi：流水线翻译阶段固定用 ForGal-json-multi-chat
    saved_translator = projectConfig.select_translator
    projectConfig.select_translator = "ForGal-json-multi-chat"
    try:
        gptapi = await init_gptapi(projectConfig)
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

        async def worker_loop():
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

        worker_tasks = [
            asyncio.create_task(worker_loop())
            for _ in range(worker_count)
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


async def doLLMTranslSingleChunk(
    semaphore: asyncio.Semaphore,
    split_chunk: SplitChunkMetadata,
    projectConfig: CProjectConfig,
    gptapi: Any,  # 添加 gptapi 参数
) -> Tuple[bool, List, List, str, SplitChunkMetadata]:
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
            # 注入文件级元数据（仅支持 set_file_metadata 的后端，如 ForGal-json-multi-chat）
            file_metadata = getattr(projectConfig, "file_metadata", None)
            if file_metadata is not None and hasattr(gptapi, "set_file_metadata"):
                _batch_file_name = file_name + (
                    f"_{file_index}" if total_splits > 1 else ""
                )
                gptapi.set_file_metadata(file_metadata, _batch_file_name)
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

            # 执行校对（如果启用）
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
                split_chunk.get_file_finished_chunks(), projectConfig
            )

        _update_runtime(projectConfig, current_file=file_name)


async def postprocess_results(
    resultChunks: List[SplitChunkMetadata],
    projectConfig: CProjectConfig,
) -> None:
    """单个文件翻译完成后的收尾工作。

    对每个 chunk 逐一：find_problems 标注问题 → save_transCache_to_json(post_save=True)
    写完整 jsonl 快照（这也是唯一一次把 append 日志合并入主快照的时机）。
    随后合并所有 chunk 的结果，套用 name 替换表并经文件插件写出最终译文。
    """

    proj_dir = projectConfig.getProjectDir()
    input_dir = projectConfig.getInputPath()
    output_dir = projectConfig.getOutputPath()
    cache_dir = _pass3_cache_dir(projectConfig)
    eng_type = projectConfig.select_translator
    gpt_dic = projectConfig.gpt_dic
    name_replaceDict = projectConfig.name_replaceDict

    # 对每个分块执行错误检查和缓存保存
    for i, chunk in enumerate(resultChunks):
        trans_list = chunk.trans_list
        file_path = chunk.file_path
        cache_file_path = joinpath(
            cache_dir,
            file_path.replace(input_dir, "").lstrip(os_sep).replace(os_sep, "-}")
            + (f"_{chunk.chunk_index}" if chunk.total_chunks > 1 else ""),
        )

        # 刷新 problem 字段（仅翻译模式；GenDic/dump-name 等不刷新）
        find_problems(trans_list, projectConfig, gpt_dic)
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


async def init_gptapi(
    projectConfig: CProjectConfig,
) -> None:
    """
    根据引擎类型获取相应的API实例（延迟导入后端模块以避免不必要依赖）。

    参数:
    projectConfig: 项目配置对象
    eng_type: 引擎类型
    endpoint: API端点（如果适用）
    proxyPool: 代理池（如果适用）
    tokenPool: Token池

    返回:
    相应的API实例
    """
    proxyPool = projectConfig.proxyPool
    tokenPool = projectConfig.tokenPool
    eng_type = projectConfig.select_translator

    match eng_type:
        case "ForGlobalPrompt":
            from GalTransl.Backend.ForGlobalPrompt import ForGlobalPrompt
            return ForGlobalPrompt(projectConfig, eng_type, proxyPool, tokenPool)
        case "ForGal-json-multi-chat":
            from GalTransl.Backend.ForGalJsonMulitChat import ForGalJsonMulitChat
            return ForGalJsonMulitChat(projectConfig, eng_type, proxyPool, tokenPool)
        case "GenDic":
            from GalTransl.Backend.GenDic import GenDic
            return GenDic(projectConfig, eng_type, proxyPool, tokenPool)
        case "ForFileMetaData":
            from GalTransl.Backend.ForFileMetaData import ForFileMetaData
            return ForFileMetaData(projectConfig, eng_type, proxyPool, tokenPool)
        case "ForBatchMetaData":
            from GalTransl.Backend.ForBatchMetaData import ForBatchMetaData
            return ForBatchMetaData(projectConfig, eng_type, proxyPool, tokenPool)
        case _:
            raise ValueError(f"不支持的翻译引擎类型 {eng_type}")


def fplugins_load_file(file_path: str, fPlugins: list) -> Tuple[List[Dict], Any]:
    """按顺序尝试每个文件插件解析 file_path。

    第一个成功的插件决定解析结果与对应的保存函数 save_func。
    返回 (json_list, save_func)；若所有插件都失败则断言报错。
    """
    result = None
    save_func = None
    for plugin in fPlugins:

        if isinstance(plugin, str):
            LOGGER.warning(f"跳过无效的插件项: {plugin}")
            continue
        try:
            result = plugin.plugin_object.load_file(file_path)
            save_func = plugin.plugin_object.save_file
            break
        except TypeError as e:
            LOGGER.error(
                f"{file_path} 不是文件插件'{getattr(plugin, 'name', 'Unknown')}'支持的格式：{e}"
            )
        except Exception as e:
            LOGGER.error(
                f"插件 {getattr(plugin, 'name', 'Unknown')} 读取文件 {file_path} 出错: {e}"
            )

    assert result is not None, get_text("file_load_failed", GT_LANG, file_path)

    assert isinstance(result, list), f"文件 {file_path} 不是列表"

    return result, save_func
