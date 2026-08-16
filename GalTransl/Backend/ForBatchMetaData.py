import os
from typing import Optional

from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProxyPool, CProjectConfig
from GalTransl import LOGGER
from GalTransl.Backend.BaseEngine import BaseEngine, register_engine
from GalTransl.Backend.Prompts import FORBATCHMETA_PROMPT, FORBATCHMETA_SYSTEM
from GalTransl.server_runtime import record_runtime_notice
from GalTransl.Backend.metadata import (
    build_glossary_prompt_text,
    format_file_metadata_block,
    load_file_metadata_map,
    save_metadata_json,
)
from GalTransl.Backend.utils import (
    build_script_text,
    coerce_bool,
    extract_json_object,
    normalize_batch_intervals,
    strip_chunk_suffix,
)


"""
ForBatchMetaData - 批次级元数据(BatchMetadata)生成后端（高质量翻译流程 第二步）

该后端不翻译文本、不使用多轮对话、不使用系统提示词。
它读取一个 Galgame 剧本文件（gt_input 下的 *.txt.json）的**全文**，为每行标注
全局行号后发给 LLM，同时把该文件的**文件级剧情元数据(FileMetaData)** 作为背景
注入提示词，要求模型依据剧情把全文划分为若干**连续、不重叠、完整覆盖全文**的
「翻译区间(批次)」，并为每个区间标注：

  - 区间：[起始行号, 结束行号]（闭区间，行号为文件内全局位置，从 1 起）
  - 视角：本区间叙述/独白的主视角角色
  - 氛围：本区间情绪基调
  - h：是否为露骨性描写(H)
  - 用词色彩：对本区间译文用词风格的具体指导

解析后按文件名写入 transl_cache/pass2_cache/{filename}.batch.json（per-file 存储）。
第三次启动翻译后端（ForGal-json-multi-chat）时，会按每批句子所处的全局行号
区间，将对应区间的批次级元数据注入首轮提示词（[batch_metadata] 占位符）。

设计与 ForFileMetaData 一致：通过覆盖 batch_translate 走独立的"生成"流程，
完全绕开翻译模型的输入/输出契约（不写 gt_output）。
"""


@register_engine("ForBatchMetaData")
class ForBatchMetaData(BaseEngine):
    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool],
        token_pool: COpenAITokenPool,
    ) -> None:
        """
        初始化 ForBatchMetaData 后端。

        与 ForFileMetaData 类似：使用专用系统提示词、不翻译、不多轮。
        额外地，会在首次需要时惰性载入 pass1_cache 的
        文件级元数据（FileMetaData），作为每个文件划分区间时的文件级背景。
        """
        super().__init__(config, eng_type, proxy_pool, token_pool)

        self.system_prompt = FORBATCHMETA_SYSTEM
        self.trans_prompt = FORBATCHMETA_PROMPT
        self._setup_prompts(eng_type, config)

        # 是否把项目翻译规范注入提示词（默认开启，可在 internals.forbatchmeta.inject_guideline 关闭）
        raw = self.pj_config.getKey("internals.forbatchmeta.inject_guideline", True)
        self._inject_guideline = coerce_bool(raw, default=True)

        # 最大批次数限制，默认 20；可在 config 的 internals.forbatchmeta.max_batches 设置
        self.max_batches = self.pj_config.getKey("internals.forbatchmeta.max_batches", 20)
        try:
            self.max_batches = max(1, int(self.max_batches))
        except (TypeError, ValueError):
            self.max_batches = 20

        # 单批区间长度约束，默认 min=8/max=64；可在 internals.forbatchmeta 设置。
        # 超过 max_batch_size 的区间会被硬切，小于 min_batch_size 的区间会尽量合并
        self.min_batch_size = self.pj_config.getKey("internals.forbatchmeta.min_batch_size", 8)
        try:
            self.min_batch_size = max(1, int(self.min_batch_size))
        except (TypeError, ValueError):
            self.min_batch_size = 8
        self.max_batch_size = self.pj_config.getKey("internals.forbatchmeta.max_batch_size", 64)
        try:
            self.max_batch_size = max(1, int(self.max_batch_size))
        except (TypeError, ValueError):
            self.max_batch_size = 64
        if self.min_batch_size > self.max_batch_size:
            LOGGER.warning(
                f"[BatchMetaData] min_batch_size({self.min_batch_size}) 大于 "
                f"max_batch_size({self.max_batch_size})，已交换两者"
            )
            self.min_batch_size, self.max_batch_size = self.max_batch_size, self.min_batch_size
        # 最大可自然划分文件行数：0.9 * 最大区间长度 * 最大批次数，超出则提示用户确认
        self.max_natural_lines = int(0.9 * self.max_batch_size * self.max_batches)
        LOGGER.debug(
            f"[BatchMetaData] 批次长度约束：min={self.min_batch_size}, "
            f"max={self.max_batch_size}，最大批次数：{self.max_batches}，"
            f"最大可自然划分行数：{self.max_natural_lines}"
        )

        # 文件级剧情元数据映射（{文件名: FileMetaData}），惰性载入一次
        self._file_metadata_by_file: dict = {}
        self._file_metadata_loaded: bool = False

        # FileMetaData 惰性载入已完成，不再需要跨文件合并锁

        # 惰性载入的全局提示词（GlobalPrompt）
        self._global_prompt: Optional[dict] = None
        self._global_prompt_loaded: bool = False

    # 0.0 全局提示词上下文（实现见 GalTransl.Backend.context）
    def _ensure_global_prompt_loaded(self) -> None:
        from GalTransl.Backend.context import ensure_global_prompt_loaded
        ensure_global_prompt_loaded(self, "BatchMetaData")

    def _build_global_prompt_block(self, filename: str = "") -> str:
        from GalTransl.Backend.context import format_global_prompt_with_route_direct
        return format_global_prompt_with_route_direct(self, "BatchMetaData", filename)

    def _format_route_context_for_file(self, filename: str) -> str:
        from GalTransl.Backend.context import format_route_context_for_file_direct
        return format_route_context_for_file_direct(self, "BatchMetaData", filename)

    # 0. 文件级剧情元数据载入与格式化
    def _ensure_file_metadata_loaded(self) -> None:
        """惰性载入 FileMetaData.json（仅执行一次）。"""
        if self._file_metadata_loaded:
            return
        self._file_metadata_loaded = True
        try:
            self._file_metadata_by_file = load_file_metadata_map(self.pj_config)
            LOGGER.info(
                f"[BatchMetaData] 已载入 FileMetaData.json，"
                f"共 {len(self._file_metadata_by_file)} 个文件有元数据"
            )
        except Exception as e:
            LOGGER.warning(f"[BatchMetaData] 载入 FileMetaData.json 失败，批次元数据将不含文件级背景：{e}")
            self._file_metadata_by_file = {}

    def _build_file_metadata_block(self, filename: str) -> str:
        """取该文件的文件级剧情元数据，格式化为提示词背景块（<plot_metadata> 包裹版）。

        与翻译轮共用 metadata.format_file_metadata_block 的形态（不含翻译轮专属
        指导语）；找不到对应条目（未生成 FileMetaData.json 或缺少该文件）时返回
        空串，对应模板中的 [plot_metadata] 会被替换为空。
        """
        self._ensure_file_metadata_loaded()
        md = self._file_metadata_by_file.get(filename)
        if md is None:
            # 翻译阶段文件可能被切成 file_0 分片，剥离后缀再匹配一次
            md = self._file_metadata_by_file.get(strip_chunk_suffix(filename))
        if md is None:
            LOGGER.debug(
                f"[BatchMetaData] {filename} 在 FileMetaData.json 中无对应条目，"
                f"该文件将不含文件级剧情背景"
            )
            return ""
        return format_file_metadata_block(md, include_guidance=False)

    # 1. 组装提示词
    def _build_prompt_request(
        self, input_src: str, gptdict: str, file_metadata: str = "", filename: str = ""
    ) -> str:
        """在基类占位符替换基础上，增加 translation_guideline 的可控注入和最大批次限制。

        公共占位符（[Input]/[Glossary]/[plot_metadata]/[batch_metadata]/
        [global_prompt]/[SourceLang]/[TargetLang]）由基类统一替换。
        """
        prompt_req = super()._build_prompt_request(
            input_src,
            gptdict,
            plot_metadata=file_metadata,
            translation_guideline=self._build_guideline_block(),
            global_prompt=self._build_global_prompt_block(filename),
        )
        prompt_req = prompt_req.replace("[max_batches]", str(self.max_batches))
        prompt_req = prompt_req.replace("[min_batch_size]", str(self.min_batch_size))
        prompt_req = prompt_req.replace("[max_batch_size]", str(self.max_batch_size))
        return prompt_req

    # 2. 准备输入
    def _build_script_text(self, json_list: list, filename: str = "") -> tuple:
        """把 json_list 拼成带全局行号的可读剧本正文。

        实现收口于 utils.build_script_text（与 ForFileMetaData 共用），
        本方法保持批次划分口径：带 [行号] 前缀、压平换行/制表符、兼容 names 字段。

        行号规则与 Loader.load_transList / CSplitter 中 runtime_index 一致：
        优先取行内显式 index，否则用 1 起的位置序号（i+1）。这样生成的区间
        行号能与翻译阶段每个句子的 runtime_index 精确对应。

        Returns:
            (script_text, max_index)：拼接后的正文，以及最大行号（供裁剪区间用）
        """
        return build_script_text(
            json_list,
            filename,
            tag="BatchMetaData",
            use_line_numbers=True,
            flatten_whitespace=True,
            accept_names_plural=True,
        )

    def _build_glossary_text(self, json_list: list) -> str:
        """按需注入 GPT 字典：仅将当前文件中实际出现的条目格式化为 Markdown 译表。

        实现收口于 metadata.build_glossary_prompt_text（与 ForFileMetaData 共用）。
        """
        return build_glossary_prompt_text(json_list, self.pj_config, "BatchMetaData")

    # 3. 解析与规整 LLM 返回的 JSON
    @staticmethod
    def _parse_meta(text: str, filename: str = "") -> Optional[dict]:
        return extract_json_object(text, tag="BatchMetaData", filename=filename)

    @staticmethod
    def _normalize_meta(obj: dict, filename: str, max_index: int,
                        max_batches: int = 20,
                        min_batch_size: Optional[int] = None,
                        max_batch_size: Optional[int] = None) -> dict:
        """规整批次数组：复用共享 normalize_batch_intervals 做字段清洗、裁剪、
        重叠修复、长度约束、最大批次数限制与间隙检测，仅强制 id == 文件名。"""
        raw_batches = obj.get("批次", obj.get("batches", []))
        cleaned = normalize_batch_intervals(
            raw_batches, filename, max_index, max_batches, tag="BatchMetaData",
            min_batch_size=min_batch_size, max_batch_size=max_batch_size,
        )
        return {"id": filename, "批次": cleaned}

    # 4. 写入 per-file 批次元数据（实现收口于 metadata.save_metadata_json，原子写）
    def _save_metadata(self, meta: dict, filename: str = "") -> None:
        from GalTransl import PASS2_CACHE_DIR

        save_metadata_json(
            self.pj_config, PASS2_CACHE_DIR, filename, "batch", meta, "BatchMetaData"
        )

    # 5. 入口
    async def batch_translate(
        self,
        json_list: list,
        filename: str = "",
        force_regen: bool = False,
    ) -> bool:
        if not filename:
            LOGGER.warning("[BatchMetaData] 未提供 filename，跳过该文件")
            return False

        # ── 入参校验 ──
        if not isinstance(json_list, list):
            LOGGER.error(
                f"[BatchMetaData] {filename} json_list 类型错误，"
                f"期望 list，实际 {type(json_list).__name__}，跳过"
            )
            return False
        if not json_list:
            LOGGER.warning(f"[BatchMetaData] {filename} json_list 为空，跳过")
            return False

        # 缓存命中：pass2_cache 中已有该文件的批次元数据则跳过 LLM 调用；
        # force_regen=True 时忽略已有缓存强制重新生成（与流水线 forceRegenBatchMeta 对齐）
        from GalTransl import PASS2_CACHE_DIR
        cache_path = os.path.join(
            self.pj_config.getCachePath(), PASS2_CACHE_DIR, f"{filename}.batch.json"
        )
        cache_exists = os.path.isfile(cache_path)
        if cache_exists and not force_regen:
            LOGGER.debug(f"[BatchMetaData] 缓存命中，跳过 LLM 调用: {filename}")
            return True
        if cache_exists and force_regen:
            LOGGER.info(f"[BatchMetaData] forceRegen 开启，忽略已有缓存重新生成: {filename}")

        script_text, max_index = self._build_script_text(json_list, filename)
        if not script_text:
            LOGGER.warning(
                f"[BatchMetaData] {filename} 剧本正文为空，跳过"
            )
            return False
        glossary_text = self._build_glossary_text(json_list)
        file_meta_block = self._build_file_metadata_block(filename)
        prompt = self._build_prompt_request(
            script_text, glossary_text, file_metadata=file_meta_block, filename=filename
        )

        LOGGER.info(f"[BatchMetaData] 正在为 {filename} 划分翻译区间…")
        LOGGER.debug(
            f"[BatchMetaData] {filename} 提示词长度：{len(prompt)} 字符，"
            f"脚本 {len(json_list)} 句，最大行号 {max_index}"
        )
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt},
        ]
        rsp, token = await self._call_llm_with_error_report(
            messages, filename, max_retry_count=3, tag="BatchMetaData"
        )
        if rsp is None:
            return False

        meta = self._parse_meta(rsp or "", filename)
        if not meta:
            LOGGER.warning(f"[BatchMetaData] {filename} 未解析到有效 JSON，跳过")
            return False

        meta = self._normalize_meta(
            meta, filename, max_index, self.max_batches,
            min_batch_size=self.min_batch_size, max_batch_size=self.max_batch_size,
        )
        if not meta["批次"]:
            LOGGER.warning(f"[BatchMetaData] {filename} 未解析到有效区间，跳过")
            return False
        # 超限提示：文件行数超过最大可自然划分范围，或存在「区间过大」的批次时给出一次性提示（前端 toast）
        if max_index > self.max_natural_lines:
            record_runtime_notice(
                self.runtime_project_dir,
                f"[批次划分] {filename} 共 {max_index} 行，超过最大可自然划分范围 "
                f"{self.max_natural_lines} 行（max_batch_size={self.max_batch_size} × "
                f"max_batches={self.max_batches} × 0.9），划分的批次可能过大",
            )
        if any(b.get("区间过大") for b in meta["批次"]):
            record_runtime_notice(
                self.runtime_project_dir,
                f"[批次划分] {filename} 存在超过 {self.max_batch_size} 行的区间，"
                f"已标注「区间过大」，翻译时该批次将整体发送",
            )
        self._save_metadata(meta, filename)
        LOGGER.info(
            f"[BatchMetaData] {filename} 已写入 "
            f"transl_cache/pass2_cache/BatchMetadata.json "
            f"（共 {len(meta['批次'])} 个区间）"
        )
        return True


if __name__ == "__main__":
    pass
