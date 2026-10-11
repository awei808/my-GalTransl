"""pytest 全局夹具：测试环境隔离。"""
import pytest

from GalTransl import known_projects


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
