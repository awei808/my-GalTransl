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
- ~~**「写工具都带 H 硬门禁」的夸大表述散落在多处（含运行时下发给 agent 的服务说明）**~~ **已完成**（口径统一为按覆盖面表述：「7 个写工具均带**路径白名单**；其中 3 个内容写入类工具（路线图/元数据/项目字典）另带 **H 硬门禁**」。运行时契约：`GalTransl/mcp_tools.py` 的 `build_server_instructions()` 第 4 条改写为点名 `write_route_map`/`save_metadata`/`write_glossary` 受门禁，并写明 `mark_skip_check`/`swap_alt_translation` 只改标志位或对调既有译文、不引入新文本故**不受门禁约束**（**决策：交换既有 H 译文属允许操作，不补门禁**，理由写进 `enforce_h_gate` 文档串与 SKILL.md §1）；金样本 `tests/test_mcp_gate_settings.py::test_default_matches_golden_text` 同步逐字改写。新增测例 `HGateScopeTests`：用源码 AST 派生每个写工具 handler 的调用闭包，锁定「第 4 条点名的工具 == 调 `enforce_h_gate` 的 ∪ 调 `_modify_cache_entry` 的」，并锁旧全称句「写工具对 H 内容有硬门禁」不得回归——今后任一侧实现变化而未同步文案即失败。instructions 长度上限 2000→2300 字节（`test_mcp_tools` 与 `test_mcp_gate_settings` 两处，注释说明该上限只挡无界膨胀、非协议限制）。用户可见文档同步：`README.md`、`README_EN.md`、`guides/07-mcp.md`（H 门禁一节改写为「只覆盖 3 个工具 + 两个缓存工具与作业域不受约束」）、`skills/galtransl-mcp/SKILL.md`（§1 事实依据、§4 表头、工具表小节标题、注意事项四处）、`docx/MCP接入指南.md`（§4 表头 + 逐行标注受/不受门禁）、`agents/dsh-preset/README.md`（三层约束里的 H 门禁层）；前端 `desktop/src/pages/mcp/McpSettingsPage.tsx` 的 H 门禁分区说明与 `types.ts` 的字段注释同步限定到 3 个工具）
- ~~**MCP 约束文案点名的 `skills/galtransl-mcp/SKILL.md` 不在便携版发布包内（查档确认）**~~ **已完成**（选定「随包分发」方案：`build_release_py312.py` 新增 `SKILLS_DIR = ROOT / "skills"`，`assemble_release` 在 `if SKILLS_DIR.exists()` 正文里 `copy_dir_filtered(SKILLS_DIR, BUILD_DIR / "skills")`，缺失时进 `missing` 走 fail-closed 完整性校验（与 `plugins/`、`Dict/`、`guides/` 同口径）；冒烟新增 `_check_instructions_doc_ships()`，以发布根目录为 cwd（与 MCP 启动 cwd 一致）取出 instructions 里的 `skills/…md` 路径逐个复核——路径经 `resolve()` 后须落在发布目录内且是文件（越界写法与同名目录都不算通过），缺文件即阻断构建，未点名路径则告警放行；产出目录清单与「用法」段同步（用法原先仍在教人跑旧 `build_release.py`，并注明 `--allow-incomplete` 不放行冒烟里的内容校验）。**并修掉运行期文案的定位问题**：instructions 末句「完整约束见随项目分发的 …」→「完整约束见 GalTransl 程序目录下的 …」（agent 的 cwd 不是程序目录，只给相对路径无法定位），金样本与 `docx/MCP接入指南.md` 的逐字引用同步；dsh persona 与 `guides/07-mcp.md` 同步为「GalTransl 程序目录下 …（随发布包分发）」并重生成 `cordis.patch.yml`。测例 `tests/test_build_release_mcp.py::SkillsDocShipsTests`（8 条）把链路锁死：文案点名（从 `SERVER_INSTRUCTIONS` 正则取路径）→ 仓库内该文件存在 → `assemble_release` 在 exists 分支正文里复制到 `BUILD_DIR/<同名目录>`、缺失进 `missing` → `smoke_test_mcp` 以 `if not …: return False` 阻断；另 4 条为复核函数的行为测例（有文件 / 缺文件 / 越界三种写法 / 未点名），避免正则失效或把关方向写反时静默放行。变异验证（删复制步骤、if/else 对调、复制目标换成别的目录、正则永不匹配、阻断条件取反）均被挡下。未做：旧 `build_release.py` 不同步此改动（0.5.1 起即按其「未同步」定位处理）；根 `AGENTS.md` 构建命令已就地改为 `python build_release_py312.py`（该文件被 .gitignore 忽略，不入库））
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
- **子目录输入文件的批次元数据键口径不一致（basename vs 扁平化路径），带子目录的项目里批次标注全程失效**
  - 位置：写入侧 `GalTransl/Frontend/llm_runtime.py` 的 `_run_meta_worker_pool`（`fname = os_basename(file_path)` → `pass2_cache/{basename}.batch.json`，`id` 也是 basename）；读取侧 `GalTransl/Backend/ForGalJsonTranslate.py` 的 `_resolve_batch_metadata`（先按传入名查、再 `strip_chunk_suffix` 剥 `_N` 分批后缀，不做 `-}` 还原）
  - 产生原因：阶段 8 与独立引擎传入的 filename 是「相对输入目录路径 + 分隔符替换为 `-}`」（`llm_postprocess._improve_single_file` 与 `llm_standalone` 的 `file_name` 拼接），翻译阶段的切块键同为扁平化形态；而批次元数据按 basename 存。**输入目录只有一个层级时两者恰好相同**，含子目录（`sub/a.json` → 传入 `sub-}a.json`）时读取侧全部落空。
  - 影响：带子目录的项目里，翻译轮的 `[batch_metadata]` 区间指导与 tonecheck 的区间色彩标注始终注入为空（走各自降级路径：翻译退化为按 `numPerRequestTranslate` 的 count 切批、tonecheck 自本次解绑后转为原文口径并打 INFO，行为安全）；无报错、无 WARNING，只有 `batchmeta=✗` 的 DEBUG 可见，容易被误判为「模型没生成标注」。
  - 修复方向（未实施）：统一键口径——写入侧改存扁平化名（`file_path` 相对化 + `-}`）或在读取侧做 `-}` → 分隔符的双向还原（`_resolve_display`/`cache_file_display_map` 已有同类还原逻辑可复用）；改动涉及 pass2 产物命名，需兼容既有项目的旧键（两种键都尝试命中）。
- **`PUT /api/app-settings` 的非法载荷返回 500 而非 400（400-vs-500 家族，两例）**
  - 位置：兜底在 `GalTransl/server_handlers_root.py` 的 `PUT /api/app-settings` 分支（`save_app_settings` 调用后的 `except Exception`）；两个来源分别是 `GalTransl/AppSettings.py:45`（`max(1, int(_raw_or_default(source, "maxConcurrentJobs")))`）与 `GalTransl/server.py:262-268`（`_read_json_body` 对非对象 JSON 抛 `ValueError`）
  - 产生原因：`int("abc")` 抛 ValueError；`_read_json_body` 抛的也是裸 ValueError（`except json.JSONDecodeError` 接不住——JSONDecodeError 是 ValueError 的子类，反向不成立），两者都落到 handler 的 `except Exception` 兜底 → 500。2026-10-15 的写盘留痕改动只统一了「缺键/显式 null 回落默认值」（`_raw_or_default`），非数值字符串等非法类型仍是原状。
  - 实测（临时设置目录 + 无令牌的进程内测试 server）：`body=[]` → 500 `failed to write app settings: json body must be an object`；`{"maxConcurrentJobs":"abc"}` → 500 `invalid literal for int() with base 10: 'abc'`；`body={}` → 200。
  - 影响：客户端拿到 500 与实现细节回声，语义上应为 400；设置不落盘（无副作用），故不紧急。
  - 修复方向（未实施）：handler 内把 `ValueError/TypeError/KeyError` 归为 400（`invalid settings payload`），只把真正的落盘 `OSError` 留给 500；或在 `_normalize_settings` 内对非法类型也回落默认值（与 null 同口径，代价是静默吞掉客户端拼写错误）。
- **`load_app_settings` 的「文件不存在/不可读」分支返回与全局默认值共享的 `mcpDisabledTools` 列表**
  - 位置：`GalTransl/AppSettings.py:70-80`（三处 `return dict(DEFAULT_APP_SETTINGS)`）
  - 产生原因：`dict(...)` 为浅拷贝，`mcpDisabledTools` 是 list → 与 `DEFAULT_APP_SETTINGS["mcpDisabledTools"]` 同一对象；而走 `_normalize_settings` 的分支（:78）经 `_normalize_disabled_tools` 的 `list(seen)` 新建列表，两条路径口径不一致。
  - 影响：当前无调用方原地修改（`mcp_tools.py` 转 `frozenset`、`run_mcp_server.py:69` 只读），无现实风险；将来任一处 `settings["mcpDisabledTools"].append(...)` 会污染进程内全局默认值，并波及后续所有「设置文件缺失/不可读」场景。
  - 修复方向（未实施）：三处返回统一构造深拷贝（如 `{**DEFAULT_APP_SETTINGS, "mcpDisabledTools": list(DEFAULT_APP_SETTINGS["mcpDisabledTools"])}`），或抽一个 `_default_settings_copy()` 供三处共用。


# 存在且上线前必须修复的bug/值得优化项
- 考虑合并自动生成字典功能至全局分析中，合并批次划分至文件元数据获取中；
- 输入框代码不复用，需优化
- **新建项目向导需持续更进新流程**
- **命令行参数持续支持和完善**
- **toast提示覆盖不完全**
- 翻译控制台显示哪些后端任务已完成
- 复核轮模板不使用文件元数据，使用批次元数据
- **允许字典输入正则来匹配对应词语** **已完成**（0.4.6：`re:` 前缀，全部字典类型支持；`\|` 转义竖线；非法正则回退字面量、零宽正则丢弃）
- **多轮对话中翻译需要对h区间文本的冲击力要求更强，对非h区间文本的画面感要求更强**
- **问题修复轮新增配置：是否附带上下文**
- context.ensure_global_prompt_loaded（context.py:20-44）确实无锁，if engine._global_prompt_loaded 后为同步段、无 await。多 worker 共享同一 ForFileMetaData 实例（LLMTranslate.py:1742 创建单实例），依赖"同步段内无 await 不切换协程"不变式。存量问题，非本次改动引入（context.py 既有）。
- 项目中不止一处有版本号，需统一控制。GalTransl/__init__.py、desktop/package.json、desktop/package-lock.json、desktop/src-tauri/tauri.conf.json、desktop/src-tauri/Cargo.toml



- 后端对结果预览的支持不完善，一些后端的结果不会在结果预览中显示
- 翻译控制台-当前提示词的状态指示灯中，黑色指示灯没有类似其他指示灯的“等待首字”“取消”的文字说明，需要显示为“已完成+耗时”
- 结果预览的更新时机可能过于频繁（需先将上一条bug修好并测试后再确认这个问题是否真实存在）
- **新增问题检测项：节奏单一**，针对部分句子换行符前后字数相等或相差不大的情况，句子节奏单一且大多是冗余的词语。（部分模型，有将换行符前后译文长度相等的倾向，dsv4.1最严重）
- **新增mcp工具，让ai直接改动缓存文件**
- [发布说明] d7a5833 后，以字面 <br> 为换行约定的项目行为有变化（缓存与输出现在是真实换行而非 <br>），建议写进更新日志。
- MCP 的 list_projects 只枚举 workspace 根，建在自定义位置的项目不会出现在该 MCP 工具列表里（UI 不受影响）。
- 各阶段的独立api页应该放在api设置页，独立api后的api检测可用性逻辑不完善

# TODO

## 后端（GalTransl/server_handlers_*）

- [ ] **part2 路由委托后无条件再补一次 404**（`server_handlers_project.py:1499`）：`route_project_api_part2(...)` 无返回值，调用方紧接着无条件 `_send_json({"error": "not found"}, 404)`。凡由 part2 处理的路径（`/progress`、`/problems`、`/cache/search` 等）都会在正确响应体之后再写一段 404 响应体。当前之所以没暴露：默认协议为 HTTP/1.0（`Content-Length` 决定一次响应边界，客户端读完第一段即返回），且前端/测试都只读第一段。风险：一旦启用 HTTP/1.1 keep-alive、改用严格解析的客户端或代理，多出的响应体会被当成下一个请求的响应（串响应/协议错误）。建议让 `route_project_api_part2` 返回「是否已处理」布尔值，调用方据此决定是否发 404（对齐 `server_cache` 等模块已有的返回值风格）。
- [ ] **`server_handlers_project2.py` import 块存在死导入**（AST 精确核对，仅出现在 import 行）：`unquote`、`_safe_project_dir`、`decode_project_dir`、`encode_project_dir`、`_deep_merge_defaults`、`_get_default_config`、`_REPLACE_FIELD_REJECTED_MSG`、`_build_cache_tree`、`_build_project_output`、`_cache_entry_key`、`_entry_modified`、`_fmt_indices`、`_list_dir_entries`、`_resolve_cache_h_ranges`。无行为影响，属拆分大文件时的残留；清理时顺带核对其它 `server_handlers_*` 拆分文件是否有同类残留（本次未逐个核查）。
- [ ] **`/alt-translations` 未统一缓存文件枚举口径**（`server_handlers_project2.py:836`）：仍用裸 `os.walk` + `name.endswith(".json")`，与 `/problems`、`/progress`、`/runtime` 进度、全量重检已统一的 `_collect_cache_files` 不一致。当前实际影响近零（pass0 元数据/断点分片均为 dict 形状、快照为 `.bak`，`_collect_alt` 对非 list 提前返回），但建议后续一并改用 `_collect_cache_files`，避免再次出现「问题统计有、其它端点无」的漂移。

## 前端（desktop/src/pages/review）

- [ ] **cacheWatcher 自动刷新 effect 为死代码**（`ReviewPage.tsx` 约 909-922 行）：`reviewMode() === "translate"` 与 `modeInfoOf(file).mode !== "translate"` 两道守卫作用在同一 `activeFilePath` 上、语义矛盾，任何情况下都会在 `loadFile` 调用前 return——"外部改动自动刷新译文文件"从未生效（cacheVersion 轮询形同虚设，仅手动「刷新」按钮可用）。需排查是历史语义变更后的残留还是有意禁用，再决定删除或修正守卫。
- [ ] **modeInfoOf 分类表不认识 `gendic_cache/`、`aftertrans_cache/` 等目录**（`ReviewPage.tsx` `modeInfoOf`）：这类目录下的单对象 JSON 文件会被默认判为 translate 模式，若出现在缓存树且可点击，会误入 `loadFile`，以字典形状 entries 触发 `snapshotKey` 的 `list.map` TypeError。可达性取决于 `/files` 的 cache_files 列举范围，待核实后在分类表中补充对应目录或对不可编辑目录做入口屏蔽。

## 跨端（翻译后端展示元数据）

- [ ] **翻译后端展示元数据收口到后端**：引擎的「中文短名 / 分组 / 组内顺序」目前散在前端两张表里——`desktop/src/lib/engineDisplay.ts`（ENGINE_LABELS / ENGINE_HINTS / ENGINE_GROUPS / ENGINE_ORDER）与 `desktop/src/pages/routeAgent/plan.ts` 的 `INSTRUCTIONS`（工作台指令标签，含「重生成路线图」等工作台专用措辞）。同一引擎两处叫法不一致（如 ForSemCheck：语义检测 vs 语义复核；ForJPResidue：残留日文修复 vs 残留修复），`/api/translators` 又只下发「引擎名 + 一段长描述」，前端拿到什么就只能显示什么。建议在 `GalTransl/__init__.py` 的 `TRANSLATOR_SUPPORTED` 每项补 `label`（中文短名）、`group`（分组）、`order`（组内顺序），经 `/api/translators`（`server_handlers_root.py`）下发；前端 `engineDisplay.ts` 只保留纯派生逻辑（过滤/分组/字段缺失回退），routeAgent 指令标签改为引用同一来源——新增引擎只改后端一处，对齐 pipeline_stages「后端唯一真源 + 前端派生」的既有做法。注意：新增字段是 `{name, description}` 的向后兼容增量，需同步 `desktop/src/lib/api/types.ts` 的 `TranslatorOption`、回退口径（字段缺失时仍回退引擎名 + 截断描述），并把两侧测试改为直接校验载荷（`desktop/src/__tests__/engineDisplay.test.ts`、`tests/test_engine_display_consistency.py` 现靠文本解析 TS 与硬编码隐藏清单，届时可一并简化）。
- [ ] **隐藏引擎清单在后端与测试间重复**：`tests/test_engine_display_consistency.py` 的 `_HIDDEN_EXTRA` 与 `GalTransl/server_handlers_root.py` 中 `/api/translators` 的内联 `_hidden_translators` 是同一份口径的两处抄写（工具引擎已 import `UTILITY_ENGINES` 共用）。后端新增隐藏引擎时测试会误报「请给不展示的引擎补表」。建议把该集合提为后端常量（`GalTransl/__init__.py` 或 `UtilityEngines.py`）供两侧 import，顺带收进上一条的元数据下发改动里。


# 未来的大更新项


# 需修复的技术债务
- `GalTransl/server.py:32` 的 `from GalTransl.AppSettings import load_app_settings, save_app_settings` 中 `save_app_settings` 已无使用（该名字在本文件全文仅此一处出现）；直接删掉即可，`load_app_settings` 的调用点不要动（`/api/app-settings` GET 与前端日志判定仍在用它）。
- 后端文件存在冗余未复用代码和层次不清晰的代码。未来需要提取公用函数并重新划分层级
**0.4.6 已清理**（死注入链与 /files 兼容块已删；`get_file_list` 对残留文件的排除保留）
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