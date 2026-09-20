"""翻译流水线的运行时支撑（0.4.10 从 LLMTranslate.py 抽出）。

职责：
- 运行时项目目录与状态推送（_runtime_project_dir / _update_runtime）；
- 元数据阶段的 worker 协程池（_run_meta_worker_pool）；
- 阶段级 worker 池与并发自适应（_stage_pool / auto_tune_workers / AdaptiveWorkerState）；
- 模型可用性预检（ensure_model_available_if_needed）；
- 协作式取消检查点（_check_stop_requested）；
- 运行时文件映射与进度标题（_build_runtime_file_maps / _build_meta_file_totals /
  update_progress_title）。
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from os import cpu_count, sep as os_sep
from os.path import basename as os_basename, join as joinpath
from time import time
from typing import Any, Dict, List, Optional, Tuple

from GalTransl import LOGGER, NEED_OpenAITokenPool
from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.CSplitter import SplitChunkMetadata
from GalTransl.server_runtime import WORKER_ID_CTX
from GalTransl.TerminalOutput import should_print_translation_logs




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


def _stage_pool(projectConfig: CProjectConfig, stage: str):
    """返回大阶段独立令牌池（common.stageBackends 配置）；未配置时回退任务主池。"""
    pool = getattr(projectConfig, "stage_token_pools", {}).get(stage)
    return pool if pool is not None else getattr(projectConfig, "tokenPool", None)


async def ensure_model_available_if_needed(
    projectConfig: CProjectConfig, stage: str = ""
) -> None:
    """在真正需要调用模型前，按需执行一次可用性检查。

    stage 非空时检查该大阶段（metadata/translate/afterTrans）的独立令牌池；
    可用性标志挂在池对象上，主池与各阶段池分别检查、互不误跳。
    checkAvailable 开关读池携带的后端配置段（阶段 profile），未配置时回退主配置。
    """
    translator = getattr(projectConfig, "select_translator", "")
    if not any(x in translator for x in NEED_OpenAITokenPool):
        return

    token_pool = _stage_pool(projectConfig, stage) if stage else getattr(projectConfig, "tokenPool", None)
    if token_pool is None:
        return

    check_section = getattr(token_pool, "backend_section", None)
    if not isinstance(check_section, dict):
        check_section = projectConfig.getBackendConfigSection("OpenAI-Compatible")
    if not check_section.get("checkAvailable", True):
        return

    if getattr(token_pool, "_availability_checked", False):
        return

    model_check_lock = getattr(projectConfig, "_model_check_lock", None)
    if model_check_lock is None:
        model_check_lock = asyncio.Lock()
        setattr(projectConfig, "_model_check_lock", model_check_lock)

    async with model_check_lock:
        if getattr(token_pool, "_availability_checked", False):
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
            token_pool._availability_checked = True
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
