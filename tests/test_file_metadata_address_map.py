"""文件级元数据「称呼映射」字段测试。

覆盖：
1. FileMetaData.address_map 默认值与构造；
2. format_file_metadata_block / _format_address_map_block 的提示词形态
   （含/不含称呼者、include_guidance 两种模式、空映射不输出）；
3. ForFileMetaData._normalize_address_map 的规整与容错
   （合法项保留、非法项丢弃、缺字段兜底、去重、非 list 回退）；
4. load_file_metadata 向后兼容（旧缓存无「称呼映射」字段）。
"""
import unittest
import tempfile
import os
import json

from GalTransl.Backend.metadata import (
    FileMetaData,
    format_file_metadata_block,
    _format_address_map_block,
    load_file_metadata,
    load_file_metadata_map,
)
from GalTransl.Backend.ForFileMetaData import ForFileMetaData


class AddressMapFieldTests(unittest.TestCase):
    """FileMetaData.address_map 字段默认与构造。"""

    def test_default_is_empty_list(self) -> None:
        md = FileMetaData()
        self.assertEqual(md.address_map, [])

    def test_accepts_list(self) -> None:
        amap = [{"被称呼者": "創", "原文": "創くん", "译文": "创君"}]
        md = FileMetaData(address_map=amap)
        self.assertEqual(md.address_map, amap)

    def test_non_list_is_coerced_to_empty(self) -> None:
        md = FileMetaData(address_map={"被称呼者": "創"})
        self.assertEqual(md.address_map, [])


class FormatAddressMapBlockTests(unittest.TestCase):
    """_format_address_map_block 的提示词形态。"""

    def _map(self):
        return [
            {"被称呼者": "創", "称呼者": "華恋", "原文": "創くん", "译文": "创先生"},
            {"被称呼者": "創", "称呼者": "凛音", "原文": "創くん", "译文": "创君"},
            {"被称呼者": "凛音", "原文": "凛音", "译文": "凛音"},
        ]

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(_format_address_map_block([]), "")
        self.assertEqual(_format_address_map_block([{"坏": "数据"}]), "")

    def test_with_caller_and_without_caller(self) -> None:
        out = _format_address_map_block(self._map())
        self.assertIn("創（由華恋称呼）：原文「創くん」→ 译文「创先生」", out)
        self.assertIn("創（由凛音称呼）：原文「創くん」→ 译文「创君」", out)
        self.assertIn("凛音：原文「凛音」→ 译文「凛音」", out)

    def test_header_present(self) -> None:
        out = _format_address_map_block(self._map())
        self.assertTrue(out.startswith("称呼映射:\n"))
        self.assertTrue(out.endswith("\n"))


class FormatFileMetadataBlockAddressMapTests(unittest.TestCase):
    """format_file_metadata_block 集成称呼映射与指导语。"""

    def test_address_map_injected(self) -> None:
        md = FileMetaData(
            id="04.txt.json",
            character=["创", "凛音"],
            address_map=[
                {"被称呼者": "創", "称呼者": "凛音", "原文": "創くん", "译文": "创君"}
            ],
        )
        out = format_file_metadata_block(md)
        self.assertIn("称呼映射:\n- 創（由凛音称呼）：原文「創くん」→ 译文「创君」", out)

    def test_guidance_added_when_address_map_present(self) -> None:
        md = FileMetaData(address_map=[{"原文": "創くん", "译文": "创君"}])
        out = format_file_metadata_block(md, include_guidance=True)
        self.assertIn("保持人物称谓：同一角色被不同人称呼时", out)

    def test_no_guidance_when_include_guidance_false(self) -> None:
        md = FileMetaData(address_map=[{"原文": "創くん", "译文": "创君"}])
        out = format_file_metadata_block(md, include_guidance=False)
        self.assertIn("称呼映射:", out)
        self.assertNotIn("保持人物称谓", out)

    def test_empty_address_map_omits_block(self) -> None:
        md = FileMetaData(id="04.txt.json", character=["创"])
        out = format_file_metadata_block(md)
        self.assertNotIn("称呼映射", out)


class NormalizeAddressMapTests(unittest.TestCase):
    """ForFileMetaData._normalize_address_map 规整与容错。"""

    def test_valid_items_kept_with_caller(self) -> None:
        raw = [
            {"被称呼者": "創", "称呼者": "華恋", "原文": "創くん", "译文": "创先生"},
            {"被称呼者": "創", "原文": "凛音", "译文": "凛音"},
        ]
        out = ForFileMetaData._normalize_address_map(raw, "04.txt.json")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["称呼者"], "華恋")
        self.assertNotIn("称呼者", out[1])

    def test_missing_subject_falls_back_to_src(self) -> None:
        raw = [{"原文": "創くん", "译文": "创君"}]
        out = ForFileMetaData._normalize_address_map(raw, "04.txt.json")
        self.assertEqual(out[0]["被称呼者"], "創くん")

    def test_missing_src_or_dst_dropped(self) -> None:
        raw = [
            {"原文": "", "译文": "创君"},
            {"原文": "創くん", "译文": ""},
            {"原文": "創くん", "译文": "创君"},
        ]
        out = ForFileMetaData._normalize_address_map(raw, "04.txt.json")
        self.assertEqual(len(out), 1)

    def test_non_dict_item_dropped(self) -> None:
        raw = ["創くん", {"原文": "凛音", "译文": "凛音"}]
        out = ForFileMetaData._normalize_address_map(raw, "04.txt.json")
        self.assertEqual(len(out), 1)

    def test_deduplicates_same_triple(self) -> None:
        raw = [
            {"被称呼者": "創", "称呼者": "凛音", "原文": "創くん", "译文": "创君"},
            {"被称呼者": "創", "称呼者": "凛音", "原文": "創くん", "译文": "创君"},
        ]
        out = ForFileMetaData._normalize_address_map(raw, "04.txt.json")
        self.assertEqual(len(out), 1)

    def test_keeps_same_src_with_different_dst(self) -> None:
        # P2 回归：同一 (被称呼者, 称呼者, 原文) 给出不同译文时，两条都应保留，
        # 不静默丢弃更优译文。
        raw = [
            {"被称呼者": "創", "称呼者": "凛音", "原文": "創くん", "译文": "创君"},
            {"被称呼者": "創", "称呼者": "凛音", "原文": "創くん", "译文": "创同学"},
        ]
        out = ForFileMetaData._normalize_address_map(raw, "04.txt.json")
        self.assertEqual(len(out), 2)
        self.assertEqual([x["译文"] for x in out], ["创君", "创同学"])

    def test_preserves_input_order_when_deduplicating(self) -> None:
        # 顺序稳定化：去重保留首次出现顺序，重复项不改变整体顺序。
        raw = [
            {"被称呼者": "創", "称呼者": "凛音", "原文": "創くん", "译文": "创君"},
            {"被称呼者": "凛音", "原文": "凛音", "译文": "凛音"},
            {"被称呼者": "創", "称呼者": "凛音", "原文": "創くん", "译文": "创君"},
            {"被称呼者": "華恋", "原文": "華恋", "译文": "华恋"},
        ]
        out = ForFileMetaData._normalize_address_map(raw, "04.txt.json")
        self.assertEqual(len(out), 3)
        self.assertEqual(
            [x["被称呼者"] for x in out], ["創", "凛音", "華恋"]
        )

    def test_non_list_returns_empty(self) -> None:
        self.assertEqual(ForFileMetaData._normalize_address_map(None, "04.txt.json"), [])
        self.assertEqual(ForFileMetaData._normalize_address_map({"a": 1}, "04.txt.json"), [])

    def test_normalize_meta_respects_enable_flag(self) -> None:
        obj = {"称呼映射": [{"原文": "創くん", "译文": "创君"}]}
        on = ForFileMetaData._normalize_meta(obj, "04.txt.json", enable_address_map=True)
        off = ForFileMetaData._normalize_meta(obj, "04.txt.json", enable_address_map=False)
        self.assertEqual(len(on["称呼映射"]), 1)
        self.assertEqual(off["称呼映射"], [])


class LoadFileMetadataAddressMapCompatTests(unittest.TestCase):
    """load_file_metadata 对「称呼映射」的读取、开关过滤与旧缓存兼容。"""

    def _make_proj(self, data: dict) -> object:
        from unittest.mock import patch

        proj = patch("GalTransl.Backend.metadata.CProjectConfig").start()
        proj.getCachePath.return_value = self._write_meta(data)
        self.addCleanup(patch.stopall)
        return proj

    def _write_meta(self, data: dict) -> str:
        from GalTransl import PASS1_CACHE_DIR

        tmp = tempfile.mkdtemp()
        cache_dir = os.path.join(tmp, PASS1_CACHE_DIR)
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, "04.txt.json.meta.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return tmp

    def test_loads_address_map_when_switch_on(self) -> None:
        proj = self._make_proj(
            {"id": "04.txt.json", "角色": ["创"], "称呼映射": [{"原文": "創くん", "译文": "创君"}]}
        )
        proj.getKey.return_value = True
        md = load_file_metadata(proj, "04.txt.json")
        self.assertEqual(len(md.address_map), 1)

    def test_filters_address_map_when_switch_off(self) -> None:
        # 主风险回归：开关关闭时，即使缓存含称呼映射，读路径也应过滤为空
        proj = self._make_proj(
            {"id": "04.txt.json", "角色": ["创"], "称呼映射": [{"原文": "創くん", "译文": "创君"}]}
        )
        proj.getKey.return_value = False
        md = load_file_metadata(proj, "04.txt.json")
        self.assertEqual(md.address_map, [])
        # 显式传值覆盖配置：开关关闭但调用方显式开启
        proj.getKey.return_value = False
        md2 = load_file_metadata(proj, "04.txt.json", enable_address_map=True)
        self.assertEqual(len(md2.address_map), 1)
        # 开关开启但显式关闭
        proj.getKey.return_value = True
        md3 = load_file_metadata(proj, "04.txt.json", enable_address_map=False)
        self.assertEqual(md3.address_map, [])

    def test_string_switch_values_coerced_consistently(self) -> None:
        # P1 回归：读路径须用 coerce_bool，字符串 "false"/"0"/"no" 应判为关闭。
        proj = self._make_proj(
            {"id": "04.txt.json", "角色": ["创"], "称呼映射": [{"原文": "創くん", "译文": "创君"}]}
        )
        for val in ("false", "0", "no", "off"):
            proj.getKey.return_value = val
            self.assertEqual(load_file_metadata(proj, "04.txt.json").address_map, [],
                             f"配置值 {val!r} 应关闭称呼映射")
        proj.getKey.return_value = "true"
        self.assertEqual(len(load_file_metadata(proj, "04.txt.json").address_map), 1)

    def test_missing_field_backward_compat(self) -> None:
        proj = self._make_proj({"id": "04.txt.json", "角色": ["创"]})
        proj.getKey.return_value = True
        md = load_file_metadata(proj, "04.txt.json")
        self.assertEqual(md.address_map, [])


class LoadFileMetadataMapAddressMapSwitchTests(unittest.TestCase):
    """load_file_metadata_map 对「称呼映射」开关的过滤。"""

    def _write_meta(self, data: dict, fname: str = "04.txt.json") -> str:
        from GalTransl import PASS1_CACHE_DIR
        from unittest.mock import patch

        tmp = tempfile.mkdtemp()
        cache_dir = os.path.join(tmp, PASS1_CACHE_DIR)
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, f"{fname}.meta.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return tmp

    def test_filter_when_switch_off(self) -> None:
        from unittest.mock import patch

        proj = patch("GalTransl.Backend.metadata.CProjectConfig").start()
        proj.getCachePath.return_value = self._write_meta(
            {"id": "04.txt.json", "角色": ["创"], "称呼映射": [{"原文": "創くん", "译文": "创君"}]}
        )
        proj.getKey.return_value = False
        self.addCleanup(patch.stopall)
        result = load_file_metadata_map(proj)
        self.assertEqual(len(result), 1)
        self.assertEqual(result["04.txt.json"].address_map, [])

    def test_keeps_when_switch_on(self) -> None:
        from unittest.mock import patch

        proj = patch("GalTransl.Backend.metadata.CProjectConfig").start()
        proj.getCachePath.return_value = self._write_meta(
            {"id": "04.txt.json", "角色": ["创"], "称呼映射": [{"原文": "創くん", "译文": "创君"}]}
        )
        proj.getKey.return_value = True
        self.addCleanup(patch.stopall)
        result = load_file_metadata_map(proj)
        self.assertEqual(len(result["04.txt.json"].address_map), 1)


if __name__ == "__main__":
    unittest.main()
