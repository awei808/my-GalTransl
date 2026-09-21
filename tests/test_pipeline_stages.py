"""流水线阶段清单（pipeline_stages）契约测试。

锁定三件事，防止前后端阶段口径漂移：
1. 清单覆盖全部 9 个阶段，key/开关路径与 DefaultProjectConfig 模板一致；
2. 依赖关系与「压缩文本」硬依赖表达正确（供跳过原因推导）；
3. 导出 payload 的结构稳定（前端与 MCP 客户端依赖此结构）。
"""
import unittest

from GalTransl.DefaultProjectConfig import DEFAULT_PROJECT_CONFIG_YAML
from GalTransl.Frontend.pipeline_stages import (
    PIPELINE_STAGES,
    SAMPLE_PRODUCTS,
    STAGES_BY_KEY,
    compute_skip_reasons,
    get_stage,
    stage_display_label,
    to_payload,
)


class PipelineStageManifestTests(unittest.TestCase):
    def test_manifest_covers_all_nine_stages(self) -> None:
        self.assertEqual(
            [s.key for s in PIPELINE_STAGES],
            [
                "validate",
                "compress",
                "global_prompt",
                "gen_dic",
                "file_meta",
                "plot_route",
                "batch_meta",
                "translate",
                "improve",
            ],
        )

    def test_enabled_keys_exist_in_config_template(self) -> None:
        """清单里的开关路径必须在默认配置模板中真实存在。"""
        for stage in PIPELINE_STAGES:
            leaf = stage.enabled_key.split(".")[-1]
            self.assertIn(
                f"{leaf}:",
                DEFAULT_PROJECT_CONFIG_YAML,
                f"阶段 {stage.key} 的开关 {leaf} 未出现在默认配置模板中",
            )

    def test_backend_slot_only_for_backend_stages(self) -> None:
        """无后端的阶段不得挂槽位；有后端的阶段槽位必须在已知四键内。"""
        known_slots = {"metadata", "translate", "afterTrans", "proofread"}
        for stage in PIPELINE_STAGES:
            if stage.backend_names:
                self.assertIn(stage.backend_slot, known_slots)
                self.assertTrue(stage.backend_slot)
            if stage.key in ("validate", "compress", "improve"):
                self.assertEqual(stage.backend_slot, "" if stage.key != "improve" else "afterTrans")

    def test_dependency_declarations(self) -> None:
        self.assertEqual(get_stage("global_prompt").depends_on, ("compress",))
        self.assertEqual(get_stage("plot_route").depends_on, ("file_meta",))
        self.assertTrue(get_stage("global_prompt").needs_compressed_text)

    def test_sample_products_map_to_stage_keys(self) -> None:
        self.assertEqual(
            SAMPLE_PRODUCTS,
            {
                "GlobalPrompt.json": "global_prompt",
                "FileMetaData.json": "file_meta",
                "PlotRouteMap.json": "plot_route",
                "BatchMetadata.json": "batch_meta",
            },
        )

    def test_display_label_is_sequential_without_decimals(self) -> None:
        """历史上「阶段 4.5」改为整序号，且编号与列表位置一致。"""
        labels = [stage_display_label(s) for s in PIPELINE_STAGES]
        self.assertEqual(labels[0], "阶段 0：输入数据校验")
        self.assertIn("阶段 5：剧情路线图", labels)
        self.assertFalse(any("4.5" in lbl for lbl in labels))


class PipelineStageSkipReasonTests(unittest.TestCase):
    def test_all_enabled_reports_no_skip(self) -> None:
        reasons = compute_skip_reasons({}, compressed_texts_present=True)
        self.assertEqual(reasons, {})

    def test_disabled_stage_reports_disabled_reason(self) -> None:
        reasons = compute_skip_reasons(
            {"file_meta": False}, compressed_texts_present=True
        )
        self.assertIn("file_meta", reasons)
        self.assertIn("enableFileMeta=false", reasons["file_meta"])
        # 依赖 file_meta 的剧情路线图应连带跳过
        self.assertIn("plot_route", reasons)
        self.assertIn("文件级元数据", reasons["plot_route"])

    def test_no_compressed_text_skips_global_prompt(self) -> None:
        reasons = compute_skip_reasons({}, compressed_texts_present=False)
        self.assertIn("global_prompt", reasons)
        self.assertNotIn("gen_dic", reasons)

    def test_payload_shape(self) -> None:
        payload = to_payload()
        self.assertEqual(len(payload["stages"]), 9)
        first = payload["stages"][0]
        for field in (
            "key",
            "label",
            "order",
            "display_label",
            "index",
            "enabled_key",
            "backend_slot",
            "depends_on",
            "backend_names",
            "sample_key",
            "needs_compressed_text",
        ):
            self.assertIn(field, first)
        self.assertEqual(first["index"], 0)
        self.assertIsInstance(STAGES_BY_KEY, dict)


if __name__ == "__main__":
    unittest.main()
