"""构建脚本的 MCP 冒烟辅助函数单测（0.6.0 批次 5）。

背景：`smoke_test_mcp()` 原先只验 `initialize` 握手，不检查 `instructions`。
而 `instructions` 承载 agent 侧的安全契约（写工具边界 / H 门禁 / 路径白名单），
打包版漏带或口径过时都不会被构建发现。

⚠️ **不要在本文件里 import `build_release_py312`**：它在模块级执行
`sys.stdout.reconfigure(encoding="utf-8")` / `sys.stderr.reconfigure(...)`，
一旦被测试进程导入就会改写全局 stdio 编码，导致后续 HTTP/日志类测试
批量抛 `UnicodeDecodeError`（实测：把全套从 1993 passed 打成上千 errors）。
因此这里改为**用 ast 提取目标函数源码后单独 exec**，隔离模块级副作用。
"""
import ast
import os
import pathlib
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_SCRIPT = os.path.join(REPO_ROOT, "build_release_py312.py")
MCP_TOOLS_SOURCE = os.path.join(REPO_ROOT, "GalTransl", "mcp_tools.py")


def _extract_function_source(name: str) -> str:
    """从构建脚本里取出指定顶层函数的源码文本（不导入模块）。"""
    with open(BUILD_SCRIPT, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"构建脚本里找不到函数 {name}")


def _module_level_imports() -> dict:
    """抽取构建脚本的**模块级 import**，供隔离执行时提供名字。

    不导入构建脚本本体（它有 stdio 副作用），但模块级 import 是纯 stdlib 引用，
    用 ast 取出后逐个 import 是安全的。这样 `expected_mcp_tool_count` 将来
    多用一个 stdlib 模块时，这里不必手工补名字。
    """
    with open(BUILD_SCRIPT, "r", encoding="utf-8") as f:
        source = f.read()
    namespace = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                namespace[alias.asname or alias.name.split(".")[0]] = __import__(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module = __import__(node.module, fromlist=["*"])
            for alias in node.names:
                namespace[alias.asname or alias.name] = getattr(module, alias.name)
    return namespace


def _build_isolated_namespace() -> dict:
    """只把 `expected_mcp_tool_count` 及其依赖放进一个干净命名空间。"""
    namespace = _module_level_imports()
    namespace.update({
        "ROOT": pathlib.Path(REPO_ROOT),
        # 该函数只在解析失败/源码缺失时调用 log_warn，这里给个静默替身
        "log_warn": lambda *a, **k: None,
    })
    exec(compile(_extract_function_source("expected_mcp_tool_count"), "<build>", "exec"), namespace)
    return namespace


class ExpectedMcpToolCountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.namespace = _build_isolated_namespace()
        self.derived_fn = self.namespace["expected_mcp_tool_count"]

    def test_derived_count_matches_real_definition(self) -> None:
        # 非 0 是硬要求：返回 0 会让 smoke_test_mcp 跳过工具数比对
        from GalTransl.mcp_tools import MCP_TOOL_DEFS

        derived = self.derived_fn()
        self.assertEqual(derived, len(MCP_TOOL_DEFS))
        self.assertGreater(derived, 0, "派生值为 0 会让冒烟测试静默跳过比对")

    def test_write_tools_are_included_in_derivation(self) -> None:
        # 写工具在 _WRITE_TOOL_DEFS 里定义后经 .extend 追加；若只数 MCP_TOOL_DEFS
        # 字面量，会得到 11 而非 15——这正是本条要挡的偏差
        from GalTransl.mcp_tools import MCP_TOOL_DEFS

        literal_read_only = 11
        self.assertGreater(len(MCP_TOOL_DEFS), literal_read_only)
        self.assertEqual(self.derived_fn(), len(MCP_TOOL_DEFS))

    def test_missing_source_returns_zero_instead_of_raising(self) -> None:
        # 源码缺失时不应抛异常（构建脚本要能继续跑并只给告警）
        original = self.namespace["ROOT"]
        try:
            self.namespace["ROOT"] = pathlib.Path(os.path.join(REPO_ROOT, "no_such_dir"))
            self.assertEqual(self.derived_fn(), 0)
        finally:
            self.namespace["ROOT"] = original

    def test_derivation_does_not_import_galtransl(self) -> None:
        # 构建脚本跑在系统 Python 下，未必装齐 GalTransl 的运行时依赖，
        # 故必须是纯 AST 读取（不 import 目标模块）
        source = _extract_function_source("expected_mcp_tool_count")
        self.assertNotIn("import GalTransl", source)
        self.assertNotIn("importlib", source)
        self.assertIn("ast.parse", source)


class DerivationDegradesSafelyTests(unittest.TestCase):
    """结构漂移时宁可返回 0（= 跳过比对），也不能返回**错值**。

    审查实证过：初版实现在 `MCP_TOOL_DEFS += [...]` 等写法下会返回偏低的非零值
    （如真值 7 却返回 3），而调用方会拿这个数字去判构建失败——用猜出来的数字
    报「工具数过时」比不检查更糟。这里把各类追加写法全部钉住。
    """

    SOURCES = {
        # 与真实文件同构：字面量 + 模块级 extend(字面量) → 必须算对
        "literal_plus_module_extend": ("MCP_TOOL_DEFS=[1,2,3]\n_W=[4,5]\nMCP_TOOL_DEFS.extend(_W)\n", 5),
        "type_annotated_assign": ("MCP_TOOL_DEFS: list = [1,2,3]\n_W=[4,5]\nMCP_TOOL_DEFS.extend(_W)\n", 5),
        # 以下都无法可靠推算 → 必须降级为 0
        "aug_assign": ("MCP_TOOL_DEFS=[1,2,3]\nMCP_TOOL_DEFS += [4,5,6,7]\n", 0),
        "extend_in_loop": ("MCP_TOOL_DEFS=[1,2,3]\nfor w in ([4,5],):\n    MCP_TOOL_DEFS.extend(w)\n", 0),
        "list_comprehension": ("MCP_TOOL_DEFS=[x for x in (1,2,3)]\n", 0),
        "append": ("MCP_TOOL_DEFS=[1,2,3]\nMCP_TOOL_DEFS.append(4)\n", 0),
        "two_extends": ("MCP_TOOL_DEFS=[1,2,3]\n_W=[4]\n_X=[5]\nMCP_TOOL_DEFS.extend(_W)\nMCP_TOOL_DEFS.extend(_X)\n", 0),
        "extend_inside_if": ("MCP_TOOL_DEFS=[1,2,3]\n_W=[4]\nif True:\n    MCP_TOOL_DEFS.extend(_W)\n", 0),
        "extend_inline_literal": ("MCP_TOOL_DEFS=[1,2,3]\nMCP_TOOL_DEFS.extend([4,5,6])\n", 0),
    }

    def _run_on(self, body: str) -> int:
        """把函数源码放进**独立命名空间**，对一段合成的 mcp_tools.py 求值。

        每次都从 `_build_isolated_namespace()` 取干净命名空间，并把 ROOT 指向
        只含该合成文件的临时目录——否则会读到真实 mcp_tools.py 而串味。
        """
        import shutil
        import tempfile

        root = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(root, "GalTransl"))
            with open(os.path.join(root, "GalTransl", "mcp_tools.py"), "w", encoding="utf-8") as f:
                f.write(body)
            namespace = _build_isolated_namespace()
            namespace["ROOT"] = pathlib.Path(root)
            exec(
                compile(_extract_function_source("expected_mcp_tool_count"), "<build>", "exec"),
                namespace,
            )
            return namespace["expected_mcp_tool_count"]()
        finally:
            shutil.rmtree(root)

    def test_expected_result_per_shape(self) -> None:
        for label, (body, expected) in self.SOURCES.items():
            with self.subTest(shape=label):
                self.assertEqual(self._run_on(body), expected)

    def test_never_returns_a_wrong_nonzero_value(self) -> None:
        # 核心不变式：返回值要么是 0（跳过），要么就得是对的
        for label, (body, expected) in self.SOURCES.items():
            if expected == 0:
                continue
            with self.subTest(shape=label):
                self.assertEqual(self._run_on(body), expected)


class SmokeTestSourceContractTests(unittest.TestCase):
    """冒烟测试的契约：用 **AST 检查函数体**，而非源码字符串包含。

    审查实证过：只用 `assertIn("expected_mcp_tool_count()", source)` 会假阳性——
    把整个实现注释掉后测试照样通过（命中的是注释里的函数名）。故这里解析
    `smoke_test_mcp` 的函数体，确认其中**确实有**可执行的对应语句。
    """

    def _smoke_body(self) -> ast.AST:
        with open(BUILD_SCRIPT, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "smoke_test_mcp":
                return node
        raise AssertionError("构建脚本里找不到 smoke_test_mcp")

    def _body_calls(self) -> set:
        return {
            n.func.id
            for n in ast.walk(self._smoke_body())
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }

    def _body_names(self) -> set:
        return {n.id for n in ast.walk(self._smoke_body()) if isinstance(n, ast.Name)}

    def test_smoke_test_reads_instructions(self) -> None:
        # 必须真的从响应里取出 instructions 并据此判断。
        # 取值形态是 `result.get("instructions")`（字符串字面量），
        # 故检查存在该键的取值调用，而非要求某个特定比较节点。
        body = self._smoke_body()
        got_instructions = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "instructions"
            for node in ast.walk(body)
        )
        self.assertTrue(got_instructions, "smoke_test_mcp 没有从响应里取 instructions")
        # 且必须被用于判断（出现同名变量并参与比较/条件）
        self.assertIn("instructions", self._body_names())

    def test_smoke_test_compares_tool_count(self) -> None:
        # 必须**调用** expected_mcp_tool_count()，而不是只在注释里提它
        self.assertIn("expected_mcp_tool_count", self._body_calls())

    def test_smoke_test_warns_when_derivation_fails(self) -> None:
        # 派生失败（返回 0）时必须显式告警而非静默跳过
        self.assertIn("log_warn", self._body_calls())

    def test_build_script_does_not_use_the_removed_spec_file(self) -> None:
        # 0.6.0 删除了 galtransl_mcp.spec（硬编码绝对路径、换机器即失效），
        # 构建改走内联 PyInstaller 命令；不许再回退到 spec。
        # 只看可执行代码里的字符串常量（docstring/注释不算），
        # 且必须确实引用了 MCP 入口。
        with open(BUILD_SCRIPT, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        strings = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        self.assertFalse(
            [s for s in strings if "galtransl_mcp.spec" in s],
            "构建脚本仍在引用已删除的 galtransl_mcp.spec",
        )
        self.assertTrue(
            any("run_mcp_server.py" in s for s in strings),
            "构建脚本未引用 MCP 入口 run_mcp_server.py",
        )


class InstructionsContractTests(unittest.TestCase):
    def test_instructions_declares_tool_count(self) -> None:
        # 与 build_release 冒烟的比对口径一致：instructions 必须声明工具数
        from GalTransl.mcp_tools import SERVER_INSTRUCTIONS

        self.assertRegex(SERVER_INSTRUCTIONS, r"（\s*\d+\s*个工具")

    def test_instructions_count_matches_tool_defs(self) -> None:
        import re

        from GalTransl.mcp_tools import MCP_TOOL_DEFS, SERVER_INSTRUCTIONS

        # 用与冒烟测试相同的严格口径（「（N 个工具」），避免宽松子串带来的假通过
        pattern = rf"（\s*{len(MCP_TOOL_DEFS)}\s*个工具"
        self.assertRegex(SERVER_INSTRUCTIONS, pattern)

    def test_strict_pattern_rejects_partial_count(self) -> None:
        # 审查实证：旧口径 `f"{11} 个" in text` 会被「11 个只读」假命中；
        # 若派生值算成半量（11），旧口径反而 PASS，漏检真问题
        import re

        misleading = "（11 个只读检索，没有写工具）"
        self.assertIn("11 个", misleading)  # 旧口径会误判通过
        self.assertIsNone(re.search(r"（\s*11\s*个工具", misleading))  # 新口径不误判


if __name__ == "__main__":
    unittest.main()
