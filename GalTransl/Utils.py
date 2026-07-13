"""
工具函数
"""

import os
import codecs
from typing import Tuple, List, Optional
from collections import Counter
from re import compile
import requests
import re
import json
from GalTransl import LOGGER

PATTERN_CODE_BLOCK = compile(r"```([\w]*)\n([\s\S]*?)\n```")
whitespace = " \t\n\r\v\f"
ascii_lowercase = "abcdefghijklmnopqrstuvwxyz"
ascii_uppercase = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ascii_letters = ascii_lowercase + ascii_uppercase
digits = "0123456789"
hexdigits = digits + "abcdef" + "ABCDEF"
octdigits = "01234567"
punctuation = r"""!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~"""
punctuation_zh = "。？！…（）；：《》「」『』【】"
printable = digits + ascii_letters + punctuation + whitespace

def load_guideline_file(file_path: str) -> str:
    try:
        if "translation_guidelines" not in file_path:
            file_path=os.path.join( "translation_guidelines",file_path)
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read()
    except Exception as e:
        LOGGER.error(f"Error reading translation_guideline file {file_path}: {e}")
        raise e
    
def extract_control_substrings(text: str) -> list[str]:
    """
    提取文本中所有以英文标点符号开头，且仅包含英文字母、数字和标点符号的子串。

    Args:
        text: 输入的文本字符串。

    Returns:
        一个包含所有匹配子串的列表。
    """
    # 允许的字符集：英文字母、数字和标点符号
    allowed_chars = ascii_letters + digits + punctuation
    first_punctuation = r"""!#$%&()*+-./:;<=>?@[\]^_`{|}~"""
    # 正则：英文标点开头 + 零个或多个允许字符（re.escape 转义特殊字符）
    pattern = f"[{re.escape(first_punctuation)}][{re.escape(allowed_chars)}]*"
    
    # 使用 re.findall 查找所有匹配的子串
    return re.findall(pattern, text)

def get_most_common_char(input_text: str) -> Tuple[str, int]:
    """
    此函数接受一个字符串作为输入，并返回该字符串中最常见的字符及其出现次数。
    它会忽略黑名单中的字符，包括 "." 和 "，"。

    参数:
    - input_text: 一段文本字符串。

    返回值:
    - 包含最常见字符及其出现次数的元组。
    """
    black_list: List[str] = [".", "，"]
    counter: Counter = Counter(input_text)
    most_common = counter.most_common()
    most_char: str = ""
    most_char_count: int = 0
    for char in most_common:
        if char[0] not in black_list:
            most_char = char[0]
            most_char_count = char[1]
            break
    return most_char, most_char_count


def contains_japanese(text: str) -> bool:
    """
    此函数接受一个字符串作为输入，检查其中是否包含日文字符。

    参数:
    - text: 要检查的字符串。

    返回值:
    - 如果字符串中包含日文字符，则返回 True，否则返回 False。
    """
    # 日文字符范围
    hiragana_range = (0x3040, 0x309F)
    katakana_range = (0x30A0, 0x30FF)
    katakana_range2 = (0xFF66, 0xFF9F)

    jp_chars = set()
    # 检查字符串中的每个字符
    for char in text:
        # 黑名单
        if char in ["ー", "・"]:
            continue
        # 获取字符的 Unicode 码点
        code_point = ord(char)
        # 检查字符是否在日文字符范围内
        if (
            hiragana_range[0] <= code_point <= hiragana_range[1]
            or katakana_range[0] <= code_point <= katakana_range[1]
            or katakana_range2[0] <= code_point <= katakana_range2[1]
        ):
            jp_chars.add(char)
    return "".join(jp_chars)


def contains_korean(text: str) -> bool:
    """
    此函数接受一个字符串作为输入，检查其中是否包含韩文字符。

    参数:
    - text: 要检查的字符串。

    返回值:
    - 如果字符串中包含韩文字符，则返回 True，否则返回 False。
    """
    # 韩文字符范围
    hangul_jamo_range = (0x1100, 0x11FF)  # 韩文声母和韵母
    hangul_compatibility_jamo_range = (0x3130, 0x318F)  # 韩文兼容声母和韵母
    hangul_syllables_range = (0xAC00, 0xD7AF)  # 韩文音节

    # 检查字符串中的每个字符
    for char in text:
        # 获取字符的 Unicode 码点
        code_point = ord(char)
        # 检查字符是否在韩文字符范围内
        if (
            hangul_jamo_range[0] <= code_point <= hangul_jamo_range[1]
            or hangul_compatibility_jamo_range[0]
            <= code_point
            <= hangul_compatibility_jamo_range[1]
            or hangul_syllables_range[0] <= code_point <= hangul_syllables_range[1]
        ):
            return True
    return False


def contains_katakana(text: str) -> bool:
    # 日文字符范围
    katakana_range = (0x30A0, 0x30FF)

    # 检查字符串中的每个字符
    for char in text:
        # 排除ー
        if char in ["ー", "・"]:
            continue
        # 获取字符的 Unicode 码点
        code_point = ord(char)
        # 检查字符是否在日文字符范围内
        if katakana_range[0] <= code_point <= katakana_range[1]:
            return True
    return False


def is_all_chinese(text: str) -> bool:
    """
    此函数接受一个字符串作为输入，检查其中是否 *全部* 都是中文字符 (汉字)。
    (使用循环检查每个字符)

    参数:
    - text: 要检查的字符串。

    返回值:
    - 如果字符串中的 *所有* 字符都是中文字符，则返回 True，否则返回 False。
      如果字符串为空，则返回 False。
    """
    if not text:
        return False

    # 定义中文字符的 Unicode 范围
    cjk_unified_range = (0x4E00, 0x9FFF)
    cjk_extension_a_range = (0x3400, 0x4DBF)
    cjk_compatibility_range = (0xF900, 0xFAFF)
    # 添加更多扩展区... (注意：大于 0xFFFF 的码点需要特殊处理或 Python 3.3+ 支持)
    # cjk_extension_b_range = (0x20000, 0x2A6DF)

    for char in text:
        code_point = ord(char)

        # 检查字符 *是否在* 任何一个定义的中文范围内
        is_chinese = (
            (cjk_unified_range[0] <= code_point <= cjk_unified_range[1])
            or (cjk_extension_a_range[0] <= code_point <= cjk_extension_a_range[1])
            or (cjk_compatibility_range[0] <= code_point <= cjk_compatibility_range[1])
            # Add checks for other ranges here if needed, e.g.:
            # or (cjk_extension_b_range[0] <= code_point <= cjk_extension_b_range[1])
        )

        # 如果当前字符 *不是* 中文字符，则整个字符串不满足条件，立即返回 False
        if not is_chinese:
            return False

    # 如果循环正常结束，说明所有字符都是中文字符
    return True

def is_all_gbk(s: str) -> str:
    if s == "":
        return ""
    
    non_gbk_chars = set()
    for char in s:
        try:
            char.encode('gbk')
        except UnicodeEncodeError:
            non_gbk_chars.add(char)
    
    return str("".join(non_gbk_chars))




def contains_english(text: str) -> str:
    """
    此函数接受一个字符串作为输入，检查其中是否包含英文字符。

    参数:
    - text: 要检查的字符串。

    返回值:
    - 如果字符串中包含英文字符，则返回 True，否则返回 False。
    """
    # 英文字符范围
    english_range = (0x0041, 0x005A)
    english_range2 = (0x0061, 0x007A)
    english_range3 = (0xFF21, 0xFF3A)
    english_range4 = (0xFF41, 0xFF5A)

    eng_chars = ""
    # 检查字符串中的每个字符
    for char in text:
        # 获取字符的 Unicode 码点
        code_point = ord(char)
        # 检查字符是否在英文字符范围内
        if (
            english_range[0] <= code_point <= english_range[1]
            or english_range2[0] <= code_point <= english_range2[1]
            or english_range3[0] <= code_point <= english_range3[1]
            or english_range4[0] <= code_point <= english_range4[1]
        ):
            eng_chars += char
    return eng_chars


def extract_code_blocks(content: str) -> Tuple[List[str], List[str]]:
    # 匹配带语言标签的代码块
    matches_with_lang = PATTERN_CODE_BLOCK.findall(content)

    # 提取所有匹配到的带语言标签的代码块
    lang_list = []
    code_list = []
    for match in matches_with_lang:
        lang_list.append(match[0])
        code_list.append(match[1])

    return lang_list, code_list


def get_file_name(file_path: str) -> str:
    """
    获取文件名，不包含扩展名
    """
    base_name = os.path.basename(file_path)
    file_name, _ = os.path.splitext(base_name)
    return file_name


def get_file_list(directory: str) -> list[str]:
    file_list = []
    for dirpath, dirnames, filenames in os.walk(directory):
        for file in filenames:
            # FileMetaData.json / PlotMetadata.json / BatchMetadata.json 是元数据控制文件，不是待翻译源文件
            if file.lower() in ("filemetadata.json", "plotmetadata.json", "batchmetadata.json"):
                continue
            file_list.append(os.path.join(dirpath, file))
    return file_list


def process_escape(text: str) -> str:
    return codecs.escape_decode(bytes(text, "utf-8"))[0].decode("utf-8")


# 收紧：要求 dst 值闭合引号后紧跟对象结束符 }（非逗号引导的后续字段），
# 降低跨字段误吞风险；可选空白更宽松。
pattern_fix_quotes = compile(r'"dst"\s*:\s*"(.+?)"\s*}')


def fix_quotes(text: str) -> str:
    """修复模型回包中 dst 值内含未转义直引号导致 JSON 解析失败的问题。

    仅对「本身无法被 json.loads 解析」的行做引号修正；已合法的 jsonline
    （含模型在 dst 关闭后又重复追加 name 等字段的畸形行，其本身仍是合法
    JSON）**原样返回**，不做任何改写，从而彻底避免破坏 JSON 结构。

    旧实现对整段响应做无条件修正：当行形如
        ...,"dst":"译文", "name": "創"}
    正则 `"dst": *"(.+?)"}` 会贪婪到第一个 "}"（即冗余 name 的末尾），把
    `, "name": "創` 一并吞入 dst 捕获组并转成弯引号，使 json.loads 虽能通过
    但译文被污染（即 22 处 "name" 污染 bug）。本实现以「先解析、仅修失败行」
    规避，且解析成功后的弯引号归一仍由 BaseTranslate._normalize_parsed_
    translation_text 中的 fix_quotes2 负责，此处不做重复处理。
    """
    out_lines = []
    for raw_line in text.split("\n"):
        line = raw_line
        # 取出 jsonline 的 JSON 主体（去掉 sig| 前缀），判断是否已经合法
        body = line.split("|", 1)[1] if "|" in line else line
        try:
            json.loads(body)
            out_lines.append(raw_line)  # 已合法，不做任何修改
            continue
        except json.JSONDecodeError:
            pass
        # 仅当整行 JSON 解析失败时，才尝试修复 dst 值内未转义的直引号
        results = pattern_fix_quotes.findall(line)
        if not results:
            out_lines.append(raw_line)
            continue
        new_line = line
        for match in results:
            new_match = match
            for i in range(match.count('"')):
                if i % 2 == 0:
                    new_match = new_match.replace('"', "“", 1).replace(r"\“", "“", 1)
                else:
                    new_match = new_match.replace('"', "”", 1).replace(r"\”", "”", 1)
            new_line = new_line.replace(match, new_match)
        out_lines.append(new_line)
    return "\n".join(out_lines)


def fix_quotes2(text: str) -> str:
    if text.startswith('"') and text.endswith('"'):
        text = f"“{text[1:-1]}”"
    for i in range(text.count('"')):
        if i % 2 == 0:
            text = text.replace('"', "“", 1).replace(r"\“", "“", 1)
        else:
            text = text.replace('"', "”", 1).replace(r"\”", "”", 1)
    return text


def get_n_symbol(src_text: str) -> list[str]:
    n_symbols = []
    if "\r\n" in src_text:
        n_symbols.append("\r\n")
    if "\n" in src_text and "\r\n" not in src_text:
        n_symbols.append("\n")
    if "\\r\\n" in src_text:
        n_symbols.append("\\r\\n")
    if "\\n" in src_text and "\\r\\n" not in src_text:
        n_symbols.append("\\n")

    return n_symbols


def check_for_tool_updates(new_version: list[str]) -> None:
    try:
        release_api = "https://api.github.com/repos/xd2333/GalTransl/releases/latest"
        response = requests.get(release_api, timeout=5).json()
        latest_release = response["tag_name"]
        new_version.append(latest_release)
    except Exception:
        pass


def find_most_repeated_substring(text: str) -> tuple[str, int]:
    max_count = 0
    max_substring = ""
    n = len(text)

    for i in range(n):
        for j in range(i + 1, n + 1):
            substring = text[i:j]
            count = 1
            start = j
            while (
                start + len(substring) <= n
                and text[start : start + len(substring)] == substring
            ):
                count += 1
                start += len(substring)

            if count > max_count or (
                count == max_count and len(substring) > len(max_substring)
            ):
                max_count = count
                max_substring = substring

    return max_substring, max_count


def decompress_file_lzma(input_filepath: str, output_filepath: Optional[str] = None) -> None:
    """
    解压缩使用 LZMA 算法压缩的单个文件。

    Args:
        input_filepath (str): 要解压缩的输入文件路径 (通常以 '.xz' 结尾)。
        output_filepath (str, optional): 解压缩后的输出文件路径。
                                         如果为 None，则移除输入文件名中的 '.xz'。
    """
    import lzma

    if output_filepath is None:
        if input_filepath.endswith(".xz"):
            output_filepath = input_filepath[:-3]
        else:
            LOGGER.error(
                "错误: 输入文件名不以 '.xz' 结尾，无法自动确定输出文件名。请指定 output_filepath。"
            )
            return

    try:
        with lzma.open(input_filepath, "rb") as f_in, open(
            output_filepath, "wb"
        ) as f_out:
            while True:
                chunk = f_in.read(4096)
                if not chunk:
                    break
                f_out.write(chunk)
        # print(f"文件 '{input_filepath}' 已成功解压缩为 '{output_filepath}'")
    except FileNotFoundError:
        LOGGER.error(f"错误: 文件 '{input_filepath}' 未找到。")
    except Exception as e:
        LOGGER.error(f"解压缩文件时发生错误: {e}")


if __name__ == "__main__":
    pass
