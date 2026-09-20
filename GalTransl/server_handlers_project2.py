"""项目级路由 handler 第二部分（0.4.10 从 server_handlers_project 再拆出）。

承接 route_project_api 后半段：运行时状态/停止、字典、人名表、问题与备选译文、日志。
这些路由全部为**精确匹配**（无前缀兜底），故可在原函数的前缀兜底之后安全委托。

由 server_handlers_project.route_project_api 调用，参数语义与前半段一致。
"""
from __future__ import annotations

import json
import os
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from GalTransl import LOGGER, CACHE_FOLDERNAME
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
    _get_default_config,
    _read_yaml_file,
    _write_yaml_file,
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
from GalTransl.server_backend import _read_backend_profiles
from GalTransl.server_scaffold import _workspace_root
from GalTransl.server_cache import (
    _REPLACE_FIELD_REJECTED_MSG,
    _append_engine_log,
    _build_cache_tree,
    _build_project_output,
    _cache_entry_key,
    _entry_modified,
    _fmt_indices,
    _list_dir_entries,
    _resolve_cache_h_ranges,
)
from GalTransl.server_jobs import JobRegistry


def route_project_api_part2(
    handler: Any, registry: JobRegistry, project_dir: str, sub_path: str
) -> None:
    """route_project_api 后半段路由（精确匹配，无前缀兜底）。"""
    if sub_path == "/progress":
        cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
        total = 0
        translated = 0
        problems = 0
        failed = 0
        file_progress = []
        if os.path.isdir(cache_dir):
            for name in sorted(os.listdir(cache_dir)):
                fp = os.path.join(cache_dir, name)
                if not os.path.isfile(fp) or not fp.endswith(".json"):
                    continue
                try:
                    import orjson
                    with open(fp, "rb") as f:
                        entries = orjson.loads(f.read())
                    f_total = len(entries)
                    f_translated = sum(1 for e in entries if isinstance(e, dict) and (e.get("pre_dst", "") or e.get("pre_zh", "")))
                    f_problems = sum(1 for e in entries if isinstance(e, dict) and e.get("problem", ""))
                    f_failed = sum(1 for e in entries if isinstance(e, dict) and "(Failed)" in str(e.get("problem", "")))
                    total += f_total
                    translated += f_translated
                    problems += f_problems
                    failed += f_failed
                    file_progress.append({
                        "filename": name,
                        "total": f_total,
                        "translated": f_translated,
                        "problems": f_problems,
                        "failed": f_failed,
                    })
                except Exception:
                    continue
        handler._send_json({
            "project_dir": project_dir,
            "total": total,
            "translated": translated,
            "problems": problems,
            "failed": failed,
            "files": file_progress,
        })
        return

    # GET /api/projects/:id/runtime
    if sub_path == "/runtime":
        runtime = RUNTIME_REGISTRY.get_runtime_snapshot(project_dir)
        file_totals = runtime.get("file_totals", {})
        cache_file_display_map = runtime.get("cache_file_display_map", {})
        # 兜底用真实配置文件探测（config.inc.yaml 优先），避免无 job 历史时误用 config.yaml 读不到 retran 配置
        config_file_name = _detect_config_file(project_dir)
        job = registry.get_project_job(project_dir)
        if job:
            config_file_name = job.config_file_name or config_file_name
        config_retran_key = RUNTIME_PROGRESS_CACHE.get_retran_key(project_dir, config_file_name)
        retran_terms = _normalize_retran_terms(config_retran_key)
        retran_key = ""
        current_job_started_at_ns = None
        if job and job.status in {"pending", "running"}:
            retran_key = config_retran_key
            current_job_started_at_ns = _parse_runtime_job_started_at_ns(job.started_at)
        progress_payload = RUNTIME_PROGRESS_CACHE.get_progress(
            project_dir,
            file_totals=file_totals,
            cache_file_display_map=cache_file_display_map,
            retran_key=retran_key,
            retran_terms=retran_terms,
            current_job_started_at_ns=current_job_started_at_ns,
        )
        total = progress_payload["total"]
        translated = progress_payload["translated"]
        percent = round((translated / total) * 100, 1) if total > 0 else 0
        speed = runtime["translation_speed_lpm"]
        remaining = max(total - translated, 0)
        eta_seconds = round((remaining / speed) * 60) if speed > 0 and remaining > 0 else None
        handler._send_json({
            "project_dir": project_dir,
            "job": None if job is None else {
                "job_id": job.job_id,
                "status": job.status,
                "translator": job.translator,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
                "error": job.error,
                "gendic_added_entries": int(getattr(job, "gendic_added_entries", 0) or 0),
                "gendic_duplicated_entries": int(getattr(job, "gendic_duplicated_entries", 0) or 0),
            },
            "summary": {
                "total": total,
                "translated": translated,
                "problems": progress_payload["problems"],
                "failed": progress_payload["failed"],
                "percent": percent,
                "workers_active": runtime["workers_active"],
                "workers_configured": runtime["workers_configured"],
                "translation_speed_lpm": speed,
                "eta_seconds": eta_seconds,
                "updated_at": runtime["updated_at"],
            },
            "stage": runtime["stage"],
            "stage_index": runtime["stage_index"],
            "stage_total": runtime["stage_total"],
            "current_file": runtime["current_file"],
            "latest_prompt_preview": runtime.get("latest_prompt_preview", ""),
            "translation_previews": runtime.get("translation_previews", {}),
            "prompt_previews": runtime.get("prompt_previews", {}),
            "ttft_states": runtime.get("ttft_states", {}),
            "recent_errors": runtime["recent_errors"],
            "recent_successes": runtime["recent_successes"],
            "notices": runtime.get("notices", []),
            "retransl_stats": progress_payload["retransl_stats"],
            "files": progress_payload["files"],
        })
        return

    # POST /api/projects/:id/runtime/notices/clear — 前端 toast 后清除一次性提示
    if sub_path == "/runtime/notices/clear":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        clear_runtime_notices(project_dir)
        handler._send_json({"success": True, "project_dir": project_dir})
        return

    # POST /api/projects/:id/stop
    if sub_path == "/stop":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        job = registry.request_project_stop(project_dir)
        if job is None:
            handler._send_json(
                {"success": False, "project_dir": project_dir, "error": "no active job for project"},
                status=HTTPStatus.CONFLICT,
            )
            return
        _append_engine_log(
            project_dir, f"[job] 收到停止翻译请求 project={project_dir}"
        )
        handler._send_json({
            "success": True,
            "project_dir": project_dir,
            "job_id": job.job_id,
            "status": job.status,
            "message": "stop requested",
        })
        return

    # GET /api/projects/:id/dictionary
    if sub_path == "/dictionary":
        config_name = parse_qs(urlparse(handler.path).query).get("config", ["config.yaml"])[0]
        if not _is_safe_config_filename(config_name):
            handler._send_json({"error": "invalid config filename"}, status=HTTPStatus.BAD_REQUEST)
            return
        config_path = os.path.join(project_dir, config_name)
        if not os.path.isfile(config_path):
            handler._send_json({"error": f"config file not found: {config_name}"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            data = _read_yaml_file(config_path)
            dict_cfg = data.get("dictionary", {})
            default_folder = dict_cfg.get("defaultDictFolder", "Dict")
            if os.path.isabs(default_folder):
                dict_base = default_folder
            else:
                dict_base = os.path.abspath(default_folder)
            fh_all = dict_cfg.get("forbiddenDictH", dict_cfg.get("hCheckDict", []))
            fnh_all = dict_cfg.get("forbiddenDictNonH", [])
            gpt_all = [str(x) for x in dict_cfg.get("gpt.dict", [])]
            # GPT 字典按文件名后缀拆 h/非h，供前端分组展示；运行时仍用 gpt.dict 全量
            def _gpt_scene(fname: str) -> str:
                lower = fname.lower()
                return "h" if ("_h" in lower and "非h" not in lower) else "nh"

            gpt_files_h = [x for x in gpt_all if _gpt_scene(x) == "h"]
            gpt_files_nh = [x for x in gpt_all if _gpt_scene(x) == "nh"]
            result = {
                "project_dir": project_dir,
                "default_dict_folder": default_folder,
                "pre_dict_files": dict_cfg.get("preDict", []),
                "gpt_dict_files": gpt_all,
                "gpt_dict_files_h": gpt_files_h,
                "gpt_dict_files_nh": gpt_files_nh,
                "post_dict_files": dict_cfg.get("postDict", []),
                "h_dict_files": fh_all,
                "forbidden_dict_files_h": fh_all,
                "forbidden_dict_files_nh": fnh_all,
                "dict_contents": {},
            }
            for _, file_list in [
                ("preDict", dict_cfg.get("preDict", [])),
                ("gpt.dict", dict_cfg.get("gpt.dict", [])),
                ("postDict", dict_cfg.get("postDict", [])),
                ("forbiddenDictH", fh_all),
                ("forbiddenDictNonH", fnh_all),
            ]:
                for fname in file_list:
                    clean = str(fname).replace(DICT_PROJECT_MARKER, "").strip()
                    if DICT_PROJECT_MARKER in str(fname):
                        fpath = os.path.join(project_dir, clean)
                    else:
                        fpath = os.path.join(dict_base, clean)
                    result["dict_contents"][str(fname)] = _read_dict_file_payload(fpath)
            handler._send_json(result)
        except Exception as exc:
            handler._send_json({"error": f"failed to read dictionary config: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # GET /api/projects/:id/dictionary/project
    if sub_path == "/dictionary/project":
        if handler.command != "GET":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        config_name = parse_qs(urlparse(handler.path).query).get("config", ["config.yaml"])[0]
        if not _is_safe_config_filename(config_name):
            handler._send_json({"error": "invalid config filename"}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            handler._send_json(_collect_project_dict_payload(project_dir, config_name))
        except FileNotFoundError as exc:
            handler._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            handler._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to load project dictionaries: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # POST /api/projects/:id/dictionary/project/create
    if sub_path == "/dictionary/project/create":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        try:
            payload = handler._read_json_body()
            config_name = str(payload.get("config_file_name", "config.yaml") or "config.yaml")
            category = str(payload.get("category", "")).strip()
            filename = str(payload.get("filename", "")).strip()

            if not _is_safe_config_filename(config_name):
                handler._send_json({"error": "invalid config filename"}, status=HTTPStatus.BAD_REQUEST)
                return

            if not _is_safe_dict_filename(filename):
                handler._send_json({"error": "invalid dictionary filename"}, status=HTTPStatus.BAD_REQUEST)
                return

            config_path = os.path.join(project_dir, config_name)
            if not os.path.isfile(config_path):
                handler._send_json({"error": f"config file not found: {config_name}"}, status=HTTPStatus.NOT_FOUND)
                return

            data = _read_yaml_file(config_path)
            dict_cfg = data.get("dictionary", {})
            list_key = _dict_category_config_key(category)
            current_list = [str(x) for x in dict_cfg.get(list_key, [])]
            file_key = f"{DICT_PROJECT_MARKER}{filename}"

            if file_key not in current_list:
                current_list.append(file_key)
                dict_cfg[list_key] = current_list
                data["dictionary"] = dict_cfg
                _write_yaml_file(config_path, data)

            file_path = os.path.join(project_dir, filename)
            os.makedirs(os.path.dirname(file_path) or project_dir, exist_ok=True)
            if not os.path.exists(file_path):
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write("")

            handler._send_json({"success": True, "file_key": file_key, "path": file_path})
        except ValueError as exc:
            handler._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to create project dictionary file: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # POST /api/projects/:id/dictionary/project/save
    if sub_path == "/dictionary/project/save":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        try:
            payload = handler._read_json_body()
            config_name = str(payload.get("config_file_name", "config.yaml") or "config.yaml")
            file_key = str(payload.get("file_key", "")).strip()
            content = _normalize_dict_text(str(payload.get("content", "")))

            if not _is_safe_config_filename(config_name):
                handler._send_json({"error": "invalid config filename"}, status=HTTPStatus.BAD_REQUEST)
                return

            if DICT_PROJECT_MARKER not in file_key:
                handler._send_json({"error": "file_key must be a project dictionary"}, status=HTTPStatus.BAD_REQUEST)
                return

            clean_name = file_key.replace(DICT_PROJECT_MARKER, "").strip()
            if not _is_safe_dict_filename(clean_name):
                handler._send_json({"error": "invalid dictionary filename"}, status=HTTPStatus.BAD_REQUEST)
                return

            config_path = os.path.join(project_dir, config_name)
            if not os.path.isfile(config_path):
                handler._send_json({"error": f"config file not found: {config_name}"}, status=HTTPStatus.NOT_FOUND)
                return

            data = _read_yaml_file(config_path)
            dict_cfg = data.get("dictionary", {})
            listed = set(str(x) for x in dict_cfg.get("preDict", []))
            listed.update(str(x) for x in dict_cfg.get("gpt.dict", []))
            listed.update(str(x) for x in dict_cfg.get("postDict", []))
            listed.update(str(x) for x in dict_cfg.get("forbiddenDictH", dict_cfg.get("hCheckDict", [])))
            listed.update(str(x) for x in dict_cfg.get("forbiddenDictNonH", []))
            if file_key not in listed:
                handler._send_json({"error": "dictionary file is not configured in project dictionary lists"}, status=HTTPStatus.BAD_REQUEST)
                return

            file_path = os.path.join(project_dir, clean_name)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            handler._send_json({"success": True, "file_key": file_key})
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to save project dictionary file: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # POST /api/projects/:id/dictionary/project/delete
    if sub_path == "/dictionary/project/delete":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        try:
            payload = handler._read_json_body()
            config_name = str(payload.get("config_file_name", "config.yaml") or "config.yaml")
            file_key = str(payload.get("file_key", "")).strip()
            delete_file = bool(payload.get("delete_file", True))

            if not _is_safe_config_filename(config_name):
                handler._send_json({"error": "invalid config filename"}, status=HTTPStatus.BAD_REQUEST)
                return

            if DICT_PROJECT_MARKER not in file_key:
                handler._send_json({"error": "file_key must be a project dictionary"}, status=HTTPStatus.BAD_REQUEST)
                return

            clean_name = file_key.replace(DICT_PROJECT_MARKER, "").strip()
            if not _is_safe_dict_filename(clean_name):
                handler._send_json({"error": "invalid dictionary filename"}, status=HTTPStatus.BAD_REQUEST)
                return

            config_path = os.path.join(project_dir, config_name)
            if not os.path.isfile(config_path):
                handler._send_json({"error": f"config file not found: {config_name}"}, status=HTTPStatus.NOT_FOUND)
                return

            data = _read_yaml_file(config_path)
            dict_cfg = data.get("dictionary", {})
            for list_key in ("preDict", "gpt.dict", "postDict", "forbiddenDictH", "forbiddenDictNonH", "hCheckDict"):
                current = [str(x) for x in dict_cfg.get(list_key, [])]
                dict_cfg[list_key] = [x for x in current if x != file_key]
            data["dictionary"] = dict_cfg
            _write_yaml_file(config_path, data)

            if delete_file:
                file_path = os.path.join(project_dir, clean_name)
                if os.path.isfile(file_path):
                    os.remove(file_path)

            handler._send_json({"success": True, "file_key": file_key, "deleted_file": delete_file})
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to delete project dictionary file: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # GET /api/projects/:id/name-table
    if sub_path == "/name-table":
        # Read the name replacement table (CSV or XLSX)
        csv_path = os.path.join(project_dir, "name替换表.csv")
        xlsx_path = os.path.join(project_dir, "name替换表.xlsx")
        names = []
        source_file = None

        if os.path.isfile(csv_path):
            source_file = "name替换表.csv"
            try:
                import csv as _csv
                with open(csv_path, "r", newline="", encoding="utf-8-sig") as f:
                    reader = _csv.reader(f)
                    header = next(reader, None)
                    if header:
                        try:
                            src_idx = header.index("SRC_Name")
                            dst_idx = header.index("DST_Name")
                        except ValueError:
                            try:
                                src_idx = header.index("JP_Name")
                                dst_idx = header.index("CN_Name")
                            except ValueError:
                                handler._send_json({"error": "CSV缺少 SRC_Name/DST_Name (或旧版 JP_Name/CN_Name) 列"}, status=HTTPStatus.BAD_REQUEST)
                                return
                        count_idx = header.index("Count") if "Count" in header else -1
                        for row in reader:
                            if len(row) > max(src_idx, dst_idx):
                                names.append({
                                    "src_name": row[src_idx],
                                    "dst_name": row[dst_idx] if dst_idx < len(row) else "",
                                    "count": int(row[count_idx]) if count_idx >= 0 and count_idx < len(row) and row[count_idx].isdigit() else 0,
                                })
            except Exception as exc:
                handler._send_json({"error": f"读取CSV人名表失败: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
        elif os.path.isfile(xlsx_path):
            source_file = "name替换表.xlsx"
            try:
                import openpyxl
                wb = openpyxl.load_workbook(xlsx_path)
                sheet = wb.active
                header = [cell.value for cell in sheet[1]]
                try:
                    src_idx = header.index("SRC_Name")
                    dst_idx = header.index("DST_Name")
                except ValueError:
                    try:
                        src_idx = header.index("JP_Name")
                        dst_idx = header.index("CN_Name")
                    except ValueError:
                        handler._send_json({"error": "XLSX缺少 SRC_Name/DST_Name (或旧版 JP_Name/CN_Name) 列"}, status=HTTPStatus.BAD_REQUEST)
                        return
                count_idx = header.index("Count") if "Count" in header else -1
                for row in sheet.iter_rows(min_row=2):
                    src_val = row[src_idx].value if src_idx < len(row) else None
                    dst_val = row[dst_idx].value if dst_idx < len(row) else None
                    count_val = row[count_idx].value if count_idx >= 0 and count_idx < len(row) else 0
                    if src_val is not None:
                        names.append({
                            "src_name": str(src_val),
                            "dst_name": str(dst_val) if dst_val is not None else "",
                            "count": int(count_val) if count_val else 0,
                        })
            except Exception as exc:
                handler._send_json({"error": f"读取XLSX人名表失败: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

        handler._send_json({
            "project_dir": project_dir,
            "source_file": source_file,
            "names": names,
        })
        return

    # POST /api/projects/:id/name-table/generate
    if sub_path == "/name-table/generate":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        # Generate name table by reusing the full pipeline for correct speaker name extraction
        try:
            config_name = parse_qs(urlparse(handler.path).query).get("config", ["config.yaml"])[0]
            result = registry.submit({
                "project_dir": project_dir,
                "config_file_name": config_name,
                "translator": "dump-name",
            })
            handler._send_json({
                "success": True,
                "job_id": result.get("job_id", ""),
            })
        except _ConcurrentLimitError as exc:
            handler._send_json({"error": str(exc)}, status=HTTPStatus.TOO_MANY_REQUESTS)
        except ValueError as exc:
            handler._send_json({"error": str(exc)}, status=HTTPStatus.CONFLICT)
        except Exception as exc:
            handler._send_json({"error": f"生成人名表失败: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # POST /api/projects/:id/name-table/ai-translate  (SSE streaming)
    if sub_path == "/name-table/ai-translate":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        try:
            payload = handler._read_json_body()
            backend_profile = str(payload.get("backend_profile", "")).strip()
            backend_profile_data = payload.get("backend_profile_data")
            untranslated = payload.get("names", [])
            if not isinstance(untranslated, list) or not untranslated:
                handler._send_json({"error": "names must be a non-empty array"}, status=HTTPStatus.BAD_REQUEST)
                return

            oai_section = None
            if isinstance(backend_profile_data, dict) and backend_profile_data:
                candidate = backend_profile_data.get("OpenAI-Compatible")
                if isinstance(candidate, dict):
                    oai_section = candidate
            else:
                profiles_data = _read_backend_profiles()
                profiles = profiles_data.get("profiles", {})
                if backend_profile and backend_profile in profiles:
                    candidate = profiles[backend_profile].get("OpenAI-Compatible")
                    if isinstance(candidate, dict):
                        oai_section = candidate
                elif backend_profile:
                    handler._send_json({"error": f"后端配置 '{backend_profile}' 不存在"}, status=HTTPStatus.NOT_FOUND)
                    return
                else:
                    for _pname, _pconf in profiles.items():
                        if "OpenAI-Compatible" in _pconf and isinstance(_pconf["OpenAI-Compatible"], dict):
                            oai_section = _pconf["OpenAI-Compatible"]
                            break

            if not oai_section:
                handler._send_json({"error": "未找到可用的 OpenAI 兼容后端配置，请先在后端配置中添加 OpenAI 兼容接口"}, status=HTTPStatus.BAD_REQUEST)
                return

            tokens = oai_section.get("tokens", [])
            if not tokens:
                handler._send_json({"error": "后端配置中没有 API token"}, status=HTTPStatus.BAD_REQUEST)
                return

            token_entry = tokens[0]
            api_key = token_entry.get("token", "")
            endpoint = token_entry.get("endpoint", "https://api.openai.com")
            model_name = token_entry.get("modelName", oai_section.get("rewriteModelName", "gpt-4o-mini"))
            timeout = oai_section.get("apiTimeout", 300)

            if not api_key or "-example-" in api_key:
                handler._send_json({"error": "后端配置中的 API token 无效"}, status=HTTPStatus.BAD_REQUEST)
                return

            import re as _re
            if endpoint.endswith("/chat/completions"):
                endpoint = endpoint.replace("/chat/completions", "")
            if not _re.search(r"/v\d+", endpoint):
                base_path = "/v1"
            else:
                base_path = ""
            base_url = endpoint.strip("/") + base_path

            src_names = [str(n.get("src_name", "")) for n in untranslated if str(n.get("src_name", "")).strip()]
            if not src_names:
                handler._send_json({"error": "没有需要翻译的人名"}, status=HTTPStatus.BAD_REQUEST)
                return

            # --- SSE streaming response ---
            handler.send_response(HTTPStatus.OK)
            handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
            handler.send_header("Cache-Control", "no-cache")
            handler.send_header("Connection", "keep-alive")
            handler.end_headers()

            def _sse_send(event: str, data: dict) -> None:
                msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                handler.wfile.write(msg.encode("utf-8"))
                handler.wfile.flush()

            names_text = "\n".join(f"- {n}" for n in src_names)
            system_prompt = (
                "你是一个专业的日语/中文人名翻译专家。"
                "请将以下日本人名翻译为中文译名。"
                "以JSONL格式回答，每行一个JSON对象，格式为："
                '{"src":"原名","dst":"译名"}\n'
                "每翻译一个人名就输出一行，不要输出其他任何内容。"
                "如果某个名字不确定，请尽量给出最常用的中文译名。"
            )
            user_prompt = f"请翻译以下人名：\n{names_text}"

            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=base_url)
            stream = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                timeout=timeout,
                stream=True,
            )

            line_buf = ""
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if not delta or not delta.content:
                    continue
                line_buf += delta.content
                # Try to extract complete JSONL lines
                while "\n" in line_buf:
                    line, line_buf = line_buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    # Strip markdown fences if present
                    if line.startswith("```"):
                        continue
                    try:
                        obj = json.loads(line)
                        src = str(obj.get("src", "")).strip()
                        dst = str(obj.get("dst", "")).strip()
                        if src and dst:
                            _sse_send("name", {"src_name": src, "dst_name": dst})
                    except (json.JSONDecodeError, ValueError):
                        # Not valid JSON — might be partial, skip
                        pass

            # Flush remaining buffer
            if line_buf.strip():
                line = line_buf.strip()
                if not line.startswith("```"):
                    try:
                        obj = json.loads(line)
                        src = str(obj.get("src", "")).strip()
                        dst = str(obj.get("dst", "")).strip()
                        if src and dst:
                            _sse_send("name", {"src_name": src, "dst_name": dst})
                    except (json.JSONDecodeError, ValueError):
                        pass

            _sse_send("done", {"total": len(untranslated)})

        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            # If headers already sent (SSE started), send error as event
            try:
                err_msg = f"AI翻译人名失败: {exc}"
                handler.wfile.write(f"event: error\ndata: {json.dumps({'error': err_msg}, ensure_ascii=False)}\n\n".encode("utf-8"))
                handler.wfile.flush()
            except Exception:
                pass
        return

    # POST /api/projects/:id/name-table/save
    if sub_path == "/name-table/save":
        if handler.command != "POST":
            handler._send_json({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
            return
        try:
            from GalTransl.Name import write_name_table_csv
            payload = handler._read_json_body()
            names = payload.get("names", [])
            if not isinstance(names, list):
                handler._send_json({"error": "names must be an array"}, status=HTTPStatus.BAD_REQUEST)
                return
            # Build name_counter and dst_names from the posted data
            name_counter: dict[str, int] = {}
            dst_names: dict[str, str] = {}
            for item in names:
                src_name = str(item.get("src_name", "") or item.get("jp_name", ""))
                dst_name = str(item.get("dst_name", "") or item.get("cn_name", ""))
                count = int(item.get("count", 0))
                if src_name:
                    name_counter[src_name] = count
                    if dst_name:
                        dst_names[src_name] = dst_name
            csv_path = os.path.join(project_dir, "name替换表.csv")
            write_name_table_csv(csv_path, name_counter, dst_names)
            handler._send_json({"success": True, "source_file": "name替换表.csv", "total": len(names)})
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"保存人名表失败: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # GET /api/projects/:id/name-dict
    if sub_path == "/name-dict":
        # Return the name replacement dict (SRC→DST) for UI pill display
        name_dict = _load_project_name_dict(project_dir)
        handler._send_json({"project_dir": project_dir, "name_dict": name_dict})
        return

    # GET /api/projects/:id/problems
    if sub_path == "/problems":
        cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
        # 支持 ?file=<相对于 transl_cache 的路径> 仅查单个文件（右键/切换文件时跟随当前文件）
        query = parse_qs(urlparse(handler.path).query)
        file_filter = (query.get("file", [""])[0] or "").strip()
        all_problems = []
        abs_cache = os.path.abspath(cache_dir)
        name_dict = _load_project_name_dict(project_dir)

        def _collect(fp_rel: str) -> None:
            fp = os.path.join(cache_dir, fp_rel)
            if not os.path.isfile(fp) or not fp.endswith(".json"):
                return
            try:
                import orjson
                with open(fp, "rb") as f:
                    entries = orjson.loads(f.read())
                if not isinstance(entries, list):
                    return
                for e in entries:
                    if isinstance(e, dict) and e.get("problem", ""):
                        all_problems.append({
                            "filename": fp_rel,
                            "index": e.get("index", 0),
                            "speaker": _lookup_name(e.get("name", ""), name_dict),
                            "post_src": e.get("post_src", "") or e.get("post_jp", ""),
                            "pre_dst": e.get("pre_dst", "") or e.get("pre_zh", ""),
                            "problem": e.get("problem", ""),
                            "trans_by": e.get("trans_by", ""),
                        })
            except Exception:
                return

        if file_filter:
            # 仅收集指定文件（需做路径穿越防护）
            norm = os.path.normpath(file_filter.replace("\\", "/"))
            if norm != ".." and not norm.startswith(".." + os.sep) and not os.path.isabs(norm):
                _collect(norm)
        elif os.path.isdir(cache_dir):
            # 递归遍历整个 transl_cache（翻译缓存位于 pass3_cache 子目录，顶层 listdir 会漏掉）
            for root, _dirs, files in os.walk(cache_dir):
                for name in sorted(files):
                    if name.endswith(".json"):
                        fp = os.path.join(root, name)
                        rel = os.path.relpath(fp, cache_dir).replace("\\", "/")
                        _collect(rel)
        handler._send_json({"project_dir": project_dir, "problems": all_problems, "total": len(all_problems)})
        return

    # GET /api/projects/:id/alt-translations
    # 列出所有 alt_dst（备选译文）非空的缓存条目，供前端"查看备选"侧边栏使用。
    if sub_path == "/alt-translations":
        cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
        query = parse_qs(urlparse(handler.path).query)
        file_filter = (query.get("file", [""])[0] or "").strip()
        all_alts = []
        name_dict = _load_project_name_dict(project_dir)
        scanned = 0

        def _collect_alt(fp_rel: str) -> None:
            fp_rel = fp_rel.replace("\\", "/")
            fp = os.path.join(cache_dir, fp_rel)
            if not os.path.isfile(fp) or not fp.endswith(".json"):
                return
            nonlocal scanned
            scanned += 1
            try:
                import orjson
                with open(fp, "rb") as f:
                    entries = orjson.loads(f.read())
                if not isinstance(entries, list):
                    return
                for e in entries:
                    if isinstance(e, dict) and e.get("alt_dst", ""):
                        all_alts.append({
                            "filename": fp_rel,
                            "index": e.get("index", 0),
                            "speaker": _lookup_name(e.get("name", ""), name_dict),
                            "post_src": e.get("post_src", "") or e.get("post_jp", ""),
                            "pre_dst": e.get("pre_dst", "") or e.get("pre_zh", ""),
                            "alt_dst": e.get("alt_dst", ""),
                            "trans_by": e.get("trans_by", ""),
                        })
            except Exception as exc:
                LOGGER.debug(f"alt-translations: 跳过无法解析的缓存文件 {fp_rel}: {exc}")
                return

        if file_filter:
            # 路径穿越防护：与 /problems 端点一致
            norm = os.path.normpath(file_filter.replace("\\", "/"))
            if norm != ".." and not norm.startswith(".." + os.sep) and not os.path.isabs(norm):
                _collect_alt(norm)
        elif os.path.isdir(cache_dir):
            for root, _dirs, files in os.walk(cache_dir):
                for name in sorted(files):
                    if name.endswith(".json"):
                        fp = os.path.join(root, name)
                        rel = os.path.relpath(fp, cache_dir).replace("\\", "/")
                        _collect_alt(rel)
        LOGGER.debug(f"alt-translations: 扫描 {scanned} 个缓存文件，命中 {len(all_alts)} 条备选译文")
        handler._send_json({"project_dir": project_dir, "alts": all_alts, "total": len(all_alts)})
        return

    # GET /api/projects/:id/logs?source=engine|frontend
    # source 默认 engine 以保持向后兼容；engine 读项目级 GalTransl.log，
    # frontend 读由 POST /api/log 统一收集的前端日志（全局 frontend.log）。
    if sub_path == "/logs":
        query = parse_qs(urlparse(handler.path).query)
        source = (query.get("source", ["engine"]) or ["engine"])[0] or "engine"
        if source not in ("engine", "frontend"):
            handler._send_json(
                {"error": f"unsupported log source: {source}"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        # tail 校验前置：非法/负数直接 400（与日志文件是否存在无关）
        tail_raw = query.get("tail", ["2000"])[0]
        try:
            tail = int(tail_raw)
        except (ValueError, TypeError):
            handler._send_json(
                {"error": f"invalid tail parameter: {tail_raw}"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        if tail < 0:
            handler._send_json(
                {"error": f"tail must be non-negative: {tail}"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        if source == "engine":
            log_path = os.path.join(project_dir, "GalTransl.log")
        else:
            # frontend：优先翻译项目目录，回退全局（兼容历史/无项目日志）
            proj_fe = os.path.join(project_dir, "frontend.log")
            log_path = proj_fe if os.path.isfile(proj_fe) else os.path.join(_workspace_root(), "frontend.log")
        if not os.path.isfile(log_path):
            handler._send_json(
                {"project_dir": project_dir, "source": source, "exists": False, "lines": []}
            )
            return
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            # 默认返回最后 2000 行；tail=0 返回空（而非全部）
            tail_lines = lines[-tail:] if tail > 0 else []
            handler._send_json({
                "project_dir": project_dir,
                "source": source,
                "exists": True,
                "total_lines": len(lines),
                "lines": tail_lines,
            })
        except Exception as exc:
            handler._send_json({"error": f"failed to read log: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    handler._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
