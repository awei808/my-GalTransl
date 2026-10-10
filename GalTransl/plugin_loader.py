"""文本插件装载（服务端非任务路径复用：缓存重建/问题重检/构建输出）。

翻译任务路径的插件装载在 Runner 内完成；服务端在任务之外重建译文时（保存并重检、
全量重检、停止后自动重检、构建输出）同样必须应用项目声明的文本插件，否则问题检测
会跑在未经插件规整的文本上，与流水线口径不一致（例如换行修复/全角半角/常规修复类
插件的结果不会被计入）。本模块只做「定位 → 装载 → gtp_init」，不含文件插件，也不
调用 gtp_final（无任务生命周期，不应触发插件收尾副作用如发通知）。
"""
from __future__ import annotations

import os
import threading
from typing import Any, List, Tuple

from GalTransl import LOGGER
from GalTransl.GTPlugin import GFilePlugin, GTextPlugin
from GalTransl.Utils import resolve_app_dir
from GalTransl.yapsy.PluginManager import PluginManager


# 已装载插件缓存：项目目录绝对路径 -> (缓存键, 插件信息对象列表)。
# 每个项目只保留最新一份：改配置或改插件清单后旧键连同插件实例一并丢弃，避免长跑后端
# 按 mtime 累积（部分插件的 gtp_init 会加载分词器等重对象）。插件 .py 改动需重启后端。
_PLUGIN_CACHE: dict[str, Tuple[Tuple[Any, ...], list]] = {}
_PLUGIN_CACHE_LOCK = threading.RLock()


def get_plugin_info_path(project_dir: str, name: str) -> str:
    """插件清单（.yaml）路径：`(project_dir)` 前缀表示项目局部插件，否则为程序内置插件。"""
    if "(project_dir)" in name:
        real = name.replace("(project_dir)", "")
        return os.path.join(project_dir, "plugins", real, real + ".yaml")
    return os.path.join(resolve_app_dir(), "plugins", name, name + ".yaml")


def _mtime(path: str) -> float:
    """文件 mtime；不存在返回 -1（参与缓存键，不用 None 以免不可哈希）。"""
    try:
        return os.path.getmtime(path)
    except OSError:
        return -1.0


def _build_cache_key(
    names: List[str], config_path: str, manifest_paths: List[str]
) -> Tuple[Any, ...]:
    """插件缓存键：插件名单 + 配置文件与各插件清单 mtime（项目维度由调用方键控）。"""
    return (
        tuple(names),
        _mtime(config_path),
        tuple(_mtime(path) for path in manifest_paths),
    )


def load_text_plugins(project_dir: str, cfg: Any) -> list:
    """按项目配置装载并初始化文本插件，返回 yapsy 插件信息对象列表。

    任何失败（插件目录不可用、清单缺失、模块导入或 gtp_init 异常）都只影响对应插件，
    整体失败时返回空列表并告警——调用方据此退化到「不使用文本插件」的旧行为，不得中断
    问题检测。返回列表为缓存副本，插件对象跨调用共享（内置插件的钩子均无共享可变状态）。

    Args:
        project_dir: 项目根目录（解析项目局部插件与注入 plugin 配置用）。
        cfg: CProjectConfig（读取 plugin.textPlugins 与 plugin 段设置）。
    """
    try:
        names = [str(n) for n in (cfg.getTextPluginList() or [])]
    except Exception:
        LOGGER.warning("[plugin] 读取文本插件配置失败，本次不应用文本插件", exc_info=True)
        return []
    if not names:
        return []

    config_name = getattr(cfg, "config_name", "") or "config.yaml"
    config_path = os.path.join(project_dir, config_name)
    project_key = os.path.abspath(project_dir)
    key = _build_cache_key(
        names, config_path, [get_plugin_info_path(project_dir, n) for n in names]
    )
    with _PLUGIN_CACHE_LOCK:
        cached = _PLUGIN_CACHE.get(project_key)
        if cached is not None and cached[0] == key:
            LOGGER.debug(f"[plugin] 复用已装载文本插件：{names}")
            return list(cached[1])
        plugins = _load_uncached(project_dir, cfg, names)
        _PLUGIN_CACHE[project_key] = (key, plugins)
        return list(plugins)


def _load_uncached(project_dir: str, cfg: Any, names: List[str]) -> list:
    """实际装载流程（在缓存锁内调用）：扫描 → 命中清单 → 装载 → gtp_init。"""
    manager = PluginManager(
        {"GTextPlugin": GTextPlugin, "GFilePlugin": GFilePlugin},
        [os.path.join(resolve_app_dir(), "plugins"), os.path.join(project_dir, "plugins")],
    )
    try:
        manager.locatePlugins()
    except Exception:
        LOGGER.warning("[plugin] 扫描插件目录失败，本次不应用文本插件", exc_info=True)
        return []

    candidates = []
    for name in names:
        try:
            candidate = manager.getPluginCandidateByInfoPath(
                get_plugin_info_path(project_dir, name)
            )
        except Exception:
            candidate = None
            LOGGER.warning(f"[plugin] 解析文本插件清单失败：{name}", exc_info=True)
        if candidate is None:
            LOGGER.warning(f"[plugin] 未找到文本插件：{name}")
            continue
        candidates.append(candidate)
    if not candidates:
        return []

    try:
        manager.setPluginCandidates(candidates)
        manager.loadPlugins()
    except Exception:
        LOGGER.warning("[plugin] 装载文本插件失败，本次不应用文本插件", exc_info=True)
        return []

    try:
        plugin_sections = cfg.getPluginConfigSection()
    except Exception:
        plugin_sections = {}
    project_conf = cfg.getCommonConfigSection()
    project_conf["project_dir"] = project_dir

    loaded: list = []
    applied: List[str] = []
    for plugin in list(manager.getPluginsOfCategory("GTextPlugin")):
        name = getattr(plugin, "name", "?")
        try:
            plugin_conf = plugin.yaml_dict
            module = (plugin_conf.get("Core") or {}).get("Module", "")
            if module and isinstance(plugin_sections, dict) and module in plugin_sections:
                plugin_conf["Settings"].update(plugin_sections[module])
            plugin.plugin_object.gtp_init(plugin_conf, project_conf)
        except Exception:
            LOGGER.warning(f"[plugin] 文本插件初始化失败，已跳过：{name}", exc_info=True)
            continue
        loaded.append(plugin)
        applied.append(name)

    if applied:
        LOGGER.info(f"[plugin] 服务端重建路径已装载文本插件：{applied}")
    else:
        LOGGER.warning("[plugin] 文本插件全部初始化失败，本次重建未应用文本插件")
    return loaded
