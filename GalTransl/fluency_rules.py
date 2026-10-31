"""中文通顺初筛规则：为 ForFluencyCheck 提供送检候选句。

两级流水线的第一级：只做高召回的正则/词表粗筛，命中即候选，不产生任何问题
标记——未经 LLM 复核的规则命中不得进入问题列表（复核由 ForFluencyCheck 完成）。
类别口径与通顺检测提示词的判据一致（A-D 初筛；E 类由 LLM 复核时判定）。
本模块是"活的词表"：golden set 度量初筛召回，漏检时在此补模式，正则召回即
系统召回上限。

dst 口径与检测轮证据校验一致：proofread_zh 优先，回退 pre_dst。
"""

import re
from typing import Dict, List, Optional, Tuple

# A 轻动词冗余：轻动词 + 名词化宾语（宾语本身是动词性的，删轻动词信息不减）。
# 了 允许插在中间（进行了讨论/达到了高潮）
_LIGHT_VERB_RE = re.compile(
    "(达到|进行|产生|做出|给予|加以)了?"
    "(高潮|绝顶|快感|刺激|抽送|摩擦|爱抚|律动|讨论|插入|结合|交合|侵犯|贯穿|抽插)"
)

# B 判断句冗赘：是不是/是否 … 的 收尾（句末或句末标点/闭引号前）。强调句的正当用法
# （是昨天告诉我的）由 LLM 复核否决，初筛只管召回
_JUDGE_TAIL_RE = re.compile(
    r"(是不是|是否)[^，。！？…、；\n]{0,8}的[。！？…，、；」』）]?\s*$"
)

# C 搭配冲突：动宾/主谓搭配在现代汉语中不成立的种子搭配（人"推向"高潮→
# 气氛/事态才能推向）。主语是否为气氛由 LLM 复核判断，初筛按字面量召回
# （re.escape：词表后续扩充可能引入正则元字符，按字面量匹配）
_CONFLICT_COLLOCATIONS: Tuple[str, ...] = ("推向高潮",)
_CONFLICT_RE = re.compile(
    "(" + "|".join(re.escape(c) for c in _CONFLICT_COLLOCATIONS) + ")"
)

# 逐行匹配的规则（按特异性排序，首个命中归档该类别）
_LINE_RULES: Tuple[Tuple[str, re.Pattern], ...] = (
    ("A", _LIGHT_VERB_RE),
    ("B", _JUDGE_TAIL_RE),
    ("C", _CONFLICT_RE),
)

# D 虚词堆叠：同一行内两组标记同时出现才候选（单出现是合法用法）
# 注：「已经相当/非常…了」是标准完成体（已经非常兴奋了），不再作为初筛判据；
# 真正的冗赘形态是「是/到了/处于 + 程度 + …的状态」这类名词化（见下）。
_D_CO_OCCURRENCE: Tuple[Tuple[re.Pattern, re.Pattern], ...] = (
    (re.compile(r"对于[^，。！？\n]{1,12}来说"), re.compile(r"在[^，。！？\n]{1,12}中")),
)

# D 状态名词化：「是/到了/处于相当…的状态」——状态名词多余，改为形容词/动词更自然
_D_NOMINAL_STATE_RE = re.compile(
    r"(?:是|到了|处于)(?:相当|十分|非常|特别)[^，。！？\n]{1,8}的状态"
)

# D 连续"～地"状语：同一行出现 ≥2 个"X地"结构（剧烈地+缓缓地）
# 排除"的地"（如"舒服的地方"），否则名词短语会被误算成第二个状语
_ADVERBIAL_DI_RE = re.compile(r"[\u4e00-\u9fa5]{1,3}(?<!的)地")
_ADVERBIAL_DI_MIN = 2


def _match_line(line: str) -> Optional[str]:
    """判断单行命中哪类初筛规则，返回类别字母（A-D），无命中返回 None。"""
    for category, pattern in _LINE_RULES:
        if pattern.search(line):
            return category
    for first, second in _D_CO_OCCURRENCE:
        if first.search(line) and second.search(line):
            return "D"
    if _D_NOMINAL_STATE_RE.search(line):
        return "D"
    if len(_ADVERBIAL_DI_RE.findall(line)) >= _ADVERBIAL_DI_MIN:
        return "D"
    return None


def match_fluency_category(dst: str) -> Optional[str]:
    """判断译文命中哪类初筛规则：逐行匹配，返回类别字母（A-D），无命中返回 None。"""
    if not dst:
        return None
    for line in dst.split("\n"):
        category = _match_line(line)
        if category:
            return category
    return None


def find_fluency_candidates(trans_list: list) -> Tuple[List, Dict[str, int]]:
    """从有效译文句中筛出初筛候选：返回 (候选句列表, 各类别命中句数)。

    多类命中按首个命中的类别归档一次（初筛类别仅供日志与统计，LLM 复核时
    重新定类）。无 dst 口径译文的句子直接跳过。
    """
    candidates: List = []
    counts: Dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0}
    for tran in trans_list:
        dst = tran.proofread_zh or tran.pre_dst or ""
        category = match_fluency_category(dst)
        if category:
            candidates.append(tran)
            counts[category] += 1
    return candidates, counts
