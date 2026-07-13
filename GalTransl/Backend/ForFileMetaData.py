import json
import os
import asyncio
import re
import traceback
from typing import Optional, List, Dict, Any
from threading import Lock

from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProxyPool, initDictList, CProjectConfig
from GalTransl import LOGGER
from GalTransl.Dictionary import CGptDict
from GalTransl.Utils import extract_code_blocks, fix_quotes
from GalTransl.Backend.BaseTranslate import BaseTranslate
from GalTransl.Backend.Prompts import FORFILEMETA_PROMPT


"""
ForFileMetaData - 文件级元数据(FileMetaData)生成后端

该后端不翻译文本、不使用多轮对话、不使用系统提示词。
它读取一个 Galgame 剧本文件（gt_input 下的 *.txt.json），把全文作为 user 消息
发给 LLM，要求模型概括剧情并输出一个 JSON 对象（角色/服装/剧情/标签），
解析后按文件名 id 合并写入 gt_input/FileMetaData.json。

默认会把项目的 translation_guideline（翻译规范，即 gpt.translation_guideline
指向的文件内容）注入提示词，供模型在命名角色/标签、把握剧情风格时遵循；
用户可在 config 的 internals.forfilemeta.inject_guideline 设为 false/0/no 关闭。

设计参考 GenDic：通过覆盖 batch_translate 走独立的"生成"流程，
完全绕开翻译模型的输入/输出契约（不写 gt_output）。
"""


class ForFileMetaData(BaseTranslate):
    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool],
        token_pool: COpenAITokenPool,
    ) -> None:
        """
        初始化 ForFileMetaData 后端。

        文件级元数据生成不依赖翻译规范文件的具体内容，但仍由基类正常载入
        项目自带的规范（如 自制提示词.md，现已归入项目程序、不会缺失）。
        """
        super().__init__(config, eng_type, proxy_pool, token_pool)

        # 不使用系统提示词：system 内容为空，且实际请求只发 user 消息
        self.system_prompt = ""
        self.trans_prompt = FORFILEMETA_PROMPT
        self._apply_internal_prompt_template_overrides()
        self.init_chatbot(eng_type, config)

        # 是否把项目翻译规范注入提示词（默认开启，可在 internals.forfilemeta.inject_guideline 关闭）
        raw = self.pj_config.getKey("internals.forfilemeta.inject_guideline", True)
        if isinstance(raw, bool):
            self._inject_guideline = raw
        else:
            self._inject_guideline = (
                str(raw).strip().lower() not in ("false", "0", "no", "")
            )

        # 跨文件（可能的并发 worker）写 FileMetaData.json 时的互斥锁
        self._fm_lock = Lock()

    # 0. 可控注入翻译规范
    def _build_prompt_request(self, input_src: str, gptdict: str) -> str:
        """
        在基类占位符替换的基础上，增加 translation_guideline 的可控注入：

        - 默认（_inject_guideline=True）把项目翻译规范整段注入提示词；
        - 关闭（config 设 internals.forfilemeta.inject_guideline=false）或
          规范为空时，占位段被替换为空，不会留下悬挂的标题。

        其余占位符（[Input]/[Glossary]/[SourceLang]/[TargetLang]）沿用基类行为。
        """
        prompt_req = self.trans_prompt
        if self._inject_guideline:
            guideline = getattr(self.pj_config, "translation_guideline", "") or ""
        else:
            guideline = ""
        guideline = (guideline or "").strip()
        if guideline:
            block = f"# 翻译规范（translation_guideline）\n{guideline}\n"
        else:
            block = ""
        prompt_req = prompt_req.replace("[translation_guideline]", block)
        prompt_req = prompt_req.replace("[Input]", input_src)
        prompt_req = prompt_req.replace("[Glossary]", gptdict)
        prompt_req = prompt_req.replace("[SourceLang]", self.source_lang)
        prompt_req = prompt_req.replace("[TargetLang]", self.target_lang)
        return prompt_req

    # 1. 准备输入
    def _build_script_text(self, json_list: list, filename: str = "") -> str:
        """把 json_list（每行一个 {name, message} 对象）拼成可读剧本正文。

        同时检查字段完整性：统计无 message/name 的条目数。
        """
        if not isinstance(json_list, list):
            LOGGER.warning(
                f"[FileMetaData] {filename} _build_script_text 收到非 list 参数"
            )
            return ""
        out: List[str] = []
        no_msg = 0
        no_name = 0
        for i, item in enumerate(json_list):
            if not isinstance(item, dict):
                continue
            name = item.get("name", "") or ""
            msg = item.get("message", "") or ""
            if not msg:
                no_msg += 1
            if not name:
                no_name += 1
            if name:
                out.append(f"{name}：{msg}")
            else:
                out.append(msg)

        # 字段完整性日志
        total = len(json_list)
        if no_msg > 0:
            if no_msg == total:
                LOGGER.warning(
                    f"[FileMetaData] {filename} 全部 {total} 个条目均无 message 字段，"
                    f"提示词将为空"
                )
                return ""
            LOGGER.warning(
                f"[FileMetaData] {filename} {no_msg}/{total} 个条目缺少 message 字段"
            )
        if no_name > 0 and no_name < total:
            LOGGER.debug(
                f"[FileMetaData] {filename} {no_name}/{total} 个条目无 name 字段（纯旁白行）"
            )
        return "\n".join(out)

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
            LOGGER.warning(f"[FileMetaData] 载入 GPT 字典失败，文件级元数据将不含专名译表：{e}")
            return ""

        lines = [
            "# Glossary",
            "| Src | Dst(/Dst2/..) | Note |",
            "| --- | --- | --- |",
        ]
        for dic in getattr(gpt_dic, "_dic_list", []):
            note = getattr(dic, "note", "") or ""
            lines.append(f"| {dic.search_word} | {dic.replace_word} | {note} |")
        LOGGER.debug(
            f"[FileMetaData] 已载入 GPT 字典，共 {len(lines) - 3} 条"
        )
        return "\n".join(lines)

    # 2. 解析与规整 LLM 返回的 JSON
    @staticmethod
    def _parse_meta(text: str, filename: str = "") -> Optional[dict]:
        if not text or not text.strip():
            if filename:
                LOGGER.debug(f"[FileMetaData] {filename} LLM 返回为空，跳过")
            return None
        if "</think>" in text:
            text = text.split("</think>")[-1]
        lang_list, code_list = extract_code_blocks(text)
        if code_list:
            text = code_list[0]
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            if filename:
                LOGGER.debug(
                    f"[FileMetaData] {filename} LLM 返回中未找到 JSON 对象，"
                    f"原文前 200 字：{text[:200]}"
                )
            return None
        try:
            return json.loads(text[start : end + 1])
        except Exception as e:
            if filename:
                LOGGER.debug(
                    f"[FileMetaData] {filename} JSON 解析失败：{e}，"
                    f"原文前 200 字：{text[:200]}"
                )
            return None

    @staticmethod
    def _normalize_meta(obj: dict, filename: str) -> dict:
        """规整字段类型，并强制 id == 文件名（与多轮后端按 id 匹配文件名一致）。"""
        roles = obj.get("角色", [])
        if isinstance(roles, str):
            roles = [roles]
        roles = [str(x).strip() for x in roles if str(x).strip()]

        tags = obj.get("标签", [])
        if isinstance(tags, str):
            tags = [tags]
        tags = [str(x).strip() for x in tags if str(x).strip()]

        return {
            "id": filename,
            "角色": roles,
            "服装": str(obj.get("服装", "") or ""),
            "剧情": str(obj.get("剧情", "") or ""),
            "标签": tags,
        }

    # 3. 合并写入 FileMetaData.json
    def _save_metadata(self, meta: dict, filename: str = "") -> None:
        from GalTransl import PASS1_CACHE_DIR
        out_dir = os.path.join(self.pj_config.getCachePath(), PASS1_CACHE_DIR)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "FileMetaData.json")
        with self._fm_lock:
            existing: List[dict] = []
            if os.path.exists(path) and os.path.getsize(path) > 0:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        existing = data
                    LOGGER.debug(
                        f"[FileMetaData] 读取已有 FileMetaData.json，"
                        f"共 {len(existing)} 条记录"
                    )
                except Exception as e:
                    LOGGER.warning(
                        f"[FileMetaData] 读取 FileMetaData.json 失败，"
                        f"将重置为仅包含当前文件：{e}"
                    )
                    existing = []
            else:
                LOGGER.debug(
                    f"[FileMetaData] 新建 FileMetaData.json"
                )
            replaced = False
            for i, e in enumerate(existing):
                if isinstance(e, dict) and e.get("id") == meta["id"]:
                    existing[i] = meta
                    replaced = True
                    LOGGER.debug(
                        f"[FileMetaData] {filename} 替换已有条目"
                    )
                    break
            if not replaced:
                existing.append(meta)
                LOGGER.debug(
                    f"[FileMetaData] {filename} 追加新条目，"
                    f"总条目数：{len(existing)}"
                )
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            LOGGER.debug(
                f"[FileMetaData] 已保存 {path}（{len(existing)} 条记录）"
            )

    # 4. 入口
    async def batch_translate(
        self,
        json_list: list,
        filename: str = "",
        gpt_dic: Optional[CGptDict] = None,
    ) -> bool:
        if not filename:
            LOGGER.warning("[FileMetaData] 未提供 filename，跳过该文件")
            return False

        # ── 入参校验 ──
        if not isinstance(json_list, list):
            LOGGER.error(
                f"[FileMetaData] {filename} json_list 类型错误，"
                f"期望 list，实际 {type(json_list).__name__}，跳过"
            )
            return False
        if not json_list:
            LOGGER.warning(f"[FileMetaData] {filename} json_list 为空，跳过")
            return False

        script_text = self._build_script_text(json_list, filename)
        if not script_text:
            LOGGER.warning(
                f"[FileMetaData] {filename} 剧本正文为空，跳过"
            )
            return False
        glossary_text = self._build_glossary_text()
        prompt = self._build_prompt_request(script_text, glossary_text)

        LOGGER.info(f"[FileMetaData] 正在为 {filename} 生成文件级元数据…")
        LOGGER.debug(
            f"[FileMetaData] {filename} 提示词长度：{len(prompt)} 字符，"
            f"脚本 {len(json_list)} 句"
        )
        try:
            # 不使用系统提示词：直接以 user 消息发送，不附带任何 system 角色
            messages = [{"role": "user", "content": prompt}]
            rsp, token = await self.ask_chatbot(
                messages=messages,
                file_name=filename,
                max_retry_count=3,
            )
        except Exception as e:
            LOGGER.error(f"[FileMetaData] {filename} LLM 请求失败：{e}")
            return False

        meta = self._parse_meta(rsp or "", filename)
        if not meta:
            LOGGER.warning(f"[FileMetaData] {filename} 未解析到有效 JSON，跳过")
            return False

        meta = self._normalize_meta(meta, filename)
        self._save_metadata(meta, filename)
        LOGGER.info(
            f"[FileMetaData] {filename} 已写入 "
            f"transl_cache/pass1_cache/FileMetaData.json "
            f"（角色={meta['角色']}，标签={meta['标签']}）"
        )
        return True


if __name__ == "__main__":
    pass
