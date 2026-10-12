"""新建项目默认配置契约测试。

锁定 DefaultProjectConfig 模板的三组默认值，防止无意漂移：
1. gpt.afterTranslation 默认后处理清单（与前端 desktop/src/lib/afterTranslation.ts
   的 createDefaultAfterTranslation 保持一致，双端各自有测试锁定）；
2. gpt.swapFixToCurrent 默认开启；
3. problemAnalyze.problemList 默认启用清单须覆盖 fix 默认修复类型（fix 轮消费
   find_problems 按 problemList 检测出的问题，类型未启用则无问题可修）。
"""
import unittest

from yaml import safe_load

from GalTransl.DefaultProjectConfig import DEFAULT_PROJECT_CONFIG_YAML


class DefaultProjectConfigContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = safe_load(DEFAULT_PROJECT_CONFIG_YAML)

    def test_after_translation_default_order(self) -> None:
        common = self.config["common"]
        order = common["gpt.afterTranslation"]
        self.assertEqual(len(order), 3)
        self.assertEqual(
            order[0],
            {
                "fix": {
                    "types": [
                        "残留日文",
                        "换行位置异常",
                        "多加换行",
                        "频繁换行",
                        "单句过长",
                    ],
                    "injectProblem": True,
                }
            },
        )
        self.assertEqual(order[1], "semcheck")
        self.assertEqual(order[2], "tonecheck")

    def test_swap_fix_to_current_default_true(self) -> None:
        self.assertIs(self.config["common"]["gpt.swapFixToCurrent"], True)

    def test_problem_list_covers_default_fix_types(self) -> None:
        problem_list = self.config["problemAnalyze"]["problemList"]
        self.assertIsInstance(problem_list, list)
        fix_types = self.config["common"]["gpt.afterTranslation"][0]["fix"]["types"]
        for fix_type in fix_types:
            self.assertIn(
                fix_type,
                problem_list,
                f"fix 默认修复类型 {fix_type} 未在默认 problemList 中启用，修复轮将无问题可修",
            )

    def test_problem_list_disabled_defaults(self) -> None:
        problem_list = self.config["problemAnalyze"]["problemList"]
        for disabled in (
            "标点错漏",
            "缺控制符",
            "独白男他",
            "丢失换行",
            "引入英文",
            "比日文长严格",
        ):
            self.assertNotIn(disabled, problem_list)

    def test_problem_list_enabled_defaults(self) -> None:
        problem_list = self.config["problemAnalyze"]["problemList"]
        self.assertEqual(
            problem_list,
            [
                "词频过高",
                "残留日文",
                "多加换行",
                "比日文长",
                "字典使用",
                "语言不通",
                "单句过长",
                "换行位置异常",
                "频繁换行",
                "疑似错误",
                "词语色彩不一致",
            ],
        )


if __name__ == "__main__":
    unittest.main()
