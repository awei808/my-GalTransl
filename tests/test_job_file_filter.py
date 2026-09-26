"""文件子集过滤与按任务配置覆盖的单测。

覆盖：
- Backend.utils.select_paths_by_filter 三级匹配口径
- Service.apply_job_config_overrides 值校验与应用
- server_jobs.validate_job_payload_extras 提交载荷校验
- JobState 运行范围快照字段
"""
import os
import unittest

from GalTransl.Backend.utils import select_paths_by_filter
from GalTransl.Service import JobSpec, apply_job_config_overrides, create_job_state
from GalTransl.server_jobs import validate_job_payload_extras


def _p(*parts: str) -> str:
    return os.path.join("proj", "gt_input", *parts)


FILES = [
    _p("00_prologue.json"),
    _p("route_a", "01_a.json"),
    _p("route_b", "02_b.json"),
]


class SelectPathsByFilterTests(unittest.TestCase):
    def test_empty_or_none_filter_returns_all_in_order(self) -> None:
        self.assertEqual(select_paths_by_filter(FILES, None), FILES)
        self.assertEqual(select_paths_by_filter(FILES, []), FILES)

    def test_exact_full_path_match(self) -> None:
        self.assertEqual(select_paths_by_filter(FILES, [FILES[1]]), [FILES[1]])

    def test_basename_match(self) -> None:
        self.assertEqual(select_paths_by_filter(FILES, ["01_a.json"]), [FILES[1]])

    def test_stem_match(self) -> None:
        self.assertEqual(select_paths_by_filter(FILES, ["02_b"]), [FILES[2]])

    def test_unmatched_items_are_ignored(self) -> None:
        result = select_paths_by_filter(FILES, ["01_a.json", "不存在.json"])
        self.assertEqual(result, [FILES[1]])

    def test_result_order_follows_original_list_not_filter(self) -> None:
        result = select_paths_by_filter(FILES, ["02_b", "00_prologue.json"])
        self.assertEqual(result, [FILES[0], FILES[2]])

    def test_duplicate_filter_entries_deduplicate(self) -> None:
        result = select_paths_by_filter(FILES, ["01_a", "01_a.json"])
        self.assertEqual(result, [FILES[1]])

    def test_blank_entries_are_skipped(self) -> None:
        result = select_paths_by_filter(FILES, ["", "  ", None])  # type: ignore[list-item]
        self.assertEqual(result, [])

    def test_works_on_dict_keys_view(self) -> None:
        data = {FILES[0]: "a", FILES[1]: "b"}
        self.assertEqual(select_paths_by_filter(data.keys(), ["01_a.json"]), [FILES[1]])


class ApplyJobConfigOverridesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = type("FakeCfg", (), {"keyValues": {}})()

    def test_applies_scalar_and_list_values(self) -> None:
        applied = apply_job_config_overrides(
            self.cfg,
            {"internals.promptBlocks.globalPrompt": False, "gpt.afterTranslation": ["brfix", "jpfix"]},
        )
        self.assertEqual(applied, ["internals.promptBlocks.globalPrompt", "gpt.afterTranslation"])
        self.assertEqual(self.cfg.keyValues["internals.promptBlocks.globalPrompt"], False)
        self.assertEqual(self.cfg.keyValues["gpt.afterTranslation"], ["brfix", "jpfix"])

    def test_forbidden_prompt_template_prefix_raises(self) -> None:
        with self.assertRaises(ValueError):
            apply_job_config_overrides(self.cfg, {"internals.prompt_template.system_prompt_override": "x"})

    def test_dict_value_raises(self) -> None:
        with self.assertRaises(ValueError):
            apply_job_config_overrides(self.cfg, {"internals.some.key": {"a": 1}})

    def test_list_with_illegal_element_raises(self) -> None:
        with self.assertRaises(ValueError):
            apply_job_config_overrides(self.cfg, {"gpt.afterTranslation": [["brfix"]]})

    def test_list_with_dict_element_is_allowed(self) -> None:
        applied = apply_job_config_overrides(
            self.cfg, {"gpt.afterTranslation": [{"fix": {"types": ["brfix"]}}]}
        )
        self.assertEqual(applied, ["gpt.afterTranslation"])

    def test_non_dict_overrides_returns_empty(self) -> None:
        self.assertEqual(apply_job_config_overrides(self.cfg, None), [])  # type: ignore[arg-type]

    def test_blank_key_is_skipped(self) -> None:
        self.assertEqual(apply_job_config_overrides(self.cfg, {"  ": 1}), [])


class ValidateJobPayloadExtrasTests(unittest.TestCase):
    def test_missing_fields_default_to_empty(self) -> None:
        file_filter, overrides = validate_job_payload_extras({})
        self.assertEqual(file_filter, [])
        self.assertEqual(overrides, {})

    def test_file_filter_strips_blanks(self) -> None:
        file_filter, _ = validate_job_payload_extras({"file_filter": [" a.json ", "", "b.json"]})
        self.assertEqual(file_filter, ["a.json", "b.json"])

    def test_file_filter_wrong_type_raises(self) -> None:
        with self.assertRaises(ValueError):
            validate_job_payload_extras({"file_filter": "a.json"})

    def test_file_filter_non_string_item_raises(self) -> None:
        with self.assertRaises(ValueError):
            validate_job_payload_extras({"file_filter": ["a.json", 3]})

    def test_config_overrides_wrong_type_raises(self) -> None:
        with self.assertRaises(ValueError):
            validate_job_payload_extras({"config_overrides": ["a=1"]})

    def test_valid_both(self) -> None:
        file_filter, overrides = validate_job_payload_extras(
            {"file_filter": ["a.json"], "config_overrides": {"internals.promptBlocks.glossary": True}}
        )
        self.assertEqual(file_filter, ["a.json"])
        self.assertEqual(overrides, {"internals.promptBlocks.glossary": True})


class JobStateSnapshotTests(unittest.TestCase):
    def test_create_job_state_snapshots_scope(self) -> None:
        spec = JobSpec(
            job_id="j1",
            project_dir="proj",
            translator="ForGal-json-translate",
            file_filter=["a.json", "b.json"],
            config_overrides={"internals.promptBlocks.glossary": True, "internals.promptBlocks.globalPrompt": True},
        )
        state = create_job_state(spec)
        self.assertEqual(state.file_filter, ["a.json", "b.json"])
        self.assertEqual(
            state.config_overrides,
            ["internals.promptBlocks.globalPrompt", "internals.promptBlocks.glossary"],
        )
        as_dict = state.to_dict()
        self.assertIn("file_filter", as_dict)
        self.assertIn("config_overrides", as_dict)

    def test_create_job_state_defaults_empty(self) -> None:
        spec = JobSpec(job_id="j2", project_dir="proj", translator="ForGal-json-translate")
        state = create_job_state(spec)
        self.assertEqual(state.file_filter, [])
        self.assertEqual(state.config_overrides, [])


if __name__ == "__main__":
    unittest.main()
