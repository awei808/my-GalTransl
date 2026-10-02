"""从 galtransl.preset.yml 生成 cordis.patch.yml，消除双份副本漂移。

背景（0.6.0 批次 4）：预设本体原先在两处手工维护——`cordis.patch.yml`（带
`- insert:` 包裹）与 `presets/galtransl.patch.yml`（同样带包裹）——审查时发现
两者的 persona 提示词已经不一致。现改为单一真相源：

    galtransl.preset.yml   顶层数组的条目清单（cordis:include 的目标格式）
            │  build（本脚本）
            ▼
    cordis.patch.yml       同一份内容套上 `- insert:`（profile patch 的目标格式）

用法：
    python tools/build_dsh_preset.py          # 生成/刷新 cordis.patch.yml
    python tools/build_dsh_preset.py --check  # 只校验是否同步（CI/审查用），不写文件
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

PRESET_DIR = Path(__file__).resolve().parent.parent / "agents" / "dsh-preset"
SOURCE_NAME = "galtransl.preset.yml"
TARGET_NAME = "cordis.patch.yml"

# 生成文件里保留的头部说明（面向「直接并入 profile patch」的用户）
TARGET_HEADER = """\
# ⚠️ 本文件由 tools/build_dsh_preset.py 从 galtransl.preset.yml 生成，请勿手改。
#    要改预设内容请改 galtransl.preset.yml，然后重跑该脚本。
#
# 用法（二选一）：
#   A. 推荐：用 cordis:include 引用 galtransl.preset.yml（见 README.md「安装」，
#      用户只需新建一个 home 层文件，不必碰自己 profile 的任何现有配置）。
#   B. 不想用 include：把下面 `- insert:` 那一整段并入
#      <profile>/cordis.patch.yml（$DSH_HOME\\profiles\\<name>\\）。
#
# ⚠️ 外部/新增行**必须放在 `insert:` 列表里**：裸写 `- id: X / name: Y` 会被当作
#    对已有条目的覆写，tree 中不存在时报 "entry not found" 且不插入。
#
# ⚠️ 下面 command / args / cwd 三处路径需按你的实际安装位置修改。
"""


def load_entries(source: Path) -> list:
    """读取条目清单；顶层必须是数组，否则抛 ValueError。"""
    with open(source, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, list):
        raise ValueError(
            f"{source.name} 顶层必须是 YAML 数组（cordis:include 的条目清单格式），"
            f"实际是 {type(data).__name__}"
        )
    return data


def render_target(entries: list) -> str:
    """把条目清单渲染成带 `- insert:` 包裹的 profile patch 文本。"""
    body = yaml.safe_dump(
        [{"insert": entries}],
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
    )
    return TARGET_HEADER + body


def main() -> int:
    parser = argparse.ArgumentParser(description="从 galtransl.preset.yml 生成 cordis.patch.yml")
    parser.add_argument("--check", action="store_true", help="只校验是否同步，不写文件")
    args = parser.parse_args()

    source = PRESET_DIR / SOURCE_NAME
    target = PRESET_DIR / TARGET_NAME
    if not source.is_file():
        print(f"找不到真相源：{source}", file=sys.stderr)
        return 2

    rendered = render_target(load_entries(source))
    current = target.read_text(encoding="utf-8") if target.is_file() else ""

    if args.check:
        if current == rendered:
            print(f"同步：{TARGET_NAME} 与 {SOURCE_NAME} 一致")
            return 0
        print(f"不同步：请重跑 `python tools/build_dsh_preset.py` 刷新 {TARGET_NAME}", file=sys.stderr)
        return 1

    # 语义等价时不动文件，避免仅注释差异造成无谓 diff
    if current == rendered:
        print(f"已是最新，无需改动：{target}")
        return 0
    # newline 固定 LF：.gitattributes 规定 *.yml eol=lf，Windows 默认 CRLF 会与之相悖
    target.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"已生成：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
