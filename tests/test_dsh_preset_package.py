"""dsh 预设包的结构与一致性单测（0.6.0 批次 4）。

回归背景：预设本体原先在两处手工维护（`cordis.patch.yml` 与
`presets/galtransl.patch.yml`），审查时发现 persona 提示词已经不一致。
现改为单一真相源 `galtransl.preset.yml` + 生成脚本。本文件锁定：
1. 生成物与真相源语义等价（防再次漂移）；
2. 预设声明的必填字段与形状正确（照 dsh 0.2.0-rc.2 的 schema）；
3. `persona` 用的是 `prefix` 而非 `text`（`text` 会让整个预设行激活失败）；
4. 预设只挂 4 个白名单行，**不含任何危险工具**（这是本预设的核心安全性质）。
"""
import importlib.util
import os
import subprocess
import sys
import unittest

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESET_DIR = os.path.join(REPO_ROOT, "agents", "dsh-preset")
SOURCE_PATH = os.path.join(PRESET_DIR, "galtransl.preset.yml")
TARGET_PATH = os.path.join(PRESET_DIR, "cordis.patch.yml")
BUILDER_PATH = os.path.join(REPO_ROOT, "tools", "build_dsh_preset.py")

# dsh 0.2.0-rc.2 的 @deepseek-ai/dsh-agent-preset 只声明这 5 个字段
ALLOWED_PRESET_FIELDS = {"id", "name", "description", "order", "plugins"}

# 预设刻意**只**挂这些行；出现其它行即视为安全回归
EXPECTED_PLUGIN_IDS = ["persona", "agent-instructions", "mcp-galtransl", "tool-ask-user"]

# 允许出现的 bundle（**白名单**）。预设的安全性质是「危险工具根本不存在」，
# 用黑名单会漏——实测 dsh 0.2.0-rc.2 共有 24 个 tool-* 行 id，其中
# tool-str-replace-editor（文件写入）、tool-cordis（改 loader 配置）、
# tool-agent-team / tool-workflow / tool-ralph（子代理编排）都不在直觉黑名单里。
# 白名单是唯一不会随 dsh 新增工具而失效的写法。
ALLOWED_PLUGIN_BUNDLES = {
    "@deepseek-ai/dsh-persona",
    "@deepseek-ai/dsh-agent-instructions",
    "@deepseek-ai/dsh-mcp-client",
    "@deepseek-ai/dsh-tool-ask-user",
}


def _load_builder():
    """按路径加载 tools/build_dsh_preset.py（它不在包里）。"""
    spec = importlib.util.spec_from_file_location("build_dsh_preset", BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_entries():
    with open(SOURCE_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class PresetSourceShapeTests(unittest.TestCase):
    """真相源本身必须是合法的 cordis:include 条目清单。"""

    def test_source_is_top_level_list(self) -> None:
        # cordis:include 要求目标文件是「顶层数组的条目清单」，
        # 写成完整 patch（带 - insert:）会被 entryListProblem 拒绝
        self.assertIsInstance(_source_entries(), list)

    def test_source_has_no_insert_wrapper(self) -> None:
        for entry in _source_entries():
            with self.subTest(entry=entry.get("id")):
                self.assertNotIn("insert", entry)

    def test_preset_row_has_only_schema_fields(self) -> None:
        entry = _source_entries()[0]
        self.assertEqual(entry["name"], "@deepseek-ai/dsh-agent-preset")
        self.assertLessEqual(set(entry["config"]), ALLOWED_PRESET_FIELDS)

    def test_required_fields_present(self) -> None:
        # id 与 plugins 是 schema 里仅有的两个 required
        config = _source_entries()[0]["config"]
        self.assertTrue(str(config["id"]).strip())
        self.assertIsInstance(config["plugins"], list)
        self.assertTrue(config["plugins"])


class PresetSafetyTests(unittest.TestCase):
    """安全性质：预设只挂白名单行，危险工具根本不存在。"""

    def _plugins(self) -> list:
        return _source_entries()[0]["config"]["plugins"]

    def test_exact_plugin_ids(self) -> None:
        plugins = _source_entries()[0]["config"]["plugins"]
        self.assertEqual([p["id"] for p in plugins], EXPECTED_PLUGIN_IDS)

    def test_only_allowlisted_bundles_are_referenced(self) -> None:
        # 白名单而非黑名单：dsh 新增工具时黑名单会静默失效，白名单不会
        plugins = _source_entries()[0]["config"]["plugins"]
        for plugin in plugins:
            with self.subTest(plugin=plugin["id"]):
                self.assertIn(plugin.get("name"), ALLOWED_PLUGIN_BUNDLES)

    def test_no_file_or_shell_capable_bundle_by_name(self) -> None:
        # 防御性冗余：即便有人往白名单里加东西，也不许出现这些明显的文件/命令能力
        names = " ".join(str(p.get("name", "")) for p in _source_entries()[0]["config"]["plugins"])
        for marker in ("tool-fs", "tool-pwsh", "tool-bash", "str-replace-editor", "plugin-manager"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, names)

    def test_no_plugin_is_disabled_or_carries_js(self) -> None:
        # 预设靠「不挂载」收窄，不靠 disabled；`!!js` 条件式行同理（会让能力随环境变化）。
        # 必须**递归**扫整行——真实风险在嵌套的 config.disabled，只查顶层键会漏。
        for plugin in self._plugins():
            with self.subTest(plugin=plugin["id"]):
                blob = yaml.safe_dump(plugin, allow_unicode=True)
                self.assertNotIn("disabled:", blob)
                self.assertNotIn("!!js", blob)
                self.assertNotIn("dshHomePath", blob)


class PersonaFieldTests(unittest.TestCase):
    def _persona_prefix(self) -> str:
        persona = next(p for p in _source_entries()[0]["config"]["plugins"] if p["id"] == "persona")
        return persona["config"]["prefix"]

    def test_persona_uses_prefix_not_text(self) -> None:
        # dsh-persona 的 Config 是 prefix(z.string().required())，
        # 用 text: 会让整条预设行激活失败（用户机器上遗留文件就踩了这个坑）
        persona = next(p for p in _source_entries()[0]["config"]["plugins"] if p["id"] == "persona")
        self.assertIn("prefix", persona["config"])
        self.assertNotIn("text", persona["config"])

    def test_persona_headline_states_current_tool_counts(self) -> None:
        # 提示词里的工具数必须与实际 MCP 工具面一致，否则会诱导 agent 误判自己的能力。
        # 数字从 MCP_TOOL_DEFS 派生而非手写——本批修的正是「提示词停留在旧工具数」的漂移。
        from GalTransl.mcp_tools import MCP_TOOL_DEFS

        read_count = sum(1 for d in MCP_TOOL_DEFS if d["kind"] == "read")
        write_count = sum(1 for d in MCP_TOOL_DEFS if d["kind"] == "write")
        prefix = self._persona_prefix()

        self.assertIn(f"{read_count + write_count} 个", prefix)
        self.assertIn(f"{read_count} 只读", prefix)
        self.assertIn(f"{write_count} 写", prefix)

    def test_persona_does_not_claim_read_only_service(self) -> None:
        # 回归：断言「不该出现」的旧口径。仅断言关键词存在是不够的——
        # 0.5.1 的旧文案（「本服务当前只读：没有启动翻译、改配置、写字典的工具」）
        # 同样含「只读/写/H/project_dir」，老断言照样通过，挡不住它要挡的漂移。
        prefix = self._persona_prefix()
        for stale in ("本服务当前只读", "没有启动翻译", "无写能力", "全部只读"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, prefix)

    def test_persona_names_every_write_tool(self) -> None:
        # 写工具必须逐个点名：agent 不知道有某工具就不会用，用户会以为功能缺失
        from GalTransl.mcp_tools import MCP_TOOL_DEFS

        prefix = self._persona_prefix()
        for definition in MCP_TOOL_DEFS:
            if definition["kind"] != "write":
                continue
            with self.subTest(tool=definition["name"]):
                self.assertIn(definition["name"].replace("galtransl_", ""), prefix)

    def test_persona_h_guidance_defers_to_mcp_instructions(self) -> None:
        # H 门禁可在 GalTransl 设置中开闭，服务说明随设置变化；persona 是静态文本，
        # 必须写成条件式并锚定服务说明——否则关门禁后 persona 仍在替用户拒绝 H 任务。
        # 无条件的旧口径「写工具有 H 硬门禁」不得回归。
        prefix = self._persona_prefix()
        self.assertIn("以 galtransl MCP 服务的使用说明", prefix)
        self.assertIn("用户已在 GalTransl 设置中关闭 H 门禁", prefix)
        self.assertIn("不要**改写措辞重试", prefix)  # 被拒行为预案保留（含否定词，门禁开时生效）
        self.assertNotIn("写工具有 H 硬门禁", prefix)

    def test_persona_h_anchors_match_instruction_texts(self) -> None:
        # 跨模块互锁：persona 引用的两态锚定词必须逐字命中 build_server_instructions
        # 的对应文案（任一侧改文案忘了同步另一侧时立即暴露）
        from GalTransl.mcp_tools import build_server_instructions

        prefix = self._persona_prefix()
        self.assertIn("禁止查看 H", build_server_instructions())
        self.assertIn(
            "用户已在 GalTransl 设置中关闭 H 门禁",
            build_server_instructions(h_gate_enabled=False),
        )
        self.assertIn("禁止查看 H", prefix)
        self.assertIn("用户已在 GalTransl 设置中关闭 H 门禁", prefix)

    def test_persona_states_project_dir_write_validation(self) -> None:
        # L3 白名单会让写工具报错，提示词应预告这一点，避免 agent 反复试错
        prefix = self._persona_prefix()
        self.assertIn("project_dir", prefix)
        self.assertIn("config.inc.yaml", prefix)

    def test_persona_requires_explicit_user_request_for_submit(self) -> None:
        # submit_job 真实消耗 API 额度，措辞与 MCP 工具描述保持一致
        self.assertIn("用户明确要求", self._persona_prefix())

    def test_agent_instructions_restates_max_bytes(self) -> None:
        # 预设树不复用 dsh-base 的同名行，maxBytes 必须重申
        row = next(p for p in _source_entries()[0]["config"]["plugins"] if p["id"] == "agent-instructions")
        self.assertIn("maxBytes", row["config"])


class MCPClientConfigTests(unittest.TestCase):
    def _mcp(self) -> dict:
        return next(p for p in _source_entries()[0]["config"]["plugins"] if p["id"] == "mcp-galtransl")

    def test_transport_is_stdio_and_named(self) -> None:
        config = self._mcp()["config"]
        self.assertEqual(config["serverName"], "galtransl")
        self.assertEqual(config["transport"], "stdio")

    def test_paths_are_absolute(self) -> None:
        # dsh 的 subprocess provider 只对裸名查 PATH，含分隔符的相对路径会被拒
        config = self._mcp()["config"]
        for key in ("command", "cwd"):
            with self.subTest(key=key):
                self.assertTrue(os.path.isabs(config[key]), config[key])
        for arg in config["args"]:
            with self.subTest(arg=arg):
                self.assertTrue(os.path.isabs(arg), arg)

    def test_fail_on_startup_error_is_false(self) -> None:
        # MCP 未就绪不应连累整个 dsh 启动
        self.assertFalse(self._mcp()["config"]["failOnStartupError"])


class GeneratedPatchSyncTests(unittest.TestCase):
    """生成物必须与真相源保持同步（本批修的就是这个漂移）。"""

    def test_generated_target_matches_source(self) -> None:
        with open(TARGET_PATH, "r", encoding="utf-8") as f:
            target = yaml.safe_load(f)
        self.assertIsInstance(target, list)
        self.assertEqual(len(target), 1)
        self.assertEqual(list(target[0]), ["insert"])
        self.assertEqual(target[0]["insert"], _source_entries())

    def test_builder_check_mode_reports_in_sync(self) -> None:
        # 用 ascii 编码读回并手动解码，规避中文路径下 Windows 控制台编码问题
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        proc = subprocess.run(
            [sys.executable, BUILDER_PATH, "--check"],
            capture_output=True,
            env=env,
        )
        detail = (proc.stdout or b"").decode("utf-8", "replace") + (proc.stderr or b"").decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, detail)

    def test_generated_file_is_marked_do_not_edit(self) -> None:
        with open(TARGET_PATH, "r", encoding="utf-8") as f:
            head = f.read(400)
        self.assertIn("请勿手改", head)
        self.assertIn("galtransl.preset.yml", head)

    def test_builder_rejects_non_list_source(self) -> None:
        import pathlib
        import tempfile

        builder = _load_builder()
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False, encoding="utf-8") as f:
            yaml.safe_dump({"not": "a list"}, f, allow_unicode=True)
            path = f.name
        try:
            with self.assertRaises(ValueError):
                builder.load_entries(pathlib.Path(path))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
