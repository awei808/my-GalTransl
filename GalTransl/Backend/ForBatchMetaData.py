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
    ):
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

        # 是否把项目翻译规范（translation_guideline）注入提示词，默认开启。
        # 用户可在 config 的 internals.forbatchmeta.inject_guideline 设为
        # false / 0 / no 来关闭注入。
        raw = self.pj_config.getKey("internals.forbatchmeta.inject_guideline", True)
        if isinstance(raw, bool):
            self._inject_guideline = raw
        else:
            self._inject_guideline = (
                str(raw).strip().lower() not in ("false", "0", "no", "")
            )

        # 文件级剧情元数据映射（{文件名: FileMetaData}），惰性载入一次
        self._file_metadata_by_file: dict = {}
        self._file_metadata_loaded: bool = False

        # 跨文件（可能的并发 worker）写 BatchMetadata.json 时的互斥锁
        self._bm_lock = Lock()

    # ------------------------------------------------------------------
    # 0. 文件级剧情元数据（FileMetaData）载入与格式化
    # ------------------------------------------------------------------
    def _ensure_file_metadata_loaded(self) -> None:
        """惰性载入 FileMetaData.json（仅执行一次）。"""
        if self._file_metadata_loaded:
            return
        self._file_metadata_loaded = True  # 先置位，避免异常导致反复重试
        try:
            self._file_metadata_by_file = load_file_metadata_map(self.pj_config)
        except Exception as e:
            LOGGER.warning(f"载入 FileMetaData.json 失败，批次元数据将不含文件级背景：{e}")
            self._file_metadata_by_file = {}

    def _build_file_metadata_block(self, filename: str) -> str:
        """取该文件的文件级剧情元数据，格式化为提示词背景块。

        找不到对应条目（未生成 FileMetaData.json 或缺少该文件）时返回空串，
        对应模板中的 [plot_metadata] 会被替换为空。
        """
        self._ensure_file_metadata_loaded()
        md = self._file_metadata_by_file.get(filename)
        if md is None:
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

    # ------------------------------------------------------------------
    # 1. 组装提示词：可控注入 translation_guideline + 文件级剧情元数据
    # ------------------------------------------------------------------
    def _build_prompt_request(
        self, input_src: str, gptdict: str, file_metadata: str = ""
    ) -> str:
        """在基类占位符替换基础上，增加 translation_guideline 的可控注入。"""
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
        prompt_req = prompt_req.replace("[plot_metadata]", file_metadata)
        prompt_req = prompt_req.replace("[SourceLang]", self.source_lang)
        prompt_req = prompt_req.replace("[TargetLang]", self.target_lang)
        return prompt_req

    # ------------------------------------------------------------------
    # 2. 准备输入：带全局行号的剧本正文 + gpt 字典（专名译表）
    # ------------------------------------------------------------------
    def _build_script_text(self, json_list) -> tuple:
        """把 json_list 拼成带全局行号的可读剧本正文。

        行号规则与 Loader.load_transList / CSplitter 中 runtime_index 一致：
        优先取行内显式 index，否则用 1 起的位置序号（i+1）。这样生成的区间
        行号能与翻译阶段每个句子的 runtime_index 精确对应。

        Returns:
            (script_text, max_index)：拼接后的正文，以及最大行号（供裁剪区间用）
        """
        out: List[str] = []
        max_index = 0
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
            # 压平换行/制表符，避免破坏逐行结构
            msg = str(msg).replace("\r\n", " ").replace("\n", " ").replace("\t", " ")
            if name:
                out.append(f"[{idx}] {name}：{msg}")
            else:
                out.append(f"[{idx}] {msg}")
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
            LOGGER.warning(f"载入 GPT 字典失败，批次元数据将不含专名译表：{e}")
            return ""

        lines = [
            "# Glossary",
            "| Src | Dst(/Dst2/..) | Note |",
            "| --- | --- | --- |",
        ]
        for dic in getattr(gpt_dic, "_dic_list", []):
            note = getattr(dic, "note", "") or ""
            lines.append(f"| {dic.search_word} | {dic.replace_word} | {note} |")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 3. 解析与规整 LLM 返回的 JSON
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_meta(text: str) -> Optional[dict]:
        if not text or not text.strip():
            return None
        if "</think>" in text:
            text = text.split("</think>")[-1]
        lang_list, code_list = extract_code_blocks(text)
        if code_list:
            text = code_list[0]
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            return None

    @staticmethod
    def _normalize_meta(obj: dict, filename: str, max_index: int) -> dict:
        """规整批次数组：清洗字段类型、裁剪并排序区间，强制 id == 文件名。

        - 区间起止裁剪到 [1, max_index]，丢弃非法/空区间；
        - 按起始行号升序排序；
        - h 规整为布尔值；视角/氛围/用词色彩规整为字符串。
        """

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
        return {"id": filename, "批次": batches}

    # ------------------------------------------------------------------
    # 4. 合并写入 BatchMetadata.json（gt_input 下，按 id 合并）
    # ------------------------------------------------------------------
    def _save_metadata(self, meta: dict) -> None:
        out_dir = self.pj_config.getInputPath()  # gt_input
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
                except Exception:
                    existing = []
            replaced = False
            for i, e in enumerate(existing):
                if isinstance(e, dict) and e.get("id") == meta["id"]:
                    existing[i] = meta
                    replaced = True
                    break
            if not replaced:
                existing.append(meta)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # 5. 入口：每文件调用一次，生成并写回一条批次元数据
    # ------------------------------------------------------------------
    async def batch_translate(
        self,
        json_list: list,
        filename: str = "",
        gpt_dic=None,
    ) -> bool:
        if not filename:
            LOGGER.warning("ForBatchMetaData: 未提供 filename，跳过该文件")
            return False

        script_text, max_index = self._build_script_text(json_list)
        glossary_text = self._build_glossary_text()
        file_meta_block = self._build_file_metadata_block(filename)
        prompt = self._build_prompt_request(
            script_text, glossary_text, file_metadata=file_meta_block
        )

        LOGGER.info(f"[BatchMetaData] 正在为 {filename} 划分翻译区间…")
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

        meta = self._parse_meta(rsp or "")
        if not meta:
            LOGGER.warning(f"[BatchMetaData] {filename} 未解析到有效 JSON，跳过")
            return False

        meta = self._normalize_meta(meta, filename, max_index)
        if not meta["批次"]:
            LOGGER.warning(f"[BatchMetaData] {filename} 未解析到有效区间，跳过")
            return False
        self._save_metadata(meta)
        LOGGER.info(
            f"[BatchMetaData] {filename} 已写入 BatchMetadata.json "
            f"（共 {len(meta['批次'])} 个区间）"
        )
        return True


if __name__ == "__main__":
    pass
