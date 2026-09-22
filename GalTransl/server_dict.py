"""字典读写与公共字典治理（0.4.10 从 server.py 抽出）。

职责：
- 项目字典与公共字典的读取 / 保存 / 删除（_read_dict_file_payload / _collect_*_payload）；
- 公共字典目录扫描与「配置自动补全」（_ensure_common_dicts_in_config 等）；
- 字典文件名与配置文件名的路径安全校验（_is_safe_dict_filename / _is_path_within 等）；
- hCheckDict → forbiddenDictH 的历史配置迁移（_migrate_h_check_dict_config）。

_common_dict_directory 与 _ensure_common_dicts_in_config 必须同模块：后者调用前者，
且测试对前者做直接赋值/patch（跨模块会静默打空）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from GalTransl import LOGGER
from GalTransl.Dictionary import _COMMENT_PREFIXES
from GalTransl.Utils import resolve_app_dir
from GalTransl.server_config_schema import _read_yaml_file, _write_yaml_file


def _ensure_empty_project_dict_file(project_dir: str, filename: str) -> None:
    """项目字典文件不存在时创建空文件（幂等）。"""
    file_path = os.path.join(project_dir, filename)
    if os.path.isfile(file_path):
        return
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("")
    except OSError as exc:
        LOGGER.warning(f"[dict] 创建项目字典文件失败：{filename}：{exc}")


def _migrate_h_check_dict_config(project_dir: str, config_name: str) -> None:
    """把旧「hCheckDict」配置迁移到「禁用词字典」新键，并保证 h/非 h 项目文件对称（幂等）。

    旧配置形如 `dictionary.hCheckDict: [02H场景用词检测.txt, ...]`，现统一为：
    - `dictionary.forbiddenDictH`（H 场景禁用词），文件名 02H场景用词检测.txt → 禁用词_h.txt。
    - `dictionary.forbiddenDictNonH`（非 H 场景禁用词），缺失时补默认公共文件。

    自动迁移后仍保证「项目禁用词_h.txt / 项目禁用词_非h.txt」对称存在：
    若非 h 已含项目文件而 h 缺失，则补齐 h 项目文件引用，避免前端禁用词 tab 只见非 h。
    """
    if not _is_safe_config_filename(config_name):
        return
    config_path = os.path.join(project_dir, config_name)
    if not os.path.isfile(config_path):
        return
    try:
        data = _read_yaml_file(config_path)
        dict_cfg_raw = data.get("dictionary", {})
        if not isinstance(dict_cfg_raw, dict):
            return
        changed = False
        # 1) 旧 hCheckDict → forbiddenDictH（仅当尚未迁移）
        if "forbiddenDictH" not in dict_cfg_raw and "hCheckDict" in dict_cfg_raw:
            old_list = dict_cfg_raw.get("hCheckDict", [])
            if not isinstance(old_list, list):
                old_list = [old_list] if old_list else []
            # 旧 H 词库文件名统一迁移到「禁用词_h.txt」风格（公共与项目字典都覆盖）
            new_list = [
                str(x)
                .replace("02H场景用词检测.txt", "禁用词_h.txt")
                .replace("项目H场景用词检测.txt", "项目禁用词_h.txt")
                for x in old_list
            ]
            dict_cfg_raw["forbiddenDictH"] = new_list
            del dict_cfg_raw["hCheckDict"]
            changed = True
            LOGGER.info(f"[dict] 已自动迁移 hCheckDict → forbiddenDictH：{project_dir}/{config_name}")
        # 2) 非 h 键缺失 → 补默认（公共 + 项目文件），并创建空项目文件避免加载告警
        if "forbiddenDictNonH" not in dict_cfg_raw:
            dict_cfg_raw["forbiddenDictNonH"] = [
                "禁用词_非h.txt",
                f"{DICT_PROJECT_MARKER}项目禁用词_非h.txt",
            ]
            _ensure_empty_project_dict_file(project_dir, "项目禁用词_非h.txt")
            changed = True
        # 3) h 缺失项目文件而非 h 已有 → 对称补 h 项目文件（兼容已迁移但不对称的旧项目）
        nh_list = dict_cfg_raw.get("forbiddenDictNonH", [])
        if not isinstance(nh_list, list):
            nh_list = [nh_list] if nh_list else []
        h_list = dict_cfg_raw.get("forbiddenDictH", [])
        if not isinstance(h_list, list):
            h_list = [h_list] if h_list else []
        if not any(str(x).startswith(DICT_PROJECT_MARKER) for x in h_list) and any(
            str(x).startswith(DICT_PROJECT_MARKER) for x in nh_list
        ):
            h_list.append(f"{DICT_PROJECT_MARKER}项目禁用词_h.txt")
            dict_cfg_raw["forbiddenDictH"] = h_list
            changed = True
            # 对称创建空项目 h 禁用词文件（与非 h 文件一致，避免加载时缺文件告警）
            _ensure_empty_project_dict_file(project_dir, "项目禁用词_h.txt")
            LOGGER.info(f"[dict] 已补齐项目 H 禁用词字典引用：{project_dir}/{config_name}")
        if changed:
            data["dictionary"] = dict_cfg_raw
            _write_yaml_file(config_path, data)
    except Exception as exc:
        LOGGER.warning(f"[dict] hCheckDict 自动迁移失败：{exc}")


DICT_PROJECT_MARKER = "(project_dir)"
COMMON_DICT_CATEGORY_MAP = ".category_map.json"


def _is_safe_dict_filename(filename: str) -> bool:
    if not isinstance(filename, str):
        return False
    trimmed = filename.strip()
    if not trimmed:
        return False
    # 拒绝 . 与 .. 段：basename("..") == ".." 不含分隔符会被放过，
    # 拼接后会解析到字典目录的父级，造成路径穿越
    if trimmed in (".", ".."):
        return False
    if trimmed != os.path.basename(trimmed):
        return False
    if any(sep in trimmed for sep in ("/", "\\")):
        return False
    return True


def _is_safe_config_filename(filename: str) -> bool:
    if not isinstance(filename, str):
        return False
    trimmed = filename.strip()
    if not trimmed:
        return False
    if trimmed != os.path.basename(trimmed):
        return False
    if any(sep in trimmed for sep in ("/", "\\")):
        return False
    if ".." in trimmed:
        return False
    return True


def _is_path_within(base_dir: str, target_path: str) -> bool:
    base_abs = os.path.abspath(base_dir)
    target_abs = os.path.abspath(target_path)
    try:
        common = os.path.commonpath([base_abs, target_abs])
    except ValueError:
        return False
    return common == base_abs


def _normalize_dict_text(content: str) -> str:
    return str(content or "").replace("\r\n", "\n").replace("\r", "\n")


def _dict_category_config_key(category: str) -> str:
    if category == "pre":
        return "preDict"
    if category in ("gpt", "gpth", "gptnh"):
        return "gpt.dict"
    if category == "post":
        return "postDict"
    if category in ("h", "forbiddenh"):
        return "forbiddenDictH"
    if category == "forbiddennh":
        return "forbiddenDictNonH"
    raise ValueError(f"invalid dictionary category: {category}")


def _read_dict_file_payload(file_path: str) -> dict[str, Any]:
    if not os.path.isfile(file_path):
        return {
            "path": file_path,
            "lines": [],
            "count": 0,
            "mtime": None,
            "error": "file not found",
        }
    try:
        mtime = os.path.getmtime(file_path)
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        return {
            "path": file_path,
            "lines": lines,
            "count": len([
                line_item
                for line_item in lines
                if line_item.strip() and not line_item.lstrip().startswith(_COMMENT_PREFIXES)
            ]),
            "mtime": mtime,
        }
    except Exception:
        return {
            "path": file_path,
            "lines": [],
            "count": 0,
            "mtime": None,
            "error": "failed to read",
        }


def _collect_project_dict_payload(project_dir: str, config_name: str) -> dict[str, Any]:
    if not _is_safe_config_filename(config_name):
        raise ValueError("invalid config filename")
    config_path = os.path.join(project_dir, config_name)
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"config file not found: {config_name}")

    data = _read_yaml_file(config_path)
    dict_cfg = data.get("dictionary", {})

    pre_all = [str(x) for x in dict_cfg.get("preDict", [])]
    gpt_all = [str(x) for x in dict_cfg.get("gpt.dict", [])]
    post_all = [str(x) for x in dict_cfg.get("postDict", [])]
    # h 禁用词：forbiddenDictH 优先，未配置时回退旧 hCheckDict
    h_all = [str(x) for x in dict_cfg.get("forbiddenDictH", dict_cfg.get("hCheckDict", []))]
    fnh_all = [str(x) for x in dict_cfg.get("forbiddenDictNonH", [])]

    pre_files = [x for x in pre_all if x.startswith(DICT_PROJECT_MARKER)]
    gpt_files = [x for x in gpt_all if x.startswith(DICT_PROJECT_MARKER)]
    post_files = [x for x in post_all if x.startswith(DICT_PROJECT_MARKER)]
    h_files = [x for x in h_all if x.startswith(DICT_PROJECT_MARKER)]
    fnh_files = [x for x in fnh_all if x.startswith(DICT_PROJECT_MARKER)]

    # GPT 字典按文件名后缀拆 h/非h（_h / _非h），供前端分组展示；运行时仍用 gpt_files 全量
    def _gpt_scene(fname: str) -> str:
        lower = fname.lower()
        return "h" if ("_h" in lower and "非h" not in lower) else "nh"

    gpt_files_h = [x for x in gpt_files if _gpt_scene(x) == "h"]
    gpt_files_nh = [x for x in gpt_files if _gpt_scene(x) == "nh"]

    dict_contents: dict[str, dict[str, Any]] = {}
    for file_key in pre_files + gpt_files + post_files + h_files + fnh_files:
        clean = file_key.replace(DICT_PROJECT_MARKER, "").strip()
        if not _is_safe_dict_filename(clean):
            dict_contents[file_key] = {
                "path": os.path.join(project_dir, clean),
                "lines": [],
                "count": 0,
                "mtime": None,
                "error": "invalid dictionary filename",
            }
            continue
        file_path = os.path.join(project_dir, clean)
        if not _is_path_within(project_dir, file_path):
            dict_contents[file_key] = {
                "path": file_path,
                "lines": [],
                "count": 0,
                "mtime": None,
                "error": "dictionary path escapes project directory",
            }
            continue
        dict_contents[file_key] = _read_dict_file_payload(file_path)

    return {
        "project_dir": project_dir,
        "config_file_name": config_name,
        "pre_dict_files": pre_files,
        "gpt_dict_files": gpt_files,
        "gpt_dict_files_h": gpt_files_h,
        "gpt_dict_files_nh": gpt_files_nh,
        "post_dict_files": post_files,
        "h_dict_files": h_files,
        "forbidden_dict_files_h": h_files,
        "forbidden_dict_files_nh": fnh_files,
        "dict_contents": dict_contents,
    }


def _common_dict_directory() -> str:
    # 程序目录口径（打包版不能用 cwd：进程 cwd 取决于启动方式）
    return os.path.join(resolve_app_dir(), "Dict")


def _ensure_common_dicts_in_config(
    project_dir: str, config_name: str, dict_dir: Optional[str] = None
) -> bool:
    """项目字典配置缺少公共字典时自动补全（幂等）。

    扫描公共字典目录（默认服务进程 cwd/Dict）下的全部字典文件，按文件名
    分类映射到对应配置键（preDict/gpt.dict/postDict/forbiddenDictH/
    forbiddenDictNonH），项目配置中缺失的无前缀引用会被追加补上。

    补全条目为无前缀公共文件名，运行时依赖项目 defaultDictFolder 指向
    该公共字典目录（约定为 "Dict"）；若项目改动了 defaultDictFolder，
    补全条目可能解析不到文件，仅产生加载告警而不影响其他流程。

    用户主动从配置移除的公共字典也会被重新补回；如需排除某公共字典，
    只能从公共字典目录中删除对应文件。

    Args:
        project_dir: 项目绝对路径。
        config_name: 配置文件名。
        dict_dir: 公共字典目录；None 时取默认（服务进程 cwd/Dict）。

    Returns:
        是否发生配置变更（供幂等判断）。
    """
    if not _is_safe_config_filename(config_name):
        return False
    config_path = os.path.join(project_dir, config_name)
    if not os.path.isfile(config_path):
        return False
    if dict_dir is None:
        dict_dir = _common_dict_directory()
    if not os.path.isdir(dict_dir):
        return False
    try:
        data = _read_yaml_file(config_path)
        dict_cfg_raw = data.get("dictionary", {})
        if not isinstance(dict_cfg_raw, dict):
            return False
        category_map = _read_common_dict_category_map(dict_dir)
        # 公共字典文件名 → 配置键 → 待补列表
        to_add: dict[str, list[str]] = {}
        for name in sorted(os.listdir(dict_dir)):
            if (
                not os.path.isfile(os.path.join(dict_dir, name))
                or name == COMMON_DICT_CATEGORY_MAP
            ):
                continue
            category = category_map.get(name) or _categorize_common_dict_file(name)
            try:
                list_key = _dict_category_config_key(category)
            except ValueError:
                continue
            to_add.setdefault(list_key, []).append(name)
        changed = False
        for list_key, names in to_add.items():
            current_items = dict_cfg_raw.get(list_key, [])
            if isinstance(current_items, list):
                current_list = [str(x) for x in current_items]
            elif current_items in (None, ""):
                current_list = []
            else:
                current_list = [str(current_items)]
            added = [name for name in names if name not in current_list]
            if not added:
                continue
            dict_cfg_raw[list_key] = current_list + added
            changed = True
            LOGGER.info(
                f"[dict] 已补全缺失公共字典：{project_dir}/{config_name} "
                f"→ {list_key}: {', '.join(added)}"
            )
        if changed:
            data["dictionary"] = dict_cfg_raw
            _write_yaml_file(config_path, data)
        return changed
    except Exception as exc:
        LOGGER.warning(f"[dict] 公共字典兜底补全失败：{exc}")
        return False


def _ensure_project_dict_file_configured(project_dir: str, config_name: str, category: str, filename: str) -> None:
    if not _is_safe_config_filename(config_name):
        raise ValueError("invalid config filename")
    if not _is_safe_dict_filename(filename):
        raise ValueError("invalid dictionary filename")

    config_path = os.path.join(project_dir, config_name)
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"config file not found: {config_name}")

    data = _read_yaml_file(config_path)
    dict_cfg_raw = data.get("dictionary", {})
    dict_cfg = dict_cfg_raw if isinstance(dict_cfg_raw, dict) else {}
    list_key = _dict_category_config_key(category)
    current_items = dict_cfg.get(list_key, [])

    if isinstance(current_items, list):
        current_list = [str(x) for x in current_items]
    elif current_items in (None, ""):
        current_list = []
    else:
        current_list = [str(current_items)]

    file_key = f"{DICT_PROJECT_MARKER}{filename}"
    if file_key in current_list:
        return

    current_list.append(file_key)
    dict_cfg[list_key] = current_list
    data["dictionary"] = dict_cfg
    _write_yaml_file(config_path, data)


def _common_dict_category_map_path(dict_dir: str) -> str:
    return os.path.join(dict_dir, COMMON_DICT_CATEGORY_MAP)


def _read_common_dict_category_map(dict_dir: str) -> dict[str, str]:
    map_path = _common_dict_category_map_path(dict_dir)
    if not os.path.isfile(map_path):
        return {}
    try:
        with open(map_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        result: dict[str, str] = {}
        for key, value in data.items():
            if _is_safe_dict_filename(str(key)) and str(value) in {
                "pre", "gpt", "gpth", "gptnh", "post", "h", "forbiddenh", "forbiddennh",
            }:
                result[str(key)] = str(value)
        return result
    except Exception:
        return {}


def _write_common_dict_category_map(dict_dir: str, category_map: dict[str, str]) -> None:
    map_path = _common_dict_category_map_path(dict_dir)
    tmp_path = map_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(category_map, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, map_path)


def _categorize_common_dict_file(filename: str) -> str:
    lower = filename.lower()
    # GPT 字典：h 判定优先于普通 gpt；显式 _h 后缀归 h，其余（含无后缀 / _非h）归非 h
    if "gpt" in lower:
        if "_h" in lower and "非h" not in lower:
            return "gpth"
        return "gptnh"
    if "post" in lower or "译后" in filename:
        return "post"
    if "hcheck" in lower or "h场景" in lower or "场景用词" in filename:
        return "h"
    # 禁用词字典：文件名含禁用词/forbidden；按 _h / _非h 后缀区分 h 与非 h
    if "禁用词" in filename or "forbidden" in lower:
        if "_h" in lower or "非h" not in lower:
            return "forbiddenh"
        return "forbiddennh"
    return "pre"


def _collect_common_dict_payload() -> dict[str, Any]:
    dict_dir = _common_dict_directory()
    os.makedirs(dict_dir, exist_ok=True)
    category_map = _read_common_dict_category_map(dict_dir)

    files = [
        name
        for name in sorted(os.listdir(dict_dir))
        if os.path.isfile(os.path.join(dict_dir, name)) and name != COMMON_DICT_CATEGORY_MAP
    ]

    pre_files: list[str] = []
    gpt_files: list[str] = []
    gpt_files_h: list[str] = []
    gpt_files_nh: list[str] = []
    post_files: list[str] = []
    h_files: list[str] = []
    fh_files: list[str] = []
    fnh_files: list[str] = []
    dict_contents: dict[str, dict[str, Any]] = {}

    for name in files:
        category = category_map.get(name) or _categorize_common_dict_file(name)
        # GPT 字典（含 h/非h 子分类）统一进 gpt_files，供运行时/现有逻辑使用
        if category in ("gpt", "gpth", "gptnh"):
            gpt_files.append(name)
            if category == "gpth":
                gpt_files_h.append(name)
            elif category == "gptnh":
                gpt_files_nh.append(name)
        elif category == "post":
            post_files.append(name)
        elif category == "h":
            h_files.append(name)
        elif category == "forbiddenh":
            fh_files.append(name)
        elif category == "forbiddennh":
            fnh_files.append(name)
        else:
            pre_files.append(name)
        dict_contents[name] = _read_dict_file_payload(os.path.join(dict_dir, name))

    return {
        "dict_dir": dict_dir,
        "pre_dict_files": pre_files,
        "gpt_dict_files": gpt_files,
        "gpt_dict_files_h": gpt_files_h,
        "gpt_dict_files_nh": gpt_files_nh,
        "post_dict_files": post_files,
        "h_dict_files": h_files,
        "forbidden_dict_files_h": fh_files,
        "forbidden_dict_files_nh": fnh_files,
        "dict_contents": dict_contents,
    }


