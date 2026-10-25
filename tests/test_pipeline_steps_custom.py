# -*- coding: utf-8 -*-
"""自定义流水线步骤（internals.pipeline.steps 低代码有序列表）单元测试。

覆盖：
  - resolve_pipeline_steps：缺省回退默认阶段顺序 / 自定义混排（阶段 key + 译后
    条目，重复允许）/ 字符串条目规范化 / 裸 fix 拒收 / 未知条目剔除告警 /
    非法类型回退（custom=False，enable* 仍生效）/ 译后步骤位于翻译前（或无
    翻译阶段）告警 / 完全相同译后条目重复告警 / 高成本阶段重复告警 / enable*
    开关被忽略告警；
  - _aftertrans_entry_label / _aftertrans_group_id：多 fix 实例的展示标签与
    断点标记分组（不同类型组合不冲突，improve 宏空分组沿用旧标记名）；
  - _coerce_problem_type_list：问题类型支持名称、序号（int / 数字字符串，按
    /api/problem-types 目录顺序解析）、枚举成员三种写法；目录顺序与
    server_meta._PROBLEM_TYPE_CATALOG 锁定一致；
  - _run_full_pipeline：自定义步骤调度（译后步骤分发 + executed 依赖判定 +
    权威模式标志），improve 宏 group_id 为空串。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from GalTransl.ConfigHelper import CProblemType, PROBLEM_TYPE_CATALOG_ORDER
from GalTransl.Frontend.pipeline_stages import (
    AFTER_TRANS_STEP_KEYS,
    DEFAULT_PIPELINE_STEPS,
    resolve_pipeline_steps,
    to_payload,
)


def _cfg(steps=None, enable=None):
    """轻量配置桩：只响应 steps 与 enable* 开关两类键。"""

    def getKey(key, default=None):
        if key == "internals.pipeline.steps":
            return steps
        if enable and key in enable:
            return enable[key]
        return default

    return SimpleNamespace(getKey=getKey)


class ResolvePipelineStepsTests(unittest.TestCase):
    def test_absent_config_returns_default_stage_order_legacy(self) -> None:
        steps, warnings, custom = resolve_pipeline_steps(_cfg(steps=None))
        self.assertEqual(steps, list(DEFAULT_PIPELINE_STEPS))
        self.assertEqual(warnings, [])
        self.assertFalse(custom)

    def test_custom_mix_of_stages_and_aftertrans_entries(self) -> None:
        fix_a = {"fix": {"types": ["残留日文"], "injectProblem": True}}
        fix_b = {"fix": {"types": [3, "4"], "injectProblem": False}}
        raw = ["validate", "compress", "file_meta", "translate", fix_a,
               "semcheck", fix_b, "improve"]
        steps, warnings, custom = resolve_pipeline_steps(_cfg(steps=raw))
        self.assertEqual(steps, raw)
        self.assertEqual(warnings, [])
        self.assertTrue(custom)

    def test_string_entries_normalized_to_lowercase(self) -> None:
        # 大小写/空白变体规范化（与运行时精确字符串匹配口径一致）
        steps, warnings, custom = resolve_pipeline_steps(
            _cfg(steps=["VALIDATE", "Translate", " SemCheck "])
        )
        self.assertEqual(steps, ["validate", "translate", "semcheck"])
        self.assertEqual(warnings, [])
        self.assertTrue(custom)

    def test_bare_fix_string_rejected_with_warning(self) -> None:
        steps, warnings, custom = resolve_pipeline_steps(
            _cfg(steps=["validate", "fix", "translate"])
        )
        self.assertEqual(steps, ["validate", "translate"])
        self.assertTrue(any("fix" in w and "对象条目" in w for w in warnings))

    def test_unknown_entries_dropped_with_warning(self) -> None:
        steps, warnings, custom = resolve_pipeline_steps(
            _cfg(steps=["validate", "hahaha", 42, {"not_fix": {}}, "translate"])
        )
        self.assertEqual(steps, ["validate", "translate"])
        self.assertEqual(len(warnings), 3)
        self.assertTrue(custom)

    def test_non_list_falls_back_to_default_legacy(self) -> None:
        steps, warnings, custom = resolve_pipeline_steps(
            _cfg(steps="validate+translate")
        )
        self.assertEqual(steps, list(DEFAULT_PIPELINE_STEPS))
        self.assertTrue(warnings)
        # 非法回退默认：不算权威模式，enable* 开关仍由各阶段消费
        self.assertFalse(custom)

    def test_empty_list_falls_back_to_default_legacy(self) -> None:
        steps, warnings, custom = resolve_pipeline_steps(_cfg(steps=[]))
        self.assertEqual(steps, list(DEFAULT_PIPELINE_STEPS))
        self.assertTrue(any("未包含任何有效步骤" in w for w in warnings))
        self.assertFalse(custom)

    def test_aftertrans_before_translate_warns(self) -> None:
        _, warnings, _ = resolve_pipeline_steps(
            _cfg(steps=["validate", "semcheck", "translate"])
        )
        self.assertTrue(any("位于翻译执行之前" in w for w in warnings))

    def test_aftertrans_without_translate_also_warns(self) -> None:
        # 无翻译阶段的流水线里译后步骤永远无输入，同样告警
        _, warnings, _ = resolve_pipeline_steps(
            _cfg(steps=["validate", "compress", "file_meta", "semcheck"])
        )
        self.assertTrue(any("位于翻译执行之前" in w for w in warnings))

    def test_expensive_stage_repeat_warns(self) -> None:
        _, warnings, _ = resolve_pipeline_steps(
            _cfg(steps=["validate", "file_meta", "file_meta", "translate"])
        )
        self.assertTrue(any("file_meta" in w and "2 次" in w for w in warnings))

    def test_improve_macro_repeat_warns_as_expensive(self) -> None:
        # improve 宏重复等于整条 afterTranslation 链跑两遍，同样告警
        _, warnings, _ = resolve_pipeline_steps(
            _cfg(steps=["translate", "improve", "improve"])
        )
        self.assertTrue(any("improve" in w and "2 次" in w for w in warnings))

    def test_aftertrans_repeat_not_flagged_as_expensive(self) -> None:
        fix_a = {"fix": {"types": ["残留日文"]}}
        fix_b = {"fix": {"types": ["单句过长"]}}
        _, warnings, _ = resolve_pipeline_steps(
            _cfg(steps=["translate", fix_a, "semcheck", fix_b])
        )
        self.assertFalse(any("重复执行通常无意义" in w for w in warnings))

    def test_identical_aftertrans_duplicate_warns(self) -> None:
        # 完全相同的译后条目共享断点分组：第二次出现会被标记直接跳过
        fix_a = {"fix": {"types": ["残留日文"], "injectProblem": True}}
        _, warnings, _ = resolve_pipeline_steps(
            _cfg(steps=["translate", fix_a, "semcheck", {"fix": dict(fix_a["fix"])}])
        )
        self.assertTrue(any("完全相同" in w for w in warnings))

    def test_disabled_enable_keys_ignored_warns_when_steps_valid(self) -> None:
        _, warnings, custom = resolve_pipeline_steps(
            _cfg(steps=["validate", "translate"],
                 enable={"internals.pipeline.enableFileMeta": False})
        )
        self.assertTrue(any("不再生效" in w for w in warnings))
        self.assertTrue(custom)

    def test_disabled_enable_keys_no_warning_without_steps(self) -> None:
        _, warnings, custom = resolve_pipeline_steps(
            _cfg(steps=None, enable={"internals.pipeline.enableFileMeta": False})
        )
        self.assertEqual(warnings, [])
        self.assertFalse(custom)

    def test_after_trans_vocabulary_excludes_improve_and_bare_fix(self) -> None:
        # improve 在步骤列表中是阶段宏（展开 gpt.afterTranslation 链），不算译后步骤
        self.assertNotIn("improve", AFTER_TRANS_STEP_KEYS)
        self.assertIn("fix", AFTER_TRANS_STEP_KEYS)

    def test_payload_contains_default_steps(self) -> None:
        payload = to_payload()
        self.assertEqual(payload["default_steps"], list(DEFAULT_PIPELINE_STEPS))


class StageEnabledStepsModeTests(unittest.TestCase):
    """_stage_enabled 的权威模式判定：以 resolver 解析一次后的标志为准。"""

    def test_custom_mode_ignores_enable_keys(self) -> None:
        from GalTransl.Frontend.llm_pipeline import _stage_enabled

        cfg = _cfg(
            steps=["validate", "translate"],
            enable={"internals.pipeline.enableFileMeta": False},
        )
        cfg.pipeline_steps_custom = True
        self.assertTrue(_stage_enabled(cfg, "file_meta"))

    def test_invalid_steps_fallback_still_honors_enable_keys(self) -> None:
        from GalTransl.Frontend.llm_pipeline import _stage_enabled

        cfg = _cfg(
            steps=[],
            enable={"internals.pipeline.enableFileMeta": False},
        )
        cfg.pipeline_steps_custom = False
        self.assertFalse(_stage_enabled(cfg, "file_meta"))
        self.assertTrue(_stage_enabled(cfg, "translate"))

    def test_missing_flag_defaults_to_legacy_behavior(self) -> None:
        from GalTransl.Frontend.llm_pipeline import _stage_enabled

        cfg = _cfg(steps=None, enable={"internals.pipeline.enableGenDic": False})
        self.assertFalse(_stage_enabled(cfg, "gen_dic"))
        self.assertTrue(_stage_enabled(cfg, "translate"))


class AftertransLabelAndGroupTests(unittest.TestCase):
    def test_label_string_entry_passthrough(self) -> None:
        from GalTransl.Frontend.llm_postprocess import _aftertrans_entry_label

        self.assertEqual(_aftertrans_entry_label("semcheck"), "semcheck")

    def test_label_fix_entry_shows_types(self) -> None:
        from GalTransl.Frontend.llm_postprocess import _aftertrans_entry_label

        label = _aftertrans_entry_label(
            {"fix": {"types": ["残留日文", "换行位置异常"]}}
        )
        self.assertEqual(label, "fix(残留日文、换行位置异常)")

    def test_label_fix_entry_truncates_long_type_list(self) -> None:
        from GalTransl.Frontend.llm_postprocess import _aftertrans_entry_label

        label = _aftertrans_entry_label(
            {"fix": {"types": ["残留日文", "换行位置异常", "单句过长"]}}
        )
        self.assertIn("等3类", label)

    def test_label_fix_entry_without_types(self) -> None:
        from GalTransl.Frontend.llm_postprocess import _aftertrans_entry_label

        self.assertEqual(_aftertrans_entry_label({"fix": {}}), "fix(未选类型)")

    def test_group_id_distinct_for_different_fix_configs(self) -> None:
        from GalTransl.Frontend.llm_pipeline import _aftertrans_group_id

        a = _aftertrans_group_id({"fix": {"types": ["残留日文"]}})
        b = _aftertrans_group_id({"fix": {"types": ["单句过长"]}})
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, "")

    def test_group_id_same_config_deterministic(self) -> None:
        from GalTransl.Frontend.llm_pipeline import _aftertrans_group_id

        entry = {"fix": {"types": ["残留日文"], "injectProblem": True}}
        self.assertEqual(_aftertrans_group_id(entry), _aftertrans_group_id(entry))

    def test_group_id_string_variant_normalizes_same(self) -> None:
        from GalTransl.Frontend.llm_pipeline import _aftertrans_group_id

        self.assertEqual(
            _aftertrans_group_id("semcheck"), _aftertrans_group_id(" SemCheck ")
        )


class CoerceProblemTypeListTests(unittest.TestCase):
    def test_coerce_by_catalog_index(self) -> None:
        from GalTransl.Backend.BaseFixRound import BaseImproveRound

        coerced = BaseImproveRound._coerce_problem_type_list([1, 2])
        self.assertEqual(coerced, [CProblemType.词频过高, CProblemType.标点错漏])

    def test_coerce_numeric_string(self) -> None:
        from GalTransl.Backend.BaseFixRound import BaseImproveRound

        coerced = BaseImproveRound._coerce_problem_type_list(["1", " 3 "])
        self.assertEqual(coerced, [CProblemType.词频过高, CProblemType.残留日文])

    def test_coerce_follows_catalog_order_not_enum_value(self) -> None:
        # 目录顺序与枚举 value 在以下类型处不同：序号必须按目录解析
        from GalTransl.Backend.BaseFixRound import BaseImproveRound

        coerced = BaseImproveRound._coerce_problem_type_list([7, 8, 9, 16, 17])
        self.assertEqual(
            coerced,
            [
                CProblemType.比日文长严格,
                CProblemType.字典使用,
                CProblemType.引入英文,
                CProblemType.用词不当,
                CProblemType.状语过长,
            ],
        )

    def test_coerce_by_name_still_works(self) -> None:
        from GalTransl.Backend.BaseFixRound import BaseImproveRound

        coerced = BaseImproveRound._coerce_problem_type_list(["残留日文"])
        self.assertEqual(coerced, [CProblemType.残留日文])

    def test_coerce_mixed_writes(self) -> None:
        from GalTransl.Backend.BaseFixRound import BaseImproveRound

        coerced = BaseImproveRound._coerce_problem_type_list(["残留日文", 2, "4"])
        self.assertEqual(
            coerced,
            [CProblemType.残留日文, CProblemType.标点错漏, CProblemType.丢失换行],
        )

    def test_coerce_out_of_range_dropped(self) -> None:
        from GalTransl.Backend.BaseFixRound import BaseImproveRound

        max_index = len(PROBLEM_TYPE_CATALOG_ORDER)
        coerced = BaseImproveRound._coerce_problem_type_list(
            [0, max_index + 1, "999"]
        )
        self.assertEqual(coerced, [])

    def test_coerce_unknown_name_dropped(self) -> None:
        from GalTransl.Backend.BaseFixRound import BaseImproveRound

        coerced = BaseImproveRound._coerce_problem_type_list(["不存在的类型"])
        self.assertEqual(coerced, [])

    def test_coerce_bool_ignored(self) -> None:
        from GalTransl.Backend.BaseFixRound import BaseImproveRound

        # bool 是 int 子类：True 不能被当成序号 1
        coerced = BaseImproveRound._coerce_problem_type_list([True, False])
        self.assertEqual(coerced, [])


class ProblemTypeCatalogOrderContractTests(unittest.TestCase):
    """序号解析基准（PROBLEM_TYPE_CATALOG_ORDER）与 /api/problem-types 目录锁定一致。"""

    def test_catalog_names_match_config_helper_order(self) -> None:
        from GalTransl.server_meta import _PROBLEM_TYPE_CATALOG

        self.assertEqual(
            [e["name"] for e in _PROBLEM_TYPE_CATALOG],
            list(PROBLEM_TYPE_CATALOG_ORDER),
        )

    def test_order_names_all_valid_enum_members(self) -> None:
        for name in PROBLEM_TYPE_CATALOG_ORDER:
            self.assertIn(name, CProblemType.__members__)


class FullPipelineDispatchTests(unittest.TestCase):
    """自定义步骤调度：译后条目分发与 improve 宏分组（不真正执行后处理）。"""

    def test_custom_steps_dispatch_aftertrans_and_improve_macro(self) -> None:
        import asyncio

        from GalTransl.Frontend import llm_pipeline as lp

        fix_entry = {"fix": {"types": ["残留日文"]}}
        raw_steps = ["validate", "translate", fix_entry, "improve"]
        called = {}

        def make_handler(key):
            async def handler(projectConfig, file_json_lists, file_list, stage_ctx):
                called.setdefault("stages", []).append(key)
                called["ctx"] = stage_ctx
                if key == "translate":
                    stage_ctx["file_chunks"] = {"a.json": [object()]}

            return handler

        async def fake_improve(projectConfig, file_chunks, order, group_id=""):
            called.setdefault("improve_calls", []).append((list(order), group_id))

        proj = SimpleNamespace(
            getKey=lambda key, default=None: {
                "internals.pipeline.steps": raw_steps,
                # improve 宏展开用的后处理链（非空才会真正调到 run_improve_stage）
                "gpt.afterTranslation": ["improve"],
            }.get(key, default),
            getProjectDir=lambda: "/proj",
        )
        handlers = {k: make_handler(k) for k in DEFAULT_PIPELINE_STEPS if k != "improve"}
        # improve 用真实处理函数（内部经补丁后的 run_improve_stage 展开宏链）
        handlers["improve"] = lp._run_stage_improve
        with (
            patch.object(lp, "_STAGE_HANDLERS", handlers),
            patch.object(lp, "_check_stop_requested"),
            patch.object(lp, "_update_runtime"),
            patch.object(lp, "reset_runtime_previews"),
            patch.object(lp, "record_runtime_notice"),
            patch(
                "GalTransl.Frontend.llm_postprocess.run_improve_stage", fake_improve
            ),
        ):
            asyncio.run(lp._run_full_pipeline(proj, {}, []))

        # 阶段按 steps 顺序调度（improve 为真实处理函数，不进 fake 记录）；
        # fix 条目与 improve 宏各触发一次 run_improve_stage
        self.assertEqual(called["stages"], ["validate", "translate"])
        self.assertEqual(len(called["improve_calls"]), 2)
        # 先分发译后步骤（独立分组 id），后执行 improve 宏（空分组沿用旧标记名）
        self.assertEqual(called["improve_calls"][0][0], [fix_entry])
        self.assertNotEqual(called["improve_calls"][0][1], "")
        self.assertEqual(called["improve_calls"][1][0], ["improve"])
        self.assertEqual(called["improve_calls"][1][1], "")
        # executed 集合含已执行阶段（译后条目不计入）；权威模式标志已挂到配置
        self.assertIn("translate", called["ctx"]["executed"])
        self.assertNotIn("fix", called["ctx"]["executed"])
        self.assertTrue(proj.pipeline_steps_custom)

    def test_invalid_steps_fallback_marks_legacy_mode(self) -> None:
        import asyncio

        from GalTransl.Frontend import llm_pipeline as lp

        called = {}

        def make_handler(key):
            async def handler(projectConfig, file_json_lists, file_list, stage_ctx):
                called.setdefault("stages", []).append(key)
                called["ctx"] = stage_ctx

            return handler

        proj = SimpleNamespace(
            getKey=lambda key, default=None: {
                # 非法 steps（非列表）→ 回退默认阶段顺序，enable* 仍生效
                "internals.pipeline.steps": "validate+translate",
                "internals.pipeline.enableFileMeta": False,
            }.get(key, default),
            getProjectDir=lambda: "/proj",
        )
        handlers = {k: make_handler(k) for k in DEFAULT_PIPELINE_STEPS}
        with (
            patch.object(lp, "_STAGE_HANDLERS", handlers),
            patch.object(lp, "_check_stop_requested"),
            patch.object(lp, "_update_runtime"),
            patch.object(lp, "reset_runtime_previews"),
            patch.object(lp, "record_runtime_notice"),
        ):
            asyncio.run(lp._run_full_pipeline(proj, {}, []))

        # 回退默认且非权威模式：被 enable 开关关闭的 file_meta 不进 executed 集合
        # （假 handler 不做 enable 跳过，真 handler 内部会依 _stage_enabled 跳过）
        self.assertNotIn("file_meta", called["ctx"]["executed"])
        self.assertIn("translate", called["ctx"]["executed"])
        self.assertFalse(proj.pipeline_steps_custom)

    def test_improve_macro_group_id_empty_in_stage_handler(self) -> None:
        """improve 阶段处理函数内部调用 run_improve_stage 不传 group_id（旧标记兼容）。"""
        import inspect

        from GalTransl.Frontend import llm_pipeline as lp

        src = inspect.getsource(lp._run_stage_improve)
        self.assertIn("run_improve_stage(projectConfig, file_chunks, order)", src)


if __name__ == "__main__":
    unittest.main()
