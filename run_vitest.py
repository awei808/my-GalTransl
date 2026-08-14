"""桌面端 vitest 测试运行器（绕过中文路径限制）。

背景：desktop 可能位于中文路径（如 D:\\项目\\...），直接经
cmd（8.3 短路径 ~1 导致 vite 模块解析失败）或 PowerShell（真实中文
路径参数被转码乱码）无法正常执行 vitest。Python subprocess 以 Unicode
传递 cwd 与参数，Windows 的 CreateProcess 原生支持，可完整绕过该限制。

用法（在项目根目录执行）：
    python run_vitest.py                   # 等价 npx vitest run
    python run_vitest.py --watch           # 监听模式
    python run_vitest.py -t "字符串index"  # 按测试名过滤
    python run_vitest.py --reporter dot    # 精简输出
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 用 Path.resolve() 取完整长路径：cmd 以 8.3 短路径（~1）启动脚本时，
# os.getcwd()/os.path.abspath() 会得到短路径，导致 vitest 解析
# test-setup.ts 失败；Path.resolve() 通过文件系统查询返回完整 Unicode 路径。
DESKTOP_ROOT = str(Path(__file__).resolve().parent / "desktop")


def _find_node() -> str:
    """定位 node.exe：优先 PATH，失败时报出可读错误。"""
    node = shutil.which("node")
    if not node:
        raise SystemExit(
            "未找到 node。请确认桌面端依赖已安装（desktop/node_modules），"
            "或 node 已加入 PATH。"
        )
    return node


def main() -> int:
    if not os.path.isdir(DESKTOP_ROOT):
        raise SystemExit(f"桌面端目录不存在: {DESKTOP_ROOT}")
    vitest_cli = os.path.join(
        DESKTOP_ROOT, "node_modules", "vitest", "vitest.mjs"
    )
    if not os.path.isfile(vitest_cli):
        raise SystemExit(
            f"vitest CLI 不存在: {vitest_cli}\n"
            "请先在 desktop 目录执行 npm install。"
        )

    # 用户透传参数默认至少保证「单次运行」语义（npx vitest 默认是 watch）
    args = list(sys.argv[1:]) or ["run"]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["LANG"] = "zh_CN.UTF-8"

    proc = subprocess.run(
        [_find_node(), vitest_cli, *args],
        cwd=DESKTOP_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # vitest 输出含 ✓ 等非 GBK 字符，stdout 需重配为 UTF-8 防 UnicodeEncodeError
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
