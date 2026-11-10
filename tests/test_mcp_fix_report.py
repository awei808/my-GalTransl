# -*- coding: utf-8 -*-
"""galtransl_get_fix_report 工具单测（修复/改进报告读取）。

覆盖：
  - 端点优先：后端可达时整体信任 /fix-report 响应（source=live），
    不带 file 回文件级汇总（剥离逐句 entries），带 file 回逐句明细
  - 磁盘回退：后端不可达 / 响应形状异常时读 transl_cache/fix_report/latest.json（source=file）
  - 离线缺报告 / 损坏报告：exists=false + hint（软失败不抛错）
  - file 未命中：count=0 + available_files 清单
  - 概览缺口回归：get_project_overview 不再把 fix_report/ 当翻译缓存文件列出
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from GalTransl.mcp_tools import _tool_get_fix_report, _tool_get_project_overview

# 门禁设置随全局 app_settings.json 变化，模块级固定为默认值以隔离开发机真实设置
_gate_settings_patcher = None


def setUpModule() -> None:
    global _gate_settings_patcher
    _gate_settings_patcher = mock.patch(
        "GalTransl.mcp_tools.load_app_settings",
        return_value={"mcpHGateEnabled": True, "mcpDisabledTools": []},
    )
    _gate_settings_patcher.start()


def tearDownModule() -> None:
    _gate_settings_patcher.stop()


def _live_payload() -> dict:
    """模拟 GET /fix-report 的响应体（registry 快照或文件回退，二者同形）。"""
    return {
        "project_dir": "whatever",
        "exists": True,
        "label": "ForImproveTranslation",
        "started_at": "2026-11-07T06:00:00Z",
        "finished_at": "2026-11-07T06:01:00Z",
        "total": 2,
        "truncated": False,
        "files": [
            {
                "filename": "a.json",
                "count": 2,
                "truncated": False,
                "entries": [
                    {
                        "filename": "a.json",
                        "index": 1,
                        "speaker": "",
                        "post_src": "s1",
                        "old_dst": "旧一",
                        "new_dst": "新一",
                        "problems": "残留日文",
                        "backend": "ForFixRound",
                        "writes_main": False,
                    },
                    {
                        "filename": "a.json",
                        "index": 2,
                        "speaker": "",
                        "post_src": "s2",
                        "old_dst": "旧二",
                        "new_dst": "新二",
                        "problems": "",
                        "backend": "ForImproveTranslation",
                        "writes_main": True,
                    },
                ],
            }
        ],
    }


class GetFixReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project_dir = tempfile.mkdtemp(prefix="mcp_fix_report_")
        with open(os.path.join(self.project_dir, "config.yaml"), "w", encoding="utf-8") as f:
            f.write('common:\n  language: "日译中"\n')
        self.report_path = os.path.join(
            self.project_dir, "transl_cache", "fix_report", "latest.json"
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def _write_report(self, payload: dict) -> None:
        os.makedirs(os.path.dirname(self.report_path), exist_ok=True)
        with open(self.report_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    def _backend_down(self):
        return mock.patch(
            "GalTransl.mcp_backend_client.get_json",
            side_effect=RuntimeError("无法连接 GalTransl 后端"),
        )

    def test_live_endpoint_priority_overview(self) -> None:
        # 后端可达：整体信任端点响应；概览只回文件级汇总，不携带逐句 entries
        with mock.patch(
            "GalTransl.mcp_backend_client.get_json", return_value=_live_payload()
        ) as m_get:
            res = _tool_get_fix_report({"project_dir": self.project_dir})
        self.assertEqual(res["source"], "live")
        self.assertTrue(res["exists"])
        self.assertEqual(res["total"], 2)
        self.assertEqual(res["label"], "ForImproveTranslation")
        self.assertEqual(res["files"][0]["filename"], "a.json")
        self.assertEqual(res["files"][0]["count"], 2)
        self.assertNotIn("entries", res["files"][0])
        self.assertIn("note", res)
        # 端点路径按项目目录编码
        self.assertIn("/fix-report", m_get.call_args.args[0])

    def test_live_endpoint_file_detail(self) -> None:
        with mock.patch(
            "GalTransl.mcp_backend_client.get_json", return_value=_live_payload()
        ):
            res = _tool_get_fix_report({"project_dir": self.project_dir, "file": "a.json"})
        self.assertEqual(res["source"], "live")
        self.assertEqual(res["file"], "a.json")
        self.assertEqual(res["count"], 2)
        self.assertEqual(len(res["entries"]), 2)
        self.assertEqual(res["entries"][0]["old_dst"], "旧一")
        self.assertEqual(res["entries"][0]["new_dst"], "新一")
        self.assertEqual(res["entries"][1]["writes_main"], True)

    def test_backend_down_falls_back_to_file(self) -> None:
        payload = _live_payload()
        payload.pop("project_dir")  # 磁盘文件不携带 project_dir
        self._write_report(payload)
        with self._backend_down():
            res = _tool_get_fix_report({"project_dir": self.project_dir})
        self.assertEqual(res["source"], "file")
        self.assertTrue(res["exists"])
        self.assertEqual(res["total"], 2)
        self.assertNotIn("entries", res["files"][0])

    def test_offline_missing_report(self) -> None:
        with self._backend_down():
            res = _tool_get_fix_report({"project_dir": self.project_dir})
        self.assertFalse(res["exists"])
        self.assertEqual(res["source"], "file")
        self.assertTrue(res["path"].endswith(os.path.join("fix_report", "latest.json")))
        self.assertIn("hint", res)

    def test_file_filter_miss_lists_available(self) -> None:
        self._write_report(_live_payload())
        with self._backend_down():
            res = _tool_get_fix_report({"project_dir": self.project_dir, "file": "b.json"})
        self.assertTrue(res["exists"])
        self.assertEqual(res["count"], 0)
        self.assertEqual(res["entries"], [])
        self.assertEqual(res["available_files"], ["a.json"])
        self.assertIn("available_files", res["note"])

    def test_corrupt_report_offline(self) -> None:
        os.makedirs(os.path.dirname(self.report_path), exist_ok=True)
        with open(self.report_path, "w", encoding="utf-8") as f:
            f.write("不是JSON{{{")
        with self._backend_down():
            res = _tool_get_fix_report({"project_dir": self.project_dir})
        self.assertFalse(res["exists"])

    def test_live_exists_false_reports_missing(self) -> None:
        # 后端明确回答尚无报告（registry 无状态且文件缺失/损坏）：不得伪装成空报告
        with mock.patch(
            "GalTransl.mcp_backend_client.get_json",
            return_value={"exists": False, "total": 0, "files": []},
        ):
            res = _tool_get_fix_report({"project_dir": self.project_dir})
        self.assertEqual(res["source"], "live")
        self.assertFalse(res["exists"])
        self.assertIn("hint", res)

    def test_malformed_live_response_falls_back(self) -> None:
        # 端点返回缺 exists 字段的畸形响应：不信任，回退磁盘
        self._write_report(_live_payload())
        with mock.patch(
            "GalTransl.mcp_backend_client.get_json", return_value={"unexpected": True}
        ):
            res = _tool_get_fix_report({"project_dir": self.project_dir})
        self.assertEqual(res["source"], "file")
        self.assertTrue(res["exists"])

    def test_overview_excludes_fix_report_dir(self) -> None:
        # 概览缺口回归：fix_report/ 是程序产物不是翻译缓存，不进 agent 文件清单
        os.makedirs(os.path.join(self.project_dir, "transl_cache", "fix_report"), exist_ok=True)
        with open(self.report_path, "w", encoding="utf-8") as f:
            json.dump({"total": 0, "files": []}, f, ensure_ascii=False)
        os.makedirs(os.path.join(self.project_dir, "transl_cache", "pass3_cache"), exist_ok=True)
        cache_file = os.path.join(self.project_dir, "transl_cache", "pass3_cache", "demo.json")
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump([{"index": 0, "pre_dst": "译"}], f, ensure_ascii=False)
        res = _tool_get_project_overview({"project_dir": self.project_dir})
        self.assertIn("pass3_cache/demo.json", res["cache_file_names"])
        self.assertNotIn("fix_report/latest.json", res["cache_file_names"])


if __name__ == "__main__":
    unittest.main()
