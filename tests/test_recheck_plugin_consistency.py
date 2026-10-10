"""服务端重建路径的文本插件口径：与流水线一致（装载、钩子生效、失败降级、缓存失效）。

背景：保存并重检/全量重检/构建输出都在任务之外重建译文，必须应用项目声明的文本插件，
否则问题检测跑在未规整文本上（如常规修复插件会删行尾句号、压掉多余换行），产生虚报。
"""

import os
import tempfile
import unittest
from unittest import mock

import yaml

from GalTransl import plugin_loader
from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.server_cache import (
    _load_rebuild_deps,
    _resolve_cache_h_ranges,
    _run_problem_detection,
)


def _write_project(root: str, text_plugins: list) -> str:
    """写最小可加载项目：只声明插件与问题检测项，返回配置文件路径。"""
    os.makedirs(os.path.join(root, "transl_cache", "pass3_cache"), exist_ok=True)
    cfg = {
        "plugin": {"filePlugin": "file_galtransl_json", "textPlugins": list(text_plugins)},
        "common": {"language": "zh-cn", "splitFile": "no", "workersPerProject": "1"},
        "problemAnalyze": {
            "problemList": ["残留日文", "丢失换行", "多加换行", "换行位置异常", "单句过长"],
            "avgSentenceLengthThreshold": 17,
            "avgSentenceLengthThresholdH": 24,
        },
        "dictionary": {"defaultDictFolder": "Dict"},
    }
    config_path = os.path.join(root, "config.yaml")
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True)
    return config_path


def _detect(root: str, entries: list, text_plugins=None):
    """走完整重建链路跑一次检测（不落盘）；text_plugins 传 [] 可强制不应用插件。"""
    extra = {} if text_plugins is None else {"text_plugins": text_plugins}
    deps = _load_rebuild_deps(root, "config.yaml", **extra)
    proj_config, pre_dic, post_dic, gpt_dic, t_plugins, h_words, forbidden = deps
    h_ranges = [
        (r["lo"], r["hi"])
        for r in _resolve_cache_h_ranges(root, "pass3_cache/demo.txt.json").get("h_ranges", [])
    ]
    results, ok = _run_problem_detection(
        entries, proj_config, pre_dic, post_dic, gpt_dic, t_plugins, h_ranges, h_words, forbidden
    )
    assert ok, "问题检测不应失败"
    return {r["index"]: r for r in results}


class _RecordingPluginObject:
    """记录钩子调用的假插件对象。"""

    def __init__(self) -> None:
        self.calls: list = []

    def gtp_init(self, plugin_conf: dict, project_conf: dict) -> None:
        self.calls.append("gtp_init")

    def before_src_processed(self, tran):
        self.calls.append("before_src")
        return tran

    def after_src_processed(self, tran):
        self.calls.append("after_src")
        return tran

    def before_dst_processed(self, tran):
        self.calls.append("before_dst")
        return tran

    def after_dst_processed(self, tran):
        self.calls.append("after_dst")
        return tran

    def gtp_final(self) -> None:
        self.calls.append("gtp_final")


class _FakePluginInfo:
    def __init__(self, obj: _RecordingPluginObject) -> None:
        self.name = "fake-plugin"
        self.plugin_object = obj
        self.yaml_dict = {"Core": {"Module": "fake_plugin"}, "Settings": {}}


class _FakePluginManager:
    """最小 yapsy 管理器替身：只满足 plugin_loader 的调用面。"""

    instances: list = []

    def __init__(self, categories, directories) -> None:
        self.directories = directories
        self.recorder = _RecordingPluginObject()
        _FakePluginManager.instances.append(self)

    def locatePlugins(self) -> None:
        return None

    def getPluginCandidateByInfoPath(self, path):
        return (path, path + ".py", self.recorder)

    def setPluginCandidates(self, candidates) -> None:
        self.candidates = candidates

    def loadPlugins(self) -> None:
        return None

    def getPluginsOfCategory(self, category):
        return [_FakePluginInfo(self.recorder)]


class TextPluginLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakePluginManager.instances = []

    def test_loads_configured_builtin_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write_project(root, ["text_common_normalfix"])
            cfg = CProjectConfig(root, "config.yaml")
            plugins = plugin_loader.load_text_plugins(root, cfg)
            self.assertEqual(len(plugins), 1)
            self.assertEqual(type(plugins[0].plugin_object).__name__, "text_common_normalfix")

    def test_no_configured_plugins_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write_project(root, [])
            cfg = CProjectConfig(root, "config.yaml")
            self.assertEqual(plugin_loader.load_text_plugins(root, cfg), [])

    def test_missing_plugin_manifest_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write_project(root, ["no_such_plugin_xyz"])
            cfg = CProjectConfig(root, "config.yaml")
            self.assertEqual(plugin_loader.load_text_plugins(root, cfg), [])

    def test_locate_failure_degrades_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write_project(root, ["text_common_normalfix"])
            cfg = CProjectConfig(root, "config.yaml")
            with mock.patch.object(plugin_loader, "PluginManager") as manager:
                manager.return_value.locatePlugins.side_effect = RuntimeError("boom")
                self.assertEqual(plugin_loader.load_text_plugins(root, cfg), [])

    def test_load_failure_degrades_and_deps_still_usable(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write_project(root, ["text_common_normalfix"])
            with mock.patch.object(plugin_loader, "PluginManager") as manager:
                manager.return_value.locatePlugins.side_effect = RuntimeError("boom")
                deps = _load_rebuild_deps(root, "config.yaml")
            self.assertIsNotNone(deps[0])
            self.assertEqual(deps[4], [])

    def test_plugin_cache_reused_until_config_changes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            config_path = _write_project(root, ["text_common_normalfix"])
            cfg = CProjectConfig(root, "config.yaml")
            with mock.patch.object(plugin_loader, "PluginManager", _FakePluginManager):
                plugin_loader.load_text_plugins(root, cfg)
                plugin_loader.load_text_plugins(root, cfg)
                self.assertEqual(len(_FakePluginManager.instances), 1)
                first = plugin_loader._PLUGIN_CACHE[os.path.abspath(root)]
                old = os.path.getmtime(config_path)
                os.utime(config_path, (old + 10, old + 10))
                plugin_loader.load_text_plugins(root, cfg)
            self.assertEqual(len(_FakePluginManager.instances), 2)
            # 每项目只保留最新一份：旧键随失效被替换而非累积
            self.assertIsNot(plugin_loader._PLUGIN_CACHE[os.path.abspath(root)], first)

    def test_explicit_plugins_skip_loader(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write_project(root, ["text_common_normalfix"])
            sentinel = object()
            with mock.patch.object(plugin_loader, "load_text_plugins") as loader:
                deps = _load_rebuild_deps(root, "config.yaml", text_plugins=[sentinel])
            loader.assert_not_called()
            self.assertEqual(deps[4], [sentinel])


class RebuildAppliesPluginHooksTests(unittest.TestCase):
    """插件钩子在重建链路上真实生效（含流水线同款边界场景）。"""

    def setUp(self) -> None:
        _FakePluginManager.instances = []

    def test_hooks_invoked_and_gtp_final_not_called(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write_project(root, ["text_common_normalfix"])
            entries = [
                {"index": 1, "name": "", "pre_src": "こんにちは", "post_src": "こんにちは", "pre_dst": "你好。"}
            ]
            with mock.patch.object(plugin_loader, "PluginManager", _FakePluginManager):
                _detect(root, entries)
            recorder = _FakePluginManager.instances[0].recorder
            self.assertIn("gtp_init", recorder.calls)
            self.assertEqual(recorder.calls.count("before_src"), 1)
            self.assertEqual(recorder.calls.count("after_dst"), 1)
            self.assertNotIn("gtp_final", recorder.calls)

    def test_trailing_period_removed_by_plugin(self) -> None:
        # 原文不以句号结尾 → 常规修复插件删掉译文多余的行尾句号（不删则 preview 与流水线不一致）
        with tempfile.TemporaryDirectory() as root:
            _write_project(root, ["text_common_normalfix"])
            entries = [
                {"index": 1, "name": "", "pre_src": "こんにちは", "post_src": "こんにちは", "pre_dst": "你好。"}
            ]
            with_plugins = _detect(root, entries)
            without_plugins = _detect(root, entries, text_plugins=[])
            self.assertEqual(with_plugins[1]["post_dst_preview"], "你好")
            self.assertEqual(without_plugins[1]["post_dst_preview"], "你好。")

    def test_extra_newline_trimmed_by_plugin_avoids_false_problem(self) -> None:
        # 原文 1 个换行、译文 2 个 → 插件压掉多余的；不装插件会虚报「多加换行」
        with tempfile.TemporaryDirectory() as root:
            _write_project(root, ["text_common_normalfix"])
            entries = [
                {
                    "index": 1,
                    "name": "",
                    "pre_src": "ああ\r\nいい",
                    "post_src": "ああ\r\nいい",
                    "pre_dst": "啊啊，\r\n然后\r\n再见",
                }
            ]
            with_plugins = _detect(root, entries)
            without_plugins = _detect(root, entries, text_plugins=[])
            self.assertEqual(with_plugins[1]["post_dst_preview"], "啊啊，然后\r\n再见")
            self.assertNotIn("多加换行", with_plugins[1]["problem"])
            self.assertIn("多加换行", without_plugins[1]["problem"])


if __name__ == "__main__":
    unittest.main()
