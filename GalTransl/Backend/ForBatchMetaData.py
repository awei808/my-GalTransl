import json
import os
from typing import Optional, List
from threading import Lock

from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProxyPool, initDictList, CProjectConfig
from GalTransl import LOGGER
from GalTransl.Dictionary import CGptDict
from GalTransl.Utils import extract_code_blocks
from GalTransl.Backend.BaseTranslate import BaseTranslate
from GalTransl.Backend.Prompts import FORBATCHMETA_PROMPT
from GalTransl.Backend.ForGalJsonMulitChat import load_file_metadata_map


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


class ForBatchMetaData(BaseTranslate):
    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool],
        token_pool: COpenAITokenPool,
    ) -> None:
        """
        初始化 ForBatchMetaData 后端。

        与 ForFileMetaData 类似：不使用系统提示词、不翻译、不多轮。
        额外地，会在首次需要时惰性载入 gt_input（及上层目录）的
        FileMetaData.json，作为每个文件划分区间时的文件级背景。
        """
        super().__init__(config, eng_type, proxy_pool, token_pool)

        # 不使用系统提示词：system 内容为空，且实际请求只发 user 消息
        self.system_prompt = ""
        self.trans_prompt = FORBATCHMETA_PROMPT
        self._apply_internal_prompt_template_overrides()
        self.init_chatbot(eng_type, config)

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
        LOGGER.debug(
            f"[BatchMetaData] 最大批次数限制：{self.max_batches}"
        )

        # 文件级剧情元数据映射（{文件名: FileMetaData}），惰性载入一次
        self._file_metadata_by_file: dict = {}
        self._file_metadata_loaded: bool = False

        # 跨文件（可能的并发 worker）写 BatchMetadata.json 时的互斥锁
        self._bm_lock = Lock()

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

    def _build_global_prompt_block(self) -> str:
        """格式化 GlobalPrompt 为提示词附加段落。"""
        self._ensure_global_prompt_loaded()
        if not self._global_prompt:
            return ""
        from GalTransl.Backend.ForGlobalPrompt import _format_global_prompt_as_context
        return _format_global_prompt_as_context(self._global_prompt)

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
        self, input_src: str, gptdict: str, file_metadata: str = ""
    ) -> str:
        """在基类占位符替换基础上，增加 translation_guideline 的可控注入和最大批次限制。"""
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
        prompt_req = prompt_req.replace("[global_prompt]", self._build_global_prompt_block())
        prompt_req = prompt_req.replace("[Input]", input_src)
        prompt_req = prompt_req.replace("[Glossary]", gptdict)
        prompt_req = prompt_req.replace("[plot_metadata]", file_metadata)
        prompt_req = prompt_req.replace("[SourceLang]", self.source_lang)
        prompt_req = prompt_req.replace("[TargetLang]", self.target_lang)
        prompt_req = prompt_req.replace("[max_batches]", str(self.max_batches))
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
            LOGGER.warning(f"[BatchMetaData] 载入 GPT 字典失败，批次元数据将不含专名译表：{e}")
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
            f"[BatchMetaData] 已载入 GPT 字典，共 {len(lines) - 3} 条"
        )
        return "\n".join(lines)

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
                        max_batches: int = 20) -> dict:
        """规整批次数组：清洗字段类型、裁剪并排序区间，强制 id == 文件名。"""

        def _to_bool(v) -> bool:
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return v != 0
            if isinstance(v, str):
                return v.strip().lower() in ("true", "1", "yes", "是", "y")
            return False

        raw_batches = obj.get("批次", obj.get("batches", []))
        if not isinstance(raw_batches, list):
            raw_batches = []

        batches: List[dict] = []
        for b in raw_batches:
            if not isinstance(b, dict):
                continue
            interval = b.get("区间", b.get("interval", None))
            if not isinstance(interval, (list, tuple)) or len(interval) < 2:
                continue
            try:
                lo = int(interval[0])
                hi = int(interval[1])
            except (TypeError, ValueError):
                continue
            if lo > hi:
                lo, hi = hi, lo
            # 裁剪到 [1, max_index]
            lo = max(1, lo)
            if max_index > 0:
                hi = min(hi, max_index)
            if hi < lo:
                continue
            batches.append(
                {
                    "区间": [lo, hi],
                    "视角": str(b.get("视角", b.get("perspective", "")) or ""),
                    "氛围": str(b.get("氛围", b.get("atmosphere", "")) or ""),
                    "h": _to_bool(b.get("h", b.get("H", False))),
                    "用词色彩": str(b.get("用词色彩", b.get("tone", "")) or ""),
                }
            )

        batches.sort(key=lambda x: (x["区间"][0], x["区间"][1]))

        # ── 重叠检测与自动修复 ──
        cleaned: List[dict] = []
        for b in batches:
            if not cleaned:
                cleaned.append(b)
                continue
            prev = cleaned[-1]
            cur_lo, cur_hi = b["区间"]
            prev_lo, prev_hi = prev["区间"]
            if cur_lo <= prev_hi:
                new_lo = prev_hi + 1
                if new_lo > cur_hi:
                    LOGGER.warning(
                        f"[BatchMetaData] {filename} 区间 [{prev_lo},{prev_hi}] "
                        f"与 [{cur_lo},{cur_hi}] 重叠，收缩后为空，已丢弃"
                    )
                    continue
                LOGGER.debug(
                    f"[BatchMetaData] {filename} 区间 [{cur_lo},{cur_hi}] "
                    f"与 [{prev_lo},{prev_hi}] 重叠，收缩为 [{new_lo},{cur_hi}]"
                )
                b["区间"] = [new_lo, cur_hi]
            cleaned.append(b)

        # ── 最大批次数限制：相邻区间合并 ──
        while len(cleaned) > max_batches:
            # 找相邻行数差最小的两个区间
            min_gap = float("inf")
            merge_idx = 0
            for i in range(len(cleaned) - 1):
                cur_lo, cur_hi = cleaned[i]["区间"]
                nxt_lo, nxt_hi = cleaned[i + 1]["区间"]
                gap = nxt_lo - cur_hi  # 区间之间的间距
                if gap < min_gap:
                    min_gap = gap
                    merge_idx = i
            # 合并 cleaned[merge_idx] 和 cleaned[merge_idx + 1]
            merged = dict(cleaned[merge_idx])  # 取前一个区间的元信息
            merged["区间"] = [merged["区间"][0], cleaned[merge_idx + 1]["区间"][1]]
            LOGGER.debug(
                f"[BatchMetaData] {filename} 合并区间 "
                f"[{cleaned[merge_idx]['区间'][0]},{cleaned[merge_idx]['区间'][1]}] + "
                f"[{cleaned[merge_idx + 1]['区间'][0]},{cleaned[merge_idx + 1]['区间'][1]}] "
                f"→ [{merged['区间'][0]},{merged['区间'][1]}]"
            )
            cleaned[merge_idx] = merged
            del cleaned[merge_idx + 1]

        return {"id": filename, "批次": cleaned}

    # 4. 合并写入 BatchMetadata.json
    def _save_metadata(self, meta: dict, filename: str = "") -> None:
        from GalTransl import PASS2_CACHE_DIR
        out_dir = os.path.join(self.pj_config.getCachePath(), PASS2_CACHE_DIR)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "BatchMetadata.json")
        with self._bm_lock:
            existing: List[dict] = []
            if os.path.exists(path) and os.path.getsize(path) > 0:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        existing = data
                    LOGGER.debug(
                        f"[BatchMetaData] 读取已有 BatchMetadata.json，"
                        f"共 {len(existing)} 条记录"
                    )
                except Exception as e:
                    LOGGER.warning(
                        f"[BatchMetaData] 读取 BatchMetadata.json 失败，"
                        f"将重置为仅包含当前文件：{e}"
                    )
                    existing = []
            else:
                LOGGER.debug(
                    f"[BatchMetaData] 新建 BatchMetadata.json"
                )
            replaced = False
            for i, e in enumerate(existing):
                if isinstance(e, dict) and e.get("id") == meta["id"]:
                    existing[i] = meta
                    replaced = True
                    if filename:
                        LOGGER.debug(
                            f"[BatchMetaData] {filename} 替换已有批次条目"
                        )
                    break
            if not replaced:
                existing.append(meta)
                LOGGER.debug(
                    f"[BatchMetaData] {filename} 追加新条目，"
                    f"总条目数：{len(existing)}"
                )
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            LOGGER.debug(
                f"[BatchMetaData] 已保存 {path}（{len(existing)} 条记录）"
            )

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

        script_text, max_index = self._build_script_text(json_list, filename)
        if not script_text:
            LOGGER.warning(
                f"[BatchMetaData] {filename} 剧本正文为空，跳过"
            )
            return False
        glossary_text = self._build_glossary_text()
        file_meta_block = self._build_file_metadata_block(filename)
        prompt = self._build_prompt_request(
            script_text, glossary_text, file_metadata=file_meta_block
        )

        LOGGER.info(f"[BatchMetaData] 正在为 {filename} 划分翻译区间…")
        LOGGER.debug(
            f"[BatchMetaData] {filename} 提示词长度：{len(prompt)} 字符，"
            f"脚本 {len(json_list)} 句，最大行号 {max_index}"
        )
        try:
            # 不使用系统提示词：直接以 user 消息发送
            messages = [{"role": "user", "content": prompt}]
            rsp, token = await self.ask_chatbot(
                messages=messages,
                file_name=filename,
                max_retry_count=3,
            )
        except Exception as e:
            LOGGER.error(f"[BatchMetaData] {filename} LLM 请求失败：{e}")
            return False

        meta = self._parse_meta(rsp or "", filename)
        if not meta:
            LOGGER.warning(f"[BatchMetaData] {filename} 未解析到有效 JSON，跳过")
            return False

        meta = self._normalize_meta(meta, filename, max_index, self.max_batches)
        if not meta["批次"]:
            LOGGER.warning(f"[BatchMetaData] {filename} 未解析到有效区间，跳过")
            return False
        self._save_metadata(meta, filename)
        LOGGER.info(
            f"[BatchMetaData] {filename} 已写入 "
            f"transl_cache/pass2_cache/BatchMetadata.json "
            f"（共 {len(meta['批次'])} 个区间）"
        )
        return True


if __name__ == "__main__":
    pass
