import unittest
from types import SimpleNamespace
from unittest.mock import patch

from GalTransl.CSentense import CSentense
from GalTransl.Backend.BaseTranslate import BaseTranslate


class IncrementalCacheAppendTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_translate_saves_only_incremental_results(self) -> None:
        captured_batch_sizes: list[int] = []

        async def fake_save_trans_cache_to_json(trans_list, cache_file_path, post_save=False, **kwargs):
            captured_batch_sizes.append(len(trans_list))

        class DummyTranslator(BaseTranslate):
            """最小 BaseTranslate 子类：__new__ 绕过重型 __init__，仅桩 common 所需依赖。"""

            skipH = False
            last_file_name = ""
            save_steps = 1
            dynamic_num_per_request = False
            pj_config = SimpleNamespace(
                non_interactive=True,
                print_translation_log_in_terminal=False,
                getProjectDir=lambda: "",
                bar=lambda n: None,
            )

            def _check_stop_requested(self):
                return None

            def _get_effective_num_per_request(self, configured_value, proofread=False, force_static=False):
                return configured_value

            def _update_dynamic_num_per_request(self, *args, **kwargs):
                return None

            def _record_runtime_success(self, *args, **kwargs):
                return None

            @staticmethod
            def _build_idx_tip(trans_list):
                return ""

            def reset_conversation(self):
                return None

            async def translate(self, trans_list_split, dic_prompt, proofread=False, **kwargs):
                return len(trans_list_split), trans_list_split

        trans_list = [CSentense(f"line-{i}", index=i) for i in range(1, 4)]

        with patch("GalTransl.Backend.BaseTranslate.save_transCache_to_json", new=fake_save_trans_cache_to_json):
            await DummyTranslator.__new__(DummyTranslator).batch_translate(
                filename="demo.json",
                cache_file_path="demo_cache.json",
                trans_list=trans_list,
                num_pre_request=2,
            )

        self.assertEqual(captured_batch_sizes, [2, 1])


if __name__ == "__main__":
    unittest.main()
