"""
统一数据校验模块（DataValidator）

集中管理所有数据校验逻辑，供流水线各阶段复用。
每个校验函数返回统一的校验结果字典：
    {
        "valid": bool,
        "errors": list[str],
        "warnings": list[str],
        "stats": dict,
    }

校验分层：
  Layer 0: 输入层 — 文件格式、字段完整性、编码正确性
  Layer 1: 压缩层 — 信息保留率、角色名/专名完整性
  Layer 2: LLM 响应层 — JSON 可解析、字段类型、非空检查、无乱码
  Layer 3: 元数据层 — FileMetaData/BatchMetadata 条目数交叉验证
  Layer 4: 输出层 — 译文完整性、格式一致性
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

# ── 通用工具 ──

def _make_result(
    valid: bool = True,
    errors: Optional[List[str]] = None,
    warnings: Optional[List[str]] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> dict:
    """构造统一的校验结果字典。"""
    return {
        "valid": valid,
        "errors": errors or [],
        "warnings": warnings or [],
        "stats": stats or {},
    }


# ── Layer 0：输入层校验 ──

def validate_input_json(
    json_list: Any,
    source: str = "",
) -> dict:
    """
    校验输入 JSON 格式。

    检查项：
      - 必须是 list
      - 每项必须是 dict
      - 每项必须有 'message' 字段
      - 'message' 必须是非空字符串
      - 'name' 可选，如果存在必须是字符串或字符串列表
      - 'index' 可选，如果存在必须是整数或可解析为整数的字符串

    Args:
        json_list: 待校验的 JSON 数据
        source: 数据来源标识（文件名或描述），用于错误信息

    Returns:
        校验结果字典
    """
    errors: List[str] = []
    warnings: List[str] = []
    stats: Dict[str, Any] = {
        "total_items": 0,
        "items_with_name": 0,
        "items_without_name": 0,
        "items_with_empty_message": 0,
        "items_missing_message": 0,
    }

    src_label = f" {source}" if source else ""

    # 类型检查：必须是 list
    if not isinstance(json_list, list):
        errors.append(f"输入数据{src_label}不是列表格式，实际类型：{type(json_list).__name__}")
        return _make_result(valid=False, errors=errors, warnings=warnings, stats=stats)

    stats["total_items"] = len(json_list)

    if len(json_list) == 0:
        warnings.append(f"输入数据{src_label}为空列表")

    for i, item in enumerate(json_list):
        pos = i + 1

        # 每项必须是 dict
        if not isinstance(item, dict):
            errors.append(f"第{pos}项不是字典格式，实际类型：{type(item).__name__}")
            continue

        # 必须有 'message' 字段
        if "message" not in item:
            errors.append(f"第{pos}项缺少 'message' 字段")
            stats["items_missing_message"] += 1
            continue

        # 'message' 必须是非空字符串（但允许空字符串，仅做警告）
        msg = item.get("message")
        if not isinstance(msg, str):
            errors.append(
                f"第{pos}项 'message' 字段类型错误，期望 str，"
                f"实际：{type(msg).__name__}"
            )
            continue
        if msg.strip() == "":
            stats["items_with_empty_message"] += 1
            # 空消息不阻止流程，仅后续可能警告

        # 'name' 可选校验
        name = item.get("name")
        if name is not None and name != "":
            if isinstance(name, (str, list)):
                stats["items_with_name"] += 1
            else:
                errors.append(
                    f"第{pos}项 'name' 字段类型错误，期望 str 或 list，"
                    f"实际：{type(name).__name__}"
                )
        else:
            stats["items_without_name"] += 1

        # 'index' 可选校验
        idx = item.get("index")
        if idx is not None:
            if isinstance(idx, int):
                pass  # 合法的整数 index
            elif isinstance(idx, str) and idx.isdigit():
                pass  # 可解析为整数的字符串 index
            elif isinstance(idx, (int, float)) and not isinstance(idx, bool):
                # 浮点数但可无损转整数
                if idx == int(idx):
                    pass
                else:
                    errors.append(
                        f"第{pos}项 'index' 字段不是有效整数：{idx}"
                    )
            else:
                errors.append(
                    f"第{pos}项 'index' 字段类型错误，期望 int，"
                    f"实际：{type(idx).__name__}，值：{idx}"
                )

    # 空消息比例警告
    if stats["total_items"] > 0:
        empty_ratio = stats["items_with_empty_message"] / stats["total_items"]
        if empty_ratio > 0.5 and stats["items_with_empty_message"] < stats["total_items"]:
            warnings.append(
                f"超过一半的条目 message 为空{src_label}（{stats['items_with_empty_message']}/{stats['total_items']}）"
            )
        elif stats["items_with_empty_message"] == stats["total_items"]:
            errors.append(f"所有条目的 message 均为空{src_label}")

    valid = len(errors) == 0
    return _make_result(valid=valid, errors=errors, warnings=warnings, stats=stats)


# ── Layer 1：压缩层校验 ──

def validate_compression_integrity(
    original: Dict[str, list],
    compressed: str,
    all_names: Optional[set] = None,
) -> dict:
    """
    校验压缩是否保留了关键信息。

    检查项：
      - 角色名是否全部保留（如果提供了 all_names）
      - 压缩后文本非空
      - 压缩后文本长度合理

    Args:
        original: 原始文件数据 {file_path: json_list}
        compressed: 压缩后的文本
        all_names: 所有角色名集合（可选）

    Returns:
        校验结果字典
    """
    errors: List[str] = []
    warnings: List[str] = []
    stats: Dict[str, Any] = {
        "original_files": len(original),
        "original_lines": sum(len(v) for v in original.values()),
        "compressed_chars": len(compressed) if compressed else 0,
    }

    if not compressed or not isinstance(compressed, str) or compressed.strip() == "":
        errors.append("压缩后文本为空")
        return _make_result(valid=False, errors=errors, warnings=warnings, stats=stats)

    # 角色名保留率检查
    if all_names:
        lost_names = []
        for name in all_names:
            if name and name.strip() and name.strip() not in compressed:
                lost_names.append(name)
        stats["total_names"] = len(all_names)
        stats["lost_names"] = len(lost_names)
        stats["name_retention"] = (
            1.0 - len(lost_names) / max(len(all_names), 1)
        )
        if lost_names:
            warnings.append(
                f"压缩后丢失角色名 ({len(lost_names)}/{len(all_names)})：{', '.join(lost_names[:10])}"
                + ("..." if len(lost_names) > 10 else "")
            )

    return _make_result(valid=len(errors) == 0, errors=errors, warnings=warnings, stats=stats)


# ── Layer 2：LLM 响应层校验 ──

def validate_llm_response(
    response_text: Any,
    expected_format: str = "json",
) -> dict:
    """
    校验 LLM 返回的原始响应。

    检查项：
      - 非空字符串
      - 能解析为指定格式（json / jsonline / tsv）
      - 不乱码（无 � 替换字符 U+FFFD）
      - 包含有效内容

    Args:
        response_text: LLM 返回的原始文本
        expected_format: 期望的格式类型 ("json", "jsonline", "tsv")

    Returns:
        校验结果字典，包含解析后的数据（parsed_data 字段）
    """
    errors: List[str] = []
    warnings: List[str] = []
    stats: Dict[str, Any] = {"response_length": 0}

    # 类型检查
    if response_text is None:
        errors.append("LLM 返回为 None")
        return _make_result(valid=False, errors=errors, warnings=warnings, stats=stats)

    if not isinstance(response_text, str):
        errors.append(f"LLM 返回类型错误，期望 str，实际：{type(response_text).__name__}")
        return _make_result(valid=False, errors=errors, warnings=warnings, stats=stats)

    stats["response_length"] = len(response_text)

    # 空响应
    if response_text.strip() == "":
        errors.append("LLM 返回为空字符串")
        return _make_result(valid=False, errors=errors, warnings=warnings, stats=stats)

    # 乱码检测
    if "�" in response_text:
        errors.append("LLM 返回包含乱码字符（U+FFFD 替换字符）")

    # 格式特定校验
    parsed_data = None
    if expected_format == "json":
        parsed_data = _try_parse_json(response_text, errors, warnings)
    elif expected_format == "jsonline":
        parsed_data = _try_parse_jsonline(response_text, errors, warnings)
    elif expected_format == "tsv":
        parsed_data = _try_parse_tsv(response_text, errors, warnings)
    else:
        warnings.append(f"未知的期望格式：{expected_format}，跳过格式校验")

    result = _make_result(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        stats=stats,
    )
    result["parsed_data"] = parsed_data
    return result


def _try_parse_json(
    text: str, errors: List[str], warnings: List[str]
) -> Optional[dict]:
    """尝试从 LLM 响应中解析 JSON。"""
    # 处理 </think> 标签（推理模型）
    if "</think>" in text:
        text = text.split("</think>")[-1]

    # 提取代码块
    code_block_pattern = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
    matches = code_block_pattern.findall(text)
    if matches:
        text = matches[0]
        warnings.append("从代码块中提取 JSON 内容")

    # 尝试找到 JSON 对象边界
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        errors.append(f"响应中未找到 JSON 对象，前 200 字符：{text[:200]}")
        return None

    json_candidate = text[start : end + 1]

    try:
        parsed = json.loads(json_candidate)
    except json.JSONDecodeError as e:
        errors.append(f"JSON 解析失败：{e}，候选内容前 200 字符：{json_candidate[:200]}")
        return None

    if not isinstance(parsed, dict):
        errors.append(f"解析后的 JSON 不是对象（dict），实际类型：{type(parsed).__name__}")
        return None

    return parsed


def _try_parse_jsonline(
    text: str, errors: List[str], warnings: List[str]
) -> Optional[List[dict]]:
    """尝试解析 jsonline 格式（每行一个 JSON 对象，可能带 sig| 前缀）。"""
    lines = text.strip().split("\n")
    parsed = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        # 跳过 markdown 代码块标记
        if line in ("```jsonline", "```json", "```"):
            continue

        # 允许 sig| 前缀（ForGal-json-multi-chat 格式）
        if "|" in line and re.match(r"^[a-z0-9]{3}\|", line):
            _, json_part = line.split("|", 1)
        else:
            json_part = line

        try:
            obj = json.loads(json_part)
            if isinstance(obj, dict):
                parsed.append(obj)
        except json.JSONDecodeError:
            # jsonline 中单行解析失败不阻止整体
            warnings.append(f"第{i+1}行 JSON 解析失败：{line[:100]}")

    if not parsed:
        errors.append("jsonline 格式未解析到任何有效 JSON 对象")
        return None

    return parsed


def _try_parse_tsv(
    text: str, errors: List[str], _warnings: List[str]
) -> Optional[List[List[str]]]:
    """尝试解析 TSV 格式。"""
    lines = text.strip().split("\n")
    parsed = []
    for _i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        # 跳过 markdown 代码块标记
        if line.startswith("```"):
            continue
        # 跳过表头行
        if "日文" in line and "中文" in line:
            continue

        fields = line.split("\t")
        if len(fields) >= 2:
            parsed.append(fields)

    if not parsed:
        errors.append("TSV 格式未解析到任何有效行")
        return None

    return parsed


# ── Layer 2：结构化数据校验 ──

def validate_global_prompt(data: Any) -> dict:
    """
    校验 GlobalPrompt.json 数据完整性。

    必填字段：
      - 游戏名称 (str, 非空)
      - 剧情概述 (str, 非空)
      - 角色列表 (list, 非空)
      角色列表中每项必须有：名称 (str, 非空)

    可选但鼓励的字段：
      - 世界观设定 (str)
      - 行文风格 (str)
      - 题材标签 (list[str])
    """
    errors: List[str] = []
    warnings: List[str] = []
    stats: Dict[str, Any] = {}

    if not isinstance(data, dict):
        errors.append(f"GlobalPrompt 根对象不是 dict，实际类型：{type(data).__name__}")
        return _make_result(valid=False, errors=errors, warnings=warnings, stats=stats)

    # 必填字段：游戏名称
    game_name = data.get("游戏名称", "")
    if not game_name or not isinstance(game_name, str) or game_name.strip() == "":
        errors.append("GlobalPrompt 缺少必填字段「游戏名称」或为空")

    # 必填字段：剧情概述
    plot = data.get("剧情概述", "")
    if not plot or not isinstance(plot, str) or plot.strip() == "":
        errors.append("GlobalPrompt 缺少必填字段「剧情概述」或为空")

    # 必填字段：角色列表
    characters = data.get("角色列表", [])
    if not isinstance(characters, list):
        errors.append(
            f"GlobalPrompt「角色列表」类型错误，期望 list，"
            f"实际：{type(characters).__name__}"
        )
    elif len(characters) == 0:
        errors.append("GlobalPrompt「角色列表」为空，至少需要一个角色")
    else:
        stats["character_count"] = len(characters)
        chars_without_speech = []
        for i, char in enumerate(characters):
            if not isinstance(char, dict):
                errors.append(f"角色列表第{i+1}项不是 dict")
                continue
            name = char.get("名称", "")
            if not name or not isinstance(name, str) or name.strip() == "":
                errors.append(f"角色列表第{i+1}项缺少「名称」或为空")
            # 可选但鼓励的字段
            speech = char.get("说话风格", "")
            if not speech or not isinstance(speech, str) or speech.strip() == "":
                chars_without_speech.append(
                    name if name else f"第{i+1}项"
                )
        if chars_without_speech:
            warnings.append(
                f"以下角色缺少「说话风格」描述：{', '.join(chars_without_speech[:5])}"
                + ("..." if len(chars_without_speech) > 5 else "")
            )

    # 可选字段检查
    if not data.get("世界观设定"):
        warnings.append("GlobalPrompt 缺少「世界观设定」字段")
    if not data.get("行文风格"):
        warnings.append("GlobalPrompt 缺少「行文风格」字段")
    tags = data.get("题材标签")
    if tags is not None and not isinstance(tags, list):
        warnings.append(f"GlobalPrompt「题材标签」类型错误，期望 list，实际：{type(tags).__name__}")

    return _make_result(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        stats=stats,
    )


def validate_file_metadata_entry(data: Any) -> dict:
    """
    校验单条 FileMetaData 条目格式。

    必填字段：
      - id (str, 非空，一般为文件名)
      - 角色 (list[str], 至少一项)
      - 剧情 (str, 非空)
    """
    errors: List[str] = []
    warnings: List[str] = []
    stats: Dict[str, Any] = {}

    if not isinstance(data, dict):
        errors.append(f"FileMetaData 条目不是 dict，实际类型：{type(data).__name__}")
        return _make_result(valid=False, errors=errors, warnings=warnings, stats=stats)

    entry_id = data.get("id", "")
    if not entry_id or not isinstance(entry_id, str):
        errors.append("FileMetaData 条目缺少 'id' 或类型错误")

    roles = data.get("角色", [])
    if not isinstance(roles, list):
        errors.append(f"FileMetaData「角色」类型错误，期望 list，实际：{type(roles).__name__}")
    elif len(roles) == 0:
        warnings.append(f"FileMetaData 条目 {entry_id}「角色」列表为空")

    plot = data.get("剧情", "")
    if not plot or not isinstance(plot, str) or plot.strip() == "":
        warnings.append(f"FileMetaData 条目 {entry_id}「剧情」为空")

    return _make_result(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        stats=stats,
    )


def validate_batch_metadata_entry(
    data: Any, max_index: int = 0
) -> dict:
    """
    校验单条 BatchMetadata 条目格式。

    必填字段：
      - id (str, 非空)
      - 批次 (list[dict], 非空)
      每批必须有：区间 [lo, hi]（整数闭区间）、视角、氛围、h、用词色彩

    额外检查（如果提供了 max_index）：
      - 所有区间在 [1, max_index] 范围内
      - 区间不重叠
      - 区间按 lo 升序排列
    """
    errors: List[str] = []
    warnings: List[str] = []
    stats: Dict[str, Any] = {"batch_count": 0}

    if not isinstance(data, dict):
        errors.append(f"BatchMetadata 条目不是 dict，实际类型：{type(data).__name__}")
        return _make_result(valid=False, errors=errors, warnings=warnings, stats=stats)

    entry_id = data.get("id", "")
    if not entry_id or not isinstance(entry_id, str):
        errors.append("BatchMetadata 条目缺少 'id' 或类型错误")

    batches = data.get("批次", data.get("batches", []))
    if not isinstance(batches, list):
        errors.append(f"BatchMetadata「批次」类型错误，期望 list，实际：{type(batches).__name__}")
        return _make_result(valid=len(errors) == 0, errors=errors, warnings=warnings, stats=stats)

    if len(batches) == 0:
        errors.append(f"BatchMetadata 条目 {entry_id}「批次」为空")
        return _make_result(valid=False, errors=errors, warnings=warnings, stats=stats)

    stats["batch_count"] = len(batches)

    prev_hi = 0
    for i, batch in enumerate(batches):
        if not isinstance(batch, dict):
            errors.append(f"批次第{i+1}项不是 dict")
            continue

        interval = batch.get("区间", batch.get("interval"))
        if not isinstance(interval, (list, tuple)) or len(interval) < 2:
            errors.append(f"批次第{i+1}项缺少「区间」或格式错误")
            continue

        try:
            lo, hi = int(interval[0]), int(interval[1])
        except (TypeError, ValueError):
            errors.append(f"批次第{i+1}项「区间」值不是有效整数：{interval}")
            continue

        if lo > hi:
            warnings.append(
                f"批次第{i+1}项区间 [{lo},{hi}] lo > hi，已自动交换"
            )
            lo, hi = hi, lo

        # 范围校验
        if max_index > 0:
            if lo < 1:
                errors.append(f"批次第{i+1}项区间起始 {lo} < 1")
            if hi > max_index:
                errors.append(f"批次第{i+1}项区间结束 {hi} > 最大行号 {max_index}")

        # 重叠检测
        if lo <= prev_hi and prev_hi > 0:
            errors.append(
                f"批次第{i+1}项区间 [{lo},{hi}] 与前一批次 [{batches[i-1].get('区间', '?')[0]},{prev_hi}] 重叠"
            )
        prev_hi = hi

        # 必填字段检查（中→英别名映射，支持 LLM 返回中/英键名）
        _FIELD_EN_MAP = {
            "视角": "perspective",
            "氛围": "atmosphere",
            "用词色彩": "tone",
        }
        for field, zh_name in [
            ("视角", "视角"),
            ("氛围", "氛围"),
            ("用词色彩", "用词色彩"),
        ]:
            en_key = _FIELD_EN_MAP.get(field, field)
            val = batch.get(field, batch.get(en_key, ""))
            if not val or not isinstance(val, str) or val.strip() == "":
                warnings.append(f"批次第{i+1}项缺少「{zh_name}」或为空")

    # 覆盖完整性检查
    if max_index > 0 and batches and not errors:
        covered = set()
        for batch in batches:
            interval = batch.get("区间", batch.get("interval", []))
            if len(interval) >= 2:
                try:
                    lo, hi = int(interval[0]), int(interval[1])
                    for idx in range(max(1, lo), min(hi, max_index) + 1):
                        covered.add(idx)
                except (TypeError, ValueError):
                    pass
        if len(covered) < max_index:
            missing_ranges = []
            current_start = None
            for idx in range(1, max_index + 1):
                if idx not in covered:
                    if current_start is None:
                        current_start = idx
                else:
                    if current_start is not None:
                        missing_ranges.append(f"[{current_start},{idx-1}]")
                        current_start = None
            if current_start is not None:
                missing_ranges.append(f"[{current_start},{max_index}]")
            warnings.append(
                f"BatchMetadata 条目 {entry_id} 未完全覆盖行号 1~{max_index}，"
                f"缺失区间：{', '.join(missing_ranges[:5])}"
                + ("..." if len(missing_ranges) > 5 else "")
            )

    return _make_result(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        stats=stats,
    )


# ── Layer 3：元数据层交叉验证 ──

def cross_validate_counts(
    expected: int,
    actual: int,
    label: str = "",
) -> dict:
    """
    跨阶段计数交叉验证。

    Args:
        expected: 期望数量
        actual: 实际数量
        label: 验证对象标签（用于日志）

    Returns:
        校验结果字典
    """
    errors: List[str] = []
    warnings: List[str] = []
    stats: Dict[str, Any] = {"expected": expected, "actual": actual}
    label_text = f"「{label}」" if label else ""

    if actual < expected:
        diff = expected - actual
        msg = f"交叉验证{label_text}：实际 {actual} < 期望 {expected}，缺失 {diff} 项"
        if diff <= 3:
            warnings.append(msg)
        else:
            errors.append(msg)
    elif actual > expected:
        diff = actual - expected
        warnings.append(
            f"交叉验证{label_text}：实际 {actual} > 期望 {expected}，多出 {diff} 项"
        )
    else:
        # 数量匹配
        pass

    return _make_result(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        stats=stats,
    )


# ── Layer 4：输出层校验 ──

def validate_translation_output(
    input_count: int,
    output_trans_list: Optional[list] = None,
) -> dict:
    """
    校验翻译输出完整性。

    检查项：
      - 翻译行数与输入行数一致
      - 无遗漏的空翻译
      - 无残留的失败标记 (Failed)

    Args:
        input_count: 输入句子数
        output_trans_list: 翻译后的 CTransList

    Returns:
        校验结果字典
    """
    errors: List[str] = []
    warnings: List[str] = []
    stats: Dict[str, Any] = {"input_count": input_count}

    if output_trans_list is None:
        stats["output_count"] = 0
        errors.append("输出翻译列表为 None")
        return _make_result(valid=False, errors=errors, warnings=warnings, stats=stats)

    output_count = len(output_trans_list)
    stats["output_count"] = output_count

    if output_count != input_count:
        errors.append(
            f"翻译输出行数 ({output_count}) 与输入行数 ({input_count}) 不一致"
        )

    # 统计失败标记和空翻译
    failed_count = 0
    empty_dst_count = 0
    for tran in output_trans_list:
        if hasattr(tran, "pre_dst"):
            dst = tran.pre_dst
            if not dst or dst.strip() == "":
                empty_dst_count += 1
            elif "(Failed)" in dst:
                failed_count += 1

    stats["failed_count"] = failed_count
    stats["empty_dst_count"] = empty_dst_count

    if failed_count > 0:
        ratio = failed_count / max(output_count, 1)
        if ratio > 0.1:
            errors.append(
                f"翻译失败率过高：{failed_count}/{output_count} ({ratio:.1%})"
            )
        else:
            warnings.append(
                f"存在翻译失败项：{failed_count}/{output_count} ({ratio:.1%})"
            )

    if empty_dst_count > 0:
        warnings.append(f"存在空白翻译：{empty_dst_count} 句")

    return _make_result(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        stats=stats,
    )
