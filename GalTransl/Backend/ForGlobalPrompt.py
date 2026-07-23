"""
ForGlobalPrompt — 全局提示词(GlobalPrompt)生成后端（全流程翻译管线 第一步）

该后端不翻译文本、不使用多轮对话、不使用系统提示词。
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
下游：ForFileMetaData、ForBatchMetaData、ForGalJsonMulitChat 可注入此全局上下文
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
from GalTransl.Utils import extract_code_blocks
from GalTransl.Backend.BaseTranslate import BaseTranslate
from GalTransl.Backend.Prompts import FORGLOBAL_PROMPT
from GalTransl.DataValidator import validate_global_prompt, validate_llm_response


# ── 全局提示词加载工具函数 ──

def _find_global_prompt_path(projectConfig: CProjectConfig) -> str:
    """返回 GlobalPrompt.json 的完整路径（pass0_cache 目录下）。"""
    return os.path.join(
        projectConfig.getCachePath(), PASS0_CACHE_DIR, "GlobalPrompt.json"
    )


def load_global_prompt(projectConfig: CProjectConfig) -> Optional[dict]:
    """
    从 transl_cache/pass0_cache/GlobalPrompt.json 加载全局提示词。

    用于流水线后续阶段（ForFileMetaData、ForBatchMetaData、ForGalJsonMulitChat）
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


def _format_global_prompt_as_context(gp: dict) -> str:
    """
    将 GlobalPrompt 字典格式化为可供其他后端注入提示词的文本块。

    格式化后的文本用于替换提示词模板中的 [global_prompt] 占位符。
    如果 gp 为 None 或空，返回空字符串（占位符被清除）。
    """
    if not gp or not isinstance(gp, dict):
        return ""

    parts: List[str] = []

    # 剧情概述
    plot = gp.get("剧情概述", "")
    if plot and isinstance(plot, str) and plot.strip():
        parts.append(f"# 全局剧情概述\n{plot.strip()}")

    # 角色列表
    characters = gp.get("角色列表", [])
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


# ── ForGlobalPrompt 后端 ──

class ForGlobalPrompt(BaseTranslate):
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

        # 不使用系统提示词：system 内容为空，且实际请求只发 user 消息
        self.system_prompt = ""
        self.trans_prompt = FORGLOBAL_PROMPT
        self._apply_internal_prompt_template_overrides()
        self.init_chatbot(eng_type, config)

        # 是否把项目翻译规范注入提示词（默认开启）
        raw = self.pj_config.getKey(
            "internals.forglobalprompt.inject_guideline", True
        )
        if isinstance(raw, bool):
            self._inject_guideline = raw
        else:
            self._inject_guideline = (
                str(raw).strip().lower() not in ("false", "0", "no", "")
            )

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
        [ExternalInfo] — 外部信息（游戏名称、简介、制作公司等）
        """
        prompt_req = self.trans_prompt

        # 外部信息：用户自由填写的游戏相关信息
        prompt_req = prompt_req.replace(
            "[ExternalInfo]", external_info or "（未提供外部信息）"
        )

        # 翻译规范可控注入
        if self._inject_guideline:
            guideline = (
                getattr(self.pj_config, "translation_guideline", "") or ""
            )
        else:
            guideline = ""
        guideline = (guideline or "").strip()
        if guideline:
            block = f"# 翻译规范（translation_guideline）\n{guideline}\n"
        else:
            block = ""
        prompt_req = prompt_req.replace("[translation_guideline]", block)

        # 其余占位符
        prompt_req = prompt_req.replace("[Input]", input_src)
        prompt_req = prompt_req.replace("[Glossary]", gptdict)
        prompt_req = prompt_req.replace("[SourceLang]", self.source_lang)
        prompt_req = prompt_req.replace("[TargetLang]", self.target_lang)

        return prompt_req

    # 1. 构建输入文本
    @staticmethod
    def _build_input_text_from_compressed(
        compressed_data: Dict[str, str]
    ) -> str:
        """
        将各文件的压缩后文本合并为一段完整的待分析文本。

        Args:
            compressed_data: {文件路径: 压缩后文本}

        Returns:
            合并后的全文文本
        """
        if not compressed_data:
            return ""

        parts: List[str] = []
        for file_path, text in compressed_data.items():
            if text and text.strip():
                short_name = os.path.basename(file_path)
                parts.append(f"=== {short_name} ===\n{text.strip()}")

        return "\n\n".join(parts)

    # 2. 构建术语表文本
    def _build_glossary_text(self) -> str:
        """把项目的 gpt.dict 全量格式化为 Markdown 译表，供模型遵循专名译法。"""
        dict_cfg = self.pj_config.getDictCfgSection()
        if not dict_cfg:
            return ""
        gpt_dic_list = dict_cfg.get("gpt.dict", [])
        if not gpt_dic_list:
            return ""
        default_dic_dir = dict_cfg.get("defaultDictFolder", "")
        try:
            paths = initDictList(
                gpt_dic_list, default_dic_dir, self.pj_config.getProjectDir()
            )
            gpt_dic = CGptDict(paths)
        except Exception as e:
            LOGGER.warning(
                f"[GlobalPrompt] 载入 GPT 字典失败，"
                f"全局分析将不含专名译表：{e}"
            )
            return ""

        lines = [
            "# Glossary",
            "| Src | Dst(/Dst2/..) | Note |",
            "| --- | --- | --- |",
        ]
        for dic in getattr(gpt_dic, "_dic_list", []):
            note = getattr(dic, "note", "") or ""
            lines.append(
                f"| {dic.search_word} | {dic.replace_word} | {note} |"
            )
        LOGGER.debug(
            f"[GlobalPrompt] 已载入 GPT 字典，共 {len(lines) - 3} 条"
        )
        return "\n".join(lines)

    # 3. 解析与规整 LLM 返回的 JSON
    @staticmethod
    def _parse_global_prompt(text: str) -> Optional[dict]:
        """
        从 LLM 返回的原始文本中解析 GlobalPrompt JSON。

        处理流程：
          1. 去除 </think> 内容（推理模型）
          2. 提取代码块（```json ... ```）
          3. 定位 JSON 对象边界（{ ... }）
          4. JSON 解析
          5. 校验顶层类型
        """
        if not text or not text.strip():
            LOGGER.debug("[GlobalPrompt] LLM 返回为空，跳过")
            return None

        # 校验 LLM 原始响应
        validation = validate_llm_response(text, expected_format="json")
        if not validation["valid"]:
            for err in validation["errors"]:
                LOGGER.warning(f"[GlobalPrompt] LLM 响应校验失败：{err}")
            for warn in validation["warnings"]:
                LOGGER.debug(f"[GlobalPrompt] LLM 响应校验警告：{warn}")

        # 尝试使用校验结果中已解析的数据
        parsed = validation.get("parsed_data")
        if isinstance(parsed, dict):
            return parsed

        # 回退：手动解析（兼容非标准格式）
        if "</think>" in text:
            text = text.split("</think>")[-1]

        lang_list, code_list = extract_code_blocks(text)
        if code_list:
            text = code_list[0]

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            LOGGER.debug(
                f"[GlobalPrompt] LLM 返回中未找到 JSON 对象，"
                f"原文前 200 字：{text[:200]}"
            )
            return None

        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            LOGGER.debug(
                f"[GlobalPrompt] JSON 解析失败：{e}，"
                f"原文前 200 字：{text[start:end+1][:200]}"
            )
            return None

        if not isinstance(obj, dict):
            LOGGER.debug(
                f"[GlobalPrompt] 解析结果不是 dict，"
                f"实际类型：{type(obj).__name__}"
            )
            return None

        return obj

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
        gpt_dic: Optional[CGptDict] = None,
    ) -> bool:
        """
        全局提示词生成的入口方法。

        Args:
            compressed_data: {文件路径: 压缩后文本}，来自 TextCompressor
            external_info: 外部信息字符串（游戏名称、简介、制作公司等，用户自由填写）
            gpt_dic: GPT 字典（未使用，保留以兼容基类接口）

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

        # 外部信息：参数 > 配置
        if not external_info:
            external_info = self.pj_config.getKey("externals.gameInfo", "") or ""

        # ── 构建输入 ──
        input_text = self._build_input_text_from_compressed(compressed_data)
        glossary_text = self._build_glossary_text()
        prompt = self._build_prompt_request(
            input_text, glossary_text, external_info=external_info
        )

        total_files = len(compressed_data)
        total_chars = len(input_text)
        LOGGER.info(
            f"[GlobalPrompt] 开始为 {total_files} 个文件生成全局提示词…"
        )
        LOGGER.debug(
            f"[GlobalPrompt] 提示词长度：{len(prompt)} 字符，"
            f"压缩后文本 {total_chars} 字符"
        )

        # ── 调用 LLM ──
        try:
            # 不使用系统提示词：直接以 user 消息发送
            messages = [{"role": "user", "content": prompt}]
            rsp, token = await self.ask_chatbot(
                messages=messages,
                file_name="GlobalPrompt",
                max_retry_count=3,
            )
        except Exception as e:
            LOGGER.error(
                f"[GlobalPrompt] LLM 请求失败：{type(e).__name__}: {e}",
                exc_info=True,
            )
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

        # ── 保存 ──
        self._save_global_prompt(meta)

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
