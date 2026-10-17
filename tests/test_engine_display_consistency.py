"""翻译后端选择器的展示元数据覆盖测试（后端清单 → 前端表格）。

背景：/api/translators 的清单唯一真源是 GalTransl/__init__.py 的 TRANSLATOR_SUPPORTED，
翻译控制台后端下拉的中文短名/短说明/分组表在 desktop/src/lib/engineDisplay.ts。
两者靠人工同步，本测试从后端侧反向核对，新增引擎忘记补表时直接失败，
避免重演「只有部分引擎有中文说明、其余显示一长段后端描述」的旧问题。

未收录引擎在前端会回退（短名=引擎名、说明=后端描述、分组=其它），不会崩，
所以这里锁的是「覆盖度」而非可用性。
"""
import re
import unittest
from pathlib import Path

from GalTransl import TRANSLATOR_SUPPORTED
from GalTransl.UtilityEngines import UTILITY_ENGINES

# 与 GalTransl/server_handlers_root.py 的 /api/translators 保持同口径：
# 隐藏 _hidden_translators 中显式列出的这些名字 + 全部工具引擎
_HIDDEN_EXTRA = ("show-plugs", "dump-name", "rebuildr", "rebuilda")

_ENGINE_DISPLAY_TS = (
    Path(__file__).resolve().parents[1] / "desktop" / "src" / "lib" / "engineDisplay.ts"
)


def _visible_engines() -> list[str]:
    """与 /api/translators 返回的引擎名清单同口径（顺序沿用后端字典序）。"""
    hidden = set(_HIDDEN_EXTRA) | set(UTILITY_ENGINES)
    return [name for name in TRANSLATOR_SUPPORTED if name not in hidden]


def _table_block(text: str, marker: str) -> str:
    """截取 TS 里某个常量表的内容块（从声明到最近的 ``};``）。"""
    start = text.index(marker)
    end = text.index("\n};", start)
    return text[start:end]


def _declares(block: str, name: str) -> bool:
    """表内是否登记了该键（兼容带引号与裸键两种写法）。"""
    return f'"{name}"' in block or re.search(rf"^\s*{re.escape(name)}:", block, re.M) is not None


class EngineDisplayCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = _ENGINE_DISPLAY_TS.read_text(encoding="utf-8")
        cls.engines = _visible_engines()

    def test_engine_list_parsed(self) -> None:
        """口径自检：解析到的清单非空，否则下面的断言会变成空转。"""
        self.assertGreater(len(self.engines), 10)
        self.assertNotIn("rebuildr", self.engines)
        self.assertIn("ForGal-json-translate", self.engines)

    def test_every_engine_has_label(self) -> None:
        block = _table_block(self.text, "export const ENGINE_LABELS")
        missing = [n for n in self.engines if not _declares(block, n)]
        self.assertEqual(
            missing,
            [],
            f"这些后端缺少中文短名，请补 desktop/src/lib/engineDisplay.ts 的 ENGINE_LABELS: {missing}",
        )

    def test_every_engine_has_hint(self) -> None:
        block = _table_block(self.text, "export const ENGINE_HINTS")
        missing = [n for n in self.engines if not _declares(block, n)]
        self.assertEqual(
            missing,
            [],
            f"这些后端缺少短说明，请补 desktop/src/lib/engineDisplay.ts 的 ENGINE_HINTS: {missing}",
        )

    def test_every_engine_has_group(self) -> None:
        block = _table_block(self.text, "const ENGINE_GROUPS")
        missing = [n for n in self.engines if not _declares(block, n)]
        self.assertEqual(
            missing,
            [],
            f"这些后端未登记分组（会落到「其它」组），请补 ENGINE_GROUPS: {missing}",
        )

    def test_every_engine_in_order_lists(self) -> None:
        """组内顺序表须覆盖全部引擎，否则未登记的会排在组尾而非按流水线顺序。

        ENGINE_ORDER 的值是字符串数组而非对象键，这里按引号匹配。
        """
        block = _table_block(self.text, "const ENGINE_ORDER")
        missing = [n for n in self.engines if f'"{n}"' not in block]
        self.assertEqual(
            missing,
            [],
            f"这些后端未登记组内顺序，请补 desktop/src/lib/engineDisplay.ts 的 ENGINE_ORDER: {missing}",
        )

    def test_no_stale_entries(self) -> None:
        """反向核对：前端各表不应残留后端已下线的引擎（单向核对会漏掉这类死条目）。"""
        checks = (
            ("export const ENGINE_LABELS", "ENGINE_LABELS"),
            ("export const ENGINE_HINTS", "ENGINE_HINTS"),
            ("const ENGINE_GROUPS", "ENGINE_GROUPS"),
        )
        for marker, table in checks:
            with self.subTest(table=table):
                block = _table_block(self.text, marker)
                keys = set(re.findall(r'^\s*"?([A-Za-z][A-Za-z0-9_\-]*)"?:\s', block, re.M))
                stale = sorted(keys - set(self.engines))
                self.assertEqual(
                    stale,
                    [],
                    f"这些条目的引擎已不在 /api/translators 清单里，请从 {table} 删除: {stale}",
                )


if __name__ == "__main__":
    unittest.main()
