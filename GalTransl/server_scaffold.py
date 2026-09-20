"""新建项目脚手架与样本文件（0.4.10 从 server.py 抽出）。

职责：
- 工作区根与新建项目目录解析（_workspace_root / _resolve_new_project_dir）；
- 项目目录布局创建（_create_project_layout，与桌面向导 NewProjectWizard 的 4 类子目录一致）；
- 初始 config.yaml 与各阶段样本文件写入（_write_initial_config / _write_stage_samples）。

样本内容（_SAMPLE_*）为纯数据常量，随本模块迁移。
"""
from __future__ import annotations

import json
import os

from yaml import safe_load

from GalTransl import (
    CACHE_FOLDERNAME,
    INPUT_FOLDERNAME,
    OUTPUT_FOLDERNAME,
    PASS0_CACHE_DIR,
    PASS1_CACHE_DIR,
    PASS2_CACHE_DIR,
    PASS3_CACHE_DIR,
)
from GalTransl.DefaultProjectConfig import DEFAULT_PROJECT_CONFIG_YAML
from GalTransl.backend_security import safe_under_project
from GalTransl.server_config_schema import _write_yaml_file


# 新建项目目录布局（A-1）：与桌面向导 NewProjectWizard 创建的 4 类子目录保持一致
_SAMPLE_CACHE_FILENAME = "_示例缓存文件.json"
_SAMPLE_CACHE_JSON_CONTENT = json.dumps(
    [
        {
            "index": 1,
            "name": "",
            "pre_src": "これはサンプルの原文です。",
            "post_src": "これはサンプルの原文です。",
            "pre_dst": "这是示例原文。",
            "proofread_dst": "",
            "trans_by": "",
            "proofread_by": "",
            "problem": "",
            "trans_conf": 0,
            "doub_content": "",
            "unknown_proper_noun": "",
            "post_dst_preview": "这是示例原文。",
        }
    ],
    ensure_ascii=False,
    indent=2,
)

# 新建项目向导中生成示例 JSON 模板（正式命名，填写后直接生效）
_SAMPLE_GLOBAL_PROMPT_CONTENT = json.dumps(
    {
        "游戏名称": "",
        "剧情概述": "",
        "角色列表": [],
        "世界观设定": "",
        "行文风格": "",
        "题材标签": [],
        "备注": "可以在本文件中增加任意符合 JSON 格式的字段，它们会作为额外上下文注入后续阶段。",
    },
    ensure_ascii=False,
    indent=2,
)
_SAMPLE_FILE_META_CONTENT = json.dumps(
    {
        "id": "",
        "角色": [],
        "服装": "",
        "剧情": "",
        "标签": [],
    },
    ensure_ascii=False,
    indent=2,
)
_SAMPLE_BATCH_META_CONTENT = json.dumps(
    {
        "id": "",
        "批次": [
            {
                "区间": [1, 10],
                "视角": "",
                "氛围": "",
                "h": False,
                "用词色彩": "",
            },
            {
                "区间": [11, 20],
                "视角": "",
                "氛围": "",
                "h": False,
                "用词色彩": "",
            },
            {
                "区间": [21, 30],
                "视角": "",
                "氛围": "",
                "h": False,
                "用词色彩": "",
            },
        ],
        "备注": "批次为区间列表：每个对象含 区间(起止行号)/视角/氛围/h/用词色彩。可按此格式继续增加或删除批次，覆盖全文行号。",
    },
    ensure_ascii=False,
    indent=2,
)
_SAMPLE_PLOT_ROUTE_CONTENT = json.dumps(
    {
        "结构类型": "树",
        "用户大纲": "序章 → 三条女主角线（华恋/凛音/学生会）→ 各线汇合 TRUE END",
        "mermaid": "flowchart TD\n  subgraph 序章[序章]\n    A[开场]\n  end\n  subgraph 华恋线[华恋线]\n    B[华恋线剧情]\n  end\n  A --> B",
        "文件归属": {
            "00_01_アバンタイトル.txt.json": "序章",
            "01_01_華恋ルート.txt.json": "华恋线",
        },
        "节点剧情": {
            "序章": "开局与导入",
            "华恋线": "与华恋的互动与告白",
        },
        "备注": "剧情路线图：结构类型为 线性/树/有向无环图/有向有环图/混合；mermaid 为路线图源码（可用绘图页可视化编辑）；文件归属标记每个剧本文件所属路线；节点剧情为该路线剧情摘要（供注入）。",
    },
    ensure_ascii=False,
    indent=2,
)


def _workspace_root() -> str:
    """init 端点的项目根：由服务端配置，不接受客户端原始路径。"""
    root = (os.environ.get("GALTRANSL_WORKSPACE_ROOT") or "").strip()
    return os.path.normpath(root) if root else os.getcwd()


def _resolve_new_project_dir(name: str) -> str:
    """将客户端提供的项目名解析为服务端根下的绝对目录，越界则抛错。

    仅接受单一路径段（不含分隔符）；含分隔符或 `..` 一律拒绝，杜绝路径注入。
    """
    candidate = name.strip()
    if not candidate or candidate in (".", ".."):
        raise ValueError("非法的项目名")
    if any(sep in candidate for sep in ("/", "\\")):
        raise ValueError("项目名不能包含路径分隔符")
    return safe_under_project(_workspace_root(), candidate)


def _create_project_layout(
    project_dir: str,
    force: bool = False,
    pipeline: dict | None = None,
    game_info: str = "",
    sample_stages: set[str] | None = None,
) -> list[str]:
    """创建项目目录布局，返回所有已创建项的绝对路径。

    force=True 时覆盖 config.yaml 与示例缓存文件（用于向导「覆盖」已存在项目），
    目录本身始终以 exist_ok 创建，不删除既有译文/缓存。
    pipeline：流水线阶段开关 dict（键如 enableGlobalPrompt），值为 bool，
    写入 config.yaml 的 internals.pipeline 段。
    game_info：外部信息（externals.gameInfo），写入 config.yaml。
    sample_stages：用户勾选「生成示例文件」的阶段键集合，生成正式命名的示例
    JSON 模板（与禁用作独立功能，填好后直接生效）。
    """
    created: list[str] = []
    os.makedirs(project_dir, exist_ok=True)
    created.append(project_dir)

    input_dir = os.path.join(project_dir, INPUT_FOLDERNAME)
    output_dir = os.path.join(project_dir, OUTPUT_FOLDERNAME)
    cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
    for d in (input_dir, output_dir, cache_dir):
        os.makedirs(d, exist_ok=True)
        created.append(d)

    for sub in (PASS0_CACHE_DIR, PASS1_CACHE_DIR, PASS2_CACHE_DIR, PASS3_CACHE_DIR):
        sub_dir = os.path.join(cache_dir, sub)
        os.makedirs(sub_dir, exist_ok=True)
        created.append(sub_dir)

    config_path = os.path.join(project_dir, "config.yaml")
    if force or not os.path.isfile(config_path):
        if pipeline or game_info:
            _write_initial_config(config_path, pipeline, game_info)
        else:
            with open(config_path, "w", encoding="utf-8") as _f:
                _f.write(DEFAULT_PROJECT_CONFIG_YAML)
    created.append(config_path)

    sample_path = os.path.join(cache_dir, PASS3_CACHE_DIR, _SAMPLE_CACHE_FILENAME)
    if force or not os.path.isfile(sample_path):
        with open(sample_path, "w", encoding="utf-8") as _f:
            _f.write(_SAMPLE_CACHE_JSON_CONTENT)
    created.append(sample_path)

    # 生成示例 JSON 模板（独立于禁用阶段；创建时无输入文件，仅 GlobalPrompt 可生成）
    if sample_stages:
        _write_stage_samples(cache_dir, sample_stages, force=force)
    return created


def _write_initial_config(config_path: str, pipeline: dict, game_info: str) -> None:
    """合并向导传入的流水线开关与外部信息，写入初始 config.yaml。

    仅当向导提供了非默认配置时调用；因此用 safe_load/dump 重写（丢弃注释，
    默认配置无自定义时不走此路径，保持带注释的原始模板）。
    """
    data = safe_load(DEFAULT_PROJECT_CONFIG_YAML) or {}
    if pipeline:
        data.setdefault("internals", {}).setdefault("pipeline", {}).update(pipeline)
    if game_info:
        data.setdefault("externals", {})["gameInfo"] = game_info
    _write_yaml_file(config_path, data)


def _write_stage_samples(
    cache_dir: str, sample_stages: set[str], force: bool, input_files: list[str] | None = None
) -> None:
    """按用户勾选生成示例 JSON 模板（正式命名，填好后直接生效）。

    支持阶段：enableGlobalPrompt / enablePlotRoute / enableFileMeta / enableBatchMeta。
    GlobalPrompt、PlotRouteMap 为固定单文件；文件级/批次级元数据按输入
    文件名逐文件生成（{filename}.meta.json / {filename}.batch.json），
    与 load_file_metadata_map / load_batch_metadata_map 的读取命名一致。
    sample_stages：需要生成示例的阶段键集合。
    input_files：gt_input 下的输入文件名列表（含扩展名，如 00_01_xxx.txt.json）。
    """
    if "enableGlobalPrompt" in sample_stages:
        p = os.path.join(cache_dir, PASS0_CACHE_DIR, "GlobalPrompt.json")
        if force or not os.path.isfile(p):
            with open(p, "w", encoding="utf-8") as _f:
                _f.write(_SAMPLE_GLOBAL_PROMPT_CONTENT)
    if "enablePlotRoute" in sample_stages:
        p = os.path.join(cache_dir, PASS0_CACHE_DIR, "PlotRouteMap.json")
        if force or not os.path.isfile(p):
            with open(p, "w", encoding="utf-8") as _f:
                _f.write(_SAMPLE_PLOT_ROUTE_CONTENT)
    if "enableFileMeta" in sample_stages:
        for name in input_files or []:
            p = os.path.join(cache_dir, PASS1_CACHE_DIR, f"{name}.meta.json")
            if force or not os.path.isfile(p):
                with open(p, "w", encoding="utf-8") as _f:
                    _f.write(_SAMPLE_FILE_META_CONTENT)
    if "enableBatchMeta" in sample_stages:
        for name in input_files or []:
            p = os.path.join(cache_dir, PASS2_CACHE_DIR, f"{name}.batch.json")
            if force or not os.path.isfile(p):
                with open(p, "w", encoding="utf-8") as _f:
                    _f.write(_SAMPLE_BATCH_META_CONTENT)


