"""任务注册表：作业提交、并发控制、取消与执行（0.4.10 从 server.py 抽出）。

JobRegistry 承载后端任务生命周期：按项目去重的并发上限、作业裁剪、停止请求传播，
以及把 JobSpec 交给 Service.run_job 执行。

注意：run_job 在本模块内按模块全局名调用，测试对 GalTransl.server.run_job 的
mock.patch 已同步改到 GalTransl.server_jobs.run_job（跨模块 patch 会静默打空）。
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

from GalTransl import LOGGER, TRANSLATOR_SUPPORTED
from GalTransl import AppSettings
from GalTransl.Service import JobSpec, JobState, create_job_state, run_job
from GalTransl.server_runtime import (
    _ConcurrentLimitError,
    _normalize_project_dir,
    reset_runtime_project,
)
from GalTransl.server_dict import _ensure_project_dict_file_configured


class JobRegistry:
    _MAX_KEPT_JOBS = 200

    def __init__(self, max_workers: int | None = None) -> None:
        self._jobs: dict[str, JobState] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._max_workers = max_workers or AppSettings.load_app_settings().get("maxConcurrentJobs", 4)
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="galtransl-job")

    def _prune_jobs_locked(self) -> None:
        """裁剪已完成的历史 job，保留最近 _MAX_KEPT_JOBS 条（运行中 job 永不删除）。持锁调用。"""
        if len(self._jobs) <= self._MAX_KEPT_JOBS:
            return
        finished = sorted(
            (job for job in self._jobs.values() if job.status not in {"pending", "running"}),
            key=lambda job: job.created_at,
        )
        excess = len(self._jobs) - self._MAX_KEPT_JOBS
        removed = 0
        for job in finished:
            if removed >= excess:
                break
            self._jobs.pop(job.job_id, None)
            removed += 1
        if removed:
            LOGGER.debug(f"[job] 裁剪历史任务: {removed} 条")

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [job.to_dict() for job in self._jobs.values()]
        return sorted(jobs, key=lambda job: job["created_at"], reverse=True)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else job.to_dict()

    def get_project_job(self, project_dir: str) -> JobState | None:
        normalized = _normalize_project_dir(project_dir)
        with self._lock:
            project_jobs = [
                job for job in self._jobs.values()
                if _normalize_project_dir(job.project_dir) == normalized
            ]
        if not project_jobs:
            return None
        active_jobs = [job for job in project_jobs if job.status in {"pending", "running"}]
        if active_jobs:
            active_jobs.sort(key=lambda job: job.created_at, reverse=True)
            return active_jobs[0]
        project_jobs.sort(key=lambda job: job.created_at, reverse=True)
        return project_jobs[0]

    def request_project_stop(self, project_dir: str) -> JobState | None:
        normalized = _normalize_project_dir(project_dir)
        with self._lock:
            active_jobs = [
                job for job in self._jobs.values()
                if _normalize_project_dir(job.project_dir) == normalized and job.status in {"pending", "running"}
            ]
            if not active_jobs:
                return None
            active_jobs.sort(key=lambda job: job.created_at, reverse=True)
            event = self._stop_events.get(normalized)
            if event is not None:
                event.set()
            return active_jobs[0]

    def clear_project_stop(self, project_dir: str) -> None:
        normalized = _normalize_project_dir(project_dir)
        with self._lock:
            self._stop_events.pop(normalized, None)

    def _has_running_job_for_project(self, project_dir: str) -> bool:
        normalized = str(Path(project_dir).resolve())
        for job in self._jobs.values():
            if str(Path(job.project_dir).resolve()) == normalized and job.status in {"pending", "running"}:
                return True
        return False

    def _running_job_count(self) -> int:
        return sum(1 for job in self._jobs.values() if job.status in {"pending", "running"})

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_dir = str(payload.get("project_dir", "")).strip()
        config_file_name = str(payload.get("config_file_name", "config.yaml")).strip() or "config.yaml"
        translator = str(payload.get("translator", "")).strip()
        backend_profile = str(payload.get("backend_profile", "")).strip()
        backend_profile_data = payload.get("backend_profile_data")
        # 提示词模板覆盖：前端按 translator 键控的 system/user prompt 覆盖（非法类型防御性忽略）
        prompt_template_overrides = payload.get("prompt_template_overrides")
        if prompt_template_overrides is not None and not isinstance(prompt_template_overrides, dict):
            LOGGER.warning(
                f"[job] prompt_template_overrides 类型非法，已忽略: {type(prompt_template_overrides).__name__}"
            )
            prompt_template_overrides = {}

        if not project_dir:
            raise ValueError("project_dir is required")
        if not translator:
            raise ValueError("translator is required")
        if translator not in TRANSLATOR_SUPPORTED:
            raise ValueError(f"unsupported translator: {translator}")
        if translator == "GenDic":
            _ensure_project_dict_file_configured(
                project_dir,
                config_file_name,
                "gpt",
                "项目GPT字典-生成.txt",
            )

        # 锁外重置运行时状态：避免 JobRegistry 锁 → runtime 锁的嵌套顺序（submit 已保证同项目不并发提交，无竞态）
        reset_runtime_project(project_dir)

        with self._lock:
            # maxConcurrentJobs 变化后懒重建 executor（PUT /app-settings 只更新数值）：
            # 在途任务留在旧 executor 线程收尾，新任务立即用新上限
            if getattr(self._executor, "_max_workers", 0) != self._max_workers:
                LOGGER.warning(
                    f"[并发] 并发上限已调整，重建任务线程池: "
                    f"{getattr(self._executor, '_max_workers', '?')} -> {self._max_workers}"
                )
                old_executor = self._executor
                self._executor = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="galtransl-job")
                old_executor.shutdown(wait=False)
            if self._has_running_job_for_project(project_dir):
                raise ValueError("the project already has a pending or running job")
            if self._running_job_count() >= self._max_workers:
                raise _ConcurrentLimitError(f"已达到最大并发翻译任务数 ({self._max_workers})，请等待已有任务完成后再试")

            job_id = uuid4().hex[:12]
            spec = JobSpec(
                job_id=job_id,
                project_dir=project_dir,
                config_file_name=config_file_name,
                translator=translator,
                backend_profile=backend_profile,
                backend_profile_data=backend_profile_data if isinstance(backend_profile_data, dict) else {},
                prompt_template_overrides=prompt_template_overrides or {},
            )
            state = create_job_state(spec)
            self._jobs[job_id] = state
            self._stop_events[_normalize_project_dir(project_dir)] = threading.Event()
            self._prune_jobs_locked()
            self._executor.submit(self._execute_job, spec, state)
            return state.to_dict()

    def _execute_job(self, spec: JobSpec, state: JobState) -> None:
        stop_event = self._stop_events.get(_normalize_project_dir(spec.project_dir))
        try:
            run_job(spec, state, stop_event=stop_event)
        finally:
            self.clear_project_stop(spec.project_dir)


