#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""n-gram 语域检测 gold set 采样 CLI（不属于 pytest 自动收集）。

用法：
  python tests/ngram_gold_sample.py --project "D:/.../小粥3-全量" --n 400 --dupes 30

口径与打分器一致（`ngram_rules.find_ngram_flagged`）：`proofread_dst` 优先、回退
`pre_dst`；汉字数少于 `ngram_rules.MIN_SENT_CHARS` 的句子本就不判，采样时剔除；
同一译文（NFC + 去空白后相同）只抽一次。

输出是**盲标文件**：每行只含 `id`/`dst`/`label`/`subtype`/`note`，不含分数、来源
文件与重复关系——重复句用于标注一致性自检，映射另存 `<out>.dupes.json`，标完后
用 `python tests/ngram_gold_eval.py --gold <out> --dupes <out>.dupes.json` 算一致率。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
from typing import Dict, Iterator, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from GalTransl import ngram_rules as nr

DEFAULT_OUT = os.path.join(ROOT, "n-gram-origin", "_corpus", "gold", "ngram_gold_pending.jsonl")


def resolve_cache_dir(path: str) -> str:
    """接受项目根目录或 pass3_cache 目录，返回实际缓存目录。"""
    candidates = [path, os.path.join(path, "transl_cache", "pass3_cache")]
    for cand in candidates:
        if os.path.isdir(cand) and glob.glob(os.path.join(cand, "*.json")):
            return cand
    raise SystemExit(f"未找到 pass3_cache：{path}")


def iter_entries(cache_dir: str) -> Iterator[Tuple[str, int, str]]:
    """逐条产出 (文件名, index, 待判译文)；兼容 list 与 {"items": [...]} 两种缓存形态。"""
    for path in sorted(glob.glob(os.path.join(cache_dir, "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            print(f"跳过无法解析的缓存 {os.path.basename(path)}：{e}", file=sys.stderr)
            continue
        items = data if isinstance(data, list) else (data.get("items") or [])
        for pos, entry in enumerate(items):
            if not isinstance(entry, dict):
                continue
            dst = entry.get("proofread_dst") or entry.get("proofread_zh") or ""
            if not dst:
                dst = entry.get("pre_dst") or entry.get("pre_zh") or ""
            if not dst:
                continue
            index = entry.get("index", pos)
            yield os.path.basename(path), int(index) if str(index).isdigit() else pos, dst


def collect_candidates(cache_dir: str, min_chars: int) -> Tuple[List[str], Dict[str, int]]:
    """收集去重后的候选句，返回 (文本列表, 统计)。"""
    seen = set()
    texts: List[str] = []
    stats = {"total": 0, "empty": 0, "short": 0, "dup": 0}
    for _name, _index, dst in iter_entries(cache_dir):
        stats["total"] += 1
        key = nr.normalize_text(dst)
        if not key:
            stats["empty"] += 1
            continue
        if nr.han_char_count(key) < min_chars:
            stats["short"] += 1
            continue
        if key in seen:
            stats["dup"] += 1
            continue
        seen.add(key)
        texts.append(dst.strip())
    return texts, stats


def build_sample(texts: List[str], n: int, dupes: int, seed: int) -> Tuple[List[dict], Dict[int, int]]:
    """抽 n 条独立句并插入 dupes 条重复句，返回 (打乱后的条目, 重复映射)。

    重复映射为 {重复条目 id: 原始条目 id}，仅写入旁路文件，不进标注文件。
    """
    rng = random.Random(seed)
    picked = texts if len(texts) <= n else rng.sample(texts, n)
    picked = list(picked)
    dupes = min(dupes, len(picked))
    dup_sources = rng.sample(range(len(picked)), dupes) if dupes > 0 else []
    rows = [{"text": t, "origin": i, "dup_of": None} for i, t in enumerate(picked)]
    for src in dup_sources:
        rows.append({"text": picked[src], "origin": None, "dup_of": src})
    rng.shuffle(rows)
    # id 按打乱后的位置分配；重复句的映射要等原始句 id 确定后再回填
    items: List[dict] = []
    origin_to_id: Dict[int, int] = {}
    for new_id, row in enumerate(rows, start=1):
        items.append({"id": new_id, "dst": row["text"], "label": "", "subtype": "", "note": ""})
        if row["origin"] is not None:
            origin_to_id[row["origin"]] = new_id
    mapping: Dict[int, int] = {}
    for new_id, row in enumerate(rows, start=1):
        if row["dup_of"] is not None:
            mapping[new_id] = origin_to_id[row["dup_of"]]
    return items, mapping


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="翻译项目根目录或 pass3_cache 目录")
    parser.add_argument("--n", type=int, default=400, help="独立句数量（默认 400）")
    parser.add_argument("--dupes", type=int, default=30, help="额外插入的重复句数量（一致性自检，默认 30）")
    parser.add_argument("--seed", type=int, default=20261102, help="随机种子（可复现）")
    parser.add_argument("--min-chars", type=int, default=nr.MIN_SENT_CHARS, help="最少汉字数（默认同打分器）")
    parser.add_argument("--out", default=DEFAULT_OUT, help="输出 JSONL 路径")
    args = parser.parse_args(argv)

    cache_dir = resolve_cache_dir(args.project)
    texts, stats = collect_candidates(cache_dir, args.min_chars)
    if not texts:
        print("候选用尽（全部为空/过短/重复），请换项目或降低 --min-chars", file=sys.stderr)
        return 1
    if len(texts) < args.n:
        print(f"候选仅 {len(texts)} 句，少于请求的 {args.n}，按全部抽样", file=sys.stderr)

    items, mapping = build_sample(texts, args.n, args.dupes, args.seed)
    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    dup_path = args.out + ".dupes.json"
    with open(dup_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({str(k): v for k, v in mapping.items()}, fh, ensure_ascii=False, indent=1)

    print("=== gold set 采样 ===")
    print(f"缓存目录：{cache_dir}")
    print(
        f"条目 {stats['total']}：空译文 {stats['empty']}，汉字<{args.min_chars} {stats['short']}，"
        f"重复 {stats['dup']} → 候选 {len(texts)}"
    )
    print(f"抽样：独立句 {len(items) - len(mapping)} + 重复句 {len(mapping)} = {len(items)} 行")
    print(f"输出：{args.out}")
    print(f"重复映射（勿给标注人看）：{dup_path}")
    print()
    print("标注规范：label 填 natural / awkward；awkward 时 subtype 填")
    print("  直译语序 | 名词化堆砌 | 搭配冲突 | 虚词冗余（体裁豁免记 stylistic）")
    print("只判「像不像人写的自然中文」，不判错译漏译；古风/敬语/角色口癖/H 场景拟声断续算 natural。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
