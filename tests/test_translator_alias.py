"""翻译后端改名兼容测试

ForGal-json-multi-chat（旧）-> ForGal-json-translate（新）：
  - resolve_translator_alias 别名解析
  - ENGINE_MODULE_PATHS 新旧引擎名都指向同一模块
  - 引擎模块导入后新名注册、旧名经别名可解析
"""

import unittest

from GalTransl import (
    TRANSLATOR_ALIASES,
    TRANSLATOR_SUPPORTED,
    resolve_translator_alias,
)
from GalTransl.Backend.BaseEngine import ENGINE_MODULE_PATHS

OLD_ENGINE = "ForGal-json-multi-chat"
NEW_ENGINE = "ForGal-json-translate"


class TranslatorAliasTests(unittest.TestCase):
    def test_old_engine_name_resolves_to_new(self) -> None:
        self.assertEqual(resolve_translator_alias(OLD_ENGINE), NEW_ENGINE)

    def test_new_and_unknown_names_pass_through(self) -> None:
        self.assertEqual(resolve_translator_alias(NEW_ENGINE), NEW_ENGINE)
        self.assertEqual(resolve_translator_alias("ForGal-full-pipeline"), "ForGal-full-pipeline")
        self.assertEqual(resolve_translator_alias("unknown-engine"), "unknown-engine")

    def test_both_engine_names_declared_in_module_paths(self) -> None:
        new_path = ENGINE_MODULE_PATHS.get(NEW_ENGINE)
        old_path = ENGINE_MODULE_PATHS.get(OLD_ENGINE)
        self.assertIsNotNone(new_path)
        self.assertEqual(new_path, old_path)
        self.assertEqual(new_path, "GalTransl.Backend.ForGalJsonTranslate")

    def test_new_engine_registered_and_module_importable(self) -> None:
        import importlib

        from GalTransl.Backend.BaseEngine import ENGINE_REGISTRY

        importlib.import_module(ENGINE_MODULE_PATHS[NEW_ENGINE])
        self.assertIn(NEW_ENGINE, ENGINE_REGISTRY)

    def test_translator_list_shows_new_name_only(self) -> None:
        self.assertIn(NEW_ENGINE, TRANSLATOR_SUPPORTED)
        self.assertNotIn(OLD_ENGINE, TRANSLATOR_SUPPORTED)

    def test_alias_table_contains_rename_mapping(self) -> None:
        self.assertEqual(TRANSLATOR_ALIASES.get(OLD_ENGINE), NEW_ENGINE)


if __name__ == "__main__":
    unittest.main()
