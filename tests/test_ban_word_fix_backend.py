"""禁用词修复后端 ForBanWordFix 单元测试。

ForBanWordFix 与 ForJPResidue 完全同构（仅覆盖类属性与提示词），不重写流程方法，
故本测试聚焦其类属性覆盖带来的实质差异：
1. 筛选：经父类 _has_target_problem 命中「用词不当」（_problem_types 覆盖），
   且 CProblemType 为 IntEnum、problem 文案用中文成员名拼接，必须按 .name 口径匹配；
2. 输入注入：_inject_problem=True 使基类 _build_input_jsonlines 携带 problem 字段
   （命中禁用词随原文/译文一并给模型）。
解析/对话分桶/错误恢复等逻辑复用 ForJPResidue，已在其测试中覆盖，不重复测。
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from GalTransl.CSentense import CSentense
from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.Problem import CProblemType
from GalTransl.Backend.ForBanWordFix import ForBanWordFix


def _make_config(tmp_dir: str) -> CProjectConfig:
    """构造最小可用的 CProjectConfig（写临时 config.yaml，避免依赖现有 fixture）。

    Args:
        tmp_dir: 临时项目目录。

    Returns:
        CProjectConfig 实例。
    """
    cfg_path = os.path.join(tmp_dir, "config.yaml")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(
            "internals:\n"
            "  gpt:\n"
            "    numPerRequestBetter: 20\n"
            "    swapFixToCurrent: false\n"
            "externals:\n"
            "  sourceLang: ja\n"
            "  targetLang: zh\n"
            "backendSpecific:\n"
            "  OpenAI-Compatible:\n"
            "    apiTimeout: 300\n"
            "common:\n"
            "  gpt:\n"
            "    change_prompt: no\n"
        )
    return CProjectConfig(tmp_dir, "config.yaml")


def _make_tran(index: int, problem: str, pre_dst: str) -> CSentense:
    tran = CSentense(pre_src="原文", index=index)
    tran.problem = problem
    tran.pre_dst = pre_dst
    tran.alt_dst = ""
    return tran


class BanWordFixFilterTests(unittest.TestCase):
    """筛选口径：经父类 _has_target_problem + 子类 _problem_types，仅命中「用词不当」。

    子类不重写 _has_target_problem，故此处调用即父类实现，命中结果由 _problem_types
    覆盖（[CProblemType.用词不当]）决定。
    """

    def _backend(self) -> ForBanWordFix:
        # 绕过 __init__（避免构造完整 token/proxy 基础设施），仅测纯逻辑方法
        return object.__new__(ForBanWordFix)

    def test_hits_word_inappropriate_with_valid_dst(self) -> None:
        backend = self._backend()
        tran = _make_tran(1, "用词不当：模型师、造型师", "她是模型师。")
        self.assertTrue(backend._has_target_problem(tran))

    def test_skips_no_problem(self) -> None:
        backend = self._backend()
        tran = _make_tran(2, "", "她是造型师。")
        self.assertFalse(backend._has_target_problem(tran))

    def test_skips_other_problem_type(self) -> None:
        backend = self._backend()
        tran = _make_tran(3, "残留日文：です", "她是造型师。")
        self.assertFalse(backend._has_target_problem(tran))

    def test_skips_failed_dst(self) -> None:
        backend = self._backend()
        tran = _make_tran(4, "用词不当：模型师", "(Failed)")
        self.assertFalse(backend._has_target_problem(tran))

    def test_skips_empty_dst(self) -> None:
        backend = self._backend()
        tran = _make_tran(5, "用词不当：模型师", "")
        self.assertFalse(backend._has_target_problem(tran))


class BanWordFixInputInjectionTests(unittest.TestCase):
    """差异点验证：输入 JSONL 通过 problem_types 注入 problem 字段。

    batch_translate 的 alt_dst 写入/解析/对话分桶逻辑复用 ForJPResidue（已测），
    本类仅验证 ForBanWordFix 相对 JP 的唯一实质差异：输入携带 problem（命中禁用词）。
    """

    def _backend(self) -> ForBanWordFix:
        # 轻量构造：仅注入 _build_input_jsonlines 所需的最小依赖
        backend = object.__new__(ForBanWordFix)
        backend.pj_config = _make_config(tempfile.mkdtemp(prefix="bwf_"))
        backend.gpt_dic = None
        return backend

    def test_input_injects_problem_field(self) -> None:
        backend = self._backend()
        tran = _make_tran(1, "用词不当：模型师", "她是模型师。")
        _input_list, _sig_list, _n_symbol, input_src = backend._build_input_jsonlines(
            [tran],
            proofread=True,
            filename="dummy.txt",
            problem_types=[CProblemType.用词不当],
            include_src=True,
        )
        # 输入应携带原文(src)、当前译文(dst)与问题(problem)
        self.assertIn('"src"', input_src)
        self.assertIn('"dst"', input_src)
        self.assertIn('"problem"', input_src)
        self.assertIn("用词不当：模型师", input_src)

    def test_input_omits_problem_when_no_problem_type(self) -> None:
        backend = self._backend()
        tran = _make_tran(1, "", "普通译文。")
        _input_list, _sig_list, _n_symbol, input_src = backend._build_input_jsonlines(
            [tran],
            proofread=True,
            filename="dummy.txt",
            include_src=True,
        )
        # 不传 problem_types 时不应注入 problem 字段
        self.assertNotIn('"problem"', input_src)


if __name__ == "__main__":
    unittest.main()
