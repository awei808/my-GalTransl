"""ForGlobalPrompt._build_glossary_text 的 h/非h 场景过滤测试。

覆盖：
  1. 项目专属 h 字典词条被过滤，不注入全局分析；
  2. 无后缀项目字典（归非 h）保留注入，不过滤误伤；
  3. 无项目专属字典时返回空串（既有行为不回归）。

与元数据轮（ForFileMetaData/ForBatchMetaData）"仅注入非 h 字典"口径一致。
"""

import os
import tempfile
import unittest
from unittest import mock

from GalTransl.Backend.ForGlobalPrompt import ForGlobalPrompt


def _make_inst(dict_files: dict):
    """构造 ForGlobalPrompt 实例（绕过 __init__）：pj_config 返回伪字典配置。

    Args:
        dict_files: {文件名: 文件内容} 的项目专属字典文件集合。
    """
    inst = ForGlobalPrompt.__new__(ForGlobalPrompt)
    tmp_dir = tempfile.mkdtemp(prefix="gp_glossary_")
    for name, content in dict_files.items():
        with open(os.path.join(tmp_dir, name), "w", encoding="utf-8") as f:
            f.write(content)
    # 项目专属字典统一带 (project_dir) 前缀（与 _build_glossary_text 的筛选口径一致）
    dict_cfg = {
        "gpt.dict": [f"(project_dir){name}" for name in dict_files],
        "defaultDictFolder": tmp_dir,
    }
    fake_cfg = mock.MagicMock()
    fake_cfg.getDictCfgSection.return_value = dict_cfg
    fake_cfg.getProjectDir.return_value = tmp_dir
    inst.pj_config = fake_cfg
    return inst, tmp_dir


class ForGlobalPromptGlossarySceneTests(unittest.TestCase):
    """全局分析术语表：h 词条被过滤、无后缀项目字典保留"""

    def _cleanup(self, tmp_dir: str, names: list) -> None:
        for name in names:
            p = os.path.join(tmp_dir, name)
            if os.path.exists(p):
                os.unlink(p)
        os.rmdir(tmp_dir)

    def test_h_dict_entry_is_filtered(self) -> None:
        inst, tmp_dir = _make_inst(
            {
                "项目GPT字典_h.txt": "まんこ|小穴\n",
                "项目GPT字典.txt": "華恋|华恋\n",
            }
        )
        try:
            out = inst._build_glossary_text()
            self.assertIn("# Glossary", out)
            self.assertIn("華恋", out)
            self.assertIn("华恋", out)
            self.assertNotIn("小穴", out)
        finally:
            self._cleanup(tmp_dir, ["项目GPT字典_h.txt", "项目GPT字典.txt"])

    def test_nonsuffix_project_dict_kept(self) -> None:
        # 无后缀项目字典归非 h 场景，全量保留
        inst, tmp_dir = _make_inst(
            {
                "项目GPT字典.txt": "ツクモ|创君\n",
                "项目GPT字典-生成.txt": "魔法|まほう\n",
            }
        )
        try:
            out = inst._build_glossary_text()
            self.assertIn("创君", out)
            self.assertIn("まほう", out)
        finally:
            self._cleanup(tmp_dir, ["项目GPT字典.txt", "项目GPT字典-生成.txt"])

    def test_all_h_dict_yields_header_only(self) -> None:
        # 只有 h 项目字典时输出仅表头，不崩溃
        inst, tmp_dir = _make_inst(
            {"项目GPT字典_h.txt": "オチンポ|肉棒\n"}
        )
        try:
            out = inst._build_glossary_text()
            self.assertNotIn("肉棒", out)
            self.assertIn("# Glossary", out)
        finally:
            self._cleanup(tmp_dir, ["项目GPT字典_h.txt"])

    def test_no_project_dict_returns_empty(self) -> None:
        # 无项目专属字典：既有行为——返回空串
        inst = ForGlobalPrompt.__new__(ForGlobalPrompt)
        fake_cfg = mock.MagicMock()
        fake_cfg.getDictCfgSection.return_value = {
            "gpt.dict": ["GPT字典_非h.txt"],  # 公共字典，不注入全局分析
            "defaultDictFolder": tempfile.gettempdir(),
        }
        inst.pj_config = fake_cfg
        self.assertEqual(inst._build_glossary_text(), "")


if __name__ == "__main__":
    unittest.main()
