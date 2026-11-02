"""n-gram 语域评分核心：char 级 1-3 gram 分层索引与打分（纯函数模块）。

与 fluency_rules 的分工：规则初筛=句法搭配（LLM 通顺检测的召回上限），本模块
=语域分布统计（本地、零 API 成本），LLM 自然度判定与之相互独立——三者为解耦
设计：ngramcheck 不喂养 fluencycheck 候选，可分开执行，结果相互印证。

分层路由由索引元数据中的 role 快照驱动（建索引时从 weights.json 固化，运行时
不依赖 weights.json）：
  positive / native_positive / translated_positive -> 正样本桶
  native_background / backoff_only                 -> 背景桶
  h_only                                           -> H 桶（仅 H 文本混入）
  baseline / 未列 role                              -> 不参与打分（反样本/基线用途）

打分：score = mean_n( log P_pos_n − λ·log P_bg_n )，P 为桶内按层权重混合的
add-α 平滑分布，n 为 1-3 gram 阶；分数越高越接近正样本语域。索引由
tools/ngram_scorer.py --build-index 产出（index_meta.json + 每层压缩 JSON）。

dst 口径与 fluency_rules 一致：proofread_zh 优先，回退 pre_dst。
"""

import json
import logging
import math
import os
import unicodedata
from typing import Dict, List, Optional, Tuple

LOGGER = logging.getLogger(__name__)

# 桶路由：role -> 桶名；未列出的 role（baseline 等）不参与打分
ROLE_BUCKETS: Dict[str, str] = {
    "positive": "pos",
    "native_positive": "pos",
    "translated_positive": "pos",
    "native_background": "bg",
    "backoff_only": "bg",
    "h_only": "h",
}

INDEX_META_NAME = "index_meta.json"
MAX_N = 3
ALPHA = 1.0            # add-α 平滑强度
DEFAULT_LAMBDA = 1.0   # 背景项系数：score = log P_pos − λ·log P_bg（分离度报告网格最优；
                       # 与 DefaultProjectConfig 模板默认一致，改 λ 后需重新标定阈值）
MIN_SENT_CHARS = 6     # 少于该数字数的句子统计不可靠，不打分
ORDER_WEIGHTS = (1.0, 1.0, 1.0)  # 1/2/3 gram 阶等权


def default_index_dir() -> str:
    """程序目录下的默认索引位置（GalTransl 包上一级 = 仓库/程序根）。"""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "n-gram-origin", "_corpus", "ngram_index",
    )


def normalize_text(text: str) -> str:
    """NFC 归一并去除全部空白（语料行本身无空白，运行时译文需对齐口径）。"""
    return "".join(unicodedata.normalize("NFC", text).split())


def han_char_count(text: str) -> int:
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def grams_of(text: str, order: int) -> List[str]:
    if len(text) < order:
        return []
    return [text[i : i + order] for i in range(len(text) - order + 1)]


def is_h_text(text: str, h_hint: List[str]) -> bool:
    """H 场景提示：命中任一 H 词典词即按 H 模式混入 h_only 层。"""
    return bool(h_hint) and any(w in text for w in h_hint)


class NgramIndex:
    """已加载的分层索引：各层词表 + 桶混合所需的统计快照。"""

    def __init__(self, index_dir: str, meta: Dict, tables: Dict[str, Dict[str, int]]) -> None:
        self.index_dir = index_dir
        self.meta = meta
        self.tables = tables
        # 桶 -> [(层名, 权重, 词表, {阶: (unique, total)})]
        self.mixes: Dict[str, List[Tuple[str, float, Dict[str, int], Dict[int, Tuple[int, int]]]]] = {
            "pos": [], "bg": [], "h": []
        }
        for name, info in meta.get("layers", {}).items():
            bucket = info.get("bucket")
            if bucket not in self.mixes:
                continue
            table = tables.get(name, {})
            order_stats = {}
            for order_key, stats in (info.get("orders") or {}).items():
                order_stats[int(order_key)] = (int(stats.get("unique", 0)), int(stats.get("total", 0)))
            self.mixes[bucket].append(
                (name, float(info.get("weight", 0.0)), table, order_stats)
            )
        self.h_hint: List[str] = list(meta.get("h_hint") or [])

    def bucket_logprob(self, bucket: str, gram: str, extra: Optional[List] = None) -> float:
        """桶内按层权重混合的 add-α 平滑 log 概率（零计数层不参与分母，避免稀释）。"""
        entries = list(self.mixes.get(bucket, ()))
        if extra:
            entries = entries + extra
        order = len(gram)
        acc = 0.0
        weight_sum = 0.0
        for (_name, w, table, order_stats) in entries:
            if not order_stats:
                continue
            unique, total = order_stats.get(order, (0, 0))
            if total <= 0:
                continue
            p = (table.get(gram, 0) + ALPHA) / (total + ALPHA * unique)
            acc += w * p
            weight_sum += w
        if weight_sum <= 0 or acc <= 0:
            return 0.0
        return math.log(acc / weight_sum)

    def effective_pos_mix(self, use_h: bool) -> Optional[List]:
        """正样本桶混合项；H 文本把 h_only 层并入正样本侧。"""
        if not use_h or not self.mixes.get("h"):
            return None
        return self.mixes["h"]


def score_text(text: str, index: NgramIndex, lam: float = DEFAULT_LAMBDA) -> Optional[float]:
    """对单句打分；汉字数不足 MIN_SENT_CHARS 返回 None（统计不可靠，不判）。"""
    text = normalize_text(text)
    if han_char_count(text) < MIN_SENT_CHARS:
        return None
    use_h = is_h_text(text, index.h_hint)
    h_mix = index.effective_pos_mix(use_h)
    order_scores: List[float] = []
    for order, order_w in enumerate(ORDER_WEIGHTS, start=1):
        grams = grams_of(text, order)
        if not grams or order_w <= 0:
            continue
        pos = sum(index.bucket_logprob("pos", g, extra=h_mix) for g in grams) / len(grams)
        bg = sum(index.bucket_logprob("bg", g) for g in grams) / len(grams)
        order_scores.append(order_w * (pos - lam * bg))
    if not order_scores:
        return None
    return sum(order_scores) / sum(ORDER_WEIGHTS[: len(order_scores)])


def find_ngram_flagged(
    trans_list: List, index: NgramIndex, threshold: float, lam: float = DEFAULT_LAMBDA
) -> Tuple[List[Tuple[object, float]], Dict[str, int]]:
    """对译文列表打分并挑出低于阈值的句子。

    dst 口径与 fluency_rules 一致：proofread_zh 优先，回退 pre_dst。

    Returns:
        (flagged, stats)：flagged 为 (句子对象, 分数) 列表，按分数升序；
        stats 记录 total/scored/skipped_short/flagged。
    """
    flagged: List[Tuple[object, float]] = []
    stats = {"total": 0, "scored": 0, "skipped_short": 0, "flagged": 0}
    for tran in trans_list:
        stats["total"] += 1
        dst = getattr(tran, "proofread_zh", "") or getattr(tran, "pre_dst", "") or ""
        if not dst:
            stats["skipped_short"] += 1
            continue
        score = score_text(dst, index, lam)
        if score is None:
            stats["skipped_short"] += 1
            continue
        stats["scored"] += 1
        if score < threshold:
            flagged.append((tran, score))
    flagged.sort(key=lambda x: x[1])
    stats["flagged"] = len(flagged)
    return flagged, stats


def auc(pos_scores: List[float], neg_scores: List[float]) -> float:
    """Mann-Whitney AUC：pos 池分数期望高于 neg 池；0.5=不可分。

    金标评估（tests/ngram_gold_eval.py）与离线分离度报告（tools/ngram_scorer.py）
    共用此实现，避免两处口径分叉。
    """
    if not pos_scores or not neg_scores:
        return 0.5
    wins = 0.0
    for p in pos_scores:
        for s in neg_scores:
            if p > s:
                wins += 1.0
            elif p == s:
                wins += 0.5
    return wins / (len(pos_scores) * len(neg_scores))


# ---- 索引 IO ----------------------------------------------------------------

def layer_table_path(index_dir: str, layer: str) -> str:
    return os.path.join(index_dir, f"{layer}.ngram.json")


def save_layer_table(index_dir: str, layer: str, table: Dict[str, int]) -> str:
    """按计数降序写出层词表（平行数组，压缩体积）。"""
    path = layer_table_path(index_dir, layer)
    items = sorted(table.items(), key=lambda kv: (-kv[1], kv[0]))
    payload = {
        "grams": [g for g, _c in items],
        "counts": [c for _g, c in items],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    return path


def load_layer_table(path: str) -> Dict[str, int]:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return dict(zip(payload["grams"], payload["counts"]))


def write_index_meta(index_dir: str, meta: Dict) -> str:
    path = os.path.join(index_dir, INDEX_META_NAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    return path


# 索引加载缓存：保留最近 1 个目录（索引表大，多目录并存易撑爆内存）
_INDEX_CACHE: Dict[Tuple[str, float], Tuple[Optional["NgramIndex"], str]] = {}


def _remember_index(
    cache_key: Tuple[str, float], result: Tuple[Optional["NgramIndex"], str]
) -> None:
    if _INDEX_CACHE:
        _INDEX_CACHE.clear()
    _INDEX_CACHE[cache_key] = result


def load_index(index_dir: str) -> Tuple[Optional["NgramIndex"], str]:
    """加载索引；失败返回 (None, 原因)，供步骤优雅禁用。

    模块级缓存（键 = 目录 + 元数据 mtime）：后处理按文件实例化 ForNgramCheck，
    无缓存时每文件重复解析索引（约 1s/次）；索引不可变，mtime 变化即失效重建。
    调用方均在事件循环线程内同步调用（无 await 点），不存在并发竞态。
    """
    meta_path = os.path.join(index_dir, INDEX_META_NAME)
    if not os.path.isfile(meta_path):
        return None, f"索引不存在：{index_dir}（先用 tools/ngram_scorer.py --build-index 构建）"
    cache_key = (os.path.abspath(index_dir), os.path.getmtime(meta_path))
    cached = _INDEX_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        tables: Dict[str, Dict[str, int]] = {}
        for name, info in (meta.get("layers") or {}).items():
            path = layer_table_path(index_dir, name)
            if not os.path.isfile(path):
                result = (None, f"索引层文件缺失：{os.path.basename(path)}")
                _remember_index(cache_key, result)
                return result
            tables[name] = load_layer_table(path)
        index = NgramIndex(index_dir, meta, tables)
        if not index.mixes.get("pos"):
            result = (None, "索引无正样本桶（weights.json role 路由结果为空）")
            _remember_index(cache_key, result)
            return result
        result = (index, "")
        _remember_index(cache_key, result)
        return result
    except Exception as e:  # 损坏索引不应让流水线失败
        LOGGER.warning("n-gram 索引加载失败：%s", e)
        result = (None, f"索引加载失败：{e}")
        _remember_index(cache_key, result)
        return result
