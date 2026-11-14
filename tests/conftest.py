"""pytest 全局夹具：测试环境隔离。"""
import os

import pytest

from GalTransl import AppSettings, known_projects


@pytest.fixture(autouse=True)
def _fast_fail_backend_url(monkeypatch):
    """把 MCP 后端探测地址 setdefault 为秒拒端口（127.0.0.1:0）。

    mcp 写工具每次调用都经 _active_job_of → mcp_backend_client.get_json 探测默认
    地址 http://127.0.0.1:12333；开发机后端未运行时，本机防火墙/安全软件对「无人
    监听端口」的 connect 要等约 2s 才回拒绝，MCP 写工具测试每例都要白付一次
    （全量套件 ~25 例 × 2s）。端口 0 在连接层立即报错（实测 ~10ms），语义仍是
    「后端不可达」。setdefault 口径：环境里已有 GALTRANSL_BACKEND_URL（含用例在
    setUpClass/测试体内自设的桩地址）时不覆盖，显式指定后端地址的用例不受影响。
    """
    if "GALTRANSL_BACKEND_URL" not in os.environ:
        monkeypatch.setenv("GALTRANSL_BACKEND_URL", "http://127.0.0.1:0")


@pytest.fixture(autouse=True)
def _isolate_known_projects_env(monkeypatch, tmp_path_factory):
    """把已知项目登记文件（GALTRANSL_KNOWN_PROJECTS_PATH）重定向到临时路径。

    项目级 API / 项目创建 / 任务提交都会 best-effort 登记 known_projects.json
    （真实路径在程序目录）；测试经真实 HTTP server 命中这些入口时不得写入
    开发机的真实登记表，统一重定向到每次测试独立的临时文件。
    各测试可用 mock.patch.dict / monkeypatch 再覆盖为自己的路径。
    节流缓存 _LAST_RECORDED 一并换空，避免同目录跨测试被节流吞掉登记。
    """
    target = tmp_path_factory.mktemp("known_projects") / "known_projects.json"
    monkeypatch.setenv("GALTRANSL_KNOWN_PROJECTS_PATH", str(target))
    monkeypatch.setattr(known_projects, "_LAST_RECORDED", {})


@pytest.fixture(autouse=True)
def _isolate_app_settings_file(monkeypatch, tmp_path_factory):
    """把全局设置文件（AppSettings._SETTINGS_PATH）重定向到临时路径。

    PUT /api/app-settings 是**整份覆写**语义：payload 缺键即回落默认值。测试若经
    真实 HTTP server 打这个端点（哪怕只为探测路由，body 为空），就会把开发机程序
    目录里的 app_settings.json 清成默认值——实测会静默关掉 writeApiCallLog，并抹掉
    maxConcurrentJobs / mcpDisabledTools 等；同理默认值也会让用例依赖开发机真实设置。
    需要自备路径的用例可再用 mock.patch.object 覆盖（如 test_mcp_gate_settings）。
    注意用例若在模块导入期就 `from GalTransl.AppSettings import _SETTINGS_PATH`
    取常量（如 test_app_dir），拿到的是真实路径，不受本夹具影响。
    同源的 backend_profiles.yaml（含真实 API 密钥）因 test_app_dir 断言其真实路径，
    未在此统一隔离，改在 test_server_routes_smoke._start_server 内就近 pin。
    """
    target = tmp_path_factory.mktemp("app_settings") / "app_settings.json"
    monkeypatch.setattr(AppSettings, "_SETTINGS_PATH", str(target))
