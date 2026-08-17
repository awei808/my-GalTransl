"""
分析问题
"""

import os
import re

from GalTransl import LOGGER
from GalTransl.CSentense import CTransList
from GalTransl.ConfigHelper import CProjectConfig, CProblemType
from GalTransl.Utils import (
    get_most_common_char,
    contains_japanese,
    contains_english,
    punctuation_zh,
    contains_korean,
    is_all_gbk,
    extract_control_substrings
)
from GalTransl.Dictionary import CGptDict

MONOLOGUE_MALE_HE_EXCLUDES = (
    "其他",
    "他们",
    "他人",
    "他乡",
    "他国",
    "他日",
    "他山",
)

# 允许出现在换行前的字符：中文标点 + 逗号、顿号（顿号后换行不判定异常）
_ALLOWED_BREAK_CHARS = punctuation_zh + "，、"


def _newline_count(s: str) -> int:
    """统计换行次数：真实 \\r\\n/\\n/\\r 与字面转义（"\\r\\n"/"\\n"）均计为 1 次，CRLF 不重复计。"""
    norm = s.replace("\\r\\n", "\n").replace("\\n", "\n")
    norm = norm.replace("\r\n", "\n").replace("\r", "\n")
    return norm.count("\n")


def _clean_text_len(s: str) -> int:
    """去除全部换行后的纯文本长度（与 _newline_count 归一化口径一致）。"""
    norm = s.replace("\\r\\n", "\n").replace("\\n", "\n")
    norm = norm.replace("\r\n", "\n").replace("\r", "\n")
    return len(norm) - norm.count("\n")


def load_h_check_words(dict_paths: list) -> list:
    """加载 H 场景用词不当检测词库（格式与 GPT 字典一致：词|备注）。

    逐行解析，取每行第一列作为检测词；解析语义与 parse_dict_line 对齐：
    Tab/四空格先归一化为 |，仅当行内不含 | 时才把 // # \\ 前缀视作注释
    （含 | 的此类行编辑器会按规则加载，后端需保持一致）；另跳过纯符号分隔线。
    取词后再防御一次：首列以 // # \\ 开头的行一律视为注释丢弃，避免模板注释
    （如「// 格式：词|备注」含 | 被当作词条）注入提示词。
    文件缺失或解析为空时返回空列表（检测自动降级为不触发）。
    """
    words: list[str] = []
    seen: set[str] = set()
    for dict_path in dict_paths:
        if not os.path.isfile(dict_path):
            LOGGER.warning(f"禁用词字典文件不存在：{dict_path}")
            continue
        count = 0
        with open(dict_path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                if "|" not in stripped and stripped.startswith(("//", "#", "\\")):
                    continue
                if re.fullmatch(r"[=\-~_*]{3,}", stripped):
                    continue
                # 与 parse_dict_line 对齐：Tab/四空格转 | 后再分割（兼容 Tab 分隔字典）
                norm = stripped.replace("    ", "\t").replace("\t", "|")
                word = norm.split("|", 1)[0].strip()
                # 首列带注释前缀（即使整行含 |，如模板说明）一律丢弃，防注入提示词
                if word.lstrip().startswith(("//", "#", "\\")):
                    LOGGER.debug(f"禁用词字典跳过注释行：{stripped}")
                    continue
                if not word:
                    continue
                if word not in seen:
                    seen.add(word)
                    words.append(word)
                    count += 1
        LOGGER.info(f"载入 H 场景用词检测词库: {dict_path} {count}词条")
    return words


def find_problems(
    trans_list: CTransList,
    projectConfig: CProjectConfig,
    gpt_dict: CGptDict = None,
    h_ranges: list = None,
    h_check_words: list = None,
    forbidden_words: list = None,
) -> None:
    """
    此函数接受一个翻译列表，查找其中的问题并将其记录在每个翻译对象的 `problem` 属性中。

    参数:
    - trans_list: 翻译对象列表。
    - find_type: 要查找的问题类型列表。
    - arinashi_dict: 一个自定义字典，其中的键值对将会被用于查找问题。
    - h_ranges: H 剧情区间列表 [(lo, hi), ...]，用于区分 h 场景（问题类型判定的一级维度）。
    - h_check_words: H 场景用词不当检测词库（h 场景内命中即标记「用词不当」）。
    - forbidden_words: 非 h 场景禁用词库（非 h 场景内命中即标记「用词不当」；h 场景内与 h 词库合并检测）。

    返回值:
    - 无返回值，但会修改每个翻译对象的 `problem` 属性。
    """
    arinashi_dict = projectConfig.getProblemAnalyzeArinashiDict()
    find_type = projectConfig.getProblemAnalyzeConfig("problemList")
    # 仅当 problemList 键未配置时才回退旧版 GPT35 段；配置为空列表则按"不检测"处理
    if not find_type and not projectConfig.hasProblemAnalyzeConfig("problemList"):
        find_type = projectConfig.getProblemAnalyzeConfig("GPT35")  # 兼容旧版

    for tran in trans_list:
        if getattr(tran, "skip_check", False):
            continue
        pre_src = tran.pre_src
        post_src = tran.post_src
        pre_dst = tran.pre_dst
        post_dst = tran.post_dst
        if pre_dst == "":
            continue
        # h 场景判定提升为一级维度：所有问题检测均可按是否 h 场景差异化处理
        is_h_scene = bool(h_ranges) and any(
            lo <= tran.index <= hi for lo, hi in h_ranges
        )
        problem_list = []
        if CProblemType.词频过高 in find_type:
            most_word, word_count = get_most_common_char(pre_dst)
            most_word_src, word_count_src = get_most_common_char(pre_src)
            if word_count > 20 and word_count > word_count_src * 2:
                problem_list.append(f"词频过高：'{most_word}'{str(word_count)}次")
        if CProblemType.标点错漏 in find_type:
            char_to_error = {
                ("（", ")"): "括号",
                "：": "冒号",
                "*": "*符号",
                "；": "；符号",
                "[": "[符号",
                "<": "<符号",
                ("『", "「", "“"): "引号",
            }

            for chars, error in char_to_error.items():
                if isinstance(chars, tuple):
                    if not any(char in pre_src for char in chars):
                        if any(char in post_dst for char in chars):
                            problem_list.append(f"本无{error}")
                    elif any(char in pre_src for char in chars):
                        if not any(char in post_dst for char in chars):
                            problem_list.append(f"本有{error}")
                else:
                    if chars not in pre_src:
                        if chars in post_dst:
                            problem_list.append(f"本无{error}")
                    elif chars in pre_src:
                        if chars not in post_dst:
                            problem_list.append(f"本有{error}")

            if contains_korean(pre_dst) and not contains_korean(pre_src):
                problem_list.append("本无韩文")
        if CProblemType.残留日文 in find_type:
            pre_dst_jp_chars = contains_japanese(pre_dst)
            post_dst_jp_chars = contains_japanese(post_dst)
            if pre_dst_jp_chars != "" and post_dst_jp_chars != "":
                problem_list.append(f"残留日文：{post_dst_jp_chars}")
        if CProblemType.丢失换行 in find_type:
            if _newline_count(pre_src) > _newline_count(post_dst):
                problem_list.append("丢失换行")
        if CProblemType.多加换行 in find_type:
            if _newline_count(pre_src) < _newline_count(post_dst):
                problem_list.append("多加换行")
        if CProblemType.长句丢失换行 in find_type:
            # 原文有换行才检测；真实/字面换行均归一化处理；h 场景用专用阈值
            if _newline_count(pre_src) > 0:
                n_number = _newline_count(post_dst)
                clean_len = _clean_text_len(post_dst)
                threshold = (
                    projectConfig.getHSentenceLengthThreshold()
                    if is_h_scene
                    else projectConfig.getAvgSentenceLengthThreshold()
                )
                if clean_len / (n_number + 1) > threshold:
                    problem_list.append("长句丢失换行")
        if CProblemType.换行位置异常 in find_type:
            bad_lines = []
            # 归一化真实/字面换行为真实 \n 后按段检查换行前字符
            _norm = post_dst.replace("\\r\\n", "\n").replace("\\n", "\n")
            _norm = _norm.replace("\r\n", "\n").replace("\r", "\n")
            _segments = _norm.split("\n")
            for _i in range(1, len(_segments)):
                _prev = _segments[_i - 1][-1] if _segments[_i - 1] else ""
                if _prev and _prev not in _ALLOWED_BREAK_CHARS:
                    bad_lines.append(str(_i))
            if bad_lines:
                problem_list.append("换行位置异常：第" + "、".join(bad_lines) + "行")
        if CProblemType.频繁换行 in find_type:
            # 仅检测换行符：译文有效字符少却出现多次换行（真实/字面换行均归一化计入）
            if post_dst:
                _clean = post_dst.replace("\\r\\n", "\n").replace("\\n", "\n")
                _clean = _clean.replace("\r\n", "\n").replace("\r", "\n")
                _newline = _clean.count("\n")
                _valid_len = _clean_text_len(post_dst)
                _hit = False
                if _valid_len < 20 and _newline >= 3:
                    _hit = True
                elif _valid_len < 10 and _newline >= 2:
                    _hit = True
                if _hit:
                    problem_list.append(
                        f"频繁换行：有效字符{_valid_len}，换行{_newline}次"
                    )
                    LOGGER.debug(
                        f"频繁换行：index={tran.index}, 有效字符={_valid_len}, 换行={_newline}次"
                    )
        if CProblemType.比日文长 in find_type or CProblemType.比日文长严格 in find_type:
            len_beta = 1.3
            min_diff=8
            if CProblemType.比日文长严格 in find_type:
                len_beta = 1.0
                min_diff=0
            if len(post_dst) > len(pre_src) * len_beta and len(post_dst) - len(pre_src) >= min_diff:
                problem_list.append(
                    f"比日文长：{round(len(post_dst)/max(len(pre_src),0.1),1)}倍({len(post_dst)-len(pre_src)}字符)"

                )
        if CProblemType.字典使用 in find_type:
            # h 场景检查 h 与 非 h（重合词条只按 h 检查）；非 h 场景只检查非 h 字典
            if val := gpt_dict.check_dic_use(
                pre_dst, tran, scene="h" if is_h_scene else "nh"
            ):
                problem_list.append(val)
        if CProblemType.用词不当 in find_type:
            # h 场景合并 h 词库与禁用词库（h 词是对非 h 词的补充）；非 h 场景只按禁用词库检测
            if is_h_scene:
                merged_words = list(
                    dict.fromkeys([*(h_check_words or []), *(forbidden_words or [])])
                )
                if merged_words:
                    hits = [w for w in merged_words if w in pre_dst or w in post_dst]
                    if hits:
                        problem_list.append("用词不当：" + "、".join(hits))
                        LOGGER.debug(f"用词不当(h场景)：index={tran.index}, 命中={hits}")
            else:
                if forbidden_words:
                    hits = [w for w in forbidden_words if w in pre_dst or w in post_dst]
                    if hits:
                        problem_list.append("用词不当：" + "、".join(hits))
                        LOGGER.debug(f"用词不当(非h场景)：index={tran.index}, 命中={hits}")
        if CProblemType.引入英文 in find_type:
            if not contains_english(post_src) and contains_english(pre_dst):
                eng_chars = contains_english(post_dst)
                if len(eng_chars)>4:
                    problem_list.append(f"引入英文：{eng_chars}")
        if CProblemType.语言不通 in find_type:
            if "zh" in projectConfig.target_lang:
                if not is_all_gbk(pre_dst):
                    non_gbk_whites=["♪","♥"]
                    non_gbk_chars = is_all_gbk(post_dst)
                    for non_gbk_white in non_gbk_whites:
                        non_gbk_chars = non_gbk_chars.replace(non_gbk_white,"")
                    if non_gbk_chars !="":
                        problem_list.append(f"语言不通-非GBK：{non_gbk_chars}")
        if CProblemType.缺控制符 in find_type:
            control_list_src = extract_control_substrings(pre_src)
            control_list_pre_dst = extract_control_substrings(pre_dst)
            control_list_post_dst = extract_control_substrings(post_dst)
            lost_list=[]
            for control_src in control_list_src:
                if (
                    control_src not in control_list_pre_dst
                    and control_src not in control_list_post_dst
                ):
                    lost_list.append(control_src)
            if lost_list:
                problem_list.append(f"缺控制符：{' '.join(lost_list)}")
        if CProblemType.独白男他 in find_type:
            if tran.speaker == "" and "他" in post_dst:
                if not any(exclude in post_dst for exclude in MONOLOGUE_MALE_HE_EXCLUDES):
                    problem_list.append("独白男他")

        if arinashi_dict != {}:
            for key, value in arinashi_dict.items():
                if key not in pre_src and value in post_dst:
                    problem_list.append(f"本无 {key} 译有 {value}")
                if key in pre_src and value not in post_dst:
                    problem_list.append(f"本有 {key} 译无 {value}")

        if "(Failed)" in post_dst:
            problem_list.append("翻译失败")

        # AI 语义检测标记（ForSemCheck 产出）：字段非空即标"疑似错误"。
        # suspected_error 是持久化信号，problem 是输出，规则重检/校对保存每次重跑都会重新认领。
        if CProblemType.疑似错误 in find_type:
            if getattr(tran, "suspected_error", "") != "":
                problem_list.append("疑似错误")

        # 定语/状语过长：只检测最终成品 post_dst（无校对时即 pre_dst），与旧分支对齐避免重复
        if CProblemType.定语过长 in find_type or CProblemType.状语过长 in find_type:
            if post_dst:
                attributive_max = projectConfig.getAttributiveMaxLength()
                adverbial_max = projectConfig.getAdverbialMaxLength()
                # 归一化真实/字面换行为真实 \n 后按行切分，避免跨字面转义行被贪婪吞并
                _norm = post_dst.replace("\\r\\n", "\n").replace("\\n", "\n")
                _norm = _norm.replace("\r\n", "\n").replace("\r", "\n")
                for line in _norm.split("\n"):
                    if CProblemType.定语过长 in find_type:
                        # 「是……的」强调/定语结构，取中间定语长度（每条只报一次）
                        for m in re.finditer(r"是([^，。！？!?…\n]+?)的", line):
                            if len(m.group(1)) > attributive_max:
                                problem_list.append(
                                    f"定语过长：是{m.group(1)}的（{len(m.group(1))}字，上限{attributive_max}）"
                                )
                                break
                    if CProblemType.状语过长 in find_type:
                        # 「在……中/里」地点状语，取中间长度
                        hit = False
                        for m in re.finditer(r"在([^，。！？!?…\n]+?)(中|里)", line):
                            if len(m.group(1)) > adverbial_max:
                                problem_list.append(
                                    f"状语过长：在{m.group(1)}{m.group(2)}（{len(m.group(1))}字，上限{adverbial_max}）"
                                )
                                hit = True
                                break
                        if not hit:
                            # 「……地」方式状语（地+逗号/句号边界）
                            for m in re.finditer(r"([^，。！？!?…\n]+?)地[，。]", line):
                                if len(m.group(1)) > adverbial_max:
                                    problem_list.append(
                                        f"状语过长：{m.group(1)}地（{len(m.group(1))}字，上限{adverbial_max}）"
                                    )
                                    break

        # 以本次检测结果覆盖 tran.problem（清空旧缓存遗留的问题，避免累积）。
        # 不要加 if problem_list 判断：无问题时需显式置空以清除旧 problem。
        tran.problem = ", ".join(problem_list)
