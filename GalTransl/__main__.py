import argparse
import os
import sys

from GalTransl.i18n import get_text,GT_LANG
from GalTransl.ConfigHelper import detect_config_file
from GalTransl.Service import JobSpec, run_job
from GalTransl import (
    PROGRAM_SPLASH,
    TRANSLATOR_SUPPORTED,
    GALTRANSL_VERSION,
    AUTHOR,
    CONTRIBUTORS,
    LOGGER,
    DEBUG_LEVEL,
)


def worker(
    project_dir: str,
    config_file_name: str,
    translator: str,
    show_banner: bool = True,
    non_interactive: bool = False,
) -> bool:
    if not project_dir or not isinstance(project_dir, str):
        LOGGER.error(get_text("error_project_path_empty", GT_LANG))
        return False
    if not config_file_name or not isinstance(config_file_name, str):
        LOGGER.error(get_text("error_config_file_empty", GT_LANG))
        return False
    if not translator or not isinstance(translator, str):
        LOGGER.error(get_text("error_translator_empty", GT_LANG))
        return False

    if show_banner:
        print(PROGRAM_SPLASH)
        print(f"GalTransl Core version: {GALTRANSL_VERSION}")
        print(f"Author: {AUTHOR}")
        print(f"Contributors: {CONTRIBUTORS}")

    state = run_job(
        JobSpec(
            project_dir=project_dir,
            config_file_name=config_file_name,
            translator=translator,
            non_interactive=non_interactive,
        )
    )
    return state.success


def _resolve_config_file(project_dir: str, config_arg: str | None) -> str | None:
    """解析实际使用的配置文件名：显式 --config 优先，否则自动探测。

    Returns:
        配置文件名；项目目录下找不到任何配置文件时返回 None 并提示错误。
    """
    if config_arg:
        # 与 run_GalTransl.py 的 .yaml 输入口径一致：统一归一为纯文件名
        config_file_name = os.path.basename(config_arg.replace("\\", "/"))
    else:
        config_file_name = detect_config_file(project_dir)
    config_path = os.path.join(project_dir, config_file_name)
    if not os.path.isfile(config_path):
        # job 日志 handler 尚未挂载，LOGGER 输出会被丢弃，此处必须直接打印
        print(get_text("config_file_not_exist", GT_LANG, config_path))
        return None
    print(f"使用配置文件: {config_file_name}")
    return config_file_name


def _build_translator_epilog() -> str:
    """用当前语言的引擎说明构建 argparse epilog。"""
    return "\n".join(
        f"{name}:\n    {desc[GT_LANG]}"
        for name, desc in TRANSLATOR_SUPPORTED.items()
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        "GalTransl",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="available translators:\n" + _build_translator_epilog(),
    )
    parser.add_argument("--project_dir", "-p", help="project folder", required=True)
    parser.add_argument(
        "--translator",
        "-t",
        choices=TRANSLATOR_SUPPORTED.keys(),
        help="choose which Translator to use",
        required=True,
    )
    parser.add_argument(
        "--config",
        "-c",
        help="config file name under project folder (default: auto-detect config.inc.yaml / config.yaml)",
        default=None,
    )
    parser.add_argument(
        "--debug-level",
        "-l",
        choices=DEBUG_LEVEL.keys(),
        help="debug level",
        default="info",
    )
    parser.add_argument(
        "--language",
        "-lang",
        choices=["zh-cn", "en"],
        help="UI language",
        default="zh-cn",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version=f"GalTransl Core {GALTRANSL_VERSION}",
    )
    args = parser.parse_args()
    # logging level
    LOGGER.setLevel(DEBUG_LEVEL[args.debug_level])

    print(PROGRAM_SPLASH)
    print(f"GalTransl Core version: {GALTRANSL_VERSION}")
    print(f"Author: {AUTHOR}")
    print(f"Contributors: {CONTRIBUTORS}")

    config_file_name = _resolve_config_file(args.project_dir, args.config)
    if config_file_name is None:
        return 1

    success = worker(
        args.project_dir,
        config_file_name,
        args.translator,
        show_banner=False,
        non_interactive=False,
    )
    return 0 if success else 1


if __name__ == "__main__":
    # 退出码必须显式上交，否则脚本化调用无法感知任务失败
    sys.exit(main())
