import os
import logging
from time import localtime
import threading
from GalTransl.Utils import check_for_tool_updates

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

PROGRAM_SPLASH1 = r"""
   ____       _ _____                    _ 
  / ___| __ _| |_   _| __ __ _ _ __  ___| |
 | |  _ / _` | | | || '__/ _` | '_ \/ __| |
 | |_| | (_| | | | || | | (_| | | | \__ \ |
  \____|\__,_|_| |_||_|  \__,_|_| |_|___/_|                 

------Translate your favorite Galgame------
"""

PROGRAM_SPLASH2 = r"""
   ______      ________                      __
  / ____/___ _/ /_  __/________ _____  _____/ /
 / / __/ __ `/ / / / / ___/ __ `/ __ \/ ___/ / 
/ /_/ / /_/ / / / / / /  / /_/ / / / (__  ) /  
\____/\__,_/_/ /_/ /_/   \__,_/_/ /_/____/_/   
                                             
-------Translate your favorite Galgame-------
"""

PROGRAM_SPLASH3 = r'''

   ___              _     _____                                     _    
  / __|   __ _     | |   |_   _|    _ _   __ _    _ _      ___     | |   
 | (_ |  / _` |    | |     | |     | '_| / _` |  | ' \    (_-<     | |   
  \___|  \__,_|   _|_|_   _|_|_   _|_|_  \__,_|  |_||_|   /__/_   _|_|_  
_|"""""|_|"""""|_|"""""|_|"""""|_|"""""|_|"""""|_|"""""|_|"""""|_|"""""| 
"`-0-0-'"`-0-0-'"`-0-0-'"`-0-0-'"`-0-0-'"`-0-0-'"`-0-0-'"`-0-0-'"`-0-0-' 

--------------------Translate your favorite Galgame--------------------
'''

PROGRAM_SPLASH4 = r"""
     _____)           ______)                 
   /             /)  (, /                  /) 
  /   ___   _   //     /  __  _  __   _   //  
 /     / ) (_(_(/_  ) /  / (_(_(_/ (_/_)_(/_  
(____ /            (_/                        

-------Translate your favorite Galgame-------
"""
ALL_BANNERS = [PROGRAM_SPLASH1, PROGRAM_SPLASH2, PROGRAM_SPLASH3, PROGRAM_SPLASH4]
PROGRAM_SPLASH = ALL_BANNERS[localtime().tm_mday % 4]

GALTRANSL_VERSION = "0.4.9"
AUTHOR = "awei808"
CONTRIBUTORS = "xd2333 (原作者), ryank231231, PiDanShouRouZhouXD, Noriverwater, Isotr0py, adsf0427, pipixia244, gulaodeng, sakura-umi, lifegpc, natsumerinchan, szyzbg"

CONFIG_FILENAME = "config.yaml"
INPUT_FOLDERNAME = "gt_input"
OUTPUT_FOLDERNAME = "gt_output"
CACHE_FOLDERNAME = "transl_cache"
PASS0_CACHE_DIR = "pass0_cache"  # 全局提示词缓存
PASS1_CACHE_DIR = "pass1_cache"  # 文件级元数据缓存
PASS2_CACHE_DIR = "pass2_cache"  # 批次级元数据缓存
PASS3_CACHE_DIR = "pass3_cache"  # 翻译缓存
TRANSLATOR_SUPPORTED = {
    "ForGal-full-pipeline": {
        "zh-cn": "完整翻译流水线：自动执行压缩→全局分析→术语表→文件元数据→批次划分→翻译，全自动串联。",
        "en": "Full translation pipeline: compression → global analysis → glossary → file metadata → batch division → translation, fully automated."
    },
    "ForGlobalPrompt": {
        "zh-cn": "由压缩后全文+游戏信息生成全局剧情概要、角色档案、行文风格。结果写入 transl_cache/pass0_cache/GlobalPrompt.json。",
        "en": "Generate global plot summary, character profiles, writing style from compressed full text + game info. Writes GlobalPrompt.json."
    },
    "ForGal-json-translate": {
        "zh-cn": "翻译后端：翻译Gal时使用，json格式输入，对话模式可选（多轮/单轮），可注入文件级元数据(FileMetaData)和批次级元数据(BatchMetadata)。",
        "en": "Translation backend for Gal translation, json input, selectable multi-turn/single-turn chat mode, supports FileMetaData and BatchMetadata injection."
    },
    "ForImproveTranslation": {
        "zh-cn": "整文件翻译完成后评估译文，对可改进的句子生成备选译文，",
        "en": "Translation quality improvement: evaluates the whole file after translation, generates alternative translations (alt_dst in cache), swappable in review page."
    },
    "ForBRStation": {
        "zh-cn": "对「换行位置异常」问题译文，生成备选译文，",
        "en": "Line-break position fix: for sentences flagged with 'line-break position anomaly', generates alternative translations (alt_dst in cache), swappable in review page."
    },
    "ForJPResidue": {
        "zh-cn": "对「残留日文」问题译文，对照原文生成备选译文，",
        "en": "JP-residue fix: for sentences flagged with 'residual Japanese', generates alternative translations (alt_dst in cache) by comparing with source, swappable in review page."
    },
    "ForBanWordFix": {
        "zh-cn": "对「用词不当」问题译文，对照原文与问题生成备选译文，",
        "en": "Ban-word fix: for sentences flagged with 'inappropriate wording' (banned words), generates alternative translations (alt_dst in cache) by comparing source and problem, swappable in review page."
    },
    "ForSemCheck": {
        "zh-cn": "逐句对照原文与译文，仅将语义极大差异的句子标记为「疑似错误」问题。",
        "en": "Semantic difference detection: AI compares source and translation per sentence, flags only sentences with huge semantic difference (suspected mistranslation/omission/misalignment) as 'suspected error' problems; never modifies translations."
    },
    "ForSemCheckAgain": {
        "zh-cn": "对已标记为「疑似错误」的句子逐句复核。",
        "en": "Second-pass confirmation for 'suspected error' flags: re-checks each flagged sentence and keeps only confirmed mistranslations, dismissing acceptable translations (false positives)."
    },
    "ForToneCheck": {
        "zh-cn": "词语色彩一致性检查：对照批次级元数据的「用词色彩」标注，标记用词色彩明显偏离所属区间的句子（tone_issue 标记，不改译文）；需先在流水线中生成批次级元数据。",
        "en": "Word-tone consistency check: flags sentences whose wording tone clearly deviates from the per-interval tone annotations in BatchMetadata (tone_issue flag, translation untouched); requires batch metadata from the pipeline."
    },
    "ForFixRound": {
        "zh-cn": "统一问题修复：按所选问题类型组合修复译文（多类可并行处理），每类按对应修复指令生成备选译文；输入模式由所选问题类型自动推导（含需对照原文的类型即用译文+原文，否则仅译文）。",
        "en": "Unified problem fix: repairs translations by a configurable combination of problem types, each with its own fix instruction, generating alternative translations (alt_dst); input mode auto-derived from selected types (src+dst if any type needs source, else dst-only)."
    },
    "ForFileMetaData": {
        "zh-cn": "由剧本文件生成文件级元数据，包含文件内出场人物及服装、剧情和剧情标签，结果写入 transl_cache/pass1_cache/{filename}.meta.json。",
        "en": "Generate FileMetaData from script files. No translation, no multi-turn; writes transl_cache/pass1_cache/{filename}.meta.json."
    },
    "ForBatchMetaData": {
        "zh-cn": "依据文件级元数据，将原文划分批次，并标注视角/氛围/H/用词色彩。",
        "en": "Partition scripts into translation intervals (batches) based on FileMetaData, tagging perspective/atmosphere/H/word-tone; writes transl_cache/pass2_cache/{filename}.batch.json for multi-turn translation."
    },
    "ForPlotRouteMap": {
        "zh-cn": "基于各文件剧情+剧情大纲生成剧情路线图和对应线路剧情",
        "en": "Generate plot route map from per-file metadata & user outline; writes PlotRouteMap.json for translation injection."
    },
    "GenDic": {
        "zh-cn": "自动化构建GPT字典",
        "en": "Automatically build GPT dictionary, requires a large model, recommended GPT4/Claude-3/Deepseek-V3"
    },
    "rebuildr": {
        "zh-cn": "重建结果：用译前译后字典经缓存刷写 gt_output 结果 json，跳过翻译且不写缓存（需缓存完整覆盖全部句子）。",
        "en": "Rebuild results: rewrite gt_output via cache with pre/post dictionaries applied; skips translation and cache writing (cache must cover all lines)."
    },
    "rebuilda": {
        "zh-cn": "重建缓存和结果：用译前译后字典刷写缓存与 gt_output 结果 json，跳过翻译（需缓存完整覆盖全部句子）。",
        "en": "Rebuild cache and results: rewrite cache + gt_output via cache with pre/post dictionaries; skips translation (cache must cover all lines)."
    },
    "dump-name": {
        "zh-cn": "导出name字段，生成name替换表，用于翻译name字段",
        "en": "Export name field to generate name replacement table for name field translation"
    },
    "show-plugs": {
        "zh-cn": "显示全部插件列表",
        "en": "Show all plugin list"
    },
    "recheck": {
        "zh-cn": "全部重检：对 pass3_cache 全部缓存重跑问题检测并写回 problem / post_dst_preview（不调用模型）。",
        "en": "Recheck all pass3 cache files: re-run problem detection and write back problem/post_dst_preview. No model calls.",
    },
    "check-batch-size": {
        "zh-cn": "批次划分预检：按 internals.forbatchmeta 计算最大可自然划分行数，列出行数超限的待翻译文件（不调用模型）。",
        "en": "Batch size precheck: compute max natural lines from internals.forbatchmeta and list oversize input files. No model calls.",
    },
    "build-output": {
        "zh-cn": "构建输出：构建前校验（仅提示）后，从缓存重建 gt_output 结果文件（不调用模型）。",
        "en": "Build output: rebuild gt_output from cache after non-blocking validation. No model calls.",
    },
}

# 死代码
TRANSLATOR_DEFAULT_ENGINE = {
    "ForGal-full-pipeline": "deepseek-chat",
    "ForGlobalPrompt": "deepseek-chat",
    "ForGal-json-translate": "gpt-4.1",
    "ForFileMetaData": "deepseek-chat",
    "ForBatchMetaData": "deepseek-chat",
    "ForPlotRouteMap": "deepseek-chat",
    "GenDic": "deepseek-chat",
}
NEED_OpenAITokenPool=["ForGal-full-pipeline", "ForGlobalPrompt", "ForGal-json-translate", "ForImproveTranslation", "ForBRStation", "ForJPResidue", "ForBanWordFix", "ForSemCheck", "ForSemCheckAgain", "ForToneCheck", "ForFixRound", "GenDic", "ForFileMetaData", "ForBatchMetaData", "ForPlotRouteMap"]

# 引擎名别名：引擎改名后的兼容映射，旧配置/旧任务提交旧名时解析到现行名
TRANSLATOR_ALIASES = {
    "ForGal-json-multi-chat": "ForGal-json-translate",
}


def resolve_translator_alias(translator: str) -> str:
    """把旧引擎名解析为现行名；非别名原样返回。"""
    return TRANSLATOR_ALIASES.get(translator, translator)

LANG_SUPPORTED = {
    "zh-cn": "Simplified_Chinese",
    "zh-tw": "Traditional_Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "ru": "Russian",
    "fr": "French",
}
LANG_SUPPORTED_W = {
    "zh-cn": "简体中文",
    "zh-tw": "繁體中文",
    "en": "English",
    "ja": "日本語",
    "ko": "한국어",
    "ru": "русский",
    "fr": "Français",
}
DEBUG_LEVEL = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

new_version = []
update_thread = threading.Thread(target=check_for_tool_updates, args=(new_version,))
update_thread.start()

