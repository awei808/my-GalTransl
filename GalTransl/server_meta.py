"""问题类型目录、人名替换表与插件扫描（0.4.10 从 server.py 抽出）。

职责：
- 问题类型目录（_PROBLEM_TYPE_CATALOG / _list_problem_types）；
- 翻译指南列表（_list_translation_guidelines）；
- 插件清单扫描（_scan_plugins）；
- 项目人名替换表加载与查询（_load_project_name_dict / _lookup_name）。

_NAME_DICT_CACHE 与其使用者 _load_project_name_dict 同模块；模块级可变缓存由
reset_caches() 重置，server.py 在 import 段调用（reload(server) 不重新导入子模块）。
"""
from __future__ import annotations

import os
from typing import Any, Tuple

from GalTransl.Utils import resolve_app_dir
from GalTransl.server_config_schema import _read_yaml_file


_PROBLEM_TYPE_CATALOG: list[dict[str, str]] = [
    {"name": "词频过高", "description": "某字在译文中重复大于 20 次（且远多于原文）。"},
    {"name": "标点错漏", "description": "括号/引号/冒号等标点与原文不一致。"},
    {"name": "残留日文", "description": "译文中残留日文平假名或片假名。"},
    {"name": "丢失换行", "description": "译文缺少原文中的行内换行。"},
    {"name": "多加换行", "description": "译文换行符比原文多，可能导致溢出。"},
    {"name": "比日文长", "description": "译文长度超过原文 1.3 倍（常用，宽松阈值）。"},
    {"name": "比日文长严格", "description": "译文长度超过原文（零容忍，严格阈值）。"},
    {"name": "字典使用", "description": "没有按 GPT 字典的要求翻译。"},
    {"name": "引入英文", "description": "原文无英文，但译文引入了英文单词。"},
    {"name": "语言不通", "description": "译文包含大量非 GBK 字符（仅对中文目标语言生效）。"},
    {"name": "缺控制符", "description": "译文缺少原文中的控制符（如 \\n、变量标记等）。"},
    {"name": "独白男他", "description": "独白（无name）译文出现'他'。"},
    {"name": "单句过长", "description": "译文单句过长，平均分句长度超过阈值（avgSentenceLengthThreshold）。"},
    {"name": "换行位置异常", "description": "换行符未紧跟中文标点（逗号/顿号/句号等）、空格、Tab、emoji 或颜文字之后，断行位置可能不当。"},
    {"name": "定语过长", "description": "译文出现「是……的」结构且中间定语长度超过「定语最大长度」阈值。"},
    {"name": "用词不当", "description": "非 H 场景译文含禁用词（按禁用词库匹配），或 H 剧情区间译文出现不符合 H 场景的词语（按 H 词库匹配）。"},
    {"name": "状语过长", "description": "译文出现「在……中/里」或「……地」状语且中间长度超过「状语最大长度」阈值。"},
    {"name": "频繁换行", "description": "译文有效字符数不足却切出过多小句（<20字符且≥3小句，或<10字符且≥2小句），短译文却频繁断句。（测试中，可能误检）"},
    {"name": "疑似错误", "description": "AI 语义检测（ForSemCheck）判定原文与译文语义存在极大差异（疑似错译/漏译/译文串行），可由 ForSemCheckAgain 二次复核确认/撤销。"},
    {"name": "词语色彩不一致", "description": "AI 词语色彩检查（ForToneCheck）判定译文用词色彩与批次区间标注的用词色彩明显不符，标记不改译文；可由统一问题修复按标注方向调整。"},
]

# name 替换表加载缓存：project_dir -> (mtime_ns, name_dict)
_NAME_DICT_CACHE: dict[str, Tuple[int, dict]] = {}


def _find_name_col(header, new_name: str, old_name: str) -> int:
    """查找表头列索引，优先新列名，回退旧列名。"""
    if new_name in header:
        return header.index(new_name)
    if old_name in header:
        return header.index(old_name)
    return -1


def _load_project_name_dict(project_dir: str) -> dict:
    """加载项目 name替换表（csv/xlsx）为 SRC→DST 映射，带 mtime 缓存。"""
    csv_path = os.path.join(project_dir, "name替换表.csv")
    xlsx_path = os.path.join(project_dir, "name替换表.xlsx")
    path = csv_path if os.path.isfile(csv_path) else (xlsx_path if os.path.isfile(xlsx_path) else "")
    if not path:
        return {}
    try:
        mtime = os.stat(path).st_mtime_ns
        cached = _NAME_DICT_CACHE.get(project_dir)
        if cached and cached[0] == mtime:
            return cached[1]
    except OSError:
        return {}

    name_dict: dict[str, str] = {}
    if path.endswith(".csv"):
        try:
            import csv as _csv
            with open(path, "r", newline="", encoding="utf-8-sig") as f:
                reader = _csv.reader(f)
                header = next(reader, None)
                if header:
                    src_idx = _find_name_col(header, "SRC_Name", "JP_Name")
                    dst_idx = _find_name_col(header, "DST_Name", "CN_Name")
                    if src_idx >= 0 and dst_idx >= 0:
                        for row in reader:
                            if len(row) > max(src_idx, dst_idx) and row[dst_idx].strip():
                                name_dict[row[src_idx]] = row[dst_idx].strip()
        except Exception:
            pass
    elif path.endswith(".xlsx"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path)
            sheet = wb.active
            header = [cell.value for cell in sheet[1]]
            src_idx = _find_name_col(header, "SRC_Name", "JP_Name")
            dst_idx = _find_name_col(header, "DST_Name", "CN_Name")
            if src_idx >= 0 and dst_idx >= 0:
                for row in sheet.iter_rows(min_row=2):
                    src_val = row[src_idx].value if src_idx < len(row) else None
                    dst_val = row[dst_idx].value if dst_idx < len(row) else None
                    if src_val is not None and dst_val is not None and str(dst_val).strip():
                        name_dict[str(src_val)] = str(dst_val)
        except Exception:
            pass
    _NAME_DICT_CACHE[project_dir] = (mtime, name_dict)
    return name_dict


def _lookup_name(name, name_dict: dict):
    """将原文名（string 或 list）按译名表替换为译名，未收录回退原文。"""
    if not name_dict:
        return name
    if isinstance(name, list):
        return [name_dict.get(n, n) for n in name]
    return name_dict.get(name, name)


def _list_problem_types() -> list[dict[str, str]]:
    """Return the list of problem types supported by the backend analyzer.

    Each entry has ``name`` (used in YAML config) and a short ``description``.
    Kept in sync with :class:`GalTransl.ConfigHelper.CProblemType` and
    :func:`GalTransl.Problem.find_problems`.
    """
    return list(_PROBLEM_TYPE_CATALOG)


def _list_translation_guidelines() -> list[str]:
    """List translation guideline filenames under the ``translation_guidelines`` folder."""
    guidelines_dir = os.path.join(resolve_app_dir(), "translation_guidelines")
    if not os.path.isdir(guidelines_dir):
        return []
    result: list[str] = []
    for name in sorted(os.listdir(guidelines_dir)):
        full = os.path.join(guidelines_dir, name)
        if not os.path.isfile(full):
            continue
        lower = name.lower()
        if lower.endswith(".md") or lower.endswith(".txt"):
            result.append(name)
    return result


def _scan_plugins() -> list[dict[str, Any]]:
    """Scan the plugins directory and return plugin metadata."""
    plugins_dir = os.path.join(resolve_app_dir(), "plugins")
    result = []
    if not os.path.isdir(plugins_dir):
        return result
    for name in sorted(os.listdir(plugins_dir)):
        yaml_path = os.path.join(plugins_dir, name, f"{name}.yaml")
        if not os.path.isfile(yaml_path):
            continue
        try:
            info = _read_yaml_file(yaml_path)
            core = info.get("Core", {})
            settings = info.get("Settings", {})
            result.append({
                "name": name,
                "display_name": core.get("Name", name),
                "version": core.get("Version", ""),
                "author": core.get("Author", ""),
                "description": core.get("Description", ""),
                "type": core.get("Type", "unknown").lower(),
                "module": core.get("Module", name),
                "settings": settings,
            })
        except Exception:
            continue
    return result




def reset_caches() -> None:
    """重置本模块的模块级缓存（由 server.py 在 import 段调用）。"""
    _NAME_DICT_CACHE.clear()
