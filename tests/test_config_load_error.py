"""配置加载与配置段缺失容错回归测试（M14 / M15 / M10）。

M14：loadConfigFile 解析失败/根节点非映射时抛 ValueError（原实现返回 False 导致
      CProjectConfig 拿到 bool 后出现误导性 AttributeError）。
M15：旧项目无 problemAnalyze 段时 getProblemAnalyzeConfig 等不再 KeyError。
M10：无 dictionary 段时 getDictCfgSection 返回空 dict 而非 KeyError。
"""

import os
import tempfile
import unittest

from GalTransl.ConfigHelper import CProjectConfig, loadConfigFile


class LoadConfigFileErrorTests(unittest.TestCase):
    def _write(self, tmp: str, content: str) -> str:
        cfg_path = os.path.join(tmp, "config.yaml")
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(content)
        return cfg_path

    def test_invalid_yaml_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = self._write(tmp, "common:\n  broken: [unclosed\n")
            with self.assertRaises(ValueError):
                loadConfigFile(cfg_path)

    def test_non_mapping_root_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = self._write(tmp, "- just\n- a\n- list\n")
            with self.assertRaises(ValueError):
                loadConfigFile(cfg_path)

    def test_empty_file_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = self._write(tmp, "")
            with self.assertRaises(ValueError):
                loadConfigFile(cfg_path)


class ProblemAnalyzeMissingSectionTests(unittest.TestCase):
    def _config(self, tmp: str) -> CProjectConfig:
        cfg_path = os.path.join(tmp, "config.yaml")
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write("common:\n  language: zh-cn\n")
        return CProjectConfig(tmp)

    def test_get_problem_analyze_config_without_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            self.assertEqual(cfg.getProblemAnalyzeConfig("problemList"), [])
            self.assertEqual(cfg.getProblemAnalyzeArinashiDict(), {})
            self.assertGreaterEqual(cfg.getAvgSentenceLengthThreshold(), 1)

    def test_h_scene_and_length_getters_without_section(self) -> None:
        # M15 残留：H 场景/定语/状语阈值 getter 缺段时返回默认值（与同族 getter 口径统一）
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            self.assertEqual(cfg.getHSentenceLengthThreshold(), 24)
            self.assertEqual(cfg.getAttributiveMaxLength(), 10)
            self.assertEqual(cfg.getAdverbialMaxLength(), 12)


class DictCfgSectionMissingTests(unittest.TestCase):
    def test_get_dict_cfg_section_without_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.yaml")
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write("common:\n  language: zh-cn\n")
            cfg = CProjectConfig(tmp)
            self.assertEqual(cfg.getDictCfgSection(), {})
            self.assertIsNone(cfg.getDictCfgSection("sortDict"))


if __name__ == "__main__":
    unittest.main()
