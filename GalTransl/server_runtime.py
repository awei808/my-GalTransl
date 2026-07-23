from __future__ import annotations

import json
import os
import re
import threading
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version
from yaml import safe_load

from GalTransl import CACHE_FOLDERNAME

def _utcnow_text() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


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


RUNTIME_RECENT_EVENT_LIMIT = 80
RUNTIME_PER_FILE_SUCCESS_LIMIT = 100
# Upper bound on success events per snapshot (500): each file keeps a 100-slot deque,
# returning all would explode HTTP payload; 500 covers UI budget + per-file filter
RUNTIME_SNAPSHOT_SUCCESS_LIMIT = 500


# 7 阶段流水线——供前端"流程完成情况"展示用。
# 顺序与 LLMTranslate.doLLMTranslate 的阶段执行顺序一致。
PIPELINE_STAGE_NAMES: list[str] = [
    "输入数据校验",
    "文本无损压缩",
    "生成全局游戏分析",
    "构建术语表",
    "生成文件级元数据",
    "划分翻译区间",
    "翻译执行中",
]
PIPELINE_STAGE_TOTAL: int = len(PIPELINE_STAGE_NAMES)


def _compute_stage_index(stage: str) -> int:
    """返回 stage 字段对应的流水线阶段索引（0-6）；-1 表示不匹配。"""
    if not stage:
        return -1
    # 子阶段如 "文件级元数据 (1/5)" 通过前缀或包含匹配映射到对应主阶段
    for idx, name in enumerate(PIPELINE_STAGE_NAMES):
        if stage.startswith(name) or name in stage:
            return idx
    # 兜底："完整流水线启动" → 阶段 0，"流水线完成" → 阶段 6，"检查模型可用性" → 阶段 0
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
    latest_assembled_preview: str = ""
    updated_at: str = field(default_factory=_utcnow_text)
    file_totals: dict[str, int] = field(default_factory=dict)
    cache_file_display_map: dict[str, str] = field(default_factory=dict)
    # Per-file deque of recent success events (newest-first, appendleft for O(1) merging)
    recent_successes_by_file: dict[str, deque[RuntimeSentenceEvent]] = field(default_factory=dict)
    recent_errors: deque[RuntimeErrorEvent] = field(default_factory=lambda: deque(maxlen=RUNTIME_RECENT_EVENT_LIMIT))
    success_timestamps: deque[float] = field(default_factory=deque)


class RuntimeRegistry:
    def __init__(self) -> None:
        self._states: dict[str, RuntimeState] = {}
        self._lock = threading.Lock()

    def ensure_project(self, project_dir: str) -> RuntimeState:
        normalized = _normalize_project_dir(project_dir)
        with self._lock:
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
            self._states[normalized] = RuntimeState(project_dir=project_dir)

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
            if cache_file_display_map is not None:
                state.cache_file_display_map = dict(cache_file_display_map)
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
                    "latest_assembled_preview": "",
                    "workers_active": 0,
                    "workers_configured": 0,
                    "translation_speed_lpm": 0,
                    "file_totals": {},
                    "cache_file_display_map": {},
                    "recent_errors": [],
                    "recent_successes": [],
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
                "latest_assembled_preview": state.latest_assembled_preview,
                "workers_active": state.workers_active,
                "workers_configured": state.workers_configured,
                "translation_speed_lpm": speed,
                "file_totals": dict(state.file_totals),
                "cache_file_display_map": dict(state.cache_file_display_map),
                "recent_errors": [event.to_dict() for event in state.recent_errors],
                "recent_successes": [event.to_dict() for event in merged_successes],
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


def set_live_snippets(project_dir: str, prompt_preview: str = "", assembled_preview: str = "") -> None:
    """在翻译循环中设置当前提示词和译文拼接的实时预览。

    由 ForGalJsonMulitChat.translate() 在每轮 LLM 调用前后调用。
    确保前端轮询 /runtime 时可以获取到最新提示词和译文拼接内容。
    """
    max_len = 4000  # 每段预览最大字符数（当前提示词/译文拼接面板用；过大仅截断，不影响翻译）
    with RUNTIME_REGISTRY._lock:
        state = RUNTIME_REGISTRY._states.get(_normalize_project_dir(project_dir))
        if state is None:
            return
        if prompt_preview:
            state.latest_prompt_preview = prompt_preview[:max_len] if len(prompt_preview) > max_len else prompt_preview
        if assembled_preview:
            state.latest_assembled_preview = assembled_preview[:max_len] if len(assembled_preview) > max_len else assembled_preview
        state.updated_at = _utcnow_text()


_CACHE_APPEND_SUFFIX = ".append.jsonl"


@dataclass(slots=True)
class _CacheProgressFileStat:
    mtime_ns: int
    size: int
    translated_keys: frozenset[str]
    problem_keys: frozenset[str]
    failed_keys: frozenset[str]
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
        self._lock = threading.Lock()

    def reset_project(self, project_dir: str) -> None:
        normalized = _normalize_project_dir(project_dir)
        with self._lock:
            self._project_files.pop(normalized, None)

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

    def get_progress(
        self,
        project_dir: str,
        file_totals: dict[str, int],
        cache_file_display_map: dict[str, str],
        retran_key: str | list[str] = "",
        retran_terms: list[str] | None = None,
        current_job_started_at_ns: int | None = None,
    ) -> dict[str, Any]:
        normalized = _normalize_project_dir(project_dir)
        cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
        retran_terms = retran_terms or []
        retran_terms_signature = tuple(retran_terms)

        with self._lock:
            project_stats = self._project_files.setdefault(normalized, {})
            seen_files: set[str] = set()

            if os.path.isdir(cache_dir):
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

                        project_stats[rel_key] = _CacheProgressFileStat(
                            mtime_ns=int(stat.st_mtime_ns),
                            size=int(stat.st_size),
                            translated_keys=frozenset(translated_keys),
                            problem_keys=frozenset(problem_keys),
                            failed_keys=frozenset(failed_keys),
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

            for rel_key, stat in project_stats.items():
                # rel_key 可能是 "pass3_cache/xxx.json" 或仅 "xxx.json"；
                # cache_file_display_map 的键总是纯文件名，故取 basename 做映射。
                base_name = os.path.basename(rel_key)
                canonical_name = (
                    base_name[: -len(_CACHE_APPEND_SUFFIX)]
                    if base_name.endswith(_CACHE_APPEND_SUFFIX)
                    else base_name
                )
                display_name = cache_file_display_map.get(canonical_name, canonical_name)
                if file_totals and display_name not in file_totals:
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
                    }
                file_progress_map[display_name]["_translated_keys"].update(stat.translated_keys)
                file_progress_map[display_name]["_problem_keys"].update(stat.problem_keys)
                file_progress_map[display_name]["_failed_keys"].update(stat.failed_keys)
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
                    },
                )

            for file_progress in file_progress_map.values():
                total_count = int(file_progress.get("total", 0))
                translated = len(file_progress["_translated_keys"])
                problems = len(file_progress["_problem_keys"])
                failed = len(file_progress["_failed_keys"])

                if total_count > 0:
                    translated = min(translated, total_count)
                    problems = min(problems, total_count)
                    failed = min(failed, total_count)

                file_progress["translated"] = translated
                file_progress["problems"] = problems
                file_progress["failed"] = failed
                file_progress.pop("_translated_keys", None)
                file_progress.pop("_problem_keys", None)
                file_progress.pop("_failed_keys", None)

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
