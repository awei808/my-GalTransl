"""全局提示词 / 路线图上下文装配（共享给 4 个后端类）。

历史实现中 _ensure_global_prompt_loaded / _format_global_prompt_block /
_format_route_context_for_file 等函数在 4 个类中各重复一遍（ForGalJsonMulitChat、
ForFileMetaData、ForBatchMetaData、ForPlotRouteMap）。本模块把这些共享逻辑收口为
模块级函数，调用方传入引擎实例，函数读写实例的 pj_config / _global_prompt* /
_plot_route_map* 属性。这样：
  1) 行为以"当前各后端实现为准"保留（注入检查、惰性/非惰性路线图加载均按调用方选择）；
  2) 重复代码收口，未来调整全局提示词加载逻辑只需改一处。
"""

from __future__ import annotations

from typing import Any

from GalTransl import LOGGER
from GalTransl.Backend.utils import strip_chunk_suffix


def ensure_global_prompt_loaded(engine: Any, tag: str) -> None:
    """惰性载入 GlobalPrompt.json（仅执行一次）；优先使用 pj_config 已注入的全局提示词。

    Args:
        engine: 后端实例，读取 pj_config、写 _global_prompt_loaded / _global_prompt
        tag: 日志标签（如 "ForGalJsonMulitChat"），用于 LOGGER.debug 标识调用方
    """
    if engine._global_prompt_loaded:
        return
    engine._global_prompt_loaded = True
    # 优先从 projectConfig 读取已注入的全局提示词
    explicit = getattr(engine.pj_config, "global_prompt", None)
    if isinstance(explicit, dict):
        engine._global_prompt = explicit
        LOGGER.debug(f"[{tag}] 使用已注入的 GlobalPrompt（来自流水线）")
        return
    # 否则尝试从缓存文件加载
    try:
        from GalTransl.Backend.ForGlobalPrompt import load_global_prompt
        engine._global_prompt = load_global_prompt(engine.pj_config)
        if engine._global_prompt:
            LOGGER.debug(f"[{tag}] 已从 pass0_cache 载入 GlobalPrompt 上下文")
    except Exception as e:
        LOGGER.debug(f"[{tag}] 载入 GlobalPrompt 失败：{e}")
        engine._global_prompt = None


def ensure_plot_route_map_loaded(engine: Any, tag: str) -> None:
    """惰性载入 PlotRouteMap.json（仅执行一次）。"""
    if engine._plot_route_map_loaded:
        return
    engine._plot_route_map_loaded = True
    try:
        from GalTransl.Backend.ForPlotRouteMap import load_plot_route_map
        engine._plot_route_map = load_plot_route_map(engine.pj_config)
        if engine._plot_route_map:
            LOGGER.debug(f"[{tag}] 已从 pass0_cache 载入 PlotRouteMap 路线图")
    except Exception as e:
        LOGGER.debug(f"[{tag}] 载入 PlotRouteMap 失败：{e}")
        engine._plot_route_map = None


def _format_gp_with_route_common(
    engine: Any,
    tag: str,
    filename: str,
    route_block_provider: Any,
) -> str:
    """format_global_prompt_*_with_route 的公共骨架。

    Args:
        engine: 后端实例
        tag: 日志标签
        filename: 当前文件名
        route_block_provider: callable(engine, tag, filename) -> str，按调用方选择
            惰性或非惰性路线上下文实现
    """
    route_block = route_block_provider(engine, tag, filename)
    ensure_global_prompt_loaded(engine, tag)
    if not engine._global_prompt:
        return route_block
    from GalTransl.Backend.ForGlobalPrompt import _format_global_prompt_as_context
    gp_block = _format_global_prompt_as_context(
        engine._global_prompt, annotate_plot=bool(route_block)
    )
    if route_block and gp_block:
        LOGGER.debug(f"[{tag}] {filename} 注入路线剧情 + 带标注的全局提示词")
        return f"{route_block}\n{gp_block}"
    return route_block or gp_block


def format_global_prompt_only(engine: Any, tag: str) -> str:
    """仅格式化 GlobalPrompt 为提示词附加段落（无路线剧情）。

    对应原 ForFileMetaData / ForPlotRouteMap 的 _build_global_prompt_block 行为。
    """
    ensure_global_prompt_loaded(engine, tag)
    if not engine._global_prompt:
        return ""
    from GalTransl.Backend.ForGlobalPrompt import _format_global_prompt_as_context
    return _format_global_prompt_as_context(engine._global_prompt)


def format_global_prompt_with_route_lazy(engine: Any, tag: str, filename: str) -> str:
    """filename + 惰性路线剧情 + 带标注的 GlobalPrompt。

    对应原 ForGalJsonMulitChat._format_global_prompt_block 行为。
    """
    def _route_block_lazy(e, t, fn):
        return format_route_context_for_file_lazy(e, t, fn)
    return _format_gp_with_route_common(engine, tag, filename, _route_block_lazy)


def format_global_prompt_with_route_direct(engine: Any, tag: str, filename: str) -> str:
    """filename + 非惰性路线剧情（每次都重新加载）+ 带标注的 GlobalPrompt。

    对应原 ForBatchMetaData._build_global_prompt_block 行为。
    """
    def _route_block_direct(e, t, fn):
        return format_route_context_for_file_direct(e, t, fn)
    return _format_gp_with_route_common(engine, tag, filename, _route_block_direct)


def format_route_context_for_file_lazy(engine: Any, tag: str, filename: str) -> str:
    """惰性版：按当前文件所属路线返回剧情上下文块；先 ensure_plot_route_map_loaded 一次。

    对应原 ForGalJsonMulitChat._format_route_context_for_file 行为（惰性 IO）。
    """
    from GalTransl.Backend.ForPlotRouteMap import _format_route_context
    try:
        ensure_plot_route_map_loaded(engine, tag)
        plot_route_map = engine._plot_route_map
        if not plot_route_map:
            return ""
        base = strip_chunk_suffix(filename)
        ctx = _format_route_context(plot_route_map, base)
        if ctx:
            LOGGER.debug(f"[{tag}] {filename} 注入路线剧情（{base}）")
        return ctx
    except Exception as e:
        LOGGER.warning(f"[{tag}] 按路线注入剧情失败：{e}")
        return ""


def format_route_context_for_file_direct(engine: Any, tag: str, filename: str) -> str:
    """非惰性版：每次调用都重新 load_plot_route_map；不缓存到实例。

    对应原 ForBatchMetaData._format_route_context_for_file 行为（非惰性 IO）。
    """
    from GalTransl.Backend.ForPlotRouteMap import _format_route_context, load_plot_route_map
    try:
        plot_route_map = load_plot_route_map(engine.pj_config)
        if not plot_route_map:
            return ""
        base = strip_chunk_suffix(filename)
        ctx = _format_route_context(plot_route_map, base)
        if ctx:
            LOGGER.debug(f"[{tag}] {filename} 注入路线剧情（{base}）")
        return ctx
    except Exception as e:
        LOGGER.warning(f"[{tag}] 按路线注入剧情失败：{e}")
        return ""