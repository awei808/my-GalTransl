"""runtime 一次性提示（notices）与流水线阶段 3 术语表非空判定。"""
import asyncio
import importlib
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from GalTransl import server as _server_mod


def _start_server(workspace_root: str, token: str = ""):
    os.environ["GALTRANSL_WORKSPACE_ROOT"] = workspace_root
    if token:
        os.environ["GALTRANSL_API_TOKEN"] = token
    else:
        os.environ.pop("GALTRANSL_API_TOKEN", None)
    importlib.reload(_server_mod)
    registry = _server_mod.JobRegistry()
    srv = _server_mod.ThreadingHTTPServer(
        ("127.0.0.1", 0), _server_mod.build_handler(registry)
    )
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp()
        cls.server, cls.port = _start_server(cls.tmp)
        cls.root = cls.tmp

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _req(self, method: str, path: str, body=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode() or "{}")

    def _init_project(self, name: str):
        return self._req("POST", "/api/projects/init", body={"name": name})


class RuntimeNoticesTests(_Base):
    def test_notices_lifecycle(self) -> None:
        # 记录 → GET runtime 返回 → clear 后为空
        _, init = self._init_project("rt_notice")
        pid = init["project_id"]
        pdir = init["project_dir"]
        from GalTransl.server_runtime import record_runtime_notice

        record_runtime_notice(pdir, "hello-notice")
        status, body = self._req("GET", f"/api/projects/{pid}/runtime")
        self.assertEqual(status, 200)
        self.assertIn("hello-notice", body["notices"])
        status, body = self._req("POST", f"/api/projects/{pid}/runtime/notices/clear")
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        _, body2 = self._req("GET", f"/api/projects/{pid}/runtime")
        self.assertEqual(body2["notices"], [])


class NonEmptyGptDictTests(unittest.TestCase):
    class FakeCfg:
        def __init__(self, project_dir: str):
            self._dir = project_dir

        def getProjectDir(self) -> str:
            return self._dir

        def getDictCfgSection(self):
            return {"gpt.dict": [], "defaultDictFolder": ""}

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.cfg = self.FakeCfg(self.tmp)
        self.patcher = mock.patch(
            "GalTransl.Frontend.LLMTranslate.initDictList", return_value=[]
        )
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()

    def _write(self, rel: str, content: str) -> None:
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)

    def test_empty_or_missing_dict_returns_false(self) -> None:
        from GalTransl.Frontend.LLMTranslate import _has_nonempty_gpt_dict

        self.assertFalse(_has_nonempty_gpt_dict(self.cfg))

    def test_generated_file_with_entries_returns_true(self) -> None:
        from GalTransl.Frontend.LLMTranslate import _has_nonempty_gpt_dict

        self._write("项目GPT字典-生成.txt", "创君\tツクモ\n")
        self.assertTrue(_has_nonempty_gpt_dict(self.cfg))

    def test_generated_file_empty_returns_false(self) -> None:
        # 关键回归：生成文件存在但内容为空 → 视为空，需要重新生成
        from GalTransl.Frontend.LLMTranslate import _has_nonempty_gpt_dict

        self._write("项目GPT字典-生成.txt", "")
        self.assertFalse(_has_nonempty_gpt_dict(self.cfg))

    def test_only_comments_and_blank_returns_false(self) -> None:
        from GalTransl.Frontend.LLMTranslate import _has_nonempty_gpt_dict

        self._write("项目GPT字典-生成.txt", "# 注释行\n\n  \n")
        self.assertFalse(_has_nonempty_gpt_dict(self.cfg))

    def test_manual_dict_nonempty_returns_true(self) -> None:
        from GalTransl.Frontend.LLMTranslate import _has_nonempty_gpt_dict

        # 手写字典被 gpt.dict 配置引用（initDictList 返回其路径）且含有效条目 → True
        manual = os.path.join(self.tmp, "项目GPT字典.txt")
        self._write("项目GPT字典.txt", "魔法\tまほう\n")
        with mock.patch(
            "GalTransl.Frontend.LLMTranslate.initDictList",
            return_value=[manual],
        ):
            self.assertTrue(_has_nonempty_gpt_dict(self.cfg))


class FullPipelineNoticesTests(_Base):
    def test_full_pipeline_emits_stage_notices(self) -> None:
        # 真实跑阶段 0-3（跳过分支），mock 阶段 4/5/6 后端，断言各阶段 toast 提示进入 notices
        from GalTransl.ConfigHelper import CProjectConfig
        from GalTransl.Frontend.LLMTranslate import _run_full_pipeline
        from GalTransl.server_runtime import RUNTIME_REGISTRY

        _, init = self._init_project("pipe_nt")
        pid = init["project_id"]
        pdir = init["project_dir"]

        # 阶段 2 跳过：GlobalPrompt.json 已存在且校验通过（无游戏名称也应通过）
        gp_dir = os.path.join(pdir, "transl_cache", "pass0_cache")
        os.makedirs(gp_dir, exist_ok=True)
        with open(os.path.join(gp_dir, "GlobalPrompt.json"), "w", encoding="utf-8") as f:
            json.dump(
                {"剧情概述": "p", "角色列表": [{"名称": "A"}]},
                f, ensure_ascii=False,
            )
        # 阶段 3 跳过：术语表产物非空
        with open(os.path.join(pdir, "项目GPT字典-生成.txt"), "w", encoding="utf-8") as f:
            f.write("创君\tツクモ\n")

        file_json_lists = {"test.txt.json": [{"message": "こんにちは", "name": "A"}]}
        cfg = CProjectConfig(pdir, "config.yaml")

        fm_mock = mock.MagicMock()
        fm_mock.return_value.batch_translate = mock.AsyncMock()
        fm_mock.return_value.shutdown = mock.AsyncMock()
        bm_mock = mock.MagicMock()
        bm_mock.return_value.batch_translate = mock.AsyncMock()
        bm_mock.return_value.shutdown = mock.AsyncMock()

        with mock.patch("GalTransl.Backend.ForFileMetaData.ForFileMetaData", fm_mock), mock.patch(
            "GalTransl.Backend.ForBatchMetaData.ForBatchMetaData", bm_mock
        ), mock.patch(
            "GalTransl.Frontend.LLMTranslate._run_translation_phase", mock.AsyncMock()
        ):
            asyncio.run(_run_full_pipeline(cfg, file_json_lists, ["test.txt.json"]))

        notices = RUNTIME_REGISTRY.get_runtime_snapshot(pdir)["notices"]
        text = "\n".join(notices)
        self.assertIn("输入校验通过", text)
        self.assertIn("文本压缩完成", text)
        self.assertIn("全局分析已存在，跳过", text)
        self.assertIn("术语表已存在", text)
        self.assertIn("开始翻译", text)
        self.assertIn("全部 6 个阶段执行完毕", text)


if __name__ == "__main__":
    unittest.main()
