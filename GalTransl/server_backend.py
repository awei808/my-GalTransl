"""后端档案（backend profile）与模型可用性检测（0.4.10 从 server.py 抽出）。

职责：
- 全局后端配置的读写（_BACKEND_PROFILES_PATH / _read_backend_profiles / _write_backend_profiles）；
- 提示词模板默认值汇总（_DEFAULT_TRANSLATOR_PROMPTS / _build_prompt_templates_payload）；
- 模型 / token 可用性检测（_check_model_availability / _check_stage_model_availability）；
- 校对页 AI 建议的后端解析（_resolve_suggest_backend，含 _REVIEW_SUGGEST_LOCK）。

_REVIEW_SUGGEST_LOCK 与其使用者 _resolve_suggest_backend 同模块，避免状态与函数分离。
"""
from __future__ import annotations

import os
import threading
from typing import Any

from GalTransl import (
    LOGGER,
    NEED_OpenAITokenPool,
    TRANSLATOR_SUPPORTED,
    ReviewAssist,
)
from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.ConfigHelper import detect_config_file as _detect_config_file
from GalTransl.Utils import resolve_app_dir
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_TRANS_PROMPT,
    FORGAL_JSON_IMPROVE_PROMPT,
    FORGAL_JSON_BRSTATION_PROMPT,
    FORGAL_JSON_JPREPAIR_PROMPT,
    FORPLOTROUTE_PROMPT,
    FORPLOTROUTE_SYSTEM,
    FORFILEMETA_PROMPT,
    FORFILEMETA_SYSTEM,
    FORBATCHMETA_PROMPT,
    FORBATCHMETA_SYSTEM,
    FORGLOBAL_PROMPT,
    FORGLOBAL_SYSTEM,
    FORIMPROVE_SYSTEM,
    FORBR_SYSTEM,
    FORJP_SYSTEM,
    FORBAN_SYSTEM,
    FORTRANS_SYSTEM,
    FORGAL_JSON_BANFIX_PROMPT,
    FORFIXROUND_SYSTEM,
    FORWORDTONE_SYSTEM,
    FORGAL_JSON_FORWORDTONE_PROMPT,
    build_fix_round_prompt,
    GENDIC_PROMPT,
    GENDIC_SYSTEM,
)
from GalTransl.server_config_schema import _read_yaml_file, _write_yaml_file


# HTTP 检测接口专用的探活上限：前端 apiRequest 自带超时（见 client.ts），后端若沿用
# 翻译期的 apiTimeout（默认 300s）再乘 2 次重试，会把「检测」拖到分钟级 ——
# 实测慢模型（glm-5.3）单次探活 39s，前端 30s 超时先放弃，报「请求超时」假故障。
# 故检测路径把单请求超时封顶、重试降为 1 次；任务启动期检测（llm_runtime）不受影响。
_AVAILABILITY_CHECK_TIMEOUT_CAP = 30
_AVAILABILITY_CHECK_MAX_RETRIES = 1


def _availability_check_limits(pool: "COpenAITokenPool") -> dict[str, int]:
    """返回 HTTP 检测专用探活参数：单请求超时取 apiTimeout 与上限的较小值。"""
    try:
        base = int(float(getattr(pool, "timeout", 0) or 0))
    except (TypeError, ValueError):
        base = 0
    timeout = min(base, _AVAILABILITY_CHECK_TIMEOUT_CAP) if base > 0 else _AVAILABILITY_CHECK_TIMEOUT_CAP
    return {"timeout": max(1, timeout), "max_retries": _AVAILABILITY_CHECK_MAX_RETRIES}


async def _check_model_availability(
    project_dir: str,
    translator: str,
    config_file_name: str,
    backend_profile: str = "",
    backend_profile_data: dict | None = None,
) -> dict[str, Any]:
    """主动检测所选后端的模型 / token 可用性。

    复用与翻译任务相同的 tokenPool 构建逻辑：仅 OpenAI-Compatible 类后端
    （translator 名称含 NEED_OpenAITokenPool 片段）需要 token 检测；本地
    端点无需检测，返回 applicable=False。检测阶段 proxy 行为与真实翻译
    任务保持一致（不传 proxy）。
    """
    # ── 配置名自动解析（与 cache/save 保持一致）──
    resolved_config = config_file_name
    if not os.path.isfile(os.path.join(project_dir, config_file_name)):
        for candidate in ("config.inc.yaml", "config.yaml"):
            if os.path.isfile(os.path.join(project_dir, candidate)):
                resolved_config = candidate
                break

    try:
        cfg = CProjectConfig(project_dir, resolved_config)
        # 服务端检测走非交互模式：不激活 alive_bar，避免与其它进度条嵌套报错
        cfg.non_interactive = True
    except Exception:
        return {
            "ok": False,
            "applicable": True,
            "available": 0,
            "total": 0,
            "engine": translator,
            "message": f"无法加载项目配置文件（{resolved_config}），请检查配置是否存在",
        }

    # 应用全局后端配置（backend profile）覆盖 backendSpecific，使「检测」与「真实调用」使用同一份令牌来源。
    # 这样翻译项目的 AI 令牌统一由程序全局后端配置管理，项目自身 config.yaml 的 tokens 不再参与检测。
    profile: dict = backend_profile_data if isinstance(backend_profile_data, dict) else {}
    if not profile and backend_profile:
        profiles_data = _read_backend_profiles()
        profiles = profiles_data.get("profiles", {})
        if backend_profile in profiles:
            candidate = profiles[backend_profile]
            if isinstance(candidate, dict):
                profile = candidate
        # 未找到则回退到 config.yaml（兼容旧项目）
    using_profile = bool(profile)
    if profile:
        cfg.projectConfig["backendSpecific"] = profile
        if "proxy" in profile:
            cfg.projectConfig["proxy"] = profile["proxy"]
            cfg.refreshProxyEnabledFlag()
    token_src = (
        f"全局后端配置「{backend_profile}」"
        if (using_profile and backend_profile)
        else ("全局后端配置" if using_profile else "项目配置")
    )

    if not any(frag in translator for frag in NEED_OpenAITokenPool):
        return {
            "ok": True,
            "applicable": False,
            "available": 0,
            "total": 0,
            "engine": translator,
            "message": "该后端为本地 / 特殊端点，无需 API token 检测",
        }

    # 先读原始 token 列表，区分「配置中无 token」与「token 全是示例/不可用」
    raw_tokens = (
        cfg.getBackendConfigSection("OpenAI-Compatible").get("tokens") or []
    )
    example_count = sum(
        1 for t in raw_tokens if "-example-" in (t.get("token") or "")
    )
    real_count = len(raw_tokens) - example_count

    if len(raw_tokens) == 0:
        return {
            "ok": False,
            "applicable": True,
            "available": 0,
            "total": 0,
            "engine": translator,
            "message": (
                f"{token_src} 中没有 token。请到「后端配置」页添加真实 API Key"
                if using_profile
                else "未配置 AI 令牌：请到「后端配置」页添加全局 API Key，再返回此处检测"
            ),
        }
    if real_count == 0 and example_count > 0:
        return {
            "ok": False,
            "applicable": True,
            "available": 0,
            "total": example_count,
            "engine": translator,
            "message": f"{token_src} 中 {example_count} 个 token 均为示例 key（含 -example-），不会被使用。请替换为真实 API Key",
        }

    token_pool = COpenAITokenPool(cfg, translator)
    total = len(token_pool.tokens)
    if total == 0:
        return {
            "ok": False,
            "applicable": True,
            "available": 0,
            "total": 0,
            "engine": translator,
            "message": "Token 池构建结果为空，请检查 token 格式是否正确",
        }
    await token_pool.checkTokenAvailablity(**_availability_check_limits(token_pool))
    available = len(token_pool.tokens)
    result = {
        "ok": available > 0,
        "applicable": True,
        "available": available,
        "total": total,
        "engine": translator,
        "message": (
            f"模型可用，可用 token {available}/{total}"
            if available > 0
            else f"所有 {total} 个 token 均不可用（endpoint 无响应或 key 无效）"
        ),
    }
    stages = await _check_stage_model_availability(
        cfg, translator, backend_profile if (using_profile and backend_profile) else ""
    )
    if stages:
        result["stages"] = stages
    return result


_STAGE_CHECK_SKIP_MESSAGE = "与已检测的后端配置相同，跳过重复检测"


class _SuggestConfigError(Exception):
    """AI 建议的后端配置不可用（无 token / 无可用 profile），映射为 400。"""


# 校对页 AI 建议的全局单飞锁：同一时刻只允许一个请求在跑（防误触连发打爆 API）
_REVIEW_SUGGEST_LOCK = threading.Lock()


def _resolve_suggest_backend(payload: dict, project_dir: str) -> tuple[str, str, str]:
    """解析 AI 建议用的 (api_key, base_url, model)。

    口径：请求携带的 profile_data → 请求携带的 profile 名 → 项目配置 backendSpecific
    → 首个含 OpenAI-Compatible 的全局 profile。与 check-model / name-table 的
    解析顺序保持一致，任何一步都跳过 -example- 示例 key。
    """
    oai_section: dict | None = None
    profile_data = payload.get("backend_profile_data")
    if isinstance(profile_data, dict) and profile_data:
        candidate = profile_data.get("OpenAI-Compatible")
        if isinstance(candidate, dict):
            oai_section = candidate
    if oai_section is None:
        profile_name = str(payload.get("backend_profile", "")).strip()
        profiles = _read_backend_profiles().get("profiles", {})
        if profile_name:
            candidate = profiles.get(profile_name, {}).get("OpenAI-Compatible")
            if isinstance(candidate, dict):
                oai_section = candidate
            else:
                raise _SuggestConfigError(f"后端配置「{profile_name}」不存在或无 OpenAI-Compatible 段")
    if oai_section is None:
        # 项目自身的 backendSpecific（翻译任务实际使用的后端）
        try:
            resolved_config = _detect_config_file(project_dir)
            cfg = CProjectConfig(project_dir, resolved_config)
            cfg.non_interactive = True
            candidate = cfg.getBackendConfigSection("OpenAI-Compatible")
            if isinstance(candidate, dict):
                oai_section = candidate
        except Exception:
            oai_section = None
    if oai_section is None:
        for _name, _conf in _read_backend_profiles().get("profiles", {}).items():
            candidate = _conf.get("OpenAI-Compatible") if isinstance(_conf, dict) else None
            if isinstance(candidate, dict):
                oai_section = candidate
                break
    if not isinstance(oai_section, dict):
        raise _SuggestConfigError("未找到可用的 OpenAI 兼容后端配置，请先在「模型设置」中添加")

    token_entry = ReviewAssist.pick_real_token(oai_section.get("tokens"))
    if token_entry is None:
        raise _SuggestConfigError("后端配置中没有真实 API token（示例 key 不会被使用）")
    api_key = str(token_entry.get("token", "") or "")
    endpoint = str(token_entry.get("endpoint", "") or "https://api.openai.com")
    model = str(
        token_entry.get("modelName")
        or oai_section.get("rewriteModelName")
        or "gpt-4o-mini"
    )
    return api_key, ReviewAssist.normalize_base_url(endpoint), model


async def _check_stage_model_availability(
    cfg: "CProjectConfig", translator: str, main_profile_name: str
) -> list[dict[str, Any]]:
    """逐个检测 common.stageBackends 引用的阶段后端配置可用性。

    与主配置同名的 profile 跳过重复检测；返回列表为空表示项目未配置阶段独立 API。
    """
    from GalTransl.ConfigHelper import ALL_STAGE_BACKEND_KEYS

    stage_map = cfg.keyValues.get("stageBackends")
    if not isinstance(stage_map, dict) or not stage_map:
        return []
    profiles = _read_backend_profiles().get("profiles", {})
    results: list[dict[str, Any]] = []
    checked_names = {main_profile_name} if main_profile_name else set()

    def _stage_result(stage: str, profile: str, ok: bool, message: str,
                      available: int = 0, total: int = 0) -> dict[str, Any]:
        return {
            "stage": stage,
            "profile": profile,
            "ok": ok,
            "applicable": True,
            "available": available,
            "total": total,
            "message": message,
        }

    for stage_key, profile_name in stage_map.items():
        profile_name = str(profile_name or "").strip()
        if not profile_name:
            continue
        if stage_key not in ALL_STAGE_BACKEND_KEYS:
            results.append(_stage_result(
                stage_key, profile_name, False,
                f"未知阶段键 '{stage_key}'（可用：{', '.join(ALL_STAGE_BACKEND_KEYS)}）",
            ))
            continue
        if profile_name in checked_names:
            results.append(_stage_result(
                stage_key, profile_name, True, _STAGE_CHECK_SKIP_MESSAGE,
                available=-1, total=-1,
            ))
            continue
        candidate = profiles.get(profile_name)
        section = candidate.get("OpenAI-Compatible") if isinstance(candidate, dict) else None
        if not isinstance(section, dict):
            results.append(_stage_result(
                stage_key, profile_name, False,
                f"后端配置 '{profile_name}' 不存在或缺少 OpenAI-Compatible 段",
            ))
            continue
        raw_tokens = section.get("tokens") or []
        example_count = sum(
            1 for t in raw_tokens if "-example-" in (t.get("token") or "")
        )
        real_count = len(raw_tokens) - example_count
        if not raw_tokens:
            results.append(_stage_result(
                stage_key, profile_name, False, f"后端配置 '{profile_name}' 中没有 token",
            ))
            continue
        if real_count == 0:
            results.append(_stage_result(
                stage_key, profile_name, False,
                f"后端配置 '{profile_name}' 中 {example_count} 个 token 均为示例 key",
                available=0, total=example_count,
            ))
            continue
        try:
            pool = COpenAITokenPool(cfg, translator, section=section)
        except Exception as exc:
            results.append(_stage_result(
                stage_key, profile_name, False,
                f"后端配置 '{profile_name}' 令牌池构建失败: {exc}",
            ))
            continue
        total = len(pool.tokens)
        if total == 0:
            results.append(_stage_result(
                stage_key, profile_name, False,
                f"后端配置 '{profile_name}' 令牌池构建结果为空",
            ))
            continue
        await pool.checkTokenAvailablity(**_availability_check_limits(pool))
        available = len(pool.tokens)
        checked_names.add(profile_name)
        results.append(_stage_result(
            stage_key, profile_name, available > 0,
            (
                f"模型可用，可用 token {available}/{total}"
                if available > 0
                else f"所有 {total} 个 token 均不可用（endpoint 无响应或 key 无效）"
            ),
            available=available, total=total,
        ))
    return results


# Global backend profiles helpers

# 全局后端配置持久化文件（程序目录下），「后端配置」页与任务提交共同使用
_BACKEND_PROFILES_PATH = os.path.join(resolve_app_dir(), "backend_profiles.yaml")


_DEFAULT_TRANSLATOR_PROMPTS: dict[str, dict[str, str]] = {
    "ForGal-json-translate": {
        "system_prompt": FORTRANS_SYSTEM,
        "user_prompt": FORGAL_JSON_TRANS_PROMPT,
    },
    "ForFileMetaData": {
        "system_prompt": FORFILEMETA_SYSTEM,
        "user_prompt": FORFILEMETA_PROMPT,
    },
    "ForBatchMetaData": {
        "system_prompt": FORBATCHMETA_SYSTEM,
        "user_prompt": FORBATCHMETA_PROMPT,
    },
    "ForGlobalPrompt": {
        "system_prompt": FORGLOBAL_SYSTEM,
        "user_prompt": FORGLOBAL_PROMPT,
    },
    "ForImproveTranslation": {
        "system_prompt": FORIMPROVE_SYSTEM,
        "user_prompt": FORGAL_JSON_IMPROVE_PROMPT,
    },
    "ForBRStation": {
        "system_prompt": FORBR_SYSTEM,
        "user_prompt": FORGAL_JSON_BRSTATION_PROMPT,
    },
    "ForJPResidue": {
        "system_prompt": FORJP_SYSTEM,
        "user_prompt": FORGAL_JSON_JPREPAIR_PROMPT,
    },
    "ForBanWordFix": {
        "system_prompt": FORBAN_SYSTEM,
        "user_prompt": FORGAL_JSON_BANFIX_PROMPT,
    },
    "ForFixRound": {
        "system_prompt": FORFIXROUND_SYSTEM,
        "user_prompt": build_fix_round_prompt(""),
    },
    "ForToneCheck": {
        "system_prompt": FORWORDTONE_SYSTEM,
        "user_prompt": FORGAL_JSON_FORWORDTONE_PROMPT,
    },
    "ForPlotRouteMap": {
        "system_prompt": FORPLOTROUTE_SYSTEM,
        "user_prompt": FORPLOTROUTE_PROMPT,
    },
    "GenDic": {
        "system_prompt": GENDIC_SYSTEM,
        "user_prompt": GENDIC_PROMPT,
    },
}


def _read_backend_profiles() -> dict:
    """Read the global backend profiles YAML file."""
    if not os.path.isfile(_BACKEND_PROFILES_PATH):
        return {"profiles": {}}
    try:
        return _read_yaml_file(_BACKEND_PROFILES_PATH)
    except Exception as exc:
        # 解析失败不能让「按名引用阶段后端」静默变成「配置不存在」，留一条可见告警
        LOGGER.warning("读取全局后端配置失败（%s）：%s", _BACKEND_PROFILES_PATH, exc)
        return {"profiles": {}}


def _write_backend_profiles(data: dict) -> None:
    """Write the global backend profiles YAML file atomically."""
    _write_yaml_file(_BACKEND_PROFILES_PATH, data)


def _build_prompt_templates_payload() -> dict[str, Any]:
    templates: list[dict[str, Any]] = []
    for name, default_prompts in _DEFAULT_TRANSLATOR_PROMPTS.items():
        description_map = TRANSLATOR_SUPPORTED.get(name, {})
        if isinstance(description_map, dict):
            description = description_map.get("zh-cn") or next(iter(description_map.values()), "")
        else:
            description = ""
        default_system_prompt = default_prompts.get("system_prompt", "")
        default_user_prompt = default_prompts.get("user_prompt", "")
        templates.append(
            {
                "name": name,
                "description": description,
                "default_system_prompt": default_system_prompt,
                "system_prompt": default_system_prompt,
                "system_overridden": False,
                "default_user_prompt": default_user_prompt,
                "user_prompt": default_user_prompt,
                "user_overridden": False,
                "overridden": False,
            }
        )
    return {"templates": templates}


