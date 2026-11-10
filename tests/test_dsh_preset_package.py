"""dsh 预设包的结构与一致性单测。

回归背景：预设本体原先在两处手工维护（`cordis.patch.yml` 与
`presets/galtransl.patch.yml`），审查时发现 persona 提示词已经不一致。
现改为单一真相源 `galtransl.preset.yml` + 生成脚本。该文件含**两条预设**：

- `galtransl-coder`：翻译助手 + 程序员，额外挂通用文件工具（`tool-fs`）；
- `galtransl`：纯翻译助手，只有 MCP 工具，且不主动给程序建议。

本文件锁定：
1. 生成物与真相源语义等价（防再次漂移）；
2. 每条预设的必填字段与形状正确（照 @deepseek-ai/dsh-agent-preset 的 schema）；
3. `persona` 用的是 `prefix` 而非 `text`（`text` 会让整个预设行激活失败）；
4. 预设只挂各自白名单内的行；`galtransl` **不含任何文件/Shell/网页工具**
   （这是它的核心安全性质），`galtransl-coder` 只多一行 `tool-fs`；
5. 子代理能力是「同工具面的分身」：`spawn` 后端、不单独选预设（继承本预设）、
   `maxDepth: 1`（不能再派生），且不得引入 fork / 多代理编排行；
6. 两条预设共用的「硬性约束」及之后段落逐字一致（避免改一条忘另一条）；
7. 官方 subagent 工具没有同步模式，故两条预设的 persona 都必须写明
   「一轮最多派 1 个、派完立即结束本轮等通知」的串行约定。
"""
import importlib.util
import json
import os
import subprocess
import sys
import unittest

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESET_DIR = os.path.join(REPO_ROOT, "agents", "dsh-preset")
SOURCE_PATH = os.path.join(PRESET_DIR, "galtransl.preset.yml")
TARGET_PATH = os.path.join(PRESET_DIR, "cordis.patch.yml")
MANIFEST_PATH = os.path.join(PRESET_DIR, "package.json")
LOCALE_DIR = os.path.join(PRESET_DIR, "locale")
ICON_PATH = os.path.join(PRESET_DIR, "icon.svg")
BUILDER_PATH = os.path.join(REPO_ROOT, "tools", "build_dsh_preset.py")

CODER = "galtransl-coder"
TRANSLATOR = "galtransl"
PRESET_IDS = (CODER, TRANSLATOR)

# @deepseek-ai/dsh-agent-preset 只声明这 5 个字段
ALLOWED_PRESET_FIELDS = {"id", "name", "description", "order", "plugins"}

# 两条预设共有的行（顺序即声明顺序）
COMMON_PLUGIN_IDS = [
    "persona",
    "agent-instructions",
    "mcp-galtransl",
    "tool-ask-user",
    "tool-subagent",
    "tool-subagent-control",
]

# 每条预设刻意**只**挂这些行；出现其它行即视为安全回归。
# coder 多一行 tool-fs（read / write / edit），它是两条预设唯一的能力差异。
EXPECTED_PLUGIN_IDS = {
    CODER: [
        "persona",
        "agent-instructions",
        "mcp-galtransl",
        "tool-fs",
        "tool-ask-user",
        "tool-subagent",
        "tool-subagent-control",
    ],
    TRANSLATOR: COMMON_PLUGIN_IDS,
}

# 允许出现的 bundle（**白名单**）。预设的安全性质是「危险工具根本不存在」，
# 用黑名单会漏——dsh 的 tool-* 行里有这类不在直觉黑名单里的能力：
# tool-str-replace-editor（文件写入）、tool-cordis（改 loader 配置）、
# tool-agent-team / tool-workflow / tool-ralph（子代理编排）。
# 白名单是唯一不会随 dsh 新增工具而失效的写法。
COMMON_PLUGIN_BUNDLES = {
    "@deepseek-ai/dsh-persona",
    "@deepseek-ai/dsh-agent-instructions",
    "@deepseek-ai/dsh-mcp-client",
    "@deepseek-ai/dsh-tool-ask-user",
    # 子代理：task 工具本身 + send_message/interrupt_agent 控制面。
    # 两者提供的都是「派生子代理/与子代理通话」，不引入文件/Shell/网页能力；
    # 子代理继承本预设，因此拿到的工具面与父会话相同。
    "@deepseek-ai/dsh-tool-subagent",
    "@deepseek-ai/dsh-tool-subagent-control",
}

ALLOWED_PLUGIN_BUNDLES = {
    # coder：额外允许通用文件工具（宿主层已被 web-app 置 disabled，由预设挂载）
    CODER: COMMON_PLUGIN_BUNDLES | {"@deepseek-ai/dsh-tool-fs"},
    TRANSLATOR: COMMON_PLUGIN_BUNDLES,
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


def _preset(preset_id: str) -> dict:
    """按 config.id 取预设条目（顺序不再固定，禁止用下标取）。"""
    for entry in _source_entries():
        if entry.get("config", {}).get("id") == preset_id:
            return entry
    raise AssertionError(f"真相源里找不到预设 {preset_id}")


def _plugins(preset_id: str) -> list:
    return _preset(preset_id)["config"]["plugins"]


def _plugin(preset_id: str, plugin_id: str) -> dict:
    return next(p for p in _plugins(preset_id) if p["id"] == plugin_id)


def _persona(preset_id: str) -> str:
    return _plugin(preset_id, "persona")["config"]["prefix"]


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

    def test_declares_exactly_two_presets(self) -> None:
        ids = [entry["config"]["id"] for entry in _source_entries()]
        self.assertEqual(ids, list(PRESET_IDS))

    def test_patch_ids_and_preset_ids_are_unique(self) -> None:
        # patch id 与 config.id 都必须在 tree 内唯一，重复会让 dsh 启动报错
        entries = _source_entries()
        patch_ids = [entry["id"] for entry in entries]
        preset_ids = [entry["config"]["id"] for entry in entries]
        self.assertEqual(len(set(patch_ids)), len(patch_ids))
        self.assertEqual(len(set(preset_ids)), len(preset_ids))

    def test_preset_row_has_only_schema_fields(self) -> None:
        for preset_id in PRESET_IDS:
            with self.subTest(preset=preset_id):
                entry = _preset(preset_id)
                self.assertEqual(entry["name"], "@deepseek-ai/dsh-agent-preset")
                self.assertLessEqual(set(entry["config"]), ALLOWED_PRESET_FIELDS)

    def test_required_fields_present(self) -> None:
        # id 与 plugins 是 schema 里仅有的两个 required
        for preset_id in PRESET_IDS:
            with self.subTest(preset=preset_id):
                config = _preset(preset_id)["config"]
                self.assertTrue(str(config["id"]).strip())
                self.assertIsInstance(config["plugins"], list)
                self.assertTrue(config["plugins"])

    def test_display_fields_are_filled_and_ordered(self) -> None:
        # 选择器里靠 name/description 区分两条预设，留空等于让用户盲选
        orders = set()
        for preset_id in PRESET_IDS:
            with self.subTest(preset=preset_id):
                config = _preset(preset_id)["config"]
                self.assertTrue(str(config.get("name", "")).strip())
                self.assertTrue(str(config.get("description", "")).strip())
                orders.add(config.get("order"))
        self.assertEqual(len(orders), len(PRESET_IDS))


class PresetSafetyTests(unittest.TestCase):
    """安全性质：预设只挂各自白名单行；translator 连文件工具都没有。"""

    def test_exact_plugin_ids(self) -> None:
        for preset_id in PRESET_IDS:
            with self.subTest(preset=preset_id):
                self.assertEqual(
                    [p["id"] for p in _plugins(preset_id)],
                    EXPECTED_PLUGIN_IDS[preset_id],
                )

    def test_only_allowlisted_bundles_are_referenced(self) -> None:
        # 白名单而非黑名单：dsh 新增工具时黑名单会静默失效，白名单不会
        for preset_id in PRESET_IDS:
            for plugin in _plugins(preset_id):
                with self.subTest(preset=preset_id, plugin=plugin["id"]):
                    self.assertIn(plugin.get("name"), ALLOWED_PLUGIN_BUNDLES[preset_id])

    def test_coder_mounts_file_tool_and_translator_does_not(self) -> None:
        # 两条预设的唯一能力差异：通用读写文件工具
        self.assertEqual(_plugin(CODER, "tool-fs")["name"], "@deepseek-ai/dsh-tool-fs")
        translator_ids = {p["id"] for p in _plugins(TRANSLATOR)}
        self.assertNotIn("tool-fs", translator_ids)

    def test_no_shell_or_orchestration_bundle_by_name(self) -> None:
        # 防御性冗余：即便有人往白名单里加东西，也不许出现命令执行/配置改写能力
        for preset_id in PRESET_IDS:
            names = " ".join(str(p.get("name", "")) for p in _plugins(preset_id))
            for marker in ("tool-bash", "tool-terminal", "str-replace-editor", "plugin-manager", "tool-cordis"):
                with self.subTest(preset=preset_id, marker=marker):
                    self.assertNotIn(marker, names)

    def test_translator_has_no_file_capable_bundle_by_name(self) -> None:
        names = " ".join(str(p.get("name", "")) for p in _plugins(TRANSLATOR))
        for marker in ("tool-fs", "-fs-search"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, names)

    def test_no_plugin_is_disabled_or_carries_js(self) -> None:
        # 预设靠「不挂载」收窄，不靠 disabled；`!!js` 条件式行同理（会让能力随环境变化）。
        # 必须**递归**扫整行——真实风险在嵌套的 config.disabled，只查顶层键会漏。
        for preset_id in PRESET_IDS:
            for plugin in _plugins(preset_id):
                with self.subTest(preset=preset_id, plugin=plugin["id"]):
                    blob = yaml.safe_dump(plugin, allow_unicode=True)
                    self.assertNotIn("disabled:", blob)
                    self.assertNotIn("!!js", blob)
                    self.assertNotIn("dshHomePath", blob)

    def test_coder_file_tool_is_not_narrowed_by_config(self) -> None:
        # tool-fs 的 read/write/edit 开关默认全开；一旦有人用 config 关掉某个，
        # 说明「可读写」这条对外承诺与实现不符——必须显式改测试而不是悄悄收窄。
        row = _plugin(CODER, "tool-fs")
        self.assertNotIn("config", row)


class SubagentCapabilityTests(unittest.TestCase):
    """子代理是「同工具面的分身」，不是权限放大器。"""

    def test_subagent_uses_spawn_backend(self) -> None:
        # 只用 spawn：fork 后端会把当前对话前缀喂给子代理，与预设
        # 「子代理拿不到父会话历史、任务说明必须自包含」的约定冲突。
        for preset_id in PRESET_IDS:
            with self.subTest(preset=preset_id):
                config = _plugin(preset_id, "tool-subagent")["config"]
                self.assertEqual(config["provider"], "spawn")
                self.assertEqual(config["toolName"], "subagent")
                # dsh 已删除 persona / toolFilter 两个键，带上它们会让整条预设行激活失败；
                # 白名单式断言保证将来加字段必须显式改测试。
                self.assertLessEqual(set(config), {"provider", "toolName", "maxDepth"})

    def test_subagent_cannot_delegate_further(self) -> None:
        # maxDepth 写死 1：子代理不能再派生，避免递归放大并发数与 API 成本
        for preset_id in PRESET_IDS:
            with self.subTest(preset=preset_id):
                self.assertEqual(_plugin(preset_id, "tool-subagent")["config"]["maxDepth"], 1)

    def test_subagent_inherits_parent_preset(self) -> None:
        # 省略 agentPreset / allowedAgentPresets ⇒ 继承父会话预设 ⇒ 子代理工具面
        # 与父会话完全相同。一旦有人加上这两项，子代理就可能被指到别的预设，
        # 安全性质即被破坏。
        for preset_id in PRESET_IDS:
            with self.subTest(preset=preset_id):
                config = _plugin(preset_id, "tool-subagent")["config"]
                self.assertNotIn("agentPreset", config)
                self.assertNotIn("allowedAgentPresets", config)
                # 子代理没有额外的工具来源：每条预设里只有一处 MCP 客户端
                mcp_rows = [p for p in _plugins(preset_id) if p.get("name") == "@deepseek-ai/dsh-mcp-client"]
                self.assertEqual(len(mcp_rows), 1)

    def test_control_surface_present(self) -> None:
        # spawn 子代理是 continuable 的，靠 send_message 回报结论、靠
        # interrupt_agent 叫停；缺这行父会话只能等它自己结束。
        for preset_id in PRESET_IDS:
            with self.subTest(preset=preset_id):
                self.assertEqual(
                    _plugin(preset_id, "tool-subagent-control")["name"],
                    "@deepseek-ai/dsh-tool-subagent-control",
                )

    def test_no_fork_or_orchestration_rows(self) -> None:
        # 只要一层子代理，不引入 fork / 列表 / 多代理编排（它们是另一套能力面）
        for preset_id in PRESET_IDS:
            ids = {p["id"] for p in _plugins(preset_id)}
            for forbidden in (
                "tool-subagent-fork",
                "tool-subagent-list-agents",
                "tool-agent-team",
                "tool-workflow",
                "workflow-ptc",
                "tool-ralph",
            ):
                with self.subTest(preset=preset_id, row=forbidden):
                    self.assertNotIn(forbidden, ids)


class PersonaFieldTests(unittest.TestCase):
    def test_persona_uses_prefix_not_text(self) -> None:
        # dsh-persona 的 Config 是 prefix(z.string().required())，
        # 用 text: 会让整条预设行激活失败（用户机器上遗留文件就踩了这个坑）
        for preset_id in PRESET_IDS:
            with self.subTest(preset=preset_id):
                persona = _plugin(preset_id, "persona")["config"]
                self.assertIn("prefix", persona)
                self.assertNotIn("text", persona)

    def test_persona_headline_states_current_tool_counts(self) -> None:
        # 提示词里的工具数必须与实际 MCP 工具面一致，否则会诱导 agent 误判自己的能力。
        # 数字从 MCP_TOOL_DEFS 派生而非手写——本批修的正是「提示词停留在旧工具数」的漂移。
        from GalTransl.mcp_tools import MCP_TOOL_DEFS

        read_count = sum(1 for d in MCP_TOOL_DEFS if d["kind"] == "read")
        write_count = sum(1 for d in MCP_TOOL_DEFS if d["kind"] == "write")
        for preset_id in PRESET_IDS:
            with self.subTest(preset=preset_id):
                prefix = _persona(preset_id)
                self.assertIn(f"{read_count + write_count} 个", prefix)
                self.assertIn(f"{read_count} 只读", prefix)
                self.assertIn(f"{write_count} 写", prefix)

    def test_persona_does_not_claim_read_only_service(self) -> None:
        # 回归：断言「不该出现」的旧口径。仅断言关键词存在是不够的——
        # 0.5.1 的旧文案（「本服务当前只读：没有启动翻译、改配置、写字典的工具」）
        # 同样含「只读/写/H/project_dir」，老断言照样通过，挡不住它要挡的漂移。
        for preset_id in PRESET_IDS:
            prefix = _persona(preset_id)
            for stale in ("本服务当前只读", "没有启动翻译", "无写能力", "全部只读"):
                with self.subTest(preset=preset_id, stale=stale):
                    self.assertNotIn(stale, prefix)

    def test_persona_names_every_write_tool(self) -> None:
        # 写工具必须逐个点名：agent 不知道有某工具就不会用，用户会以为功能缺失
        from GalTransl.mcp_tools import MCP_TOOL_DEFS

        for preset_id in PRESET_IDS:
            prefix = _persona(preset_id)
            for definition in MCP_TOOL_DEFS:
                if definition["kind"] != "write":
                    continue
                with self.subTest(preset=preset_id, tool=definition["name"]):
                    self.assertIn(definition["name"].replace("galtransl_", ""), prefix)

    def test_persona_h_guidance_defers_to_mcp_instructions(self) -> None:
        # H 门禁可在 GalTransl 设置中开闭，服务说明随设置变化；persona 是静态文本，
        # 必须写成条件式并锚定服务说明——否则关门禁后 persona 仍在替用户拒绝 H 任务。
        for preset_id in PRESET_IDS:
            prefix = _persona(preset_id)
            with self.subTest(preset=preset_id):
                self.assertIn("以 galtransl MCP 服务的使用说明", prefix)
                self.assertIn("用户已在 GalTransl 设置中关闭 H 门禁", prefix)
                self.assertIn("不要**改写措辞重试", prefix)
                self.assertNotIn("写工具有 H 硬门禁", prefix)

    def test_persona_h_anchors_match_instruction_texts(self) -> None:
        # 跨模块互锁：persona 引用的两态锚定词必须逐字命中 build_server_instructions
        # 的对应文案（任一侧改文案忘了同步另一侧时立即暴露）
        from GalTransl.mcp_tools import build_server_instructions

        self.assertIn("禁止查看 H", build_server_instructions())
        self.assertIn(
            "用户已在 GalTransl 设置中关闭 H 门禁",
            build_server_instructions(h_gate_enabled=False),
        )
        for preset_id in PRESET_IDS:
            prefix = _persona(preset_id)
            with self.subTest(preset=preset_id):
                self.assertIn("禁止查看 H", prefix)
                self.assertIn("用户已在 GalTransl 设置中关闭 H 门禁", prefix)

    def test_persona_states_project_dir_write_validation(self) -> None:
        # L3 白名单会让写工具报错，提示词应预告这一点，避免 agent 反复试错
        for preset_id in PRESET_IDS:
            prefix = _persona(preset_id)
            with self.subTest(preset=preset_id):
                self.assertIn("project_dir", prefix)
                self.assertIn("config.inc.yaml", prefix)

    def test_persona_requires_explicit_user_request_for_submit(self) -> None:
        # submit_job 真实消耗 API 额度，措辞与 MCP 工具描述保持一致
        for preset_id in PRESET_IDS:
            with self.subTest(preset=preset_id):
                self.assertIn("用户明确要求", _persona(preset_id))

    def test_persona_documents_subagent_boundary(self) -> None:
        # 子代理的写边界是 persona 唯一的防线（dsh 侧没有按工具名禁用），必须逐字锁住。
        for preset_id in PRESET_IDS:
            prefix = _persona(preset_id)
            for expected in (
                "继承本预设",
                "深度上限 1",
                "不能再派生",
                "读不到本会话的历史",
                "send_message",
                "interrupt_agent",
                "一个 index 区间只派给一个子代理",
                "不要启停任务",
            ):
                with self.subTest(preset=preset_id, expected=expected):
                    self.assertIn(expected, prefix)
            for stale in ("没有**文件读写、Shell、子代理", "无文件/Shell/子代理"):
                with self.subTest(preset=preset_id, stale=stale):
                    self.assertNotIn(stale, prefix)

    def test_persona_defaults_subagent_to_serial(self) -> None:
        # 官方 subagent 工具硬编码 delivery:"parent"（调用即返回，没有同步参数），
        # 「默认同步」只能落在 persona 的串行约定上；任一条丢了就等于恢复并发派活。
        for preset_id in PRESET_IDS:
            prefix = _persona(preset_id)
            for expected in (
                "默认串行",
                "最多派 **1 个**子代理",
                "立即结束本轮",
                "不要在一轮里并发派多个",
                "不要轮询、休眠",
            ):
                with self.subTest(preset=preset_id, expected=expected):
                    self.assertIn(expected, prefix)
            # 反向断言：同一段里不得再宣称「并行」，否则模型会按前半句恢复并发派活
            self.assertNotIn("做并行工作", prefix)
            self.assertNotIn("并行盘点", prefix)

    def test_coder_persona_is_also_a_programmer(self) -> None:
        # coder 的身份与文件工具边界：既要说明可主动给建议，也要说清覆盖前必须先读
        prefix = _persona(CODER)
        for expected in (
            "既是翻译助手，也是程序员",
            "针对 GalTransl 程序本身的建议",
            "`read` / `read_image` / `write` / `edit`",
            "必须先 `read`",
            "backend_profiles.yaml",
            "AGENTS.md",
            # H 门禁只拦 MCP 工具，文件工具可绕过——必须写明，否则门禁承诺失效
            "文件工具**不受** H 硬门禁约束",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, prefix)
        # 反向断言：coder 有文件工具，不得沿用 translator 的「本会话没有文件读写」段
        self.assertNotIn("本会话**没有**文件读写", prefix)

    def test_translator_persona_declines_unsolicited_program_advice(self) -> None:
        # 纯翻译助手：明确不主动给程序建议，且不得出现文件工具与程序员身份
        prefix = _persona(TRANSLATOR)
        for expected in (
            "身份：只做翻译助手",
            "不要主动给出针对 GalTransl 程序的建议",
            "除非用户**主动**要求",
            "没有**文件读写",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, prefix)
        for forbidden in ("`write` / `edit`", "程序员", "tool-fs"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, prefix)

    def test_shared_tail_is_identical_across_presets(self) -> None:
        # 「硬性约束」及之后的段落（H 门禁、缓存结构、阶段判定）是两条预设共用口径，
        # 必须逐字一致——改一条忘另一条是本文件要挡的典型漂移。
        marker = "## 硬性约束"
        tails = {}
        for preset_id in PRESET_IDS:
            prefix = _persona(preset_id)
            self.assertIn(marker, prefix, f"{preset_id} 缺少共用段起点")
            tails[preset_id] = prefix[prefix.index(marker):]
        self.assertEqual(tails[CODER], tails[TRANSLATOR])

    def test_agent_instructions_restates_max_bytes(self) -> None:
        # 预设树不复用 dsh-base 的同名行，maxBytes 必须重申
        for preset_id in PRESET_IDS:
            with self.subTest(preset=preset_id):
                self.assertIn("maxBytes", _plugin(preset_id, "agent-instructions")["config"])


class MCPClientConfigTests(unittest.TestCase):
    def _mcp_rows(self) -> list:
        return [_plugin(preset_id, "mcp-galtransl")["config"] for preset_id in PRESET_IDS]

    def test_transport_is_stdio_and_named(self) -> None:
        for config in self._mcp_rows():
            with self.subTest(preset=config.get("serverName")):
                self.assertEqual(config["serverName"], "galtransl")
                self.assertEqual(config["transport"], "stdio")

    def test_paths_are_absolute(self) -> None:
        # dsh 的 subprocess provider 只对裸名查 PATH，含分隔符的相对路径会被拒
        for config in self._mcp_rows():
            for key in ("command", "cwd"):
                with self.subTest(key=key):
                    self.assertTrue(os.path.isabs(config[key]), config[key])
            for arg in config["args"]:
                with self.subTest(arg=arg):
                    self.assertTrue(os.path.isabs(arg), arg)

    def test_both_presets_point_at_the_same_server(self) -> None:
        # 两条预设各挂一份 MCP 客户端；路径漂移会让其中一个预设静默连不上
        rows = self._mcp_rows()
        self.assertEqual(len(rows), len(PRESET_IDS))
        first = rows[0]
        for config in rows[1:]:
            self.assertEqual(config["command"], first["command"])
            self.assertEqual(config["args"], first["args"])
            self.assertEqual(config["cwd"], first["cwd"])

    def test_fail_on_startup_error_is_false(self) -> None:
        # MCP 未就绪不应连累整个 dsh 启动
        for config in self._mcp_rows():
            with self.subTest(preset=config.get("serverName")):
                self.assertFalse(config["failOnStartupError"])


class GeneratedPatchSyncTests(unittest.TestCase):
    """生成物必须与真相源保持同步（本批修的就是这个漂移）。"""

    def test_generated_target_matches_source(self) -> None:
        with open(TARGET_PATH, "r", encoding="utf-8") as f:
            target = yaml.safe_load(f)
        self.assertIsInstance(target, list)
        self.assertEqual(len(target), 1)
        self.assertEqual(list(target[0]), ["insert"])
        self.assertEqual(target[0]["insert"], _source_entries())
        # 两条预设都要进 insert，缺一条就等于某个预设装不上
        self.assertEqual(len(target[0]["insert"]), len(PRESET_IDS))

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


class BundleManifestTests(unittest.TestCase):
    """`agents/dsh-preset` 同时是可直接 `plugin_manager install_bundle` 的 dsh bundle。"""

    def _manifest(self) -> dict:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_manifest_points_at_generated_patch(self) -> None:
        # 指向生成物而非真相源：bundle patch 需要 `- insert:` 包裹，
        # 真相源是裸条目清单（install_bundle 会按 patch 解析）
        bundle = self._manifest()["dsh"]["bundle"]
        self.assertEqual(bundle["patch"], "./cordis.patch.yml")
        self.assertTrue(os.path.isfile(TARGET_PATH))

    def test_manifest_declares_display_resources(self) -> None:
        # dsh 的 plugin manager / settings 从导出资源读标题、描述与图标
        manifest = self._manifest()
        exports = manifest["exports"]
        self.assertIn("./locale/*.json", exports)
        self.assertEqual(exports["./icon"], "./icon.svg")
        for required in ("cordis.patch.yml", "galtransl.preset.yml", "icon.svg"):
            with self.subTest(required=required):
                self.assertIn(required, manifest["files"])

    def test_every_locale_has_meta_title_and_description(self) -> None:
        locales = sorted(n for n in os.listdir(LOCALE_DIR) if n.endswith(".json"))
        self.assertTrue(locales, "locale/ 下没有任何 JSON 资源")
        for name in locales:
            with self.subTest(locale=name):
                with open(os.path.join(LOCALE_DIR, name), "r", encoding="utf-8") as f:
                    meta = json.load(f).get("meta", {})
                self.assertTrue(str(meta.get("title", "")).strip())
                self.assertTrue(str(meta.get("description", "")).strip())

    def test_icon_is_a_small_valid_svg(self) -> None:
        # 仅凭扩展名不能证明字节可渲染；至少校验 SVG 根元素与 dsh 的 256 KiB 上限
        size = os.path.getsize(ICON_PATH)
        self.assertLess(size, 256 * 1024)
        with open(ICON_PATH, "r", encoding="utf-8") as f:
            svg = f.read()
        self.assertTrue(svg.lstrip().startswith("<svg"))
        self.assertIn("</svg>", svg)
        self.assertIn("viewBox=\"0 0 64 64\"", svg)


if __name__ == "__main__":
    unittest.main()
