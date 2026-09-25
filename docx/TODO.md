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
- **H 禁用词表的裸字条目（`逼`/`操`）会子串误报，当前样本量不足以定论**
  - 位置：`Dict/禁用词_H.txt:20-23`（`鸡巴`/`逼`/`操`/`肏`）；检测在 `GalTransl/Problem.py:106 _hit_display_words`（普通词走 `word in t` 子串匹配，仅 `re:` 前缀走正则 `DictWordMatcher.hit`）
  - 问题：`逼`/`操` 是常用字（`操作`/`操纵`/`逼近`/`逼迫`/`体操`/`情操`/`节操`），子串匹配**无法区分**它们与粗俗用法。用真实 `DictWordMatcher` 路径实测（38 个正常词 + 37 个粗俗词）：**裸字方案误报 37 个**；`re:` 环视正则方案误报 0 / 漏报 0；枚举粗俗搭配误报 0 但漏掉裸字单独出现（如 `舔她的逼`）。
  - 现状未造成损失：本项目 `用词不当` **0 命中**，那 37 个理论误报词一个都没出现过 → **样本量太小，不足以判定问题是否真实存在**，待更多项目数据积累后再定。
  - 附带约束（影响方案选择）：`_h_word_text()`（`ForGalJsonTranslate.py:77-79`）对 `re:` 条目**只剥掉前缀、正文本体原样注入提示词**，模板为「同时，以下词语在本项目 H 区间中属于用词不当，禁止使用：{words}。」→ 直接写正则会让提示词出现 `(?<![体情节])操(?![…])` 这类噪音。要「检测精确 + 提示词干净」兼得，需给 `_h_word_text` 加一步「提取字面核心」（已验证：`(?<![体情节])操(?![…])` → `操`，提示词与现在完全一致）。
  - 修复方向（未实施，待定）：方案 A 枚举粗俗搭配（纯数据、0 误报、漏裸字）；方案 B `re:` 环视正则 + `_h_word_text` 提取字面核心（0 误报 0 漏报，需改代码）。注意 `禁用词_H.txt` 是**公共字典**，改动影响所有项目。

# 存在且上线前必须修复的bug/值得优化项
- 首页如何开始和引导用户，
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

- 全局分析后端似乎不导入人名替换表
- 对于全局分析返回结果中含有错误的角色名称时，后续消费程序对于这个角色的逻辑未知
- 取消只能在应用目录下新建项目的限制 
- 双击 AI 建议没有让用户知道有这个功能的提示
- 术语提取要求新增：完全本地化、注释需要写详细，去除注释只能写“术语/意思h”的限制
- 字典ctrl+s保存似乎不可用或无弹窗反馈
- 各阶段的独立api页应该放在api设置页，独立api后的api检测可用性逻辑不完善
- 新增：对于行数小于20的文件，不用处理元数据
- 翻译控制台的文件进度显示不完善：当前阶段完成后且未完成所有翻译任务时，显示的是“排队中”，而不是“已完成某个阶段”
- 翻译控制台的速度单位错误，应为xx条/分钟
- 允许在所有阶段用户自主选择是否注入翻译规范
- 结果预览不要做及时刷新，应该减少刷新时机，当worker内容被替换时不刷新为空，（主要影响在修复改进轮后端的执行结果展示环节）
- **单独执行某个修复改进后端时会有问题，会连续执行别的后端**
- 疑似错误问题检测项的显示时，有时会显示具体原因有时不显示 **已完成**（semcheck 提示词 reason 必填具体原因、占位值归一为哨兵 "1"；find_problems 认领时原因拼入「疑似错误：原因」文案，非字符串缓存数据 str 收敛防崩）

# 未来的大更新项
- 0.4.1：跟进原项目进度，对原先缺失的功能修补，追加类似上有项目的视觉效果
- 0.4.2：翻译控制台视觉效果总更新、字典界面视觉效果更新、首页新增“新建项目向导”
- 0.4.3：恢复命令行版本的适配 **已完成**（CLI 恢复进度条/交互提示与配置文件自动探测，新增 -c/--config、--version 参数；补齐 CLI 工具引擎 recheck 全部重检 / check-batch-size 批次划分预检 / build-output 构建输出，新增 rebuildr / rebuilda 缓存重建引擎），补充翻译指南和项目地址的内容
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