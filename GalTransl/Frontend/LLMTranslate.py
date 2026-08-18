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

from GalTransl import LOGGER, NEED_OpenAITokenPool
from GalTransl.i18n import get_text, GT_LANG
from GalTransl.Cache import get_transCache_from_json
from GalTransl.ConfigHelper import initDictList, CProjectConfig
from GalTransl.CSentense import CTransList
from GalTransl.Dictionary import CGptDict, CNormalDic
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


async def _run_meta_worker_pool(
    projectConfig: CProjectConfig,
    gptapi: Any,
    file_json_lists: dict,
    existing_map: dict,
    worker_count: int,
    tag: str,
    stage_prefix: str,
    force_regen: bool = False,
) -> int:
    """多 worker 并发执行文件级/批次级元数据生成。

    与翻译阶段 worker 池同构：队列分发 + 每 worker 绑定 WORKER_ID_CTX，
    使元数据阶段提示词预览同样按 worker 分板块展示。

    Args:
        projectConfig: 项目配置。
        gptapi: ForFileMetaData / ForBatchMetaData 后端实例。
        file_json_lists: {file_path: json_list} 待处理文件映射。
        existing_map: 已有缓存映射，用于跳过已生成元数据的文件。
        worker_count: 并发 worker 数。
        tag: 日志前缀（"FileMetaData" / "BatchMetaData"）。
        stage_prefix: 运行时阶段提示前缀。
        force_regen: 为 True 时忽略已有缓存，强制重新生成。

    Returns:
        实际处理的文件数（跳过缓存的不计）。
    """
    todo = []
    for file_path, jsons in file_json_lists.items():
        fname = os_basename(file_path)
        if fname in existing_map and not force_regen:
            LOGGER.debug(f"[{tag}] 跳过已有缓存: {fname}")
            continue
        todo.append((fname, jsons))

    if not todo:
        LOGGER.info(f"[{tag}] 全部文件已有缓存，无需处理")
        return 0

    queue: asyncio.Queue = asyncio.Queue()
    for item in todo:
        queue.put_nowait(item)
    for _ in range(worker_count):
        queue.put_nowait(None)

    processed = 0
    total_todo = len(todo)

    async def worker_loop(worker_index: int) -> None:
        nonlocal processed
        # 与翻译阶段一致：绑定 worker 身份，提示词预览按此分板块
        worker_token = WORKER_ID_CTX.set(str(worker_index))
        LOGGER.debug(
            f"[{tag}] worker_loop[{worker_index}] 启动, "
            f"WORKER_ID_CTX={WORKER_ID_CTX.get()!r}"
        )
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                fname, jsons = item
                _update_runtime(
                    projectConfig, current_file=fname,
                    stage=f"{stage_prefix} {fname}",
                )
                # 仅成功（返回 True）才计数，LLM 业务失败返回 False 不计入已处理
                ok = await gptapi.batch_translate(
                    jsons, filename=fname, force_regen=force_regen
                )
                if ok:
                    processed += 1
                LOGGER.debug(
                    f"[{tag}] worker_loop[{worker_index}] {fname} 处理完成 "
                    f"ok={ok} ({processed}/{total_todo})"
                )
        finally:
            WORKER_ID_CTX.reset(worker_token)

    tasks = [asyncio.create_task(worker_loop(i)) for i in range(worker_count)]
    try:
        await asyncio.gather(*tasks)
    except Exception:
        # 任一 worker 抛出未捕获异常（写盘失败/构建异常等）：取消其余 worker，
        # 避免孤儿任务继续写盘导致未定义完成状态的残留写入
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return processed


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
            LOGGER.info(
                f"[并发] worker 自适应调档：{current} -> {target} "
                f"(429比例={ratio_429:.2f} 平均延迟={avg_latency:.1f}s)"
            )
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

        # 磁盘缓存文件名：save_transCache_to_json 会对不以 .json 结尾的路径补一次 .json，
        # 故多 chunk 磁盘文件为 file_name_{index}.json，此处补齐后与磁盘命名完全一致
        if not cache_key.endswith(".json"):
            cache_key = f"{cache_key}.json"
        cache_file_display_map[cache_key] = display_name

    return file_totals, cache_file_display_map


def _build_meta_file_totals(file_json_lists: dict, input_dir: str) -> dict[str, int]:
    """为元数据阶段构建 file_totals：{相对显示名: 非空行数}。

    元数据阶段（ForFileMetaData / ForBatchMetaData）不经过切块，无法复用
    _build_runtime_file_maps（其输入是 SplitChunkMetadata 列表）。此函数直接从
    file_json_lists 统计每个输入文件的非空行数，作为前端文件进度面板的进度分母。
    """
    file_totals: dict[str, int] = {}
    for file_path, json_list in file_json_lists.items():
        display_name = file_path.replace(input_dir, "").lstrip(os_sep).replace(os_sep, "/")
        count = 0
        for row in json_list:
            if not isinstance(row, dict):
                continue
            message = str(row.get("message", "") or "").strip()
            if not message:
                continue
            count += 1
        file_totals[display_name] = count
    return file_totals


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
            # 上报兜底后的值：worker 全空闲时也上报 configured（而非 0），
            # 避免前端"并发"指示条误显示 0，也避免 snapshot 依赖 workers_active 时丢失数据
            _update_runtime(
                projectConfig,
                workers_active=projectConfig.active_workers,
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
    # 载入 gt_input 中的 FileMetaData.json
    from GalTransl.Backend.metadata import load_file_metadata
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
        if hasattr(gptapi, "shutdown"):
            await gptapi.shutdown()
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

        # 交叉验证：检查 FileMetaData.json 条目数
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
        if hasattr(gptapi, "shutdown"):
            await gptapi.shutdown()
        return True

    if eng_type == "ForPlotRouteMap":
        # 独立运行剧情路线图生成：基于 FileMetaData 剧情摘要 + 用户大纲/结构类型，
        # 输出 PlotRouteMap.json（mermaid 源码 + 文件→路线归属 + 路线剧情摘要）
        _check_stop_requested(projectConfig)
        await ensure_model_available_if_needed(projectConfig)
        gptapi = await init_gptapi(projectConfig)
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
        if hasattr(gptapi, "shutdown"):
            await gptapi.shutdown()
        _update_runtime(projectConfig, stage="剧情路线图生成完毕")
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

        # 交叉验证：检查 BatchMetadata.json 条目数
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
            # 注入文件级元数据（供首轮修复）
            file_metadata = getattr(projectConfig, "file_metadata", None)
            if file_metadata is not None and hasattr(gptapi, "set_file_metadata"):
                gptapi.set_file_metadata(file_metadata, file_name)
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
            # 注入文件级元数据（供首轮评估）
            file_metadata = getattr(projectConfig, "file_metadata", None)
            if file_metadata is not None and hasattr(gptapi, "set_file_metadata"):
                gptapi.set_file_metadata(file_metadata, file_name)
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
        if hasattr(gptapi, "shutdown"):
            await gptapi.shutdown()
        return True

    # ---- 2.7c 独立引擎：语义差异检测（ForSemCheck）----
    if eng_type == "ForSemCheck":
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
        total = len(file_json_lists)
        worker_count = max(1, projectConfig.get_workers_per_project())
        projectConfig.active_workers = worker_count
        LOGGER.info(
            f"[语义检测] 开始为 {total} 个文件执行语义差异检测，并发 {worker_count} worker"
        )
        _update_runtime(projectConfig, stage="语义差异检测")
        num_better = projectConfig.getKey("gpt.numPerRequestBetter")
        try:
            num_better = int(num_better) if num_better else 100
        except (TypeError, ValueError):
            num_better = 100

        async def _semcheck_single_file(file_path: str, json_list: list) -> None:
            """处理单个文件的语义检测：重建句子、命中缓存、检测并写回 suspected_error。"""
            _check_stop_requested(projectConfig)
            file_name = (
                file_path.replace(input_dir, "")
                .lstrip(os_sep)
                .replace(os_sep, "-}")
            )
            cache_file_path = joinpath(cache_dir, file_name)
            if not isPathExists(cache_file_path):
                LOGGER.warning(f"[语义检测] {file_name} 无缓存译文，跳过")
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
                    f"[语义检测] {file_name} 无有效译文，跳过缓存保存（保留已有缓存）"
                )

        file_queue: asyncio.Queue = asyncio.Queue()
        for file_path, json_list in file_json_lists.items():
            file_queue.put_nowait((file_path, json_list))
        for _ in range(worker_count):
            file_queue.put_nowait(None)

        async def _semcheck_worker_loop(worker_index: int) -> None:
            worker_token = WORKER_ID_CTX.set(str(worker_index))
            LOGGER.debug(
                f"[语义检测] worker_loop[{worker_index}] 启动, "
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
        LOGGER.info("[语义检测] 语义差异检测完成")
        _update_runtime(projectConfig, stage="语义差异检测完成")
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
                    if s and not s.startswith("#"):
                        return True
        except Exception:
            continue
    return False


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

    if not projectConfig.getKey("internals.pipeline.enableValidate", True):
        LOGGER.warning("[流水线] 阶段 0 已禁用（enableValidate=false），跳过输入校验")
        record_runtime_notice(
            projectConfig.getProjectDir(), "阶段 0/6：输入校验已禁用，跳过"
        )
    else:
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
        record_runtime_notice(
            projectConfig.getProjectDir(),
            f"阶段 0/6：输入校验通过（{len(file_list)} 个文件）",
        )

    # ── 阶段 1：文本压缩 ──
    LOGGER.info("[流水线] 阶段 1/6：文本无损压缩")
    _update_runtime(projectConfig, stage="文本无损压缩")

    if not projectConfig.getKey("internals.pipeline.enableCompress", True):
        LOGGER.warning("[流水线] 阶段 1 已禁用（enableCompress=false），跳过文本压缩")
        record_runtime_notice(
            projectConfig.getProjectDir(), "阶段 1/6：文本压缩已禁用，跳过"
        )
        # 置空以便阶段 2 检查：全局分析依赖压缩文本，禁用后阶段 2 自动跳过
        compressed_texts: Dict[str, str] = {}
    else:
        from GalTransl.TextCompressor import TextCompressor

        max_chars = projectConfig.getKey("internals.pipeline.maxInputChars", 950000)
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
        record_runtime_notice(
            projectConfig.getProjectDir(),
            f"阶段 1/6：文本压缩完成（{len(all_compressed_text)} 字符）",
        )

    # ── 阶段 2：全局提示词生成 ──
    LOGGER.info("[流水线] 阶段 2/6：全局游戏分析")
    _update_runtime(projectConfig, stage="生成全局游戏分析")

    if not projectConfig.getKey("internals.pipeline.enableGlobalPrompt", True):
        LOGGER.warning("[流水线] 阶段 2 已禁用（enableGlobalPrompt=false），跳过全局分析")
        record_runtime_notice(
            projectConfig.getProjectDir(), "阶段 2/6：全局分析已禁用，跳过"
        )
        # 后续阶段通过 projectConfig.global_prompt 或缓存惰性读取，缺省时自动退化
        projectConfig.global_prompt = None
    elif not compressed_texts:
        LOGGER.warning("[流水线] 阶段 2 跳过：阶段 1 压缩已禁用，无压缩文本可供全局分析")
        record_runtime_notice(
            projectConfig.getProjectDir(), "阶段 2/6：压缩已禁用，全局分析跳过"
        )
        projectConfig.global_prompt = None
    else:
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
            record_runtime_notice(
                projectConfig.getProjectDir(), "阶段 2/6：全局分析已存在，跳过"
            )
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
            if hasattr(gptapi_global, "shutdown"):
                await gptapi_global.shutdown()
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
        record_runtime_notice(
            projectConfig.getProjectDir(),
            f"阶段 2/6：全局分析完成（{char_count} 个角色）",
        )

    # ── 阶段 3：术语表构建（GenDic）──
    LOGGER.info("[流水线] 阶段 3/6：术语表构建")
    _update_runtime(projectConfig, stage="构建术语表")

    if not projectConfig.getKey("internals.pipeline.enableGenDic", True):
        LOGGER.warning("[流水线] 阶段 3 已禁用（enableGenDic=false），跳过术语表构建")
        record_runtime_notice(
            projectConfig.getProjectDir(), "阶段 3/6：术语表构建已禁用，跳过"
        )
    else:
        force_regen = projectConfig.getKey(
            "internals.pipeline.forceRegenDic", False
        )

        # 跳过条件：项目级 gpt 字典已有非空有效条目（不再只看文件是否存在）
        if _has_nonempty_gpt_dict(projectConfig) and not force_regen:
            LOGGER.info("[流水线] 阶段 3 跳过：术语表已存在（非空）")
            record_runtime_notice(
                projectConfig.getProjectDir(), "阶段 3 跳过：术语表已存在（非空），不重新生成"
            )
        else:
            LOGGER.info("[流水线] 阶段 3：开始生成术语表")
            record_runtime_notice(
                projectConfig.getProjectDir(), "阶段 3：开始生成术语表"
            )
            from GalTransl.Backend.GenDic import GenDic

            gptapi_dic = GenDic(
                projectConfig, "GenDic",
                projectConfig.proxyPool, projectConfig.tokenPool,
            )
            all_jsons = []
            for json_list in file_json_lists.values():
                all_jsons.extend(json_list)
            dic_ok = await gptapi_dic.batch_translate(all_jsons)
            if hasattr(gptapi_dic, "shutdown"):
                await gptapi_dic.shutdown()
            # internals.pipeline.abortOnDicFailure：术语表构建失败（如分词模型加载失败）时中止流水线。
            # batch_translate 仅在硬失败（分词模型无法加载）时返回 False；分片级失败已被记录但
            # 视为部分成功（与流水线容错设计一致），不中止，避免误伤"文本无可提取词条"的合法场景。
            if not dic_ok:
                abort = projectConfig.getKey("internals.pipeline.abortOnDicFailure", False)
                if abort:
                    LOGGER.error("[流水线] 阶段 3：术语表生成失败，按 abortOnDicFailure 配置中止流水线")
                    raise RuntimeError(
                        "术语表生成失败（分词模型加载失败），已按 abortOnDicFailure=true 中止流水线"
                    )
                LOGGER.warning("[流水线] 阶段 3：术语表生成失败，abortOnDicFailure=false 继续流水线")
            else:
                LOGGER.info("[流水线] 阶段 3 完成：术语表已生成")

    # ── 阶段 4：文件级元数据生成 ──
    LOGGER.info("[流水线] 阶段 4/6：文件级剧情元数据")
    _update_runtime(projectConfig, stage="生成文件级元数据")
    # 上报输入文件总行数：使前端文件进度面板在元数据阶段显示输入文件而非缓存文件
    _update_runtime(
        projectConfig,
        file_totals=_build_meta_file_totals(
            file_json_lists, projectConfig.getInputPath()
        ),
    )
    # 总文件数在阶段 4/5 共用，先于开关判断定义
    total_files = len(file_json_lists)

    if not projectConfig.getKey("internals.pipeline.enableFileMeta", True):
        LOGGER.warning("[流水线] 阶段 4 已禁用（enableFileMeta=false），跳过文件级元数据")
        record_runtime_notice(
            projectConfig.getProjectDir(), "阶段 4/6：文件级元数据已禁用，跳过"
        )
    else:
        from GalTransl.Backend.ForFileMetaData import ForFileMetaData
        from GalTransl.Backend.metadata import load_file_metadata_map

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
        # 多 worker 并发生成文件级元数据（绑定 WORKER_ID_CTX，提示词预览按 worker 分板块）
        # workersPerProject 解析统一走 CProjectConfig.get_workers_per_project（兼容字符串/非法回退 1）
        workers_per_project = projectConfig.get_workers_per_project()
        worker_count = max(1, workers_per_project)
        processed_fm = await _run_meta_worker_pool(
            projectConfig, gptapi_filemeta, file_json_lists,
            existing_map=existing_fm_map,
            worker_count=worker_count,
            tag="FileMetaData", stage_prefix="文件级元数据",
            force_regen=force_regen_fm,
        )
        skipped_files = total_files - processed_fm

        # 交叉验证 FileMetaData 条目数
        fm_map = load_file_metadata_map(projectConfig)
        fm_count = len(fm_map)
        if fm_count < total_files:
            LOGGER.warning(
                f"[流水线] 阶段 4 警告：{fm_count}/{total_files} 个文件"
                f"生成了元数据，缺失 {total_files - fm_count} 个"
            )
            record_runtime_notice(
                projectConfig.getProjectDir(),
                f"阶段 4/6 警告：{total_files - fm_count} 个文件未生成文件级元数据",
            )
        else:
            LOGGER.info(
                f"[流水线] 阶段 4 完成：{fm_count}/{total_files} 个文件"
            )
            record_runtime_notice(
                projectConfig.getProjectDir(),
                f"阶段 4/6：文件级元数据完成（{fm_count}/{total_files} 个文件）",
            )
        if skipped_files:
            LOGGER.info(
                f"[流水线] 阶段 4 跳过 {skipped_files} 个已存在文件级元数据的文件"
            )
        # 同时关闭 ForFileMetaData 后端
        if hasattr(gptapi_filemeta, "shutdown"):
            await gptapi_filemeta.shutdown()

    # ── 阶段 4.5：剧情路线图生成 ──
    LOGGER.info("[流水线] 阶段 4.5/6：剧情路线图")
    _update_runtime(projectConfig, stage="生成剧情路线图")
    if not projectConfig.getKey("internals.pipeline.enablePlotRoute", True):
        LOGGER.warning("[流水线] 阶段 4.5 已禁用（enablePlotRoute=false），跳过剧情路线图")
        record_runtime_notice(
            projectConfig.getProjectDir(), "阶段 4.5/6：剧情路线图已禁用，跳过"
        )
    elif not projectConfig.getKey("internals.pipeline.enableFileMeta", True):
        # 依赖文件级元数据：阶段 4 被禁用时自动跳过（无剧情摘要可输入）
        LOGGER.warning("[流水线] 阶段 4.5 跳过：阶段 4（文件级元数据）已禁用，无剧情摘要可输入")
        record_runtime_notice(
            projectConfig.getProjectDir(),
            "阶段 4.5/6：文件级元数据已禁用，剧情路线图自动跳过",
        )
    else:
        from GalTransl.Backend.ForPlotRouteMap import ForPlotRouteMap, load_plot_route_map

        force_regen_pr = projectConfig.getKey(
            "internals.pipeline.forceRegenPlotRoute", False
        )
        if load_plot_route_map(projectConfig) and not force_regen_pr:
            LOGGER.info(
                "[流水线] 阶段 4.5 跳过：PlotRouteMap.json 已存在"
                "（如需重新生成请启用 forceRegenPlotRoute）"
            )
            record_runtime_notice(
                projectConfig.getProjectDir(),
                "阶段 4.5/6：剧情路线图已存在，跳过（如需重新生成请启用 forceRegenPlotRoute）",
            )
        else:
            gptapi_plotroute = ForPlotRouteMap(
                projectConfig, "ForPlotRouteMap",
                projectConfig.proxyPool, projectConfig.tokenPool,
            )
            structure_type = projectConfig.getKey("internals.plotroute.structureType", "树")
            user_outline = projectConfig.getKey("internals.plotroute.userOutline", "")
            ok = await gptapi_plotroute.batch_translate(
                structure_type=structure_type,
                user_outline=user_outline,
                force_regen=force_regen_pr,
            )
            if not ok:
                LOGGER.warning("[流水线] 阶段 4.5 生成失败或未生成，继续流水线")
            if hasattr(gptapi_plotroute, "shutdown"):
                await gptapi_plotroute.shutdown()

    # ── 阶段 5：批次级元数据生成 ──
    LOGGER.info("[流水线] 阶段 5/6：翻译区间划分")
    _update_runtime(projectConfig, stage="划分翻译区间")
    # 上报输入文件总行数：使前端文件进度面板在元数据阶段显示输入文件而非缓存文件
    _update_runtime(
        projectConfig,
        file_totals=_build_meta_file_totals(
            file_json_lists, projectConfig.getInputPath()
        ),
    )

    if not projectConfig.getKey("internals.pipeline.enableBatchMeta", True):
        LOGGER.warning("[流水线] 阶段 5 已禁用（enableBatchMeta=false），跳过批次级元数据")
        record_runtime_notice(
            projectConfig.getProjectDir(), "阶段 5/6：批次级元数据已禁用，跳过"
        )
    else:
        from GalTransl.Backend.ForBatchMetaData import ForBatchMetaData
        from GalTransl.Backend.metadata import load_batch_metadata_map

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
        # 多 worker 并发划分翻译区间（绑定 WORKER_ID_CTX，提示词预览按 worker 分板块）
        # workersPerProject 解析统一走 CProjectConfig.get_workers_per_project（兼容字符串/非法回退 1）
        workers_per_project = projectConfig.get_workers_per_project()
        worker_count = max(1, workers_per_project)
        processed_bm = await _run_meta_worker_pool(
            projectConfig, gptapi_batchmeta, file_json_lists,
            existing_map=existing_bm_map,
            worker_count=worker_count,
            tag="BatchMetaData", stage_prefix="批次划分",
            force_regen=force_regen_bm,
        )
        skipped_batches = total_files - processed_bm

        # 交叉验证 BatchMetadata 条目数
        bm_map = load_batch_metadata_map(projectConfig)
        bm_count = len(bm_map)
        if bm_count < total_files:
            LOGGER.warning(
                f"[流水线] 阶段 5 警告：{bm_count}/{total_files} 个文件"
                f"划分了批次，缺失 {total_files - bm_count} 个"
            )
            record_runtime_notice(
                projectConfig.getProjectDir(),
                f"阶段 5/6 警告：{total_files - bm_count} 个文件未划分翻译区间",
            )
        else:
            LOGGER.info(
                f"[流水线] 阶段 5 完成：{bm_count}/{total_files} 个文件"
            )
            record_runtime_notice(
                projectConfig.getProjectDir(),
                f"阶段 5/6：翻译区间划分完成（{bm_count}/{total_files} 个文件）",
            )
        if skipped_batches:
            LOGGER.info(
                f"[流水线] 阶段 5 跳过 {skipped_batches} 个已存在批次级元数据的文件"
            )
        if hasattr(gptapi_batchmeta, "shutdown"):
            await gptapi_batchmeta.shutdown()

    # ── 阶段 6：翻译（ForGalJsonMulitChat）──
    LOGGER.info("[流水线] 阶段 6/6：翻译执行")
    record_runtime_notice(projectConfig.getProjectDir(), "阶段 6/6：开始翻译")
    _update_runtime(projectConfig, stage="翻译执行中")

    if not projectConfig.getKey("internals.pipeline.enableTranslate", True):
        LOGGER.warning("[流水线] 阶段 6 已禁用（enableTranslate=false），跳过翻译执行")
        record_runtime_notice(
            projectConfig.getProjectDir(), "阶段 6/6：翻译执行已禁用，跳过"
        )
    else:
        # 翻译阶段复用现有的翻译流程：
        # 重新进入 doLLMTranslate 的下半部分逻辑
        # 由于我们已经在 doLLMTranslate 内部，设置标志跳过前处理
        # 直接执行翻译阶段的核心流程
        await _run_translation_phase(
            projectConfig, file_json_lists, file_list
        )

    LOGGER.info("=" * 50)
    LOGGER.info("[流水线] 全部 6 个阶段完成！")
    record_runtime_notice(projectConfig.getProjectDir(), "流水线完成：全部 6 个阶段执行完毕")
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
            # 注入文件级元数据（仅支持 set_file_metadata 的后端，如 ForGal-json-multi-chat）
            file_metadata = getattr(projectConfig, "file_metadata", None)
            if file_metadata is not None and hasattr(gptapi, "set_file_metadata"):
                # 待废弃：_file_index 后缀桶键由 splitter 分块驱动，未来随 BatchMetadata 语义段移除
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


async def postprocess_results(
    resultChunks: List[SplitChunkMetadata],
    projectConfig: CProjectConfig,
    gptapi: Any = None,
) -> None:
    """单个文件翻译完成后的收尾工作。

    对每个 chunk 逐一：find_problems 标注问题 → save_transCache_to_json(post_save=True)
    写完整 jsonl 快照（这也是唯一一次把 append 日志合并入主快照的时机）。
    随后合并所有 chunk 的结果，套用 name 替换表并经文件插件写出最终译文。

    若 gpt.afterTranslation 配置为 improve/brfix（或组合 improve+brfix），会在保存
    快照前先执行对应后处理后端（独立实例、逐文件），把模型给出的备选译文写入各句
    alt_dst，随快照一并落盘。none 则跳过。
    """

    proj_dir = projectConfig.getProjectDir()
    input_dir = projectConfig.getInputPath()
    output_dir = projectConfig.getOutputPath()
    cache_dir = _pass3_cache_dir(projectConfig)
    eng_type = projectConfig.select_translator
    gpt_dic = projectConfig.gpt_dic
    name_replaceDict = projectConfig.name_replaceDict

    # 后处理阶段（替代原"向多轮对话追加改进轮"）：整文件翻译+校对完成后，
    # 按 gpt.afterTranslation 配置逐文件调度改进轮/换行修复后端（none 跳过）。
    # 放在保存循环之前，使备选译文随 post_save 快照一并落盘。
    _after_mode = _resolve_after_translation_mode(projectConfig)
    if _after_mode != "none":
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
        # 组合 improve+brfix 按序执行，brfix 最后定稿 alt_dst
        for _m in _after_mode.split("+"):
            _update_runtime(projectConfig, stage=f"后处理-{_m}")
            LOGGER.info(
                f"[后处理] 开始：{_m}，文件={_orig_name}"
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
                LOGGER.info(f"[后处理] 完成：{_m}，文件={_orig_name}")
            except Exception as e:
                from GalTransl.Service import JobCancelledError

                if isinstance(e, JobCancelledError):
                    raise
                LOGGER.warning(
                    f"[后处理/{_m}] {resultChunks[0].file_path} 执行失败，已跳过：{e}"
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


def _resolve_after_translation_mode(projectConfig: CProjectConfig) -> str:
    """解析流水线翻译后处理后端配置。

    读取 gpt.afterTranslation（none/improve/brfix/improve+brfix 组合）。
    缺省时回退 gpt.enableBetterTranslation（true→improve）以兼容旧项目配置。
    """
    mode = projectConfig.getKey("gpt.afterTranslation")
    if not mode:
        # 旧项目兼容：enableBetterTranslation 已废弃，true 等价于 improve
        if projectConfig.getKey("gpt.enableBetterTranslation"):
            LOGGER.debug(
                "[后处理] gpt.afterTranslation 缺省，回退 enableBetterTranslation=true→improve"
            )
            return "improve"
        return "none"
    mode = str(mode).strip().lower()
    # 仅保留白名单内 token，过滤非法配置
    allowed = {"none", "improve", "brfix", "jpfix", "banfix", "semcheck"}
    parts = [p for p in mode.split("+") if p in allowed]
    if not parts:
        LOGGER.warning(f"[后处理] gpt.afterTranslation 非法值 '{mode}'，回退 none")
        return "none"
    return "+".join(parts)


async def _run_after_trans_single_file(
    mode: str,
    orig_name: str,
    file_path: str,
    merged_trans: list,
    projectConfig: CProjectConfig,
    num_better: int,
) -> None:
    """对单个文件执行一种后处理后端（improve 改进轮 / brfix 换行修复）。

    直接实例化对应后端类（复用 projectConfig 已载入的 proxyPool/tokenPool/
    pre_dic/post_dic/gpt_dic/file_metadata，不重新 initDictList、不调
    ensure_model_available、不碰 select_translator）。用完 shutdown 释放连接。
    异常 caller 负责捕获：JobCancelledError 上抛，其余由 caller 记录。
    """
    # JobCancelledError 必须在函数内 import：GalTransl.Service 会反向 import 本模块
    # （Service→Runner→LLMTranslate），模块级 import 会触发循环依赖。
    from GalTransl.Service import JobCancelledError
    from GalTransl.Backend.ForImproveTranslation import ForImproveTranslation
    from GalTransl.Backend.ForBRStation import ForBRStation
    from GalTransl.Backend.ForJPResidue import ForJPResidue
    from GalTransl.Backend.ForBanWordFix import ForBanWordFix
    from GalTransl.Backend.ForSemCheck import ForSemCheck

    _api = None
    try:
        if mode == "improve":
            _api = ForImproveTranslation(
                projectConfig,
                "ForImproveTranslation",
                projectConfig.proxyPool,
                projectConfig.tokenPool,
            )
        elif mode == "brfix":
            _api = ForBRStation(
                projectConfig,
                "ForBRStation",
                projectConfig.proxyPool,
                projectConfig.tokenPool,
            )
        elif mode == "jpfix":
            _api = ForJPResidue(
                projectConfig,
                "ForJPResidue",
                projectConfig.proxyPool,
                projectConfig.tokenPool,
            )
        elif mode == "banfix":
            _api = ForBanWordFix(
                projectConfig,
                "ForBanWordFix",
                projectConfig.proxyPool,
                projectConfig.tokenPool,
            )
        elif mode == "semcheck":
            _api = ForSemCheck(
                projectConfig,
                "ForSemCheck",
                projectConfig.proxyPool,
                projectConfig.tokenPool,
            )
        else:
            LOGGER.warning(f"[后处理] 未知模式 '{mode}'，跳过")
            return
        # 注入文件级元数据（与翻译轮一致）
        _fm = getattr(projectConfig, "file_metadata", None)
        if _fm is not None and hasattr(_api, "set_file_metadata"):
            _api.set_file_metadata(_fm, orig_name)
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


async def init_gptapi(
    projectConfig: CProjectConfig,
) -> "BaseEngine":
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

    import importlib

    from GalTransl.Backend.BaseEngine import ENGINE_MODULE_PATHS, ENGINE_REGISTRY

    module_path = ENGINE_MODULE_PATHS.get(eng_type)
    if module_path is None:
        raise ValueError(f"不支持的翻译引擎类型 {eng_type}")
    # 惰性加载目标模块：装饰器在 import 期把「name -> 构造工厂」填入 ENGINE_REGISTRY
    importlib.import_module(module_path)

    factory = ENGINE_REGISTRY.get(eng_type)
    if factory is None:
        raise ValueError(f"引擎 {eng_type} 未注册构造工厂")
    return factory(projectConfig, eng_type, proxyPool, tokenPool)


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
