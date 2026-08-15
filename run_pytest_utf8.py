"""以完整 Unicode 路径运行 pytest，规避 PowerShell 中文路径参数转码乱码。"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGETS = [
    "tests/test_forgal_json_multichat.py",
    "tests/test_forbatchmeta.py",
    "tests/test_batch_metadata_h_guide.py",
]
os.chdir(ROOT)
cmd = [sys.executable, "-m", "pytest", *TARGETS, "-q"]
result = subprocess.run(cmd)
sys.exit(result.returncode)
