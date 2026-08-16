"""翻译/元数据后端共用的纯工具函数。

与具体后端无耦合，可被 ForGalJsonMulitChat、ForBatchMetaData、ForFileMetaData 等自由复用。
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional, Tuple

from GalTransl import LOGGER


def detect_line_break_symbol(src_text: str) -> str:
    """
    检测原文中的换行符类型，返回用于后处理还原的换行符标记。

    将换行符判定集中到模块级函数，并在整批评句上**仅判定一次**：
    避免原来逐句判定时，因后续句子不含换行符而把已确定的 n_symbol 覆盖，
    导致解析阶段的换行符还原（<br> → 原换行符）出现错乱。

    优先级（与原逻辑一致）：
      "\\r\\n"（字面转义串，galgame 脚本中常见） > 实际 "\\r\\n" >
      "\\n"（字面转义串） > 实际 "\\n"

    Args:
        src_text: 待检测的完整原文（通常为整批评句的拼接）

    Returns:
        检测到的换行符标记；未检测到则返回 ""
    """
    if "\\r\\n" in src_text:
        return "\\r\\n"
    elif "\r\n" in src_text:
        return "\r\n"
    elif "\\n" in src_text:
        return "\\n"
    elif "\n" in src_text:
        return "\n"
    return ""


def detect_batch_line_break_symbol(post_src_list: List[str]) -> str:
    """
    对整批评句**仅判定一次**换行符类型，返回用于后处理还原的换行符标记。

    采用「逐句检测取首命中」，而**不是**把句子用 "\\n" 拼接后再检测：
    拼接分隔符 "\\n" 会混入检测串，使「字面 <br> 约定」的源（句子内容里是
    <br> 而非真实换行）被误判成 "\\n"，进而在解码阶段把 <br> 错误还原成真实
    换行，破坏源换行约定。

    逐句取首命中既保留了「整批统一单一 n_symbol」的语义，又不会引入拼接
    产生的伪换行符。

    Args:
        post_src_list: 当前批次所有句子的原文列表

    Returns:
        检测到的换行符标记；整批均无换行符则返回 ""
    """
    for src in post_src_list:
        s = detect_line_break_symbol(src)
        if s:
            return s
    return ""


# ── 共享区间工具（批次级元数据生成与多轮翻译分组复用）──
# 历史实现中，ForBatchMetaData 与 ForGalJsonMulitChat 各自重复实现区间解析，
# 对脏数据（非整数、长度不足、lo>hi 写反）的处理方式不一致且均静默丢弃。
# 下方函数统一收口，确保两模块对 LLM 产出区间的处理行为一致。


def parse_interval(raw: object) -> Optional[Tuple[int, int]]:
    """把区间字段（支持「区间」/interval，list 或 tuple）解析为 (lo, hi) 闭区间。

    统一处理两类常见情况：
      - 字段缺失 / 非 list·tuple / 长度不足 2 → 返回 None（调用方跳过该区间）
      - lo > hi（方向写反）→ 自动交换为 lo <= hi
    端点解析失败（非整数）时返回 None，调用方应跳过该区间并（可选）告警，
    而不是抛出未捕获异常。
    """
    if not isinstance(raw, (list, tuple)):
        return None
    if len(raw) < 2:
        return None
    try:
        lo = int(raw[0])
        hi = int(raw[1])
    except (TypeError, ValueError):
        return None
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _to_bool_meta(v: object) -> bool:
    """把多种表示的布尔字段规整为 bool（与批次元数据生成保持一致）。"""
    return coerce_bool(v, default=False)



def coerce_bool(value: object, default: bool = False) -> bool:
    """统一把多种配置写法转成 bool。

    与旧 BaseEngine._coerce_bool / utils._to_bool_meta 兼容，并补充常见中英文开关：
    true/false、1/0、yes/no、on/off、是/否、y/n。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("true", "1", "yes", "on", "是", "y"):
            return True
        if s in ("false", "0", "no", "off", "否", "n", ""):
            return False
        return default
    return default


def extract_json_object(
    text: str,
    tag: str = "",
    filename: str = "",
    merge_code_blocks: bool = False,
) -> Optional[dict]:
    """从 LLM 返回文本中稳健提取一个 JSON 对象。

    统一处理 ``</think>``、Markdown 代码块、首尾垃圾字符、``{}`` 边界定位。
    供 ForGlobalPrompt / ForFileMetaData / ForBatchMetaData / ForPlotRouteMap 等
    元数据类后端复用，避免各后端 JSON 解析口径不一致。
    """
    if not text or not text.strip():
        if filename:
            LOGGER.debug(f"[{tag}] {filename} LLM 返回为空，跳过")
        return None

    if "</think>" in text:
        text = text.split("</think>")[-1]

    if "```" in text:
        from GalTransl.Utils import extract_code_blocks
        _lang_list, code_list = extract_code_blocks(text)
        if code_list:
            text = "\n".join(code_list) if merge_code_blocks else code_list[0]

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        LOGGER.debug(
            f"[{tag}] {filename} LLM 返回中未找到 JSON 对象，"
            f"原文前 200 字：{text[:200]}"
        )
        return None

    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        LOGGER.debug(
            f"[{tag}] {filename} JSON 解析失败：{e}，"
            f"原文前 200 字：{text[start:end+1][:200]}"
        )
        return None

    if not isinstance(obj, dict):
        LOGGER.debug(f"[{tag}] {filename} 解析结果不是 dict，实际类型：{type(obj).__name__}")
        return None
    return obj


def preprocess_jsonline_response(text: str, merge_code_blocks: bool = True) -> str:
    """预处理 LLM 返回的 jsonline 文本：去 think、合并/提取代码块、定位锚点、修引号。

    翻译轮与稀疏修复轮都应使用同一入口，避免“取第一个代码块 vs 合并代码块”的差异。
    """
    if not text:
        return ""
    result = text
    if "</think>" in result:
        result = result.split("</think>")[-1]
    if "```" in result:
        from GalTransl.Utils import extract_code_blocks
        _lang_list, code_list = extract_code_blocks(result)
        if code_list:
            result = "\n".join(code_list) if merge_code_blocks else code_list[0]
    sig_start = re.search(r"\b[a-z0-9]{3}\|\{\"id\"", result)
    if sig_start:
        result = result[sig_start.start():]
    from GalTransl.Utils import fix_quotes
    result = fix_quotes(result)
    return result


def decode_json_line_part(json_part: str) -> Optional[dict]:
    """容错解析单行 JSON 对象。

    优先严格 ``json.loads``；失败时从首个 ``{`` 起用 ``raw_decode`` 解析第一个
    JSON 值并忽略尾随垃圾，避免模型输出 ``</br>``、``；`` 等多余字符时丢句。
    """
    try:
        obj = json.loads(json_part)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    start = json_part.find("{")
    if start == -1:
        return None
    try:
        obj, _end = json.JSONDecoder().raw_decode(json_part[start:])
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def strip_chunk_suffix(filename: str) -> str:
    """剥离分批后缀 ``_<数字>``（如 ``file.txt.json_0`` -> ``file.txt.json``）。

    文件级/批次级元数据按原始文件名 id 存放，而翻译阶段文件可能被切成
    ``file_0`` 之类的分片。两处元数据解析都应先剥离后缀再匹配，
    避免分片文件拿不到剧情/批次背景。
    """
    m = re.match(r"^(.*)_\d+$", filename)
    return m.group(1) if m else filename


def normalize_batch_intervals(
    raw_batches: object,
    filename: str,
    max_index: int,
    max_batches: int,
    tag: str = "BatchMetaData",
    min_batch_size: Optional[int] = None,
    max_batch_size: Optional[int] = None,
) -> List[dict]:
    """规整批次数组：清洗字段、裁剪并排序区间、重叠修复、长度约束（超长切分/过短合并）、最大批次数限制、间隙检测。

    把 ForBatchMetaData._normalize_meta 的区间处理逻辑收口为共享函数，供批次级
    元数据生成与多轮翻译分组复用，确保对脏数据/边界的处理行为一致。返回
    ``[{"区间":[lo,hi], "视角":str, "氛围":str, "h":bool, "用词色彩":str}, ...]``。
    """
    if not isinstance(raw_batches, list):
        raw_batches = []

    batches: List[dict] = []
    for b in raw_batches:
        if not isinstance(b, dict):
            continue
        rng = parse_interval(b.get("区间", b.get("interval", None)))
        if rng is None:
            LOGGER.debug(f"[{tag}] {filename} 区间字段非法，已跳过：{b.get('区间', b.get('interval'))!r}")
            continue
        lo, hi = rng
        lo = max(1, lo)
        if max_index > 0:
            hi = min(hi, max_index)
        if hi < lo:
            continue
        batches.append(
            {
                "区间": [lo, hi],
                "视角": str(b.get("视角", b.get("perspective", "")) or ""),
                "氛围": str(b.get("氛围", b.get("atmosphere", "")) or ""),
                "h": _to_bool_meta(b.get("h", b.get("H", False))),
                "用词色彩": str(b.get("用词色彩", b.get("tone", "")) or ""),
            }
        )

    batches.sort(key=lambda x: (x["区间"][0], x["区间"][1]))

    # 重叠修复：与相邻区间取并后的非重叠起点
    cleaned: List[dict] = []
    for b in batches:
        if not cleaned:
            cleaned.append(b)
            continue
        prev = cleaned[-1]
        cur_lo, cur_hi = b["区间"]
        prev_lo, prev_hi = prev["区间"]
        if cur_lo <= prev_hi:
            new_lo = prev_hi + 1
            if new_lo > cur_hi:
                LOGGER.warning(
                    f"[{tag}] {filename} 区间 [{prev_lo},{prev_hi}] "
                    f"与 [{cur_lo},{cur_hi}] 重叠，收缩后为空，已丢弃"
                )
                continue
            LOGGER.debug(
                f"[{tag}] {filename} 区间 [{cur_lo},{cur_hi}] "
                f"与 [{prev_lo},{prev_hi}] 重叠，收缩为 [{new_lo},{cur_hi}]"
            )
            b["区间"] = [new_lo, cur_hi]
        cleaned.append(b)

    # 超长区间标注：超过 max_batch_size 的区间不切分（保留自然边界），仅标注「区间过大」
    if max_batch_size and max_batch_size > 0:
        for b in cleaned:
            lo, hi = b["区间"]
            if hi - lo + 1 > max_batch_size:
                b["区间过大"] = True
                LOGGER.warning(
                    f"[{tag}] {filename} 区间 [{lo},{hi}] 行数超过 max_batch_size({max_batch_size})，"
                    f"已标注「区间过大」，翻译时该批次将整体发送"
                )

    # 过短区间合并：长度小于 min_batch_size 的区间，优先与「合并后不超过 max_batch_size
    # 且合并后总长最小」的相邻区间合并；找不到满足上限的相邻对则保留原区间并告警
    if min_batch_size and min_batch_size > 0:
        i = 0
        while i < len(cleaned):
            lo, hi = cleaned[i]["区间"]
            if hi - lo + 1 >= min_batch_size:
                i += 1
                continue
            options: List[Tuple[int, int]] = []
            if i > 0:
                prev_lo, _prev_hi = cleaned[i - 1]["区间"]
                options.append((hi - prev_lo + 1, i - 1))
            if i + 1 < len(cleaned):
                _nxt_lo, nxt_hi = cleaned[i + 1]["区间"]
                options.append((nxt_hi - lo + 1, i))
            options.sort(key=lambda o: (o[0], o[1]))
            best: Optional[Tuple[int, int]] = None
            for merged_len, target in options:
                if max_batch_size and max_batch_size > 0 and merged_len > max_batch_size:
                    continue
                best = (merged_len, target)
                break
            if best is None:
                LOGGER.warning(
                    f"[{tag}] {filename} 区间 [{lo},{hi}] 行数 {hi - lo + 1} 小于 "
                    f"min_batch_size({min_batch_size})，且合并将超过 max_batch_size，已保留原区间"
                )
                i += 1
                continue
            _merged_len, target = best
            if target == i - 1:
                cleaned[i - 1]["区间"][1] = hi
                LOGGER.debug(
                    f"[{tag}] {filename} 过短区间 [{lo},{hi}] 向左合并 → "
                    f"[{cleaned[i - 1]['区间'][0]},{hi}]"
                )
                del cleaned[i]
                i = max(0, i - 1)
            else:
                cleaned[i]["区间"][1] = cleaned[i + 1]["区间"][1]
                LOGGER.debug(
                    f"[{tag}] {filename} 过短区间 [{lo},{hi}] 向右合并 → "
                    f"[{lo},{cleaned[i]['区间'][1]}]"
                )
                del cleaned[i + 1]

    # 最大批次数限制：反复合并间距最小的两个相邻区间
    while len(cleaned) > max_batches:
        min_gap = float("inf")
        merge_idx = 0
        for i in range(len(cleaned) - 1):
            cur_lo, cur_hi = cleaned[i]["区间"]
            nxt_lo, nxt_hi = cleaned[i + 1]["区间"]
            gap = nxt_lo - cur_hi
            if gap < min_gap:
                min_gap = gap
                merge_idx = i
        merged = dict(cleaned[merge_idx])
        merged["区间"] = [merged["区间"][0], cleaned[merge_idx + 1]["区间"][1]]
        LOGGER.debug(
            f"[{tag}] {filename} 合并区间 "
            f"[{cleaned[merge_idx]['区间'][0]},{cleaned[merge_idx]['区间'][1]}] + "
            f"[{cleaned[merge_idx + 1]['区间'][0]},{cleaned[merge_idx + 1]['区间'][1]}] "
            f"→ [{merged['区间'][0]},{merged['区间'][1]}]"
        )
        cleaned[merge_idx] = merged
        del cleaned[merge_idx + 1]

    # 间隙检测：提示未覆盖的句子区间
    if not cleaned:
        LOGGER.warning(
            f"[{tag}] {filename} 无有效区间，全文（第 1～{max_index} 行）均无批次覆盖"
        )
    else:
        expected = 1
        gaps = []
        for b in cleaned:
            lo = b["区间"][0]
            if lo > expected:
                gaps.append((expected, lo - 1))
            expected = max(expected, b["区间"][1] + 1)
        if expected <= max_index:
            gaps.append((expected, max_index))
        if gaps:
            gap_desc = "、".join(
                f"第 {g[0]}～{g[1]} 行" if g[0] != g[1] else f"第 {g[0]} 行"
                for g in gaps
            )
            LOGGER.warning(
                f"[{tag}] {filename} 区间存在间隙：{gap_desc} 无批次覆盖。"
                f"这可能导致对应句子的翻译缺少批次级指导"
            )

    return cleaned