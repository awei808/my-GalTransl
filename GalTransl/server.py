from __future__ import annotations

import argparse
import base64
import email
from email import policy
import json
import re
import threading
import time
from asyncio import run
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import urlparse, parse_qs, unquote
from uuid import uuid4

import os
import subprocess
import sys
from datetime import datetime
from yaml import safe_load, safe_dump

from GalTransl import LOGGER, TRANSLATOR_SUPPORTED, INPUT_FOLDERNAME, OUTPUT_FOLDERNAME, CACHE_FOLDERNAME, GALTRANSL_VERSION, AUTHOR, new_version, NEED_OpenAITokenPool, PASS0_CACHE_DIR, PASS1_CACHE_DIR, PASS2_CACHE_DIR, PASS3_CACHE_DIR, resolve_translator_alias
from GalTransl import ReviewAssist
from GalTransl.Dictionary import parse_dict_line, DictRow, _COMMENT_PREFIXES
from GalTransl.Utils import get_n_symbol
from GalTransl.Service import JobSpec, JobState, create_job_state, run_job
from GalTransl.Cache import CACHE_TEMP_SUFFIX
from GalTransl.AppSettings import load_app_settings, save_app_settings
from GalTransl.DefaultProjectConfig import DEFAULT_PROJECT_CONFIG_YAML
from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProjectConfig
# 兼容别名：历史代码与测试以 _detect_config_file 引用配置探测
from GalTransl.ConfigHelper import detect_config_file as _detect_config_file
from GalTransl.UtilityEngines import UTILITY_ENGINES
from GalTransl.CSplitter import DictionaryCountSplitter, EqualPartsSplitter
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_TRANS_PROMPT,
    FORGAL_JSON_IMPROVE_PROMPT,
    FORGAL_JSON_BRSTATION_PROMPT,
    FORGAL_JSON_JPREPAIR_PROMPT,
    FORPLOTROUTE_PROMPT,
    FORPLOTROUTE_SYSTEM,
    FORFILEMETA_PROMPT,
    FORFILEMETA_SYSTEM,
    FORBATCHMETA_PROMPT,
    FORBATCHMETA_SYSTEM,
    FORGLOBAL_PROMPT,
    FORGLOBAL_SYSTEM,
    FORIMPROVE_SYSTEM,
    FORBR_SYSTEM,
    FORJP_SYSTEM,
    FORBAN_SYSTEM,
    FORTRANS_SYSTEM,
    FORGAL_JSON_BANFIX_PROMPT,
    FORFIXROUND_SYSTEM,
    FORWORDTONE_SYSTEM,
    FORGAL_JSON_FORWORDTONE_PROMPT,
    build_fix_round_prompt,
    GENDIC_PROMPT,
    GENDIC_SYSTEM,
)
from GalTransl.Backend.utils import coerce_h_value, is_h_value


from GalTransl.server_runtime import (
    RUNTIME_PROGRESS_CACHE,
    RUNTIME_REGISTRY,
    RuntimeProgressCache,
    RuntimeRegistry,
    _CACHE_APPEND_SUFFIX,
    _ConcurrentLimitError,
    _has_newer_release,
    _normalize_project_dir,
    _normalize_retran_terms,
    _parse_runtime_job_started_at_ns,
    _safe_project_dir,
    _trim_preview,
    decode_project_dir,
    encode_project_dir,
    clear_runtime_notices,
    record_runtime_error,
    record_runtime_success,
    reset_runtime_project,
    update_runtime_status,
)

from GalTransl.CSerialize import save_json
from GalTransl.backend_security import (
    load_allowed_origins,
    origin_allowed,
    load_api_token,
    token_ok,
    safe_under_project,
)
from GalTransl.server_webui import INDEX_HTML
from GalTransl.server_config_schema import (
    _read_yaml_file,
    _write_yaml_file,
    _parse_yaml_comments,
    _build_config_schema,
    _get_config_schema,
    _CONFIG_SCHEMA_CACHE,
    _get_default_config,
    _DEFAULT_CONFIG_CACHE,
    _DEFAULT_CONFIG_LOCK,
    _deep_merge_defaults,
)
from GalTransl.server_config_schema import reset_caches as _reset_config_caches
from GalTransl.server_backend import (
    _BACKEND_PROFILES_PATH,
    _DEFAULT_TRANSLATOR_PROMPTS,
    _read_backend_profiles,
    _write_backend_profiles,
    _build_prompt_templates_payload,
    _check_model_availability,
    _check_stage_model_availability,
    _STAGE_CHECK_SKIP_MESSAGE,
    _SuggestConfigError,
    _REVIEW_SUGGEST_LOCK,
    _resolve_suggest_backend,
)
from GalTransl.server_dict import (
    DICT_PROJECT_MARKER,
    COMMON_DICT_CATEGORY_MAP,
    _ensure_empty_project_dict_file,
    _migrate_h_check_dict_config,
    _is_safe_dict_filename,
    _is_safe_config_filename,
    _is_path_within,
    _normalize_dict_text,
    _dict_category_config_key,
    _read_dict_file_payload,
    _collect_project_dict_payload,
    _common_dict_directory,
    _ensure_common_dicts_in_config,
    _ensure_project_dict_file_configured,
    _common_dict_category_map_path,
    _read_common_dict_category_map,
    _write_common_dict_category_map,
    _categorize_common_dict_file,
    _collect_common_dict_payload,
)
from GalTransl.server_meta import (
    _PROBLEM_TYPE_CATALOG,
    _NAME_DICT_CACHE,
    _find_name_col,
    _load_project_name_dict,
    _lookup_name,
    _list_problem_types,
    _list_translation_guidelines,
    _scan_plugins,
)
from GalTransl.server_meta import reset_caches as _reset_meta_caches
from GalTransl.server_scaffold import (
    _SAMPLE_CACHE_FILENAME,
    _SAMPLE_CACHE_JSON_CONTENT,
    _SAMPLE_GLOBAL_PROMPT_CONTENT,
    _SAMPLE_FILE_META_CONTENT,
    _SAMPLE_BATCH_META_CONTENT,
    _SAMPLE_PLOT_ROUTE_CONTENT,
    _workspace_root,
    _resolve_new_project_dir,
    _create_project_layout,
    _write_initial_config,
    _write_stage_samples,
)
from GalTransl.server_jobs import JobRegistry
from GalTransl.server_handlers_project import route_project_api
from GalTransl.server_handlers_root import (
    do_get,
    do_post,
    do_put,
    do_delete,
    _LOG_STRIP_TABLE,
    _VALID_LOG_LEVELS,
    _sanitize_log_field,
)
from GalTransl.server_cache import (
    _open_in_file_manager,
    _load_rebuild_deps,
    _rebuild_trans_list_with_postprocess,
    _run_problem_detection,
    recheck_pass3_cache_files,
    _append_engine_log,
    _cache_entry_key,
    _entry_modified,
    _fmt_indices,
    _collect_cache_files,
    _extract_dialogue_symbols,
    _pick_output_dst,
    _build_project_output,
    _normalize_cache_filenames,
    _is_numeric_index,
    _resolve_cache_row_offset,
    _resolve_cache_h_ranges,
    _validate_build,
    _check_batch_size,
    _list_dir_entries,
    _build_cache_tree,
    _REPLACE_FIELD_REJECTED_MSG,
)

# 重置子模块缓存：importlib.reload(server) 不会重新导入子模块，
# 故需在 import 段显式重置，保证 reload 后拿到干净状态（测试隔离依赖此语义）。
_reset_config_caches()
_reset_meta_caches()

_ALLOWED_ORIGINS = load_allowed_origins()
_API_TOKEN = load_api_token()


# 日志字段白名单与清洗：防止通过 level/source 注入伪造日志行


def build_handler(registry: JobRegistry) -> type:
    class RequestHandler(BaseHTTPRequestHandler):
        def end_headers(self) -> None:
            origin = self.headers.get("Origin")
            if origin and origin_allowed(origin, _ALLOWED_ORIGINS):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                # Web 模式（公网前端访问本机服务）的私有网络预检需要此头
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            super().end_headers()

        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()

        # Routing helpers

        def _route_project_api(self, project_id: str, sub_path: str) -> None:
            """委托到 server_handlers_project.route_project_api（0.4.10 拆分）。"""
            route_project_api(self, registry, project_id, sub_path)
        def do_GET(self) -> None:
            """委托到 server_handlers_root.do_get（0.4.10 拆分）。"""
            do_get(self, registry)

        def do_POST(self) -> None:
            """委托到 server_handlers_root.do_post（0.4.10 拆分）。"""
            do_post(self, registry)

        def do_PUT(self) -> None:
            """委托到 server_handlers_root.do_put（0.4.10 拆分）。"""
            do_put(self, registry)

        def do_DELETE(self) -> None:
            """委托到 server_handlers_root.do_delete（0.4.10 拆分）。"""
            do_delete(self)


        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_json_body(self) -> dict[str, Any]:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            data = json.loads(raw_body.decode("utf-8") or "{}")
            if not isinstance(data, dict):
                raise ValueError("json body must be an object")
            return data

        def _read_raw_body(self) -> bytes:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0:
                return b""
            return self.rfile.read(content_length)


        def _require_write_auth(self) -> bool:
            """写端点鉴权：未配置令牌则放行；配置后须 Bearer 匹配，否则回 401。"""
            if token_ok(self.headers.get("Authorization"), _API_TOKEN):
                return True
            self._send_json({"error": "未授权：写操作需要有效的 API 令牌"}, status=HTTPStatus.UNAUTHORIZED)
            return False

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return RequestHandler


def serve(host: str = "127.0.0.1", port: int = 12333) -> None:
    registry = JobRegistry()
    try:
        server = ThreadingHTTPServer((host, port), build_handler(registry))
    except OSError as exc:
        # WinError 10048 / errno 98 (EADDRINUSE) / errno 13 (EACCES on Windows for occupied ports)
        errno_val = getattr(exc, "errno", None)
        winerror = getattr(exc, "winerror", None)
        if errno_val in (48, 98, 10048, 13) or winerror == 10048:
            print(
                f"[错误] 端口 {port} 已被占用，无法启动 GalTransl 后端服务。\n"
                f"       请先关闭占用该端口的程序，或使用 --port 指定其他端口，例如：\n"
                f"       python run_backend.py --host {host} --port {port + 1}"
            )
            raise SystemExit(1)
        print(f"[错误] 无法绑定 {host}:{port} —— {exc}")
        raise SystemExit(1)
    print(f"GalTransl backend mode listening at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser("GalTransl backend mode")
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    parser.add_argument("--port", type=int, default=12333, help="bind port")
    args = parser.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
