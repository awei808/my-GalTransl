#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 pass3_cache 快照还原人工修订，导出成对评测集（只读）。

用法：
  python tests/ngram_revision_pairs.py --project <项目目录> \
      --out n-gram-origin/_corpus/gold/revision_pairs.jsonl

背景：桌面校对页保存主译文时把新文本写进 `pre_dst` 并全量覆写缓存，修订前的
副本留在 `transl_cache/.snapshots/pass3_cache/<file>.<stamp>.bak`。因此
`proofread_dst` 不是修订信号，`pre_dst` 的快照 diff 才是。

对每个缓存文件取最早一份快照与当前按 index 对齐，`normalize_text` 后比较
`pre_dst`；差异即一对「改前→改后」。随后按 diff 片段自动分类，把称呼/术语/
纯标点类修订标为 `included=false` 并另存 sidecar，避免把外接 agent 负责的
一致性问题混进句子级自然度评测。

输出字段：id, file, index, name, src, before, after, diff_type, category,
included, removed, added。脚本不修改项目目录，只写 `--out` 指定位置。
"""
from __future__ import annotations

import argparse
import csv
import difflib
import glob
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from GalTransl import ngram_rules as nr

DEFAULT_OUT = os.path.join(ROOT, "n-gram-origin", "_corpus", "gold", "revision_pairs.jsonl")
# 称呼后缀：用于识别「创君→创先生」这类一致性修订（词典会再补充具体组合）
HONORIFIC_SUFFIXES = (
    "先生", "君", "さん", "くん", "ちゃん", "さま", "酱", "桑", "大人", "同学",
    "哥哥", "姐姐", "妹妹", "弟弟", "小姐", "少爷", "老爷", "殿", "样",
)
PUNCTUATION = set("，。！？…、；：,.!?~～「」『』（）()《》——-…· \t\r\n\u3000")


def load_entries(path: str) -> List[dict]:
    """读缓存/快照文件，兼容 list 与 {"items": [...]} 两种形态。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    items = data if isinstance(data, list) else (data.get("items") or [])
    return [e for e in items if isinstance(e, dict)] if isinstance(items, list) else []


def load_vocab(project_dir: str) -> Tuple[set, set]:
    """从项目 GPT 字典与人名替换表收集译文候选，返回 (术语集合, 人名集合)。"""
    terms: set = set()
    names: set = set()
    dict_path = os.path.join(project_dir, "项目GPT字典.txt")
    if os.path.isfile(dict_path):
        with open(dict_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("//") or "|" not in line:
                    continue
                for candidate in line.split("|")[1].split("/"):
                    candidate = candidate.strip()
                    if candidate:
                        terms.add(candidate)
    name_path = os.path.join(project_dir, "name替换表.csv")
    if os.path.isfile(name_path):
        with open(name_path, "r", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                dst = (row.get("DST_Name") or "").strip()
                if dst:
                    names.add(dst)
                    terms.add(dst)
    return terms, names


def diff_fragments(before: str, after: str) -> Tuple[str, str]:
    """返回 (被删片段, 被增片段)，按字符级对齐合并所有 replace/delete/insert。"""
    removed: List[str] = []
    added: List[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, before, after, autojunk=False
    ).get_opcodes():
        if tag in ("replace", "delete"):
            removed.append(before[i1:i2])
        if tag in ("replace", "insert"):
            added.append(after[j1:j2])
    return "".join(removed), "".join(added)


def _is_honorific(fragment: str, names: set) -> bool:
    """片段是否只由称呼成分构成（含「人名+后缀」组合）。"""
    text = "".join(ch for ch in fragment if ch not in PUNCTUATION)
    if not text:
        return True
    if text in HONORIFIC_SUFFIXES:
        return True
    for name in names:
        if text.startswith(name) and text[len(name):] in HONORIFIC_SUFFIXES:
            return True
        if text.endswith(name) and text[: -len(name)] in HONORIFIC_SUFFIXES:
            return True
    return False


def classify(
    removed: str, added: str, terms: set, names: set
) -> Tuple[str, str, bool]:
    """返回 (diff_type, category, included)。

    一致性类（称呼/术语）与纯格式类不进入自然度评测；混合修订保守保留。
    """
    rem = "".join(ch for ch in removed if ch not in PUNCTUATION)
    add = "".join(ch for ch in added if ch not in PUNCTUATION)
    if not rem and not add:
        return "标点/格式", "format", False
    # 两端都只剩称呼成分时才排除，避免把「改称呼顺带改措辞」误删
    if (rem or add) and _is_honorific(rem, names) and _is_honorific(add, names):
        return "称呼口径", "consistency", False
    if rem and add and rem in terms and add in terms:
        return "术语/语体口径", "consistency", False
    # 大幅改写多为错译/漏译修复（忠实度），不属于句子级自然度
    if max(len(rem), len(add)) >= 8:
        return "大幅改写/疑似错译", "adequacy", False
    if not add:
        return "删冗/精简", "naturalness", True
    if not rem:
        return "补写", "content", True
    return "措辞/搭配", "naturalness", True


def collect_pairs(project_dir: str, terms: set, names: set) -> Tuple[List[dict], Dict[str, int]]:
    """遍历快照目录，产出成对样本与统计。"""
    cache_dir = os.path.join(project_dir, "transl_cache")
    cur_dir = os.path.join(cache_dir, "pass3_cache")
    snap_dir = os.path.join(cache_dir, ".snapshots", "pass3_cache")
    if not os.path.isdir(snap_dir):
        raise SystemExit(f"未找到快照目录：{snap_dir}（项目里没有人工修订痕迹）")

    snapshots: Dict[str, List[str]] = {}
    for path in glob.glob(os.path.join(snap_dir, "*.bak")):
        name = os.path.basename(path).split(".json.")[0] + ".json"
        snapshots.setdefault(name, []).append(path)

    pairs: List[dict] = []
    stats = {"snapshot_files": len(snapshots), "compared_files": 0, "compared_entries": 0,
             "punct_only": 0, "changed": 0}
    for name in sorted(snapshots):
        cur_path = os.path.join(cur_dir, name)
        if not os.path.isfile(cur_path):
            continue
        old = load_entries(sorted(snapshots[name])[0])
        new = load_entries(cur_path)
        if not old or not new:
            continue
        old_by_idx = {int(e.get("index", i)): e for i, e in enumerate(old)}
        stats["compared_files"] += 1
        for pos, entry in enumerate(new):
            stats["compared_entries"] += 1
            idx = int(entry.get("index", pos))
            before_raw = old_by_idx.get(idx, {}).get("pre_dst") or ""
            after_raw = entry.get("pre_dst") or ""
            before = nr.normalize_text(before_raw)
            after = nr.normalize_text(after_raw)
            if not before or not after or before == after:
                continue
            stats["changed"] += 1
            removed, added = diff_fragments(before, after)
            diff_type, category, included = classify(removed, added, terms, names)
            if diff_type == "标点/格式":
                stats["punct_only"] += 1
            src = entry.get("post_src") or entry.get("pre_src") or ""
            pairs.append({
                "id": len(pairs) + 1,
                "file": name,
                "index": idx,
                "name": entry.get("name") or "",
                "src": src,
                "before": before,
                "after": after,
                "diff_type": diff_type,
                "category": category,
                "included": included,
                "removed": removed,
                "added": added,
            })
    return pairs, stats


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="翻译项目根目录（含 transl_cache/.snapshots）")
    parser.add_argument("--out", default=DEFAULT_OUT, help="输出 JSONL 路径")
    parser.add_argument("--no-dict", action="store_true", help="不读项目字典（跳过术语过滤）")
    args = parser.parse_args(argv)

    terms, names = (set(), set()) if args.no_dict else load_vocab(args.project)
    pairs, stats = collect_pairs(args.project, terms, names)
    if not pairs:
        print("未还原出任何修订对（快照与当前缓存一致），请确认项目目录", file=sys.stderr)
        return 1

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    included = [p for p in pairs if p["included"]]
    excluded = [p for p in pairs if not p["included"]]
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for item in included:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    excluded_path = args.out + ".excluded.jsonl"
    with open(excluded_path, "w", encoding="utf-8", newline="\n") as fh:
        for item in excluded:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    summary = {
        "project": os.path.basename(os.path.normpath(args.project)),
        "stats": stats,
        "pairs_total": len(pairs),
        "pairs_included": len(included),
        "pairs_excluded": len(excluded),
        "diff_types": {k: sum(1 for p in pairs if p["diff_type"] == k)
                       for k in sorted({p["diff_type"] for p in pairs})},
        "vocab": {"terms": len(terms), "names": len(names)},
    }
    with open(args.out + ".summary.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)

    print("=== 人工修订成对集 ===")
    print(f"快照文件 {stats['snapshot_files']}，比对 {stats['compared_files']} 个文件 / "
          f"{stats['compared_entries']} 条，归一化后有改动 {stats['changed']} 条")
    print(f"纳入自然度评测 {len(included)} 对 → {args.out}")
    print(f"排除 {len(excluded)} 对 → {excluded_path}")
    print("分类:", summary["diff_types"])
    print(f"字典术语 {len(terms)} 条 / 人名 {len(names)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
