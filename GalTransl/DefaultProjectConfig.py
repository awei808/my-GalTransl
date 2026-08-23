DEFAULT_PROJECT_CONFIG_YAML = """# 翻译后端相关设置
backendSpecific:
  OpenAI-Compatible: # (ForGal/ForNovel/GenDic)OpenAI API兼容接口通用
    tokens:
      - token: sk-example-key1
        endpoint: https://api.deepseek.com # 请求地址，加不加v1都可以
        modelName: deepseek-chat
      - token: sk-example-key2
        endpoint: https://openrouter.ai/api/v1/chat/completions # /chat/completions结尾则不自动补v1
        modelName: deepseek/deepseek-chat-v3-0324:free
        stream: true # 支持为单个token设置流式请求
    tokenStrategy: "random" # 令牌策略，random随机轮询；fallback优先第一个，出现[API错误]或[解析错误]时使用下一个
    checkAvailable: true # 翻译前检查API是否可用[True/False]
    checkAvailableConcurrency: 4 # checkAvailable阶段的并发检测数，避免启动时瞬时打满请求。[1-16]
    globalRequestRPM: 0 # 全局跨任务请求限速（每分钟请求数）。0表示不限制。[0-60000]
    stream: true # 流式请求，一般不用修改除非接口不支持流式[True/False]
    provider: auto # 服务商[auto自动识别/deepseek/openai/kimi/qwen/anthropic/zhipu/grok/gemini/custom]
    thinking_mode: default # 思考模式[default不干预/on开启/off关闭思考]，仅部分模型支持关闭
    reasoning_effort: "" # 思考强度[low/medium/high/max]，留空不发送（ds/Anthropic 支持 max，OpenAI 不支持）
    extra_body: "" # 高级参数JSON，透传到请求体（可覆盖上述映射），如 {"thinking_budget": 4096}
    apiTimeout: 300 # 请求超时时间，单位秒
    apiErrorWait: auto # 发生API Error时的等待时间，包括频率限制。auto将自动适应[auto/0-120]

  SakuraLLM: # (Sakura/Galtransl)
    endpoints:
      - http://127.0.0.1:8080
      #- https://sakura-share.one/ # 可以使用sakura-share的免费sakura-v1.0模型
    rewriteModelName: "" # 设置自定义的模型名称，在使用ollama时要修改

# 插件，插件列表可在启动程序后选择show-plugs查看，或在plugins目录内查看
plugin:
  filePlugin: file_galtransl_json # 用于支持更多格式，字幕file_subtitle_srt_lrc_vtt，小说file_epub_epub或file_plaintext_txt，mtooljson用file_i18n_json
  textPlugins: # 文本处理插件列表，可以设置多个，按顺序执行
    - text_common_normalfix # 常规文本修复插件
    #- text_common_skipNoJP # 跳过无日语句子插件
  # 某个插件自己的设置可以进入plugins目录内修改对应的yaml文件，也可以这样设置：
  file_galtransl_json:
    output_with_src: False # 输出到gt_output时是否保留原文[True/False]

# 程序设置
common:
  gpt.numPerRequestTranslate: 16 # 每次请求包含的句子数，建议不超过16。[1-32]
  gpt.dynamicNumPerRequestTranslate: false # 动态句数调整：根据解析错误自动调节每次请求句数。[True/False]
  gpt.dynamicNumPerRequestTranslate.min: 8 # 动态句数调整的最小句数。[1-64]
  gpt.dynamicNumPerRequestTranslate.max: 64 # 动态句数调整的最大句数。[1-64]
  workersPerProject: 16 # 项目级并行文件数；单文件并行需配合splitFile。
  autoAdjustWorkers: false # 基于近期429比例和响应延迟自动调节并发worker数。[True/False]
  sortBy: "size" # 文件调度顺序：name按文件名，size优先大文件（并行时通常更快）。[name/size]
  language: "zh-cn" # 目标输出语言。[zh-cn/zh-tw/en/ja/ko/ru/fr]

  # 单文件分割设置
  ###【重要】分割设置直接影响缓存文件的读取命中，迁移旧项目请确保单文件分割设置一致 ###
  splitFile: "no" # 单文件分片模式：no关闭（默认）；Num每n句切一片；Equal每文件均分n片。[no/Num/Equal]
  splitFileNum: 2048 # 分片参数：Num模式表示每片句数；Equal模式表示分片总数。
  splitFileCrossNum: 0 # 分片重叠句数（上下文缓冲），可提升片段衔接质量。[推荐0或10]

  save_steps: 1 # 每处理n个批次保存一次缓存；值越大保存更少、速度可能更快。[1-999]
  start_time: "" # 定时启动时间（24小时制，如00:30）；留空表示立即启动。[00:00-23:59]
  linebreakSymbol: "auto" # JSON内换行符类型，供问题检测/自动修复使用，不改变翻译语义。
  skipH: false # 是否跳过可能触发敏感词检测的句子。[True/False]
  smartRetry: True # 解析失败时自动缩小批次并重置上下文，减少无效重试。[True/False]
  retranslFail: false # 程序重启时是否自动重翻标记为"(Failed)"的句子。[True/False]
  retranslKey: # 在下方添加需要重翻的关键字，匹配原文/译文/problem 中的子串；留空不重翻。
    #- "翻译失败" # 启动时重翻命中“翻译失败”的句子
    #- "残留日文" # 启动时重翻命中“残留日文”的句子

  gpt.contextNum: 8 # 每次请求附带的前文句数；值越大上下文更强、成本更高（常用8）。[0-32]
  # ForGal/ForGal-json/ForNovel
  gpt.translation_guideline: "Basic.md" # 使用的翻译规范文件名（位于translation_guidelines），会影响文风与措辞。
  gpt.enhance_jailbreak: False # 是否启用“抗拒答”增强提示，降低模型拒答概率。[True/False]
  gpt.change_prompt: "no" # Prompt修改模式：no不改；AdditionalPrompt追加；OverwritePrompt覆盖默认提示词。[no/AdditionalPrompt/OverwritePrompt]
  gpt.prompt_content: "翻译结果使用文言文" # Prompt自定义内容；仅在change_prompt为AdditionalPrompt/OverwritePrompt时生效。
  gpt.afterTranslation: [] # 完整流水线翻译完成后追加的后处理后端（阶段 7）：有序数组，元素顺序即执行顺序；空数组不追加。可用项：improve改进轮；brfix换行修复；jpfix残留日文修复；banfix禁用词修复；semcheck语义差异检测（AI判定疑似错译/漏译/串行，写入suspected_error并标记"疑似错误"问题）；semcheckagain命中句二次复核（对semcheck标记句逐句确认/撤销误报，需先跑过semcheck）。旧字符串格式（none/improve+brfix）仍兼容读取。[improve/brfix/jpfix/banfix/semcheck/semcheckagain]
  gpt.enableBetterTranslation: false # [已废弃] 由 gpt.afterTranslation 取代。旧项目兼容：true 等价于 afterTranslation=improve。[True/False]
  gpt.numPerRequestBetter: 100 # 改进轮每批发送的句子数，越小越稳但越慢[1-512]
  gpt.enableProblemInject: false # 改进轮是否把译文问题(problem)注入提示词，供AI针对性改进，需先开启 gpt.afterTranslation(含 improve) [True/False]
  gpt.problemInjectTypes: [] # 改进轮注入的问题类型白名单（与 problemAnalyze.problemList 相同的类型名）；空列表=注入全部已检测问题
  gpt.swapFixToCurrent: false # 修复轮（brfix/jpfix）产生的备选译文是否与当前译文交换属性：true 时修复结果直接覆盖当前译文（校对优先否则初译），原译文存入备选译文可回退；false 时仅作备选译文需手动交换。[True/False]
  gpt.numPerRequestSemCheck: 20 # 语义差异检测（ForSemCheck）每批发送的句子数，越小越稳但越慢。[1-512]
  # Sakura/GalTransl
  gpt.token_limit: 0 # (Sakura/GalTransl) 单轮token上限；0表示不限制。用于避免上下文溢出。
  # 调试日志
  loggingLevel: info # 日志输出级别：debug详细，info常规，warning仅警告。[debug/info/warning]
  saveLog: false # 是否将日志写入文件。[True/False]

# 内部流程参数
internals:
  # === 完整流水线配置 ===
  pipeline:
    maxInputChars: 950000         # 全局分析阶段压缩后文本的“软阈值”：超过此值仅打印告警、不做截断（无损原则，绝不删行）。0 表示不检查。[1000-1000000]
    forceRegenDic: false          # 是否强制重新生成术语表（即使已存在）[True/False]
    abortOnDicFailure: false      # 是否在术语表生成失败时中止流水线 [True/False]
    # === 流水线阶段开关（false 则跳过该阶段）===
    enableValidate: true          # 阶段0 输入数据校验 [True/False]
    enableCompress: true          # 阶段1 文本无损压缩（仅全局分析需要）[True/False]
    enableGlobalPrompt: true      # 阶段2 全局游戏分析 [True/False]
    enableGenDic: true            # 阶段3 术语表构建 [True/False]
    enableFileMeta: true          # 阶段4 文件级元数据 [True/False]
    enablePlotRoute: true         # 阶段4.5 剧情路线图 [True/False]
    forceRegenPlotRoute: false    # 是否强制重新生成剧情路线图（即使已存在）[True/False]
    enableBatchMeta: true         # 阶段5 批次级元数据 [True/False]
    enableTranslate: true         # 阶段6 翻译执行 [True/False]
    enableImprove: true           # 阶段7 修复和改进译文（后处理，按 gpt.afterTranslation 顺序执行）[True/False]
  # ForPlotRouteMap 后端专用配置（剧情路线图）
  plotroute:
    structureType: "树"           # 剧情结构类型 [线性/树/有向无环图/有向有环图/混合]
    userOutline: ""               # 用户提供的剧情大纲（纯文本，可空；留空由 AI 根据各文件剧情自行归纳）
  # ForGlobalPrompt 后端专用配置
  forglobalprompt:
    inject_guideline: false       # 是否将翻译规范注入全局分析提示词 [True/False]
  forbatchmeta:
    max_batches: 20 # 翻译区间（批次）最大数量，超过此数将自动合并相邻区间；设大模型输出不稳可调大。[1-200]
    min_batch_size: 8 # 单批最小区间长度（行数），小于此值的区间会尽量与相邻区间合并。[1-1000]
    max_batch_size: 64 # 单批最大区间长度（行数），超过此值的区间会被自动切分。[1-1000]
    inject_guideline: false # 是否将翻译规范注入批次划分提示词。[True/False]
  forfilemeta:
    inject_guideline: false # 是否将翻译规范注入文件元数据生成提示词。[True/False]
  # GenDic 术语表模式配置（设计文档 gendic_terms_mode_design.md）
  gendic:
    mode: terms            # 术语表模式：terms 词表模式（本地提取词表后逐词翻译，推荐）/ segments 旧片段模式 [terms/segments]
    batch_size: 50         # terms 模式每批请求的词数。[1-200]
    context: true          # terms 模式是否附每个词首现完整句（多义消歧+防幻觉）。[True/False]
    context_samples: 3     # terms 模式每个词附带的示例句数量（取含该词的前 N 个完整句，与 context 配合）。[2-10]
    max_terms: 128         # terms 模式词表/生成字典硬上限：固有名詞优先保底，但总条目不超此值（过多条目影响后续翻译）；0 不截断。[0-20000]
    han_allowlist: []      # 汉字普通名词收录白名单（如 射精/膣内 等 H 术语需统一译法时逐个添加）；默认空=不收录（既定决策）。[日文词列表]
    ban_words: []          # 太过平常的词汇黑名单（代词/语气词/口语等，如 キミ/ダメ/ヤダ），提取时不发送 AI；默认空。[日文词列表]



# 代理设置，使用中转供应商时一般不用开代理
proxy:
  enableProxy: false # 是否启用代理。[True/False]
  proxies:
    - address: http://127.0.0.1:7890

# 自动问题分析配置，在-前面加#号可以禁用
problemAnalyze:
  problemList: # 要发现的问题清单
    - 词频过高 # 重复大于20次
    - 标点错漏 # 标点符号多加或漏加
    - 残留日文 # 日文平假名片假名残留
    #- 丢失换行 # 缺少行内换行，一般没所谓
    - 多加换行 # 换行符比原句多，可能导致溢出屏幕
    - 比日文长 # 比日文长1.3倍以上
    - 字典使用 # 没有按GPT字典要求翻译
    - 语言不通 # 疑似没有被翻译成目标语言，翻译为中文时检查是否包含非GBK字符
    - 缺控制符 # 检测译文丢失ruby或其他控制符的情况
    - 独白男他 # 独白（无name）里出现“他”，排除“其他/他们/他人/他乡/他国/他日/他山”
    #- 引入英文 # 本来没有英文，译文引入了英文
    #- 比日文长严格 # 比日文长1倍以上就提醒
    #- 长句丢失换行 # 译文平均分句长度超过 avgSentenceLengthThreshold，疑似丢失应有换行
    #- 换行位置异常 # 换行符未紧跟中文标点（逗号/顿号/句号等）之后
    - 疑似错误 # AI语义检测：原文与译文语义极大差异（错译/漏译/串行），由 ForSemCheck 后端标注 suspected_error 后认领
  avgSentenceLengthThreshold: 17 # 长句丢失换行的分句长度阈值，默认17，建议范围15~25
  avgSentenceLengthThresholdH: 24 # 长句丢失换行的H场景专用分句长度阈值，默认24，建议范围20~30

# 字典设置
dictionary:
  defaultDictFolder: Dict # 通用字典文件夹，相对于程序目录，也可填入绝对路径
  usePreDictInName: false # 将译前字典用在name字段，可用于翻译name字段，会发送给翻译引擎替换后的name[True/False]
  usePostDictInName: false # 将译后字典用在name字段，可用于翻译name字段[True/False]
  useGPTDictInName: true # 将GPT字典用在name字段，可用于翻译name字段[True/False]
  sortDict: true # 将所有字典按查找词长度重排序。[True/False]
  # 译前字典
  preDict:
    - 01H字典_矫正_译前.txt # 用于口齿不清的矫正
    - 00通用字典_译前.txt
    - (project_dir)项目字典_译前.txt # (project_dir)代表字典在项目文件夹
  # GPT 字典（h/非h 按剧情场景生效）
  gpt.dict:
    - GPT字典_非h.txt
    - GPT字典_h.txt
    - (project_dir)项目GPT字典.txt
    - (project_dir)项目GPT字典-生成.txt
  # 译后字典
  postDict:
    - 00通用字典_符号_译后.txt # 符号矫正
    - 00通用字典_译后.txt
    - (project_dir)项目字典_译后.txt
"""
