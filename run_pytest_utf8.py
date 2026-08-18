"""以完整 Unicode 路径运行 pytest，规避 PowerShell 中文路径参数转码乱码。"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 默认全量运行 tests/；可透传任意 pytest 参数覆盖，如：
#   python run_pytest_utf8.py -v tests/test_xxx.py -k keyword
args = sys.argv[1:] or ["tests/", "-q"]
os.chdir(ROOT)
result = subprocess.run([sys.executable, "-m", "pytest", *args])
sys.exit(result.returncode)
