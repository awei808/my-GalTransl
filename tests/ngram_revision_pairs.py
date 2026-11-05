#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 pass3_cache 快照还原人工修订，导出成对评测集（只读）。

用法：
  python tests/ngram_revision_pairs.py --project <项目目录> \
      --out n-gram-origin/_corpus/gold/revision_pairs.jsonl
  python tests/ngram_revision_pairs.py --project <项目目录> --adjacent --force

背景：桌面校对页保存主译文时把新文本写进 `pre_dst` 并全量覆写缓存，修订前的
副本留在 `transl_cache/.snapshots/**/<file>.<stamp>.bak`。因此 `proofread_dst`
不是修订信号，`pre_dst` 的快照 diff 才是。

默认取每个文件最早一份快照与当前缓存比对（累计净改动）；`--adjacent` 改为逐对
相邻快照比对，可还原多次编辑的中间版本，但同一条会产出多对。

按 diff 片段自动分类，把称呼/术语/语体、大幅改写（疑似错译）与纯标点类修订标为
`included=false` 并另存 sidecar，避免把外接 agent 负责的一致性与忠实度问题混进
句子级自然度评测。脚本不修改项目目录；`--out` 已存在时需 `--force` 才覆写。

输出字段：id, file, index, name, src, before, after, diff_type, category,
included, removed, added, version。
"""
from __future__ import annotations

import argparse
import csv
import difflib
import glob
import json
import os
import re
import sys
import unicodedata
from typing import Dict, List, Optional, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from GalTransl import ngram_rules as nr
from GalTransl.Dictionary import _split_dict_line
from GalTransl.server_cache import _SNAPSHOT_STAMP_RE

DEFAULT_OUT = os.path.join(ROOT, "n-gram-origin", "_corpus", "gold", "revision_pairs.jsonl")
# 称呼后缀：用于识别「创君→创先生」这类一致性修订（词典会再补充具体组合）
HONORIFIC_SUFFIXES = (
    "先生", "君", "さん", "くん", "ちゃん", "さま", "酱", "桑", "大人", "同学",
    "哥哥", "姐姐", "妹妹", "弟弟", "小姐", "少爷", "老爷", "殿", "样",
)


def _is_punctuation(ch: str) -> bool:
    return unicodedata.category(ch)[0] in ("P", "Z") or ch in "\t\r\n"


def _strip_punctuation(text: str) -> str:
    return "".join(ch for ch in text if not _is_punctuation(ch))


def load_entries(path: str) -> List[dict]:
    """读缓存/快照文件，兼容 list 与 {"items": [...]} 两种形态。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    items = data if isinstance(data, list) else (data.get("items") or [])
    return [e for e in items if isinstance(e, dict)] if isinstance(items, list) else []


def load_vocab(project_dir: str) -> Tuple[Set[str], Set[str]]:
    """从项目 GPT 字典与人名替换表收集译文候选，返回 (术语集合, 人名集合)。"""
    terms: Set[str] = set()
    names: Set[str] = set()
    for filename in ("项目GPT字典.txt", "项目GPT字典-生成.txt"):
        dict_path = os.path.join(project_dir, filename)
        if not os.path.isfile(dict_path):
            continue
        with open(dict_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith(("//", "#")):
                    continue
                parts = _split_dict_line(line)
                if len(parts) < 2:
                    continue
                for candidate in parts[1].split("/"):
                    candidate = candidate.strip()
                    if candidate:
                        terms.add(candidate)
    name_path = os.path.join(project_dir, "name替换表.csv")
    if os.path.isfile(name_path):
        with open(name_path, "r", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                dst = (row.get("DST_Name") or row.get("CN_Name") or "").strip()
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


def _is_honorific(fragment: str, names: Set[str]) -> bool:
    """片段是否由称呼成分构成（含「人名+后缀」组合）；空片段不是称呼。"""
    text = _strip_punctuation(fragment)
    if not text:
        return False
    if text in HONORIFIC_SUFFIXES:
        return True
    for name in names:
        if text.startswith(name) and text[len(name):] in HONORIFIC_SUFFIXES:
            return True
        if text.endswith(name) and text[: -len(name)] in HONORIFIC_SUFFIXES:
            return True
    return False


def classify(
    removed: str, added: str, terms: Set[str], names: Set[str], ref_len: int = 0
) -> Tuple[str, str, bool]:
    """返回 (diff_type, category, included)。

    一致性类（称呼/术语）、疑似错译与纯格式类不进入自然度评测；混合修订保守保留。
    ref_len 为原句长度，用于把「大幅改写」阈值随句长放宽（默认 8 字）。
    """
    rem = _strip_punctuation(removed)
    add = _strip_punctuation(added)
    if not rem and not add:
        return "标点/格式", "format", False
    # 两端都只剩称呼成分，或单侧为空且另一侧纯称呼（补/删称呼）
    if rem and add and _is_honorific(rem, names) and _is_honorific(add, names):
        return "称呼口径", "consistency", False
    if (not rem or not add) and _is_honorific(rem or add, names):
        return "称呼口径", "consistency", False
    if rem and add and rem in terms and add in terms:
        return "术语/语体口径", "consistency", False
    limit = max(8, int(0.4 * ref_len)) if ref_len else 8
    if max(len(rem), len(add)) >= limit:
        return "大幅改写/疑似错译", "adequacy", False
    if not add:
        return "删冗/精简", "naturalness", True
    if not rem:
        return "补写", "content", True
    return "措辞/搭配", "naturalness", True


def _index_of(entry: dict, pos: int) -> Optional[int]:
    try:
        return int(entry.get("index", pos))
    except (TypeError, ValueError):
        return None


def _diff_versions(
    old: List[dict], new: List[dict], name: str, version: str,
    terms: Set[str], names: Set[str], out: List[dict], stats: Dict[str, int],
) -> None:
    """比对两个版本的条目，把有改动的成对样本追加到 out。"""
    old_by_idx: Dict[int, dict] = {}
    for pos, entry in enumerate(old):
        idx = _index_of(entry, pos)
        if idx is None:
            stats["bad_index"] += 1
        elif idx in old_by_idx:
            stats["dup_index"] += 1
        else:
            old_by_idx[idx] = entry
    for pos, entry in enumerate(new):
        idx = _index_of(entry, pos)
        if idx is None:
            stats["bad_index"] += 1
            continue
        before_raw = old_by_idx.get(idx, {}).get("pre_dst") or ""
        after_raw = entry.get("pre_dst") or ""
        before = nr.normalize_text(before_raw)
        after = nr.normalize_text(after_raw)
        if not before or not after or before == after:
            continue
        stats["changed"] += 1
        removed, added = diff_fragments(before, after)
        diff_type, category, included = classify(
            removed, added, terms, names, max(len(before), len(after))
        )
        if diff_type == "标点/格式":
            stats["punct_only"] += 1
        out.append({
            "id": len(out) + 1,
            "file": name,
            "index": idx,
            "name": entry.get("name") or "",
            "src": entry.get("post_src") or entry.get("pre_src") or "",
            "before": before,
            "after": after,
            "diff_type": diff_type,
            "category": category,
            "included": included,
            "removed": removed,
            "added": added,
            "version": version,
        })


def _snapshot_files(cache_dir: str) -> Dict[str, List[str]]:
    """收集快照，兼容 pass3_cache 子目录与顶层旧布局（按 server_cache 口径）。"""
    snap_root = os.path.join(cache_dir, ".snapshots")
    snapshots: Dict[str, List[Tuple[str, str]]] = {}
    for sub in ("pass3_cache", ""):
        snap_dir = os.path.join(snap_root, sub) if sub else snap_root
        cur_dir = os.path.join(cache_dir, sub) if sub else cache_dir
        for path in glob.glob(os.path.join(snap_dir, "*.bak")):
            base = os.path.basename(path)
            match = re.search(r"\.(\d{8}-\d{6}-\d{6})\.bak$", base)
            if not match or not _SNAPSHOT_STAMP_RE.fullmatch(match.group(1)):
                continue
            name = base[: match.start()]
            snapshots.setdefault(os.path.join(sub, name), []).append((path, cur_dir))
    return {key: [path for path, _ in sorted(items, key=lambda x: x[0])]
            for key, items in snapshots.items()}


def collect_pairs(
    project_dir: str, terms: Set[str], names: Set[str], adjacent: bool = False
) -> Tuple[List[dict], Dict[str, int]]:
    """遍历快照目录，产出成对样本与统计。"""
    cache_dir = os.path.join(project_dir, "transl_cache")
    snapshots = _snapshot_files(cache_dir)
    if not snapshots:
        raise SystemExit(f"未找到快照目录或无有效 .bak：{os.path.join(cache_dir, '.snapshots')}")

    pairs: List[dict] = []
    stats = {"snapshot_files": len(snapshots), "compared_files": 0, "compared_entries": 0,
             "punct_only": 0, "changed": 0, "bad_index": 0, "dup_index": 0}
    for rel in sorted(snapshots):
        cur_path = os.path.join(cache_dir, rel)
        if not os.path.isfile(cur_path):
            continue
        versions = [load_entries(p) for p in snapshots[rel]] + [load_entries(cur_path)]
        if not all(versions):
            continue
        stats["compared_files"] += 1
        stats["compared_entries"] += len(versions[-1])
        spans: List[Tuple[List[dict], List[dict], str]] = []
        if adjacent:
            for i in range(len(versions) - 1):
                spans.append((versions[i], versions[i + 1], f"v{i}->v{i + 1}"))
        else:
            spans.append((versions[0], versions[-1], "earliest->current"))
        for old, new, version in spans:
            _diff_versions(old, new, rel, version, terms, names, pairs, stats)
    return pairs, stats


def _check_outputs(path: str, force: bool) -> None:
    outputs = [path, path + ".excluded.jsonl", path + ".summary.json"]
    existing = [p for p in outputs if os.path.exists(p)]
    if existing and not force:
        raise SystemExit("输出文件已存在，重跑会覆盖已有评测集；确认后加 --force：\n  " + "\n  ".join(existing))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="翻译项目根目录（含 transl_cache/.snapshots）")
    parser.add_argument("--out", default=DEFAULT_OUT, help="输出 JSONL 路径")
    parser.add_argument("--no-dict", action="store_true", help="不读项目字典（跳过术语过滤）")
    parser.add_argument("--adjacent", action="store_true", help="逐对相邻快照比对，还原多次编辑的中间版本")
    parser.add_argument("--force", action="store_true", help="允许覆写已存在的输出文件")
    args = parser.parse_args(argv)

    _check_outputs(args.out, args.force)
    terms, names = (set(), set()) if args.no_dict else load_vocab(args.project)
    pairs, stats = collect_pairs(args.project, terms, names, args.adjacent)
    if not pairs:
        print("未还原出任何修订对（快照与当前缓存一致），请确认项目目录", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    included = [p for p in pairs if p["included"]]
    excluded = [p for p in pairs if not p["included"]]
    for path, rows in ((args.out, included), (args.out + ".excluded.jsonl", excluded)):
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            for item in rows:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    summary = {
        "project": os.path.basename(os.path.normpath(args.project)),
        "mode": "adjacent" if args.adjacent else "earliest->current",
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
    print(f"模式 {summary['mode']}；快照文件 {stats['snapshot_files']}，"
          f"比对 {stats['compared_files']} 个文件 / {stats['compared_entries']} 条")
    print(f"归一化后有改动 {stats['changed']} 条；index 异常 {stats['bad_index']} / 重复 {stats['dup_index']}")
    print(f"纳入自然度评测 {len(included)} 对 → {args.out}")
    print(f"排除 {len(excluded)} 对 → {args.out}.excluded.jsonl")
    print("分类:", summary["diff_types"])
    print(f"字典术语 {len(terms)} 条 / 人名 {len(names)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
