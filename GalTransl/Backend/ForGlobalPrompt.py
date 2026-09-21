"""
ForGlobalPrompt — 全局提示词(GlobalPrompt)生成后端（全流程翻译管线 第一步）

该后端不翻译文本、不使用多轮对话、使用专用系统提示词。
它读取压缩后的游戏全文文本以及外部信息（游戏名称等），要求 LLM 生成：
  - 全局剧情概述：整体剧情框架、核心冲突、情感主线
  - 角色列表：每个角色的形象、语气、说话风格
  - 世界观设定：游戏的世界背景
  - 行文风格：剧本的整体文风特征
  - 题材标签：游戏的整体题材标签

解析后写入 transl_cache/pass0_cache/GlobalPrompt.json。
设计参考 ForFileMetaData / ForBatchMetaData：通过覆盖 batch_translate 走独立的
"生成"流程，完全绕开翻译模型的输入/输出契约（不写 gt_output）。

上游：TextCompressor 的压缩输出
下游：ForFileMetaData、ForBatchMetaData、ForGalJsonTranslate 可注入此全局上下文
"""

from __future__ import annotations

import json
import os
from threading import Lock
from typing import Any, Dict, List, Optional

from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProxyPool, CProjectConfig, initDictList
from GalTransl import LOGGER, PASS0_CACHE_DIR
from GalTransl.Dictionary import CGptDict
from GalTransl.Backend.BaseEngine import BaseEngine, register_engine
from GalTransl.Backend.Prompts import FORGLOBAL_PROMPT, FORGLOBAL_SYSTEM
from GalTransl.Backend.utils import coerce_bool, extract_json_object
from GalTransl.DataValidator import validate_global_prompt
from GalTransl.server_runtime import set_live_snippets


# ── 全局提示词加载工具函数 ──

def _find_global_prompt_path(projectConfig: CProjectConfig) -> str:
    """返回 GlobalPrompt.json 的完整路径（pass0_cache 目录下）。"""
    return os.path.join(
        projectConfig.getCachePath(), PASS0_CACHE_DIR, "GlobalPrompt.json"
    )


def _select_compressed_paths(
    compressed_data: Dict[str, str],
    file_filter: Optional[List[str]],
) -> List[str]:
    """按 file_filter 挑选 compressed_data 中要纳入分析的路径。

    筛选口径（宽松匹配，便于前端多选/路线化传入的各种写法）：
      1. 完整路径精确命中
      2. 文件名（basename，含扩展名）命中
      3. 去扩展名的文件名命中（如 "route_a" 匹配 "route_a.json"）

    保留 compressed_data 原有顺序；file_filter 为 None / 空时返回全部。
    未命中任何文件的 filter 项会被忽略并记 warning（不视为错误，避免因
    文件名写法差异导致整个分析中止）。
    """
    if not file_filter:
        return list(compressed_data.keys())

    by_exact = set(compressed_data.keys())
    by_basename: Dict[str, str] = {}
    by_stem: Dict[str, str] = {}
    for path in compressed_data:
        base = os.path.basename(path)
        by_basename.setdefault(base, path)
        by_stem.setdefault(os.path.splitext(base)[0], path)

    selected: List[str] = []
    seen: set = set()
    unmatched: List[str] = []
    for raw in file_filter:
        key = str(raw or "").strip()
        if not key:
            continue
        hit = None
        if key in by_exact:
            hit = key
        elif key in by_basename:
            hit = by_basename[key]
        elif key in by_stem:
            hit = by_stem[key]
        if hit is None:
            unmatched.append(key)
            continue
        if hit not in seen:
            seen.add(hit)
            selected.append(hit)

    if unmatched:
        LOGGER.warning(
            f"[GlobalPrompt] file_filter 中有 {len(unmatched)} 项未匹配到任何文件，"
            f"已忽略：{', '.join(unmatched[:5])}"
        )
    # 按 compressed_data 原顺序返回，保证提示词内文件顺序稳定
    return [p for p in compressed_data if p in seen]


def load_global_prompt(projectConfig: CProjectConfig) -> Optional[dict]:
    """
    从 transl_cache/pass0_cache/GlobalPrompt.json 加载全局提示词。

    用于流水线后续阶段（ForFileMetaData、ForBatchMetaData、ForGalJsonTranslate）
    在需要时读取全局分析结果。

    Returns:
        解析后的 GlobalPrompt 字典；文件不存在或解析失败时返回 None
    """
    path = _find_global_prompt_path(projectConfig)
    if not os.path.exists(path):
        LOGGER.debug(f"[GlobalPrompt] 文件不存在：{path}")
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        LOGGER.warning(f"[GlobalPrompt] 读取失败：{e}")
        return None

    if not isinstance(data, dict):
        LOGGER.warning(
            f"[GlobalPrompt] 根元素类型异常，期望 dict，"
            f"实际：{type(data).__name__}"
        )
        return None

    return data


def _format_global_prompt_as_context(
    gp: dict, annotate_plot: bool = False, characters: Optional[List[dict]] = None
) -> str:
    """
    将 GlobalPrompt 字典格式化为可供其他后端注入提示词的文本块。

    格式化后的文本用于替换提示词模板中的 [global_prompt] 占位符。
    如果 gp 为 None 或空，返回空字符串（占位符被清除）。

    Args:
        annotate_plot: 为 True 时在「剧情概述」标题处附加标注，说明该剧情
            为游戏全局剧情、可能与当前文件不完全对应。
        characters: 按需注入的角色条目子集（None=全量渲染「角色列表」）。
            传入空列表时省略角色段。
    """
    if not gp or not isinstance(gp, dict):
        return ""

    parts: List[str] = []

    # 剧情概述
    plot = gp.get("剧情概述", "")
    if plot and isinstance(plot, str) and plot.strip():
        if annotate_plot:
            heading = (
                "# 全局剧情概述（游戏整体剧情，可能与当前文件不完全对应，"
                "请优先参考上方路线剧情和文件元数据）"
            )
        else:
            heading = "# 全局剧情概述"
        parts.append(f"{heading}\n{plot.strip()}")

    # 角色列表
    characters = characters if characters is not None else gp.get("角色列表", [])
    if isinstance(characters, list) and characters:
        char_lines = ["# 角色设定"]
        for ch in characters:
            if not isinstance(ch, dict):
                continue
            name = ch.get("名称", "")
            if isinstance(name, (list, tuple)):
                name_str = "、".join(str(x) for x in name)
            elif isinstance(name, str):
                name_str = name
            else:
                name_str = str(name)
            if not name_str.strip():
                continue
            info_parts = [f"- {name_str}"]
            for key, label in [
                ("形象", "形象"),
                ("语气", "语气"),
                ("说话风格", "说话风格"),
                ("关系", "关系"),
            ]:
                val = ch.get(key, "")
                if val and isinstance(val, str) and val.strip():
                    info_parts.append(f"  {label}：{val.strip()}")
            char_lines.append("\n".join(info_parts))
        if len(char_lines) > 1:
            parts.append("\n".join(char_lines))

    # 世界观设定
    world = gp.get("世界观设定", "")
    if world and isinstance(world, str) and world.strip():
        parts.append(f"# 世界观设定\n{world.strip()}")

    # 行文风格
    style = gp.get("行文风格", "")
    if style and isinstance(style, str) and style.strip():
        parts.append(f"# 行文风格\n{style.strip()}")

    # 题材标签
    tags = gp.get("题材标签", [])
    if isinstance(tags, list) and tags:
        parts.append(f"# 题材标签\n{'、'.join(str(t) for t in tags)}")

    if not parts:
        return ""

    return "\n\n".join(parts)


MERGE_FIELD_KEYS: tuple = (
    "游戏名称",
    "剧情概述",
    "角色列表",
    "世界观设定",
    "行文风格",
    "题材标签",
)


def merge_global_prompt(
    base: Optional[dict],
    incoming: dict,
    fields: Optional[List[str]] = None,
) -> dict:
    """把子集分析结果合并进已有全局分析（策略：覆盖指定字段）。

    Args:
        base: 已有 GlobalPrompt（None 或空表示首次分析，直接返回 incoming）。
        incoming: 本次（可能是子集）分析产出的规整结果。
        fields: 要覆盖的字段名；None 时覆盖全部字段（= 整体替换）。

    Returns:
        合并后的新 dict（不修改入参）。

    说明：未在 fields 中的字段保留 base 的值，使「只对一条路线重跑全局分析」
    不会抹掉其余字段。fields 中出现在 incoming 的空值也不覆盖 base 的非空内容
    ——规整阶段会用空值补齐缺失键，直接覆盖会让子集分析清空无关字段。
    """
    if not base or not isinstance(base, dict):
        return dict(incoming)

    target_fields = MERGE_FIELD_KEYS if fields is None else tuple(fields)
    merged = dict(base)
    for key in target_fields:
        if key not in incoming:
            continue
        # 规整后的 incoming 会用空值补齐缺失字段；子集分析时空值不应抹掉
        # base 里已有的非空内容（模型对子集未提及的角色/设定属正常现象）。
        incoming_value = incoming[key]
        if _is_blank_value(incoming_value):
            continue
        merged[key] = incoming_value
    return merged


def _is_blank_value(value: Any) -> bool:
    """判断规整后的字段值是否为「空缺」（空串 / 空列表）。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


# ── ForGlobalPrompt 后端 ──

@register_engine("ForGlobalPrompt")
class ForGlobalPrompt(BaseEngine):
    """
    ForGlobalPrompt — 全局提示词生成后端。

    不翻译、不用系统提示词、不用多轮对话。
    接收压缩后的全文 + 外部信息（游戏名称、简介、制作公司等），要求 LLM 输出全局游戏分析 JSON，
    解析后写入 transl_cache/pass0_cache/GlobalPrompt.json。
    """

    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool],
        token_pool: COpenAITokenPool,
    ) -> None:
        """
        初始化 ForGlobalPrompt 后端。

        与 ForFileMetaData 类似：不使用系统提示词、不翻译、不多轮。
        """
        super().__init__(config, eng_type, proxy_pool, token_pool)

        self.system_prompt = FORGLOBAL_SYSTEM
        self.trans_prompt = FORGLOBAL_PROMPT
        self._setup_prompts(eng_type, config)

        # 是否把项目翻译规范注入提示词（默认开启）
        raw = self.pj_config.getKey(
            "internals.forglobalprompt.inject_guideline", True
        )
        self._inject_guideline = coerce_bool(raw, default=True)

        # 跨文件写 GlobalPrompt.json 时的互斥锁（虽然当前只有一次写入，
        # 但保留锁以防未来并发场景）
        self._gp_lock = Lock()

    # 0. 可控注入翻译规范
    def _build_prompt_request(
        self,
        input_src: str,
        gptdict: str = "",
        external_info: str = "",
    ) -> str:
        """
        在基类占位符替换的基础上，增加 GlobalPrompt 特有的占位符：
        [ExternalInfo] — 外部信息（游戏名称、简介、制作公司等）。

        公共占位符（[Input]/[Glossary]/[translation_guideline]/
        [SourceLang]/[TargetLang]）由基类统一替换。
        """
        prompt_req = super()._build_prompt_request(
            input_src,
            gptdict,
            translation_guideline=self._build_guideline_block(),
        )
        # 外部信息：用户自由填写的游戏相关信息
        prompt_req = prompt_req.replace(
            "[ExternalInfo]", external_info or "（未提供外部信息）"
        )
        return prompt_req

    # 1. 构建输入文本
    @staticmethod
    def _build_input_text_from_compressed(
        compressed_data: Dict[str, str],
        file_filter: Optional[List[str]] = None,
    ) -> str:
        """
        将各文件的压缩后文本合并为一段完整的待分析文本。

        Args:
            compressed_data: {文件路径: 压缩后文本}
            file_filter: 仅纳入这些文件（支持完整路径、文件名或去扩展名的
                文件名）。None / 空列表 = 全量（不筛选）。

        Returns:
            合并后的全文文本
        """
        if not compressed_data:
            return ""

        selected = _select_compressed_paths(compressed_data, file_filter)
        parts: List[str] = []
        for file_path in selected:
            text = compressed_data[file_path]
            if text and text.strip():
                short_name = os.path.basename(file_path)
                parts.append(f"=== {short_name} ===\n{text.strip()}")

        return "\n\n".join(parts)

    # 2. 构建术语表文本
    def _build_glossary_text(self) -> str:
        """仅把项目的 gpt.dict 中项目专属字典格式化为 Markdown 译表。

        全局提示词阶段只注入项目级字典（(project_dir) 前缀），
        不注入公共字典，避免无关术语干扰全局分析。
        """
        dict_cfg = self.pj_config.getDictCfgSection()
        if not dict_cfg:
            return ""
        gpt_dic_list = dict_cfg.get("gpt.dict", [])
        if not gpt_dic_list:
            return ""
        # 仅保留项目专属字典条目
        project_only = [e for e in gpt_dic_list if str(e).startswith("(project_dir)")]
        if not project_only:
            LOGGER.debug("[GlobalPrompt] 无项目专属字典，跳过 GPT 字典注入")
            return ""
        default_dic_dir = dict_cfg.get("defaultDictFolder", "")
        try:
            paths = initDictList(
                project_only, default_dic_dir, self.pj_config.getProjectDir()
            )
            gpt_dic = CGptDict(paths)
        except Exception as e:
            LOGGER.warning(
                f"[GlobalPrompt] 载入项目 GPT 字典失败，"
                f"全局分析将不含专名译表：{e}"
            )
            return ""

        lines = [
            "# Glossary",
            "| Src | Dst(/Dst2/..) | Note |",
            "| --- | --- | --- |",
        ]
        skipped = 0
        for dic in getattr(gpt_dic, "_dic_list", []):
            # 过滤 h 场景词条，避免污染全局剧情分析（与元数据轮口径一致）
            if gpt_dic._is_h_dict(dic):
                skipped += 1
                continue
            note = getattr(dic, "note", "") or ""
            lines.append(
                f"| {dic.search_word} | {dic.replace_word} | {note} |"
            )
        LOGGER.debug(
            f"[GlobalPrompt] 已载入项目 GPT 字典，共 {len(lines) - 3} 条"
            f"（跳过 {skipped} 条 h 词条）"
        )
        return "\n".join(lines)

    # 3. 解析与规整 LLM 返回的 JSON
    @staticmethod
    def _parse_global_prompt(text: str) -> Optional[dict]:
        """
        从 LLM 返回的原始文本中解析 GlobalPrompt JSON。

        与其它元数据引擎统一走 utils.extract_json_object（去 </think>、代码块提取、
        JSON 边界定位、容错解析），不再走 DataValidator.validate_llm_response 的
        重复 JSON 提取路径；仅保留 GlobalPrompt 特有的乱码(U+FFFD)诊断。
        内容级校验由 batch_translate 中的 validate_global_prompt 负责。
        """
        if not text or not text.strip():
            LOGGER.debug("[GlobalPrompt] LLM 返回为空，跳过")
            return None
        if "\ufffd" in text:
            LOGGER.warning("[GlobalPrompt] LLM 返回包含乱码字符（U+FFFD 替换字符）")
        return extract_json_object(text, tag="GlobalPrompt")

    @staticmethod
    def _normalize_global_prompt(obj: dict) -> dict:
        """
        规整字段类型，确保输出 JSON 结构一致。

        处理：
          - 字符串字段统一为 str 并 strip
          - 角色列表统一为 list[dict]
          - 题材标签统一为 list[str]
          - 缺失字段用空值填充
        """
        # 辅助函数：安全取字符串
        def _str(val: Any) -> str:
            if val is None:
                return ""
            if isinstance(val, str):
                return val.strip()
            return str(val).strip()

        # 角色列表规整
        characters = obj.get("角色列表", [])
        if not isinstance(characters, list):
            characters = []
        normalized_chars = []
        for ch in characters:
            if not isinstance(ch, dict):
                continue
            name = _str(ch.get("名称", ""))
            if not name:
                continue  # 跳过无名角色
            normalized_chars.append({
                "名称": name,
                "形象": _str(ch.get("形象", "")),
                "语气": _str(ch.get("语气", "")),
                "说话风格": _str(ch.get("说话风格", "")),
                "关系": _str(ch.get("关系", "")),
            })

        # 题材标签规整
        tags = obj.get("题材标签", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.replace("、", ",").split(",") if t.strip()]
        elif not isinstance(tags, list):
            tags = []
        else:
            tags = [_str(t) for t in tags if _str(t)]

        return {
            "游戏名称": _str(obj.get("游戏名称", "")),
            "剧情概述": _str(obj.get("剧情概述", "")),
            "角色列表": normalized_chars,
            "世界观设定": _str(obj.get("世界观设定", "")),
            "行文风格": _str(obj.get("行文风格", "")),
            "题材标签": tags,
        }

    # 4. 保存 GlobalPrompt.json
    def _save_global_prompt(self, data: dict) -> None:
        """
        线程安全写入 transl_cache/pass0_cache/GlobalPrompt.json。

        当前流水线中只写入一次（全局分析只有一个结果），
        但保留锁以兼容未来可能的并发写入场景。
        """
        out_dir = os.path.join(
            self.pj_config.getCachePath(), PASS0_CACHE_DIR
        )
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "GlobalPrompt.json")

        with self._gp_lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        LOGGER.debug(
            f"[GlobalPrompt] 已保存 {path}"
        )

    # 5. 入口
    async def batch_translate(
        self,
        compressed_data: Dict[str, str],
        external_info: str = "",
        file_filter: Optional[List[str]] = None,
        merge_fields: Optional[List[str]] = None,
    ) -> bool:
        """
        全局提示词生成的入口方法。

        Args:
            compressed_data: {文件路径: 压缩后文本}，来自 TextCompressor
            external_info: 外部信息字符串（游戏名称、简介、制作公司等，用户自由填写）
            file_filter: 仅分析这些文件（完整路径 / 文件名 / 去扩展名文件名）。
                None 或空列表 = 全量（与 0.4.x 行为一致）。
            merge_fields: 子集分析时只覆盖这些字段；None = 覆盖全部字段。
                首次分析（无已有产物）时该参数无效果。

        Returns:
            True 如果生成成功并写入 GlobalPrompt.json，否则 False
        """
        # ── 入参校验 ──
        if not compressed_data or not isinstance(compressed_data, dict):
            LOGGER.error(
                f"[GlobalPrompt] compressed_data 类型错误，"
                f"期望 dict，实际 {type(compressed_data).__name__}，跳过"
            )
            return False

        # 过滤空文本
        compressed_data = {
            k: v
            for k, v in compressed_data.items()
            if v and isinstance(v, str) and v.strip()
        }
        if not compressed_data:
            LOGGER.warning("[GlobalPrompt] compressed_data 全为空，跳过")
            return False

        # 子集筛选：选中文件可能全部为空文本 → 提前失败，避免把空提示词发给模型
        selected_paths = _select_compressed_paths(compressed_data, file_filter)
        if not selected_paths:
            LOGGER.error(
                f"[GlobalPrompt] file_filter 未匹配到任何有效文件"
                f"（候选 {len(compressed_data)} 个），跳过"
            )
            return False

        # 外部信息：参数 > 配置
        if not external_info:
            external_info = self.pj_config.getKey("externals.gameInfo", "") or ""

        # ── 构建输入 ──
        input_text = self._build_input_text_from_compressed(
            compressed_data, file_filter=file_filter
        )
        glossary_text = self._build_glossary_text()
        prompt = self._build_prompt_request(
            input_text, glossary_text, external_info=external_info
        )

        total_files = len(selected_paths)
        total_chars = len(input_text)
        scope = "全量" if not file_filter else f"子集（{total_files} 个文件）"
        LOGGER.info(
            f"[GlobalPrompt] 开始生成全局提示词，范围：{scope}…"
        )
        LOGGER.debug(
            f"[GlobalPrompt] 提示词长度：{len(prompt)} 字符，"
            f"压缩后文本 {total_chars} 字符"
        )

        # ── 调用 LLM ──
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt},
        ]
        rsp, token = await self._call_llm_with_error_report(
            messages, "GlobalPrompt", max_retry_count=3, tag="GlobalPrompt"
        )
        if rsp is None:
            return False

        # ── 解析响应 ──
        meta = self._parse_global_prompt(rsp or "")
        if not meta:
            LOGGER.warning(
                "[GlobalPrompt] 未解析到有效的 GlobalPrompt JSON，跳过"
            )
            return False

        # ── 规整字段 ──
        meta = self._normalize_global_prompt(meta)

        # ── 内容校验 ──
        gp_validation = validate_global_prompt(meta)
        if not gp_validation["valid"]:
            for err in gp_validation["errors"]:
                LOGGER.error(f"[GlobalPrompt] 内容校验失败：{err}")
            return False
        for warn in gp_validation["warnings"]:
            LOGGER.warning(f"[GlobalPrompt] 内容校验警告：{warn}")

        # ── 合并（策略：覆盖指定字段）──
        # 子集分析按字段合并，避免抹掉已有结果中本次未涉及的字段。
        # 未显式指定 merge_fields 时默认覆盖全部字段（等价整体替换，但保留 base 的键序）。
        base = load_global_prompt(self.pj_config) if file_filter else None
        if base is not None:
            fields = merge_fields if merge_fields is not None else list(MERGE_FIELD_KEYS)
            before = len(base.get("角色列表", []))
            meta = merge_global_prompt(base, meta, fields)
            LOGGER.info(
                f"[GlobalPrompt] 已按子集结果覆盖 {len(fields)} 个字段，"
                f"角色数 {before} → {len(meta.get('角色列表', []))}"
            )

        # ── 保存 ──
        self._save_global_prompt(meta)

        # 推送结果预览（前端翻译控制台"结果预览"；预览异常不影响主流程）
        try:
            set_live_snippets(
                self.runtime_project_dir,
                translation_preview=json.dumps(meta, ensure_ascii=False, indent=2),
            )
        except Exception:
            pass

        char_count = len(meta.get("角色列表", []))
        LOGGER.info(
            f"[GlobalPrompt] LLM 返回解析成功，共 {char_count} 个角色"
        )
        LOGGER.info(
            f"[GlobalPrompt] 已写入 transl_cache/pass0_cache/GlobalPrompt.json"
        )

        return True


if __name__ == "__main__":
    pass
