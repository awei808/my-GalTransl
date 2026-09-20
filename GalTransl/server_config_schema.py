"""配置模板 schema 与 YAML 读写工具（0.4.10 从 server.py 抽出）。

对外提供：
- YAML 读写（_read_yaml_file / _write_yaml_file）——被字典域、缓存域、脚手架复用；
- 默认配置模板解析（_get_default_config / _deep_merge_defaults）；
- 配置注释 schema（_parse_yaml_comments / _build_config_schema / _get_config_schema）。

两个模块级缓存（_DEFAULT_CONFIG_CACHE / _CONFIG_SCHEMA_CACHE）与其使用者
成对放在本模块，避免「状态在 server.py、函数在本模块」造成循环导入。
server.py 的 import 段末尾调用 reset_caches()，使 reload(server) 能重置缓存。
"""
from __future__ import annotations

import os
import threading
from typing import Any

from yaml import safe_load, safe_dump

from GalTransl.DefaultProjectConfig import DEFAULT_PROJECT_CONFIG_YAML


def _read_yaml_file(path: str) -> dict:
    """Read and parse a YAML file."""
    with open(path, "r", encoding="utf-8") as f:
        return safe_load(f) or {}


# 全局缓存：默认配置模板的解析结果，启动后首次使用解析一次
_DEFAULT_CONFIG_CACHE: dict | None = None
_DEFAULT_CONFIG_LOCK = threading.Lock()


def _get_default_config() -> dict:
    """返回默认配置模板的解析结果（带缓存，线程安全）。"""
    global _DEFAULT_CONFIG_CACHE
    if _DEFAULT_CONFIG_CACHE is None:
        with _DEFAULT_CONFIG_LOCK:
            if _DEFAULT_CONFIG_CACHE is None:
                _DEFAULT_CONFIG_CACHE = safe_load(DEFAULT_PROJECT_CONFIG_YAML) or {}
    return _DEFAULT_CONFIG_CACHE


def _deep_merge_defaults(cfg: dict, defaults: dict) -> dict:
    """将默认配置深合并进用户配置：仅补齐缺失键，已有键原样保留。

    用于旧项目升级场景——config.yaml 缺少新增配置段时，用默认值补全，
    使前端设置界面能显示并编辑这些新配置项。list 整体保留（不逐项合并）。
    """
    merged = dict(cfg)
    for key, default_val in defaults.items():
        if key not in merged:
            merged[key] = default_val
        elif isinstance(default_val, dict) and isinstance(merged[key], dict):
            merged[key] = _deep_merge_defaults(merged[key], default_val)
    return merged


def _write_yaml_file(path: str, data: dict) -> None:
    """Write data to a YAML file atomically."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        safe_dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    os.replace(tmp, path)


def _parse_yaml_comments(yaml_text: str) -> dict[str, str]:
    """解析 YAML 模板文本，提取每条参数路径→注释的描述映射。

    路径以点号分隔（如 common.gpt.numPerRequestTranslate）。
    列表项以 [] 标识（如 tokens[].endpoint）。
    """
    comments: dict[str, str] = {}
    stack: list[tuple[str, int]] = []  # (key, indent)

    for raw_line in yaml_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        indent = len(raw_line) - len(raw_line.lstrip())

        # 弹出比当前缩进更深或相同的栈帧（回到父级或同级）
        while stack and stack[-1][1] >= indent:
            stack.pop()

        # 提取注释部分（# 及其后文本）
        if '#' in stripped:
            content_part, _, raw_comment = stripped.partition('#')
            comment = raw_comment.strip()
        else:
            content_part = stripped
            comment = ''

        content_part = content_part.rstrip()

        # 列表项 - 把键挂在父路径下方
        if content_part.startswith('- '):
            list_content = content_part[2:]
            # list_content 可能是 "key: value" 或 "value"
            if ':' in list_content:
                key = list_content.split(':', 1)[0].strip()
            else:
                key = list_content.strip()
            if key:
                stack.append((key, indent))
        else:
            key = content_part.split(':', 1)[0].strip()
            if key:
                stack.append((key, indent))

        # 构建当前路径并记录注释
        if comment and stack:
            path_parts = []
            for k, _ in stack:
                path_parts.append(k)
            path = '.'.join(path_parts)
            # 取第一个注释（前面可能有连续的注释行）
            if path not in comments:
                comments[path] = comment

    return comments


def _build_config_schema() -> dict[str, Any]:
    """从 DEFAULT_PROJECT_CONFIG_YAML 模板生成配置 schema：
    返回 { parameters: { "path.to.key": "注释文本" } }（值为字符串注释，非对象）
    """
    comments = _parse_yaml_comments(DEFAULT_PROJECT_CONFIG_YAML)
    return {"parameters": comments}


# 全局缓存：配置模板的注释映射，启动时解析一次
_CONFIG_SCHEMA_CACHE: dict[str, Any] | None = None


def _get_config_schema() -> dict[str, Any]:
    global _CONFIG_SCHEMA_CACHE
    if _CONFIG_SCHEMA_CACHE is None:
        _CONFIG_SCHEMA_CACHE = _build_config_schema()
    return _CONFIG_SCHEMA_CACHE


def reset_caches() -> None:
    """重置本模块的模块级缓存。

    由 server.py 在 import 段调用：importlib.reload(server) 不会重新导入子模块，
    故子模块缓存需显式重置，否则测试间会残留上一个项目/环境的解析结果。
    """
    global _DEFAULT_CONFIG_CACHE, _CONFIG_SCHEMA_CACHE
    _DEFAULT_CONFIG_CACHE = None
    _CONFIG_SCHEMA_CACHE = None
