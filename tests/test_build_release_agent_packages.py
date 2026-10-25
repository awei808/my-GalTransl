"""构建脚本的外置 Agent 包单测（MCP / Skill / dsh 预设 / 预设明文）。

背景：发布包此前只有主 zip；外部 Agent 需要单独获取 MCP 服务、Skill 文档
与预设。构建新增四个包，其中 dsh 预设必须把源码模式的绝对路径改写为
打包版占位路径，否则会既泄露开发机路径又无法运行。

⚠️ 不 import build_release_py312（模块级有 stdio reconfigure 副作用），
沿用 test_build_release_mcp.py 的 AST 提取 + 隔离 exec 方式。
"""
import ast
import os
import pathlib
import shutil
import tempfile
import unittest
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_SCRIPT = os.path.join(REPO_ROOT, "build_release_py312.py")
PRESET_DIR = os.path.join(REPO_ROOT, "agents", "dsh-preset")
SKILLS_DIR = os.path.join(REPO_ROOT, "skills")

PACKAGED_EXE = "C:/GalTransl/backend/galtransl_mcp.exe"
PACKAGED_ROOT = "C:/GalTransl"
PACKAGED_NOTE = "【打包版说明】"

PACKAGE_FUNCTIONS = (
    "_collect_dir_entries",
    "_write_zip_file",
    "_rewrite_mcp_client_config",
    "_mcp_package_readme",
    "_skill_package_readme",
    "_dsh_package_readme",
    "_plain_preset_package_readme",
    "_packaged_dsh_readme",
    "_create_mcp_package",
    "_create_skill_package",
    "_create_dsh_package",
    "_create_plain_preset_package",
)


def _load_source() -> str:
    with open(BUILD_SCRIPT, "r", encoding="utf-8") as f:
        return f.read()


def _extract_function_source(name: str) -> str:
    source = _load_source()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"构建脚本里找不到函数 {name}")


def _module_level_imports() -> dict:
    namespace = {}
    for node in ast.parse(_load_source()).body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                namespace[alias.asname or alias.name.split(".")[0]] = __import__(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module = __import__(node.module, fromlist=["*"])
            for alias in node.names:
                namespace[alias.asname or alias.name] = getattr(module, alias.name)
    return namespace


def _build_namespace(dict_dir: pathlib.Path, guidelines_dir: pathlib.Path) -> dict:
    namespace = _module_level_imports()
    namespace.update({
        "PACKAGED_MCP_EXE_HINT": PACKAGED_EXE,
        "PACKAGED_ROOT_HINT": PACKAGED_ROOT,
        "PACKAGED_PRESET_NOTE": (
            f"# {PACKAGED_NOTE}本包不含 tools/build_dsh_preset.py 生成脚本。\n"
            "# 请按 INSTALL-PACKAGED.md 手改本文件的 command/args/cwd；\n"
            "# 若 galtransl.preset.yml 与 cordis.patch.yml 都要用，请手工保持一致。\n"
        ),
        "MCP_ZIP_NAME": "GalTransl_0.0.0_mcp.zip",
        "DICT_DIR": dict_dir,
        "GUIDELINES_DIR": guidelines_dir,
        "log_debug": lambda *a, **k: None,
    })
    for name in PACKAGE_FUNCTIONS:
        exec(
            compile(_extract_function_source(name), "<build>", "exec"),
            namespace,
        )
    return namespace


class PresetRewriteTests(unittest.TestCase):
    """dsh 预设的源码路径必须被占位符替换，且改动范围只限 MCP 行。"""

    def setUp(self) -> None:
        self.namespace = _build_namespace(pathlib.Path("."), pathlib.Path("."))
        self.rewrite = self.namespace["_rewrite_mcp_client_config"]

    def _actual(self, name: str) -> str:
        with open(os.path.join(PRESET_DIR, name), "r", encoding="utf-8") as f:
            return f.read()

    def _assert_packaged(self, written: str) -> None:
        repo_paths = {REPO_ROOT, REPO_ROOT.replace("\\", "/")}
        for repo_path in repo_paths:
            self.assertNotIn(repo_path, written)
        self.assertIn(f"command: '{PACKAGED_EXE}'", written)
        self.assertIn("args: []", written)
        self.assertIn(f"cwd: '{PACKAGED_ROOT}'", written)
        # 块外内容不得被改写
        self.assertIn("serverName: galtransl", written)
        self.assertIn("tool-subagent-control", written)

    def test_rewrites_truth_source(self) -> None:
        written = self.rewrite(self._actual("galtransl.preset.yml"))
        self._assert_packaged(written)
        self.assertIn("你是 GalTransl 翻译项目的助手", written)

    def test_rewrites_generated_patch(self) -> None:
        # 生成物里 args 列表项与 args: 同级（YAML 合法写法），改写必须一并吞掉
        written = self.rewrite(self._actual("cordis.patch.yml"))
        self._assert_packaged(written)
        self.assertTrue(written.lstrip().startswith("#"))
        self.assertIn("- insert:", written)

    def test_rewrite_is_idempotent(self) -> None:
        once = self.rewrite(self._actual("galtransl.preset.yml"))
        self.assertEqual(self.rewrite(once), once)

    def test_missing_mcp_block_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.rewrite("- id: persona\n  name: '@deepseek-ai/dsh-persona'\n")

    def test_duplicated_mcp_block_raises(self) -> None:
        # 重复块会让 command 命中 2 次；宁可失败也不生成残缺预设
        block = (
            "- id: mcp-galtransl\n"
            "  config:\n"
            "    command: 'D:\\dev\\python.exe'\n"
            "    args:\n"
            "      - 'D:\\dev\\run_mcp_server.py'\n"
            "    cwd: 'D:\\dev'\n"
        )
        with self.assertRaises(ValueError):
            self.rewrite(block + block)


class AgentPackageZipTests(unittest.TestCase):
    """四个包的内容与路径改写结果必须符合各自用途。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.dict_dir = self.root / "Dict"
        self.dict_dir.mkdir()
        (self.dict_dir / "公共字典.txt").write_text("x", encoding="utf-8")
        self.guidelines_dir = self.root / "translation_guidelines"
        self.guidelines_dir.mkdir()
        (self.guidelines_dir / "指南.md").write_text("x", encoding="utf-8")
        self.skills_dir = self.root / "skills"
        skill = self.skills_dir / "galtransl-mcp" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: galtransl-mcp\n---\n", encoding="utf-8")
        self.preset_dir = self.root / "dsh-preset"
        (self.preset_dir / "locale").mkdir(parents=True)
        (self.preset_dir / "package.json").write_text("{}", encoding="utf-8")
        (self.preset_dir / "icon.svg").write_text("<svg/>", encoding="utf-8")
        (self.preset_dir / "locale" / "zh.json").write_text("{}", encoding="utf-8")
        (self.preset_dir / "README.md").write_text("dsh readme", encoding="utf-8")
        preset = (
            "# preset\n"
            "- id: preset-galtransl\n"
            "  config:\n"
            "    plugins:\n"
            "      - id: persona\n"
            "        config:\n"
            "          prefix: 'persona text'\n"
            "      - id: mcp-galtransl\n"
            "        config:\n"
            "          serverName: galtransl\n"
            "          command: 'D:\\dev\\repo\\venv\\Scripts\\python.exe'\n"
            "          args:\n"
            "            - 'D:\\dev\\repo\\run_mcp_server.py'\n"
            "          cwd: 'D:\\dev\\repo'\n"
            "      - id: tool-ask-user\n"
            "        name: '@deepseek-ai/dsh-tool-ask-user'\n"
        )
        (self.preset_dir / "galtransl.preset.yml").write_text(preset, encoding="utf-8")
        # cordis 生成物的 args 列表项与 args: 同级
        patch = preset.replace("args:\n            - '", "args:\n          - '")
        (self.preset_dir / "cordis.patch.yml").write_text(
            "- insert:\n  " + patch.replace("\n", "\n  "), encoding="utf-8"
        )
        self.exe = self.root / "galtransl_mcp.exe"
        self.exe.write_bytes(b"exe")
        self.ns = _build_namespace(self.dict_dir, self.guidelines_dir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _zip_names(self, path: pathlib.Path) -> list:
        with zipfile.ZipFile(path) as zf:
            return zf.namelist()

    def test_mcp_package_contains_exe_skill_and_common_dict(self) -> None:
        zip_path = self.root / "mcp.zip"
        self.ns["_create_mcp_package"](zip_path, self.exe, self.skills_dir)
        names = self._zip_names(zip_path)
        self.assertIn("mcp/backend/galtransl_mcp.exe", names)
        self.assertIn("mcp/skills/galtransl-mcp/SKILL.md", names)
        self.assertIn("mcp/Dict/公共字典.txt", names)
        self.assertIn("mcp/translation_guidelines/指南.md", names)
        self.assertIn("mcp/README.md", names)
        with zipfile.ZipFile(zip_path) as zf:
            readme = zf.read("mcp/README.md").decode("utf-8")
        self.assertIn("galtransl_mcp.exe", readme)
        self.assertIn("mcpServers", readme)

    def test_skill_package_is_drop_in_skill_dir(self) -> None:
        zip_path = self.root / "skill.zip"
        self.ns["_create_skill_package"](zip_path, self.skills_dir)
        names = self._zip_names(zip_path)
        self.assertIn("galtransl-mcp/SKILL.md", names)
        self.assertIn("galtransl-mcp/README.md", names)

    def test_skill_package_requires_skill_md(self) -> None:
        empty_skills = self.root / "empty_skills"
        empty_skills.mkdir()
        with self.assertRaises(FileNotFoundError):
            self.ns["_create_skill_package"](self.root / "bad.zip", empty_skills)

    def test_dsh_package_requires_manifest_and_both_yaml(self) -> None:
        for missing in ("galtransl.preset.yml", "cordis.patch.yml", "package.json"):
            with self.subTest(missing=missing):
                broken = self.root / f"broken-{missing}"
                shutil.copytree(self.preset_dir, broken)
                (broken / missing).unlink()
                with self.assertRaises(FileNotFoundError):
                    self.ns["_create_dsh_package"](self.root / "bad.zip", broken, None)

    def test_dsh_package_rewrites_both_yaml_files(self) -> None:
        zip_path = self.root / "dsh.zip"
        self.ns["_create_dsh_package"](zip_path, self.preset_dir, self.skills_dir)
        names = self._zip_names(zip_path)
        self.assertIn("galtransl-dsh-preset/galtransl.preset.yml", names)
        self.assertIn("galtransl-dsh-preset/cordis.patch.yml", names)
        self.assertIn("galtransl-dsh-preset/INSTALL-PACKAGED.md", names)
        self.assertIn("galtransl-dsh-preset/skills/galtransl-mcp/SKILL.md", names)
        with zipfile.ZipFile(zip_path) as zf:
            for name in (
                "galtransl-dsh-preset/galtransl.preset.yml",
                "galtransl-dsh-preset/cordis.patch.yml",
            ):
                text = zf.read(name).decode("utf-8")
                self.assertTrue(text.startswith(f"# {PACKAGED_NOTE}"), name)
                self.assertIn("INSTALL-PACKAGED.md", text, name)
                self.assertNotIn("D:\\dev\\repo", text, name)
                self.assertIn(f"command: '{PACKAGED_EXE}'", text, name)
                self.assertIn("args: []", text, name)
                self.assertIn(f"cwd: '{PACKAGED_ROOT}'", text, name)
                self.assertIn("persona text", text, name)
            readme = zf.read("galtransl-dsh-preset/README.md").decode("utf-8")
        self.assertTrue(readme.startswith("> **打包版覆盖说明"), readme[:80])
        self.assertIn("INSTALL-PACKAGED.md", readme)

    def test_no_repo_path_leaks_in_any_package_text(self) -> None:
        # 四个包的所有文本条目都不得出现构建机仓库路径（含正/反斜杠两种写法）
        expected = {
            "mcp.zip": ("mcp/backend/galtransl_mcp.exe",),
            "skill.zip": ("galtransl-mcp/SKILL.md",),
            "dsh.zip": ("galtransl-dsh-preset/galtransl.preset.yml",),
            "plain.zip": ("galtransl-preset-plain/galtransl.preset.yml",),
        }
        builders = {
            "mcp.zip": lambda p: self.ns["_create_mcp_package"](p, self.exe, self.skills_dir),
            "skill.zip": lambda p: self.ns["_create_skill_package"](p, self.skills_dir),
            "dsh.zip": lambda p: self.ns["_create_dsh_package"](p, self.preset_dir, self.skills_dir),
            "plain.zip": lambda p: self.ns["_create_plain_preset_package"](
                p, self.preset_dir, self.skills_dir
            ),
        }
        repo_paths = {REPO_ROOT, REPO_ROOT.replace("\\", "/")}
        text_exts = (".md", ".yml", ".yaml", ".json", ".svg", ".txt")
        for name, build in builders.items():
            with self.subTest(package=name):
                zip_path = self.root / name
                build(zip_path)
                with zipfile.ZipFile(zip_path) as zf:
                    entries = [n for n in zf.namelist() if n.lower().endswith(text_exts)]
                    entry_names = set(zf.namelist())
                    for required in expected[name]:
                        self.assertIn(required, entry_names)
                    for entry in entries:
                        text = zf.read(entry).decode("utf-8", "replace")
                        for repo_path in repo_paths:
                            self.assertNotIn(repo_path, text, f"{name}:{entry}")

    def test_plain_preset_package_contains_source_and_skill(self) -> None:
        zip_path = self.root / "plain.zip"
        self.ns["_create_plain_preset_package"](
            zip_path, self.preset_dir, self.skills_dir
        )
        names = self._zip_names(zip_path)
        self.assertIn("galtransl-preset-plain/galtransl.preset.yml", names)
        self.assertIn("galtransl-preset-plain/skills/galtransl-mcp/SKILL.md", names)
        self.assertIn("galtransl-preset-plain/README.md", names)
        with zipfile.ZipFile(zip_path) as zf:
            text = zf.read("galtransl-preset-plain/galtransl.preset.yml").decode("utf-8")
            readme = zf.read("galtransl-preset-plain/README.md").decode("utf-8")
        self.assertNotIn("D:\\dev\\repo", text)
        self.assertIn("请让你的 Agent", readme)
        self.assertIn("自定义预设", readme)

    def _orchestrator_namespace(self, release_dir: pathlib.Path) -> dict:
        namespace = dict(self.ns)
        namespace.update({
            "RELEASE_DIR": release_dir,
            "SKILLS_DIR": self.skills_dir,
            "DSH_PRESET_DIR": self.preset_dir,
            "MCP_ZIP_NAME": "GalTransl_0.0.0_mcp.zip",
            "SKILL_ZIP_NAME": "GalTransl_0.0.0_skill.zip",
            "DSH_ZIP_NAME": "GalTransl_0.0.0_dsh-preset.zip",
            "PLAIN_PRESET_ZIP_NAME": "GalTransl_0.0.0_preset-plain.zip",
            "mcp_exe_name": lambda: "galtransl_mcp.exe",
            "log_info": lambda *a, **k: None,
            "log_ok": lambda *a, **k: None,
            "log_warn": lambda *a, **k: None,
            "log_err": lambda *a, **k: None,
        })
        exec(
            compile(_extract_function_source("build_agent_packages"), "<build>", "exec"),
            namespace,
        )
        return namespace

    def test_build_agent_packages_end_to_end(self) -> None:
        release = self.root / "release"
        release.mkdir()
        namespace = self._orchestrator_namespace(release)
        built = namespace["build_agent_packages"](self.exe, allow_incomplete=False)
        self.assertEqual(
            sorted(p.name for p in built),
            [
                "GalTransl_0.0.0_dsh-preset.zip",
                "GalTransl_0.0.0_mcp.zip",
                "GalTransl_0.0.0_preset-plain.zip",
                "GalTransl_0.0.0_skill.zip",
            ],
        )
        for path in built:
            self.assertTrue(path.is_file(), path)

    def test_build_agent_packages_degrades_with_allow_incomplete(self) -> None:
        release = self.root / "release-degraded"
        release.mkdir()
        namespace = self._orchestrator_namespace(release)
        empty_skills = self.root / "no_skills"
        empty_skills.mkdir()
        namespace["SKILLS_DIR"] = empty_skills
        built = namespace["build_agent_packages"](self.exe, allow_incomplete=True)
        names = {p.name for p in built}
        self.assertIn("GalTransl_0.0.0_mcp.zip", names)
        self.assertNotIn("GalTransl_0.0.0_skill.zip", names)
        self.assertIn("GalTransl_0.0.0_dsh-preset.zip", names)
        self.assertIn("GalTransl_0.0.0_preset-plain.zip", names)
        with self.assertRaises(SystemExit):
            namespace["build_agent_packages"](self.exe, allow_incomplete=False)


class BuildFlowContractTests(unittest.TestCase):
    """主流程必须真的调用外置 Agent 包构建，且受 --no-zip 控制。"""

    def test_main_calls_build_agent_packages_under_zip_guard(self) -> None:
        source = _load_source()
        tree = ast.parse(source)
        main_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        guarded = False
        for node in ast.walk(main_node):
            if not isinstance(node, ast.If):
                continue
            test_src = ast.get_source_segment(source, node.test) or ""
            if "args.no_zip" not in test_src:
                continue
            if any(
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "build_agent_packages"
                for sub in ast.walk(node)
            ):
                guarded = True
        self.assertTrue(guarded, "main 未在 if not args.no_zip 内调用 build_agent_packages")

    def test_package_names_cover_all_four_artifacts(self) -> None:
        # 常量必须在 build_agent_packages 的可执行代码里被引用（只写进注释/字符串不算）
        body = ast.parse(_extract_function_source("build_agent_packages"))
        names = {node.id for node in ast.walk(body) if isinstance(node, ast.Name)}
        for constant in (
            "MCP_ZIP_NAME",
            "SKILL_ZIP_NAME",
            "DSH_ZIP_NAME",
            "PLAIN_PRESET_ZIP_NAME",
        ):
            with self.subTest(constant=constant):
                self.assertIn(constant, names)

    def test_missing_prerequisites_fail_closed(self) -> None:
        # 与主发布包同口径：缺 exe/skills/预设时默认中止；sys.exit 必须在
        # 「not allow_incomplete」分支内，--allow-incomplete 才降级
        body = ast.parse(_extract_function_source("build_agent_packages"))
        guarded = False
        for node in ast.walk(body):
            if not isinstance(node, ast.If):
                continue
            names = {sub.id for sub in ast.walk(node.test) if isinstance(sub, ast.Name)}
            if "allow_incomplete" not in names:
                continue
            exits = [
                sub for sub in ast.walk(node)
                if isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "exit"
            ]
            if exits:
                guarded = True
        self.assertTrue(
            guarded,
            "缺组件时 sys.exit 未受 allow_incomplete 约束（应 fail-closed）",
        )


if __name__ == "__main__":
    unittest.main()
