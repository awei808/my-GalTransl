#!/usr/bin/env python3
"""
GalTransl Windows 发布版构建脚本

一键构建整个桌面可发行版本，产出含前端 + 后端的便携版目录及 MSI 安装包。

用法:
  python build_release.py                # 构建全部
  python build_release.py --skip-fe      # 跳过前端构建（可复用已有 exe）
  python build_release.py --skip-be      # 跳过后端构建
  python build_release.py --clean        # 构建前清理旧产物
  python build_release.py --no-zip       # 不创建 zip 压缩包

产出目录:
  release/
    GalTransl_{version}_win/
      GalTransl Desktop.exe          # Tauri 前端 exe
      backend/galtransl_backend.exe  # Python 后端 (PyInstaller)
      plugins/                       # 插件目录
      res/                           # 运行时资源
    GalTransl_{version}_win.zip       # 便携版压缩包
"""

import argparse
import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ─── 配置 ───────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
DESKTOP_DIR = ROOT / "desktop"
TAURI_DIR = DESKTOP_DIR / "src-tauri"
TAURI_RELEASE = TAURI_DIR / "target" / "release"
RELEASE_DIR = ROOT / "release"
PLUGINS_DIR = ROOT / "plugins"
DICT_DIR = ROOT / "Dict"
GUIDELINES_DIR = ROOT / "translation_guidelines"
RES_DIR = ROOT / "res"

BACKEND_ENTRY = ROOT / "run_backend.py"
BACKEND_DIST_NAME = "galtransl_backend"
VENV_DIR = ROOT / ".venv-build"


def get_version() -> str:
    """从 GalTransl/__init__.py 读取版本号"""
    init_py = ROOT / "GalTransl" / "__init__.py"
    for line in init_py.read_text(encoding="utf-8").splitlines():
        if line.startswith("GALTRANSL_VERSION"):
            return line.split('"')[1]
    return "0.0.0"


VERSION = get_version()
BUILD_NAME = f"GalTransl_{VERSION}_win"
BUILD_DIR = RELEASE_DIR / BUILD_NAME
ZIP_NAME = f"{BUILD_NAME}.zip"


def log_info(msg: str):
    print(f"\033[36m>>> {msg}\033[0m")


def log_ok(msg: str):
    print(f"\033[32m  ✓ {msg}\033[0m")


def log_warn(msg: str):
    print(f"\033[33m  ⚠ {msg}\033[0m")


def log_err(msg: str):
    print(f"\033[31m  ✗ {msg}\033[0m")


def run(cmd: str, cwd: Path | None = None, check: bool = True) -> int:
    """执行命令并实时输出"""
    log_info(cmd)
    result = subprocess.run(cmd, shell=True, cwd=cwd or ROOT)
    if check and result.returncode != 0:
        log_err(f"命令失败 (exit code {result.returncode})")
        sys.exit(1)
    return result.returncode


def copy_dir_filtered(src: Path, dst: Path):
    """复制目录，过滤 __pycache__ 和 .pyc"""
    shutil.copytree(
        str(src), str(dst),
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        dirs_exist_ok=True,
    )


# ─── 查找构建产物 ─────────────────────────────────────────

def find_frontend_exe() -> Path | None:
    """查找编译好的前端 exe"""
    candidates = [
        TAURI_RELEASE / "GalTransl Desktop.exe",
        TAURI_RELEASE / "galtransl-desktop.exe",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def backend_exe_name() -> str:
    return f"{BACKEND_DIST_NAME}.exe"


def find_backend_exe() -> Path | None:
    """查找 PyInstaller 打包好的后端 exe"""
    candidates = [
        ROOT / "dist" / backend_exe_name(),
        ROOT / "dist" / BACKEND_DIST_NAME / backend_exe_name(),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


# ─── 清理 ───────────────────────────────────────────────

def clean():
    log_warn("清理旧构建产物...")
    dirs = [
        RELEASE_DIR,
        DESKTOP_DIR / "dist",
        ROOT / "dist",
        ROOT / "build",
        VENV_DIR,
    ]
    for d in dirs:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            log_info(f"  删除 {d}")
    log_ok("清理完成")


# ─── 前端构建 (Tauri) ────────────────────────────────────

def build_frontend():
    log_info("═══ 构建前端 (Tauri Desktop) ═══")

    if not (DESKTOP_DIR / "node_modules").exists():
        log_info("安装前端依赖 (npm install)...")
        run("npm install", cwd=DESKTOP_DIR)

    # 先构建静态资源
    log_info("构建前端静态资源 (vite build)...")
    run("npm run build", cwd=DESKTOP_DIR)

    # 检查是否有 Rust 工具链
    try:
        subprocess.run("cargo --version", shell=True, capture_output=True, check=True)
        has_rust = True
    except subprocess.CalledProcessError:
        has_rust = False

    if has_rust:
        log_info("编译 Tauri 桌面应用 (cargo build + tauri build)...")
        run("npx tauri build --no-bundle", cwd=DESKTOP_DIR)

        exe = find_frontend_exe()
        if not exe:
            log_err("前端 exe 未找到，检查以下路径:")
            for p in [TAURI_RELEASE / "GalTransl Desktop.exe", TAURI_RELEASE / "galtransl-desktop.exe"]:
                log_err(f"  {p} (exists={p.exists()})")
            sys.exit(1)
        log_ok(f"前端 exe: {exe}")
        return exe
    else:
        log_warn("未检测到 Rust 工具链，跳过 Tauri 编译。")
        log_warn("请安装 Rust: https://rustup.rs")
        return None


# ─── 后端构建 (PyInstaller) ──────────────────────────────

def build_backend():
    log_info("═══ 构建后端 (PyInstaller) ═══")

    # 虚拟环境路径
    venv_python = VENV_DIR / "Scripts" / "python.exe"
    venv_pip = VENV_DIR / "Scripts" / "pip.exe"

    # 强制清理旧 venv，避免复用半失败残留触发外部删除钩子（fail-closed）
    if VENV_DIR.exists():
        shutil.rmtree(VENV_DIR, ignore_errors=True)
    log_info("创建构建虚拟环境...")
    run(f'"{sys.executable}" -m venv "{VENV_DIR}" --clear')

    # 安装 PyInstaller（构建工具）与全量运行时依赖
    log_info("安装构建工具 (PyInstaller)...")
    run(f'"{venv_pip}" install pyinstaller')
    log_info("安装全量运行时依赖 (requirements.txt)...")
    run(f'"{venv_pip}" install -r "{ROOT / "requirements.txt"}"')

    # 扫描插件的隐式导入
    auto_hidden = []
    if PLUGINS_DIR.exists():
        auto_hidden = scan_plugin_hidden_imports()
        if auto_hidden:
            log_info(f"插件依赖: {', '.join(auto_hidden)}")

    # 固定隐藏导入
    hidden = [
        "GalTransl", "GalTransl.server", "GalTransl.Service",
        "GalTransl.Runner", "GalTransl.Cache", "GalTransl.CSentense",
        "GalTransl.CSerialize", "GalTransl.CSplitter",
        "GalTransl.Dictionary", "GalTransl.ConfigHelper",
        "GalTransl.AppSettings", "GalTransl.COpenAI",
        "GalTransl.Name", "GalTransl.i18n", "GalTransl.Problem",
        "GalTransl.Utils", "GalTransl.TerminalOutput",
        "GalTransl.yapsy", "GalTransl.Frontend",
        "GalTransl.Frontend.LLMTranslate",
        # 常见运行时依赖
        "requests", "yaml", "pyyaml",
    ]
    hidden.extend(auto_hidden)
    hidden = sorted(set(hidden))
    hidden_args = " ".join(f'--hidden-import="{m}"' for m in hidden)

    # 执行 PyInstaller
    cmd = (
        f'"{venv_python}" -m PyInstaller '
        f"--noconfirm --clean "
        f"--onefile "
        f"--name {BACKEND_DIST_NAME} "
        f"{hidden_args} "
        f"--distpath dist "
        f"--workpath build "
        f'"{BACKEND_ENTRY}"'
    )
    run(cmd)

    exe = find_backend_exe()
    if not exe:
        log_err(f"后端 exe 未找到 (dist/{backend_exe_name()})")
        sys.exit(1)
    log_ok(f"后端 exe: {exe}")
    return exe


# ─── 组装发布目录 ────────────────────────────────────────

def assemble_release(frontend_exe: Path | None, backend_exe: Path | None):
    log_info("═══ 组装发布包 ═══")

    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True)

    if frontend_exe and frontend_exe.exists():
        shutil.copy2(frontend_exe, BUILD_DIR / "GalTransl Desktop.exe")
        log_ok(f"前端 exe -> GalTransl Desktop.exe")
    else:
        log_warn("前端 exe 缺失，已跳过")

    if backend_exe and backend_exe.exists():
        be_dir = BUILD_DIR / "backend"
        be_dir.mkdir(exist_ok=True)
        shutil.copy2(backend_exe, be_dir / backend_exe_name())
        log_ok(f"后端 -> backend/{backend_exe_name()}")
    else:
        log_warn("后端 exe 缺失，已跳过")

    # 插件
    if PLUGINS_DIR.exists():
        copy_dir_filtered(PLUGINS_DIR, BUILD_DIR / "plugins")
        log_ok("插件 -> plugins/")

    # 字典
    if DICT_DIR.exists():
        copy_dir_filtered(DICT_DIR, BUILD_DIR / "Dict")
        log_ok("字典 -> Dict/")

    # 翻译指南
    if GUIDELINES_DIR.exists():
        copy_dir_filtered(GUIDELINES_DIR, BUILD_DIR / "translation_guidelines")
        log_ok("指南 -> translation_guidelines/")

    # 资源
    if RES_DIR.exists():
        copy_dir_filtered(RES_DIR, BUILD_DIR / "res")
        log_ok("资源 -> res/")

    log_ok(f"发布包: {BUILD_DIR}")


# ─── 压缩 ───────────────────────────────────────────────

def create_zip():
    log_info("═══ 创建压缩包 ═══")
    zip_path = RELEASE_DIR / ZIP_NAME
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(
        str(zip_path.with_suffix("")),
        "zip",
        root_dir=str(RELEASE_DIR),
        base_dir=BUILD_NAME,
    )
    log_ok(f"压缩包: {zip_path}")


# ─── 扫描插件隐式导入 ────────────────────────────────────

def scan_plugin_hidden_imports() -> list[str]:
    """扫描 plugins/*.py 中的第三方 import"""
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    stdlib.update({"__future__", "typing_extensions"})
    skip_roots = {"GalTransl", "plugins"}
    discovered: set[str] = set()

    for py_file in PLUGINS_DIR.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue
                if not node.module:
                    continue
                names = [node.module]
            else:
                continue
            for name in names:
                root = name.split(".", 1)[0]
                if not root or root in skip_roots or root in stdlib:
                    continue
                discovered.add(root)
    return sorted(discovered)


# ─── 主流程 ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GalTransl 发布版构建脚本")
    parser.add_argument("--skip-fe", action="store_true", help="跳过前端构建")
    parser.add_argument("--skip-be", action="store_true", help="跳过后端构建")
    parser.add_argument("--clean", action="store_true", help="构建前清理")
    parser.add_argument("--no-zip", action="store_true", help="不创建 zip")
    args = parser.parse_args()

    print(f"\033[1mGalTransl v{VERSION} 发布版构建\033[0m")
    print(f"输出目录: {RELEASE_DIR}\n")

    if args.clean:
        clean()

    # 前端
    if not args.skip_fe:
        frontend_exe = build_frontend()
    else:
        frontend_exe = find_frontend_exe()
        if frontend_exe:
            log_info(f"已有前端 exe: {frontend_exe}")
        else:
            log_warn("未找到前端 exe")

    # 后端
    if not args.skip_be:
        backend_exe = build_backend()
    else:
        backend_exe = find_backend_exe()
        if not backend_exe:
            log_err("跳过后端但未找到已有 exe")
            sys.exit(1)
        log_info(f"已有后端 exe: {backend_exe}")

    # 组装
    assemble_release(frontend_exe, backend_exe)

    # 压缩
    if not args.no_zip:
        create_zip()

    print(f"\n\033[32m✅ 构建完成!\033[0m")
    print(f"   发布目录: {BUILD_DIR}")
    if not args.no_zip:
        print(f"   压缩包:   {RELEASE_DIR / ZIP_NAME}")

    # 清理 PyInstaller 临时产出
    for tmp_dir in [ROOT / "dist", ROOT / "build"]:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
            log_info(f"清理临时目录: {tmp_dir}")


if __name__ == "__main__":
    main()
