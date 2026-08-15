import json
import os
from typing import Optional, List

from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProxyPool, initDictList, CProjectConfig
from GalTransl import LOGGER
from GalTransl.CSentense import CSentense
from GalTransl.Dictionary import CGptDict
from GalTransl.Utils import extract_code_blocks
from GalTransl.Backend.BaseEngine import BaseEngine
from GalTransl.Backend.Prompts import FORBATCHMETA_PROMPT, FORBATCHMETA_SYSTEM
from GalTransl.server_runtime import record_runtime_notice
from GalTransl.Backend.ForGalJsonMulitChat import (
    load_file_metadata_map,
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

解析后按文件名 id 合并写入 gt_input/BatchMetadata.json。
第三次启动翻译后端（ForGal-json-multi-chat）时，会按每批句子所处的全局行号
区间，将对应区间的批次级元数据注入首轮提示词（[batch_metadata] 占位符）。

设计与 ForFileMetaData 一致：通过覆盖 batch_translate 走独立的"生成"流程，
完全绕开翻译模型的输入/输出契约（不写 gt_output）。
"""


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
        额外地，会在首次需要时惰性载入 gt_input（及上层目录）的
        FileMetaData.json，作为每个文件划分区间时的文件级背景。
        """
        super().__init__(config, eng_type, proxy_pool, token_pool)

        self.system_prompt = FORBATCHMETA_SYSTEM
        self.trans_prompt = FORBATCHMETA_PROMPT
        self._setup_prompts(eng_type, config)

        # 是否把项目翻译规范注入提示词（默认开启，可在 internals.forbatchmeta.inject_guideline 关闭）
        raw = self.pj_config.getKey("internals.forbatchmeta.inject_guideline", True)
        if isinstance(raw, bool):
            self._inject_guideline = raw
        else:
            self._inject_guideline = (
                str(raw).strip().lower() not in ("false", "0", "no", "")
            )

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

    # 0.0 全局提示词上下文
    def _ensure_global_prompt_loaded(self) -> None:
        """惰性载入 GlobalPrompt.json（仅执行一次）。"""
        if self._global_prompt_loaded:
            return
        self._global_prompt_loaded = True
        explicit = getattr(self.pj_config, "global_prompt", None)
        if isinstance(explicit, dict):
            self._global_prompt = explicit
            LOGGER.debug("[BatchMetaData] 使用已注入的 GlobalPrompt（来自流水线）")
            return
        try:
            from GalTransl.Backend.ForGlobalPrompt import load_global_prompt
            self._global_prompt = load_global_prompt(self.pj_config)
            if self._global_prompt:
                LOGGER.debug("[BatchMetaData] 已从 pass0_cache 载入 GlobalPrompt 上下文")
        except Exception as e:
            LOGGER.debug(f"[BatchMetaData] 载入 GlobalPrompt 失败：{e}")
            self._global_prompt = None

    def _build_global_prompt_block(self, filename: str = "") -> str:
        """格式化 GlobalPrompt 为提示词附加段落。

        有路线图归属时注入「路线剧情 + 带标注的全量 GlobalPrompt」；无归属或
        没有路线图时仅注入全量 GlobalPrompt。标注说明全局剧情为游戏整体剧情、
        可能与当前文件不完全对应，以路线剧情和文件元数据为准。
        """
        route_block = self._format_route_context_for_file(filename)
        self._ensure_global_prompt_loaded()
        if not self._global_prompt:
            return route_block
        from GalTransl.Backend.ForGlobalPrompt import _format_global_prompt_as_context
        gp_block = _format_global_prompt_as_context(
            self._global_prompt, annotate_plot=bool(route_block)
        )
        if route_block and gp_block:
            LOGGER.debug(
                f"[BatchMetaData] {filename} 注入路线剧情 + 带标注的全局提示词"
            )
            return f"{route_block}\n{gp_block}"
        return route_block or gp_block

    def _format_route_context_for_file(self, filename: str) -> str:
        """按当前文件所属路线返回剧情上下文块；无归属/无路线图时返回空串。"""
        from GalTransl.Backend.ForPlotRouteMap import (
            _format_route_context,
            load_plot_route_map,
        )

        try:
            plot_route_map = load_plot_route_map(self.pj_config)
            if not plot_route_map:
                return ""
            base = strip_chunk_suffix(filename)
            ctx = _format_route_context(plot_route_map, base)
            if ctx:
                LOGGER.debug(f"[BatchMetaData] {filename} 注入路线剧情（{base}）")
            return ctx
        except Exception as e:
            LOGGER.warning(
                f"[BatchMetaData] 按路线注入剧情失败：{e}"
            )
            return ""

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
        """取该文件的文件级剧情元数据，格式化为提示词背景块。

        找不到对应条目（未生成 FileMetaData.json 或缺少该文件）时返回空串，
        对应模板中的 [plot_metadata] 会被替换为空。
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

        def _join(value) -> str:
            if value is None or value == "":
                return "无"
            if isinstance(value, list):
                items = [str(x).strip() for x in value if str(x).strip() != ""]
                return "、".join(items) if items else "无"
            s = str(value).strip()
            return s if s else "无"

        return (
            f"角色: {_join(md.character)}\n"
            f"服装: {_join(md.costume)}\n"
            f"剧情: {_join(md.plot)}\n"
            f"标签: {_join(md.tags)}\n"
        )

    # 1. 组装提示词
    def _build_prompt_request(
        self, input_src: str, gptdict: str, file_metadata: str = "", filename: str = ""
    ) -> str:
        """在基类占位符替换基础上，增加 translation_guideline 的可控注入和最大批次限制。"""
        prompt_req = self.trans_prompt
        if self._inject_guideline:
            guideline = getattr(self.pj_config, "translation_guideline", "") or ""
        else:
            guideline = ""
        guideline = (guideline or "").strip()
        if guideline:
            block = f"# 翻译规范\n{guideline}\n"
        else:
            block = ""
        prompt_req = prompt_req.replace("[translation_guideline]", block)
        prompt_req = prompt_req.replace("[global_prompt]", self._build_global_prompt_block(filename))
        prompt_req = prompt_req.replace("[Input]", input_src)
        prompt_req = prompt_req.replace("[Glossary]", gptdict)
        prompt_req = prompt_req.replace("[plot_metadata]", file_metadata)
        prompt_req = prompt_req.replace("[SourceLang]", self.source_lang)
        prompt_req = prompt_req.replace("[TargetLang]", self.target_lang)
        prompt_req = prompt_req.replace("[max_batches]", str(self.max_batches))
        prompt_req = prompt_req.replace("[min_batch_size]", str(self.min_batch_size))
        prompt_req = prompt_req.replace("[max_batch_size]", str(self.max_batch_size))
        return prompt_req

    # 2. 准备输入
    def _build_script_text(self, json_list: list, filename: str = "") -> tuple:
        """把 json_list 拼成带全局行号的可读剧本正文。

        行号规则与 Loader.load_transList / CSplitter 中 runtime_index 一致：
        优先取行内显式 index，否则用 1 起的位置序号（i+1）。这样生成的区间
        行号能与翻译阶段每个句子的 runtime_index 精确对应。

        同时检查字段完整性：统计无 message/name 的条目数。

        Returns:
            (script_text, max_index)：拼接后的正文，以及最大行号（供裁剪区间用）
        """
        if not isinstance(json_list, list):
            LOGGER.warning(
                f"[BatchMetaData] {filename} _build_script_text 收到非 list 参数"
            )
            return "", 0
        out: List[str] = []
        max_index = 0
        no_msg = 0
        for i, item in enumerate(json_list):
            if not isinstance(item, dict):
                continue
            raw_idx = item.get("index")
            if isinstance(raw_idx, int):
                idx = raw_idx
            elif isinstance(raw_idx, str) and raw_idx.isdigit():
                idx = int(raw_idx)
            else:
                idx = i + 1
            max_index = max(max_index, idx)
            name = item.get("name", item.get("names", "")) or ""
            msg = item.get("message", "") or ""
            if not msg:
                no_msg += 1
            # 压平换行/制表符，避免破坏逐行结构
            msg = str(msg).replace("\r\n", " ").replace("\n", " ").replace("\t", " ")
            if name:
                out.append(f"[{idx}] {name}：{msg}")
            else:
                out.append(f"[{idx}] {msg}")

        # 字段完整性日志
        total = len(json_list)
        if no_msg > 0:
            if no_msg == total:
                LOGGER.warning(
                    f"[BatchMetaData] {filename} 全部 {total} 个条目均无 message 字段，"
                    f"提示词将为空"
                )
                return "", max_index
            LOGGER.warning(
                f"[BatchMetaData] {filename} {no_msg}/{total} 个条目缺少 message 字段"
            )
        return "\n".join(out), max_index

    def _build_glossary_text(self, json_list: list) -> str:
        """按需注入 GPT 字典：仅将当前文件中实际出现的条目格式化为 Markdown 译表。

        与多轮翻译后端策略一致：先用 json_list 构造 CTransList，再通过
        CGptDict.gen_prompt 检测哪些字典条目实际出现在原文中，只注入命中条目。
        """
        if not json_list:
            return ""
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
            LOGGER.warning(f"[BatchMetaData] 载入 GPT 字典失败，批次元数据将不含专名译表：{e}")
            return ""

        # 从 json_list 构造临时 CTransList
        trans_list = []
        for item in json_list:
            if not isinstance(item, dict):
                continue
            msg = str(item.get("message", ""))
            if not msg:
                continue
            tran = CSentense(msg, speaker=str(item.get("name", "") or ""))
            tran.post_src = tran.pre_src
            trans_list.append(tran)

        # 元数据阶段不分流，仅注入非 h 字典，避免 h 词条污染整体剧情描述
        glossary = gpt_dic.gen_prompt(trans_list, scene="nh") if trans_list else ""
        if glossary:
            LOGGER.debug(
                f"[BatchMetaData] 按需注入 GPT 字典，命中 {glossary.count(chr(10)) - 3} 条"
            )
        else:
            LOGGER.debug("[BatchMetaData] 当前文件无命中 GPT 字典条目")
        return glossary

    # 3. 解析与规整 LLM 返回的 JSON
    @staticmethod
    def _parse_meta(text: str, filename: str = "") -> Optional[dict]:
        if not text or not text.strip():
            if filename:
                LOGGER.debug(f"[BatchMetaData] {filename} LLM 返回为空，跳过")
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
                    f"[BatchMetaData] {filename} LLM 返回中未找到 JSON 对象，"
                    f"原文前 200 字：{text[:200]}"
                )
            return None
        try:
            return json.loads(text[start : end + 1])
        except Exception as e:
            if filename:
                LOGGER.debug(
                    f"[BatchMetaData] {filename} JSON 解析失败：{e}，"
                    f"原文前 200 字：{text[:200]}"
                )
            return None

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

    # 4. 写入 per-file 批次元数据（无锁、不合并，每文件独立存储）
    def _save_metadata(self, meta: dict, filename: str = "") -> None:
        from GalTransl import PASS2_CACHE_DIR
        out_dir = os.path.join(self.pj_config.getCachePath(), PASS2_CACHE_DIR)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{filename}.batch.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        LOGGER.debug(f"[BatchMetaData] 已保存 {path}")

    # 5. 入口
    async def batch_translate(
        self,
        json_list: list,
        filename: str = "",
        gpt_dic: Optional[CGptDict] = None,
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

        # 缓存命中：pass2_cache 中已有该文件的批次元数据则跳过 LLM 调用
        from GalTransl import PASS2_CACHE_DIR
        cache_path = os.path.join(
            self.pj_config.getCachePath(), PASS2_CACHE_DIR, f"{filename}.batch.json"
        )
        if os.path.isfile(cache_path):
            LOGGER.debug(f"[BatchMetaData] 缓存命中，跳过 LLM 调用: {filename}")
            return True

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
        try:
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ]
            rsp, token = await self.ask_chatbot(
                messages=messages,
                file_name=filename,
                max_retry_count=3,
            )
        except Exception as e:
            LOGGER.error(f"[BatchMetaData] {filename} LLM 请求失败：{type(e).__name__}: {e}", exc_info=True)
            try:
                from GalTransl.server import record_runtime_error

                record_runtime_error(
                    getattr(
                        self.pj_config,
                        "runtime_project_dir",
                        self.pj_config.getProjectDir(),
                    ),
                    kind="llm",
                    message=f"{type(e).__name__}: {e}",
                    filename=filename,
                    index_range="-",
                    model=self.get_last_chatbot_model(),
                    level="error",
                )
            except Exception:
                pass
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
                self.pj_config.getProjectDir(),
                f"[批次划分] {filename} 共 {max_index} 行，超过最大可自然划分范围 "
                f"{self.max_natural_lines} 行（max_batch_size={self.max_batch_size} × "
                f"max_batches={self.max_batches} × 0.9），划分的批次可能过大",
            )
        if any(b.get("区间过大") for b in meta["批次"]):
            record_runtime_notice(
                self.pj_config.getProjectDir(),
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
