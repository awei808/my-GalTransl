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
"""
import unittest

from GalTransl.Service import _resolve_stage_backend_profiles
from GalTransl.ConfigHelper import STAGE_BACKEND_KEYS
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
        self.assertEqual(
            set(STAGE_BACKEND_KEYS), {"metadata", "translate", "afterTrans", "proofread"}
        )


class TokenPoolSectionTests(unittest.TestCase):
    def test_pool_without_section_reads_main_config(self) -> None:
        cfg = _FakeBackendConfig(MAIN_SECTION)
        pool = COpenAITokenPool(cfg, "ForGal-json-multi-chat")
        self.assertEqual(len(pool.tokens), 1)
        self.assertEqual(pool.tokens[0][1].token, "sk-main")
        self.assertEqual(pool.backend_section, MAIN_SECTION)
        self.assertTrue(pool.stream)

    def test_pool_with_section_reads_profile(self) -> None:
        cfg = _FakeBackendConfig(MAIN_SECTION)
        profile = _profile([{"token": "sk-stage", "endpoint": "https://s.example.com"}])
        pool = COpenAITokenPool(cfg, "ForGal-json-multi-chat", section=profile["OpenAI-Compatible"])
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
