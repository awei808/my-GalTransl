# TODO

待办、已知 bug 与技术债务清单。已完成条目直接删除，实现记录查 git 历史。

# 存在但不急着修复的bug

- **多轮对话历史裁剪后破坏 user/assistant 交替结构（连续两条 user）**
  - 位置：`GalTransl/Backend/ForGalJsonMulitChat.py` 的 `_trim_conversation`（1511-1535 行）
  - 要点：`head=messages[:2]` + `tail[-2m:]`（tail 长度恒为奇数）→ 裁剪结果必以 user 开头，拼回后形成连续两条 user；首轮回复 a1 也被丢弃。
  - 触发：需 `multiRoundMaxHistory>0`（默认 0 不裁剪，且无前端入口，只能手改 config.yaml）+ 仅影响实验性后端 ForGalJsonMulitChat。
  - 影响：交替结构破坏，OpenAI 兼容 API 不报错但语义错乱、质量隐性下降；严格交替的 API（Anthropic）会直接报错导致整批失败。
  - 修复方向（未实施）：`head=messages[:3]` 保留完整首轮、`tail=messages[3:]`，同步调整 `len(messages) <= 3` 的提前返回边界。
- 从其他界面切回翻译控制台时，会有 toast 重复提示
- **字典里的纯符号分隔线被当成真实词条（与 h 词库加载器口径不一致）**
  - 位置：数据 `Dict/01H字典_矫正_译前.txt:105`（一行 `====`）；加载器 `GalTransl/Dictionary.py`（译前/译后/GPT 字典解析）与 `GalTransl/Problem.py:118 load_h_check_words`
  - 要点：只有整行行首 `//` 才算注释，`====` 这类分隔线被解析成 `src="===…"`、`dst=""` 的真实词条；而 `load_h_check_words` 明确写了「跳过纯符号分隔线」。已用 `galtransl_search_dict` 实测确认。
  - 影响：本项目该串不出现在正文，实际无害；但公共字典里任何分隔行都会变成生效规则，作者会以为只是分区注释。
  - 修复方向（未实施）：字典解析统一跳过「无 `|` 且不含实义字符」的纯符号行，与 `load_h_check_words` 对齐；顺带清理该数据行。
- **400 上下文超限（Input exceeds the context limit）被当作普通 API Error 退避重试**
  - 位置：`GalTransl/Backend/BaseEngine.py` ask_chatbot 的 API Error 重试循环
  - 要点：确定性失败未与可重试错误区分，仍退避重试到 maxApiRetries（实测连续 400 仍 2s/4s 交替重试，timeout INFO 逐次刷屏）。
  - 影响：白白消耗退避等待与重试预算，浪费时间且日志刷屏。
  - 修复方向（未实施）：识别 400 + context limit 为不可重试，直接失败并给出可读原因（建议调大 maxInputChars 或分文件发送）。
- **子目录输入文件的批次元数据键口径不一致（basename vs 扁平化路径）**
  - 位置：写入侧 `GalTransl/Frontend/llm_runtime.py` 的 `_run_meta_worker_pool`（按 basename 存）；读取侧 `GalTransl/Backend/ForGalJsonTranslate.py` 的 `_resolve_batch_metadata`（按传入名查，剥 `_N` 后缀，不做 `-}` 还原）
  - 要点：翻译/后处理传入的是「相对路径 + 分隔符替换为 `-}`」的扁平名，批次元数据却按 basename 存；单层级目录两者恰好相同，含子目录时读取侧全部落空。
  - 影响：带子目录项目里 `[batch_metadata]` 区间指导与 tonecheck 色彩标注全程为空（各自走降级路径，行为安全）；无报错无 WARNING，只有 DEBUG `batchmeta=✗` 可见，易误判为「模型没生成标注」。
  - 修复方向（未实施）：统一键口径——写入改存扁平化名，或读取侧做 `-}` → 分隔符双向还原（可复用 `_resolve_display`/`cache_file_display_map` 逻辑）；需兼容既有项目的旧键（两种键都尝试命中）。
- **`PUT /api/app-settings` 的非法载荷返回 500 而非 400**
  - 位置：兜底在 `GalTransl/server_handlers_root.py` 的 PUT 分支；两个来源是 `GalTransl/AppSettings.py:45`（`int("abc")` 抛 ValueError）与 `GalTransl/server.py:262-268`（`_read_json_body` 抛裸 ValueError，`except json.JSONDecodeError` 接不住）
  - 实测：`body=[]` → 500；`{"maxConcurrentJobs":"abc"}` → 500；`body={}` → 200。
  - 影响：语义上应为 400，客户端却拿到 500 与实现细节回声；设置不落盘、无副作用，故不紧急。
  - 修复方向（未实施）：handler 内把 `ValueError/TypeError/KeyError` 归为 400（`invalid settings payload`），只把落盘 `OSError` 留给 500。
- **`load_app_settings` 的兜底分支返回与全局默认值共享的 `mcpDisabledTools` 列表**
  - 位置：`GalTransl/AppSettings.py:70-80`（三处 `return dict(DEFAULT_APP_SETTINGS)`）
  - 要点：`dict(...)` 是浅拷贝，list 字段与全局默认同一对象；走 `_normalize_settings` 的分支（:78）则经 `list(seen)` 新建，两条路径口径不一致。
  - 影响：当前无调用方原地修改，无现实风险；将来任一处 `append` 会污染进程内全局默认值。
  - 修复方向（未实施）：三处统一深拷贝，或抽一个 `_default_settings_copy()` 共用。

# 存在且上线前必须修复的bug/值得优化项

- 考虑合并自动生成字典功能至全局分析，合并批次划分至文件元数据获取
- 输入框代码不复用，需优化
- **新建项目向导需持续跟进新流程**
- **命令行参数持续支持和完善**
- **toast 提示覆盖不完全**（另见上一节「切回翻译控制台 toast 重复」）
- 复核轮模板不使用文件元数据，使用批次元数据
- **问题修复/复核轮注入问题句的上下文**（并新增配置：是否附带上下文）
- `context.ensure_global_prompt_loaded`（context.py:20-44）无锁：if 后为同步段、无 await，多 worker 共享同一 ForFileMetaData 实例（LLMTranslate.py:1742 创建单实例），依赖「同步段内无 await 不切换协程」不变式。存量问题，非本次改动引入。
- 项目中不止一处有版本号，需统一控制：`GalTransl/__init__.py`、`desktop/package.json`、`desktop/package-lock.json`、`desktop/src-tauri/tauri.conf.json`、`desktop/src-tauri/Cargo.toml`
- 翻译流水线可自定义程度提高：改为低代码文本，用列表表示执行顺序（统一问题修复后端可额外传问题检测项序号或名称），允许一个后端在一份流水线中多次出现
- 翻译控制台「当前提示词」状态指示灯中，黑色指示灯没有类似其他指示灯的「等待首字/取消」文字说明，需显示为「已完成 + 耗时」
- [发布说明] d7a5833 后，以字面 `<br>` 为换行约定的项目行为有变化（缓存与输出现在是真实换行而非 `<br>`），建议写进更新日志
- [发布说明] `gpt.chatMode: single` 已推广至修复/改进轮（fix/brfix/jpfix/banfix/improve）：此前该配置仅影响翻译轮，修复轮恒为多轮；现在 single 项目的修复轮每批也改为独立完整请求（不建立会话），token 消耗会上升，建议写进更新日志
- MCP 的 `list_projects` 只枚举 workspace 根，建在自定义位置的项目不会出现在该工具列表里（UI 不受影响）
- 各阶段的独立 API 页应放在 API 设置页；独立 API 后的可用性检测逻辑不完善

# 分端待办（后端 / 前端 / 跨端）

## 后端（GalTransl/server_handlers_*）

- [ ] **part2 路由委托后无条件再补一次 404**（`server_handlers_project.py:1499`）：`route_project_api_part2(...)` 无返回值，调用方紧接着无条件发 404。凡由 part2 处理的路径（`/progress`、`/problems`、`/cache/search` 等）都会在正确响应体之后再写一段 404。当前未暴露的原因：默认 HTTP/1.0，靠 `Content-Length` 定界，客户端读完第一段即返回。风险：一旦启用 keep-alive 或改用严格解析的客户端/代理，多出的响应体会被当成下一个请求的响应。修法：让 `route_project_api_part2` 返回「是否已处理」布尔值，调用方据此决定是否发 404（对齐 `server_cache` 等模块的返回值风格）。
- [ ] **`server_handlers_project2.py` import 块存在死导入**（AST 精确核对，仅出现在 import 行）：`unquote`、`_safe_project_dir`、`decode_project_dir`、`encode_project_dir`、`_deep_merge_defaults`、`_get_default_config`、`_REPLACE_FIELD_REJECTED_MSG`、`_build_cache_tree`、`_build_project_output`、`_cache_entry_key`、`_entry_modified`、`_fmt_indices`、`_list_dir_entries`、`_resolve_cache_h_ranges`。无行为影响，属拆分大文件时的残留；清理时顺带核对其它 `server_handlers_*` 拆分文件是否有同类残留（本次未逐个核查）。
- [ ] **`/alt-translations` 未统一缓存文件枚举口径**（`server_handlers_project2.py:836`）：仍用裸 `os.walk` + `name.endswith(".json")`，与 `/problems`、`/progress`、`/runtime` 进度、全量重检已统一的 `_collect_cache_files` 不一致。当前实际影响近零（pass0 元数据/断点分片为 dict 形状、快照为 `.bak`，`_collect_alt` 对非 list 提前返回），但建议一并改用 `_collect_cache_files`，避免再次出现「问题统计有、其它端点无」的漂移。

## 前端（desktop/src/pages/review）

- [ ] **cacheWatcher 自动刷新 effect 为死代码**（`ReviewPage.tsx` 约 909-922 行）：`reviewMode() === "translate"` 与 `modeInfoOf(file).mode !== "translate"` 两道守卫作用在同一 `activeFilePath` 上、语义矛盾，任何情况下都会在 `loadFile` 前 return——"外部改动自动刷新译文文件"从未生效（cacheVersion 轮询形同虚设，仅手动「刷新」按钮可用）。需先排查是历史语义变更后的残留还是有意禁用，再决定删除或修正守卫。
- [ ] **modeInfoOf 分类表不认识 `gendic_cache/`、`aftertrans_cache/` 等目录**（`ReviewPage.tsx` `modeInfoOf`）：这类目录下的单对象 JSON 会被默认判为 translate 模式，若出现在缓存树且可点击，会误入 `loadFile`，以字典形状 entries 触发 `snapshotKey` 的 `list.map` TypeError。可达性取决于 `/files` 的 cache_files 列举范围，待核实后在分类表补充对应目录或对不可编辑目录做入口屏蔽。

## 跨端（翻译后端展示元数据）

- [ ] **翻译后端展示元数据收口到后端**：引擎的「中文短名 / 分组 / 组内顺序」散在前端两张表——`desktop/src/lib/engineDisplay.ts`（ENGINE_LABELS / ENGINE_HINTS / ENGINE_GROUPS / ENGINE_ORDER）与 `desktop/src/pages/routeAgent/plan.ts` 的 `INSTRUCTIONS`；同一引擎两处叫法不一致（ForSemCheck：语义检测 vs 语义复核；ForJPResidue：残留日文修复 vs 残留修复），而 `/api/translators` 只下发「引擎名 + 一段长描述」。建议在 `GalTransl/__init__.py` 的 `TRANSLATOR_SUPPORTED` 每项补 `label`/`group`/`order`，经 `/api/translators`（`server_handlers_root.py`）下发，`engineDisplay.ts` 只留派生逻辑，routeAgent 指令标签引用同一来源——新增引擎只改后端一处，对齐 pipeline_stages「后端唯一真源 + 前端派生」的做法。注意：新增字段是 `{name, description}` 的向后兼容增量，需同步 `api/types.ts` 的 `TranslatorOption` 与字段缺失回退口径，并把两侧测试改为直接校验载荷。
- [ ] **隐藏引擎清单在后端与测试间重复**：`tests/test_engine_display_consistency.py` 的 `_HIDDEN_EXTRA` 与 `server_handlers_root.py` 中 `/api/translators` 的内联 `_hidden_translators` 是同一份口径的两处抄写（工具引擎已 import `UTILITY_ENGINES` 共用），后端新增隐藏引擎时测试会误报。建议提为后端常量（`GalTransl/__init__.py` 或 `UtilityEngines.py`）供两侧 import，收进上一条的元数据下发改动里。

# 未来的大更新项

# 需修复的技术债务

- `GalTransl/server.py:32` 导入的 `save_app_settings` 已无使用，直接删掉（`load_app_settings` 的调用点保留：`/api/app-settings` GET 与前端日志判定仍用）
- 后端文件存在冗余未复用代码和层次不清晰的代码，需提取公用函数并重新划分层级（0.4.6 已清理死注入链与 `/files` 兼容块，`get_file_list` 对残留文件的排除保留）
- 多个依赖存在不兼容（原项目就有，暂时无影响）：
  - Rust 侧：windows crate 0.58 与 0.61.3 双主版本并存
  - Rust 侧：HTML/CSS 解析栈新旧两代分裂
  - Python 侧开发环境与构建环境不一致：openai 2.44 vs 2.53、httpx-aiohttp 0.1.12 vs 0.2.0、pyreqwest 0.12.0 vs 0.12.2
  - Python 侧隐式必需依赖：httpx-aiohttp
  - 冗余依赖：tiktoken、fasttext-predict
  - Rust 声明滞后
- 输入框换行逻辑未提取成公用代码
- 存在多个超过 2000 行代码的文件
- 基类架构不完善，且后端文件架构不一致
- `Cache.py` `get_transCache_from_json` 重试失败句分支直接下标访问 `cache_dict[cache_key]["proofread_by"]`：条目缺该字段 + `retry_failed` 开启 + pre_dst 含 "(Failed)" + 有校对稿（no_proofread=False 绕过短路）时，读缓存阶段 KeyError。修法：改用 `_cache_get(cache_dict[cache_key], "proofread_by", "")`

# 已知但不打算修复的问题

- 文件读取和写入默认不鉴权，任意路径读写。原因：原项目既有；后端只绑 127.0.0.1 + CORS 白名单，远程攻击面小。
- API 密钥明文显示。原因：原项目既有；只有本机被恶意入侵或主动分享项目配置才会泄漏。
- 开启动态句数 + 多 worker 时，多轮翻译后端动态句数调节失真

# 提示词相关

- **规定 AI 的立场，本质上就是为它设定一个清晰的"翻译目的"。核心方法是在 Prompt 中明确以下几点：**

明确目标受众（为谁译）：在 Prompt 的开头就指明目标群体，例如："你是一位专业的游戏本地化专家，请将以下日文视觉小说文本翻译成简体中文，目标受众是中国大陆的年轻二次元玩家。"

定义翻译风格（怎么译）：提供风格指南，甚至角色设定资料。例如："请采用轻松、口语化的现代汉语风格，保留'学长'、'学姐'等日式校园称呼。男主角'桐人'的语气要冷淡简洁，女主角'亚丝娜'则要温柔礼貌。"

设定具体约束（有哪些限制）：明确技术或内容上的要求。例如："请确保所有译文能适配每行最多 15 个汉字的对话框。保持所有人名、地名、技能名前后统一，术语表见附录。"

提供参考样例（照这个译）：给出 1-2 句你理想中的译文作为示例，让 AI 迅速理解你对"信达雅"的平衡点，这比抽象的描述更有效。

明确禁止事项（别这么译）：直接告诉 AI 什么不能做，例如："禁止使用网络流行语或梗"、"禁止添加原文没有的解释性文字"。
