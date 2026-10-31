#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""中文通顺检测 gold set 回归 CLI（不属于 pytest 自动收集）。

用法：
  python tests/fluency_gold_eval.py --mode prefilter
  python tests/fluency_gold_eval.py --mode llm --dry-run
  python tests/fluency_gold_eval.py --mode llm --endpoint http://host/v1 \
      --model model-name --api-key-env MINIMAX_API_KEY

说明：
- prefilter 模式只跑规则初筛，输出 positive 召回与 ok 送检率，不联网；
- llm 模式按文件分组构造与生产一致的提示词（src/dst/name + 文件级元数据），
  复用 ForFluencyCheck._parse_fix_response 的证据校验（含 +2 字上限）；
  --dry-run 只打印提示词，不联网；真实调用会产生 API 费用。
- 24 句金标：issue 10（确认应改）、partial 4（确有局部问题但模型建议不可直接
  采用）、ok 10（误报）。binary 以 issue+partial 为正例，strict 仅以 issue 为正例。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from GalTransl.Backend.ForFluencyCheck import ForFluencyCheck
from GalTransl.Backend.Prompts import (
    FORFLUENCY_SYSTEM,
    FORGAL_JSON_FLUENCYCHECK_PROMPT,
)
from GalTransl.Backend.utils import (
    detect_batch_line_break_symbol,
    encode_line_breaks_for_prompt,
)
from GalTransl.CSentense import CSentense
from GalTransl.fluency_rules import match_fluency_category

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "fluency_gold_set.json"
POSITIVE_LABELS = ("issue", "partial")


class _GoldCfg:
    """_parse_fix_response 所需的最小配置替身（不写运行态、不读项目配置）。"""

    active_workers = 1
    runtime_project_dir = ""

    def getProjectDir(self) -> str:
        return ""

    def getKey(self, key: str, default=None):
        return default

    def getProblemAnalyzeConfig(self, key: str) -> list:
        return []

    def hasProblemAnalyzeConfig(self, key: str) -> bool:
        return True

    def getProblemAnalyzeArinashiDict(self) -> dict:
        return {}


def load_items() -> list[dict]:
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)["items"]


def _group_by_file(items: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item["file"], []).append(item)
    return list(groups.items())


def _build_input_rows(items: list[dict]) -> tuple[str, str, list[CSentense]]:
    """复刻 ForGalJsonTranslate._build_input_jsonlines（proofread=True, include_src=True）。"""
    n_symbol = detect_batch_line_break_symbol([item["src"] for item in items])
    rows: list[str] = []
    trans_list: list[CSentense] = []
    for item in items:
        speaker = (item.get("name") or "null").replace("\r\n", "").replace(
            "\t", ""
        ).replace("\n", "")
        src = encode_line_breaks_for_prompt(item["src"], n_symbol)
        dst = encode_line_breaks_for_prompt(item["dst"], n_symbol)
        obj = {"id": item["id"], "name": speaker, "src": src, "dst": dst}
        if obj["name"] == "null":
            del obj["name"]
        sig = f"g{item['id']:02d}"
        rows.append(f"{sig}|" + json.dumps(obj, ensure_ascii=False))

        tran = CSentense(item["src"], item.get("name") or "", index=item["id"])
        tran.post_src = item["src"]
        tran.pre_dst = item["dst"]
        tran.proofread_zh = ""
        tran.fluency_issue = ""
        trans_list.append(tran)
    return "\n".join(rows), n_symbol, trans_list


def build_prompt(items: list[dict]) -> tuple[str, str, list[CSentense]]:
    input_src, n_symbol, trans_list = _build_input_rows(items)
    prompt = (
        FORGAL_JSON_FLUENCYCHECK_PROMPT.replace("[TargetLang]", "Simplified_Chinese")
        .replace("[plot_metadata]", items[0]["plot_metadata"])
        .replace("[Input]", input_src)
    )
    return prompt, n_symbol, trans_list


def _make_parser() -> ForFluencyCheck:
    parser = object.__new__(ForFluencyCheck)
    parser._log_tag = "[gold-eval]"
    parser._disabled_reason = ""
    parser.pj_config = _GoldCfg()
    return parser


def prefilter_report(items: list[dict]) -> int:
    positives = [item for item in items if item["label"] in POSITIVE_LABELS]
    # 初筛召回只考核"有对应规则"的 positive；已知缺口单独列出，不计入分母
    reachable = [
        item for item in positives if item.get("prefilter_expected", True)
    ]
    known_gap = [item for item in positives if not item.get("prefilter_expected", True)]
    negatives = [item for item in items if item["label"] == "ok"]
    pos_hit = [item for item in reachable if match_fluency_category(item["dst"])]
    neg_hit = [item for item in negatives if match_fluency_category(item["dst"])]
    print("=== Gold set 初筛回归（离线） ===")
    print(
        f"items={len(items)} positives={len(positives)} "
        f"（初筛可达 {len(reachable)}） negatives={len(negatives)}"
    )
    print(
        f"positive 召回: {len(pos_hit)}/{len(reachable)}"
        + ("（全部召回）" if len(pos_hit) == len(reachable) else "（存在漏检）")
    )
    if known_gap:
        print("已知初筛缺口（不计入召回，需补规则或由其他路径覆盖）:")
        for item in known_gap:
            print(f"  #{item['id']:<2} {item['file']}#{item['index']} {item['note']}")
    if negatives:
        print(
            f"ok 送检: {len(neg_hit)}/{len(negatives)}"
            f"（送检率 {len(neg_hit) / len(negatives):.0%}；其余由初筛直接排除）"
        )
    else:
        print("ok 送检: 0/0（当前子集无 ok 样本）")
    if neg_hit:
        print("ok 仍送检（交给 LLM 否决）:")
        for item in neg_hit:
            print(
                f"  #{item['id']:<2} {item['file']}#{item['index']} "
                f"[{match_fluency_category(item['dst'])}] {item['note']}"
            )
    missed = [item for item in reachable if item not in pos_hit]
    if missed:
        print("positive 漏检（需要补正则）:")
        for item in missed:
            print(f"  #{item['id']:<2} {item['file']}#{item['index']} {item['note']}")
        return 1
    return 0


def _print_metrics(items: list[dict], results: dict[int, tuple[bool, str]]) -> None:
    def _score(positive_labels: tuple[str, ...], title: str) -> None:
        tp = fp = fn = tn = 0
        for item in items:
            pred = results.get(item["id"], (False, ""))[0]
            gold = item["label"] in positive_labels
            if pred and gold:
                tp += 1
            elif pred and not gold:
                fp += 1
            elif not pred and gold:
                fn += 1
            else:
                tn += 1
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        print(
            f"{title}: TP={tp} FP={fp} FN={fn} TN={tn} | "
            f"precision={precision:.1%} recall={recall:.1%} f1={f1:.1%}"
        )

    _score(POSITIVE_LABELS, "binary（issue+partial 为正例）")
    _score(("issue",), "strict（仅 issue 为正例）")
    print("--- 逐条结果 ---")
    for item in items:
        pred, marker = results.get(item["id"], (False, ""))
        flag = "预测:问题" if pred else "预测:通过"
        print(
            f"#{item['id']:<2} {item['label']:<7} {flag} "
            f"{item['file']}#{item['index']}"
        )
        if marker:
            print(f"    {marker}")
        if pred != (item["label"] in POSITIVE_LABELS):
            print(f"    金标说明: {item['note']}")


def run_llm(items: list[dict], args: argparse.Namespace) -> int:
    parser = _make_parser()
    results: dict[int, tuple[bool, str]] = {}
    quality_total: dict[str, int] = {}
    groups = _group_by_file(items)
    client = None
    if not args.dry_run:
        from openai import OpenAI

        api_key = os.environ.get(args.api_key_env, "")
        if not api_key:
            print(f"缺少环境变量 {args.api_key_env}（API key）", file=sys.stderr)
            return 2
        client = OpenAI(base_url=args.endpoint, api_key=api_key)

    for file, group in groups:
        prompt, n_symbol, trans_list = build_prompt(group)
        if args.dry_run:
            print("=" * 80)
            print(f"FILE {file}  items={[item['id'] for item in group]}")
            print(prompt)
            continue
        response = client.chat.completions.create(
            model=args.model,
            messages=[
                {"role": "system", "content": FORFLUENCY_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content or ""
        quality: dict[str, int] = {}
        parser._parse_fix_response(content, trans_list, n_symbol, quality)
        for key, value in quality.items():
            quality_total[key] = quality_total.get(key, 0) + value
        for tran in trans_list:
            results[tran.index] = (bool(tran.fluency_issue), tran.fluency_issue)
        print(f"FILE {file}: {len(trans_list)} 句，命中 {sum(1 for t in trans_list if t.fluency_issue)} 句")

    if args.dry_run:
        print(f"\n[dry-run] 共 {len(groups)} 个文件分组，未联网。")
        return 0

    print("\n=== Gold set LLM 回归 ===")
    _print_metrics(items, results)
    if quality_total:
        print(f"解析质量计数: {quality_total}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("prefilter", "llm"), default="prefilter")
    parser.add_argument("--dry-run", action="store_true", help="llm 模式只打印提示词，不联网")
    parser.add_argument("--endpoint", default="", help="OpenAI 兼容接口 base_url")
    parser.add_argument("--model", default="", help="模型名")
    parser.add_argument(
        "--api-key-env", default="GALTRANSL_EVAL_API_KEY", help="存放 API key 的环境变量名"
    )
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 句（调试用）")
    args = parser.parse_args()

    items = load_items()
    if args.limit > 0:
        items = items[: args.limit]

    if args.mode == "prefilter":
        return prefilter_report(items)
    if args.dry_run:
        return run_llm(items, args)
    if not args.endpoint or not args.model:
        print("llm 模式需提供 --endpoint 与 --model（或使用 --dry-run）", file=sys.stderr)
        return 2
    return run_llm(items, args)


if __name__ == "__main__":
    raise SystemExit(main())
