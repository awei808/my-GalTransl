#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""句子级自然度指标对比台：AUC + 成对准确率 + 误标预算（不属于 pytest 自动收集）。

用法：
  # 单指标（旧用法：仍输出完整阈值扫描与一致性；表格排版已调整）
  python tests/ngram_gold_eval.py --gold tests/fixtures/ngram_gold_set.json
  # 多指标对比（外部分数文件，每行 {"text": "原句", "score": 0.73}）
  python tests/ngram_gold_eval.py --gold <gold> --pairs <revision_pairs.jsonl> \\
      --scores qemind=.../scores_qemind.jsonl --scores qwen4b=.../scores_qwen4b.jsonl
  # 本机没有 n-gram 索引时只跑外部指标
  python tests/ngram_gold_eval.py --gold <gold> --no-ngram --scores qemind=.../scores.jsonl

协议：
- 所有指标分数统一为「越高越自然」；PPL / 错误分这类越低越自然的用 --invert NAME 取反。
- 分数文件按 normalize_text(text) 建索引，gold 的 dst 与 pairs 的 before/after 共用
  同一份分数；缺分数的条目跳过并在覆盖率里报告。
- 各指标可在各自隔离环境离线打分（HF / 魔搭模型不必装进发布包依赖）。

三张表：
  1) AUC：gold 上正负例的排序质量（Mann-Whitney）
  2) 成对准确率：人工修订对上「改后分 > 改前分」的比例（并列单列）
  3) 误标预算：给定允许误标率时的推荐阈值、实测误标率与负例召回

输入 gold 支持 JSONL（id/dst/label）与 fixture JSON（items）；pairs 为
ngram_revision_pairs.py 的输出，默认只取 included=true 的行。
label 正例取 natural / 1 / positive / true，负例取 awkward / 0 / negative / false。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Set, Tuple

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
        row = dict(item)
        row.setdefault("id", pos + 1)
        row.setdefault("dst", item.get("text", ""))
        items.append(row)
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
    items: List[dict], index: nr.NgramIndex, lam: float, skip_ids: Optional[Set[int]] = None
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


def load_pairs(path: str, include_excluded: bool = False) -> List[dict]:
    """读取人工修订成对集，默认只取 included=true 的行。

    include_excluded 时合并同名 sidecar（采集脚本把排除项写在 <path>.excluded.jsonl）。
    """
    with open(path, "r", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    if include_excluded:
        sidecar = path + ".excluded.jsonl"
        if os.path.isfile(sidecar):
            with open(sidecar, "r", encoding="utf-8") as fh:
                rows += [json.loads(line) for line in fh if line.strip()]
        return rows
    return [r for r in rows if r.get("included", True)]


def load_metric_scores(path: str) -> Tuple[Dict[str, float], Dict[str, int]]:
    """读取外部指标分数：每行 {"text"|"key": 原句, "score": 数值}。

    键统一 normalize_text，非法/缺分行跳过并计数，避免坏行让整轮评估崩掉。
    """
    if not os.path.isfile(path):
        raise SystemExit(f"分数文件不存在：{path}")
    scores: Dict[str, float] = {}
    stats = {"rows": 0, "used": 0, "skipped": 0, "duplicated": 0}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            stats["rows"] += 1
            try:
                row = json.loads(line)
                text = row.get("text") or row.get("key") or ""
                value = float(row["score"])
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                stats["skipped"] += 1
                continue
            key = nr.normalize_text(str(text))
            if not key:
                stats["skipped"] += 1
                continue
            if key in scores:
                stats["duplicated"] += 1
            scores[key] = value
            stats["used"] += 1
    return scores, stats


def build_ngram_scores(
    texts: List[str], index: nr.NgramIndex, lam: float
) -> Dict[str, float]:
    """内置 n-gram 指标：对去重文本打分（score_text 返回 None 的短句视作缺分）。"""
    scores: Dict[str, float] = {}
    for text in set(texts):
        key = nr.normalize_text(text)
        if not key or key in scores:
            continue
        value = nr.score_text(text, index, lam)
        if value is not None:
            scores[key] = value
    return scores


def lookup(scores: Dict[str, float], text: str) -> Optional[float]:
    """按归一化文本查分数；缺失返回 None。"""
    key = nr.normalize_text(text)
    return scores.get(key) if key else None


def gold_pools_for(
    items: List[dict], scores: Dict[str, float], skip_ids: Optional[Set[int]] = None
) -> Tuple[List[float], List[float], Dict[str, int]]:
    """某指标在 gold 上的正负分数池与覆盖率统计。"""
    skip_ids = skip_ids or set()
    pos: List[float] = []
    neg: List[float] = []
    stats = {"total": len(items), "usable": 0, "missing": 0, "skipped": 0}
    for item in items:
        if int(item.get("id", 0)) in skip_ids or not label_of(item):
            stats["skipped"] += 1
            continue
        value = lookup(scores, item.get("dst", "") or "")
        if value is None:
            stats["missing"] += 1
            continue
        stats["usable"] += 1
        (pos if label_of(item) == "positive" else neg).append(value)
    return pos, neg, stats


def pair_result_for(pairs: List[dict], scores: Dict[str, float]) -> Tuple[int, int, int, int]:
    """返回 (改后更优, 改后更差, 并列, 缺分数) 的对数。"""
    better = worse = tie = missing = 0
    for pair in pairs:
        before = lookup(scores, pair.get("before", "") or "")
        after = lookup(scores, pair.get("after", "") or "")
        if before is None or after is None:
            missing += 1
        elif after > before:
            better += 1
        elif after < before:
            worse += 1
        else:
            tie += 1
    return better, worse, tie, missing


def _row(label: str, *cells: str) -> str:
    return f"{label:<14}" + "".join(f"{cell:<12}" for cell in cells)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True, help="gold JSONL 或 fixture JSON")
    parser.add_argument("--pairs", help="人工修订成对集（ngram_revision_pairs.py 输出）")
    parser.add_argument("--pairs-all", action="store_true", help="合并 sidecar 中被排除的修订对一起统计")
    parser.add_argument("--index-dir", default=nr.default_index_dir(), help="内置 n-gram 索引目录")
    parser.add_argument("--lam", type=float, default=nr.DEFAULT_LAMBDA, help="背景项系数 λ")
    parser.add_argument("--no-ngram", action="store_true", help="不计算内置 n-gram 指标（只跑外部分数）")
    parser.add_argument(
        "--scores", action="append", default=[], metavar="NAME=PATH",
        help="外部指标分数 JSONL，可多次指定；每行 {\"text\": 原句, \"score\": 数值}",
    )
    parser.add_argument(
        "--invert", action="append", default=[], metavar="NAME",
        help="该指标分数取反（越低越自然的指标，如 PPL / 错误分）",
    )
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_DEPLOY_THRESHOLD, help="对照用的当前部署阈值"
    )
    parser.add_argument("--target-fpr", type=float, default=0.08, help="可接受的误标率预算")
    parser.add_argument("--fpr-grid", default=DEFAULT_FPR_GRID, help="扫描表的目标误标率档位（逗号分隔）")
    parser.add_argument("--grid-metric", help="单指标模式下输出完整扫描表的指标名")
    parser.add_argument(
        "--dupes",
        help="重复映射文件；省略时自动找 <gold>.dupes.json。重复句不参与 AUC，只用于一致率",
    )
    parser.add_argument("--gate", type=float, help="AUC 门槛：任一指标低于该值返回码 1（CI 用）")
    args = parser.parse_args(argv)

    items = load_gold(args.gold)
    pairs = load_pairs(args.pairs, args.pairs_all) if args.pairs else []
    dupes_path = args.dupes or (args.gold + ".dupes.json")
    mapping: Dict[int, int] = {}
    if os.path.isfile(dupes_path):
        mapping = load_dupes(dupes_path)
    elif args.dupes:
        load_dupes(dupes_path)  # 显式指定却缺失：直接报错退出

    metrics: Dict[str, Dict[str, float]] = {}
    if not args.no_ngram:
        index, err = nr.load_index(args.index_dir)
        if index is None:
            if not args.scores:
                print(f"无法加载索引：{err}", file=sys.stderr)
                return 1
            print(f"[提示] 索引不可用，跳过内置 n-gram：{err}", file=sys.stderr)
        else:
            texts = [it.get("dst", "") or "" for it in items]
            texts += [p.get(k, "") or "" for p in pairs for k in ("before", "after")]
            metrics["ngram"] = build_ngram_scores(texts, index, args.lam)

    for spec in args.scores:
        if "=" not in spec:
            raise SystemExit(f"--scores 需要 NAME=PATH 格式：{spec}")
        name, path = spec.split("=", 1)
        name = name.strip()
        if not name:
            raise SystemExit(f"--scores 指标名不能为空：{spec}")
        scores, stats = load_metric_scores(path)
        print(f"[载入] {name}: 用 {stats['used']} 行，跳过 {stats['skipped']} 行，"
              f"重复键 {stats['duplicated']} 行（{path}）")
        metrics[name] = scores
    for name in args.invert:
        if name not in metrics:
            raise SystemExit(f"--invert 找不到指标 {name}；已载入：{', '.join(metrics)}")
        metrics[name] = {k: -v for k, v in metrics[name].items()}
    if not metrics:
        raise SystemExit("没有任何可用指标（检查 --scores / --no-ngram）")

    names = list(metrics)
    if args.grid_metric and args.grid_metric not in names:
        raise SystemExit(f"--grid-metric 找不到指标 {args.grid_metric}；已载入：{', '.join(names)}")
    print()
    print("=== 多指标对比台 ===")
    print(f"gold：{args.gold}（{len(items)} 条，重复句排除 {len(mapping)}）")
    print(f"pairs：{args.pairs or '未提供'}（{len(pairs)} 对）")
    print(f"指标：{', '.join(names)}（分数越高越自然）")

    print()
    print("--- [1/3] AUC（gold 正负例排序）---")
    print(_row("指标", "可用/总数", "AUC", "正例", "负例"))
    auc_by_metric: Dict[str, float] = {}
    pools_by_metric: Dict[str, Tuple[List[float], List[float]]] = {}
    for name in names:
        pos, neg, cov = gold_pools_for(items, metrics[name], skip_ids=set(mapping))
        pools_by_metric[name] = (pos, neg)
        auc = nr.auc(pos, neg) if pos and neg else 0.5
        auc_by_metric[name] = auc
        print(_row(name, f"{cov['usable']}/{cov['total']}", f"{auc:.4f}", str(len(pos)), str(len(neg))))
        if cov["missing"]:
            print(f"    [提示] {name} 缺 {cov['missing']} 条分数")
    empty_pools = [name for name, (pos, neg) in pools_by_metric.items() if not pos or not neg]
    if empty_pools:
        print(f"[错误] 指标的正例或负例分数池为空，结果无意义：{', '.join(empty_pools)}", file=sys.stderr)
        return 1

    print()
    print("--- [2/3] 成对准确率（人工修订：改后分 > 改前分）---")
    print(_row("指标", "有效对", "改后更优", "准确率", "并列/缺分"))
    if pairs:
        for name in names:
            better, worse, tie, missing = pair_result_for(pairs, metrics[name])
            valid = better + worse
            acc = better / valid if valid else 0.0
            print(_row(name, str(valid), str(better), f"{acc:.1%}", f"{tie}/{missing}"))
    else:
        print("（未提供 --pairs，跳过）")

    print()
    print(f"--- [3/3] 误标预算（目标 {args.target_fpr:.2f}）---")
    print(_row("指标", "阈值", "实测误标", "召回", "允许误标"))
    for name in names:
        pos, neg = pools_by_metric[name]
        threshold, fpr, recall = best_threshold(pos, neg, args.target_fpr)
        allowance = int(args.target_fpr * len(pos))
        print(_row(name, f"{threshold:.3f}", f"{fpr:.3f}", f"{recall:.3f}", f"{allowance}/{len(pos)}"))

    # 单指标时保留旧的完整扫描表与阈值建议，避免破坏既有用法
    grid_metric = args.grid_metric or (names[0] if len(names) == 1 else None)
    if grid_metric:
        pos, neg = pools_by_metric[grid_metric]
        print()
        print(f"--- {grid_metric} 阈值扫描（误标 = 正例被判异常；召回 = 负例被标出）---")
        print(_row("目标误标", "阈值", "实测误标", "召回"))
        for target in [float(x) for x in args.fpr_grid.split(",") if x.strip()]:
            threshold, fpr, recall = best_threshold(pos, neg, target)
            print(_row(f"{target:.2f}", f"{threshold:.3f}", f"{fpr:.3f}", f"{recall:.3f}"))
        fpr_now, recall_now = _rates(pos, neg, args.threshold)
        print()
        print(f"当前阈值 {args.threshold:.3f}：误标 {fpr_now:.3f}，召回 {recall_now:.3f}")
        rec_t, rec_fpr, rec_recall = best_threshold(pos, neg, args.target_fpr)
        print(
            f"按误标预算 {args.target_fpr:.2f} 推荐阈值 {rec_t:.3f}"
            f"（实测误标 {rec_fpr:.3f}，召回 {rec_recall:.3f}）"
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

    if args.gate is not None:
        failed = [name for name, value in auc_by_metric.items() if value < args.gate]
        if failed:
            print(f"未达 AUC 门槛 {args.gate:.3f} 的指标：{', '.join(failed)}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
