from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from os import path
from GalTransl.CSentense import CSentense, CTransList
from GalTransl import LOGGER
from GalTransl.Utils import process_escape


class IfWord:
    __slots__ = ["without_flag", "startswith_flag", "endswith_flag", "word"]

    def __init__(self, if_word: str) -> None:
        if if_word.startswith(">"):
            startswith_flag, if_word = True, if_word[1:]
        else:
            startswith_flag = False

        if if_word.endswith("<"):
            endswith_flag, if_word = True, if_word[:-1]
        else:
            endswith_flag = False

        if if_word.startswith("!"):
            without_flag, if_word = True, if_word[1:]
        else:
            without_flag = False

        self.without_flag = without_flag
        self.startswith_flag = startswith_flag
        self.endswith_flag = endswith_flag
        self.word = if_word


@dataclass
class ConditionItem:
    """条件字典的子条件项结构化表示。"""

    word: str  # 纯文本（已剥离 ! > < 语法）
    op: str  # 子条件间的连接符："and" / "or"（单条件时为空串）
    negate: bool = False  # ! 前缀
    startswith: bool = False  # > 前缀
    endswith: bool = False  # < 后缀
    placeholder: bool = False  # (同上) / ~ / （同上）占位


@dataclass
class DictRow:
    """单行字典的结构化解析结果（与前端 DictRow 字段对齐）。"""

    type: str  # normal/conditional/situation/gpt/comment/blank
    values: List[str]
    raw: str
    # 结构化字段（供卡片 UI 展示语法分离后的"有效文本"）。
    # values 仍保留 5 元素兼容形式以便序列化回文本时不丢信息。
    target: Optional[str] = None
    cond_items: List[ConditionItem] = field(default_factory=list)
    spl_word: str = ""  # "and" / "or" / ""（仅 conditional 有值）
    note: str = ""  # 行内 // 注释内容（已剥离 // 前缀）
    is_regex: bool = False  # 搜索词为 re: 正则词条
    regex_error: str = ""  # 正则预校验错误（空串表示合法或非正则行）


# 解析用的注释前缀：仅 //，且只在整行行首起效（与引擎各 load_dic 对齐）
_COMMENT_PREFIXES = ("//",)
# 纯符号装饰分隔线（如 ====、----、****）：视作注释口径跳过，与 load_h_check_words 对齐
_SEPARATOR_LINE_RE = re.compile(r"[=\-~_*]{3,}")


def _is_separator_line(line: str) -> bool:
    """判定是否纯符号装饰分隔线：strip 后整行由 = - ~ _ * 组成且 ≥3 字符。"""
    return bool(_SEPARATOR_LINE_RE.fullmatch(line.strip()))

_CONDITIONAL_KEYS = [
    "pre_src", "post_src", "pre_dst", "post_dst",
    "pre_jp", "post_jp", "pre_zh", "post_zh",
]
_SITUATION_KEYS = ["mono", "diag"]
_COND_PLACEHOLDERS = ("(同上)", "（同上）", "~")
_COND_NEGATE_PREFIX = "!"
_COND_STARTSWITH_PREFIX = ">"
_COND_ENDSWITH_SUFFIX = "<"
_REGEX_PREFIX = "re:"  # 搜索词正则前缀（在 ^^/1^ 剥离之后判定，可与二者组合）
_PIPE_SENTINEL = "\x00"  # \| 转义竖线在分割阶段的哨兵字符


def _safe_escape(text: str) -> str:
    """对字段做转义处理；转义序列非法（如孤立的反斜杠）时回退原文，避免整行解析失败。"""
    try:
        return process_escape(text)
    except (ValueError, UnicodeDecodeError):
        return text


def _split_dict_line(line: str) -> List[str]:
    """按 | 分割字典行；``\\|`` 还原为字面竖线（正则选择符等场景需要）。"""
    parts = line.replace("\\|", _PIPE_SENTINEL).split("|")
    return [p.replace(_PIPE_SENTINEL, "|") for p in parts]


def _compile_dict_regex(pattern: str) -> Tuple[Optional[re.Pattern], str]:
    """编译词条正则。返回 (pattern, error)。

    error 为空串表示编译成功且非零宽；为 "zero-width" 表示模式可匹配空串
    （调用方应丢弃词条，防 re.sub 零宽全文插入）；其余为编译错误信息
    （调用方应回退字面量匹配）。
    """
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return None, str(e)
    if compiled.match(""):
        return None, "zero-width"
    return compiled, ""


def _parse_cond_items(cond: str) -> tuple[List[ConditionItem], str]:
    """把条件列字符串解析为结构化子条件列表。

    条件列形态: `人妻[or]ひとづま`、`!サタン`、`(同上)`、`>字<` 等。
    支持 [and]/[or] 两种连接符；同时存在时按先 [and] 优先，
    避免与边界情况冲突（与引擎 load_dic 保持一致）。

    Returns:
        (cond_items, spl_word): 解析后的子条件列表与连接符。
    """
    if not cond:
        return [], ""

    spl_word = "and" if "[and]" in cond else "or"
    raw_items = [s.strip() for s in cond.split(f"[{spl_word}]")]
    items: List[ConditionItem] = []
    for i, raw in enumerate(raw_items):
        op = spl_word if 0 < i else ""
        placeholder = raw in _COND_PLACEHOLDERS
        if placeholder:
            items.append(ConditionItem(
                word="", op=op, negate=False,
                startswith=False, endswith=False, placeholder=True,
            ))
            continue
        word = raw
        negate = word.startswith(_COND_NEGATE_PREFIX)
        if negate:
            word = word[len(_COND_NEGATE_PREFIX):]
        startswith = word.startswith(_COND_STARTSWITH_PREFIX)
        if startswith:
            word = word[len(_COND_STARTSWITH_PREFIX):]
        endswith = word.endswith(_COND_ENDSWITH_SUFFIX)
        if endswith:
            word = word[: -len(_COND_ENDSWITH_SUFFIX)]
        items.append(ConditionItem(
            word=word, op=op, negate=negate,
            startswith=startswith, endswith=endswith, placeholder=False,
        ))
    return items, spl_word


def _serialize_cond_item(item: ConditionItem) -> str:
    """把子条件项还原为引擎可识别的字符串。"""
    if item.placeholder:
        return "(同上)"
    word = item.word
    if item.startswith:
        word = f">{word}"
    if item.endswith:
        word = f"{word}<"
    if item.negate:
        word = f"!{word}"
    return word


def _regex_flag_of(raw_search: str) -> Tuple[bool, str]:
    """按引擎口径判定搜索词是否正则词条（^^/1^ 剥离后 re: 前缀），并预校验编译。

    Returns:
        (is_regex, regex_error)：is_regex 表示引擎会按正则处理；regex_error
        为空串表示合法，否则为错误说明（引擎侧会回退字面量或丢弃）。
    """
    body = raw_search
    if body.startswith("^^") or body.startswith("1^"):
        body = body[2:]
    if not body.startswith(_REGEX_PREFIX):
        return False, ""
    _, err = _compile_dict_regex(body[len(_REGEX_PREFIX):])
    if err == "zero-width":
        return True, "正则可匹配空串"
    return True, err


def parse_dict_line(line: str, category: str) -> DictRow:
    """纯解析：单行字典文本 -> 结构化 DictRow，不做任何 IO / 翻译副作用。

    解析语义与翻译引擎 load_dic 对齐：Tab/四空格先归一化为 |，再按 | 分割、
    对每字段做转义处理；仅当整行行首以 // 开头时视作整行注释。
    note 字段为备注列原始内容（不剥离 // 前缀），仅供显示，不影响加载逻辑。

    Args:
        line: 单行字典文本（不含换行符）。
        category: 字典类别，pre/gpt/post 之一。

    Returns:
        DictRow: 含类型、字段值列表与原始行，以及结构化子条件/目标/注释。
    """
    raw_line = line
    if not line.strip():
        return DictRow("blank", [], raw_line)
    # 注释判定：仅 // 前缀，且只在整行行首起效（即使含 |）即为整行注释。
    # 与引擎各 load_dic / server 计数 / H 词库加载统一，避免含 | 的注释被当作普通词条加载。
    if line.lstrip().startswith(_COMMENT_PREFIXES):
        return DictRow("comment", [line], raw_line)
    # 纯符号分隔线视作注释口径跳过（与引擎 load_dic / load_h_check_words 统一）
    if _is_separator_line(line):
        return DictRow("comment", [line], raw_line)
    # 与引擎 load_dic 一致：Tab / 四空格转 | 后再分割（兼容旧版 Tab 分隔字典文件）
    line = line.replace("    ", "\t").replace("\t", "|")
    parts = [_safe_escape(p) for p in _split_dict_line(line)]
    if category in ("gpt", "gpth", "gptnh", "forbiddenh", "forbiddennh"):
        # 禁用词字典 / h-非h GPT 字典与 gpt 字典同构：词|备注 或 原文|译文|解释
        # 禁用词不支持替换（仅词条+备注），故返回 forbidden；h/非h GPT 仍是 gpt 行类型
        src = parts[0] if len(parts) > 0 else ""
        dst = parts[1] if len(parts) > 1 else ""
        rest = "|".join(parts[2:]) if len(parts) > 2 else ""
        # gpt/禁用词备注列原样保留（不剥离 //），禁用词运行时剥离在 load_h_check_words
        note = rest
        row_type = "gpt" if category in ("gpt", "gpth", "gptnh") else "forbidden"
        is_regex, regex_error = _regex_flag_of(src)
        return DictRow(
            row_type, [src, dst, rest], raw_line,
            target=None, cond_items=[], spl_word="", note=note,
            is_regex=is_regex, regex_error=regex_error,
        )
    if len(parts) >= 4 and parts[0] in _CONDITIONAL_KEYS:
        target, cond, search, replace = parts[0], parts[1], parts[2], parts[3]
        rest = "|".join(parts[4:]) if len(parts) > 4 else ""
        cond_items, spl_word = _parse_cond_items(cond)
        # 备注列原样保留（不剥离 //），仅供显示
        note = rest
        is_regex, regex_error = _regex_flag_of(search)
        return DictRow(
            "conditional", [target, cond, search, replace, rest], raw_line,
            target=target, cond_items=cond_items, spl_word=spl_word, note=note,
            is_regex=is_regex, regex_error=regex_error,
        )
    if len(parts) >= 3 and parts[0] in _SITUATION_KEYS:
        scene = parts[0]
        search = parts[1]
        # 与引擎 CNormalDic.load_dic 一致：第3列即 replace（不含 |）；多余列作为备注
        replace = parts[2] if len(parts) > 2 else ""
        note = "|".join(parts[3:]) if len(parts) > 3 else ""
        is_regex, regex_error = _regex_flag_of(search)
        return DictRow(
            "situation", [scene, search, replace], raw_line,
            target=scene, cond_items=[], spl_word="", note=note,
            is_regex=is_regex, regex_error=regex_error,
        )
    search = parts[0] if len(parts) > 0 else ""
    replace = parts[1] if len(parts) > 1 else ""
    rest = "|".join(parts[2:]) if len(parts) > 2 else ""
    # 备注列原样保留（不剥离 //），仅供显示，不影响替换逻辑
    note = rest
    is_regex, regex_error = _regex_flag_of(search)
    return DictRow(
        "normal", [search, replace, rest], raw_line,
        target=None, cond_items=[], spl_word="", note=note,
        is_regex=is_regex, regex_error=regex_error,
    )


class CBasicDicElement:
    """字典基本字元素"""

    conditionaDic_key = ["pre_src", "post_src", "pre_dst", "post_dst", "pre_jp", "post_jp", "pre_zh", "post_zh"]  # 条件字典（新名在前，旧名兼容）
    situationsDic_key = ["mono", "diag"]  # 场景字典
    __slots__ = [
        "search_word",  # 搜索词
        "replace_word",  # 替换词
        "startswith_flag",  # 是否为startwith情况
        "onetime_flag",  # 是否为onetime情况
        "special_key",  # 区分是否为特殊词典的关键字
        "is_situationsDic",  # 是否为情景字典
        "is_conditionaDic",  # 是否为条件字典
        "if_word_list",  # 条件字典中的条件词列表
        "spl_word",  # if_word_list的连接关键字
        "note",  # For GPT
        "dic_name",  # 字典名
        "is_regex",  # 是否为 re: 正则词条
        "regex_pattern",  # 预编译正则（load 时编译，is_regex 为 True 且合法时非 None）
        "regex_error",  # 正则错误说明（"zero-width" 表示可匹配空串，调用方应丢弃词条）
    ]

    def __init__(
        self,
        search_word: str = "",
        replace_word: str = "",
        special_key: str = "",
        dic_name: str = "",
    ) -> None:
        self.search_word: str = search_word
        self.replace_word: str = replace_word

        self.startswith_flag: bool = False
        if search_word.startswith("^^"):  # startswith情况
            self.startswith_flag = True
            self.search_word = search_word[2:]
        if search_word.startswith("1^"):  # onetime情况
            self.onetime_flag = True
            self.search_word = search_word[2:]
        else:
            self.onetime_flag = False

        # 正则词条：re: 前缀在 ^^/1^ 剥离之后判定，可与二者组合
        # （编译失败回退字面量并 warning；可匹配空串保留标记，由 load 侧丢弃）
        self.is_regex: bool = False
        self.regex_pattern: Optional[re.Pattern] = None
        self.regex_error: str = ""
        if self.search_word.startswith(_REGEX_PREFIX):
            pattern_body = self.search_word[len(_REGEX_PREFIX):]
            self.search_word = pattern_body
            compiled, err = _compile_dict_regex(pattern_body)
            if err == "":
                self.is_regex = True
                self.regex_pattern = compiled
            elif err == "zero-width":
                self.is_regex = True
                self.regex_error = "zero-width"
            else:
                self.regex_error = err
                LOGGER.warning(
                    f"[字典:{dic_name}] 正则编译失败，已按字面量匹配：{pattern_body}（{err}）"
                )

        self.special_key: str = special_key  # 区分是否为特殊词典的关键字

        self.is_situationsDic: bool = False

        self.is_conditionaDic: bool = False
        self.if_word_list: List[IfWord] = None  # 条件字典中的条件词列表
        self.spl_word: str = ""  # if_word_list的连接关键字
        self.note: str = ""  # For GPT
        self.dic_name: str = dic_name  # 字典名

    def __repr__(self) -> str:
        return f"{self.search_word} -> {self.replace_word}"

    def load_line(self, line: str, category: str = "pre") -> Optional["CBasicDicElement"]:
        """翻译入口：解析单行字典文本并应用到本元素，无效行返回 None。

        复用模块级 parse_dict_line 做解析，仅负责把解析结果落为本元素字段。

        Args:
            line: 单行字典文本（不含换行符）。
            category: 字典类别，pre/gpt/post 之一。

        Returns:
            self: 解析成功；None: 空行 / 注释 / 无替换的无效行。
        """
        row = parse_dict_line(line, category)
        if row.type in ("blank", "comment"):
            return None
        if row.type == "conditional":
            target, cond, search, replace = row.values[0], row.values[1], row.values[2], row.values[3]
            spl_word = "[and]" if "[and]" in cond else "[or]"
            self.special_key = target
            self.is_conditionaDic = True
            self.if_word_list = [IfWord(w.strip()) for w in cond.split(spl_word)]
            self.spl_word = spl_word
            self.search_word = search
            self.replace_word = replace
            return self
        if row.type == "situation":
            scene, search, replace = row.values[0], row.values[1], row.values[2]
            self.special_key = scene
            self.is_situationsDic = True
            self.search_word = search
            self.replace_word = replace
            return self
        # normal / gpt：无替换词的无效行直接丢弃
        if len(row.values) < 2 or not row.values[1]:
            return None
        search = row.values[0]
        replace = row.values[1]
        # 注意：load_line 当前为死代码（CNormalDic/CGptDict 走 CBasicDicElement.__init__）。
        # 此处 if/elif 与 __init__ 的双独立 if 口径不同（^^1^ 组合前缀仅命中一个）；如需启用请先统一。
        if search.startswith("^^"):  # startswith情况
            self.startswith_flag = True
            self.search_word = search[2:]
        elif search.startswith("1^"):  # onetime情况
            self.onetime_flag = True
            self.search_word = search[2:]
        else:
            self.search_word = search
        self.replace_word = replace
        return self


def _dict_word_match(dic: CBasicDicElement, text: str) -> bool:
    """词条搜索词命中判定：正则词条用 re.search，普通词条用子串包含。"""
    if dic.is_regex:
        return dic.regex_pattern is not None and dic.regex_pattern.search(text) is not None
    return dic.search_word in text


def _dict_word_consume(dic: CBasicDicElement, text: str) -> str:
    """从文本中消费（删除）词条命中的内容，供注入/检查两侧统一去重口径。"""
    if dic.is_regex:
        return dic.regex_pattern.sub("", text) if dic.regex_pattern is not None else text
    return text.replace(dic.search_word, "")


def _sorted_by_len_desc(items: List[CBasicDicElement]) -> List[CBasicDicElement]:
    """按搜索词长度降序排序（正则按模式串长度），供注入/检查两侧统一长词优先的消费顺序。"""
    return sorted(items, key=lambda d: len(d.search_word), reverse=True)


def _check_dic_element(elem: CBasicDicElement, dic_path: str) -> bool:
    """词条入库校验：零宽正则、空搜索词、替换词非法转义的词条丢弃（warning），返回是否可用。"""
    if elem.is_regex and elem.regex_pattern is None:
        LOGGER.warning(f"字典 {dic_path} 词条正则可匹配空串，已跳过：{elem.search_word}")
        return False
    if not elem.search_word:
        LOGGER.warning(f"字典 {dic_path} 存在空搜索词词条，已跳过")
        return False
    if elem.is_regex:
        try:
            # 预演一次替换：替换词含无效转义（如 \q）时 re.sub 会在翻译期抛错，提前拦截
            elem.regex_pattern.sub(elem.replace_word, "")
        except re.error as e:
            LOGGER.warning(
                f"字典 {dic_path} 替换词含无效转义，已跳过："
                f"{elem.search_word} -> {elem.replace_word}（{e}）"
            )
            return False
    return True


class DictWordMatcher:
    """词表检测词条目：支持普通词与 `re:` 前缀正则（load_h_check_words / find_problems 共用）。

    编译失败或可匹配空串时回退字面量匹配：检测词表丢弃会静默降低检测覆盖，回退更稳妥。
    """

    __slots__ = ["word", "is_regex", "pattern"]

    def __init__(self, raw: str) -> None:
        self.is_regex: bool = raw.startswith(_REGEX_PREFIX)
        self.word: str = raw[len(_REGEX_PREFIX):] if self.is_regex else raw
        self.pattern: Optional[re.Pattern] = None
        if self.is_regex:
            compiled, err = _compile_dict_regex(self.word)
            if err != "":
                LOGGER.warning(f"检测词正则无效，已按字面量匹配：{raw}（{err}）")
                self.is_regex = False
            else:
                self.pattern = compiled

    def hit(self, text: str) -> bool:
        """判定 text 是否命中该检测词。"""
        if self.is_regex:
            return self.pattern.search(text) is not None
        return self.word in text

    def __eq__(self, other: object) -> bool:
        # 与 str 等价比较：混合 str/Matcher 的词表去重与断言行为与旧 list[str] 一致
        if isinstance(other, DictWordMatcher):
            return self.is_regex == other.is_regex and self.word == other.word
        if isinstance(other, str):
            return self.word == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.word)

    def __repr__(self) -> str:
        return self.word


class CNormalDic:
    """
    :由多个BasicDic字典元素构成的大字典List（这个Dic不Normal但是懒得改名）
    :dic_list:字典文件的list，可以只有文件名，然后提供dir参数，也可以是完整的，混搭也可以
    :dic_base_dir:字典目录的path，会自动进行拼接
    """
    conditionaDic_key = ["pre_src", "post_src", "pre_dst", "post_dst", "pre_jp", "post_jp", "pre_zh", "post_zh"]  # 条件字典（新名在前，旧名兼容）
    situationsDic_key = ["mono", "diag"]  # 场景字典

    def __init__(self, dic_list: list) -> None:
        self.dic_list: List[CBasicDicElement] = []
        for dic_path in dic_list:
            self.load_dic(dic_path)  # 加载字典

    def sort_dic(self):
        """
        按字典search_word的长度重排序
        """
        self.dic_list.sort(key=lambda x: len(x.search_word), reverse=True)

    def get_dst(self, word: str):
        for dic in self.dic_list:
            if dic.search_word == word:
                return dic.replace_word
        return ""

    def load_dic(self, dic_path: str) -> None:
        """加载一个字典txt到这个对象的内存"""
        if not path.exists(dic_path):
            LOGGER.warning(f"{dic_path}不存在，请检查路径。")
            return
        with open(dic_path, encoding="utf8") as f:
            dic_lines = f.readlines()
        if len(dic_lines) == 0:
            return

        normalDic_count = 0
        conditionaDic_count = 0
        situationsDic_count = 0
        regexDic_count = 0
        dic_name = path.basename(dic_path)
        dic_name = path.splitext(dic_name)[0]

        for line in dic_lines:
            if line.startswith("\n"):
                continue
            # 整行注释（仅 // 前缀，即使含 |）跳过，与 parse_dict_line 统一
            if line.lstrip().startswith(_COMMENT_PREFIXES):
                continue
            # 纯符号分隔线跳过，与 load_h_check_words 口径一致
            if _is_separator_line(line):
                LOGGER.debug(f"字典 {dic_path} 跳过纯符号分隔线：{line.strip()}")
                continue

            # 四个空格和Tab兼容为|分隔符
            line = line.replace("    ", "\t")
            line = line.replace("\t", "|")

            sp = _split_dict_line(line.rstrip("\r\n"))  # 去多余换行符，|分割（\| 为字面竖线）
            len_sp = len(sp)
            if len_sp < 2:  # 至少是2个元素
                continue
            # 处理转义字符（非法转义回退原文，避免孤立反斜杠炸掉整个字典加载）
            for i in range(len_sp):
                sp[i] = _safe_escape(sp[i])

            is_conditionaDic_line = True if sp[0] in self.conditionaDic_key else False
            is_situationsDic_line = True if sp[0] in self.situationsDic_key else False
            if (is_conditionaDic_line and len_sp < 4) or (
                is_situationsDic_line and len_sp < 3
            ):
                continue

            if is_conditionaDic_line:
                if_word = sp[1]
                spl_word = "[and]" if "[and]" in if_word else "[or]"  # 判断连接字符
                # 初始化ifWord的list
                if_word_list = [IfWord(w.strip()) for w in if_word.split(spl_word)]
                con_dic = CBasicDicElement(sp[2], sp[3], sp[0], dic_name)
                if not _check_dic_element(con_dic, dic_path):
                    continue
                con_dic.is_conditionaDic = True
                con_dic.if_word_list = if_word_list
                con_dic.spl_word = spl_word
                self.dic_list.append(con_dic)
                conditionaDic_count += 1
                regexDic_count += 1 if con_dic.is_regex else 0
            elif is_situationsDic_line:
                sit_dic = CBasicDicElement(sp[1], sp[2], sp[0], dic_name)
                if not _check_dic_element(sit_dic, dic_path):
                    continue
                sit_dic.is_situationsDic = True
                self.dic_list.append(sit_dic)
                situationsDic_count += 1
                regexDic_count += 1 if sit_dic.is_regex else 0
            else:
                nor_dic = CBasicDicElement(sp[0], sp[1], dic_name=dic_name)
                if not _check_dic_element(nor_dic, dic_path):
                    continue
                self.dic_list.append(nor_dic)
                normalDic_count += 1
                regexDic_count += 1 if nor_dic.is_regex else 0
        LOGGER.info(
            "载入 普通字典："
            + path.basename(dic_path)
            + "  "
            + (str(normalDic_count) + "普通词条 " if normalDic_count != 0 else "")
            + (
                str(conditionaDic_count) + "条件词条 "
                if conditionaDic_count != 0
                else ""
            )
            + (
                str(situationsDic_count) + "场景词条 "
                if situationsDic_count != 0
                else ""
            )
            + (str(regexDic_count) + "正则词条" if regexDic_count != 0 else "")
        )

    def do_replace(
        self, input_text: str, input_tran: CSentense, full_match: bool = False
    ) -> str:
        """
        通过这个dic字典来优化一个句子。
        input_text：要被润色的句子
        input_translate：这个句子所在的Translate对象
        full_match：是否全匹配，默认False，开启后查找词完全等于input_text才替换
        """
        # 条件字典"同上"标记：记录上一个条件词条是否命中（首条条件词条命中"~"时回退 False，避免 UnboundLocalError）
        last_one_success = False
        # 遍历每个BasicDicElement做替换
        for dic in self.dic_list:
            # 场景字典判断
            if dic.is_situationsDic:
                if ("diag" == dic.special_key and input_tran.is_dialogue == False) or (
                    "mono" == dic.special_key and input_tran.is_dialogue == True
                ):
                    continue
            # 条件字典屎山
            if dic.is_conditionaDic:
                can_replace = False  # True代表本轮满足替换条件
                # 取对应的查找关键字的句子
                match dic.special_key:
                    case "pre_src" | "pre_jp":
                        find_ifword_text = input_tran.pre_src
                    case "post_src" | "post_jp":
                        find_ifword_text = input_tran.post_src
                    case "pre_dst" | "pre_zh":
                        find_ifword_text = input_tran.pre_dst
                    case "post_dst" | "post_zh":
                        find_ifword_text = input_tran.post_dst
                    case _:
                        raise ValueError(f"不支持的条件字典关键字{dic.special_key}")
                # 遍历if_word_list
                for if_word in dic.if_word_list:
                    # 因为如果有stratwith的话需要修改word，所以要新建一份副本
                    if_word_now = if_word.word
                    if if_word_now == "":
                        continue
                    if if_word.startswith_flag:
                        if dic.special_key in ("pre_src", "pre_jp"):
                            # 把left_symbol先拼接回去
                            if_word_now = input_tran.left_symbol + if_word_now
                        elif dic.special_key in ("post_src", "post_jp"):
                            # 需要给判断词加上对应的format
                            if input_tran.is_dialogue:
                                if_word_now = (
                                    input_tran.dia_format.split("#句子")[0]
                                    + if_word_now
                                )
                            else:
                                if_word_now = (
                                    input_tran.mono_format.split("#句子")[0]
                                    + if_word_now
                                )

                    if if_word_now in ["~", "(同上)", "（同上）"]:  # 同上flag判断
                        can_replace = True if last_one_success == True else False
                    elif if_word.startswith_flag:  # startswith
                        can_replace = find_ifword_text.startswith(if_word_now)
                    else:  # 默认为find
                        can_replace = if_word_now in find_ifword_text

                    if if_word.without_flag:  # 有without_flag时则取反
                        can_replace = not can_replace

                    # and中有一个是False就跳出
                    if dic.spl_word == "[and]" and can_replace == False:
                        break
                    elif (
                        dic.spl_word == "[or]" and can_replace == True
                    ):  # or中有一个是True就跳出
                        break

                if not can_replace:  # 条件不满足，跳到字典下一条
                    last_one_success = False
                    continue
                else:
                    last_one_success = True

            # 不管是不是特殊字典，替换部分都是一样的，跑到这里就是条件满足了：

            search_word = dic.search_word
            replace_word = dic.replace_word

            if dic.is_regex:
                # 正则词条：re.sub 替换；^^ 组合用 re.match 锚定开头（最左命中即开头）
                if dic.startswith_flag:
                    if dic.regex_pattern.match(input_text):
                        input_text = dic.regex_pattern.sub(replace_word, input_text, count=1)
                elif dic.onetime_flag:
                    input_text = dic.regex_pattern.sub(replace_word, input_text, count=1)
                elif not full_match:
                    input_text = dic.regex_pattern.sub(replace_word, input_text)
                elif dic.regex_pattern.fullmatch(input_text):
                    input_text = replace_word
            # startwith情况，只替换开头的
            elif dic.startswith_flag:
                len_search_word = len(search_word)
                len_input_text = len(input_text)
                if len_search_word > len_input_text:
                    continue  # 肯定不满足
                elif input_text[:len_search_word] == search_word:
                    input_text = input_text.replace(search_word, replace_word, 1)
            elif dic.onetime_flag:  # onetime情况，只替换一次
                input_text = input_text.replace(search_word, replace_word, 1)
            else:  # 普通情况
                if not full_match:
                    input_text = input_text.replace(search_word, replace_word)
                elif search_word == input_text:
                    input_text = replace_word

        return input_text


class CGptDict:
    def __init__(self, dic_list: list) -> None:
        self._dic_list: List[CBasicDicElement] = []
        for dic_path in dic_list:
            self.load_dic(dic_path)  # 加载字典

    def get_dst(self, word: str):
        for dic in self._dic_list:
            if dic.search_word == word:
                return dic.replace_word
        return ""

    def _is_h_dict(self, dic: CBasicDicElement) -> bool:
        """按字典文件名后缀判断是否 h 场景字典（含 _h 且非 _非h）。"""
        name = dic.dic_name or ""
        lower = name.lower()
        return "_h" in lower and "_非h" not in lower

    def sort_dic(self):
        self._dic_list.sort(key=lambda x: len(x.search_word), reverse=True)

    def load_dic(self, dic_path: str) -> None:
        if not path.exists(dic_path):
            LOGGER.warning(f"{dic_path}不存在，请检查路径。")
            return
        with open(dic_path, encoding="utf8") as f:
            dic_lines = f.readlines()
        if len(dic_lines) == 0:
            return

        dic_name = path.basename(dic_path)
        dic_name = path.splitext(dic_name)[0]
        normalDic_count = 0
        regexDic_count = 0

        for line in dic_lines:
            if line.startswith("\n"):
                continue
            # 整行注释（仅 // 前缀，即使含 |）跳过，与 parse_dict_line 统一
            if line.lstrip().startswith(_COMMENT_PREFIXES):
                continue
            # 纯符号分隔线跳过，与 load_h_check_words 口径一致
            if _is_separator_line(line):
                LOGGER.debug(f"字典 {dic_path} 跳过纯符号分隔线：{line.strip()}")
                continue

            # 兼容四个空格和Tab
            line = line.replace("    ", "\t")
            line = line.replace("\t", "|")
            # 兼容旧箭头格式 src->dst（# 已不再是注释/分隔符，不再替换）
            if "->" in line:
                line = line.replace("->", "|")

            sp = _split_dict_line(line.rstrip("\r\n"))  # 去多余换行符，|分割（\| 为字面竖线）
            len_sp = len(sp)

            if len_sp < 2:  # 至少是2个元素
                continue

            # 与 parse_dict_line gpt 分支对齐：各字段做转义处理
            search_word = _safe_escape(sp[0])
            replace_word = _safe_escape(sp[1])
            # 与 parse_dict_line gpt 分支一致：note 取第3列后全部（含 | 拼接）
            note = "|".join(_safe_escape(x) for x in sp[2:]) if len_sp > 2 else ""

            redundant_flag = False
            for d in self._dic_list:
                if d.search_word == search_word and d.replace_word == replace_word:
                    if d.note and d.note == note:
                        LOGGER.warning(f"重复的GPT字典词条 {search_word} -> {replace_word} 已忽略")
                        redundant_flag = True
                        break
            if redundant_flag:
                continue

            dic = CBasicDicElement(search_word, replace_word, dic_name=dic_name)
            if not _check_dic_element(dic, dic_path):
                continue
            dic.note = note
            self._dic_list.append(dic)
            normalDic_count += 1
            regexDic_count += 1 if dic.is_regex else 0
        LOGGER.info(
            f"载入 GPT字典: {path.basename(dic_path)} {normalDic_count}普通词条"
            + (f"（含{regexDic_count}正则词条）" if regexDic_count else "")
        )

    def gen_prompt(
        self, trans_list: CTransList, type: str = "gpt", scene: str = "all"
    ) -> str:
        """生成 glossary 提示词段落。

        Args:
            trans_list: 待翻译句子列表。
            type: 输出格式（gpt / sakura / tsv）。
            scene: 字典场景过滤：
                - "all" 全量字典（默认，兼容未指定场景的调用方）；
                - "nh" 仅非 h 场景字典（跳过文件名含 _h 的字典）；
                - "h"  h 场景：h 字典优先注入，非 h 字典补全；与 h 字典重合的词条只取 h 的译文。
        """
        def _should_add_dic(dic: CBasicDicElement, input_text: str, input_text_copy: str, used_dic: list[str]) -> bool:
            """判断是否应该添加字典条目到提示中"""
            if _dict_word_match(dic, input_text):
                return True
            # 短词先被消费时的兜底注入：已用词 ⊂ 当前词条搜索词（仅普通词条，正则无此语义）
            if not dic.is_regex and dic.search_word in input_text_copy:
                for word in used_dic:
                    if word in dic.search_word:
                        return True
            return False

        def _format_dic_entry_gpt(dic: CBasicDicElement) -> str:
            """格式化字典条目为提示所需的字符串"""
            entry = f"| {dic.search_word} | {dic.replace_word} |"
            if dic.note:
                entry += f" {dic.note}"
            entry += " |\n"
            return entry
        TITLE_GPT="# Glossary\n| Src | Dst(/Dst2/..) | Note |\n| --- | --- | --- |\n"
        def _format_dic_entry_tsv(dic: CBasicDicElement) -> str:
            """格式化字典条目为提示所需的字符串"""
            entry = f"{dic.search_word}\t{dic.replace_word}"
            if dic.note:
                entry += f"\t{dic.note}"
            entry += "\n"
            return entry
        TITLE_TSV="SRC\tDST\tNOTE\n"

        promt = ""
        input_text = "\n".join(
            [f"{tran.get_speaker_name()}:{tran.post_src}" for tran in trans_list]
        )
        input_text_copy=input_text
        used_dic=[]

        # scene 过滤与组内排序：nh 只取非 h；h 让 h 字典先遍历（h 优先）再补非 h；
        # 组内按搜索词长度降序（正则按模式串长度），消除对调用方 sort_dic 的依赖
        if scene == "nh":
            dic_iter = _sorted_by_len_desc([d for d in self._dic_list if not self._is_h_dict(d)])
        elif scene == "h":
            dic_iter = _sorted_by_len_desc([d for d in self._dic_list if self._is_h_dict(d)]) + _sorted_by_len_desc(
                [d for d in self._dic_list if not self._is_h_dict(d)]
            )
        else:
            dic_iter = _sorted_by_len_desc(self._dic_list)

        h_hit: set[str] = set()  # scene=h 时已加入的 h 词条（用于重合词 h 优先）
        for dic in dic_iter:
            # h 场景：非 h 词条若与已加入的 h 词条重合，只取 h 的译文/注释
            if scene == "h" and not self._is_h_dict(dic) and dic.search_word in h_hit:
                continue
            if _should_add_dic(dic, input_text, input_text_copy, used_dic):
                if type=="gpt":
                    promt += _format_dic_entry_gpt(dic)
                elif type=="tsv":
                    promt += _format_dic_entry_tsv(dic)
                input_text = _dict_word_consume(dic, input_text)
                if scene == "h" and self._is_h_dict(dic):
                    h_hit.add(dic.search_word)
            used_dic.append(dic.search_word)
        if promt:
            if type=="gpt":
                promt=TITLE_GPT+promt
            elif type=="tsv":
                promt=TITLE_TSV+promt


        return promt

    def check_dic_use(
        self, find_from_str: str, tran: CSentense, scene: str = "all", skip_overlap: bool = True
    ) -> str:
        """检查译文是否使用了源词在 GPT 字典中的替换词，返回未使用词条的提示文本。

        Args:
            find_from_str: 待检查文本（通常是译文）。
            tran: 当前句子（取 post_src 判定源词是否出现）。
            scene: 场景过滤，all 检查全部；h 检查 h 与 非 h（重合词条只按
                   h 检查，与 gen_prompt 注入侧一致）；nh 只检查非 h 字典。
            skip_overlap: True 按长词优先消费检查——长词条命中后从原文副本消费其
                覆盖范围，短的重叠词条不再重复检查（与注入侧 gen_prompt 口径一致）；
                False 保留旧口径：每条词条独立对完整原文检查，重叠词会重复检查。
        """
        if not skip_overlap:
            return self._check_dic_use_legacy(find_from_str, tran, scene)

        problem_list = []
        remaining = tran.post_src  # 消费副本：长词命中后删除其覆盖范围
        # h 组优先、组内长词降序（正则按模式串长度）：与注入侧 h 优先语义一致
        h_part = [d for d in self._dic_list if self._is_h_dict(d)]
        nh_part = [d for d in self._dic_list if not self._is_h_dict(d)]
        if scene == "nh":
            dic_iter = _sorted_by_len_desc(nh_part)
        elif scene == "h":
            dic_iter = _sorted_by_len_desc(h_part) + _sorted_by_len_desc(nh_part)
        else:
            dic_iter = _sorted_by_len_desc(self._dic_list)

        for dic in dic_iter:
            if not _dict_word_match(dic, remaining):
                continue
            replace_word_list = (
                dic.replace_word.split("/")
                if "/" in dic.replace_word
                else [dic.replace_word]
            )
            if not any(replace_word in find_from_str for replace_word in replace_word_list):
                problem_list.append(
                    f"{dic.dic_name}未使用：{dic.search_word}---{dic.replace_word}"
                )
            # 无论是否判定未使用都消费：长词条报错时其覆盖范围不再由短词条重复报
            remaining = _dict_word_consume(dic, remaining)

        return ", ".join(problem_list)

    def _check_dic_use_legacy(
        self, find_from_str: str, tran: CSentense, scene: str = "all"
    ) -> str:
        """旧口径的字典使用检查（skipOverlapCheck=false 时启用）：逐条独立检查，重叠词会重复检查。"""
        problem_list = []
        # 与注入侧 gen_prompt(scene="h") 对齐：源词被 h 词条覆盖时，非 h 同源词条不参与
        h_hit = set()
        if scene == "h":
            for dic in self._dic_list:
                if self._is_h_dict(dic) and _dict_word_match(dic, tran.post_src):
                    h_hit.add(dic.search_word)
        for dic in self._dic_list:
            if scene == "nh" and self._is_h_dict(dic):
                continue
            if scene == "h" and not self._is_h_dict(dic) and dic.search_word in h_hit:
                continue
            if not _dict_word_match(dic, tran.post_src):
                continue

            replace_word_list = (
                dic.replace_word.split("/")
                if "/" in dic.replace_word
                else [dic.replace_word]
            )

            flag = False
            for replace_word in replace_word_list:
                if replace_word in find_from_str:
                    flag = True
                    break

            if not flag:
                problem_list.append(
                    f"{dic.dic_name}未使用：{dic.search_word}---{dic.replace_word}"
                )

        return ", ".join(problem_list)
