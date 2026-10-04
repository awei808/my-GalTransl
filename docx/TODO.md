本文件存储
# 存在但不急着修复的bug

- **多轮对话历史裁剪后破坏 user/assistant 交替结构（连续两条 user）**
  - 位置：`GalTransl/Backend/ForGalJsonMulitChat.py` 的 `_trim_conversation`（1511-1535 行）
  - 产生原因：裁剪逻辑为 `head = messages[:2]`（只留 `[system, u1]`）+ `tail = messages[2:]`（从首轮回复 a1 开始，长度恒为奇数 `2K-1`）+ `tail[-keep:]`（`keep = max_turns*2`，偶数）。`tail[-2m:]` 的起始索引 `(2K-1)-2m` 恒为奇数，而 tail 中奇数索引全是 user → 裁剪结果必以 user 开头，拼回 `[system, u1]` 后形成 u1 紧接另一个 user（如 `s u u a u a`）。同时 a1（首轮回复 = 首批译文）被 `head[:2]` 丢弃，"保留首轮上下文"的意图落空。破坏一旦发生便持续存在（历史变偶数长度后仍从 user 开头截取）。
  - 触发条件：
    1. 配置 `gpt.multiRoundMaxHistory`（后端 `multi_round_max_history`）> 0；**默认 0 不裁剪，且该配置无前端入口，只能手动改项目 config.yaml**；
    2. 同一文件多轮对话完成轮数 K ≥ m+1（m = 保留轮数），即第 m+1 轮完成后开始裁剪（`2K-1 > 2m`）；
    3. 仅影响实验性后端 `ForGalJsonMulitChat`（需手动注册接入，普通用户不触发）。
  - 影响：连续 user 破坏交替结构。OpenAI 兼容 API 通常容忍（不报错）但语义错乱——u1 是含完整提示词的"首轮输入"，其后紧跟无对应回复的 user，模型会误把后续批次当第一轮，翻译质量隐性下降；严格交替的 API（如 Anthropic）会直接报错导致整批失败；首轮译文 a1 丢失。
  - 修复方向（未实施）：`head = messages[:3]` 保留完整首轮 `[system, u1, a1]`，`tail = messages[3:]` 从 u2 开始（偶数长度，截取后从 user 开头，交替结构完整）；并同步调整 `len(messages) <= 3` 的提前返回边界。
- 从其他界面切回翻译控制台时，会有toast重复提示
- **字典里的纯符号分隔线被当成真实词条（译前字典不跳过，与 h 词库加载器口径不一致）**
  - 位置：数据 `Dict/01H字典_矫正_译前.txt:105`（一行 `=======================`）；加载器 `GalTransl/Dictionary.py`（译前/译后/GPT 字典解析）与 `GalTransl/Problem.py:118 load_h_check_words`
  - 产生原因：只有**整行行首 `//`** 才算注释。`====` 这类装饰分隔线既没有 `//` 也不含 `|`，于是被解析成真实词条：`src="======================="`、`dst=""`（语义是"替换为空"）、`row_type: normal`（已用 `galtransl_search_dict` 实测确认）。而 `load_h_check_words` 的文档明确写了「另跳过纯符号分隔线」——**两个加载器口径不一致**。
  - 影响：本项目该字符串不出现在正文，实际无害；但同文件里任何以 `===` 起头的分隔行都会变成生效规则，作者会以为它只是分区注释。公共字典改动影响所有项目。
  - 修复方向（未实施）：在译前/译后/GPT 字典解析中统一按注释跳过「无 `|` 且不含实义字符」的纯符号行，与 `load_h_check_words` 对齐；顺带清理该数据行。
- **400 上下文超限（Input exceeds the context limit）被当作普通 API Error 退避重试**
  - 位置：`GalTransl/Backend/BaseEngine.py` ask_chatbot 的 API Error 重试循环
  - 产生原因：BadRequestError 400「Input exceeds the context limit」是确定性失败（重试必然再失败），但重试逻辑未区分错误类型，仍按退避重试到 maxApiRetries；实测（魔王的地下要塞2 GlobalPrompt 全文分析）连续 400 仍 sleeping 2s/4s 交替重试，并伴随 timeout INFO 逐次刷屏。
  - 影响：输入超限时白白消耗退避等待与重试预算，浪费时间且日志刷屏。
  - 修复方向（未实施）：识别 400 + context limit 类错误为不可重试错误，直接失败并给出可读原因（建议调大 maxInputChars 或分文件发送）。
- **输入枚举未跳过 gt_input 下的 _excluded 等下划线开头目录，无法解析的文件每任务刷 ERROR**
  - 位置：输入文件枚举（doLLMTranslate 的 file_list 构建处，待定位）与文件插件加载 `GalTransl/Frontend/LLMTranslate.py:813`（fplugins_load_file）
  - 产生原因：用户把无法解析的 `index-会話イベント.tsv` 挪进 `gt_input/_excluded/`，但枚举仍扫入该子目录，每次任务都尝试用文件插件加载并失败 →「处理文件 …_excluded\index-会話イベント.tsv 时发生错误: …无法加载」ERROR 每任务一条。
  - 影响：每次任务固定刷一条与翻译无关的 ERROR；`_excluded` 作为人工排除区的语义实际未实现。
  - 修复方向（未实施）：文件枚举跳过 `_` 开头的子目录（_excluded 等）；或对无匹配文件插件的扩展名给出一次性提示而非每任务 ERROR。


# 存在且上线前必须修复的bug/值得优化项
- ~~首页如何开始和引导用户~~ **已完成**（首页「快速上手」三步卡：新建项目 → 配置 API → 启动翻译，各自可点击跳转；底部「查看完整指南 →」进使用指南入门篇）
- 考虑合并自动生成字典功能至全局分析中，合并批次划分至文件元数据获取中；
- 考虑取消翻译后端的强制绑多轮的限制，采用单轮对话
- 输入框代码不复用，需优化
- **新建项目向导需持续更进新流程**
- **命令行参数持续支持和完善**
- **toast提示覆盖不完全**

- 文件元数据提取后端中，新增称呼翻译策略，要求给出原文到译文的翻译 **已完成未实测**
- 翻译控制台显示哪些后端任务已完成
- 复核轮模板不使用文件元数据，使用批次元数据
- **允许字典输入正则来匹配对应词语** **已完成**（0.4.6：`re:` 前缀，全部字典类型支持；`\|` 转义竖线；非法正则回退字面量、零宽正则丢弃）
- **多轮对话中翻译需要对h区间文本的冲击力要求更强，对非h区间文本的画面感要求更强**
- **问题修复轮新增配置：是否附带上下文**
- context.ensure_global_prompt_loaded（context.py:20-44）确实无锁，if engine._global_prompt_loaded 后为同步段、无 await。多 worker 共享同一 ForFileMetaData 实例（LLMTranslate.py:1742 创建单实例），依赖"同步段内无 await 不切换协程"不变式。存量问题，非本次改动引入（context.py 既有）。
- 项目中不止一处有版本号，需统一控制。GalTransl/__init__.py、desktop/package.json、desktop/package-lock.json、desktop/src-tauri/tauri.conf.json、desktop/src-tauri/Cargo.toml

- MCP 的 list_projects 只枚举 workspace 根，建在自定义位置的项目不会出现在该 MCP 工具列表里（UI 不受影响）。
- ~~双击 AI 建议没有让用户知道有这个功能的提示~~ **已完成**（首次进入校对页延时 toast 一次性提示，localStorage 标记只提示一次；校对指南篇同步说明该交互）
- 0.5.4：新增左侧按钮“剧情路线图-简易agent界面” **已完成**（视图名 route-agent，按钮文案「路线图工作台」：RouteMapViewer 只渲染不显示源码、渲染失败/无 mermaid 退化为按路线分组的有序矩形列表；节点/矩形右键（或左键）多选文件加入底边栏 agent；AI 仅持 3 工具：read_route_map/write_route_map（整体覆盖+校验原子写）/search_file_metadata（POST /metadata/search），终端不经 AI 直连 /api/jobs；任务支持 file_filter 文件子集与 config_overrides 注入覆盖（Service→LLMTranslate 唯一过滤点，复用 globalPromptFiles 匹配口径）；JobState//runtime 透出任务范围，翻译控制台显示「文件范围: 仅 N 个文件」；执行配置按项目存 localStorage；agent 会话翻译运行中 409、同项目单飞）
  - 0.6.0 修订：**内置简易 agent 已整体移除**（`server_agent.py`、`/agent/chat`、`AgentPanel.tsx`、`AGENT_SYSTEM_PROMPT`、agent 反向互斥），路线图工作台**保留视图**但底边栏只剩「执行终端」；路线图读写逻辑迁入 `GalTransl/mcp_tools.py`（`read_route_map`/`write_route_map`），Agent 能力统一改由外置 dsh + MCP 提供。
- **0.6.0 批次 6（文档收尾与一致性）已完成**：
  - 逐项核对结果（脚本化验证，非目测）：工具数（15 = 11 + 4）与 7 处版本号（0.6.0）**全部一致**；
    `SKILL.md` 的工具名与代码 **1:1 匹配**（无错名、无漏写）；已删除产物（`server_agent` /
    `AGENT_SYSTEM_PROMPT` / `AgentPanel` / `/agent/chat`）在代码侧**无失效残留**；
    `SKILL.md` 的安全条款抽查属实；文档引用的 9 个提交号**全部真实存在**。
  - **补上最大缺口**：`README.md` / `README_EN.md` 原先**完全没提 MCP 与外置 agent**（grep 零命中），
    更新日志停在 v0.3.0。已补：功能列表第 10 条（MCP 服务 + 外置 agent）、
    「近期更新」补 v0.5.0 / v0.5.1 / v0.5.4 / v0.6.0 四条（逐条查证过代码依据）、导航加 MCP 入口。中英文同步。
  - **修正 README 安全说明的口径**：原文「接口默认可对任意路径读写文件」现在只对 **HTTP 接口**成立，
    MCP 侧另有更严约束（只操作项目目录 + 4 个写工具的路径白名单 + H 硬门禁）。
    已加说明块区分两条入口，避免读者误以为 MCP 也能任意写。
  - **标记三份已过时/已实施的计划文档**，避免误导后来的读者：
    - `docx/0.5.0修改计划.md` §5：0.6.0 批次 6/7/8（**内置 Agent 内核 + SSE + 图界面 agent 面板**）
      与现行「程序本体不内置 Agent」方向相反，已加「本节已被取代」说明并保留原表作历史记录。
    - `docx/0.5.1-MCP接入计划.md`：头部原写「**待用户裁决**」，实际早已实施完毕；
      且文中「11 个工具」「全部只读」是 0.5.1 时点状态。已改为「已实施完毕」并注明 0.6.0 增为 15 个。
    - `docx/MCP约束下发计划.md`：同上（已实施），并注明新增写工具的 `kind="write"`
      同样经派生逻辑不下发只读注解。
  - **审查发现并修掉 4 处口径错误**（都是「读者会拿到错误事实」类型）：
    - **README 把剧情路线图工作台错误归给 v0.6.0**（我自己写错的）：实测该视图由
      `3217017` 引入（当时版本 0.5.3，收尾为 **0.5.4**），而 0.6.0 做的恰恰是**反向**——
      `3efe619` 删掉了工作台里的 `AgentPanel.tsx`（-116 行）。该错误还与项目自己的
      `TODO.md:46`「0.5.4：新增…」**自相矛盾**。已改为正确归属（0.6.0 那条写成「移除内置 agent，
      视图本身保留，由 0.5.4 引入」）。
    - 连带修正：**「v0.5.x」这个粗粒度归并本身是错误来源**——它把三个版本的东西合并成一条
      （每阶段 API 配置/注入块 = **0.5.0**；`server_search.py` = **0.5.1**；
      路线分片 = **0.5.4**）。已拆成 v0.5.4 / v0.5.1 / v0.5.0 三条独立记录。
    - `docx/MCP接入指南.md:186` 的心跳**响应示例**仍写 `"tools": 11`（实际 15）；`:36` 的
      「11 工具」未加时点说明。均已修正。
    - **另自查发现两处「只读」过时文案**（审查未覆盖，因为当时还不在这批范围）：
      `run_mcp_server.py` 的 `SERVER_DESCRIPTION` 写「术语与译文检索（只读）」——
      这是**模型可见**的 serverInfo 元数据，会低估服务能力；`.mcp.json` 的 description 同样写「只读」。
      均已改为「15 个工具：11 只读检索 + 4 受限写入」，并补测试
      `test_server_description_does_not_claim_read_only` 锁定（此前 `SERVER_INSTRUCTIONS`
      有测试、`SERVER_DESCRIPTION` 没有，这正是它漂移的原因）。
  - 未改动 `docx/` 下其余历史计划/验收文档（`0.4.10` / `0.5.0验收报告` 等）——它们是当时的事实记录，
    按「保留历史原貌」处理。

- **0.6.0 批次 5（打包 MCP exe）已完成**：
  - 排查结论：构建链路（`build_release_py312.py` 的 `build_mcp` / `assemble_release` / `smoke_test_mcp`）**本已实现**，本批是补验证与收口，而非新写。
  - **冒烟测试补 `instructions` 断言**（原为 `MCP约束下发计划.md` 的 R9 / §6 第 5 项待办）：现在验 `initialize` 握手 + `instructions` **非空** + **工具数口径**。理由：`instructions` 承载 agent 侧安全契约（写工具边界 / H 门禁 / 路径白名单），打包版漏带或口径过时原先不会被构建发现。
  - 工具数**从 `GalTransl/mcp_tools.py` 的 AST 派生**（`expected_mcp_tool_count()`），不写死数字；且刻意不 import 该模块——构建脚本跑在系统 Python 下，未必装齐运行时依赖。回归测试见 `tests/test_build_release_mcp.py`（13 用例）。
  - **删除 `galtransl_mcp.spec`** 并加入 `.gitignore`（与 `galtransl_backend.spec` 对称）：它已不被任何脚本引用（构建走内联 PyInstaller 命令），且硬编码绝对路径、换机器即失效。`docx/MCP接入指南.md` 里那条「手工 PyInstaller」命令同步删除，避免给出已失效的指引。
  - **踩到并记录的坑**：`build_release_py312.py` 在模块级执行 `sys.stdout.reconfigure(encoding="utf-8")`。测试若直接 `import` 它，会改写整个测试进程的 stdio 编码，导致后续 HTTP/日志类用例批量抛 `UnicodeDecodeError`（实测把全套从 2002 passed 打成上千 errors）。已在该处加注释，并在测试里改用「ast 取函数源码后单独 exec」隔离副作用。
  - **审查发现并修掉的 3 处强度问题**（都不影响功能，但会让防护形同虚设）：
    - **派生函数在结构漂移时会返回「错值」而非 0**：审查实证 `MCP_TOOL_DEFS += [...]` 时真值 7 却返回 3，而调用方会拿这个数字去判构建失败——**用猜出来的数字报「工具数过时」比不检查更糟**。已加一致性哨兵：只认「字面量 + 单个模块级 `extend(字面量)`」这一种形态，其余（`+=` / 循环内 extend / `append` / 推导式 / 条件内 extend / 多次 extend / 内联字面量）一律降级为 0（跳过比对）并告警。八种写法已全部加测试钉住。
    - **源码字符串断言是假阳性**：原用 `assertIn("expected_mcp_tool_count()", source)` 判断冒烟是否用了它——把实现整段注释掉后测试**仍然通过**（命中的是注释里的函数名）。已改为解析 `smoke_test_mcp` 的 AST 函数体，确认真有可执行语句；负向验证：注释掉实现后由 0 失败变为 **3 失败**。
    - **工具数匹配口径有假通过风险**：原 `f"{n} 个" in instructions` 下，`n=11` 会命中「11 个只读」→ **半量错值反而 PASS**。已改为严格正则 `（\s*N\s*个工具`（与 `SERVER_INSTRUCTIONS` 实际格式一致）。
    - 另修：JSON-RPC `error` 响应原先会被误报成「缺少 tools 能力」，掩盖真因（如协议版本不支持）；现在先判 `payload["error"]` 并打印 code/message。
- **0.6.0 批次 4（dsh 预设包交付形态）已完成**：
  - 修掉一个**真实漂移缺陷**：预设本体原先在两处手工维护（`cordis.patch.yml` 与 `presets/galtransl.patch.yml`），审查时发现两份的 persona 提示词**已经不一致**，且都是 0.5.1 时代旧文案（称「11 个只读工具」「无写能力」）。
  - 改为**单一真相源**：`agents/dsh-preset/galtransl.preset.yml`（顶层数组的条目清单，即 `cordis:include` 的目标格式）。`cordis.patch.yml` 改由 `tools/build_dsh_preset.py` 生成（带「请勿手改」头），`--check` 校验同步。回归测试见 `tests/test_dsh_preset_package.py`（23 用例，含防漂移与安全性质断言）。
  - 交付形态从「用户手改自己 profile 的 `cordis.patch.yml`」改为 **home 层 `$DSH_HOME/cordis.patch.yml` + `cordis:include`**：用户只需**新建 1 个文件**，不碰自己 profile 任何现有配置，且一个文件覆盖所有 profile（home 层对全部 profile 生效，文件缺失时静默跳过不影响启动）。
  - **实测确认的两个 include 坑**（已写入 README，非推测）：① 被 include 的文件必须放在 `$DSH_HOME\profiles\` 内，否则文件里的裸包名 `@deepseek-ai/dsh-*` 解析失败报 `failed to import`（被 include 的树走宿主模块管线，不按自身目录解析）② `path` 相对 profile 目录解析而非 patch 文件，且 `Include` 要求 `file:` URL scheme，写裸 Windows 路径报 `ERR_INVALID_URL_SCHEME`。
  - 另查明：**dsh 不存在用户级预设目录**（`registry` 「neither scans directories nor accepts preset paths」）；遗留的 `$DSH_HOME\.agent-presets\` 已不被读取。官方另一条路径是把预设打包成 bundle 用 `plugin_manager` 的 `install_bundle` 安装，但它在 Host 进程执行插件代码、需 Full access，与「不给 agent 写权限」的初衷相悖，故未采用。
  - `persona` 的必填字段是 `prefix`（`z.string().required()`），**不是** `text`——用 `text` 会让整个预设行激活失败；用户机器上遗留的 `agent.cordis.yml` 有该 bug（无害，因该目录已不被读取），已加测试锁定。
  - 端到端验证：按新 README 步骤安装后启动，MCP 成功握手（`启动 stdio 服务（版本 0.6.0）`），`mcp_status.json` 显示 `tools: 15`。探针与用户 `$DSH_HOME` 均已清理复原。
- **0.6.0 批次 3（L3 路径白名单）已完成**：
  - 新增 `mcp_tools.validate_project_dir`：判据沿用 `_tool_list_projects` 的既有口径——`detect_config_file()` 解析到的配置文件必须**真实存在**。注意该函数找不到时会回退返回 `config.yaml`，故不能只用它做判定，否则任意空目录都会通过。
  - 写工具（4 个）经 `_require_write_project_dir` **硬拒绝**非法项目；实测把 `project_dir` 指向仓库根时 `save_metadata` 已被拦住，不再落盘（此即审查实证过的缺陷）。
  - 只读工具（11 个）**只告警不阻断**：在 `call_mcp_tool` 集中补 `project_dir_valid`（非法时另附 `project_dir_hint`），而非改 11 个处理器——集中一处避免遗漏，将来新增只读工具自动获得该行为。只读工具名集合以 `MCP_TOOL_DEFS` 的 `kind` 为唯一真相源（`_READ_ONLY_TOOL_NAMES`）。
  - 未纳入路径黑名单（不拒绝仓库根/盘符根/系统目录）：改用「有配置文件」这一条判据更准，加黑名单反而会误伤「工作区根下确实存在合法项目」的用法。
  - 用户提示：只读工具的 `project_dir_hint` 只用于提醒 agent 与用户确认路径，不得据此继续扩大检索范围。
  - `write_route_map` / `write_metadata` 作为**公开函数自身也调 `validate_project_dir`**：不能只依赖处理器层校验，否则未来复用方（或测试直接调用）可绕过 L3。回归测试见 `test_public_write_functions_self_validate`。
  - 判据边界：只认 `config.inc.yaml` / `config.yaml`（`.yml` 后缀不认，与全仓库既有约定一致）；判据是**文件存在性**而非内容合法性（空 config 也算合法项目）。
- **0.6.0 批次 2（写工具）已完成**：
  - `SERVER_INSTRUCTIONS` 已改写为「15 个工具：11 个只读检索 + 4 个写操作」，并明确写工具边界；`READ_ONLY_ANNOTATIONS` 上方说明同步改为按 `kind` 派生。
  - `_def()` 的 `kind` 参数（默认 `"read"`）已落地；4 个写工具均显式传 `kind="write"`，`tool_annotations()` 对 write 返回空 dict。回归测试见 `tests/test_mcp_tools.py::test_write_tools_are_never_annotated_read_only`。
  - 新增 `GalTransl/mcp_backend_client.py`：作业域 2 工具（`submit_job`/`stop_job`）经 HTTP 回后端（`JobRegistry` 是后端进程内单例，MCP 为独立进程）。这是 0.5.1「不打自家 REST」约定的**范围克制例外**（仅 2 个工具，其余 13 个仍直接读磁盘）。
  - H 硬门禁落在 `mcp_tools.enforce_h_gate`：文件维度复用 `server_cache._resolve_cache_h_ranges`、文本维度经 `server_cache._load_rebuild_deps(...)[5]` 取 H 词库 + `Problem._hit_display_words`。**写路径有门禁，读路径仍无**。
  - **已修的两处静默失效**（写工具测试当场抓到）：
    - `_entry_has_h` 原先传裸文件名，而 `_resolve_cache_h_ranges` 只认 `pass3_cache/xx.json` 形态 → 文件维度判据永远返回 False。已改为拼 `pass3_cache/{name}.json`，并加回归测试 `test_entry_has_h_uses_pass3_relative_path`。
    - `_ensure_safe_metadata_filename` 原先放过含冒号与 Windows 保留名的输入（`a:b` 会让 `os.makedirs` 抛 OSError 而非 ValueError）。已补冒号与保留名拦截 + 测试 `test_ads_and_reserved_names_rejected`。
  - 附带发现（未修，属 HTTP 侧既有问题）：`POST /metadata/filemeta|batchmeta` 直拼路径且**无文件名校验**、**非原子写**；MCP 侧已用 `.tmp`+`os.replace` 且校验更严。建议后续把 `_ensure_safe_metadata_filename` 提为共用并给 HTTP 端点补原子写。
- 各阶段的独立api页应该放在api设置页，独立api后的api检测可用性逻辑不完善
- 新增：对于行数小于20的文件，不用处理元数据
- 翻译控制台的文件进度显示不完善：当前阶段完成后且未完成所有翻译任务时，显示的是“排队中”，而不是“已完成某个阶段”
- **设置界面新增mcp门禁设置** **已完成**（独立页面：设置页「AI API 调用接口相关」分区提示词模板下方留「MCP 服务与门禁 →」入口，标题栏「翻译」菜单同步加「MCP 设置」；页面含① MCP H 门禁开关（`app_settings.json` 的 `mcpHGateEnabled`，关闭后 `enforce_h_gate` 直接放行，SERVER_INSTRUCTIONS 第 4 条随开关改写为放行说明）；② MCP 工具逐个开关（`mcpDisabledTools` 黑名单，工具清单经新端点 `/api/mcp-tools` 下发供渲染；`tools/list`/调用分发/心跳均按启用集合过滤，调用被禁工具返回「已由用户禁用」，实时读设置无需重启；instructions/工具计数为 MCP 进程启动快照，重开 agent 会话刷新）。设置文件经 `resolve_app_dir()` 与打包版同目录，MCP 独立进程与后端读同一份；dsh 预设 persona 的 H 段同步改为条件式（以 MCP 服务说明为准，随门禁开关联动，未见到说明时保守按禁止处理），工具面段注明实际可用工具以会话挂载清单为准）

# 未来的大更新项
- 0.4.1：跟进原项目进度，对原先缺失的功能修补，追加类似上有项目的视觉效果
- 0.4.2：翻译控制台视觉效果总更新、字典界面视觉效果更新、首页新增“新建项目向导”
- 0.4.3：恢复命令行版本的适配 **已完成**（CLI 恢复进度条/交互提示与配置文件自动探测，新增 -c/--config、--version 参数；补齐 CLI 工具引擎 recheck 全部重检 / check-batch-size 批次划分预检 / build-output 构建输出，新增 rebuildr / rebuilda 缓存重建引擎），补充翻译指南和项目地址的内容 **已完成**（0.6.x：新增 guides/ 使用指南视图——后端 /api/guides 只读端点 + 前端 guide 视图，帮助菜单「翻译指南 / 项目地址 / 关于」三项接通，四页页内「指南」入口 + 首页上手卡）
- 0.4.4：已有后端完善：对元数据的消费采用按需注入而非全量（如不将全局分析中的所有角色形象注入，仅注入文件元数据中包含的角色的角色形象）
- 0.4.5：在翻译结果后处理阶段划分ai初步处理阶段：处理换行等基本问题、标注疑似错误、修正翻译风格；新增后端“词语色彩一致性检查”；允许每个阶段接入不同api接口
- 0.4.6：字典支持正则且不会重复检查有重叠的词语 **已完成**（全部字典类型支持 `re:` 正则；`check_dic_use` 消费式去重，新增 `dictionary.skipOverlapCheck` 配置可回退旧口径；清理废弃代码：file_metadata 死注入链、/files 旧元数据兼容块、Sakura 端点队列死代码、相关过时措辞）；
- 0.4.7：校对审核界面的元数据模式改为json字段对应式修改（键、值均可修改），译文条目模式添加新功能：双击空白处，将本段json发送给ai，让ai提供修改后的译文。（暂定右侧侧边栏出现类似vscode的右侧边栏的agent显示）；撤回重做机制触发时，出现toast提示 **已完成**（元数据键值编辑器 MetaKeyValueEditor：键值均可增删改、宽松解析+类型徽标+回程类型安全；双击 AI 建议：POST /review/ai-suggest（ReviewAssist.py 纯函数模块，一次性不落盘）+ 校对页建议面板，采纳写入 alt_dst 走既有交换/撤销链路，vscode 式 agent 侧栏归 0.5.0；同文件内撤销/重做触发时 toast.info 提示操作描述）
- 0.4.8：更进上游除了agent模式外的改动 **已完成**（
  批次 B：从上游直接抄的后端小修（低风险）项
    	上游 commit	内容	量级
  B1	ca7cf71	缺控制符检测改按子串包含，消除 [汉字/罗马字] 注音误报（Problem.py + test_problem_control_symbols）	~10 行 **已完成**
  B2	66d1584	check-model 容忍 provider 对 max_tokens 的下限要求（COpenAI.py 可用性检测摘参重试 + test_openai_token_availability；本地 /check-model 走 COpenAI.checkTokenAvailablity，上游同改在 COpenAI）	~10 行 **已完成**
  B3	b8bfbae	Cache.py 两处 shutil.move → os.replace + cleanup_stale_cache_temp_files 启动清扫（Service.run_job_async 调用）+ server /cache 与 _build_cache_tree 列表过滤 .json.tmp（test_cache_temp_files）	~15 行 **已完成**
  B4	424469a	后端配置令牌卡片加“上下文大小”字段（默认 128000，BackendProfilesPage 适配本地页面结构 + 默认模板/sampleProject + backendProfileContextWindow 测试）——也是 0.5.0 agent 上下文指示器的前置	后端 schema + Solid 设置页小改 **已完成**
  B5	b5daa87 部分	缺 proofread_dst 的旧缓存误命中修复——已验证本地存在同款问题（Cache.py `_cache_get(...) == ""`，字段缺失被当成有校对稿跳过检查），一行修复 + test_cache_proofread_hit_rules 锁行为	**已完成**
  B6	864f376	重建引擎 shutdown 假警告修复——对照本地重建引擎：RebuildTranslate 已有空操作 shutdown 覆写，无同款问题，跳过	**已验证，跳过**
  B7	ab62299/3511234	翻译规范“日译中_增强v2”补“控制符保留”“禁止日文残留”两条 + 向导默认规范改 v2（wizardExplicitSaveButton 测试同步）	纯文本，直接抄 **已完成**），
  B8  将项目里的前端深色/浅色鲜艳显示模式统一为与上游相同的实现（鲜艳圆角取上游 control/card/panel=14/18/24、浅色 shadow-lg 取上游 shadow-panel、主按钮改上游渐变+三档投影；emoji 图标替换移除对齐上游 SVG；侧栏毛玻璃为本地特色保留），本身深色/浅色鲜艳显示模式就是为了保持上游页面设计风格才有的，上游已更新，这两个也要更新 **已完成**（iconEmoji 测试改写为“无 emoji 残留”回归锁）
- 0.4.9：解耦「多轮翻译后端」 **已完成**（
  ① 多轮翻译后端改名「翻译后端」：类/模块 ForGalJsonMulitChat → ForGalJsonTranslate，引擎 ID ForGal-json-translate，下拉只展示新 ID；旧名 ForGal-json-multi-chat 经 TRANSLATOR_ALIASES 兼容旧配置/旧任务（Runner 统一解析 select_translator、init_gptapi 兜底、/check-model 解析、Service 提示词覆盖键双向兼容）；
  ② 多轮对话后端类与翻译解耦：新建 Backend/Conversation.py，MultiRoundChatMixin 承载按文件隔离 conversations/历史裁剪/失败强制首轮标记/multiRoundMaxHistory；
  ③ 取消翻译后端强制绑多轮：gpt.chatMode=multi(默认)/single；单轮每批请求独立（全量提示词+元数据+术语+规范每请求注入），补实现 _format_restore_context_line 并接通 restore_context（contextNum 句滚动上下文，sig 固定 "old" 的 jsonline + 代码块包装，恢复历史单轮后端口径）注入 [history_result]；jailbreak 预填充按请求生效；解析失败直接重试；单轮专用提示词 FORGAL_JSON_TRANS_PROMPT_SINGLE（仅改写「历史上下文」任务段语义，其余骨架与多轮一致），模板 override 对两套同等生效；自定义模板缺 [history_result] 占位符且历史非空时 warning 一次；
  ④ 前端配置界面：项目配置页「翻译后端-对话翻译」分区落地对话模式选择（schema 注释驱动枚举下拉 + token 成本提示 + multi/single 友好名），默认模板加 gpt.chatMode（旧项目经默认值合并可见）；
  ⑤ 原计划的「所有后端均允许自由选择多轮/单轮对话」顺延至后续版本（0.4.9 仅翻译后端支持）；「超过3000行代码文件重构」移至 0.4.10）
- 0.4.10：大文件重构：GalTransl/server.py（5781 行）按功能域拆分为多模块（内嵌 Web UI/配置schema/缓存与输出构建/字典/后端档案/JobRegistry/项目脚手架，server.py 保留路由分发与兼容 re-export）；LLMTranslate.py（2800 行）与 desktop ReviewPage.tsx（2659 行）同步重构（原 0.4.9 的「超过3000行重构」移入本版）
- 0.5.0：新增左侧按钮“图-工作台界面”，用于承载全新界面：整个页面基于mermaid渲染，主界面显示为多个文件矩形，可通过mermaid可视化连接构建剧情路线图。可以多选文件（暂定右键多选），添加到工作台（暂定显示在右侧边栏），选择对应指令调用翻译后端执行所有满足条件的翻译流程后端；全局分析后端条件放宽，不再限制项目全部文件，可执行对多个文件（可能只是一条路线）的分析；剧情路线图可由用户自己划分（即mermaid连接构建路线图），后端可只负责对每条线的剧情梳理；流水线不再固定，可修改，每个后端注入哪些内容也可修改（有gui）。

# 需修复的技术债务
- 后端文件存在冗余未复用代码和层次不清晰的代码。未来需要提取公用函数并重新划分层级
- ~~后端退化读取的文件路径（如：项目根目录 `FileMetaData.json`、`gt_input/FileMetaData.json`）是修改架构不完全的产物，未及时删除~~ **0.4.6 已清理**（死注入链与 /files 兼容块已删；`get_file_list` 对残留文件的排除保留）
- 多个依赖存在不兼容（原项目就有，暂时无影响）：
  - Rust 侧：windows crate 0.58 与 0.61.3 双主版本并存
  - Rust 侧：HTML/CSS 解析栈新旧两代分裂
  - Python 侧开发环境与构建环境不一致： openai 2.44 vs 2.53、httpx-aiohttp 0.1.12 vs 0.2.0、pyreqwest 0.12.0 vs 0.12.2
  -  Python 侧隐式必需依赖：httpx-aiohttp
  - 冗余依赖：tiktoken、fasttext-predict 
  -  Rust 声明滞后
- 输入框换行逻辑未提取成公用代码
- 存在多个超过2000行代码的文件
- 基类架构不完善，且后端文件架构不一致
- Cache.py `get_transCache_from_json` 重试失败句分支里 `cache_dict[cache_key]["proofread_by"]` 为直接下标访问：条目缺 `proofread_by` 字段、且 `retry_failed` 开启、pre_dst 含 "(Failed)"、又有校对稿（no_proofread=False 绕过短路）时会在读缓存阶段 KeyError。0.4.8 审查发现（0.4.8 的 proofread_dst 默认值修复反而降低了触发概率：缺校对字段的条目现在都走 no_proofread=True 短路），修法为改用 `_cache_get(cache_dict[cache_key], "proofread_by", "")`

# 已知但不打算修复的问题
- 文件读取和写入默认不鉴权，任意路径读取、读写文件，原因：原项目就有这个问题；后端只绑 127.0.0.1 + CORS 白名单 → 远程攻击面小；
- api密钥明文显示，原因：原项目就有这个问题；最多造成密钥泄漏，且只有本机被恶意入侵或主动分享项目配置才会造成密钥泄漏；
- 开启动态句数 + 多 worker时，多轮翻译后端动态句数调节失真

# 提示词相关
- **规定AI的立场，本质上就是为它设定一个清晰的“翻译目的”。核心方法是在Prompt中明确以下几点：**

明确目标受众（为谁译）：在Prompt的开头就指明目标群体，例如：“你是一位专业的游戏本地化专家，请将以下日文视觉小说文本翻译成简体中文，目标受众是中国大陆的年轻二次元玩家。”

定义翻译风格（怎么译）：提供风格指南，甚至角色设定资料。例如：“请采用轻松、口语化的现代汉语风格，保留‘学长’、‘学姐’等日式校园称呼。男主角‘桐人’的语气要冷淡简洁，女主角‘亚丝娜’则要温柔礼貌。”

设定具体约束（有哪些限制）：明确技术或内容上的要求。例如：“请确保所有译文能适配每行最多15个汉字的对话框。保持所有人名、地名、技能名前后统一，术语表见附录。”

提供参考样例（照这个译）：给出1-2句你理想中的译文作为示例，让AI迅速理解你对“信达雅”的平衡点，这比抽象的描述更有效。

明确禁止事项（别这么译）：直接告诉AI什么不能做，例如：“禁止使用网络流行语或梗”、“禁止添加原文没有的解释性文字”。