"""程序目录解析（resolve_app_dir）回归测试（0.5.1）。

打包版（PyInstaller onefile）模块 `__file__` 指向临时解包目录 sys._MEIPASS：
- app_settings.json / backend_profiles.yaml 曾据此定位 → 实测写到
  `%TEMP%\\_MEIxxxx\\`，重启即丢（且每次启动路径不同）；
- 而 Dict / translation_guidelines / plugins 走 cwd 相对路径，两套口径并存。
本测试锁定统一后的口径：frozen 时按 exe 位置解析，且各类配置同根。
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

from GalTransl import server_backend
from GalTransl.AppSettings import _SETTINGS_PATH
from GalTransl.Utils import resolve_app_dir


class ResolveAppDirTests(unittest.TestCase):
    def test_dev_mode_points_to_repo_root(self) -> None:
        # 开发模式：程序目录 = GalTransl 包的上一级（仓库根）
        app_dir = resolve_app_dir()
        self.assertTrue(os.path.isdir(os.path.join(app_dir, "GalTransl")))
        self.assertTrue(os.path.isfile(os.path.join(app_dir, "run_backend.py")))

    def test_frozen_backend_subdir_takes_parent(self) -> None:
        # 打包布局 <程序根>/backend/galtransl_backend.exe → 程序根
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "prog")
            exe = os.path.join(root, "backend", "galtransl_backend.exe")
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", exe):
                self.assertEqual(resolve_app_dir(), os.path.abspath(root))

    def test_frozen_dist_subdir_takes_parent(self) -> None:
        # Rust 侧候选路径还包含 dist/galtransl_backend.exe，同样取上一级
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "prog")
            exe = os.path.join(root, "dist", "galtransl_backend.exe")
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", exe):
                self.assertEqual(resolve_app_dir(), os.path.abspath(root))

    def test_frozen_exe_in_root_uses_exe_dir(self) -> None:
        # exe 与程序根同级时取 exe 所在目录
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "galtransl_backend.exe")
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", exe):
                self.assertEqual(resolve_app_dir(), os.path.abspath(tmp))

    def test_persisted_configs_share_app_dir(self) -> None:
        # 两类持久化配置必须同根，不能再出现「一个走 __file__、一个走 cwd」
        app_dir = resolve_app_dir()
        self.assertEqual(os.path.dirname(_SETTINGS_PATH), app_dir)
        self.assertEqual(os.path.dirname(server_backend._BACKEND_PROFILES_PATH), app_dir)


if __name__ == "__main__":
    unittest.main()
