"""项目级路由 handler（0.4.10 从 server.py 的 RequestHandler 抽出）。

原 `RequestHandler._route_project_api`（2074 行 / 44 个路由块）整体搬为模块级函数，
**保留原有的 if 链与顺序不变**（顺序敏感：如 /cache/ 前缀兜底必须晚于 /cache/save）。
唯一的机械变换是把方法体内的 `self` 引用改为 `handler` 参数。

调用方（server.py 的 RequestHandler）以 `route_project_api(self, registry, project_id, sub_path)`
委托，故 handler 侧仍使用同一套 `_send_json` / `_read_json_body` / `command` / `path` 语义。

注意：本模块**不得**从 GalTransl.server 导入（会形成循环导入），所有依赖
均从各功能域子模块直接导入。
"""
from __future__ import annotations

import json
import os
import re
from asyncio import run
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from GalTransl import (
    LOGGER,
    CACHE_FOLDERNAME,
    INPUT_FOLDERNAME,
    OUTPUT_FOLDERNAME,
    PASS0_CACHE_DIR,
    PASS1_CACHE_DIR,
    PASS2_CACHE_DIR,
    ReviewAssist,
    resolve_translator_alias,
)
from GalTransl.Cache import CACHE_TEMP_SUFFIX
from GalTransl.ConfigHelper import detect_config_file as _detect_config_file
from GalTransl.server_runtime import (
    RUNTIME_PROGRESS_CACHE,
    RUNTIME_REGISTRY,
    _ConcurrentLimitError,
    _normalize_retran_terms,
    _parse_runtime_job_started_at_ns,
    _safe_project_dir,
    clear_runtime_notices,
    decode_project_dir,
    encode_project_dir,
)
from GalTransl.server_config_schema import (
    _deep_merge_defaults,
    _get_config_schema,
    _get_default_config,
    _read_yaml_file,
    _write_yaml_file,
)
from GalTransl.server_backend import (
    _REVIEW_SUGGEST_LOCK,
    _SuggestConfigError,
    _check_model_availability,
    _read_backend_profiles,
    _resolve_suggest_backend,
)
from GalTransl.server_dict import (
    DICT_PROJECT_MARKER,
    _collect_project_dict_payload,
    _dict_category_config_key,
    _is_safe_config_filename,
    _is_safe_dict_filename,
    _normalize_dict_text,
    _read_dict_file_payload,
)
from GalTransl.server_meta import _load_project_name_dict, _lookup_name
from GalTransl.server_cache import (
    _REPLACE_FIELD_REJECTED_MSG,
    _append_engine_log,
    _build_cache_tree,
    _build_project_output,
    _cache_entry_key,
    _check_batch_size,
    _collect_cache_files,
    _entry_modified,
    _fmt_indices,
    _list_dir_entries,
    _load_rebuild_deps,
    _open_in_file_manager,
    _resolve_cache_h_ranges,
    _run_problem_detection,
    _validate_build,
    recheck_pass3_cache_files,
)
from GalTransl.server_scaffold import _workspace_root
from GalTransl.server_jobs import JobRegistry
from GalTransl.server_handlers_project2 import route_project_api_part2
from GalTransl.server_handlers_root import handle_import_files


def route_project_api(handler: Any, registry: JobRegistry, project_id: str, sub_path: str) -> None:
    """Handle /api/projects/:id/* routes."""
    try:
        project_dir = _safe_project_dir(project_id)
    except ValueError as exc:
        handler._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        return

    # GET /api/projects/:id/config
    if sub_path == "/config":
        config_name = parse_qs(urlparse(handler.path).query).get("config", ["config.yaml"])[0]
        config_path = os.path.join(project_dir, config_name)
        if not os.path.isfile(config_path):
            handler._send_json({"error": f"config file not found: {config_name}"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            # 深合并默认模板：补齐旧项目缺失的新配置段（只补不覆盖），
            # 使设置界面能显示并编辑升级后新增的配置项。
            data = _deep_merge_defaults(
                _read_yaml_file(config_path), _get_default_config()
            )
            handler._send_json({"config": data, "project_dir": project_dir, "config_file_name": config_name})
        except Exception as exc:
            handler._send_json({"error": f"failed to read config: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # GET /api/projects/:id/config-name
    # 探测项目目录下真实配置文件名，供前端贯通真实配置名（避免写死 config.yaml）。
    if sub_path == "/config-name":
        try:
            config_name = _detect_config_file(project_dir)
            handler._send_json({"project_dir": project_dir, "config_file_name": config_name})
        except Exception as exc:
            handler._send_json({"error": f"failed to detect config: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # GET /api/projects/:id/config-schema
    # 返回配置参数路径→注释描述映射，供前端设置界面显示参数解释。
    if sub_path == "/config-schema":
        schema = _get_config_schema()
        handler._send_json({"project_dir": project_dir, **schema})
        return

    # POST /api/projects/:id/check-model
    # 主动检测所选后端的模型 / token 可用性。请求体:
    #   {"translator": "ForGal-json-translate",
    #    "config_file_name": "config.yaml"}
    if sub_path == "/check-model":
        if handler.command != "POST":
            handler._send_json(
                {"error": "method not allowed"},
                status=HTTPStatus.METHOD_NOT_ALLOWED,
            )
            return
        payload = handler._read_json_body()
        # 旧引擎名别名解析，保证旧客户端的可用性检测口径与真实任务一致
        translator = resolve_translator_alias(str(payload.get("translator", "")).strip())
        config_file_name = (
            str(payload.get("config_file_name", "config.yaml")).strip()
            or "config.yaml"
        )
        backend_profile = str(payload.get("backend_profile", "")).strip()
        backend_profile_data = payload.get("backend_profile_data")
        if not translator:
            handler._send_json(
                {"error": "translator is required"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        try:
            result = run(
                _check_model_availability(
                    project_dir,
                    translator,
                    config_file_name,
                    backend_profile=backend_profile,
                    backend_profile_data=backend_profile_data
                    if isinstance(backend_profile_data, dict)
                    else None,
                )
            )
            handler._send_json(result)
        except Exception as exc:
            handler._send_json(
                {"error": f"模型可用性检测失败: {exc}"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        return

    # POST /api/projects/:id/check-batch-size
    # 批次划分预检：计算最大可自然划分文件行数（0.9 * max_batch_size * max_batches），
    # 返回行数超限的待翻译文件列表，供前端弹窗确认是否继续划分。
    # 请求体: {"translator": "ForGal-full-pipeline", "config_file_name": "config.yaml"}
    if sub_path == "/check-batch-size":
        if handler.command != "POST":
            handler._send_json(
                {"error": "method not allowed"},
                status=HTTPStatus.METHOD_NOT_ALLOWED,
            )
            return
        payload = handler._read_json_body()
        translator = str(payload.get("translator", "")).strip()
        config_file_name = (
            str(payload.get("config_file_name", "config.yaml")).strip()
            or "config.yaml"
        )
        result: dict[str, Any] = {
            "max_natural_lines": 0,
            "oversize_files": [],
            "applicable": False,
        }
        if translator not in ("ForGal-full-pipeline", "ForBatchMetaData"):
            handler._send_json(result)
            return
        try:
            result = _check_batch_size(project_dir, config_file_name)
        except Exception as exc:
            handler._send_json(
                {"error": f"批次划分预检失败: {exc}"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        handler._send_json(result)
        return

    # POST /api/projects/:id/build/validate — 构建前校验（仅提示，不阻断构建）
    if sub_path == "/build/validate":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        filenames: list[str] | None = None
        content_length = int(handler.headers.get("Content-Length", "0"))
        if content_length > 0:
            try:
                payload = handler._read_json_body()
                filenames = payload.get("filenames")
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
        result = _validate_build(project_dir, filenames=filenames)
        handler._send_json(result)
        return

    # POST /api/projects/:id/build-output
    # 从缓存文件构建输出文件。支持全量构建和单个文件构建。
    # 请求体可选: {"filenames": ["01_intro.json", ...]} 仅构建指定文件
    # 无请求体 / 空 filenames：构建所有缓存文件
    if sub_path == "/build-output":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        # 翻译任务运行中读取缓存构建输出可能读到 worker 半写状态，拒绝执行（与 recheck-all 一致）
        job = registry.get_project_job(project_dir)
        if job is not None and job.status in {"pending", "running"}:
            LOGGER.warning(f"[cache] 构建输出被拒绝（翻译任务运行中）：{project_dir}")
            handler._send_json(
                {"success": False, "error": "翻译进行中，请停止翻译后再构建输出"},
                status=HTTPStatus.CONFLICT,
            )
            return
        filenames: list[str] | None = None
        content_length = int(handler.headers.get("Content-Length", "0"))
        if content_length > 0:
            try:
                payload = handler._read_json_body()
                filenames = payload.get("filenames")
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
        result = _build_project_output(project_dir, filenames=filenames)
        handler._send_json(result)
        return

    # POST /api/projects/:id/build-output/<filename>
    # 构建单个文件
    if sub_path.startswith("/build-output/"):
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        # 翻译任务运行中拒绝（与 recheck-all 一致）
        job = registry.get_project_job(project_dir)
        if job is not None and job.status in {"pending", "running"}:
            LOGGER.warning(f"[cache] 构建输出被拒绝（翻译任务运行中）：{project_dir}")
            handler._send_json(
                {"success": False, "error": "翻译进行中，请停止翻译后再构建输出"},
                status=HTTPStatus.CONFLICT,
            )
            return
        filename = sub_path[len("/build-output/"):]
        if not filename:
            handler._send_json({"error": "filename is required"}, status=HTTPStatus.BAD_REQUEST)
            return
        result = _build_project_output(project_dir, filenames=[filename])
        handler._send_json(result)
        return

    # POST /api/projects/:id/import
    # 导入源文件到 gt_input；已存在的同名文件跳过并返回 skipped（去重，避免磁盘覆盖）
    if sub_path == "/import":
        handle_import_files(handler, project_dir)
        return

    # GET /api/projects/:id/files
    if sub_path == "/files":
        input_dir = os.path.join(project_dir, INPUT_FOLDERNAME)
        output_dir = os.path.join(project_dir, OUTPUT_FOLDERNAME)
        cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
        cache_files = _build_cache_tree(cache_dir)
        handler._send_json({
            "project_dir": project_dir,
            "input_dir": input_dir,
            "output_dir": output_dir,
            "cache_dir": cache_dir,
            "input_files": _list_dir_entries(input_dir),
            "output_files": _list_dir_entries(output_dir),
            "cache_files": cache_files,
        })
        return

    # POST /api/projects/:id/reveal — 在系统文件管理器中定位（文件）/ 打开（文件夹）
    if sub_path == "/reveal":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        try:
            payload = handler._read_json_body()
            rel = str(payload.get("path", "")).strip()
            is_metadata = bool(payload.get("is_metadata", False))
            if not rel:
                handler._send_json({"error": "missing path"}, status=HTTPStatus.BAD_REQUEST)
                return
            cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
            if is_metadata:
                # 元数据文件位于 gt_input，而非 cache_dir（与 GET /files 的兼容追加一致）
                norm = os.path.normpath(rel.replace("\\", "/"))
                if norm == ".." or norm.startswith(".." + os.sep) or os.path.isabs(norm) or "/" in norm or os.sep in norm:
                    handler._send_json({"error": "invalid path"}, status=HTTPStatus.BAD_REQUEST)
                    return
                target = os.path.join(project_dir, INPUT_FOLDERNAME, norm)
                allowed_root = os.path.abspath(os.path.join(project_dir, INPUT_FOLDERNAME))
            else:
                norm = os.path.normpath(rel.replace("\\", "/"))
                if norm == ".." or norm.startswith(".." + os.sep) or os.path.isabs(norm):
                    handler._send_json({"error": "invalid path"}, status=HTTPStatus.BAD_REQUEST)
                    return
                target = os.path.join(cache_dir, norm)
                allowed_root = os.path.abspath(cache_dir)
            abs_target = os.path.abspath(target)
            if not (abs_target == allowed_root or abs_target.startswith(allowed_root + os.sep)):
                handler._send_json({"error": "invalid path"}, status=HTTPStatus.BAD_REQUEST)
                return
            if not (os.path.isfile(target) or os.path.isdir(target)):
                handler._send_json({"error": f"路径不存在: {rel}"}, status=HTTPStatus.NOT_FOUND)
                return
            _open_in_file_manager(target, os.path.isfile(target))
            handler._send_json({"success": True, "path": target})
        except Exception as exc:
            handler._send_json({"error": f"打开文件管理器失败: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # POST /api/projects/:id/review/ai-suggest — 校对页单句 AI 建议译文（一次性，不落盘）
    if sub_path == "/review/ai-suggest":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        try:
            payload = handler._read_json_body()
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
            return
        file_name = str(payload.get("file", "")).strip()
        instruction = str(payload.get("instruction", "") or "").strip()
        draft = str(payload.get("draft", "") or "")
        try:
            index = int(payload.get("index"))
        except (TypeError, ValueError):
            handler._send_json({"error": "index must be an integer"}, status=HTTPStatus.BAD_REQUEST)
            return
        if not file_name:
            handler._send_json({"error": "missing file"}, status=HTTPStatus.BAD_REQUEST)
            return
        if registry._has_running_job_for_project(project_dir):
            handler._send_json({"error": "翻译任务运行中，请先停止任务再使用 AI 建议"}, status=HTTPStatus.CONFLICT)
            return
        if not _REVIEW_SUGGEST_LOCK.acquire(blocking=False):
            handler._send_json({"error": "已有一个 AI 建议请求进行中，请稍候"}, status=HTTPStatus.CONFLICT)
            return
        try:
            # 读缓存条目（路径穿越防护与 /cache/:filename 一致）
            norm = os.path.normpath(file_name.replace("\\", "/"))
            if norm == ".." or norm.startswith(".." + os.sep) or os.path.isabs(norm):
                handler._send_json({"error": "invalid cache path"}, status=HTTPStatus.BAD_REQUEST)
                return
            cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
            file_path = os.path.join(cache_dir, norm)
            abs_cache = os.path.abspath(cache_dir)
            abs_file = os.path.abspath(file_path)
            if not (abs_file == abs_cache or abs_file.startswith(abs_cache + os.sep)):
                handler._send_json({"error": "invalid cache path"}, status=HTTPStatus.BAD_REQUEST)
                return
            if not os.path.isfile(file_path):
                handler._send_json({"error": f"cache file not found: {file_name}"}, status=HTTPStatus.NOT_FOUND)
                return
            import orjson
            with open(file_path, "rb") as f:
                cache_entries = orjson.loads(f.read())
            # 按条目序号定位（容错跳过 index 非法的坏行，不让单条坏数据拖垮整个请求）
            pos = next(
                (
                    p for p, e in enumerate(cache_entries)
                    if isinstance(e, dict)
                    and str(e.get("index", "")).strip().lstrip("-").isdigit()
                    and int(e.get("index", -1)) == index
                ),
                None,
            )
            if pos is None:
                handler._send_json({"error": f"条目不存在: {file_name}#{index}"}, status=HTTPStatus.NOT_FOUND)
                return
            entry = cache_entries[pos]
            prev_entry = cache_entries[pos - 1] if pos > 0 else {}
            next_entry = cache_entries[pos + 1] if pos + 1 < len(cache_entries) else {}

            api_key, base_url, model = _resolve_suggest_backend(payload, project_dir)
            messages = ReviewAssist.build_suggest_messages(
                str(entry.get("pre_src", "") or ""),
                draft if draft else str(entry.get("proofread_dst", "") or entry.get("pre_dst", "") or ""),
                prev_dst=str(prev_entry.get("pre_dst", "") or ""),
                next_dst=str(next_entry.get("pre_dst", "") or ""),
                problem=str(entry.get("problem", "") or ""),
                doub_content=str(entry.get("doub_content", "") or ""),
                instruction=instruction,
            )
            raw = ReviewAssist.request_suggestion(
                messages, api_key=api_key, base_url=base_url, model=model
            )
            suggestion = ReviewAssist.extract_suggestion(raw)
            if not suggestion:
                handler._send_json({"error": "AI 返回了空建议，请重试"}, status=HTTPStatus.BAD_GATEWAY)
                return
            LOGGER.info(f"AI 建议: {norm}#{index} model={model} 建议 {len(suggestion)} 字")
            handler._send_json({"suggestion": suggestion, "model": model})
        except _SuggestConfigError as exc:
            handler._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            LOGGER.warning(f"AI 建议失败: {file_name}#{index}: {exc}")
            handler._send_json({"error": f"AI 建议失败: {exc}"}, status=HTTPStatus.BAD_GATEWAY)
        finally:
            _REVIEW_SUGGEST_LOCK.release()
        return

    # GET /api/projects/:id/cache
    if sub_path == "/cache":
        cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
        handler._send_json({
            "project_dir": project_dir,
            "cache_dir": cache_dir,
            "files": _list_dir_entries(
                cache_dir, count_json_entries=True, skip_suffixes=(CACHE_TEMP_SUFFIX,)
            ),
        })
        return

    # POST /api/projects/:id/cache/check — 重新运行问题检测；persist=true 时写回缓存文件（单文件范围）
    if sub_path == "/cache/check":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        try:
            payload = handler._read_json_body()
            raw_filename = str(payload.get("filename", "")).strip()
            entries = payload.get("entries", [])
            config_name = str(payload.get("config_file_name", "config.yaml")).strip() or "config.yaml"
            persist = bool(payload.get("persist", False))
            if not raw_filename or not isinstance(entries, list):
                handler._send_json({"error": "invalid payload"}, status=HTTPStatus.BAD_REQUEST)
                return
            norm = os.path.normpath(raw_filename.replace("\\", "/"))
            if norm == ".." or norm.startswith(".." + os.sep) or os.path.isabs(norm):
                handler._send_json({"error": "invalid cache path"}, status=HTTPStatus.BAD_REQUEST)
                return
            cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
            abs_cache = os.path.abspath(cache_dir)
            abs_file = os.path.abspath(os.path.join(cache_dir, norm))
            if not (abs_file == abs_cache or abs_file.startswith(abs_cache + os.sep)):
                handler._send_json({"error": "invalid cache path"}, status=HTTPStatus.BAD_REQUEST)
                return
            proj_config, pre_dic, post_dic, gpt_dic, tPlugins, h_check_words, forbidden_words = _load_rebuild_deps(
                project_dir, config_name
            )
            if proj_config is None:
                LOGGER.warning(f"cache/check skipped (config load failed): {norm}")
                handler._send_json({"success": False, "error": "config load failed", "results": []})
                return
            h_ranges = [
                (r["lo"], r["hi"])
                for r in _resolve_cache_h_ranges(project_dir, norm).get("h_ranges", [])
            ]
            results, ok = _run_problem_detection(
                entries, proj_config, pre_dic, post_dic, gpt_dic, tPlugins, h_ranges, h_check_words, forbidden_words
            )
            if persist and ok:
                # 写回缓存文件（单文件范围）：合并 problem / post_dst_preview / skip_check
                file_path = os.path.join(cache_dir, norm)
                if os.path.isfile(file_path):
                    import orjson
                    for e, r in zip(entries, results):
                        post_src_val = e.get("post_src", "") or e.get("post_jp", "")
                        if post_src_val == "":
                            continue
                        if r["problem"]:
                            e["problem"] = r["problem"]
                        elif "problem" in e:
                            del e["problem"]
                        e["post_dst_preview"] = r["post_dst_preview"]
                        if r["skip_check"]:
                            e["skip_check"] = True
                        elif "skip_check" in e:
                            del e["skip_check"]
                    with open(file_path, "wb") as f:
                        f.write(orjson.dumps(entries, option=orjson.OPT_INDENT_2))
            handler._send_json({"success": ok, "filename": norm, "results": results, "persisted": persist and ok})
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to check cache file: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # POST /api/projects/:id/cache/recheck-all — 全缓存重检（pass3_cache 下所有 *.json）
    if sub_path == "/cache/recheck-all":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        try:
            payload = handler._read_json_body()
            config_name = str(payload.get("config_file_name", "config.yaml")).strip() or "config.yaml"
            # 翻译任务运行中重写缓存会与增量写入/快照互相覆盖，拒绝执行
            job = registry.get_project_job(project_dir)
            if job is not None and job.status in {"pending", "running"}:
                LOGGER.warning(f"[cache] 全缓存重检被拒绝（翻译任务运行中）：{project_dir}")
                handler._send_json(
                    {"success": False, "error": "翻译进行中，请停止翻译后再重检"},
                    status=HTTPStatus.CONFLICT,
                )
                return
            proj_config, pre_dic, post_dic, gpt_dic, tPlugins, h_check_words, forbidden_words = _load_rebuild_deps(
                project_dir, config_name
            )
            if proj_config is None:
                LOGGER.warning(f"[cache] 全缓存重检跳过（配置加载失败）：{project_dir}")
                handler._send_json({"success": False, "error": "config load failed"})
                return
            cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
            LOGGER.info(f"[cache] 全缓存重检开始：{project_dir}")
            rechecked = recheck_pass3_cache_files(
                cache_dir, proj_config, pre_dic, post_dic, gpt_dic, tPlugins, h_check_words, forbidden_words
            )
            LOGGER.info(f"[cache] 全缓存重检完成：{rechecked} 个文件已写回")
            handler._send_json({"success": True, "rechecked": rechecked})
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            LOGGER.error(f"[cache] 全缓存重检失败：{exc}")
            handler._send_json({"error": f"failed to recheck all cache files: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # POST /api/projects/:id/cache/save
    if sub_path == "/cache/save":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        # 翻译任务运行中全量覆写缓存会与 worker 增量写盘互相覆盖丢数据，拒绝执行（与 recheck-all 一致）
        job = registry.get_project_job(project_dir)
        if job is not None and job.status in {"pending", "running"}:
            LOGGER.warning(f"[cache] 缓存保存被拒绝（翻译任务运行中）：{project_dir}")
            handler._send_json(
                {"success": False, "error": "翻译进行中，请停止翻译后再保存缓存"},
                status=HTTPStatus.CONFLICT,
            )
            return
        try:
            payload = handler._read_json_body()
            raw_filename = str(payload.get("filename", "")).strip()
            entries = payload.get("entries", [])
            config_name = str(payload.get("config_file_name", "config.yaml")).strip() or "config.yaml"

            if not raw_filename:
                handler._send_json({"error": f"invalid cache filename (empty)"}, status=HTTPStatus.BAD_REQUEST)
                return

            # 允许相对子路径（如 pass1_cache/文件名.meta.json），与 GET 行为一致
            norm = os.path.normpath(raw_filename.replace("\\", "/"))
            if norm == ".." or norm.startswith(".." + os.sep) or os.path.isabs(norm):
                handler._send_json({"error": "invalid cache path"}, status=HTTPStatus.BAD_REQUEST)
                return

            cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
            file_path = os.path.join(cache_dir, norm)
            abs_cache = os.path.abspath(cache_dir)
            abs_file = os.path.abspath(file_path)
            if not (abs_file == abs_cache or abs_file.startswith(abs_cache + os.sep)):
                handler._send_json({"error": "invalid cache path"}, status=HTTPStatus.BAD_REQUEST)
                return

            if not os.path.isfile(file_path):
                handler._send_json({"error": f"cache file not found: {raw_filename} (norm:{norm})"}, status=HTTPStatus.NOT_FOUND)
                return

            import orjson
            # 写盘前对比旧文件，记录本次保存修改/删除/交换了哪些条目（审计日志）
            modified_indices: list[Any] = []
            deleted_indices: list[Any] = []
            swapped_indices: list[Any] = []
            try:
                with open(file_path, "rb") as _old_f:
                    old_entries = orjson.loads(_old_f.read())
                if isinstance(old_entries, list) and isinstance(entries, list):
                    old_map = {_cache_entry_key(e): e for e in old_entries}
                    new_map = {_cache_entry_key(e): e for e in entries}
                    for k, oe in old_map.items():
                        if k not in new_map:
                            deleted_indices.append(oe.get("index"))
                    for k, ne in new_map.items():
                        if k in old_map:
                            oe = old_map[k]
                            # 交换备选译文：新 pre_dst == 旧 alt_dst 且 新 alt_dst == 旧 pre_dst
                            is_swap = bool(
                                oe.get("alt_dst")
                                and ne.get("pre_dst") == oe.get("alt_dst")
                                and ne.get("alt_dst") == oe.get("pre_dst")
                            )
                            if is_swap:
                                swapped_indices.append(ne.get("index"))
                            elif _entry_modified(oe, ne):
                                modified_indices.append(ne.get("index"))
            except Exception:
                pass
            with open(file_path, "wb") as f:
                f.write(orjson.dumps(entries, option=orjson.OPT_INDENT_2))
            if modified_indices or deleted_indices or swapped_indices:
                _audit_parts = [
                    f"修改 {len(modified_indices)} 条 {_fmt_indices(modified_indices)}",
                    f"删除 {len(deleted_indices)} 条 {_fmt_indices(deleted_indices)}",
                ]
                if swapped_indices:
                    _audit_parts.append(
                        f"交换备选译文 {len(swapped_indices)} 条 "
                        f"{_fmt_indices(swapped_indices)}"
                    )
                _append_engine_log(
                    project_dir,
                    f"[cache] 保存缓存文件 {norm}：" + "，".join(_audit_parts),
                )

            # Rebuild: re-derive problem and post_dst_preview fields
            try:
                proj_config, pre_dic, post_dic, gpt_dic, tPlugins, h_check_words, forbidden_words = _load_rebuild_deps(
                    project_dir, config_name
                )
                # If config could not be loaded, do NOT run the rebuild:
                # find_problems would be skipped and the update loop would
                # delete every existing "problem" field. Preserve them instead.
                if proj_config is None:
                    LOGGER.warning(f"cache/save rebuild skipped (config load failed): {norm}")
                    handler._send_json({"success": True, "filename": raw_filename})
                    return
                h_ranges = [
                    (r["lo"], r["hi"])
                    for r in _resolve_cache_h_ranges(project_dir, norm).get("h_ranges", [])
                ]
                results, detection_ok = _run_problem_detection(
                    entries, proj_config, pre_dic, post_dic, gpt_dic, tPlugins, h_ranges, h_check_words, forbidden_words
                )
                # Update entries with problem and post_dst_preview.
                # detection_ok=False 时保留已有 problem，避免把检测结果误删。
                for e, r in zip(entries, results):
                    post_src_val = e.get("post_src", "") or e.get("post_jp", "")
                    if post_src_val == "":
                        continue
                    if detection_ok:
                        if r["problem"]:
                            e["problem"] = r["problem"]
                        elif "problem" in e:
                            del e["problem"]
                    e["post_dst_preview"] = r["post_dst_preview"]
                    if r["skip_check"]:
                        e["skip_check"] = True
                    elif "skip_check" in e:
                        del e["skip_check"]

                # Re-save with updated fields
                with open(file_path, "wb") as f:
                    f.write(orjson.dumps(entries, option=orjson.OPT_INDENT_2))

                handler._send_json({"success": True, "filename": norm, "entries": entries})
            except Exception:
                # Rebuild failed, but original save succeeded
                LOGGER.warning(f"cache/save rebuild failed for {norm}", exc_info=True)
                handler._send_json({"success": True, "filename": norm})
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to save cache file: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # ── 元数据(JSON)读取/保存（per-file 模式）──
    # 文件级元数据存储为 pass1_cache/{filename}.meta.json
    # 批次级元数据存储为 pass2_cache/{filename}.batch.json
    # 全局提示词仍为 pass0_cache/GlobalPrompt.json

    # GET/POST /api/projects/:id/metadata/filemeta/:filename
    if sub_path.startswith("/metadata/filemeta/"):
        _filename = unquote(sub_path[len("/metadata/filemeta/"):])
        if not _filename:
            handler._send_json({"error": "filename required"}, status=HTTPStatus.BAD_REQUEST)
            return
        # 与字典端点一致的路径穿越防护：拒绝 . / .. / 含分隔符的文件名，
        # 否则 os.path.join 会把 .meta.json 写到 pass1_cache 之外
        if not _is_safe_dict_filename(_filename):
            handler._send_json({"error": "invalid metadata filename"}, status=HTTPStatus.BAD_REQUEST)
            return
        _meta_path = os.path.join(project_dir, CACHE_FOLDERNAME, PASS1_CACHE_DIR, f"{_filename}.meta.json")

        if handler.command == "GET":
            if not os.path.isfile(_meta_path):
                handler._send_json({"exists": False, "type": "filemeta", "filename": _filename, "entry": None, "path": _meta_path})
                return
            try:
                with open(_meta_path, "r", encoding="utf-8") as f:
                    entry = json.load(f)
            except Exception as e:
                handler._send_json({"error": f"读取元数据失败: {e}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            handler._send_json({"exists": True, "type": "filemeta", "filename": _filename, "entry": entry, "path": _meta_path})
            return

        if handler.command == "POST":
            try:
                payload = handler._read_json_body()
                entry = payload.get("entry", payload)
                if not isinstance(entry, dict):
                    handler._send_json({"error": "entry must be a JSON object"}, status=HTTPStatus.BAD_REQUEST)
                    return
                os.makedirs(os.path.dirname(_meta_path), exist_ok=True)
                with open(_meta_path, "w", encoding="utf-8") as f:
                    json.dump(entry, f, ensure_ascii=False, indent=2)
                handler._send_json({"success": True, "type": "filemeta", "filename": _filename, "path": _meta_path})
            except json.JSONDecodeError:
                handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                handler._send_json({"error": f"保存元数据失败: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
        return

    # GET/POST /api/projects/:id/metadata/batchmeta/:filename
    if sub_path.startswith("/metadata/batchmeta/"):
        _filename = unquote(sub_path[len("/metadata/batchmeta/"):])
        if not _filename:
            handler._send_json({"error": "filename required"}, status=HTTPStatus.BAD_REQUEST)
            return
        # 与字典端点一致的路径穿越防护：拒绝 . / .. / 含分隔符的文件名
        if not _is_safe_dict_filename(_filename):
            handler._send_json({"error": "invalid metadata filename"}, status=HTTPStatus.BAD_REQUEST)
            return
        _meta_path = os.path.join(project_dir, CACHE_FOLDERNAME, PASS2_CACHE_DIR, f"{_filename}.batch.json")

        if handler.command == "GET":
            if not os.path.isfile(_meta_path):
                handler._send_json({"exists": False, "type": "batchmeta", "filename": _filename, "entry": None, "path": _meta_path})
                return
            try:
                with open(_meta_path, "r", encoding="utf-8") as f:
                    entry = json.load(f)
            except Exception as e:
                handler._send_json({"error": f"读取元数据失败: {e}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            handler._send_json({"exists": True, "type": "batchmeta", "filename": _filename, "entry": entry, "path": _meta_path})
            return

        if handler.command == "POST":
            try:
                payload = handler._read_json_body()
                entry = payload.get("entry", payload)
                if not isinstance(entry, dict):
                    handler._send_json({"error": "entry must be a JSON object"}, status=HTTPStatus.BAD_REQUEST)
                    return
                os.makedirs(os.path.dirname(_meta_path), exist_ok=True)
                with open(_meta_path, "w", encoding="utf-8") as f:
                    json.dump(entry, f, ensure_ascii=False, indent=2)
                handler._send_json({"success": True, "type": "batchmeta", "filename": _filename, "path": _meta_path})
            except json.JSONDecodeError:
                handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                handler._send_json({"error": f"保存元数据失败: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
        return

    # GET/POST /api/projects/:id/metadata/globalprompt  （GlobalPrompt 保留原有行为）
    if sub_path == "/metadata/globalprompt" or sub_path == "/metadata/globalprompt/":
        _meta_path = os.path.join(project_dir, CACHE_FOLDERNAME, PASS0_CACHE_DIR, "GlobalPrompt.json")

        if handler.command == "GET":
            if not os.path.isfile(_meta_path):
                handler._send_json({"exists": False, "type": "globalprompt", "entry": None, "path": _meta_path})
                return
            try:
                with open(_meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                handler._send_json({"error": f"读取元数据失败: {e}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            entry = data if isinstance(data, dict) else {}
            handler._send_json({"exists": True, "type": "globalprompt", "entry": entry, "path": _meta_path})
            return

        if handler.command == "POST":
            try:
                payload = handler._read_json_body()
                entry = payload.get("entry", payload)
                if not isinstance(entry, dict):
                    handler._send_json({"error": "entry must be a JSON object"}, status=HTTPStatus.BAD_REQUEST)
                    return
                os.makedirs(os.path.dirname(_meta_path), exist_ok=True)
                with open(_meta_path, "w", encoding="utf-8") as f:
                    json.dump(entry, f, ensure_ascii=False, indent=2)
                handler._send_json({"success": True, "type": "globalprompt", "path": _meta_path})
            except json.JSONDecodeError:
                handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                handler._send_json({"error": f"保存元数据失败: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
        return

    # GET/POST /api/projects/:id/metadata/plotroute  （剧情路线图，与 GlobalPrompt 并列）
    if sub_path == "/metadata/plotroute" or sub_path == "/metadata/plotroute/":
        _meta_path = os.path.join(project_dir, CACHE_FOLDERNAME, PASS0_CACHE_DIR, "PlotRouteMap.json")

        if handler.command == "GET":
            if not os.path.isfile(_meta_path):
                handler._send_json({"exists": False, "type": "plotroute", "entry": None, "path": _meta_path})
                return
            try:
                with open(_meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                handler._send_json({"error": f"读取元数据失败: {e}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            entry = data if isinstance(data, dict) else {}
            handler._send_json({"exists": True, "type": "plotroute", "entry": entry, "path": _meta_path})
            return

        if handler.command == "POST":
            try:
                payload = handler._read_json_body()
                entry = payload.get("entry", payload)
                if not isinstance(entry, dict):
                    handler._send_json({"error": "entry must be a JSON object"}, status=HTTPStatus.BAD_REQUEST)
                    return
                os.makedirs(os.path.dirname(_meta_path), exist_ok=True)
                tmp_path = _meta_path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(entry, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, _meta_path)
                handler._send_json({"success": True, "type": "plotroute", "path": _meta_path})
            except json.JSONDecodeError:
                handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                handler._send_json({"error": f"保存元数据失败: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
        return

    # POST /api/projects/:id/cache/delete-entry
    if sub_path == "/cache/delete-entry":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        # 翻译任务运行中改写缓存会与 worker 增量写盘互相覆盖丢数据，拒绝执行（与 recheck-all 一致）
        job = registry.get_project_job(project_dir)
        if job is not None and job.status in {"pending", "running"}:
            LOGGER.warning(f"[cache] 缓存删除被拒绝（翻译任务运行中）：{project_dir}")
            handler._send_json(
                {"success": False, "error": "翻译进行中，请停止翻译后再删除缓存条目"},
                status=HTTPStatus.CONFLICT,
            )
            return
        try:
            payload = handler._read_json_body()
            filename = str(payload.get("filename", "")).strip()
            entry_index = int(payload.get("index", -1))

            if not filename or filename != os.path.basename(filename):
                handler._send_json({"error": "invalid cache filename"}, status=HTTPStatus.BAD_REQUEST)
                return

            cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
            file_path = os.path.join(cache_dir, filename)
            if not os.path.isfile(file_path):
                handler._send_json({"error": f"cache file not found: {filename}"}, status=HTTPStatus.NOT_FOUND)
                return

            import orjson
            with open(file_path, "rb") as f:
                data = orjson.loads(f.read())
            if not isinstance(data, list) or entry_index < 0 or entry_index >= len(data):
                handler._send_json({"error": "invalid entry index"}, status=HTTPStatus.BAD_REQUEST)
                return
            deleted = data.pop(entry_index)
            with open(file_path, "wb") as f:
                f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
            _append_engine_log(
                project_dir, f"[cache] 删除条目 {filename} index={entry_index}"
            )
            handler._send_json({"success": True, "filename": filename, "deleted_index": entry_index})
        except (json.JSONDecodeError, ValueError):
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to delete cache entry: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # POST /api/projects/:id/cache/delete-file
    if sub_path == "/cache/delete-file":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        try:
            payload = handler._read_json_body()
            filenames = payload.get("filenames", [])
            if not isinstance(filenames, list) or not filenames:
                handler._send_json({"error": "filenames must be a non-empty list"}, status=HTTPStatus.BAD_REQUEST)
                return

            cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
            abs_cache = os.path.abspath(cache_dir)
            deleted_files = []
            not_found_files = []
            for rel in filenames:
                rel = str(rel).strip()
                if not rel:
                    not_found_files.append(rel)
                    continue
                # 允许相对缓存根的子目录路径（如 pass1_cache/文件名.meta.json），
                # 但禁止任何路径穿越（../、绝对路径等），确保只删除缓存目录内的文件。
                file_path = os.path.join(cache_dir, rel)
                abs_target = os.path.abspath(file_path)
                if abs_target != abs_cache and not abs_target.startswith(abs_cache + os.sep):
                    not_found_files.append(rel)
                    continue
                if not os.path.isfile(abs_target):
                    not_found_files.append(rel)
                    continue
                try:
                    os.remove(abs_target)
                    deleted_files.append(rel)
                except OSError:
                    not_found_files.append(rel)
            if deleted_files:
                _append_engine_log(
                    project_dir,
                    f"[cache] 删除缓存文件 {','.join(str(x) for x in deleted_files)}",
                )
            handler._send_json({"success": True, "deleted_files": deleted_files, "not_found_files": not_found_files})
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to delete cache files: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # POST /api/projects/:id/cache/search
    if sub_path == "/cache/search":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        try:
            payload = handler._read_json_body()
            query = str(payload.get("query", "")).strip()
            field = str(payload.get("field", "all")).strip()  # all | src | dst
            max_results = min(int(payload.get("max_results", 500)), 2000)
            # 正则模式（options.re）：仅用于搜索；编译一次，非法正则返回 400
            options = payload.get("options")
            use_regex = isinstance(options, dict) and bool(options.get("re", False))
            search_pattern = None
            if use_regex:
                try:
                    search_pattern = re.compile(query)
                except re.error as exc:
                    handler._send_json(
                        {"error": f"无效的正则表达式: {exc}"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return

            if not query:
                handler._send_json({"results": [], "total": 0})
                return

            cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
            results = []
            total_matches = 0
            if os.path.isdir(cache_dir):
                for rel in _collect_cache_files(cache_dir):
                    fp = os.path.join(cache_dir, rel)
                    try:
                        import orjson
                        with open(fp, "rb") as f:
                            entries = orjson.loads(f.read())
                        for e in entries:
                            if not isinstance(e, dict):
                                continue
                            # 可见文本对齐页面渲染：原文行显示 pre_src（post_src 为更底层原文，页面不展示）
                            src_text = (
                                e.get("pre_src", "")
                                or e.get("post_src", "")
                                or e.get("post_jp", "")
                                or e.get("pre_jp", "")
                            )
                            dst_text = e.get("pre_dst", "") or e.get("pre_zh", "") or e.get("proofread_dst", "") or e.get("proofread_zh", "")
                            problem_text = e.get("problem", "")
                            # 说话人徽章（name 可能为字符串或 names 列表）也是页面可见文本
                            raw_name = e.get("name", "") or e.get("names", "")
                            if isinstance(raw_name, list):
                                speaker_text = " ".join(str(x) for x in raw_name)
                            else:
                                speaker_text = str(raw_name) if raw_name else ""
                            if search_pattern is not None:
                                match_src = bool(search_pattern.search(src_text))
                                match_dst = bool(search_pattern.search(dst_text))
                                match_problem = bool(search_pattern.search(problem_text))
                                match_speaker = bool(speaker_text) and bool(
                                    search_pattern.search(speaker_text)
                                )
                            else:
                                match_src = query.lower() in src_text.lower()
                                match_dst = query.lower() in dst_text.lower()
                                match_problem = query.lower() in problem_text.lower()
                                match_speaker = bool(speaker_text) and query.lower() in speaker_text.lower()
                            if field == "src" and not match_src:
                                continue
                            if field == "dst" and not match_dst:
                                continue
                            if field == "problem" and not match_problem:
                                continue
                            if field == "all" and not match_src and not match_dst and not match_problem and not match_speaker:
                                continue
                            total_matches += 1
                            if len(results) < max_results:
                                results.append({
                                    "filename": rel,
                                    "index": e.get("index", 0),
                                    "speaker": raw_name,
                                    "post_src": src_text,
                                    "pre_dst": dst_text,
                                    "match_src": match_src,
                                    "match_dst": match_dst,
                                    "match_problem": match_problem,
                                    "match_speaker": match_speaker,
                                    "problem": e.get("problem", ""),
                                    "trans_by": e.get("trans_by", ""),
                                })
                    except Exception as exc:
                        LOGGER.warning(f"cache/search 跳过无法解析的缓存文件 {rel}: {exc}")
                        continue
            LOGGER.info(f"cache/search 完成: query={query!r} field={field} 命中 {total_matches} 条")
            handler._send_json({"results": results, "total": total_matches})
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to search cache: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # POST /api/projects/:id/cache/replace
    if sub_path == "/cache/replace":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        try:
            payload = handler._read_json_body()
            query = str(payload.get("query", "")).strip()
            replacement = str(payload.get("replacement", ""))
            field = str(payload.get("field", "dst")).strip()  # dst | all（src/problem 拒绝）
            dry_run = bool(payload.get("dry_run", False))

            if not query:
                handler._send_json({"error": "empty query"}, status=HTTPStatus.BAD_REQUEST)
                return
            # 原文替换会污染送翻文本（post_src）且与搜索口径（pre_src）不一致；问题字段只读。
            # 与前端侧边栏守卫一致，all 仅替换译文侧字段
            if field in ("src", "problem"):
                handler._send_json({"error": _REPLACE_FIELD_REJECTED_MSG}, status=HTTPStatus.BAD_REQUEST)
                return

            # 真实替换会写缓存文件：翻译任务运行中与 worker 增量写盘互相覆盖，拒绝执行
            # （dry_run 预览只读不落盘，保持可用）
            if not dry_run:
                job = registry.get_project_job(project_dir)
                if job is not None and job.status in {"pending", "running"}:
                    LOGGER.warning(f"[cache] 批量替换被拒绝（翻译任务运行中）：{project_dir}")
                    handler._send_json(
                        {"success": False, "error": "翻译进行中，请停止翻译后再执行替换"},
                        status=HTTPStatus.CONFLICT,
                    )
                    return

            import orjson
            cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
            total_matches = 0
            total_files = 0
            file_details = []

            if os.path.isdir(cache_dir):
                for rel in _collect_cache_files(cache_dir):
                    fp = os.path.join(cache_dir, rel)
                    try:
                        with open(fp, "rb") as f:
                            entries = orjson.loads(f.read())
                    except Exception:
                        continue
                    file_changed = False
                    file_matches = 0
                    for e in entries:
                        if not isinstance(e, dict):
                            continue
                        dst_key = "pre_dst" if "pre_dst" in e else ("pre_zh" if "pre_zh" in e else None)
                        # replace in dst
                        if field in ("dst", "all") and dst_key and query in e.get(dst_key, ""):
                            if not dry_run:
                                e[dst_key] = e[dst_key].replace(query, replacement)
                            file_matches += 1
                            file_changed = True
                        # also replace in proofread_dst / proofread_zh
                        if field in ("dst", "all"):
                            pr_key = "proofread_dst" if "proofread_dst" in e else ("proofread_zh" if "proofread_zh" in e else None)
                            if pr_key and query in e.get(pr_key, ""):
                                if not dry_run:
                                    e[pr_key] = e[pr_key].replace(query, replacement)
                                file_matches += 1
                                file_changed = True
                    if file_matches > 0:
                        total_matches += file_matches
                        total_files += 1
                        detail: dict = {"filename": rel, "matches": file_matches}
                        # dry_run 与真实替换都返回 entries：dry_run 中条目未被修改（替换前原值），
                        # 供前端构造撤销栈的 before 快照；真实替换后返回替换后值作 after 快照
                        if file_changed:
                            detail["entries"] = entries
                        file_details.append(detail)
                        # 真实替换写盘（原子写：临时文件 + os.replace）。
                        # 此前该端点从不落盘、前端也不保存，替换结果只在响应里，实际不生效；
                        # 写盘失败直接抛出（500），不再被"跳过损坏文件"的容错吞掉
                        if not dry_run and file_changed:
                            tmp_path = fp + ".tmp"
                            with open(tmp_path, "wb") as f:
                                f.write(orjson.dumps(entries, option=orjson.OPT_INDENT_2))
                            os.replace(tmp_path, fp)
            if not dry_run and total_matches > 0:
                _append_engine_log(
                    project_dir,
                    f"[cache] 批量替换 {query[:30]!r} -> {replacement[:30]!r} "
                    f"field={field} 命中 {total_matches} 处 / {total_files} 文件",
                )
            if total_matches == 0:
                LOGGER.warning(
                    f"cache/replace 无匹配项: query={query!r} field={field} "
                    f"dry_run={dry_run} project={project_dir}"
                )
            handler._send_json({
                "success": True,
                "total_matches": total_matches,
                "total_files": total_files,
                "dry_run": dry_run,
                "file_details": file_details,
            })
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to replace in cache: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # POST /api/projects/:id/cache/replace-entry（查找替换侧边栏「替换单个」）
    if sub_path == "/cache/replace-entry":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        try:
            payload = handler._read_json_body()
            query = str(payload.get("query", "")).strip()
            replacement = str(payload.get("replacement", ""))
            field = str(payload.get("field", "dst")).strip()  # dst | all（src/problem 拒绝）
            filename = str(payload.get("filename", "")).strip()
            index = payload.get("index")
            dry_run = bool(payload.get("dry_run", False))

            if not query:
                handler._send_json({"error": "empty query"}, status=HTTPStatus.BAD_REQUEST)
                return
            # 与 /cache/replace 一致：原文/问题字段不支持替换
            if field in ("src", "problem"):
                handler._send_json({"error": _REPLACE_FIELD_REJECTED_MSG}, status=HTTPStatus.BAD_REQUEST)
                return
            if not filename:
                handler._send_json({"error": "empty filename"}, status=HTTPStatus.BAD_REQUEST)
                return
            if index is None:
                handler._send_json({"error": "empty index"}, status=HTTPStatus.BAD_REQUEST)
                return
            # 路径穿越防护（与 /cache/:filename 一致）：先用系统分隔符形式检查，再统一正斜杠返回
            norm = os.path.normpath(filename.replace("\\", "/"))
            if norm == ".." or norm.startswith(".." + os.sep) or os.path.isabs(norm):
                handler._send_json({"error": "invalid cache path"}, status=HTTPStatus.BAD_REQUEST)
                return
            norm = norm.replace("\\", "/")

            # 真实替换会写缓存文件：翻译任务运行中与 worker 增量写盘互相覆盖，拒绝执行（与批量替换一致）
            if not dry_run:
                job = registry.get_project_job(project_dir)
                if job is not None and job.status in {"pending", "running"}:
                    LOGGER.warning(f"[cache] 单条替换被拒绝（翻译任务运行中）：{project_dir}")
                    handler._send_json(
                        {"success": False, "error": "翻译进行中，请停止翻译后再执行替换"},
                        status=HTTPStatus.CONFLICT,
                    )
                    return

            import orjson
            cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
            fp = os.path.join(cache_dir, norm)
            if not os.path.isfile(fp):
                handler._send_json({"error": "cache file not found"}, status=HTTPStatus.NOT_FOUND)
                return

            with open(fp, "rb") as f:
                entries = orjson.loads(f.read())

            file_matches = 0
            file_changed = False
            for e in entries:
                if not isinstance(e, dict):
                    continue
                if str(e.get("index", "")) != str(index):
                    continue
                dst_key = "pre_dst" if "pre_dst" in e else ("pre_zh" if "pre_zh" in e else None)
                # replace in dst
                if field in ("dst", "all") and dst_key and query in e.get(dst_key, ""):
                    if not dry_run:
                        e[dst_key] = e[dst_key].replace(query, replacement)
                    file_matches += 1
                    file_changed = True
                # also replace in proofread_dst / proofread_zh
                if field in ("dst", "all"):
                    pr_key = "proofread_dst" if "proofread_dst" in e else ("proofread_zh" if "proofread_zh" in e else None)
                    if pr_key and query in e.get(pr_key, ""):
                        if not dry_run:
                            e[pr_key] = e[pr_key].replace(query, replacement)
                        file_matches += 1
                        file_changed = True

            if not dry_run and file_changed:
                tmp_path = fp + ".tmp"
                with open(tmp_path, "wb") as f:
                    f.write(orjson.dumps(entries, option=orjson.OPT_INDENT_2))
                os.replace(tmp_path, fp)

            if file_matches > 0:
                _append_engine_log(
                    project_dir,
                    f"[cache] 单条替换 {query[:30]!r} -> {replacement[:30]!r} "
                    f"field={field} index={index} 命中 {file_matches} 处（{norm}）",
                )
            else:
                LOGGER.warning(
                    f"cache/replace-entry 无匹配项: query={query!r} field={field} "
                    f"index={index} file={norm} project={project_dir}"
                )
            # 响应结构与批量 replace 一致，前端复用 buildReplaceUndoEntries 构造撤销栈
            handler._send_json({
                "success": True,
                "total_matches": file_matches,
                "total_files": 1 if file_matches > 0 else 0,
                "dry_run": dry_run,
                "file_details": [{"filename": norm, "matches": file_matches, "entries": entries}]
                if file_changed
                else [],
            })
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to replace entry in cache: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # GET /api/projects/:id/cache/:filename/h-ranges（须在通用 /cache/ catch-all 之前）
    if sub_path.startswith("/cache/") and sub_path.endswith("/h-ranges"):
        h_filename = unquote(sub_path[len("/cache/"):-len("/h-ranges")])
        if not h_filename:
            handler._send_json({"error": "invalid cache filename"}, status=HTTPStatus.BAD_REQUEST)
            return
        h_norm = os.path.normpath(h_filename.replace("\\", "/"))
        if h_norm == ".." or h_norm.startswith(".." + os.sep) or os.path.isabs(h_norm):
            handler._send_json({"error": "invalid cache path"}, status=HTTPStatus.BAD_REQUEST)
            return
        handler._send_json(_resolve_cache_h_ranges(project_dir, h_norm))
        return

    # GET /api/projects/:id/cache/:filename (catch-all, must be after specific /cache/* routes)
    if sub_path.startswith("/cache/"):
        filename = unquote(sub_path[len("/cache/"):])
        if not filename:
            handler._send_json({"error": "invalid cache filename"}, status=HTTPStatus.BAD_REQUEST)
            return
        # 允许相对子路径（如 pass1_cache/文件名.meta.json），但禁止路径穿越
        norm = os.path.normpath(filename.replace("\\", "/"))
        if norm == ".." or norm.startswith(".." + os.sep) or os.path.isabs(norm):
            handler._send_json({"error": "invalid cache path"}, status=HTTPStatus.BAD_REQUEST)
            return
        cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
        file_path = os.path.join(cache_dir, norm)
        abs_cache = os.path.abspath(cache_dir)
        abs_file = os.path.abspath(file_path)
        if not (abs_file == abs_cache or abs_file.startswith(abs_cache + os.sep)):
            handler._send_json({"error": "invalid cache path"}, status=HTTPStatus.BAD_REQUEST)
            return
        if not os.path.isfile(file_path):
            handler._send_json({"error": f"cache file not found: {filename}"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            import orjson
            with open(file_path, "rb") as f:
                data = orjson.loads(f.read())
            # 原样返回缓存条目：不应用 name 替换表（name 参与缓存 key，
            # 若在此替换会污染缓存文件导致后续缓存 key 失配）
            handler._send_json({"project_dir": project_dir, "filename": norm, "entries": data})
        except Exception as exc:
            handler._send_json({"error": f"failed to read cache: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

    # GET /api/projects/:id/progress
    # 后半段路由（精确匹配，无前缀兜底）委托到 server_handlers_project2
    route_project_api_part2(handler, registry, project_dir, sub_path)
    handler._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
