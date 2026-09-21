# -*- coding: utf-8 -*-
"""大阶段独立 API（common.stageBackends）的单元测试。

覆盖：
  - Service._resolve_stage_backend_profiles：合法解析 / 留空跳过 / 未知键与缺失引用 fail-fast
  - COpenAITokenPool section 参数：池携带 profile 配置段（tokens/stream 读自该段），
    backend_section 属性回退主配置
  - Runner._build_stage_token_pools：按 stage_profiles 预建各阶段池，缺段回退告警
  - BaseEngine._effective_backend_section：池携带段实例级优先，Sakura 分支不覆盖
  - CProjectConfig.get_stage_token_pool：未配置阶段回退主池
  - server_runtime._compute_stage_index：阶段 7 新旧命名与别名映射

0.5.0 批次 2（每阶段独立后端）补充：
  - STAGE_BACKEND_KEYS 扩为 10 阶段键，旧 4 键降为 LEGACY，ALL 为并集
  - STAGE_BACKEND_FALLBACKS：6 个元数据域阶段回退旧 metadata 槽位
  - CProjectConfig.resolve_stage_pool_key：三级回退链（自身 → 回退槽位 → 自身）
  - _build_stage_token_pools：同一 profile 对象跨阶段共享同一池实例
"""
import unittest

from GalTransl.Service import _resolve_stage_backend_profiles
from GalTransl.ConfigHelper import (
    ALL_STAGE_BACKEND_KEYS,
    LEGACY_STAGE_BACKEND_KEYS,
    STAGE_BACKEND_FALLBACKS,
    STAGE_BACKEND_KEYS,
)
from GalTransl.Runner import _build_stage_token_pools
from GalTransl.Backend.BaseEngine import BaseEngine
from GalTransl.COpenAI import COpenAITokenPool
from GalTransl import server_runtime


def _profile(tokens: list | None = None) -> dict:
    return {
        "OpenAI-Compatible": {
            "tokens": tokens
            if tokens is not None
            else [{"token": "sk-stage", "endpoint": "https://stage.example.com"}],
            "stream": False,
        }
    }


class _FakeBackendConfig:
    """COpenAITokenPool 配置替身：主配置段可控。"""

    def __init__(self, section: dict) -> None:
        self._section = section

    def getBackendConfigSection(self, name: str) -> dict:
        return self._section


MAIN_SECTION = {
    "tokens": [{"token": "sk-main", "endpoint": "https://main.example.com"}],
    "stream": True,
}


class ResolveStageBackendProfilesTests(unittest.TestCase):
    def test_valid_map_resolved(self) -> None:
        profiles = {"pA": _profile(), "pB": _profile()}
        resolved = _resolve_stage_backend_profiles(
            {"metadata": "pA", "translate": "pB", "afterTrans": "  "},
            profiles,
        )
        self.assertEqual(set(resolved), {"metadata", "translate"})
        self.assertEqual(resolved["metadata"]["OpenAI-Compatible"]["stream"], False)

    def test_empty_or_invalid_map_returns_empty(self) -> None:
        for raw in (None, {}, "x", {"metadata": ""}, {"metadata": None}):
            with self.subTest(raw=raw):
                self.assertEqual(_resolve_stage_backend_profiles(raw, {}), {})

    def test_unknown_stage_key_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _resolve_stage_backend_profiles({"trans": "pA"}, {"pA": _profile()})
        self.assertIn("未知阶段键", str(ctx.exception))

    def test_missing_profile_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _resolve_stage_backend_profiles({"metadata": "ghost"}, {"pA": _profile()})
        self.assertIn("ghost", str(ctx.exception))

    def test_profile_missing_section_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _resolve_stage_backend_profiles({"metadata": "pBad"}, {"pBad": {"proxy": {}}})
        self.assertIn("OpenAI-Compatible", str(ctx.exception))

    def test_reserved_proofread_key_accepted(self) -> None:
        # proofread 为预留键：合法解析保存，供后续版本消费
        resolved = _resolve_stage_backend_profiles(
            {"proofread": "pA"}, {"pA": _profile()}
        )
        self.assertEqual(set(resolved), {"proofread"})

    def test_stage_keys_declared(self) -> None:
        # 0.5.0：阶段级键扩为 10（9 流水线阶段 + afterTrans），旧 4 键降为兼容键
        self.assertEqual(
            set(STAGE_BACKEND_KEYS),
            {
                "validate", "compress", "global_prompt", "gen_dic", "file_meta",
                "plot_route", "batch_meta", "translate", "afterTrans", "proofread",
            },
        )
        self.assertEqual(
            set(LEGACY_STAGE_BACKEND_KEYS),
            {"metadata", "translate", "afterTrans", "proofread"},
        )
        self.assertEqual(set(ALL_STAGE_BACKEND_KEYS), set(STAGE_BACKEND_KEYS) | {"metadata"})

    def test_new_metadata_substage_keys_accepted(self) -> None:
        # 6 个元数据域子阶段键均可写入并解析
        resolved = _resolve_stage_backend_profiles(
            {
                "global_prompt": "pA",
                "gen_dic": "pA",
                "file_meta": "pB",
                "plot_route": "pB",
                "batch_meta": "pA",
            },
            {"pA": _profile(), "pB": _profile()},
        )
        self.assertEqual(
            set(resolved), {"global_prompt", "gen_dic", "file_meta", "plot_route", "batch_meta"}
        )

    def test_legacy_metadata_key_still_accepted(self) -> None:
        # 旧项目 config.yaml 仍写 metadata：必须继续可用（批次 2 验收：旧配置仍生效）
        resolved = _resolve_stage_backend_profiles({"metadata": "pA"}, {"pA": _profile()})
        self.assertEqual(set(resolved), {"metadata"})


class StageBackendFallbackTests(unittest.TestCase):
    def test_metadata_domain_stages_fall_back_to_legacy_slot(self) -> None:
        for key in ("global_prompt", "gen_dic", "file_meta", "plot_route", "batch_meta"):
            with self.subTest(key=key):
                self.assertEqual(STAGE_BACKEND_FALLBACKS.get(key), "metadata")

    def test_non_metadata_stages_have_no_fallback(self) -> None:
        # translate/afterTrans/proofread 为独立域：无回退槽位，未配置即跟随主配置
        for key in ("validate", "compress", "translate", "afterTrans", "proofread"):
            with self.subTest(key=key):
                self.assertEqual(STAGE_BACKEND_FALLBACKS.get(key), "")

    def test_every_stage_key_has_fallback_entry(self) -> None:
        self.assertEqual(set(STAGE_BACKEND_FALLBACKS), set(STAGE_BACKEND_KEYS))

    def _proj(self, pools: dict):
        from GalTransl.ConfigHelper import CProjectConfig

        proj = object.__new__(CProjectConfig)
        proj.tokenPool = object()
        proj.stage_token_pools = pools
        return proj

    def test_resolve_prefers_own_stage_pool(self) -> None:
        proj = self._proj({"global_prompt": object(), "metadata": object()})
        self.assertEqual(proj.resolve_stage_pool_key("global_prompt"), "global_prompt")

    def test_resolve_falls_back_to_legacy_slot(self) -> None:
        proj = self._proj({"metadata": object()})
        self.assertEqual(proj.resolve_stage_pool_key("global_prompt"), "metadata")
        self.assertEqual(proj.resolve_stage_pool_key("plot_route"), "metadata")

    def test_resolve_returns_stage_when_nothing_configured(self) -> None:
        proj = self._proj({})
        self.assertEqual(proj.resolve_stage_pool_key("global_prompt"), "global_prompt")

    def test_get_stage_token_pool_uses_fallback_chain(self) -> None:
        legacy = object()
        proj = self._proj({"metadata": legacy})
        self.assertIs(proj.get_stage_token_pool("file_meta"), legacy)

    def test_get_stage_token_pool_main_when_all_empty(self) -> None:
        proj = self._proj({})
        self.assertIs(proj.get_stage_token_pool("file_meta"), proj.tokenPool)


class TokenPoolSectionTests(unittest.TestCase):
    def test_pool_without_section_reads_main_config(self) -> None:
        cfg = _FakeBackendConfig(MAIN_SECTION)
        pool = COpenAITokenPool(cfg, "ForGal-json-translate")
        self.assertEqual(len(pool.tokens), 1)
        self.assertEqual(pool.tokens[0][1].token, "sk-main")
        self.assertEqual(pool.backend_section, MAIN_SECTION)
        self.assertTrue(pool.stream)

    def test_pool_with_section_reads_profile(self) -> None:
        cfg = _FakeBackendConfig(MAIN_SECTION)
        profile = _profile([{"token": "sk-stage", "endpoint": "https://s.example.com"}])
        pool = COpenAITokenPool(cfg, "ForGal-json-translate", section=profile["OpenAI-Compatible"])
        self.assertEqual(len(pool.tokens), 1)
        self.assertEqual(pool.tokens[0][1].token, "sk-stage")
        self.assertIs(pool.backend_section, profile["OpenAI-Compatible"])
        # profile 段的 stream=False 覆盖主配置 True
        self.assertFalse(pool.stream)


class _FakeRunnerConfig:
    """Runner._build_stage_token_pools 配置替身。"""

    def __init__(self, stage_profiles: dict) -> None:
        self.stage_profiles = stage_profiles
        self.stage_token_pools = {}

    def getBackendConfigSection(self, name: str) -> dict:
        return MAIN_SECTION


class BuildStageTokenPoolsTests(unittest.TestCase):
    def test_pools_built_per_stage_with_profile_section(self) -> None:
        cfg = _FakeRunnerConfig(
            {
                "metadata": _profile([{"token": "sk-m", "endpoint": "https://m.example.com"}]),
                "afterTrans": _profile([{"token": "sk-a", "endpoint": "https://a.example.com"}]),
            }
        )
        _build_stage_token_pools(cfg, "ForGal-full-pipeline")
        self.assertEqual(set(cfg.stage_token_pools), {"metadata", "afterTrans"})
        self.assertEqual(cfg.stage_token_pools["metadata"].tokens[0][1].token, "sk-m")
        self.assertEqual(cfg.stage_token_pools["afterTrans"].tokens[0][1].token, "sk-a")

    def test_stage_without_section_skipped(self) -> None:
        cfg = _FakeRunnerConfig({"metadata": {"proxy": {}}})
        _build_stage_token_pools(cfg, "ForGal-full-pipeline")
        self.assertEqual(cfg.stage_token_pools, {})

    def test_no_stage_profiles_noop(self) -> None:
        cfg = _FakeRunnerConfig({})
        _build_stage_token_pools(cfg, "ForGal-full-pipeline")
        self.assertEqual(cfg.stage_token_pools, {})

    def test_same_profile_object_shares_one_pool(self) -> None:
        # 多个阶段引用同一 profile 对象 → 复用一个池实例，避免重复建池与令牌重复计数
        shared = _profile([{"token": "sk-shared", "endpoint": "https://s.example.com"}])
        cfg = _FakeRunnerConfig({"global_prompt": shared, "gen_dic": shared, "file_meta": shared})
        _build_stage_token_pools(cfg, "ForGal-full-pipeline")
        self.assertEqual(
            set(cfg.stage_token_pools), {"global_prompt", "gen_dic", "file_meta"}
        )
        self.assertIs(
            cfg.stage_token_pools["global_prompt"], cfg.stage_token_pools["gen_dic"]
        )
        self.assertIs(
            cfg.stage_token_pools["gen_dic"], cfg.stage_token_pools["file_meta"]
        )

    def test_distinct_profiles_get_distinct_pools(self) -> None:
        cfg = _FakeRunnerConfig(
            {
                "global_prompt": _profile([{"token": "sk-a", "endpoint": "https://a.example.com"}]),
                "gen_dic": _profile([{"token": "sk-b", "endpoint": "https://b.example.com"}]),
            }
        )
        _build_stage_token_pools(cfg, "ForGal-full-pipeline")
        self.assertIsNot(
            cfg.stage_token_pools["global_prompt"], cfg.stage_token_pools["gen_dic"]
        )
        self.assertEqual(cfg.stage_token_pools["global_prompt"].tokens[0][1].token, "sk-a")
        self.assertEqual(cfg.stage_token_pools["gen_dic"].tokens[0][1].token, "sk-b")


class EffectiveBackendSectionTests(unittest.TestCase):
    class _PoolWithSection:
        backend_section = {"stream": False, "provider": "custom"}

    class _PlainPool:
        pass

    class _FakeCfg:
        def __init__(self, section: dict) -> None:
            self._section = section

        def getBackendConfigSection(self, name: str) -> dict:
            return self._section

    def _engine(self, token_provider) -> BaseEngine:
        obj = object.__new__(BaseEngine)
        obj.tokenProvider = token_provider
        obj.pj_config = self._FakeCfg(MAIN_SECTION)
        return obj

    def test_pool_section_overrides_main(self) -> None:
        eng = self._engine(self._PoolWithSection())
        section = eng._effective_backend_section()
        self.assertEqual(section["provider"], "custom")

    def test_plain_pool_falls_back_to_main(self) -> None:
        eng = self._engine(self._PlainPool())
        self.assertEqual(eng._effective_backend_section(), MAIN_SECTION)

    def test_sakura_section_never_overridden(self) -> None:
        eng = self._engine(self._PoolWithSection())
        self.assertEqual(
            eng._effective_backend_section("SakuraLLM"), MAIN_SECTION
        )


class GetStageTokenPoolTests(unittest.TestCase):
    def test_falls_back_to_main_when_stage_missing(self) -> None:
        from GalTransl.ConfigHelper import CProjectConfig

        proj = object.__new__(CProjectConfig)
        main = object()
        proj.tokenPool = main
        proj.stage_token_pools = {}
        self.assertIs(proj.get_stage_token_pool("metadata"), main)

    def test_stage_pool_preferred_when_configured(self) -> None:
        from GalTransl.ConfigHelper import CProjectConfig

        proj = object.__new__(CProjectConfig)
        main, stage_pool = object(), object()
        proj.tokenPool = main
        proj.stage_token_pools = {"metadata": stage_pool}
        self.assertIs(proj.get_stage_token_pool("metadata"), stage_pool)


class ComputeStageIndexAliasTests(unittest.TestCase):
    def test_new_stage7_name_prefixed(self) -> None:
        self.assertEqual(server_runtime._compute_stage_index("AI初步处理-fix"), 7)

    def test_old_stage7_name_alias(self) -> None:
        self.assertEqual(server_runtime._compute_stage_index("译文质量改进"), 7)
        self.assertEqual(server_runtime._compute_stage_index("译文质量改进完成"), 7)

    def test_legacy_postprocess_prefix_alias(self) -> None:
        self.assertEqual(server_runtime._compute_stage_index("后处理-improve"), 7)

    def test_other_stages_unaffected(self) -> None:
        self.assertEqual(server_runtime._compute_stage_index("翻译执行中"), 6)
        self.assertEqual(server_runtime._compute_stage_index("生成全局游戏分析"), 2)
        self.assertEqual(server_runtime._compute_stage_index("未知阶段"), -1)


if __name__ == "__main__":
    unittest.main()
