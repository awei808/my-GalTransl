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
- **MCP 约束文案点名的 `skills/galtransl-mcp/SKILL.md` 不在便携版发布包内（查档确认）**
  - 位置：`GalTransl/mcp_tools.py` 的 `build_server_instructions()` 末句「完整约束见随项目分发的 skills/galtransl-mcp/SKILL.md」；发布脚本 `build_release_py312.py:594-620`（`copy_dir_filtered` 只复制 `plugins/`、`Dict/`、`translation_guidelines/`、`guides/`、`res/`）与 `build_release.py:298-313`（更少，无 `guides/`）；`scan_data_files` 的 `DATA_FILE_EXTS`（`build_release_py312.py:158`）只收 7 类数据扩展名，`.md` 不在内，且该函数只扫 `GalTransl/` 包目录。两个脚本全文均无 `skills` 字样。
  - 影响：拿便携版 zip 的用户手里没有该文件，「随项目分发」的说法与事实不符；运行期功能不受影响（`guides/07-mcp.md` 在包内且用的是「仓库内」措辞，口径与 instructions 的「随项目分发」不一致）。
  - 修复方向（未实施）：二选一——在发布脚本加 `skills/ -> skills/` 复制（并同步缺失必需项校验），或把 instructions 末句改成与指南一致的「仓库内」措辞（改文案需同步金样本 `tests/test_mcp_gate_settings.py::test_default_matches_golden_text`）。取哪一种取决于 skills 是否要随便携版分发，需先决策。
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


# 存在且上线前必须修复的bug/值得优化项
- ~~首页如何开始和引导用户~~ **已完成**（首页「快速上手」三步卡：新建项目 → 配置 API → 启动翻译，各自可点击跳转；底部「查看完整指南 →」进使用指南入门篇）
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

- ~~请求最小间隔秒 (apiMinIntervalSec)似乎从未生效~~ **已完成**（根因：节流状态原挂引擎实例上，后处理每文件新建实例致首请求绕过+实例间互不约束，且旧实现并发下同睡同醒成簇；已改为 (endpoint,token) 维度进程级时隙预占，BaseEngine.py）
- ~~添加mcp工具：用于给特定json条目标记为跳过检查、给特定json条目交换备选译文与当前译文、增删项目字典；mcp工具调用方式更改，不强制使用项目的绝对路径，也可以直接使用项目名称~~ **已完成**（新增写工具 `galtransl_mark_skip_check`/`galtransl_swap_alt_translation`，逐条改 skip_check 标志/交换 alt_dst 与 pre_dst，改前快照+按 /cache/save 同口径重检+与运行中翻译任务互斥；`write_glossary`/`read_glossary` 扩展 category=gpt/pre/post 覆盖译前/译后项目字典；全部工具的 project_dir 支持项目名称——后端经 `known_projects.json` 登记「打开过/创建过/跑过任务」的项目（`GalTransl/known_projects.py`），MCP 优先按登记表解析、回退工作区根扫描，不要求项目在工作区根下，mcp_tools.py）
- 后端对结果预览的支持不完善，一些后端的结果不会在结果预览中显示
- 翻译控制台-当前提示词的状态指示灯中，黑色指示灯没有类似其他指示灯的“等待首字”“取消”的文字说明，需要显示为“已完成+耗时”
- 结果预览的更新时机可能过于频繁（需先将上一条bug修好并测试后再确认这个问题是否真实存在）
- [发布说明] d7a5833 后，以字面 <br> 为换行约定的项目行为有变化（缓存与输出现在是真实换行而非 <br>），建议写进更新日志。
- MCP 的 list_projects 只枚举 workspace 根，建在自定义位置的项目不会出现在该 MCP 工具列表里（UI 不受影响）。
- ~~双击 AI 建议没有让用户知道有这个功能的提示~~ **已完成**（首次进入校对页延时 toast 一次性提示，localStorage 标记只提示一次；校对指南篇同步说明该交互）
- 各阶段的独立api页应该放在api设置页，独立api后的api检测可用性逻辑不完善
- ~~新增：对于行数小于20的文件，不用处理元数据~~ **已完成**（新增 `internals.pipeline.metaMinLines`（默认 20，0=不跳过，前端「翻译后端-文件/批次元数据提取」组可调）：文件级与批次级元数据两阶段都跳过「非空行数 < 阈值」的文件——不调用 AI、不写 pass1/pass2 产物；口径复用 `metadata.count_nonempty_lines`（与进度分母同源），`_run_meta_worker_pool` 返回 `MetaPoolResult` 单独汇报小文件跳过数，四个调用点（流水线两阶段 + 独立后端两分支）改用「剔除跳过后的预期文件数」做交叉验证，避免误报缺失；文件进度面板按 `meta_min_lines` 把这类文件的 meta/batch 阶段判为已跳过（否则永远不亮绿灯）。既有产物不清理，forceRegen 也不使小文件复活）
- 翻译控制台的文件进度显示不完善：当前阶段完成后且未完成所有翻译任务时，显示的是“排队中”，而不是“已完成某个阶段”
- **设置界面新增mcp门禁设置** **已完成**（独立页面：设置页「AI API 调用接口相关」分区提示词模板下方留「MCP 服务与门禁 →」入口，标题栏「翻译」菜单同步加「MCP 设置」；页面含① MCP H 门禁开关（`app_settings.json` 的 `mcpHGateEnabled`，关闭后 `enforce_h_gate` 直接放行，SERVER_INSTRUCTIONS 第 4 条随开关改写为放行说明）；② MCP 工具逐个开关（`mcpDisabledTools` 黑名单，工具清单经新端点 `/api/mcp-tools` 下发供渲染；`tools/list`/调用分发/心跳均按启用集合过滤，调用被禁工具返回「已由用户禁用」，实时读设置无需重启；instructions/工具计数为 MCP 进程启动快照，重开 agent 会话刷新）。设置文件经 `resolve_app_dir()` 与打包版同目录，MCP 独立进程与后端读同一份；dsh 预设 persona 的 H 段同步改为条件式（以 MCP 服务说明为准，随门禁开关联动，未见到说明时保守按禁止处理），工具面段注明实际可用工具以会话挂载清单为准）

# 未来的大更新项


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