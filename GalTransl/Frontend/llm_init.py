"""翻译引擎初始化与插件加载（0.4.10 从 LLMTranslate.py 抽出）。

职责：
- 初始化共享 gptapi 实例（init_gptapi：按引擎类型从注册表取构造工厂）；
- 文件插件的加载与格式转换（fplugins_load_file）。
"""
from __future__ import annotations

import importlib
from typing import Any, Dict, List, Tuple

from GalTransl import LOGGER, resolve_translator_alias
from GalTransl.i18n import get_text, GT_LANG
from GalTransl.ConfigHelper import CProjectConfig




async def init_gptapi(
    projectConfig: CProjectConfig,
    token_pool: Any = None,
) -> "BaseEngine":
    """
    根据引擎类型获取相应的API实例（延迟导入后端模块以避免不必要依赖）。

    参数:
    projectConfig: 项目配置对象
    token_pool: 指定令牌池（大阶段独立 API）；缺省用任务主池
    eng_type: 引擎类型
    endpoint: API端点（如果适用）
    proxyPool: 代理池（如果适用）
    tokenPool: Token池

    返回:
    相应的API实例
    """
    proxyPool = projectConfig.proxyPool
    tokenPool = token_pool if token_pool is not None else projectConfig.tokenPool
    # 旧引擎名别名兜底：即使调用方未走 Runner 解析也能加载引擎
    eng_type = resolve_translator_alias(projectConfig.select_translator)

    import importlib

    from GalTransl.Backend.BaseEngine import ENGINE_MODULE_PATHS, ENGINE_REGISTRY

    module_path = ENGINE_MODULE_PATHS.get(eng_type)
    if module_path is None:
        raise ValueError(f"不支持的翻译引擎类型 {eng_type}")
    # 惰性加载目标模块：装饰器在 import 期把「name -> 构造工厂」填入 ENGINE_REGISTRY
    importlib.import_module(module_path)

    factory = ENGINE_REGISTRY.get(eng_type)
    if factory is None:
        raise ValueError(f"引擎 {eng_type} 未注册构造工厂")
    return factory(projectConfig, eng_type, proxyPool, tokenPool)


def fplugins_load_file(file_path: str, fPlugins: list) -> Tuple[List[Dict], Any]:
    """按顺序尝试每个文件插件解析 file_path。

    第一个成功的插件决定解析结果与对应的保存函数 save_func。
    返回 (json_list, save_func)；若所有插件都失败则断言报错。
    """
    result = None
    save_func = None
    for plugin in fPlugins:

        if isinstance(plugin, str):
            LOGGER.warning(f"跳过无效的插件项: {plugin}")
            continue
        try:
            result = plugin.plugin_object.load_file(file_path)
            save_func = plugin.plugin_object.save_file
            break
        except TypeError as e:
            LOGGER.error(
                f"{file_path} 不是文件插件'{getattr(plugin, 'name', 'Unknown')}'支持的格式：{e}"
            )
        except Exception as e:
            LOGGER.error(
                f"插件 {getattr(plugin, 'name', 'Unknown')} 读取文件 {file_path} 出错: {e}"
            )

    assert result is not None, get_text("file_load_failed", GT_LANG, file_path)

    assert isinstance(result, list), f"文件 {file_path} 不是列表"

    return result, save_func
