"""
AI API 调用专用日志模块。

功能：
- 以结构化纯文本格式记录每次 API 请求/响应
- 异步写入（asyncio.Queue + 后台 writer task），不阻塞翻译主流程
- 每条记录含唯一 TraceID，可串联请求与响应，便于 grep/tail 追踪
- 记录 token 消耗、延迟、状态、错误详情

用法：
    from GalTransl.ApiLogger import api_logger
    trace_id = api_logger.begin(project_dir, backend=..., model=..., ...)
    api_logger.record(trace_id, status=..., latency_ms=..., ...)

日志文件：{project_dir}/api_calls.log

格式示例：
[07-30 14:00:00][API] b8f3a1d2 >>> gpt4 deepseek-chat stream file=00_01.txt.json
[07-30 14:00:00][API] b8f3a1d2 -REQ tokens=3330
提示词内容行1
提示词内容行2
[07-30 14:00:16][API] b8f3a1d2 -RESP success 1234ms 256t
响应内容行1
响应内容行2
[07-30 14:00:16][API] b8f3a1d2 <<<
"""

import asyncio
import os
import time
import uuid
from typing import Any, Optional

from GalTransl import LOGGER
from GalTransl.AppSettings import load_app_settings


class ApiLogger:
    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._writer_task: Optional[asyncio.Task] = None
        self._file_handle: Any = None
        self._file_path: str = ""

    # ── 公开 API ──

    def begin(
        self,
        project_dir: str,
        *,
        backend: str = "",
        file: str = "",
        model: str = "",
        endpoint: str = "",
        stream: bool = False,
        prompt_preview: str = "",
    ) -> str:
        trace_id = _new_trace_id()
        if self._writer_task is None or self._writer_task.done():
            self._start_writer(project_dir)
        self._queue.put_nowait({
            "_stage": "request",
            "trace_id": trace_id,
            "ts_req": _localtime_str(),
            "backend": backend,
            "file": file,
            "model": model,
            "endpoint": endpoint,
            "stream": stream,
            "prompt": prompt_preview,
        })
        return trace_id

    def record(
        self,
        trace_id: str,
        *,
        status: str = "success",
        latency_ms: float = 0.0,
        retry_count: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        response_preview: str = "",
        reasoning: str = "",
        error: str = "",
    ) -> None:
        if self._writer_task is None or self._writer_task.done():
            return
        self._queue.put_nowait({
            "_stage": "response",
            "trace_id": trace_id,
            "ts_resp": _localtime_str(),
            "status": status,
            "latency_ms": round(latency_ms, 1),
            "retry_count": retry_count,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "response": response_preview,
            "reasoning": reasoning,
            "error": error,
        })

    async def shutdown(self, timeout: float = 5.0) -> None:
        if self._writer_task and not self._writer_task.done():
            await self._queue.put(None)
            try:
                await asyncio.wait_for(self._writer_task, timeout=timeout)
            except asyncio.TimeoutError:
                self._writer_task.cancel()
            except Exception:
                pass
        self._close_file()

    # ── 内部 ──

    def _start_writer(self, project_dir: str) -> None:
        # 按 AppSettings.writeApiCallLog 决定是否落盘 api_calls.log。
        # 关闭时不启动 writer task，record() 因 writer_task 为 None 会自动跳过，安全无副作用。
        if not load_app_settings().get("writeApiCallLog", True):
            LOGGER.debug("按 AppSettings.writeApiCallLog=false，跳过 api_calls.log 写入")
            return
        # 每次启动 writer 都重建队列，使其 _loop 在当前 event loop 首次 get() 时惰性绑定，
        # 避免单例跨多次 asyncio.run（每次新 loop）复用旧 loop 绑定的队列而抛异常。
        self._queue = asyncio.Queue()
        LOGGER.debug("ApiLogger 为新 event loop 重建写入队列")
        self._close_file()
        os.makedirs(project_dir, exist_ok=True)
        self._file_path = os.path.join(project_dir, "api_calls.log")
        try:
            self._file_handle = open(self._file_path, "a", encoding="utf-8", buffering=1)
        except OSError:
            self._file_handle = None
        self._writer_task = asyncio.get_running_loop().create_task(self._writer_loop())

    def _close_file(self) -> None:
        if self._file_handle:
            try:
                self._file_handle.close()
            except Exception:
                pass
            self._file_handle = None

    async def _writer_loop(self) -> None:
        merged: dict[str, dict] = {}
        while True:
            item = await self._queue.get()
            if item is None:
                for entry in merged.values():
                    self._write_combined(entry)
                merged.clear()
                self._close_file()
                return
            tid = item["trace_id"]
            if tid not in merged:
                merged[tid] = {"trace_id": tid}
            merged[tid].update(item)
            del merged[tid]["_stage"]
            if "status" in item:
                self._write_combined(merged.pop(tid))

    # ── 格式化输出 ──

    def _write_combined(self, entry: dict) -> None:
        if not self._file_handle:
            return
        try:
            tid = entry["trace_id"]
            lines: list[str] = []

            # 请求头
            _s = "stream" if entry.get("stream") else "nonstream"
            _f = f' file={entry["file"]}' if entry.get("file") else ""
            lines.append(
                f'[{entry.get("ts_req", "?")}][API] {tid} >>> '
                f'{entry.get("backend","")} {entry.get("model","")}'
                f' {entry.get("endpoint","")} {_s}{_f}'
            )

            # 请求体
            prompt = entry.get("prompt", "")
            if prompt:
                _pt = entry.get("prompt_tokens", 0) or 0
                _pt_info = f" tokens={_pt}" if _pt else ""
                lines.append(f'[{entry.get("ts_resp", "?")}][API] {tid} -REQ{_pt_info}')
                for pline in prompt.split("\n"):
                    lines.append(pline if pline else " ")

            # 响应
            resp = entry.get("response", "")
            err = entry.get("error", "")

            if entry.get("status") == "success":
                _lt = entry.get("latency_ms", 0)
                _ct = entry.get("completion_tokens", 0) or 0
                lines.append(
                    f'[{entry.get("ts_resp", "?")}][API] {tid} -RESP '
                    f'success {_lt}ms {_ct}t'
                    f'{"" if not entry.get("retry_count") else f" retry={entry.get("retry_count")}"}'
                )
                reason = entry.get("reasoning", "") or ""
                if reason:
                    # 思考内容单独标记输出，便于与译文区分
                    lines.append(
                        f'[{entry.get("ts_resp", "?")}][API] {tid} -REASONING'
                    )
                    for rline in reason.split("\n"):
                        lines.append(rline if rline else " ")
                    if resp:
                        lines.append(
                            f'[{entry.get("ts_resp", "?")}][API] {tid} -CONTENT'
                        )
                if resp:
                    for rline in resp.split("\n"):
                        lines.append(rline if rline else " ")
            elif err:
                _lt = entry.get("latency_ms", 0)
                lines.append(
                    f'[{entry.get("ts_resp", "?")}][API] {tid} <<< '
                    f'{entry.get("status", "error")} {_lt}ms '
                    f'retry={entry.get("retry_count",0)}'
                )
                lines.append(err)
            else:
                lines.append(
                    f'[{entry.get("ts_resp", "?")}][API] {tid} <<< '
                    f'{entry.get("status", "?")}'
                )

            # 边界标记
            lines.append("---")

            self._file_handle.write("\n".join(lines) + "\n")
            self._file_handle.flush()
        except Exception:
            pass


# ── 全局单例 ──

api_logger = ApiLogger()


# ── 工具 ──

def _new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def _localtime_str() -> str:
    return time.strftime("%m-%d %H:%M:%S", time.localtime())
