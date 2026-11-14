from __future__ import annotations

import json
import os
import re
import threading
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections import deque
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from packaging.version import InvalidVersion, Version
from yaml import safe_load

from GalTransl import (
    CACHE_FOLDERNAME,
    INPUT_FOLDERNAME,
    LOGGER,
    PASS1_CACHE_DIR,
    PASS2_CACHE_DIR,
    PASS3_CACHE_DIR,
)
from GalTransl.Utils import get_file_list

def _utcnow_text() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _parse_utc_text_timestamp(value: str) -> float:
    """解析 _utcnow_text 生成的时间文本为 epoch 秒；非法返回 0.0。"""
    try:
        dt = datetime.fromisoformat(str(value).rstrip("Z"))
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


def _normalize_version_text(value: str) -> str:
    return value.strip().removeprefix("v").removeprefix("V")


def _has_newer_release(current_version: str, latest_version: str | None) -> bool:
    if not latest_version:
        return False

    current_text = _normalize_version_text(current_version)
    latest_text = _normalize_version_text(latest_version)
    try:
        return Version(latest_text) > Version(current_text)
    except InvalidVersion:
        return latest_text != current_text


def _normalize_project_dir(project_dir: str) -> str:
    return str(Path(project_dir).resolve())


def _read_cache_index_span(path: str) -> tuple[int, int]:
    """读缓存分片条目 index 的最小/最大值；读不出时返回 (-1, -1)（不参与按序号命中）。

    取值口径与 server_cache._is_numeric_index 一致（int 或纯数字字符串），
    额外排除 bool（bool 是 int 子类，会被误当成 0/1）。
    """
    import orjson

    try:
        with open(path, "rb") as f:
            data = orjson.loads(f.read())
    except Exception:
        return -1, -1
    if not isinstance(data, list):
        return -1, -1
    values = []
    for row in data:
        if not isinstance(row, dict):
            continue
        raw = row.get("index")
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            values.append(raw)
        elif isinstance(raw, str) and raw.isdigit():
            values.append(int(raw))
    if not values:
        return -1, -1
    return min(values), max(values)


def _collect_shard_spans(
    cache_dirs: list[tuple[str, str]], stem: str
) -> list[tuple[int, int, str]]:
    """收集 ``<stem>_<N>.json`` 分片及其 index 区间。

    按 cache_dirs 顺序分组、组内按分片序号（数字）升序，整体不保证全局有序。
    返回 ``[(lo, hi, 缓存相对路径), ...]``；区间读不出的分片记为 (-1, -1)，
    仍可作为最后兜底目标。cache_dirs 为 ``[(目录, 相对路径前缀), ...]``，
    前缀用于拼出与 ``/cache/<rel>`` 一致的口径（pass3_cache/ 或空）。
    """
    spans: list[tuple[int, int, str]] = []
    pattern = f"{stem}_"
    for directory, prefix in cache_dirs:
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        shards: list[tuple[int, str]] = []
        for name in names:
            if not name.startswith(pattern) or not name.endswith(".json"):
                continue
            suffix = name[len(pattern) : -len(".json")]
            if suffix.isdecimal():
                shards.append((int(suffix), name))
        for _num, name in sorted(shards):
            low, high = _read_cache_index_span(os.path.join(directory, name))
            spans.append((low, high, f"{prefix}{name}"))
    return spans


def _resolve_fix_report_cache_path(
    project_dir: str,
    filename: str,
    index: int | None = None,
    spans_memo: dict[str, list[tuple[int, int, str]]] | None = None,
) -> str:
    """把修复报告里的流水线文件名解析为缓存相对路径（供前端跳转校对页）。

    修复轮记录的是输入相对名（子目录以 ``-}`` 连接，如 ``a-}b.txt``），而校对页
    的 activeFilePath 要求「pass3_cache/<缓存文件名>」。候选依次为：原样 / ``-}``
    形式 / 两者的 ±``.json``（去重后先查 pass3_cache、再查旧布局的 transl_cache 根）。
    全部落空时按分片缓存兜底：大文件切成 ``<file_name>_<N>.json``（file_name 本身
    可能已带 ``.json``），此时按条目的 index 选分片。注意分片序号只在输入带显式
    index 时是全局行号，否则每片从 1 重计、区间会重叠，可能仍落在首片——属切分
    缓存的既有口径限制。解析不到返回空串，调用方回退原始文件名。

    spans_memo 传入可变 dict 可跨调用复用分片区间探测结果（每次仍要 listdir）。
    """
    name = str(filename or "").strip().replace("\\", "/")
    if not name:
        return ""
    parts = [part for part in name.split("/") if part not in ("", ".")]
    # 报告文件可被手工编辑：绝对路径/目录穿越一律不解析（与 /cache/ 端点同口径）
    if not parts or name.startswith("/") or ":" in parts[0] or ".." in parts:
        return ""
    cache_root = os.path.join(project_dir, CACHE_FOLDERNAME)
    pass3_dir = os.path.join(cache_root, PASS3_CACHE_DIR)
    name = "/".join(parts)
    base = name.replace("/", "-}")
    candidates = dict.fromkeys(
        [name, base]
        + [
            value[:-5] if value.endswith(".json") else f"{value}.json"
            for value in (name, base)
        ]
    )
    for candidate in candidates:
        if candidate and os.path.isfile(os.path.join(pass3_dir, candidate)):
            return f"{PASS3_CACHE_DIR}/{candidate}"
    for candidate in candidates:
        if candidate and os.path.isfile(os.path.join(cache_root, candidate)):
            return candidate
    cache_dirs = [(pass3_dir, f"{PASS3_CACHE_DIR}/"), (cache_root, "")]
    # 分片名 = <file_name>_<N>.json，file_name 可能已带 .json（如 x.txt.json_0.json）
    spans = None if spans_memo is None else spans_memo.get(name)
    if spans is None:
        spans = _collect_shard_spans(cache_dirs, base)
        if spans_memo is not None:
            spans_memo[name] = spans
    if not spans and base.endswith(".json") and len(base) > len(".json"):
        # 备用 stem（剥掉 .json）可能命中另一个输入文件的缓存（如 x.txt_0.json），
        # 故只采纳 index 确实落在其分片区间内的结果，宁可返回空让前端回退原始名
        alt = _collect_shard_spans(cache_dirs, base[:-5])
        if index is not None:
            spans = [item for item in alt if item[0] <= index <= item[1]]
    if not spans:
        return ""
    if index is not None:
        for low, high, rel in spans:
            if low <= index <= high:
                return rel
    return spans[0][2]


def backfill_fix_report_cache_paths(project_dir: str, report: Dict[str, Any]) -> Dict[str, Any]:
    """给旧版持久化报告就地补齐 cache_path（旧报告没有该字段，导致点击跳不动）。

    仅补缺失字段，解析不到保持空（前端回退原始文件名）；返回同一 dict。
    """
    groups = report.get("files")
    if not isinstance(groups, list):
        return report
    memo: dict[tuple[str, int | None], str] = {}
    spans_memo: dict[str, list[tuple[int, int, str]]] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        entries = group.get("entries")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("cache_path"):
                continue
            name = str(entry.get("filename") or group.get("filename") or "")
            index = entry.get("index")
            key = (name, index if isinstance(index, int) else None)
            if key not in memo:
                memo[key] = _resolve_fix_report_cache_path(
                    project_dir, name, key[1], spans_memo
                )
            if memo[key]:
                entry["cache_path"] = memo[key]
    return report


class _ConcurrentLimitError(ValueError):
    """Raised when the global concurrent job limit has been reached."""
    pass


@dataclass(slots=True)
class RuntimeSentenceEvent:
    id: str
    ts: str
    filename: str
    index: int
    speaker: str | list[str] | None
    source_preview: str
    translation_preview: str
    trans_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RuntimeErrorEvent:
    id: str
    ts: str
    kind: str
    level: str
    message: str
    filename: str = ""
    index_range: str = ""
    retry_count: int | None = None
    model: str = ""
    sleep_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# 当前执行上下文所属的翻译 worker 标识（-1 表示非 worker 上下文，如元数据生成阶段）
WORKER_ID_CTX: ContextVar[str] = ContextVar("galtransl_worker_id", default="-1")


@dataclass(slots=True)
class WorkerPromptSnapshot:
    """单个 worker 当前提示词的实时快照。"""
    worker_id: str
    preview: str = ""
    filename: str = ""
    batch: str = ""
    updated_at: str = ""


class TTFTStatus:
    """流式首字状态灯的取值（字符串常量，便于前端字面对比）。"""
    IDLE = "IDLE"            # 空闲：当前 worker 无进行中的请求
    WAITING = "WAITING"      # 已发起请求，尚未收到首个正文分片
    FIRST_TOKEN = "FIRST_TOKEN"  # 已收到首字（正文首片到达）
    RETRYING = "RETRYING"    # 限流/异常触发重试中
    CANCELLED = "CANCELLED"  # 任务整体取消
    COMPLETED = "COMPLETED"  # 上一次请求成功完成（ttft_ms/model 保留供前端展示「已完成+耗时」）


@dataclass(slots=True)
class TTFTPreview:
    """单个 worker 当前请求的流式首字状态与首字响应时间。"""
    worker_id: str
    status: str = TTFTStatus.IDLE
    ttft_ms: float | None = None  # 首字响应时间（毫秒），未收到或不可测为 None
    model: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


RUNTIME_RECENT_EVENT_LIMIT = 80
RUNTIME_PER_FILE_SUCCESS_LIMIT = 100

# 修复/改进报告明细上限：entries 超限截断，count 保持精确（前端提示"仅显示前 N 条"）
FIX_REPORT_PER_FILE_LIMIT = 200
FIX_REPORT_TOTAL_LIMIT = 5000
# Upper bound on success events per snapshot (500): each file keeps a 100-slot deque,
# returning all would explode HTTP payload; 500 covers UI budget + per-file filter
RUNTIME_SNAPSHOT_SUCCESS_LIMIT = 500


# 9 阶段流水线——供前端"流程完成情况"展示用。
# 顺序与 _run_full_pipeline 的阶段执行顺序（pipeline_stages.PIPELINE_STAGES）一致。
PIPELINE_STAGE_NAMES: list[str] = [
    "输入数据校验",
    "文本无损压缩",
    "构建术语表",
    "生成文件级元数据",
    "生成剧情路线图",
    "生成全局游戏分析",
    "划分翻译区间",
    "翻译执行中",
    "AI初步处理",
]
PIPELINE_STAGE_TOTAL: int = len(PIPELINE_STAGE_NAMES)


def _compute_stage_index(stage: str) -> int:
    """返回 stage 字段对应的流水线阶段索引（0-8）；-1 表示不匹配。"""
    if not stage:
        return -1
    # 子阶段如 "文件级元数据 (1/5)" 通过前缀或包含匹配映射到对应主阶段
    for idx, name in enumerate(PIPELINE_STAGE_NAMES):
        if stage.startswith(name) or name in stage:
            return idx
    # 别名：独立引擎运行态（路线分析/全局分析汇总）→ 全局分析阶段
    if "路线分析" in stage or "全局分析汇总" in stage:
        return PIPELINE_STAGE_NAMES.index("生成全局游戏分析")
    # 别名：旧版阶段7显示名 / 独立改进轮运行态 → 末阶段
    if "译文质量改进" in stage or "后处理-" in stage:
        return PIPELINE_STAGE_TOTAL - 1
    # 兜底："完整流水线启动" → 阶段 0，"流水线完成" → 末阶段，"检查模型可用性" → 阶段 0
    if "启动" in stage or "模型可用性" in stage:
        return 0
    if "完成" in stage:
        return PIPELINE_STAGE_TOTAL - 1
    return -1


@dataclass(slots=True)
class RuntimeState:
    project_dir: str
    workers_active: int = 0
    workers_configured: int = 0
    stage: str = ""
    stage_index: int = -1
    stage_total: int = PIPELINE_STAGE_TOTAL
    current_file: str = ""
    current_batch: int = 0
    batch_total: int = 0
    latest_prompt_preview: str = ""
    # 按 worker_id 隔离的最新译文预览快照（多 worker 并发时各占一个 key，覆盖式）
    translation_previews: dict[str, str] = field(default_factory=dict)
    # 最后一次写入是「说明性预览」（0 命中/0 采纳）的 key：真实结果不被说明覆盖
    preview_note_keys: set[str] = field(default_factory=set)
    # 按 worker_id 隔离的提示词快照（多 worker 并发时各占一个 key）
    prompt_previews: dict[str, WorkerPromptSnapshot] = field(default_factory=dict)
    # 按 worker_id 隔离的流式首字状态灯（多 worker 并发时各占一个 key，覆盖式）
    ttft_states: dict[str, TTFTPreview] = field(default_factory=dict)
    updated_at: str = field(default_factory=_utcnow_text)
    file_totals: dict[str, int] = field(default_factory=dict)
    cache_file_display_map: dict[str, str] = field(default_factory=dict)
    # 元数据阶段的行数阈值（internals.pipeline.metaMinLines）：供文件进度面板把
    # 行数不足而被跳过元数据的文件判为「已跳过」而非永远未完成（0=不跳过）
    meta_min_lines: int = 0
    # 进度是否已封口：reset 后若沿用旧 file_totals 则为 False（重建中），收到新 file_totals 后置 True
    progress_sealed: bool = True
    # Per-file deque of recent success events (newest-first, appendleft for O(1) merging)
    recent_successes_by_file: dict[str, deque[RuntimeSentenceEvent]] = field(default_factory=dict)
    recent_errors: deque[RuntimeErrorEvent] = field(default_factory=lambda: deque(maxlen=RUNTIME_RECENT_EVENT_LIMIT))
    # 一次性用户提示队列（前端轮询到即 toast 并清除），用于流水线阶段告知
    notices: deque[str] = field(default_factory=lambda: deque(maxlen=20))
    success_timestamps: deque[float] = field(default_factory=deque)
    # 本次任务修复/改进报告：reset_project 时随 RuntimeState 一并清零（"本次"语义）。
    # fix_report_files[filename] = {"count": int, "entries": list[dict], "truncated": bool}
    fix_report_label: str = ""
    fix_report_started_at: str = ""
    fix_report_finished_at: str = ""
    fix_report_total: int = 0
    fix_report_truncated: bool = False
    fix_report_files: dict[str, dict] = field(default_factory=dict)
    # 修复报告跳转用缓存路径：(文件名, 序号) -> 缓存相对路径；分片区间按文件名记忆
    fix_report_cache_paths: dict[tuple[str, int | None], str] = field(default_factory=dict)
    fix_report_shard_spans: dict[str, list[tuple[int, int, str]]] = field(default_factory=dict)


class RuntimeRegistry:
    _MAX_STATES = 50  # 保留的项目状态数量上限
    _STATE_TTL_SECONDS = 24 * 3600  # 超过上限时，仅清理 updated_at 早于该时长的状态

    def __init__(self) -> None:
        self._states: dict[str, RuntimeState] = {}
        self._lock = threading.Lock()

    def _prune_stale_states_locked(self) -> None:
        """超过数量上限时清理长时间未更新的项目状态（持锁调用，只删 >24h 未更新的）。"""
        if len(self._states) <= self._MAX_STATES:
            return
        now_ts = datetime.utcnow().timestamp()
        stale = [
            (name, st)
            for name, st in self._states.items()
            if (ts := _parse_utc_text_timestamp(st.updated_at)) != 0.0
            and now_ts - ts > self._STATE_TTL_SECONDS
        ]
        stale.sort(key=lambda kv: kv[1].updated_at)
        removed = 0
        for name, _ in stale:
            if len(self._states) <= self._MAX_STATES:
                break
            self._states.pop(name, None)
            removed += 1
        if removed:
            LOGGER.debug(f"[runtime] 清理过期项目状态: {removed} 个")

    def ensure_project(self, project_dir: str) -> RuntimeState:
        normalized = _normalize_project_dir(project_dir)
        with self._lock:
            self._prune_stale_states_locked()
            state = self._states.get(normalized)
            if state is None:
                state = RuntimeState(project_dir=project_dir)
                self._states[normalized] = state
            else:
                state.project_dir = project_dir
            state.updated_at = _utcnow_text()
            return state

    def reset_project(self, project_dir: str) -> None:
        with self._lock:
            normalized = _normalize_project_dir(project_dir)
            previous = self._states.get(normalized)
            if previous is not None:
                # 沿用上一轮的 file_totals / display_map / meta_min_lines，
                # 避免轮询窗口内 total=0 致进度条消失、阈值丢失致阶段绿灯判定回退
                state = RuntimeState(
                    project_dir=project_dir,
                    file_totals=previous.file_totals,
                    cache_file_display_map=previous.cache_file_display_map,
                    meta_min_lines=previous.meta_min_lines,
                )
                state.progress_sealed = False
                # 仅测试用，测完删除
                LOGGER.debug(f"[runtime] reset_project 沿用旧 file_totals 共 {len(previous.file_totals)} 个文件，progress_sealed=False")
            else:
                state = RuntimeState(project_dir=project_dir)
            self._states[normalized] = state

    def reset_previews(self, project_dir: str) -> None:
        """清空预览类快照，仅保留进度相关数据（仅阶段切换/任务开始时调用）。

        上一阶段的 worker 板块若残留，会遮蔽本阶段写入的公共 key "-1"，并使 worker
        tab 显示陈旧内容。file_totals / progress_sealed / meta_min_lines 不动；
        worker 空闲瞬间不得调用本方法，否则前端 tab 会闪断。
        """
        with self._lock:
            state = self._states.get(_normalize_project_dir(project_dir))
            if state is None:
                return
            state.translation_previews.clear()
            state.preview_note_keys.clear()
            state.prompt_previews.clear()
            state.ttft_states.clear()
            state.latest_prompt_preview = ""
            state.updated_at = _utcnow_text()
            LOGGER.debug(
                f"[runtime] reset_previews 清空预览快照: stage={state.stage!r}, "
                f"project_dir={project_dir!r}"
            )

    def update_status(
        self,
        project_dir: str,
        *,
        stage: str | None = None,
        current_file: str | None = None,
        current_batch: int | None = None,
        batch_total: int | None = None,
        workers_active: int | None = None,
        workers_configured: int | None = None,
        file_totals: dict[str, int] | None = None,
        cache_file_display_map: dict[str, str] | None = None,
        meta_min_lines: int | None = None,
    ) -> None:
        with self._lock:
            state = self._states.get(_normalize_project_dir(project_dir))
            if state is None:
                state = RuntimeState(project_dir=project_dir)
                self._states[_normalize_project_dir(project_dir)] = state
            if stage is not None:
                state.stage = stage
            if current_file is not None:
                state.current_file = current_file
            if current_batch is not None:
                state.current_batch = max(0, int(current_batch))
            if batch_total is not None:
                state.batch_total = max(0, int(batch_total))
            if workers_active is not None:
                state.workers_active = max(0, workers_active)
            if workers_configured is not None:
                state.workers_configured = max(0, workers_configured)
            if file_totals is not None:
                state.file_totals = dict(file_totals)
                state.progress_sealed = True
            if cache_file_display_map is not None:
                state.cache_file_display_map = dict(cache_file_display_map)
            if meta_min_lines is not None:
                state.meta_min_lines = max(0, int(meta_min_lines))
            state.updated_at = _utcnow_text()

    def append_success(
        self,
        project_dir: str,
        *,
        filename: str,
        index: int,
        speaker: str | list[str] | None,
        source_preview: str,
        translation_preview: str,
        trans_by: str = "",
    ) -> None:
        now = datetime.utcnow().timestamp()
        with self._lock:
            state = self._states.get(_normalize_project_dir(project_dir))
            if state is None:
                state = RuntimeState(project_dir=project_dir)
                self._states[_normalize_project_dir(project_dir)] = state
            display_filename = self._resolve_display_filename_locked(state, filename)
            event = RuntimeSentenceEvent(
                id=f"{display_filename}:{index}:{int(now * 1000)}",
                ts=_utcnow_text(),
                filename=display_filename,
                index=index,
                speaker=speaker,
                source_preview=_trim_preview(source_preview),
                translation_preview=_trim_preview(translation_preview),
                trans_by=trans_by,
            )
            file_deque = state.recent_successes_by_file.get(display_filename)
            if file_deque is None:
                file_deque = deque(maxlen=RUNTIME_PER_FILE_SUCCESS_LIMIT)
                state.recent_successes_by_file[display_filename] = file_deque
            file_deque.appendleft(event)
            state.success_timestamps.append(now)
            self._trim_speed_window_locked(state, now)
            state.updated_at = event.ts

    def append_error(
        self,
        project_dir: str,
        *,
        kind: str,
        message: str,
        filename: str = "",
        index_range: str = "",
        retry_count: int | None = None,
        model: str = "",
        sleep_seconds: float | None = None,
        level: str = "error",
    ) -> None:
        with self._lock:
            state = self._states.get(_normalize_project_dir(project_dir))
            if state is None:
                state = RuntimeState(project_dir=project_dir)
                self._states[_normalize_project_dir(project_dir)] = state
            display_filename = self._resolve_display_filename_locked(state, filename)
            ts = _utcnow_text()
            state.recent_errors.appendleft(RuntimeErrorEvent(
                id=f"{kind}:{display_filename}:{int(datetime.utcnow().timestamp() * 1000)}",
                ts=ts,
                kind=kind,
                level=level,
                message=_trim_preview(message, 240),
                filename=display_filename,
                index_range=index_range,
                retry_count=retry_count,
                model=model,
                sleep_seconds=sleep_seconds,
            ))
            state.updated_at = ts

    def add_notice(self, project_dir: str, message: str) -> None:
        with self._lock:
            state = self._states.get(_normalize_project_dir(project_dir))
            if state is None:
                state = RuntimeState(project_dir=project_dir)
                self._states[_normalize_project_dir(project_dir)] = state
            state.notices.appendleft(message)
            state.updated_at = _utcnow_text()

    def clear_notices(self, project_dir: str) -> None:
        with self._lock:
            state = self._states.get(_normalize_project_dir(project_dir))
            if state is not None:
                state.notices.clear()
                state.updated_at = _utcnow_text()

    def add_fix_change(self, project_dir: str, entry: dict) -> None:
        """追加一条修复/改进采纳明细到本次任务的修复报告。

        仅在项目存在运行中的任务（RuntimeState 已由 reset_runtime_project 重建）
        且报告未收尾时记录；CLI 直跑后端（无任务）不产生孤儿记录。
        entries 超过 FIX_REPORT_*_LIMIT 时截断，count 保持精确。
        """
        with self._lock:
            state = self._states.get(_normalize_project_dir(project_dir))
            if state is None or state.fix_report_finished_at:
                return
            filename = str(entry.get("filename", "") or "未知文件")
            # 缓存相对路径供前端跳转校对页：按 (文件名, 序号) 记忆，
            # 分片缓存的区间探测按文件名记忆（一次 listdir + 读取），后续命中内存
            index = entry.get("index")
            memo_key = (filename, index if isinstance(index, int) else None)
            if memo_key not in state.fix_report_cache_paths:
                state.fix_report_cache_paths[memo_key] = (
                    _resolve_fix_report_cache_path(
                        project_dir,
                        filename,
                        memo_key[1],
                        state.fix_report_shard_spans,
                    )
                )
            file_report = state.fix_report_files.get(filename)
            if file_report is None:
                file_report = {"count": 0, "entries": [], "truncated": False}
                state.fix_report_files[filename] = file_report
            file_report["count"] += 1
            state.fix_report_total += 1
            if (
                len(file_report["entries"]) < FIX_REPORT_PER_FILE_LIMIT
                and state.fix_report_total <= FIX_REPORT_TOTAL_LIMIT
            ):
                stored = dict(entry)
                cache_path = state.fix_report_cache_paths.get(memo_key) or ""
                if cache_path:
                    stored["cache_path"] = cache_path
                file_report["entries"].append(stored)
            else:
                file_report["truncated"] = True
                state.fix_report_truncated = True
            if not state.fix_report_started_at:
                state.fix_report_started_at = _utcnow_text()
            state.updated_at = _utcnow_text()

    def finish_fix_report(self, project_dir: str, label: str = "") -> None:
        """收尾本次任务的修复报告：置结束时间并原子写报告文件（best-effort）。

        total=0 也写文件：明确"本次无修复"，避免端点回退读到上一次的陈旧报告。
        """
        with self._lock:
            state = self._states.get(_normalize_project_dir(project_dir))
            if state is None:
                return
            if label:
                state.fix_report_label = str(label)
            if not state.fix_report_finished_at:
                state.fix_report_finished_at = _utcnow_text()
            state.updated_at = state.fix_report_finished_at
            snapshot = self._snapshot_fix_report_locked(state)
        try:
            self._write_fix_report_file(project_dir, snapshot)
            if snapshot["total"] > 0:
                LOGGER.info(
                    f"[fix-report] 本次任务修复报告已生成：共 {snapshot['total']} 条，"
                    f"涉及 {len(snapshot['files'])} 个文件"
                )
            else:
                LOGGER.debug("[fix-report] 本次任务无修复/改进记录")
        except Exception as e:
            LOGGER.warning(f"[fix-report] 修复报告文件写入失败：{e}")

    @staticmethod
    def _snapshot_fix_report_locked(state: RuntimeState) -> dict:
        """组装修复报告快照（持锁调用）；entries 按 index 排序便于展示。"""
        files = []
        for filename, file_report in state.fix_report_files.items():
            files.append({
                "filename": filename,
                "count": file_report["count"],
                "truncated": file_report["truncated"],
                "entries": sorted(
                    file_report["entries"], key=lambda e: e.get("index", 0)
                ),
            })
        files.sort(key=lambda f: f["filename"])
        return {
            "label": state.fix_report_label,
            "started_at": state.fix_report_started_at,
            "finished_at": state.fix_report_finished_at,
            "total": state.fix_report_total,
            "truncated": state.fix_report_truncated,
            "files": files,
        }

    @staticmethod
    def _write_fix_report_file(project_dir: str, snapshot: dict) -> None:
        """原子写修复报告到 <project>/transl_cache/fix_report/latest.json。"""
        report_dir = os.path.join(project_dir, CACHE_FOLDERNAME, "fix_report")
        os.makedirs(report_dir, exist_ok=True)
        target = os.path.join(report_dir, "latest.json")
        tmp_path = target + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, ensure_ascii=False))
        os.replace(tmp_path, target)

    def get_fix_report_snapshot(self, project_dir: str) -> dict | None:
        """返回该项目当前任务的修复报告快照；无运行状态时返回 None。"""
        with self._lock:
            state = self._states.get(_normalize_project_dir(project_dir))
            if state is None:
                return None
            return self._snapshot_fix_report_locked(state)

    @staticmethod
    def _resolve_display_filename_locked(state: RuntimeState, filename: str) -> str:
        normalized = str(filename or "").strip()
        if not normalized:
            return ""

        candidates: list[str] = []

        def add_candidate(value: str) -> None:
            candidate = str(value or "").strip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        add_candidate(normalized)
        add_candidate(f"{normalized}.json")
        add_candidate(f"{normalized}{_CACHE_APPEND_SUFFIX}")

        split_match = re.match(r"^(.*)_\d+$", normalized)
        if split_match:
            split_base = split_match.group(1)
            add_candidate(split_base)
            add_candidate(f"{split_base}.json")
            add_candidate(f"{split_base}{_CACHE_APPEND_SUFFIX}")

        normalized_path = normalized.replace("-}", "/")
        add_candidate(normalized_path)
        add_candidate(f"{normalized_path}.json")
        add_candidate(f"{normalized_path}{_CACHE_APPEND_SUFFIX}")

        split_path_match = re.match(r"^(.*)_\d+$", normalized_path)
        if split_path_match:
            split_path_base = split_path_match.group(1)
            add_candidate(split_path_base)
            add_candidate(f"{split_path_base}.json")
            add_candidate(f"{split_path_base}{_CACHE_APPEND_SUFFIX}")

        for candidate in candidates:
            display = state.cache_file_display_map.get(candidate)
            if display:
                return display

        if normalized_path in state.file_totals:
            return normalized_path
        if split_path_match and split_path_match.group(1) in state.file_totals:
            return split_path_match.group(1)
        if normalized in state.file_totals:
            return normalized

        return normalized_path

    def get_runtime_snapshot(self, project_dir: str) -> dict[str, Any]:
        normalized = _normalize_project_dir(project_dir)
        with self._lock:
            state = self._states.get(normalized)
            if state is None:
                return {
                    "stage": "",
                    "stage_index": -1,
                    "stage_total": PIPELINE_STAGE_TOTAL,
                    "current_file": "",
                    "current_batch": 0,
                    "batch_total": 0,
                    "latest_prompt_preview": "",
                    "translation_previews": {},
                    "prompt_previews": {},
                    "ttft_states": {},
                    "workers_active": 0,
                    "workers_configured": 0,
                    "translation_speed_lpm": 0,
                    "file_totals": {},
                    "cache_file_display_map": {},
                    "meta_min_lines": 0,
                    "recent_errors": [],
                    "recent_successes": [],
                    "notices": [],
                    "updated_at": _utcnow_text(),
                }
            now = datetime.utcnow().timestamp()
            self._trim_speed_window_locked(state, now)
            speed = round((len(state.success_timestamps) / 60) * 60, 1) if state.success_timestamps else 0
            # Flatten per-file success deques and re-order globally by timestamp desc
            merged_successes: list[RuntimeSentenceEvent] = []
            for file_deque in state.recent_successes_by_file.values():
                merged_successes.extend(file_deque)
            merged_successes.sort(key=lambda ev: ev.ts, reverse=True)
            if len(merged_successes) > RUNTIME_SNAPSHOT_SUCCESS_LIMIT:
                merged_successes = merged_successes[:RUNTIME_SNAPSHOT_SUCCESS_LIMIT]
            return {
                "stage": state.stage,
                "stage_index": _compute_stage_index(state.stage),
                "stage_total": PIPELINE_STAGE_TOTAL,
                "current_file": state.current_file,
                "current_batch": state.current_batch,
                "batch_total": state.batch_total,
                "latest_prompt_preview": state.latest_prompt_preview,
                "translation_previews": dict(state.translation_previews),
                # 直接透传已写入的提示词快照：数据存在性不依赖 workers_active，
                # 否则 worker 空闲瞬间（队列间隙/任务首尾）会被清空，前端 tab 消失
                "prompt_previews": {
                    wid: {
                        "worker_id": snap.worker_id,
                        "preview": snap.preview,
                        "filename": snap.filename,
                        "batch": snap.batch,
                        "updated_at": snap.updated_at,
                    }
                    for wid, snap in state.prompt_previews.items()
                },
                "ttft_states": {
                    wid: {
                        "worker_id": snap.worker_id,
                        "status": snap.status,
                        "ttft_ms": snap.ttft_ms,
                        "model": snap.model,
                        "updated_at": snap.updated_at,
                    }
                    for wid, snap in state.ttft_states.items()
                },
                "workers_active": state.workers_active,
                "workers_configured": state.workers_configured,
                "translation_speed_lpm": speed,
                "file_totals": dict(state.file_totals),
                "cache_file_display_map": dict(state.cache_file_display_map),
                "meta_min_lines": state.meta_min_lines,
                "recent_errors": [event.to_dict() for event in state.recent_errors],
                "recent_successes": [event.to_dict() for event in merged_successes],
                "notices": list(state.notices),
                "updated_at": state.updated_at,
            }

    @staticmethod
    def _trim_speed_window_locked(state: RuntimeState, now: float) -> None:
        while state.success_timestamps and now - state.success_timestamps[0] > 60:
            state.success_timestamps.popleft()


def _trim_preview(value: str, limit: int = 140) -> str:
    normalized = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)] + "…"


RUNTIME_REGISTRY = RuntimeRegistry()


def set_live_snippets(
    project_dir: str,
    prompt_preview: str = "",
    translation_preview: str = "",
    worker_id: str = "",
    filename: str = "",
    note_only: bool = False,
    force: bool = False,
) -> None:
    """在翻译循环中设置当前提示词和译文预览的实时快照。

    由 ForGalJsonTranslate.translate() 与 BaseTranslate.ask_chatbot() 调用。
    worker_id 为空时回退到 contextvars 中的 worker 标识（-1 表示非 worker 上下文），
    此时仅更新 latest_prompt_preview 兼容元数据生成阶段。

    提示词预览（prompt_preview）：覆盖式更新，限 8000 字符。
    结果预览（translation_preview）：按 worker 覆盖式更新，单条封顶 10000 字符保留
    最新尾部，避免内存与接口传输无限增长；非 worker 上下文（元数据生成、全局分析等
    单上下文后端）写入公共 key "-1"，供前端在无 worker 分板块时展示。
    note_only=True 表示说明性预览（0 命中/0 采纳/结果已丢弃）：已有真实结果时不覆盖。
    force=True 与 note_only 配合：强制覆盖真实结果，但仍按说明性预览记账。
    """
    max_len = 8000  # 仅提示词预览限制长度
    max_translation_len = 10000  # 单 worker 译文预览封顶长度，保留最新尾部
    worker_id = worker_id or WORKER_ID_CTX.get()
    with RUNTIME_REGISTRY._lock:
        state = RUNTIME_REGISTRY._states.get(_normalize_project_dir(project_dir))
        if state is None:
            LOGGER.debug(f"[prompt-preview] set_live_snippets: 无对应 runtime state, project_dir={project_dir!r}")
            return
        if prompt_preview:
            preview_text = prompt_preview[:max_len] if len(prompt_preview) > max_len else prompt_preview
            state.latest_prompt_preview = preview_text
            if worker_id and worker_id != "-1":
                # filename 含 ":批次" 后缀时拆分展示字段
                file_name, _, batch = filename.partition(":")
                state.prompt_previews[worker_id] = WorkerPromptSnapshot(
                    worker_id=worker_id,
                    preview=preview_text,
                    filename=file_name,
                    batch=batch,
                    updated_at=_utcnow_text(),
                )
                LOGGER.debug(
                    f"[prompt-preview] set_live_snippets 写入成功: worker_id={worker_id!r}, "
                    f"filename={file_name!r}, batch={batch!r}, "
                    f"prompt_previews keys={list(state.prompt_previews.keys())}"
                )
            else:
                LOGGER.debug(
                    f"[prompt-preview] set_live_snippets 未写入 prompt_previews: "
                    f"worker_id={worker_id!r}（-1/空表示非 worker 上下文）, 仅更新 latest_prompt_preview"
                )
        if translation_preview:
            # 覆盖式替换该 worker 最新结果预览，而非跨批次累积；
            # 非 worker 上下文统一写入公共 key "-1"（如全局分析、GenDic 等单上下文后端）
            preview_key = worker_id if (worker_id and worker_id != "-1") else "-1"
            if (
                note_only
                and not force
                and preview_key in state.translation_previews
                and preview_key not in state.preview_note_keys
            ):
                # 已有真实结果：说明性预览让位，避免把低频命中的真实结果刷掉
                LOGGER.debug(
                    f"[prompt-preview] worker {preview_key} 已有真实结果预览，跳过说明性预览"
                )
            else:
                preview_text = translation_preview
                if len(preview_text) > max_translation_len:
                    preview_text = preview_text[-max_translation_len:]
                    LOGGER.debug(f"[prompt-preview] worker {preview_key} translation_preview 已封顶至最近 {max_translation_len} 字符")
                state.translation_previews[preview_key] = preview_text
                if note_only:
                    state.preview_note_keys.add(preview_key)
                else:
                    state.preview_note_keys.discard(preview_key)
        state.updated_at = _utcnow_text()


def set_ttft_state(
    project_dir: str,
    status: str,
    worker_id: str = "",
    ttft_ms: float | None = None,
    model: str = "",
) -> None:
    """更新指定 worker 当前请求的流式首字状态灯。

    由 BaseEngine.ask_chatbot() 在流式生命周期关键点调用：
    WAITING（发起请求）→ FIRST_TOKEN（首字到达，附 ttft_ms）→ COMPLETED（成功，保留 ttft_ms
    与 model 供前端展示「已完成+耗时」）/ RETRYING / CANCELLED。非 worker 上下文（worker_id="-1"）不写入。
    """
    worker_id = worker_id or WORKER_ID_CTX.get()
    if not worker_id or worker_id == "-1":
        return
    # IDLE 表示"当前无进行中请求"，须清空 ttft_ms 与 model，避免残留上次结果
    if status == TTFTStatus.IDLE:
        ttft_ms = None
        model = ""
    with RUNTIME_REGISTRY._lock:
        state = RUNTIME_REGISTRY._states.get(_normalize_project_dir(project_dir))
        if state is None:
            return
        prev = state.ttft_states.get(worker_id)
        # IDLE 强制清空；非 IDLE 时 ttft_ms/model 仅在未提供时继承上一次的值
        if status == TTFTStatus.IDLE:
            result_ttft = None
            result_model = ""
        else:
            result_ttft = ttft_ms if ttft_ms is not None else (prev.ttft_ms if prev else None)
            result_model = model or (prev.model if prev else "")
        state.ttft_states[worker_id] = TTFTPreview(
            worker_id=worker_id,
            status=status,
            ttft_ms=result_ttft,
            model=result_model,
            updated_at=_utcnow_text(),
        )
        state.updated_at = _utcnow_text()


_CACHE_APPEND_SUFFIX = ".append.jsonl"


@dataclass(slots=True)
class _CacheProgressFileStat:
    mtime_ns: int
    size: int
    translated_keys: frozenset[str]
    problem_keys: frozenset[str]
    failed_keys: frozenset[str]
    # 全部条目 key（含未翻译），用于 file_totals 缺失时的进度分母回退
    total_keys: frozenset[str] = field(default_factory=frozenset)
    retran_terms_signature: tuple[str, ...] = field(default_factory=tuple)
    retran_hit_keys: dict[str, frozenset[str]] = field(default_factory=dict)


@dataclass(slots=True)
class _RetranConfigStat:
    mtime_ns: int
    size: int
    retran_key: str | list[str]


def _normalize_retran_key(value: Any) -> str | list[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return ""


def _normalize_retran_terms(value: str | list[str]) -> list[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                result.append(text)
        return result
    return []


def _check_retran_key(retran_key: str | list[str], target: Any) -> bool:
    text = str(target or "")
    if isinstance(retran_key, str):
        return bool(retran_key) and retran_key in text
    if isinstance(retran_key, list):
        return any(key in text for key in retran_key if key)
    return False


def _parse_runtime_job_started_at_ns(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        return int(dt.timestamp() * 1_000_000_000)
    except Exception:
        return None


class RuntimeProgressCache:
    def __init__(self) -> None:
        self._project_files: dict[str, dict[str, _CacheProgressFileStat]] = {}
        self._retran_config_cache: dict[str, _RetranConfigStat] = {}
        # pass1/pass2 缓存清单缓存：按项目目录键控，value 为 (目录 mtime 签名, meta 清单, batch 清单)
        self._stage_listing: dict[str, tuple[tuple[int, int], dict[str, int], dict[str, int]]] = {}
        self._lock = threading.Lock()

    def reset_project(self, project_dir: str) -> None:
        normalized = _normalize_project_dir(project_dir)
        with self._lock:
            self._project_files.pop(normalized, None)
            self._stage_listing.pop(normalized, None)

    def get_retran_key(self, project_dir: str, config_file_name: str = "config.yaml") -> str | list[str]:
        config_path = os.path.join(project_dir, config_file_name or "config.yaml")
        normalized_config = str(Path(config_path).resolve())

        try:
            stat = os.stat(config_path)
        except OSError:
            with self._lock:
                self._retran_config_cache.pop(normalized_config, None)
            return ""

        with self._lock:
            cached = self._retran_config_cache.get(normalized_config)
            if (
                cached is not None
                and cached.mtime_ns == int(stat.st_mtime_ns)
                and cached.size == int(stat.st_size)
            ):
                return cached.retran_key

        retran_key: str | list[str] = ""
        try:
            with open(config_path, "rb") as cfg_file:
                cfg = safe_load(cfg_file.read()) or {}
            common = cfg.get("common", {}) if isinstance(cfg, dict) else {}
            retran_key = _normalize_retran_key(common.get("retranslKey", ""))
        except Exception:
            retran_key = ""

        with self._lock:
            self._retran_config_cache[normalized_config] = _RetranConfigStat(
                mtime_ns=int(stat.st_mtime_ns),
                size=int(stat.st_size),
                retran_key=retran_key,
            )

        return retran_key

    @staticmethod
    def _list_input_display_names(project_dir: str) -> list[str]:
        """枚举输入目录中的源文件，返回显示名列表（相对路径，/ 分隔）。

        文件清单复用 Utils.get_file_list（已排除 FileMetaData.json 等元数据控制文件），
        并额外跳过点文件与 .tmp。显示名与 LLMTranslate._build_runtime_file_maps
        的 file_totals 键同口径。
        """
        input_dir = os.path.join(project_dir, INPUT_FOLDERNAME)
        if not os.path.isdir(input_dir):
            input_dir = os.path.join(project_dir, "json_jp")
            if not os.path.isdir(input_dir):
                return []
        display_names: list[str] = []
        for file_path in get_file_list(input_dir):
            name = os.path.basename(file_path)
            if name.startswith(".") or name.endswith(".tmp"):
                continue
            rel = os.path.relpath(file_path, input_dir)
            display_names.append(rel.replace(os.sep, "/"))
        return sorted(display_names)

    def _get_meta_stage_listing(
        self, project_dir: str, cache_dir: str
    ) -> tuple[dict[str, int], dict[str, int]]:
        """列出 pass1/pass2 缓存文件存在清单（按目录 mtime 缓存，避免每次轮询重扫）。

        返回 (meta_map, batch_map)：key 为去掉 .meta.json / .batch.json 后缀的
        缓存条目名（即输入文件 basename），value 为缓存文件 mtime_ns。
        缓存按项目目录键控，避免多项目共享单例时互相串数据。
        """
        meta_dir = os.path.join(cache_dir, PASS1_CACHE_DIR)
        batch_dir = os.path.join(cache_dir, PASS2_CACHE_DIR)
        try:
            meta_sig = int(os.stat(meta_dir).st_mtime_ns)
        except OSError:
            meta_sig = -1
        try:
            batch_sig = int(os.stat(batch_dir).st_mtime_ns)
        except OSError:
            batch_sig = -1

        listing_key = _normalize_project_dir(project_dir)
        cached = self._stage_listing.get(listing_key)
        if cached is not None and cached[0] == (meta_sig, batch_sig):
            return cached[1], cached[2]

        def _scan(listing_dir: str, suffix: str) -> dict[str, int]:
            result: dict[str, int] = {}
            try:
                for name in os.listdir(listing_dir):
                    if not name.endswith(suffix):
                        continue
                    try:
                        result[name[: -len(suffix)]] = int(
                            os.stat(os.path.join(listing_dir, name)).st_mtime_ns
                        )
                    except OSError:
                        continue
            except OSError:
                pass
            return result

        meta_map = _scan(meta_dir, ".meta.json")
        batch_map = _scan(batch_dir, ".batch.json")
        self._stage_listing[listing_key] = ((meta_sig, batch_sig), meta_map, batch_map)
        return meta_map, batch_map

    def get_progress(
        self,
        project_dir: str,
        file_totals: dict[str, int],
        cache_file_display_map: dict[str, str],
        retran_key: str | list[str] = "",
        retran_terms: list[str] | None = None,
        current_job_started_at_ns: int | None = None,
        meta_min_lines: int = 0,
    ) -> dict[str, Any]:
        normalized = _normalize_project_dir(project_dir)
        cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
        retran_terms = retran_terms or []
        retran_terms_signature = tuple(retran_terms)

        with self._lock:
            project_stats = self._project_files.setdefault(normalized, {})
            seen_files: set[str] = set()

            if os.path.isdir(cache_dir):
                # 缓存文件范围判定与构建输出/校验、问题列表、全量重检同源（延迟导入避免
                # server_cache 与本模块的顶层循环依赖）
                from GalTransl.server_cache import _is_translation_cache_rel

                # 递归扫描：缓存文件可能位于 pass3_cache/ 等子目录中（如翻译阶段写
                # 入 transl_cache/pass3_cache/*.json）。os.scandir 只列直接子项，
                # 改用 os.walk 以覆盖所有子目录。
                for dirpath, _dirnames, filenames in os.walk(cache_dir):
                    for name in filenames:
                        if not (
                            name.endswith(".json")
                            or name.endswith(_CACHE_APPEND_SUFFIX)
                        ):
                            continue

                        # 用相对于 cache_dir 的路径作为 key，避免不同子目录下同名文件冲突
                        rel_dir = os.path.relpath(dirpath, cache_dir)
                        rel_key = os.path.join(rel_dir, name) if rel_dir != "." else name
                        # 句子条目缓存剔除元数据/示例/快照（pass1/pass2 元数据的存在性由
                        # _get_meta_stage_listing 单独检测，不参与句子进度统计）；
                        # .append.jsonl 增量日志只计入 total/translated 供翻译中的实时显示：
                        # 其条目按 post_save=False 落盘不带 problem 字段，不参与问题计数。
                        if name.endswith(".json") and not _is_translation_cache_rel(rel_key):
                            continue
                        seen_files.add(rel_key)

                        full_path = os.path.join(dirpath, name)
                        try:
                            stat = os.stat(full_path)
                        except OSError:
                            continue

                        cached = project_stats.get(rel_key)
                        if (
                            cached is not None
                            and cached.mtime_ns == int(stat.st_mtime_ns)
                            and cached.size == int(stat.st_size)
                            and cached.retran_terms_signature == retran_terms_signature
                        ):
                            continue

                        translated_keys: set[str] = set()
                        problem_keys: set[str] = set()
                        failed_keys: set[str] = set()
                        total_keys: set[str] = set()
                        retran_hit_keys: dict[str, set[str]] = {term: set() for term in retran_terms}

                        def _name_src(items: list[Any], idx: int) -> str:
                            if idx < 0 or idx >= len(items):
                                return ""
                            item = items[idx]
                            if not isinstance(item, dict):
                                return ""
                            name = str(item.get("name", "") or "")
                            pre_src = str(item.get("pre_src", item.get("pre_jp", "")) or "")
                            return f"{name}{pre_src}"

                        def _entry_signature(items: list[Any], idx: int) -> str:
                            line_now = _name_src(items, idx)
                            row = items[idx] if 0 <= idx < len(items) else {}
                            row_index = str(row.get("index", "")) if isinstance(row, dict) else ""
                            if not line_now:
                                if isinstance(row, dict):
                                    row_src = str(
                                        row.get("pre_src", row.get("pre_jp", row.get("post_src", "")))
                                        or ""
                                    )
                                    row_name = str(row.get("name", "") or "")
                                    return f"__row__:{idx}:{row_index}:{row_name}:{row_src}"
                                return f"__row__:{idx}"

                            line_prev = "None"
                            j = idx - 1
                            while j >= 0:
                                candidate = _name_src(items, j)
                                if candidate:
                                    line_prev = candidate
                                    break
                                j -= 1

                            line_next = "None"
                            j = idx + 1
                            while j < len(items):
                                candidate = _name_src(items, j)
                                if candidate:
                                    line_next = candidate
                                    break
                                j += 1

                            # 在 context key 前拼接 index，使不同位置的相同上下文生成不同 key，避免进度少计
                            context_key = f"{line_prev}{line_now}{line_next}"
                            if row_index:
                                return f"{row_index}:{context_key}"
                            return context_key

                        entries: list[Any] = []
                        try:
                            import orjson
                            with open(full_path, "rb") as f:
                                raw = f.read()
                            if name.endswith(_CACHE_APPEND_SUFFIX):
                                for line in raw.splitlines():
                                    if not line:
                                        continue
                                    try:
                                        row = orjson.loads(line)
                                    except Exception:
                                        continue
                                    if isinstance(row, dict):
                                        entries.append(row)
                            else:
                                loaded = orjson.loads(raw)
                                if isinstance(loaded, list):
                                    entries = loaded
                        except Exception:
                            continue

                        for idx, item in enumerate(entries):
                            if not isinstance(item, dict):
                                continue

                            entry_key = str(item.get("__cache_key", "")).strip()
                            if entry_key:
                                # 同 _entry_signature：以 index 为前缀使不同位置的同文本条目
                                # 在 set 中各占一席，同时保持与 .json 快照 key 的一致性。
                                item_index = str(item.get("index", ""))
                                if item_index:
                                    entry_key = f"{item_index}:{entry_key}"
                            else:
                                entry_key = _entry_signature(entries, idx)

                            is_translated = bool(item.get("pre_dst", "") or item.get("pre_zh", ""))
                            is_problem = bool(item.get("problem", ""))
                            is_failed = (
                                "翻译失败" in str(item.get("problem", ""))
                                or "(Failed)" in str(item.get("pre_dst", "") or item.get("pre_zh", ""))
                                or "(翻译失败)" in str(item.get("pre_dst", "") or item.get("pre_zh", ""))
                            )

                            no_proofread = str(item.get("proofread_dst", "") or "") == ""

                            # retran_hit_keys 统计不受 retran_key 过滤影响，确保新旧文件统计口径一致
                            if (
                                not name.endswith(_CACHE_APPEND_SUFFIX)
                                and retran_hit_keys
                            ):
                                source_text = item.get("pre_src", item.get("pre_jp", ""))
                                problem_text = item.get("problem", "")
                                for term in retran_terms:
                                    if _check_retran_key(term, source_text) or _check_retran_key(term, problem_text):
                                        retran_hit_keys[term].add(entry_key)

                            should_apply_retransl_filter = not name.endswith(_CACHE_APPEND_SUFFIX)
                            if (
                                should_apply_retransl_filter
                                and current_job_started_at_ns is not None
                                and int(stat.st_mtime_ns) >= int(current_job_started_at_ns)
                            ):
                                should_apply_retransl_filter = False
                            if (
                                is_translated
                                and should_apply_retransl_filter
                                and retran_key
                                and no_proofread
                                and (
                                    _check_retran_key(retran_key, item.get("pre_src", item.get("pre_jp", "")))
                                    or _check_retran_key(retran_key, item.get("problem", ""))
                                )
                            ):
                                is_translated = False

                            if is_translated:
                                translated_keys.add(entry_key)
                            if is_problem:
                                problem_keys.add(entry_key)
                            if is_failed:
                                failed_keys.add(entry_key)
                            total_keys.add(entry_key)

                        project_stats[rel_key] = _CacheProgressFileStat(
                            mtime_ns=int(stat.st_mtime_ns),
                            size=int(stat.st_size),
                            translated_keys=frozenset(translated_keys),
                            problem_keys=frozenset(problem_keys),
                            failed_keys=frozenset(failed_keys),
                            total_keys=frozenset(total_keys),
                            retran_terms_signature=retran_terms_signature,
                            retran_hit_keys={
                                term: frozenset(hit_keys)
                                for term, hit_keys in retran_hit_keys.items()
                            },
                        )

            stale_files = [name for name in project_stats if name not in seen_files]
            for name in stale_files:
                project_stats.pop(name, None)

            file_progress_map: dict[str, dict[str, Any]] = {}
            retran_counts: dict[str, set[str]] = {term: set() for term in retran_terms}

            # 阶段检测数据源：输入文件清单 + pass1/pass2 缓存存在清单
            input_displays = self._list_input_display_names(project_dir)
            meta_map, batch_map = self._get_meta_stage_listing(project_dir, cache_dir)
            input_display_set = set(input_displays) | set(file_totals)

            def _resolve_display(canonical_name: str) -> str:
                # 缓存文件名 -> 显示名：优先 display_map；其次把 "-}" 还原为 "/"
                # 并去掉多块后缀 _N（与 _build_runtime_file_maps 的命名互逆），
                # 保证后端重启（file_totals/display_map 为空）后仍能对回输入文件
                display = cache_file_display_map.get(canonical_name)
                if display:
                    return display
                path_like = canonical_name.replace("-}", "/")
                chunk_match = re.match(r"^(.*)_\d+$", path_like)
                candidates = [path_like]
                if chunk_match:
                    candidates.append(chunk_match.group(1))
                for candidate in candidates:
                    if candidate in input_display_set:
                        return candidate
                return canonical_name

            for rel_key, stat in project_stats.items():
                # rel_key 可能是 "pass3_cache/xxx.json" 或仅 "xxx.json"；
                # cache_file_display_map 的键总是纯文件名，故取 basename 做映射。
                base_name = os.path.basename(rel_key)
                canonical_name = (
                    base_name[: -len(_CACHE_APPEND_SUFFIX)]
                    if base_name.endswith(_CACHE_APPEND_SUFFIX)
                    else base_name
                )
                display_name = _resolve_display(canonical_name)
                if (
                    file_totals
                    and display_name not in file_totals
                    and display_name not in input_display_set
                ):
                    continue
                if display_name not in file_progress_map:
                    file_progress_map[display_name] = {
                        "filename": display_name,
                        "total": int(file_totals.get(display_name, 0)),
                        "translated": 0,
                        "problems": 0,
                        "failed": 0,
                        "_translated_keys": set(),
                        "_problem_keys": set(),
                        "_failed_keys": set(),
                        "_total_keys": set(),
                    }
                file_progress_map[display_name]["_translated_keys"].update(stat.translated_keys)
                file_progress_map[display_name]["_problem_keys"].update(stat.problem_keys)
                file_progress_map[display_name]["_failed_keys"].update(stat.failed_keys)
                file_progress_map[display_name]["_total_keys"].update(stat.total_keys)
                for term, hit_keys in stat.retran_hit_keys.items():
                    retran_counts.setdefault(term, set()).update(hit_keys)

            for display_name, total_count in file_totals.items():
                file_progress_map.setdefault(
                    display_name,
                    {
                        "filename": display_name,
                        "total": int(total_count),
                        "translated": 0,
                        "problems": 0,
                        "failed": 0,
                        "_translated_keys": set(),
                        "_problem_keys": set(),
                        "_failed_keys": set(),
                        "_total_keys": set(),
                    },
                )

            # 输入目录中尚无任何 pass3 缓存的文件也要进入列表（如仅跑过元数据后端）
            for display_name in input_displays:
                file_progress_map.setdefault(
                    display_name,
                    {
                        "filename": display_name,
                        "total": int(file_totals.get(display_name, 0)),
                        "translated": 0,
                        "problems": 0,
                        "failed": 0,
                        "_translated_keys": set(),
                        "_problem_keys": set(),
                        "_failed_keys": set(),
                        "_total_keys": set(),
                    },
                )

            for file_progress in file_progress_map.values():
                total_count = int(file_progress.get("total", 0))
                if total_count <= 0:
                    # file_totals 缺失（如后端重启后未重跑翻译）时，用缓存条目数回退分母
                    total_count = len(file_progress["_total_keys"])
                translated = len(file_progress["_translated_keys"])
                problems = len(file_progress["_problem_keys"])
                failed = len(file_progress["_failed_keys"])

                if total_count > 0:
                    translated = min(translated, total_count)
                    problems = min(problems, total_count)
                    failed = min(failed, total_count)

                trans_done = total_count > 0 and translated >= total_count
                # 阶段完成检测：文件/批次元数据按缓存文件前缀存在性，翻译按条目统计；
                # 校对暂无独立缓存文件（结果写回 pass3 条目 proofread_dst 字段），先预留接口
                # TODO: 校对缓存独立成文件后，在此检测该文件前缀的校对缓存并置位 done
                file_base = os.path.basename(file_progress["filename"])
                # 行数不足 metaMinLines 的文件不生成元数据（无缓存文件是预期行为），
                # 故按「已跳过」等同完成，否则这两个阶段会永远显示未完成。
                # 仅取本轮 file_totals 的真实行数判定：分母回退为「缓存条目数」时
                # 该值不是文件行数，凭它判跳过会把部分翻译的大文件误标为已跳过。
                line_total = int(file_totals.get(file_progress["filename"], 0) or 0)
                if meta_min_lines > 0 and 0 < line_total < meta_min_lines:
                    meta_done = True
                    batch_done = True
                else:
                    meta_done = file_base in meta_map
                    batch_done = file_base in batch_map
                file_progress["stages"] = [
                    {"key": "meta", "done": meta_done},
                    {"key": "batch", "done": batch_done},
                    {"key": "trans", "done": trans_done},
                    {"key": "proofread", "done": False},
                ]

                file_progress["total"] = total_count
                file_progress["translated"] = translated
                file_progress["problems"] = problems
                file_progress["failed"] = failed
                file_progress.pop("_translated_keys", None)
                file_progress.pop("_problem_keys", None)
                file_progress.pop("_failed_keys", None)
                file_progress.pop("_total_keys", None)

            files = sorted(file_progress_map.values(), key=lambda item: item["filename"])
            return {
                "total": sum(int(item["total"]) for item in files),
                "translated": sum(int(item["translated"]) for item in files),
                "problems": sum(int(item["problems"]) for item in files),
                "failed": sum(int(item["failed"]) for item in files),
                "retransl_stats": [
                    {"key": term, "count": len(retran_counts.get(term, set()))}
                    for term in retran_terms
                ],
                "files": files,
            }


RUNTIME_PROGRESS_CACHE = RuntimeProgressCache()


def reset_runtime_project(project_dir: str) -> None:
    RUNTIME_REGISTRY.reset_project(project_dir)
    RUNTIME_PROGRESS_CACHE.reset_project(project_dir)


def reset_runtime_previews(project_dir: str) -> None:
    """清空该项目的预览快照（阶段切换时由流水线调用，避免跨阶段残留遮蔽新结果）。"""
    RUNTIME_REGISTRY.reset_previews(project_dir)


def update_runtime_status(
    project_dir: str,
    *,
    stage: str | None = None,
    current_file: str | None = None,
    current_batch: int | None = None,
    batch_total: int | None = None,
    workers_active: int | None = None,
    workers_configured: int | None = None,
    file_totals: dict[str, int] | None = None,
    cache_file_display_map: dict[str, str] | None = None,
    meta_min_lines: int | None = None,
) -> None:
    RUNTIME_REGISTRY.update_status(
        project_dir,
        stage=stage,
        current_file=current_file,
        current_batch=current_batch,
        batch_total=batch_total,
        workers_active=workers_active,
        workers_configured=workers_configured,
        file_totals=file_totals,
        cache_file_display_map=cache_file_display_map,
        meta_min_lines=meta_min_lines,
    )


def record_runtime_success(
    project_dir: str,
    *,
    filename: str,
    index: int,
    speaker: str | list[str] | None,
    source_preview: str,
    translation_preview: str,
    trans_by: str = "",
) -> None:
    RUNTIME_REGISTRY.append_success(
        project_dir,
        filename=filename,
        index=index,
        speaker=speaker,
        source_preview=source_preview,
        translation_preview=translation_preview,
        trans_by=trans_by,
    )


def record_runtime_error(
    project_dir: str,
    *,
    kind: str,
    message: str,
    filename: str = "",
    index_range: str = "",
    retry_count: int | None = None,
    model: str = "",
    sleep_seconds: float | None = None,
    level: str = "error",
) -> None:
    RUNTIME_REGISTRY.append_error(
        project_dir,
        kind=kind,
        message=message,
        filename=filename,
        index_range=index_range,
        retry_count=retry_count,
        model=model,
        sleep_seconds=sleep_seconds,
        level=level,
    )


def record_runtime_notice(project_dir: str, message: str) -> None:
    """记录一条一次性用户提示（前端轮询 runtime 后 toast 并清除）。"""
    RUNTIME_REGISTRY.add_notice(project_dir, message)


def clear_runtime_notices(project_dir: str) -> None:
    """清除该项目的所有未读提示（前端 toast 后调用）。"""
    RUNTIME_REGISTRY.clear_notices(project_dir)


def record_fix_change(projectConfig: Any, entry: dict) -> None:
    """记录一条修复/改进采纳明细到本次任务的修复报告（供任务完成后按文件展示）。

    项目目录解析与 _runtime_project_dir 同口径（runtime_project_dir 优先）；
    无运行中任务 / 解析失败时静默跳过，绝不影响修复主流程。
    """
    try:
        project_dir = getattr(projectConfig, "runtime_project_dir", None) or (
            projectConfig.getProjectDir()
        )
        RUNTIME_REGISTRY.add_fix_change(project_dir, entry)
    except Exception as e:
        LOGGER.debug(f"[fix-report] 修复记录写入失败（忽略）：{e}")


def finish_project_fix_report(project_dir: str, label: str = "") -> None:
    """任务收尾：置本次修复报告结束时间并持久化到项目缓存目录（best-effort）。"""
    try:
        RUNTIME_REGISTRY.finish_fix_report(project_dir, label)
    except Exception as e:
        LOGGER.warning(f"[fix-report] 修复报告收尾失败：{e}")


def load_fix_report_file(project_dir: str) -> dict | None:
    """读取最近一次持久化的修复报告文件；不存在/损坏返回 None。"""
    path = os.path.join(project_dir, CACHE_FOLDERNAME, "fix_report", "latest.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except Exception as e:
        LOGGER.warning(f"[fix-report] 修复报告文件读取失败：{e}")
        return None


# Project path helpers

def encode_project_dir(project_dir: str) -> str:
    """Encode a filesystem path to a URL-safe token."""
    return urlsafe_b64encode(project_dir.encode("utf-8")).decode("ascii")


def decode_project_dir(token: str) -> str:
    """Decode a URL-safe token back to a filesystem path."""
    padding = 4 - len(token) % 4
    if padding != 4:
        token += "=" * padding
    return urlsafe_b64decode(token.encode("ascii")).decode("utf-8")


def _safe_project_dir(token: str) -> str:
    """Decode and validate a project directory token. Raises ValueError on failure."""
    try:
        project_dir = decode_project_dir(token)
    except Exception:
        raise ValueError("invalid project id")
    if not os.path.isdir(project_dir):
        raise ValueError(f"project directory does not exist: {project_dir}")
    return project_dir
