# -*- coding: utf-8 -*-
"""清空翻译缓存（transl_cache/pass3_cache）中指定字段的测试辅助脚本。

用于测试后清理缓存字段：
  - suspected_error：疑似错误标记（ForSemCheck 产出，模型整批回显误标时清空）
  - alt_dst：备选译文（ForImproveTranslation / ForBRStation / ForJPResidue /
    ForBanWordFix 产出）

与程序落盘口径一致（Cache._build_cache_obj：空字段不写入缓存），本脚本直接
删除对应键；删除后程序读取缓存时按键缺失取默认空值，行为等价于"从未标记"。

用法:
  python clear_cache_fields.py <项目目录> --field suspected_error
  python clear_cache_fields.py <项目目录> --field alt_dst --file 01_01_理想の二人.txt.json
  python clear_cache_fields.py <项目目录> --field suspected_error --dry-run
"""

import argparse
import glob
import os
from typing import List, Tuple

import orjson

FIELD_CHOICES = ("suspected_error", "alt_dst")


def collect_cache_files(project_dir: str, filename: str = "") -> List[str]:
    """收集待处理的缓存文件路径（可限定单文件）。

    Args:
        project_dir: 翻译项目目录（含 transl_cache/pass3_cache）。
        filename: 指定缓存文件名（含 .json）；为空时处理全部。

    Returns:
        排序后的缓存文件路径列表。
    """
    cache_dir = os.path.join(project_dir, "transl_cache", "pass3_cache")
    pattern = os.path.join(cache_dir, filename if filename else "*.json")
    return sorted(glob.glob(pattern))


def clear_field_in_file(file_path: str, field: str, dry_run: bool) -> Tuple[int, int]:
    """清空单文件中的指定字段。

    Args:
        file_path: 缓存文件路径。
        field: 要清空的字段名（suspected_error / alt_dst）。
        dry_run: 为 True 时只统计不写回。

    Returns:
        (总条目数, 清除条目数)。
    """
    with open(file_path, "rb") as f:
        data = orjson.loads(f.read())
    if not isinstance(data, list):
        print(f"跳过（非数组格式）: {file_path}")
        return 0, 0
    total = len(data)
    cleared = 0
    for entry in data:
        if isinstance(entry, dict) and field in entry:
            entry.pop(field)
            cleared += 1
    if cleared > 0 and not dry_run:
        with open(file_path, "wb") as f:
            f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    return total, cleared


def main() -> None:
    """解析参数并执行清空。"""
    parser = argparse.ArgumentParser(
        description="清空翻译缓存（transl_cache/pass3_cache）中的指定字段"
    )
    parser.add_argument("project_dir", help="翻译项目目录（含 transl_cache/pass3_cache）")
    parser.add_argument(
        "--field",
        required=True,
        choices=FIELD_CHOICES,
        help=f"要清空的字段：{' / '.join(FIELD_CHOICES)}",
    )
    parser.add_argument(
        "--file",
        default="",
        help="仅处理指定缓存文件名（默认处理全部）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅预览统计，不写入文件",
    )
    args = parser.parse_args()

    files = collect_cache_files(args.project_dir, args.file)
    if not files:
        print(
            f"未找到缓存文件: "
            f"{os.path.join(args.project_dir, 'transl_cache', 'pass3_cache')}"
        )
        return
    total_cleared = 0
    for file_path in files:
        total_entries, cleared = clear_field_in_file(file_path, args.field, args.dry_run)
        action = "将清除" if args.dry_run else "已清除"
        if cleared > 0:
            print(f"{action} {os.path.basename(file_path)}: {cleared}/{total_entries} 条 {args.field}")
        total_cleared += cleared
    prefix = "（dry-run 预览）" if args.dry_run else ""
    print(f"{prefix}合计 {len(files)} 个文件，{total_cleared} 条 {args.field}")


if __name__ == "__main__":
    main()
