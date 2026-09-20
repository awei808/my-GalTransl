"""根级 HTTP 路由与请求辅助（0.4.10 从 server.py 的 RequestHandler 抽出）。

搬迁内容：
- do_GET / do_POST / do_PUT / do_DELETE 的 30 个 /api 路由（含版本、translators、
  jobs、backend-profiles、plugins、problem-types、translation-guidelines、
  dictionaries/common、openai-models、projects/init、log、dictionaries/parse 等）；
- 请求解析与辅助：_handle_init_project / _handle_import_files / _parse_multipart_files /
  _parse_json_import_files / _collect_files_from_source_paths / _handle_parse_dictionary；
- 日志接收端点与其字段清洗（_handle_log_receive + _LOG_STRIP_TABLE /
  _VALID_LOG_LEVELS / _sanitize_log_field）。

与原实现保持**相同的 if 链顺序**，唯一机械变换是方法体 `self` -> `handler`。
调用方以 `do_get(self, registry)` 等形式委托。

注意：本模块不得从 GalTransl.server 导入（循环导入），依赖均从各子模块直接导入。
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import asdict
from email import policy as _email_policy  # noqa: F401
import email
from http import HTTPStatus
from typing import Any
from urllib.parse import unquote, urlparse

from GalTransl import (
    AUTHOR,
    CACHE_FOLDERNAME,
    GALTRANSL_VERSION,
    INPUT_FOLDERNAME,
    LOGGER,
    TRANSLATOR_SUPPORTED,
    new_version,
)
from GalTransl import AppSettings
from GalTransl.Dictionary import parse_dict_line
from GalTransl.DefaultProjectConfig import DEFAULT_PROJECT_CONFIG_YAML
from GalTransl.UtilityEngines import UTILITY_ENGINES
from GalTransl.backend_security import safe_under_project
from GalTransl.server_runtime import (
    _ConcurrentLimitError,
    _has_newer_release,
    _safe_project_dir,
    encode_project_dir,
)
from GalTransl.server_webui import INDEX_HTML
from GalTransl.server_config_schema import _write_yaml_file
from GalTransl.server_backend import (
    _build_prompt_templates_payload,
    _read_backend_profiles,
    _write_backend_profiles,
)
from GalTransl.server_dict import (
    _collect_common_dict_payload,
    _common_dict_directory,
    _dict_category_config_key,
    _is_safe_dict_filename,
    _normalize_dict_text,
    _read_common_dict_category_map,
    _write_common_dict_category_map,
)
from GalTransl.server_meta import _list_problem_types, _list_translation_guidelines, _scan_plugins
from GalTransl.server_scaffold import (
    _create_project_layout,
    _resolve_new_project_dir,
    _workspace_root,
    _write_stage_samples,
)
from GalTransl.server_jobs import JobRegistry


_LOG_STRIP_TABLE = {i: None for i in range(0x20)}
_LOG_STRIP_TABLE[0x7F] = None  # DEL 一并移除
_VALID_LOG_LEVELS = frozenset({"debug", "info", "warn", "warning", "error"})


def _sanitize_log_field(value: str, max_len: int = 64) -> str:
    """剥离不可打印控制字符（含换行/回车）并限制长度，防止伪造日志行。"""
    return value.translate(_LOG_STRIP_TABLE).strip()[:max_len]


def do_get(handler: Any, registry: JobRegistry) -> None:
    parsed = urlparse(handler.path)
    path = parsed.path

    if path == "/":
        handler._send_html(INDEX_HTML)
        return
    if path == "/api/version":
        handler._send_json({"version": GALTRANSL_VERSION, "author": AUTHOR})
        return
    if path == "/api/version/check":
        latest_version = new_version[0] if new_version else None
        handler._send_json(
            {
                "version": GALTRANSL_VERSION,
                "latest_version": latest_version,
                "update_available": _has_newer_release(GALTRANSL_VERSION, latest_version),
            }
        )
        return
    if path == "/api/translators":
        _hidden_translators = {
            "show-plugs",
            "dump-name",
            "rebuildr",
            "rebuilda",
            *UTILITY_ENGINES,
        }
        translators = [
            {
                "name": name,
                "description": description.get("zh-cn") or next(iter(description.values())),
            }
            for name, description in TRANSLATOR_SUPPORTED.items()
            if name not in _hidden_translators
        ]
        handler._send_json({"translators": translators})
        return
    if path == "/api/jobs":
        handler._send_json({"jobs": registry.list_jobs()})
        return
    if path == "/api/app-settings":
        handler._send_json(AppSettings.load_app_settings())
        return
    if path == "/api/project-config-template":
        handler._send_json({"content": DEFAULT_PROJECT_CONFIG_YAML})
        return
    if path == "/api/prompt-templates":
        handler._send_json(_build_prompt_templates_payload())
        return
    if path.startswith("/api/jobs/"):
        job_id = path.rsplit("/", 1)[-1]
        job = registry.get_job(job_id)
        if job is None:
            handler._send_json({"error": "job not found"}, status=HTTPStatus.NOT_FOUND)
            return
        handler._send_json(job)
        return

    # --- Backend Profiles API ---
    if path == "/api/backend-profiles":
        data = _read_backend_profiles()
        handler._send_json(data)
        return

    if path.startswith("/api/backend-profiles/"):
        profile_name = unquote(path.split("/", 3)[-1])
        data = _read_backend_profiles()
        profiles = data.get("profiles", {})
        if profile_name not in profiles:
            handler._send_json({"error": f"profile not found: {profile_name}"}, status=HTTPStatus.NOT_FOUND)
            return
        handler._send_json({"name": profile_name, "profile": profiles[profile_name]})
        return

    # --- Project API ---
    if path == "/api/plugins":
        handler._send_json({"plugins": _scan_plugins()})
        return

    if path == "/api/problem-types":
        handler._send_json({"problem_types": _list_problem_types()})
        return

    if path == "/api/translation-guidelines":
        handler._send_json({"guidelines": _list_translation_guidelines()})
        return

    if path == "/api/projects/workspace-root":
        handler._send_json({"workspace_root": _workspace_root()})
        return

    if path.startswith("/api/projects/"):
        parts = path.split("/", 4)  # /api/projects/:id/sub...
        if len(parts) < 4:
            handler._send_json({"error": "invalid project path"}, status=HTTPStatus.BAD_REQUEST)
            return
        project_id = parts[3]
        sub_path = "/" + "/".join(parts[4:]) if len(parts) > 4 else "/"
        handler._route_project_api(project_id, sub_path)
        return

    # GET /api/dictionaries/common
    if path == "/api/dictionaries/common":
        try:
            handler._send_json(_collect_common_dict_payload())
        except Exception as exc:
            handler._send_json({"error": f"failed to load common dictionaries: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    handler._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)


def do_post(handler: Any, registry: JobRegistry) -> None:
    if not handler._require_write_auth():
        return
    parsed = urlparse(handler.path)
    path = parsed.path

    if path == "/api/projects/init":
        handle_init_project(handler)
        return

    # POST /api/log — 前端日志统一入口，由单一 handler 收集
    if path == "/api/log":
        handle_log_receive(handler)
        return

    # POST /api/dictionaries/parse — 将字典文本解析为结构化行（纯计算）
    if path == "/api/dictionaries/parse":
        handle_parse_dictionary(handler)
        return

    if path.startswith("/api/projects/"):
        parts = path.split("/", 4)
        if len(parts) < 4:
            handler._send_json({"error": "invalid project path"}, status=HTTPStatus.BAD_REQUEST)
            return
        project_id = parts[3]
        sub_path = "/" + "/".join(parts[4:]) if len(parts) > 4 else "/"
        handler._route_project_api(project_id, sub_path)
        return

    # POST /api/openai-models — query a list of models from an OpenAI-compatible API.
    if path == "/api/openai-models":
        try:
            payload = handler._read_json_body()
            endpoint = str(payload.get("endpoint", "")).strip() or "https://api.openai.com"
            token = str(payload.get("token", "")).strip()
            proxy = payload.get("proxy")
            timeout = float(payload.get("timeout", 15))

            base = endpoint.rstrip("/")
            if base.endswith("/v1"):
                url = base + "/models"
            elif base.rstrip("/").endswith("/models"):
                url = base
            else:
                url = base + "/v1/models"

            import urllib.request
            import urllib.error
            req = urllib.request.Request(url, method="GET")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            req.add_header("Accept", "application/json")

            opener_args = []
            if isinstance(proxy, dict):
                proxy_map: dict[str, str] = {}
                http_proxy = str(proxy.get("http") or proxy.get("http_proxy") or "").strip()
                https_proxy = str(proxy.get("https") or proxy.get("https_proxy") or http_proxy).strip()
                if http_proxy:
                    proxy_map["http"] = http_proxy
                if https_proxy:
                    proxy_map["https"] = https_proxy
                if proxy_map:
                    opener_args.append(urllib.request.ProxyHandler(proxy_map))
            elif isinstance(proxy, str) and proxy.strip():
                opener_args.append(urllib.request.ProxyHandler({"http": proxy.strip(), "https": proxy.strip()}))
            else:
                # Explicitly bypass system proxies when none provided to avoid unexpected routing.
                opener_args.append(urllib.request.ProxyHandler({}))

            opener = urllib.request.build_opener(*opener_args)
            try:
                with opener.open(req, timeout=timeout) as resp:
                    raw = resp.read()
            except urllib.error.HTTPError as http_exc:
                try:
                    body = http_exc.read().decode("utf-8", errors="replace")
                except Exception:
                    body = ""
                handler._send_json(
                    {"error": f"HTTP {http_exc.code}: {http_exc.reason}", "detail": body[:1000]},
                    status=HTTPStatus.BAD_GATEWAY,
                )
                return
            except urllib.error.URLError as url_exc:
                handler._send_json({"error": f"请求失败: {url_exc.reason}"}, status=HTTPStatus.BAD_GATEWAY)
                return

            try:
                data = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                handler._send_json({"error": "响应不是合法的 JSON"}, status=HTTPStatus.BAD_GATEWAY)
                return

            # OpenAI-style: {"data": [{"id": "..."}, ...]}
            # Some backends may return a bare list.
            items: list[Any] = []
            if isinstance(data, dict):
                if isinstance(data.get("data"), list):
                    items = data["data"]
                elif isinstance(data.get("models"), list):
                    items = data["models"]
            elif isinstance(data, list):
                items = data

            models: list[str] = []
            for item in items:
                if isinstance(item, str):
                    models.append(item)
                elif isinstance(item, dict):
                    mid = item.get("id") or item.get("name") or item.get("model")
                    if isinstance(mid, str) and mid:
                        models.append(mid)
            # De-duplicate while preserving order.
            seen = set()
            unique_models: list[str] = []
            for m in models:
                if m not in seen:
                    seen.add(m)
                    unique_models.append(m)

            handler._send_json({"models": unique_models, "url": url})
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to fetch models: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    if path == "/api/jobs":
        try:
            payload = handler._read_json_body()
            job = registry.submit(payload)
        except _ConcurrentLimitError as exc:
            handler._send_json({"error": str(exc)}, status=HTTPStatus.TOO_MANY_REQUESTS)
            return
        except ValueError as exc:
            handler._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
            return

        handler._send_json(job, status=HTTPStatus.ACCEPTED)
        return

    # POST /api/dictionaries/common/create
    if path == "/api/dictionaries/common/create":
        try:
            payload = handler._read_json_body()
            category = str(payload.get("category", "")).strip()
            filename = str(payload.get("filename", "")).strip()

            _dict_category_config_key(category)
            if not _is_safe_dict_filename(filename):
                handler._send_json({"error": "invalid dictionary filename"}, status=HTTPStatus.BAD_REQUEST)
                return

            dict_dir = _common_dict_directory()
            os.makedirs(dict_dir, exist_ok=True)
            file_path = os.path.join(dict_dir, filename)
            if not os.path.exists(file_path):
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write("")

            category_map = _read_common_dict_category_map(dict_dir)
            category_map[filename] = category
            _write_common_dict_category_map(dict_dir, category_map)

            handler._send_json({"success": True, "filename": filename, "path": file_path})
        except ValueError as exc:
            handler._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to create common dictionary file: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # POST /api/dictionaries/common/save
    if path == "/api/dictionaries/common/save":
        try:
            payload = handler._read_json_body()
            filename = str(payload.get("filename", "")).strip()
            content = _normalize_dict_text(str(payload.get("content", "")))

            if not _is_safe_dict_filename(filename):
                handler._send_json({"error": "invalid dictionary filename"}, status=HTTPStatus.BAD_REQUEST)
                return

            dict_dir = _common_dict_directory()
            os.makedirs(dict_dir, exist_ok=True)
            file_path = os.path.join(dict_dir, filename)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            handler._send_json({"success": True, "filename": filename})
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to save common dictionary file: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # POST /api/dictionaries/common/delete
    if path == "/api/dictionaries/common/delete":
        try:
            payload = handler._read_json_body()
            filename = str(payload.get("filename", "")).strip()
            if not _is_safe_dict_filename(filename):
                handler._send_json({"error": "invalid dictionary filename"}, status=HTTPStatus.BAD_REQUEST)
                return

            file_path = os.path.join(_common_dict_directory(), filename)
            if os.path.isfile(file_path):
                os.remove(file_path)

            dict_dir = _common_dict_directory()
            category_map = _read_common_dict_category_map(dict_dir)
            if filename in category_map:
                del category_map[filename]
                _write_common_dict_category_map(dict_dir, category_map)

            handler._send_json({"success": True, "filename": filename})
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to delete common dictionary file: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    handler._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)


def do_put(handler: Any, registry: JobRegistry) -> None:
    if not handler._require_write_auth():
        return
    parsed = urlparse(handler.path)
    path = parsed.path

    if path == "/api/app-settings":
        try:
            payload = handler._read_json_body()
            settings = AppSettings.save_app_settings(payload)
            # 同步更新并发上限数值；线程池在下次 submit 时懒重建
            new_max = settings.get("maxConcurrentJobs")
            if isinstance(new_max, int) and new_max > 0 and new_max != registry._max_workers:
                LOGGER.info(f"[并发] maxConcurrentJobs: {registry._max_workers} -> {new_max}（下次提交任务时生效）")
                registry._max_workers = new_max
            handler._send_json(settings)
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to write app settings: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # PUT /api/backend-profiles/:name
    if path.startswith("/api/backend-profiles/"):
        profile_name = unquote(path.split("/", 3)[-1])
        if not profile_name:
            handler._send_json({"error": "profile name is required"}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = handler._read_json_body()
            profile_data = payload.get("profile")
            if profile_data is None:
                handler._send_json({"error": "profile field is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            data = _read_backend_profiles()
            if "profiles" not in data:
                data["profiles"] = {}
            data["profiles"][profile_name] = profile_data
            _write_backend_profiles(data)
            handler._send_json({"success": True, "name": profile_name})
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to write profile: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    # PUT /api/projects/:id/config
    if path.startswith("/api/projects/") and path.endswith("/config"):
        parts = path.split("/")
        if len(parts) < 5:
            handler._send_json({"error": "invalid project path"}, status=HTTPStatus.BAD_REQUEST)
            return
        project_id = parts[3]
        try:
            project_dir = _safe_project_dir(project_id)
        except ValueError as exc:
            handler._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = handler._read_json_body()
            config_data = payload.get("config")
            config_name = payload.get("config_file_name", "config.yaml")
            if config_data is None:
                handler._send_json({"error": "config field is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            config_path = os.path.join(project_dir, config_name)
            if not os.path.isfile(config_path):
                handler._send_json({"error": f"config file not found: {config_name}"}, status=HTTPStatus.NOT_FOUND)
                return
            _write_yaml_file(config_path, config_data)
            # 向导保存设置时可能携带「生成示例文件」的阶段键集合（独立于禁用阶段）
            sample_raw = payload.get("sample_stages")
            if isinstance(sample_raw, list):
                sample_stages = {k for k in sample_raw if isinstance(k, str)}
                cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
                input_dir = os.path.join(project_dir, INPUT_FOLDERNAME)
                input_files = [
                    n
                    for n in os.listdir(input_dir)
                    if os.path.isfile(os.path.join(input_dir, n))
                    and not n.startswith("_")
                ] if os.path.isdir(input_dir) else []
                _write_stage_samples(
                    cache_dir,
                    sample_stages,
                    force=False,
                    input_files=input_files,
                )
            handler._send_json({"success": True, "project_dir": project_dir, "config_file_name": config_name})
        except json.JSONDecodeError:
            handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            handler._send_json({"error": f"failed to write config: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return

    handler._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)


def do_delete(handler: Any) -> None:
    if not handler._require_write_auth():
        return
    parsed = urlparse(handler.path)
    path = parsed.path

    # DELETE /api/backend-profiles/:name
    if path.startswith("/api/backend-profiles/"):
        profile_name = unquote(path.split("/", 3)[-1])
        if not profile_name:
            handler._send_json({"error": "profile name is required"}, status=HTTPStatus.BAD_REQUEST)
            return
        data = _read_backend_profiles()
        profiles = data.get("profiles", {})
        if profile_name not in profiles:
            handler._send_json({"error": f"profile not found: {profile_name}"}, status=HTTPStatus.NOT_FOUND)
            return
        del data["profiles"][profile_name]
        _write_backend_profiles(data)
        handler._send_json({"success": True, "name": profile_name})
        return

    handler._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)


def handle_init_project(handler: Any) -> None:
    """POST /api/projects/init：在服务端 workspace 根下按客户端给定名称创建项目。"""
    try:
        payload = handler._read_json_body()
    except (ValueError, TypeError, json.JSONDecodeError):
        handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        return
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        handler._send_json({"error": "name is required"}, status=HTTPStatus.BAD_REQUEST)
        return
    try:
        project_dir = _resolve_new_project_dir(name.strip())
    except ValueError as exc:
        handler._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        return
    overwrite = bool(payload.get("overwrite", False))
    if os.path.exists(project_dir) and not overwrite:
        handler._send_json(
            {"error": f"项目已存在: {project_dir}"},
            status=HTTPStatus.CONFLICT,
        )
        return
    # 向导传入的流水线阶段开关（仅收集 enable* 布尔键）与外部信息
    pipeline_raw = payload.get("pipeline")
    pipeline = (
        {
            k: bool(v)
            for k, v in pipeline_raw.items()
            if k.startswith("enable") and isinstance(v, bool)
        }
        if isinstance(pipeline_raw, dict)
        else None
    )
    game_info_raw = payload.get("game_info")
    game_info = game_info_raw if isinstance(game_info_raw, str) else ""
    # 用户勾选「生成示例文件」的阶段键集合（独立于禁用阶段）
    sample_raw = payload.get("sample_stages")
    sample_stages = (
        {k for k in sample_raw if isinstance(k, str)}
        if isinstance(sample_raw, list)
        else None
    )
    try:
        created = _create_project_layout(
            project_dir,
            force=overwrite,
            pipeline=pipeline,
            game_info=game_info,
            sample_stages=sample_stages,
        )
    except OSError as exc:
        handler._send_json({"error": f"创建项目失败: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return
    handler._send_json(
        {
            "project_id": encode_project_dir(project_dir),
            "project_dir": project_dir,
            "created": created,
            "config_file_name": "config.yaml",
        },
        status=HTTPStatus.CREATED,
    )


def handle_import_files(handler: Any, project_dir: str) -> None:
    """POST /api/projects/:id/import：接收上传字节流或本地源路径写入 gt_input，已存在则跳过。

    支持三种来源：
    - multipart/form-data：浏览器/Web 回退，逐文件上传；
    - JSON files/base64：便于测试与 Web 回退；
    - JSON source_paths：桌面端把用户经系统对话框选中的真实路径交给后端读取，
      所有磁盘 IO 收敛到后端，避免前端直接操作文件（绕过后端安全校验）。
    """
    content_type = handler.headers.get("Content-Type", "")
    try:
        if content_type.startswith("multipart/form-data"):
            files = parse_multipart_files(handler)
        else:
            payload = handler._read_json_body()
            files = parse_json_import_files(handler, payload)
            source_paths = payload.get("source_paths")
            if isinstance(source_paths, list):
                files.extend(collect_files_from_source_paths(handler, source_paths))
    except (ValueError, TypeError, base64.binascii.Error) as exc:
        handler._send_json({"error": f"请求解析失败: {exc}"}, status=HTTPStatus.BAD_REQUEST)
        return
    except Exception as exc:
        handler._send_json({"error": f"请求解析失败: {exc}"}, status=HTTPStatus.BAD_REQUEST)
        return

    imported: list[str] = []
    skipped: list[str] = []
    for filename, content in files:
        if not _is_safe_dict_filename(filename):
            skipped.append(filename)
            continue
        try:
            target = safe_under_project(project_dir, os.path.join(INPUT_FOLDERNAME, filename))
        except ValueError:
            skipped.append(filename)
            continue
        if os.path.exists(target):
            skipped.append(filename)
            continue
        try:
            with open(target, "wb") as _f:
                _f.write(content)
            imported.append(filename)
        except OSError:
            skipped.append(filename)
    handler._send_json({"imported": imported, "skipped": skipped})


def parse_multipart_files(handler: Any) -> list[tuple[str, bytes]]:
    """用 email 模块稳健解析 multipart/form-data，提取带文件名的分片。"""
    content_type = handler.headers.get("Content-Type", "")
    raw = handler._read_raw_body()
    head = b"Content-Type: " + content_type.encode("utf-8", "replace") + b"\r\n\r\n" + raw
    msg = email.message_from_bytes(head, policy=email.policy.default)
    files: list[tuple[str, bytes]] = []
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True)
        files.append((filename, payload if payload is not None else b""))
    return files


def parse_json_import_files(handler: Any, payload: dict[str, Any]) -> list[tuple[str, bytes]]:
    """解析 JSON 上传（便于测试与 Web 回退）：单文件或 files 数组，内容为 base64。"""
    files: list[tuple[str, bytes]] = []
    single = payload.get("filename")
    if isinstance(single, str) and "content_b64" in payload:
        files.append((single, base64.b64decode(str(payload["content_b64"]))))
        return files
    file_list = payload.get("files")
    if isinstance(file_list, list):
        for item in file_list:
            if isinstance(item, dict) and isinstance(item.get("filename"), str):
                b64 = item.get("content_b64")
                if isinstance(b64, str):
                    files.append((item["filename"], base64.b64decode(b64)))
    return files


def collect_files_from_source_paths(handler: Any, source_paths: list[Any]) -> list[tuple[str, bytes]]:
    """收集本地源路径下的常规文件，以 basename 为导入名扁平写入 gt_input。

    目录递归展开（不跟随符号链接目录，避免环路）；符号链接文件与非常规文件跳过。
    以 basename 去重，与既有 upload 导入端点一致：文件始终落在 gt_input 顶层。
    """
    out: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for src in source_paths:
        if not isinstance(src, str):
            continue
        path = os.path.normpath(src)
        # 跳过符号链接源，避免借软链读取项目目录之外的内容
        if os.path.islink(path):
            continue
        if not os.path.exists(path):
            continue
        candidates: list[str] = []
        if os.path.isdir(path):
            for root, _dirs, fnames in os.walk(path, followlinks=False):
                for fn in fnames:
                    candidates.append(os.path.join(root, fn))
        elif os.path.isfile(path):
            candidates.append(path)
        else:
            continue
        for fpath in candidates:
            if not os.path.isfile(fpath) or os.path.islink(fpath):
                continue
            base = os.path.basename(fpath)
            if base in seen:
                continue
            try:
                with open(fpath, "rb") as _f:
                    data = _f.read()
            except OSError:
                continue
            seen.add(base)
            out.append((base, data))
    return out


def handle_log_receive(handler: Any) -> None:
    """POST /api/log：前端日志统一入口，追加写入全局 frontend.log。"""
    try:
        payload = handler._read_json_body()
    except (ValueError, TypeError, json.JSONDecodeError):
        handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        return
    if not isinstance(payload, dict):
        handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        return
    # 按 AppSettings.writeFrontendLog 决定是否落盘 frontend.log。
    # 默认关闭（新建项目/未自定义时），仅 error.log 保留写入。
    if not AppSettings.load_app_settings().get("writeFrontendLog", False):
        LOGGER.debug("按 AppSettings.writeFrontendLog=false，跳过 frontend.log 写入")
        handler._send_json({"ok": True, "skipped": True})
        return
    source = _sanitize_log_field(str(payload.get("source", "frontend"))) or "frontend"
    raw_level = _sanitize_log_field(str(payload.get("level", "info"))).lower()
    level = raw_level if raw_level in _VALID_LOG_LEVELS else "info"
    message = payload.get("message")
    if message is None:
        lines = payload.get("lines")
        message = "\n".join(str(x) for x in lines) if isinstance(lines, list) else ""
    if not isinstance(message, str):
        message = str(message)
    # frontend.log 归集到翻译项目目录：携带合法 project_id 时写入
    # project_dir/frontend.log，否则回退全局（如未打开项目时的日志）
    project_id = payload.get("project_id")
    if project_id:
        try:
            pdir = _safe_project_dir(str(project_id))
            log_path = os.path.join(pdir, "frontend.log")
        except ValueError:
            log_path = os.path.join(_workspace_root(), "frontend.log")
    else:
        log_path = os.path.join(_workspace_root(), "frontend.log")
    try:
        parent = os.path.dirname(log_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as _f:
            for line in message.split("\n"):
                # 去掉回车，避免 CRLF 在日志中造成错位
                stripped = line.replace("\r", "")
                _f.write(f"[{ts}] [{level}] [{source}] {stripped}\n")
        handler._send_json({"ok": True})
    except OSError as exc:
        handler._send_json({"error": f"failed to write log: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)


def handle_parse_dictionary(handler: Any) -> None:
    """POST /api/dictionaries/parse：将字典文本解析为结构化行（纯计算，无副作用）。"""
    try:
        payload = handler._read_json_body()
    except (ValueError, TypeError, json.JSONDecodeError):
        handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        return
    if not isinstance(payload, dict):
        handler._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
        return
    category = str(payload.get("category", "pre")).strip()
    if category not in ("pre", "gpt", "gpth", "gptnh", "post", "h", "forbiddenh", "forbiddennh"):
        handler._send_json({"error": f"invalid category: {category}"}, status=HTTPStatus.BAD_REQUEST)
        return
    content = payload.get("content", "")
    if not isinstance(content, str):
        content = str(content)
    # 与引擎 load_dic 的 line.rstrip("\r\n") 对齐：按行剥除回车符，
    # 避免 CRLF 文本把 \r 泄漏进解析出的字段值
    rows = [asdict(parse_dict_line(line.rstrip("\r"), category)) for line in content.split("\n")]
    handler._send_json({"rows": rows})
