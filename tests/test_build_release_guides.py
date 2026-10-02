"""构建脚本打包 guides/ 的源码契约测试。

背景：使用指南内容（guides/）由构建脚本复制进发布包，漏带时打包版
「翻译指南」会显示空列表。此测试锁定「复制 guides/ + 缺失即构建中止」，
防止将来改动打包段落时无声丢失。

⚠️ 不 import build_release_py312（模块级有 stdio reconfigure 副作用，
详见 test_build_release_mcp.py 的说明），只做 AST/源码级断言。
"""
import ast
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_SCRIPT = os.path.join(REPO_ROOT, "build_release_py312.py")


def _load_tree() -> ast.Module:
    with open(BUILD_SCRIPT, "r", encoding="utf-8") as f:
        return ast.parse(f.read())


class GuidesPackagingContractTests(unittest.TestCase):
    def test_guides_dir_constant_defined(self) -> None:
        tree = _load_tree()
        assigns = [
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "GUIDES_DIR" for t in node.targets
            )
        ]
        self.assertTrue(assigns, "构建脚本缺少 GUIDES_DIR 常量")
        source = ast.get_source_segment(open(BUILD_SCRIPT, encoding="utf-8").read(), assigns[0])
        self.assertIn("guides", source or "", "GUIDES_DIR 应指向仓库根的 guides/ 目录")

    def test_assembly_copies_guides_to_build_dir(self) -> None:
        source = open(BUILD_SCRIPT, "r", encoding="utf-8").read()
        tree = ast.parse(source)
        # 找 assemble 阶段里的 copy_dir_filtered(GUIDES_DIR, ... / "guides")
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == "copy_dir_filtered"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Name):
                continue
            if node.args[0].id != "GUIDES_DIR":
                continue
            dest = ast.get_source_segment(source, node.args[1]) if len(node.args) > 1 else ""
            if dest and '"guides"' in dest:
                found = True
        self.assertTrue(found, "构建脚本未把 GUIDES_DIR 复制到发布包的 guides/ 目录")

    def test_missing_guides_dir_blocks_build(self) -> None:
        # fail-closed：guides/ 缺失应记入 missing 列表（缺失即构建中止），
        # 与「翻译指南显示空列表」这一用户可见故障的直接防线一致。
        source = open(BUILD_SCRIPT, "r", encoding="utf-8").read()
        tree = ast.parse(source)
        guarded = False
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and isinstance(node.test, ast.Call):
                # if GUIDES_DIR.exists(): ... else: missing.append("guides/ 目录")
                test_src = ast.get_source_segment(source, node.test) or ""
                if "GUIDES_DIR.exists()" not in test_src:
                    continue
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "append"
                        and isinstance(sub.func.value, ast.Name)
                        and sub.func.value.id == "missing"
                    ):
                        guarded = True
        self.assertTrue(guarded, "guides/ 缺失未记入 missing（应 fail-closed 中止构建）")


if __name__ == "__main__":
    unittest.main()
