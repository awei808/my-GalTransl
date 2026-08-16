import math
import os
from typing import Optional

from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProxyPool, CProjectConfig
from GalTransl import LOGGER
from GalTransl.Dictionary import CGptDict
from GalTransl.Backend.utils import (
    build_script_text,
    coerce_bool,
    extract_json_object,
)
from GalTransl.Backend.metadata import (
    build_glossary_prompt_text,
    save_metadata_json,
)
from GalTransl.Backend.BaseEngine import BaseEngine, register_engine
from GalTransl.Backend.Prompts import FORFILEMETA_PROMPT, FORFILEMETA_SYSTEM


"""
ForFileMetaData - 文件级元数据(FileMetaData)生成后端

该后端不翻译文本、不使用多轮对话、使用专用系统提示词。
它读取一个 Galgame 剧本文件（gt_input 下的 *.txt.json），把全文作为 user 消息
发给 LLM，要求模型概括剧情并输出一个 JSON 对象（角色/服装/剧情/标签），
解析后按文件名写入 transl_cache/pass1_cache/{filename}.meta.json（per-file 存储）。

默认会把项目的 translation_guideline（翻译规范，即 gpt.translation_guideline
指向的文件内容）注入提示词，供模型在命名角色/标签、把握剧情风格时遵循；
用户可在 config 的 internals.forfilemeta.inject_guideline 设为 false/0/no 关闭。

设计参考 GenDic：通过覆盖 batch_translate 走独立的"生成"流程，
完全绕开翻译模型的输入/输出契约（不写 gt_output）。
"""


@register_engine("ForFileMetaData")
class ForFileMetaData(BaseEngine):
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

        self.system_prompt = FORFILEMETA_SYSTEM
        self.trans_prompt = FORFILEMETA_PROMPT
        self._setup_prompts(eng_type, config)

        # 是否把项目翻译规范注入提示词（默认开启，可在 internals.forfilemeta.inject_guideline 关闭）
        raw = self.pj_config.getKey("internals.forfilemeta.inject_guideline", True)
        self._inject_guideline = coerce_bool(raw, default=True)

        # 惰性载入的全局提示词（GlobalPrompt）
        self._global_prompt: Optional[dict] = None
        self._global_prompt_loaded: bool = False

    # 0.1 全局提示词上下文（实现见 GalTransl.Backend.context）
    def _ensure_global_prompt_loaded(self) -> None:
        from GalTransl.Backend.context import ensure_global_prompt_loaded
        ensure_global_prompt_loaded(self, "FileMetaData")

    def _build_global_prompt_block(self) -> str:
        from GalTransl.Backend.context import format_global_prompt_only
        return format_global_prompt_only(self, "FileMetaData")

    # 0. 可控注入翻译规范
    def _build_prompt_request(self, input_src: str, gptdict: str, max_chars: int = 200) -> str:
        """
        在基类占位符替换的基础上，增加 translation_guideline 的可控注入：

        - 默认（_inject_guideline=True）把项目翻译规范整段注入提示词；
        - 关闭（config 设 internals.forfilemeta.inject_guideline=false）或
          规范为空时，占位段被替换为空，不会留下悬挂的标题。

        其余占位符（[Input]/[Glossary]/[plot_metadata]/[batch_metadata]/
        [global_prompt]/[SourceLang]/[TargetLang]）由基类统一替换。

        Args:
            max_chars: 剧情概括字数上限，按公式 clamp(20 + 63·max(0, log₂(句数/15)), 20, 400) 自动计算
        """
        prompt_req = super()._build_prompt_request(
            input_src,
            gptdict,
            translation_guideline=self._build_guideline_block(),
            global_prompt=self._build_global_prompt_block(),
        )
        prompt_req = prompt_req.replace("[max_chars]", str(max_chars))
        return prompt_req

    # 1. 准备输入
    def _build_script_text(self, json_list: list, filename: str = "") -> str:
        """把 json_list（每行一个 {name, message} 对象）拼成可读剧本正文。

        实现收口于 utils.build_script_text（与 ForBatchMetaData 共用），
        本方法保持「无行号/不压平/仅 name」的文件级元数据口径并返回正文串。
        """
        script_text, _max_index = build_script_text(
            json_list, filename, tag="FileMetaData"
        )
        return script_text

    def _build_glossary_text(self, json_list: list) -> str:
        """按需注入 GPT 字典：仅将当前文件中实际出现的条目格式化为 Markdown 译表。

        实现收口于 metadata.build_glossary_prompt_text（与 ForBatchMetaData 共用）。
        """
        return build_glossary_prompt_text(json_list, self.pj_config, "FileMetaData")

    # 2. 解析与规整 LLM 返回的 JSON
    @staticmethod
    def _parse_meta(text: str, filename: str = "") -> Optional[dict]:
        return extract_json_object(text, tag="FileMetaData", filename=filename)

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

    # 3. 写入 per-file 元数据文件（实现收口于 metadata.save_metadata_json，原子写）
    def _save_metadata(self, meta: dict, filename: str = "") -> None:
        from GalTransl import PASS1_CACHE_DIR

        save_metadata_json(
            self.pj_config, PASS1_CACHE_DIR, filename, "meta", meta, "FileMetaData"
        )

    # 4. 入口
    async def batch_translate(
        self,
        json_list: list,
        filename: str = "",
        gpt_dic: Optional[CGptDict] = None,
        force_regen: bool = False,
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

        # 缓存命中：pass1_cache 中已有该文件的元数据则跳过 LLM 调用；
        # force_regen=True 时忽略已有缓存强制重新生成（与流水线 forceRegenFileMeta 对齐）
        from GalTransl import PASS1_CACHE_DIR
        cache_path = os.path.join(
            self.pj_config.getCachePath(), PASS1_CACHE_DIR, f"{filename}.meta.json"
        )
        cache_exists = os.path.isfile(cache_path)
        if cache_exists and not force_regen:
            LOGGER.debug(f"[FileMetaData] 缓存命中，跳过 LLM 调用: {filename}")
            return True
        if cache_exists and force_regen:
            LOGGER.info(f"[FileMetaData] forceRegen 开启，忽略已有缓存重新生成: {filename}")

        script_text = self._build_script_text(json_list, filename)
        if not script_text:
            LOGGER.warning(
                f"[FileMetaData] {filename} 剧本正文为空，跳过"
            )
            return False
        glossary_text = self._build_glossary_text(json_list)

        # 根据文件句数动态计算剧情概括字数上限：连续对数映射，公式 clamp(20 + 63·max(0, log₂(句数/15)), 20, 400)
        _num_lines = len(json_list)
        max_chars = max(
            20, min(400, round(20 + 63 * max(0, math.log2(_num_lines / 15))))
        )
        prompt = self._build_prompt_request(script_text, glossary_text, max_chars=max_chars)

        LOGGER.info(f"[FileMetaData] 正在为 {filename} 生成文件级元数据…")
        LOGGER.debug(
            f"[FileMetaData] {filename} 提示词长度：{len(prompt)} 字符，"
            f"脚本 {_num_lines} 句，剧情上限 {max_chars} 字"
        )
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt},
        ]
        rsp, token = await self._call_llm_with_error_report(
            messages, filename, max_retry_count=3, tag="FileMetaData"
        )
        if rsp is None:
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
