"""ForGlobalPrompt 人名对照表注入与角色名校正测试。

覆盖：
  1. 项目人名替换表（csv 新旧列名 / xlsx）被格式化为 [NameTable] 注入块；
  2. 无表、表内无有效译名、开关关闭时不注入（返回空串）；
  3. _build_prompt_request 对 [NameTable] 的替换/清除；
  4. 角色名对照校正：命中人名表 / 命中 GPT 字典 / 未命中 / 已是译名。
"""

import os
import tempfile
import types
import unittest
from typing import Optional
from unittest import mock

from GalTransl.Backend.ForGlobalPrompt import ForGlobalPrompt


def _write_name_csv(
    tmp_dir: str,
    rows: list,
    header: tuple = ("SRC_Name", "DST_Name", "Count"),
) -> str:
    """在项目根写一个 name替换表.csv（utf-8-sig，与实际导出口径一致）。"""
    path = os.path.join(tmp_dir, "name替换表.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        f.write(",".join(header) + "\n")
        for row in rows:
            f.write(",".join(row) + "\n")
    return path


def _make_inst(
    tmp_dir: str, dict_cfg: Optional[dict] = None, inject: bool = True
) -> ForGlobalPrompt:
    """构造 ForGlobalPrompt 实例（绕过 __init__），pj_config 为 mock 桩。"""
    inst = ForGlobalPrompt.__new__(ForGlobalPrompt)
    fake_cfg = mock.MagicMock()
    fake_cfg.getProjectDir.return_value = tmp_dir
    fake_cfg.getDictCfgSection.return_value = dict_cfg or {}
    fake_cfg.translation_guideline = ""
    fake_cfg.getKey.return_value = True
    inst.pj_config = fake_cfg
    inst._inject_name_table = inject
    inst._inject_guideline = False
    inst.trans_prompt = (
        "E=[ExternalInfo]\nN=[NameTable]\nG=[Glossary]\n"
        "GL=[translation_guideline]\nI=[Input]\nS=[SourceLang] T=[TargetLang]"
    )
    inst.source_lang = "日语"
    inst.target_lang = "简体中文"
    return inst


class NameTableTextTests(unittest.TestCase):
    """_build_name_table_text：人名表读取与注入块格式化。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="gp_nametable_")

    def tearDown(self) -> None:
        for name in os.listdir(self.tmp_dir):
            os.unlink(os.path.join(self.tmp_dir, name))
        os.rmdir(self.tmp_dir)

    def test_csv_new_columns_injected(self) -> None:
        _write_name_csv(self.tmp_dir, [("浅倉", "浅仓", "10")])
        inst = _make_inst(self.tmp_dir)
        out = inst._build_name_table_text()
        self.assertIn("# 人名对照表", out)
        self.assertIn("| 浅倉 | 浅仓 |", out)

    def test_csv_old_columns_injected(self) -> None:
        _write_name_csv(
            self.tmp_dir, [("宮村", "宫村", "3")],
            header=("JP_Name", "CN_Name", "Count"),
        )
        inst = _make_inst(self.tmp_dir)
        self.assertIn("| 宮村 | 宫村 |", inst._build_name_table_text())

    def test_xlsx_old_columns_injected(self) -> None:
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["JP_Name", "CN_Name", "Count"])
        ws.append(["春", "小春", "5"])
        wb.save(os.path.join(self.tmp_dir, "name替换表.xlsx"))
        inst = _make_inst(self.tmp_dir)
        self.assertIn("| 春 | 小春 |", inst._build_name_table_text())

    def test_missing_table_returns_empty(self) -> None:
        inst = _make_inst(self.tmp_dir)
        self.assertEqual(inst._build_name_table_text(), "")

    def test_all_dst_empty_returns_empty(self) -> None:
        _write_name_csv(self.tmp_dir, [("浅倉", "", "10")])
        inst = _make_inst(self.tmp_dir)
        self.assertEqual(inst._build_name_table_text(), "")

    def test_toggle_off_returns_empty(self) -> None:
        _write_name_csv(self.tmp_dir, [("浅倉", "浅仓", "10")])
        inst = _make_inst(self.tmp_dir, inject=False)
        self.assertEqual(inst._build_name_table_text(), "")


class NameTablePlaceholderTests(unittest.TestCase):
    """_build_prompt_request：[NameTable] 占位符替换与清除。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="gp_nametable_ph_")

    def tearDown(self) -> None:
        for name in os.listdir(self.tmp_dir):
            os.unlink(os.path.join(self.tmp_dir, name))
        os.rmdir(self.tmp_dir)

    def test_placeholder_replaced_with_table(self) -> None:
        _write_name_csv(self.tmp_dir, [("浅倉", "浅仓", "10")])
        inst = _make_inst(self.tmp_dir)
        out = inst._build_prompt_request("正文", "")
        self.assertIn("| 浅倉 | 浅仓 |", out)
        self.assertNotIn("[NameTable]", out)

    def test_placeholder_cleared_without_table(self) -> None:
        inst = _make_inst(self.tmp_dir)
        out = inst._build_prompt_request("正文", "")
        self.assertNotIn("[NameTable]", out)
        self.assertIn("N=\n", out)

    def test_real_template_contains_placeholder(self) -> None:
        from GalTransl.Backend.Prompts import FORGLOBAL_PROMPT

        self.assertIn("[NameTable]", FORGLOBAL_PROMPT)


class CorrectCharacterNamesTests(unittest.TestCase):
    """_correct_character_names：角色名对照校正。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="gp_nametable_fix_")

    def tearDown(self) -> None:
        for name in os.listdir(self.tmp_dir):
            os.unlink(os.path.join(self.tmp_dir, name))
        os.rmdir(self.tmp_dir)

    def test_hit_name_table(self) -> None:
        _write_name_csv(self.tmp_dir, [("浅倉", "浅仓", "10")])
        inst = _make_inst(self.tmp_dir)
        meta = {
            "角色列表": [
                {"名称": "浅倉"},
                {"名称": "浅仓"},
                {"名称": "未知子"},
            ]
        }
        self.assertEqual(inst._correct_character_names(meta), 1)
        names = [ch["名称"] for ch in meta["角色列表"]]
        self.assertEqual(names, ["浅仓", "浅仓", "未知子"])

    def test_hit_gpt_dict(self) -> None:
        dict_file = os.path.join(self.tmp_dir, "项目GPT字典.txt")
        with open(dict_file, "w", encoding="utf-8") as f:
            f.write("宮村|宫村\n")
        dict_cfg = {
            "gpt.dict": ["(project_dir)项目GPT字典.txt"],
            "defaultDictFolder": self.tmp_dir,
        }
        inst = _make_inst(self.tmp_dir, dict_cfg=dict_cfg)
        meta = {"角色列表": [{"名称": "宮村"}]}
        self.assertEqual(inst._correct_character_names(meta), 1)
        self.assertEqual(meta["角色列表"][0]["名称"], "宫村")

    def test_empty_characters(self) -> None:
        inst = _make_inst(self.tmp_dir)
        self.assertEqual(inst._correct_character_names({"角色列表": []}), 0)
        self.assertEqual(inst._correct_character_names({}), 0)

    def test_duck_typed_config_skips_correction(self) -> None:
        # 不完整配置桩（无 getProjectDir/getDictCfgSection）静默跳过两个来源
        inst = ForGlobalPrompt.__new__(ForGlobalPrompt)
        inst.pj_config = types.SimpleNamespace()
        meta = {"角色列表": [{"名称": "浅倉"}]}
        self.assertEqual(inst._correct_character_names(meta), 0)
        self.assertEqual(meta["角色列表"][0]["名称"], "浅倉")

    def test_name_table_load_error_skips_correction(self) -> None:
        # 人名表加载异常 → warning 降级不抛错，校正跳过
        _write_name_csv(self.tmp_dir, [("浅倉", "浅仓", "10")])
        inst = _make_inst(self.tmp_dir)
        with mock.patch(
            "GalTransl.Backend.ForGlobalPrompt.load_name_table_dict",
            side_effect=OSError("boom"),
        ):
            meta = {"角色列表": [{"名称": "浅倉"}]}
            self.assertEqual(inst._correct_character_names(meta), 0)
            self.assertEqual(meta["角色列表"][0]["名称"], "浅倉")

    def test_gpt_dict_h_entry_not_used(self) -> None:
        # h 字典词条不参与校正（与术语表注入口径一致）
        h_file = os.path.join(self.tmp_dir, "项目GPT字典_h.txt")
        with open(h_file, "w", encoding="utf-8") as f:
            f.write("浅倉|小仓\n")
        dict_cfg = {
            "gpt.dict": ["(project_dir)项目GPT字典_h.txt"],
            "defaultDictFolder": self.tmp_dir,
        }
        inst = _make_inst(self.tmp_dir, dict_cfg=dict_cfg)
        meta = {"角色列表": [{"名称": "浅倉"}]}
        self.assertEqual(inst._correct_character_names(meta), 0)
        self.assertEqual(meta["角色列表"][0]["名称"], "浅倉")


if __name__ == "__main__":
    unittest.main()
