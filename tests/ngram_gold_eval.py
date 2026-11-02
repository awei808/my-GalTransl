#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""n-gram 语域检测 gold set 评估 CLI：AUC + 阈值扫描（不属于 pytest 自动收集）。

用法：
  python tests/ngram_gold_eval.py --gold n-gram-origin/_corpus/gold/ngram_gold_pending.jsonl
  python tests/ngram_gold_eval.py --gold ... --dupes ...ngram_gold_pending.jsonl.dupes.json
  python tests/ngram_gold_eval.py --gold ... --target-fpr 0.08 --gate 0.85

输入支持两种形态：标注用 JSONL（每行 `id`/`dst`/`label`），以及入库 fixture
（`{"items": [{"dst": ..., "label": ...}]}`）。label 正例取 natural / 1 /
positive / true，负例取 awkward / 0 / negative / false，空值计入「未标注」。

AUC 衡量排序质量；阈值扫描把 AUC 换算成可决策的口径：给定允许的误标率
（正例被判低于阈值），能拿到多少负例召回。默认部署阈值 0.044 是零标注池标定
的人类母语 p10，金标数据到位后应据扫描表重新选定。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from GalTransl import ngram_rules as nr

POSITIVE_LABELS = ("1", "positive", "natural", "true")
NEGATIVE_LABELS = ("0", "negative", "awkward", "false")
DEFAULT_FPR_GRID = "0.02,0.05,0.08,0.10,0.15,0.20"
# 与 DefaultProjectConfig 模板的 gpt.ngramThreshold 保持一致（改模板时同步）
DEFAULT_DEPLOY_THRESHOLD = 0.044


def load_gold(path: str) -> List[dict]:
    """读取 gold 文件，统一为 [{id, dst, label}]。"""
    items: List[dict] = []
    if path.endswith(".jsonl"):
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
        return items
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    for pos, item in enumerate(data.get("items") or []):
        item.setdefault("id", pos + 1)
        item.setdefault("dst", item.get("text", ""))
        items.append(item)
    return items


def label_of(item: dict) -> str:
    """归一化标签：positive / negative / 空（未标注或非法值）。"""
    raw = str(item.get("label", "")).strip().lower()
    if raw in POSITIVE_LABELS:
        return "positive"
    if raw in NEGATIVE_LABELS:
        return "negative"
    return ""


def score_gold(
    items: List[dict], index: nr.NgramIndex, lam: float, skip_ids: Optional[set] = None
) -> Tuple[List[float], List[float], Dict[str, int]]:
    """按标签分池打分；skip_ids 内的重复句不参与统计（只用于一致性校验）。"""
    pos: List[float] = []
    neg: List[float] = []
    skip_ids = skip_ids or set()
    stats = {"total": len(items), "unlabeled": 0, "short": 0, "dupes": 0}
    for item in items:
        if int(item.get("id", 0)) in skip_ids:
            stats["dupes"] += 1
            continue
        label = label_of(item)
        if not label:
            stats["unlabeled"] += 1
            continue
        score = nr.score_text(item.get("dst", "") or "", index, lam)
        if score is None:
            stats["short"] += 1
            continue
        (pos if label == "positive" else neg).append(score)
    return pos, neg, stats


def _rates(pos: List[float], neg: List[float], threshold: float) -> Tuple[float, float]:
    """返回 (误标率, 召回)：误标=正例评分低于阈值，召回=负例评分低于阈值。"""
    fpr = sum(1 for s in pos if s < threshold) / len(pos) if pos else 0.0
    recall = sum(1 for s in neg if s < threshold) / len(neg) if neg else 0.0
    return fpr, recall


def best_threshold(pos: List[float], neg: List[float], target_fpr: float) -> Tuple[float, float, float]:
    """在误标率不超过 target 的前提下取最高阈值（= 召回最大）；返回 (阈值, 误标率, 召回)。"""
    if not pos or not neg:
        return 0.0, 0.0, 0.0
    # 候选阈值取正例分数（误标率只在正例分数处变化）+ 高于最大正例的边界值
    candidates = sorted(set(pos)) + [max(pos) + 1e-6]
    best = None
    for threshold in candidates:
        fpr, recall = _rates(pos, neg, threshold)
        if fpr <= target_fpr and (best is None or threshold > best[0]):
            best = (threshold, fpr, recall)
    return best if best is not None else (candidates[0], 0.0, 0.0)


def load_dupes(path: str) -> Dict[int, int]:
    """读取重复映射 {重复 id: 原始 id}；缺失时给中文提示而不是堆栈。"""
    if not os.path.isfile(path):
        raise SystemExit(f"重复映射文件不存在：{path}")
    with open(path, "r", encoding="utf-8") as fh:
        return {int(k): int(v) for k, v in json.load(fh).items()}


def consistency(items: List[dict], mapping: Dict[int, int]) -> Tuple[int, int, List[Tuple[int, int]]]:
    """重复句标注一致率；返回 (一致数, 可比较数, 不一致的 (重复 id, 原始 id) 列表)。"""
    labels = {int(item.get("id", 0)): label_of(item) for item in items}
    agree = 0
    total = 0
    bad: List[Tuple[int, int]] = []
    for dup_id, orig_id in mapping.items():
        a, b = labels.get(dup_id, ""), labels.get(orig_id, "")
        if not a or not b:
            continue
        total += 1
        if a == b:
            agree += 1
        else:
            bad.append((dup_id, orig_id))
    return agree, total, bad


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True, help="gold JSONL 或 fixture JSON")
    parser.add_argument("--index-dir", default=nr.default_index_dir(), help="索引目录")
    parser.add_argument("--lam", type=float, default=nr.DEFAULT_LAMBDA, help="背景项系数 λ")
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_DEPLOY_THRESHOLD, help="对照用的当前部署阈值"
    )
    parser.add_argument("--target-fpr", type=float, default=0.08, help="可接受的误标率预算（推荐阈值用）")
    parser.add_argument("--fpr-grid", default=DEFAULT_FPR_GRID, help="扫描表的目标误标率档位（逗号分隔）")
    parser.add_argument(
        "--dupes",
        help="重复映射文件；省略时自动找 <gold>.dupes.json。重复句不参与 AUC，只用于一致率",
    )
    parser.add_argument("--gate", type=float, help="AUC 门槛：低于该值返回码 1（待 gold 入库后用于 CI）")
    args = parser.parse_args(argv)

    index, err = nr.load_index(args.index_dir)
    if index is None:
        print(f"无法加载索引：{err}", file=sys.stderr)
        return 1

    items = load_gold(args.gold)
    dupes_path = args.dupes or (args.gold + ".dupes.json")
    mapping: Dict[int, int] = {}
    if os.path.isfile(dupes_path):
        mapping = load_dupes(dupes_path)
    elif args.dupes:
        load_dupes(dupes_path)  # 显式指定却缺失：直接报错退出

    pos, neg, stats = score_gold(items, index, args.lam, skip_ids=set(mapping))
    if not pos or not neg:
        print(f"正例 {len(pos)} / 负例 {len(neg)} 不足，无法评估（先完成标注）", file=sys.stderr)
        return 1

    auc = nr.auc(pos, neg)
    print("=== gold set 评估 ===")
    print(f"文件：{args.gold}")
    print(
        f"样本 {stats['total']}：未标注 {stats['unlabeled']}，不可打分 {stats['short']}，"
        f"重复句排除 {stats['dupes']}，正例 {len(pos)}，负例 {len(neg)}"
    )
    print(f"AUC：{auc:.4f}（0.5 不可分，1.0 完全可分）")
    if stats["unlabeled"]:
        print(f"提示：仍有 {stats['unlabeled']} 条未标注，结论会随标注补齐而变")

    print()
    print("--- 阈值扫描（误标率 = 正例被判「语域异常」；召回 = 负例被标出）---")
    print(f"{'目标误标':<10}{'阈值':<10}{'实测误标':<10}{'召回':<10}")
    grid = [float(x) for x in args.fpr_grid.split(",") if x.strip()]
    for target in grid:
        threshold, fpr, recall = best_threshold(pos, neg, target)
        print(f"{target:<10.2f}{threshold:<10.3f}{fpr:<10.3f}{recall:<10.3f}")

    fpr_now, recall_now = _rates(pos, neg, args.threshold)
    print()
    print(f"当前阈值 {args.threshold:.3f}：误标 {fpr_now:.3f}，召回 {recall_now:.3f}")
    rec_t, rec_fpr, rec_recall = best_threshold(pos, neg, args.target_fpr)
    allowance = int(args.target_fpr * len(pos))
    print(
        f"按误标预算 {args.target_fpr:.2f}（正例 {len(pos)} 句，至多误标 {allowance} 句）"
        f"推荐阈值 {rec_t:.3f}（实测误标 {rec_fpr:.3f}，召回 {rec_recall:.3f}）"
    )

    if mapping:
        agree, total, bad = consistency(items, mapping)
        if total:
            print()
            print(f"标注一致性：{agree}/{total} = {agree / total:.1%}（仅供参考，不是硬门槛）")
            for dup_id, orig_id in bad[:10]:
                print(f"  不一致：重复 #{dup_id} vs 原始 #{orig_id}")
        else:
            print()
            print("标注一致性：无可比较的重复句（重复句尚未标注）")

    if args.gate is not None and auc < args.gate:
        print(f"未达 AUC 门槛 {args.gate:.3f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
