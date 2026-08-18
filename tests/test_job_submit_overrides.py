"""任务提交透传 prompt_template_overrides 的回归测试。

覆盖 H4：JobRegistry.submit 此前未从 payload 取出 prompt_template_overrides，
导致前端保存的提示词模板覆盖永不生效；修复后应透传到 JobSpec。
"""

import threading
import unittest
from unittest import mock

from GalTransl.server import JobRegistry

_PAYLOAD_OVERRIDES = {
    "ForGal-json-multi-chat": {
        "system_prompt": "覆盖系统提示词",
        "user_prompt": "覆盖用户提示词",
    }
}


class JobSubmitOverridesTests(unittest.TestCase):
    def _submit_and_capture(self, payload: dict) -> object:
        """提交任务并捕获 _execute_job 传给 run_job 的 JobSpec（patch 掉真实执行）。"""
        captured = {}
        called = threading.Event()

        def fake_run_job(spec, state, stop_event=None):
            captured["spec"] = spec
            called.set()

        registry = JobRegistry(max_workers=1)
        try:
            with mock.patch("GalTransl.server.run_job", side_effect=fake_run_job):
                registry.submit(payload)
                self.assertTrue(called.wait(5), "run_job 未被调用")
        finally:
            registry._executor.shutdown(wait=True)
        return captured["spec"]

    def test_submit_passes_prompt_template_overrides_to_job_spec(self) -> None:
        spec = self._submit_and_capture({
            "project_dir": r"C:\tmp\proj",
            "translator": "ForGal-json-multi-chat",
            "prompt_template_overrides": _PAYLOAD_OVERRIDES,
        })
        self.assertEqual(spec.prompt_template_overrides, _PAYLOAD_OVERRIDES)

    def test_submit_ignores_invalid_overrides_type(self) -> None:
        spec = self._submit_and_capture({
            "project_dir": r"C:\tmp\proj",
            "translator": "ForGal-json-multi-chat",
            "prompt_template_overrides": "not-a-dict",
        })
        self.assertEqual(spec.prompt_template_overrides, {})

    def test_submit_without_overrides_defaults_to_empty(self) -> None:
        spec = self._submit_and_capture({
            "project_dir": r"C:\tmp\proj",
            "translator": "ForGal-json-multi-chat",
        })
        self.assertEqual(spec.prompt_template_overrides, {})


if __name__ == "__main__":
    unittest.main()
