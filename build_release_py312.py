#!/usr/bin/env python3.12
"""
GalTransl Windows 发布版构建脚本

一键构建整个桌面可发行版本，产出含前端 + 后端的便携版目录及 zip 压缩包。

用法:
  python build_release.py                # 构建全部
  python build_release.py --skip-fe      # 跳过前端构建（可复用已有 exe）
  python build_release.py --skip-be      # 跳过后端构建
  python build_release.py --clean        # 构建前清理旧产物
  python build_release.py --no-zip       # 不创建 zip 压缩包
  python build_release.py --no-smoke     # 跳过构建后后端冒烟测试
  python build_release.py --allow-incomplete  # 允许产物不完整仍继续（默认缺失即失败）
  python build_release.py --no-deps-cache     # 禁用 venv 依赖缓存（强制重建）

产出目录:
  release/
    GalTransl_{version}_win/
      GalTransl Desktop.exe          # Tauri 前端 exe
      backend/galtransl_backend.exe  # Python 后端 (PyInstaller)
      plugins/                       # 插件目录（仅运行所需文件）
      Dict/                          # 字典
      translation_guidelines/        # 翻译指南
      res/                           # 运行时资源
    GalTransl_{version}_win.zip       # 便携版压缩包

说明:
  本脚本产出的是便携版（zip），不产出 MSI/NSIS 安装包。
  如需安装包，请另行使用 Tauri bundle 产物。
  构建完成后会对后端 exe 做一次冒烟测试（从发布根目录为 cwd 启动，
  探测 HTTP 服务是否可响应），确保「能构建、能运行」。
"""

import argparse
import ast
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# 中文 Windows 下 stdout/stderr 默认 GBK，打印非 ASCII 字符（如 ✓、中文）会
# 触发 UnicodeEncodeError。强制以 UTF-8 输出，避免构建脚本自身崩溃。
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ─── 日志 ───────────────────────────────────────────────
# 统一走 logging：控制台（按级别着色，配合 UTF-8 输出）与文件（固定 UTF-8 纯文本）双写。
# 颜色仅由控制台 Formatter 注入，消息本体始终为纯文本，避免 ANSI 转义混入日志文件。
# 层级约定：
#   DEBUG   命令细节、中间产物路径、依赖扫描结果
#   INFO    构建阶段进度、产物位置
#   OK      成功信息（自定义 25，介于 INFO 与 WARNING 之间，控制台映射为绿色）
#   WARNING 可继续的降级（如显式跳过某组件、复用缓存）
#   ERROR   阻断性失败（会导致退出）
#   CRITICAL 环境不可用（缺 Python 版本 / 关键工具）
OK = 25
logging.addLevelName(OK, "OK")
LOG = logging.getLogger("build_release")

_LEVEL_COLORS = {
    logging.DEBUG: "\033[37m",
    logging.INFO: "\033[36m",
    OK: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[31;1m",
}


class _ColorFormatter(logging.Formatter):
    """控制台专用：按级别给整行加 ANSI 颜色。"""

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        color = _LEVEL_COLORS.get(record.levelno)
        if color:
            return f"{color}{msg}\033[0m"
        return msg


def _setup_logging(log_file: Path) -> None:
    LOG.setLevel(logging.DEBUG)
    # 文件：固定 UTF-8 纯文本，无 ANSI 颜色，避免中文 Windows 编码与乱码
    file_fmt = logging.Formatter("[%(levelname)s] %(message)s")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(file_fmt)
    LOG.addHandler(fh)
    # 控制台：stdout 已 reconfigure 为 UTF-8，可安全输出 ANSI 颜色
    console_fmt = _ColorFormatter("[%(levelname)s] %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(console_fmt)
    LOG.addHandler(sh)


def log_info(msg: str):
    LOG.info(msg)


def log_ok(msg: str):
    LOG.log(OK, msg)


def log_warn(msg: str):
    LOG.warning(msg)


def log_err(msg: str):
    LOG.error(msg)


def log_debug(msg: str):
    LOG.debug(msg)

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

# 后端 PyInstaller --add-data 需要收集的运行时数据文件扩展名白名单。
# 仅收集白名单类型（纯数据，非编译扩展——编译扩展应由 PyInstaller 二进制
# 收集，add-data 对 import 无效），避免把超大/无关文件打入 onefile 导致体积失控。
DATA_FILE_EXTS = {".json", ".yaml", ".yml", ".txt", ".xlsx", ".csv", ".xml"}

# 冒烟测试：后端启动就绪等待超时（秒）
BACKEND_BOOT_TIMEOUT = 30


def get_version() -> str:
    """从 GalTransl/__init__.py 读取版本号（正则容错单/双引号、空白）。

    解析失败或未找到时返回空串，由调用方决定是否阻断，避免静默回退 0.0.0。
    """
    init_py = ROOT / "GalTransl" / "__init__.py"
    text = init_py.read_text(encoding="utf-8")
    m = re.search(r'^\s*GALTRANSL_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return m.group(1) if m else ""


def get_frontend_version() -> str:
    """从 tauri.conf.json 读取前端版本，用于与后端版本一致性校验。"""
    conf = TAURI_DIR / "tauri.conf.json"
    try:
        m = re.search(r'"version"\s*:\s*"([^"]+)"', conf.read_text(encoding="utf-8"))
        return m.group(1) if m else ""
    except OSError:
        return ""


VERSION = get_version()
BUILD_NAME = f"GalTransl_{VERSION}_win"
BUILD_DIR = RELEASE_DIR / BUILD_NAME
ZIP_NAME = f"{BUILD_NAME}.zip"


def _resolve_windows_cmd(args: list[str]) -> list[str]:
    """Windows 下解析可执行命令（npx/npm 等 .cmd 批处理）。

    subprocess.run 的列表模式不经过 shell，CreateProcess 只执行 PE 可执行文件，
    无法直接运行 npx.cmd/npm.cmd。此处用 shutil.which 定位命令：
      - 命中 .cmd/.bat 时，包装为 [cmd.exe, /c, ...原参数]（保留列表传参避免引号问题）
      - 否则原样返回（如 python.exe/pip.exe/cargo.exe 带明确扩展名或为 PE）
    """
    if os.name != "nt" or not args:
        return args
    first = args[0]
    # 已含路径分隔符或已有扩展名 → 视为完整可执行文件，直接返回
    if os.path.sep in first or os.path.altsep in first or os.path.splitext(first)[1]:
        return args
    full = shutil.which(first)
    if full and full.lower().endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c"] + list(args)
    return args


def run(
    args: list[str] | str,
    cwd: Path | None = None,
    check: bool = True,
    env: dict | None = None,
) -> int:
    """执行命令并实时输出。

    优先以列表形式传参（避免 shell=True 的引号/路径拼接问题）。
    传入字符串时仍走 shell（用于必须依赖 shell 解释的少数场景）。

    Windows 下列表模式会自动把 npx/npm 等 .cmd 命令包装为 cmd /c 执行。

    env: 额外注入到子进程的环境变量（会与当前 os.environ 合并）。
    """
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    if isinstance(args, str):
        log_info(args)
        result = subprocess.run(args, shell=True, cwd=cwd or ROOT, env=run_env)
    else:
        cmd = _resolve_windows_cmd(args)
        cmd_str = " ".join(str(a) for a in cmd)
        log_info(cmd_str)
        result = subprocess.run(cmd, cwd=cwd or ROOT, env=run_env)
    if check and result.returncode != 0:
        log_err(f"命令失败 (exit code {result.returncode}): {result}")
        sys.exit(1)
    return result.returncode


def copy_dir_filtered(src: Path, dst: Path, extra_ignore: tuple[str, ...] = ()):
    """复制目录，过滤 __pycache__、.pyc 及额外忽略模式。

    extra_ignore: 追加的 shutil.ignore_patterns 模式（如插件裁剪用）。
    """
    patterns = ("__pycache__", "*.pyc") + tuple(extra_ignore)
    shutil.copytree(
        str(src), str(dst),
        ignore=shutil.ignore_patterns(*patterns),
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
    """查找 PyInstaller 打包好的后端 exe（当前为 --onefile 单文件产物）"""
    p = ROOT / "dist" / backend_exe_name()
    return p if p.exists() else None


# ─── 清理 ───────────────────────────────────────────────

# 清理目标及用途说明（--clean 显式触发，删除前打印清单便于确认）
_CLEAN_TARGETS = [
    (lambda: RELEASE_DIR, "发布产物目录 (release/)"),
    (lambda: DESKTOP_DIR / "dist", "前端 vite 产物 (desktop/dist)"),
    (lambda: ROOT / "dist", "PyInstaller 临时产物 (dist/)"),
    (lambda: ROOT / "build", "PyInstaller 工作目录 (build/)"),
    (lambda: VENV_DIR, "构建虚拟环境 (.venv-build)"),
]


def clean():
    log_warn("清理旧构建产物（仅删除下列构建相关目录）:")
    for getter, desc in _CLEAN_TARGETS:
        d = getter()
        if d.exists():
            log_warn(f"  - {d}  ({desc})")
    for getter, _ in _CLEAN_TARGETS:
        d = getter()
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            log_info(f"  删除 {d}")
    log_ok("清理完成")


# ─── 前端构建 (Tauri) ────────────────────────────────────

def build_frontend():
    log_info("═══ 构建前端 (Tauri Desktop) ═══")

    if not (DESKTOP_DIR / "node_modules").exists():
        log_info("安装前端依赖 (npm install)...")
        run(["npm", "install"], cwd=DESKTOP_DIR)

    # 检查是否有 Rust 工具链
    has_rust = _check_tool("cargo")

    if has_rust:
        # tauri.conf.json 已配置 beforeBuildCommand="npm run build"，tauri build
        # 会自动执行 vite 构建，此处无需再手动跑一次 npm run build，避免重复构建。
        log_info("编译 Tauri 桌面应用 (tauri build)...")
        run(["npx", "tauri", "build", "--no-bundle"], cwd=DESKTOP_DIR)

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

def _check_tool(name: str) -> bool:
    """检查命令是否可用（如 cargo）。失败返回 False，不抛异常。"""
    try:
        subprocess.run([name, "--version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _venv_uptodate(deps_hash: str) -> bool:
    """判断构建 venv 是否与当前依赖状态匹配（P2-3 增量缓存）。"""
    marker = VENV_DIR / ".deps_hash"
    if not (VENV_DIR / "Scripts" / "python.exe").exists():
        return False
    try:
        return marker.read_text(encoding="utf-8").strip() == deps_hash
    except OSError:
        return False


def _deps_hash() -> str:
    """对 requirements.txt 内容做稳定摘要，用于判断依赖是否变化。"""
    req = ROOT / "requirements.txt"
    try:
        data = req.read_text(encoding="utf-8")
    except OSError:
        data = ""
    return f"{len(data)}:{hashlib.sha256(data.encode('utf-8')).hexdigest()[:16]}"


def scan_data_files(pkg_dir: Path) -> list[Path]:
    """递归扫描包目录下 PyInstaller 需要收集的数据文件（P0-2）。

    仅收集 DATA_FILE_EXTS 白名单内的文件，排除 __pycache__。
    返回绝对路径列表，供 --add-data 使用。
    """
    found: list[Path] = []
    for f in pkg_dir.rglob("*"):
        if not f.is_file():
            continue
        if "__pycache__" in f.parts:
            continue
        if f.suffix.lower() in DATA_FILE_EXTS:
            found.append(f)
    return found


def _collect_data_args() -> list[str]:
    """生成 PyInstaller --add-data 参数列表。

    PyInstaller 的 --add-data 格式为 "源路径;目标目录"（Windows 用分号分隔）。
    用列表传参避免引号问题；同时打印收集清单以便排查（DEBUG 级）。

    只收集 GalTransl 包内运行时数据文件；res/ 为前端资源，后端通过 cwd 相对
    定位（发布根目录下已复制），无需打进 onefile，避免体积冗余。
    """
    args: list[str] = []
    src = ROOT / "GalTransl"
    if not src.exists():
        return args
    files = scan_data_files(src)
    for f in files:
        # 目标目录：相对 _MEIPASS 顶层，保持与包内相对路径结构一致
        rel = f.relative_to(ROOT)
        args.append(f"--add-data={f};{rel.parent}")
        log_debug(f"  收集数据文件: {rel}")
    if files:
        log_info(f"后端数据文件收集: {len(files)} 个 (GalTransl)")
    return args


def build_backend(no_deps_cache: bool = False):
    log_info("═══ 构建后端 (PyInstaller) ═══")

    # 虚拟环境路径
    venv_python = VENV_DIR / "Scripts" / "python.exe"
    venv_pip = VENV_DIR / "Scripts" / "pip.exe"

    # P2-3 增量缓存：依赖未变且 venv 存在则复用，避免每次全量重建（极慢）
    deps = _deps_hash()
    if not no_deps_cache and _venv_uptodate(deps):
        log_warn("构建 venv 与依赖一致，复用已有虚拟环境（如需强制重建用 --no-deps-cache）")
    else:
        # 强制清理旧 venv，避免复用半失败残留触发外部删除钩子（fail-closed）
        if VENV_DIR.exists():
            shutil.rmtree(VENV_DIR, ignore_errors=True)
        log_info("创建构建虚拟环境...")
        run([sys.executable, "-m", "venv", str(VENV_DIR), "--clear"])
        # 注入 PYTHONUTF8=1：requirements.txt 含 UTF-8 中文注释，中文 Windows
        # 下 pip 默认以 GBK 读取会触发 UnicodeDecodeError，强制 UTF-8 模式可根治。
        utf8_env = {"PYTHONUTF8": "1"}
        log_info("安装构建工具 (PyInstaller)...")
        run([str(venv_pip), "install", "pyinstaller"], env=utf8_env)
        log_info("安装全量运行时依赖 (requirements.txt)...")
        run([str(venv_pip), "install", "-r", str(ROOT / "requirements.txt")], env=utf8_env)
        VENV_DIR.mkdir(exist_ok=True)
        (VENV_DIR / ".deps_hash").write_text(deps, encoding="utf-8")

    # 扫描插件的隐式导入（yapsy 动态加载 plugins/*.py，需单独收集）
    auto_hidden = []
    if PLUGINS_DIR.exists():
        auto_hidden = scan_plugin_hidden_imports()
        if auto_hidden:
            log_info(f"插件依赖: {', '.join(auto_hidden)}")

    # 收集 GalTransl 顶层包：--paths 显式加入 ROOT 搜索路径 + --collect-submodules
    # 递归收集整包，一次性覆盖 Backend/Frontend/yapsy 等子模块。
    collect_args = [f"--paths={ROOT}", "--collect-submodules=GalTransl"]
    hidden_args = [f"--hidden-import={m}" for m in sorted(set(auto_hidden))]
    data_args = _collect_data_args()
    log_info(f"后端打包收集策略: {' '.join(collect_args)}")

    # 执行 PyInstaller（用列表传参，规避 shell 引号问题）
    cmd = (
        [str(venv_python), "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile"]
        + [f"--name={BACKEND_DIST_NAME}"]
        + collect_args
        + hidden_args
        + data_args
        + ["--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build")]
        + [str(BACKEND_ENTRY)]
    )
    run(cmd)

    exe = find_backend_exe()
    if not exe:
        log_err(f"后端 exe 未找到 (dist/{backend_exe_name()})")
        sys.exit(1)
    log_ok(f"后端 exe: {exe}")
    return exe


# ─── 组装发布目录 ────────────────────────────────────────

# 插件目录裁剪：仅复制运行所需文件，剔除源码级/编译中间产物，避免体积膨胀与源码泄露
PLUGIN_IGNORE = (".pyx", ".pxd", ".pxi", ".c", ".h", "*.pyx", "*.pxd", "*.pxi", "*test*", "__tests__")


def assemble_release(frontend_exe: Path | None, backend_exe: Path | None, allow_incomplete: bool = False):
    log_info("═══ 组装发布包 ═══")

    if BUILD_DIR.exists():
        # 兜底：旧 backend.exe 可能仍被占用（上一轮进程未退出），先尝试结束
        # 相关进程再删目录。taskkill 匹配不到时返回非零，故 check=False 忽略。
        subprocess.run(
            ["taskkill", "/F", "/IM", backend_exe_name()],
            shell=False, capture_output=True, check=False,
        )
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
        if BUILD_DIR.exists():
            # rmtree 可能因文件被占用而静默失败（ignore_errors=True），
            # 残留目录会阻塞后续复制。此处显式重试一次并如实警告。
            log_warn(f"发布目录删除未完全完成，尝试二次清理: {BUILD_DIR}")
            subprocess.run(
                ["taskkill", "/F", "/IM", backend_exe_name()],
                shell=False, capture_output=True, check=False,
            )
            shutil.rmtree(BUILD_DIR, ignore_errors=True)
    # exist_ok=True：即使上方删除未完全成功也允许继续（残留文件由后续 copy 覆盖）
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    missing: list[str] = []
    if frontend_exe and frontend_exe.exists():
        shutil.copy2(frontend_exe, BUILD_DIR / "GalTransl Desktop.exe")
        log_ok(f"前端 exe -> GalTransl Desktop.exe")
    else:
        log_warn("前端 exe 缺失，已跳过")
        missing.append("前端 exe (GalTransl Desktop.exe)")

    if backend_exe and backend_exe.exists():
        be_dir = BUILD_DIR / "backend"
        be_dir.mkdir(exist_ok=True)
        shutil.copy2(backend_exe, be_dir / backend_exe_name())
        log_ok(f"后端 -> backend/{backend_exe_name()}")
    else:
        log_warn("后端 exe 缺失，已跳过")
        missing.append(f"后端 exe (backend/{backend_exe_name()})")

    # 插件（P1-4：裁剪源码级文件）
    if PLUGINS_DIR.exists():
        copy_dir_filtered(PLUGINS_DIR, BUILD_DIR / "plugins", extra_ignore=PLUGIN_IGNORE)
        log_ok("插件 -> plugins/（已裁剪源码级文件）")
    else:
        missing.append("plugins/ 目录")

    # 字典
    if DICT_DIR.exists():
        copy_dir_filtered(DICT_DIR, BUILD_DIR / "Dict")
        log_ok("字典 -> Dict/")
    else:
        missing.append("Dict/ 目录")

    # 翻译指南
    if GUIDELINES_DIR.exists():
        copy_dir_filtered(GUIDELINES_DIR, BUILD_DIR / "translation_guidelines")
        log_ok("指南 -> translation_guidelines/")

    # 资源
    if RES_DIR.exists():
        copy_dir_filtered(RES_DIR, BUILD_DIR / "res")
        log_ok("资源 -> res/")

    # P0-4 产物完整性校验（fail-closed）：缺失关键组件默认阻断，显式降级才放行
    if missing and not allow_incomplete:
        log_err("以下关键组件缺失，发布包不完整，构建中止（可用 --allow-incomplete 显式降级）:")
        for m in missing:
            log_err(f"  - {m}")
        sys.exit(1)

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


# ─── 后端冒烟测试 ────────────────────────────────────────

def _pick_free_port() -> int:
    """返回一个当前空闲的 TCP 端口，降低冒烟测试端口冲突概率。"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def smoke_test_backend() -> bool:
    """从发布根目录为 cwd 启动后端 exe，探测 HTTP 服务是否可响应。

    后端通过 cwd 相对定位 plugins/ 等资源（已确认前端启动后端时 cwd=发布根目录）。
    启动成功且 GET / 返回 200 视为通过；否则返回 False。
    """
    log_info("═══ 后端冒烟测试 ═══")
    exe = BUILD_DIR / "backend" / backend_exe_name()
    if not exe.exists():
        log_warn("后端 exe 不在发布目录，跳过冒烟测试")
        return True

    port = _pick_free_port()
    proc = None
    try:
        proc = subprocess.Popen(
            [str(exe), "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(BUILD_DIR),  # 以发布根目录为 cwd，验证资源定位契约
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # 轮询等待服务就绪
        deadline = time.time() + BACKEND_BOOT_TIMEOUT
        while time.time() < deadline:
            if proc.poll() is not None:
                log_err(f"后端进程提前退出 (exit={proc.returncode})，冒烟失败")
                return False
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/", timeout=2
                ) as resp:
                    if resp.status == 200:
                        log_ok(f"后端冒烟通过 (HTTP 200, port {port})")
                        return True
            except Exception:
                time.sleep(0.5)
        log_err(f"后端冒烟超时（{BACKEND_BOOT_TIMEOUT}s 内未就绪）")
        return False
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


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


# ─── 预检 (preflight) ───────────────────────────────────

def preflight(args) -> None:
    """构建前集中校验环境与参数，一次性报出缺失项（P2-1 / P1-2 / P2-4）。"""
    problems: list[str] = []

    # Python 版本要求：项目统一使用 3.12+
    if sys.version_info < (3, 12):
        # 打印实际命中的解释器路径，便于排查 py 启动器/shebang 选错版本的环境歧义
        log_debug(f"当前解释器: {sys.executable} (Python {sys.version_info.major}.{sys.version_info.minor})")
        problems.append(
            f"需要 Python 3.12+（当前 {sys.version_info.major}.{sys.version_info.minor}），"
            f"实际解释器: {sys.executable}，"
            f"请用 `py -3.12 build_release_py312.py` 运行"
        )

    # 后端版本解析（P1-2）：解析失败即阻断，避免静默产出 GalTransl__win
    if not VERSION:
        problems.append("无法从 GalTransl/__init__.py 解析 GALTRANSL_VERSION，请检查格式")

    # 参数互斥（P2-4）：同时跳过前后端且无既有产物会产生空壳包
    if args.skip_fe and args.skip_be:
        problems.append("--skip-fe 与 --skip-be 不能同时使用（会产出空壳包）")

    # 关键工具/依赖预检（P2-1）
    if not args.skip_be and not (ROOT / "requirements.txt").exists():
        problems.append("缺少 requirements.txt")
    if not args.skip_fe:
        if not (DESKTOP_DIR / "package.json").exists():
            problems.append("缺少 desktop/package.json")
        if not _check_tool("cargo"):
            log_warn("未检测到 Rust/cargo 工具链，前端将无法编译（若已有 exe 可加 --skip-fe）")

    if problems:
        log_err("构建前置检查未通过:")
        for p in problems:
            log_err(f"  - {p}")
        sys.exit(1)

    # 前后端版本一致性（P1-2）：不一致时警告，便于发现双版本失配
    fe_ver = get_frontend_version()
    if fe_ver and fe_ver != VERSION:
        log_warn(f"前后端版本不一致: 后端 v{VERSION} vs 前端 v{fe_ver}（发布包名以后端为准）")


# ─── 主流程 ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GalTransl 发布版构建脚本")
    parser.add_argument("--skip-fe", action="store_true", help="跳过前端构建")
    parser.add_argument("--skip-be", action="store_true", help="跳过后端构建")
    parser.add_argument("--clean", action="store_true", help="构建前清理")
    parser.add_argument("--no-zip", action="store_true", help="不创建 zip")
    parser.add_argument("--no-smoke", action="store_true", help="跳过构建后后端冒烟测试")
    parser.add_argument("--allow-incomplete", action="store_true", help="允许产物不完整仍继续")
    parser.add_argument("--no-deps-cache", action="store_true", help="禁用 venv 依赖缓存（强制重建）")
    args = parser.parse_args()

    # 日志初始化：写入 release/logs/build_<时间戳>.log
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    log_dir = RELEASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    _setup_logging(log_dir / f"build_{ts}.log")

    log_info(f"GalTransl v{VERSION} 发布版构建")
    log_info(f"输出目录: {RELEASE_DIR}")
    log_info(f"构建日志: {log_dir / f'build_{ts}.log'}")

    # 集中预检（环境/版本/参数）
    preflight(args)

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
            log_warn("未找到前端 exe（产物将不完整，组装阶段会拦截）")

    # 后端
    if not args.skip_be:
        backend_exe = build_backend(no_deps_cache=args.no_deps_cache)
    else:
        backend_exe = find_backend_exe()
        if not backend_exe:
            log_err("跳过后端但未找到已有 exe")
            sys.exit(1)
        log_info(f"已有后端 exe: {backend_exe}")

    # 组装 + 完整性校验（P0-4）
    assemble_release(frontend_exe, backend_exe, allow_incomplete=args.allow_incomplete)

    # 后端冒烟测试（P0-1）：确保产物可运行
    if not args.no_smoke and backend_exe and not smoke_test_backend():
        log_err("后端冒烟测试失败：产物可能无法运行")
        sys.exit(1)

    # 压缩
    if not args.no_zip:
        create_zip()

    log_ok("构建完成!")
    log_info(f"   发布目录: {BUILD_DIR}")
    if not args.no_zip:
        log_info(f"   压缩包:   {RELEASE_DIR / ZIP_NAME}")

    # 清理 PyInstaller 临时产出（P1-3：仅本次构建产生的 dist/build）
    # 本次构建一定生成了 ROOT/dist 与 ROOT/build；skip-be 复用 exe 时不清理 dist。
    if not args.skip_be:
        for tmp_dir in [ROOT / "dist", ROOT / "build"]:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
                log_info(f"清理临时目录: {tmp_dir}")


if __name__ == "__main__":
    main()
