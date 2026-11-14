"""以完整 Unicode 路径运行 pytest，规避 PowerShell 中文路径参数转码乱码。"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 默认全量运行 tests/；可透传任意 pytest 参数覆盖，如：
#   python run_pytest_utf8.py -v tests/test_xxx.py -k keyword
args = sys.argv[1:] or ["tests/", "-q"]


def _explicit_parallelism_requested() -> bool:
    """用户已显式给出并行度（-n / --numprocesses 任一写法）时不再追加。

    覆盖 ``-n 8``（空格分隔，argv 里是裸 "-n"）、``-n8``、``--numprocesses=8``。
    """
    for arg in args:
        if arg in ("-n", "--numprocesses") or arg.startswith(("-n", "--numprocesses=")):
            return True
    return False


def _default_parallel_args() -> list[str]:
    """默认并行参数：装了 pytest-xdist 就按文件亲和并行。

    --dist loadfile 保证每个测试文件整体落在同一 worker，文件内 setUpClass 级的
    环境/服务器状态不会跨进程串味；-n 0 可强制串行。
    """
    if importlib.util.find_spec("xdist") is None:
        return []
    return ["-n", "auto", "--dist", "loadfile"]


if not _explicit_parallelism_requested():
    args = [*args, *_default_parallel_args()]
os.chdir(ROOT)
result = subprocess.run([sys.executable, "-m", "pytest", *args])
sys.exit(result.returncode)
