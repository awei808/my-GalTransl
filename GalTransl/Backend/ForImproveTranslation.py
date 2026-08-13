"""改进轮后端：整文件翻译完成后评估译文质量，生成可替换的备选译文。"""

import json
import re
from typing import List, Optional, Union

from GalTransl import LOGGER
from GalTransl.CSentense import CTransList
from GalTransl.Backend.ForGalJsonMulitChat import ForGalJsonMulitChat
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_IMPROVE_PROMPT,
    FORIMPROVE_SYSTEM,
)
from GalTransl.Service import JobCancelledError
from GalTransl.Utils import extract_code_blocks, fix_quotes


class ForImproveTranslation(ForGalJsonMulitChat):
    """
    改进轮后端：向 AI 发送「文件级元数据 + 翻译规范 + 评估标准 + 原文 + 译文」，
    让模型评估哪些句子的译文还能翻译得更好，并把备选译文写入各句 alt_dst。

    作为完整流水线的第 8 阶段使用；也可被独立选中对已翻译文件执行改进评估。
    引擎标识：ForImproveTranslation
    """

    def __init__(
        self,
        config,
        eng_type: str,
        proxy_pool=None,
        token_pool=None,
    ) -> None:
        """
        初始化改进轮后端。

        Args:
            config: 项目配置对象。
            eng_type: 引擎标识（ForImproveTranslation）。
            proxy_pool: 代理池对象，为 None 时不使用代理。
            token_pool: API Token 池。
        """
        super().__init__(config, eng_type, proxy_pool, token_pool)
        # 覆盖基类（翻译轮）的系统提示词为改进轮专用角色声明
        self.system_prompt = FORIMPROVE_SYSTEM
        self.trans_prompt = FORGAL_JSON_IMPROVE_PROMPT
        # 覆盖默认值后重新应用用户模板 override（基类 __init__ 已应用过一次）
        self._apply_internal_prompt_template_overrides()

    async def batch_translate(
        self,
        filename: str,
        cache_file_path: str,
        trans_list: CTransList,
        num_pre_request: int,
        retry_failed: bool = False,
        gpt_dic=None,
        proofread: bool = False,
        retran_key: str = "",
        translist_hit: list = [],
        translist_unhit: list = [],
    ) -> CTransList:
        """
        翻译接口：对整文件译文执行质量改进评估。

        与 ForGalJsonMulitChat.batch_translate 同名同签名（"翻译接口"），
        使 LLMTranslate 能以统一的 batch_translate 驱动本后端。
        改进轮不改写 pre_dst / trans_by，仅把模型给出的备选译文写入各句 alt_dst；
        输入输出的 trans_list 保持一致。

        Args:
            filename: 原始文件名（对话分桶键，不带分块后缀）。
            cache_file_path: 缓存文件路径（本后端不读写缓存，仅兼容签名）。
            trans_list: 全文件句子（含原文与当前译文）。
            num_pre_request: 每批句子数（受 gpt.numPerRequestBetter 覆盖）。
            gpt_dic: 术语表对象（每批按本批句子注入，供术语一致性评估）。

        Returns:
            CTransList: 与输入一致（alt_dst 已就地更新）。
        """
        # 是否向提示词注入译文问题（problem）及类型白名单（空=注入全部）
        problem_types = None
        if self.pj_config.getKey("gpt.enableProblemInject"):
            problem_types = self._coerce_problem_type_list(
                self.pj_config.getKey("gpt.problemInjectTypes")
            )
            LOGGER.debug(
                f"[改进轮] {filename} 注入译文问题，类型白名单："
                f"{[t.name for t in problem_types] if problem_types else '全部'}"
            )

        valid = [
            t
            for t in trans_list
            if t.post_src != "" and t.pre_dst != "" and "(Failed)" not in t.pre_dst
        ]
        total = len(valid)
        if total == 0:
            LOGGER.info(f"[改进轮] {filename} 无可评估译文，跳过")
            return trans_list

        # 用原始文件名独立分桶，从首轮重建（不混用翻译轮历史）
        self.reset_conversation(filename)
        self.conversations[filename] = [
            {"role": "system", "content": self.system_prompt}
        ]

        num_per_request = self._coerce_positive_int(
            self.pj_config.getKey("gpt.numPerRequestBetter"), num_pre_request or 100
        )
        total_batches = (total + num_per_request - 1) // num_per_request
        improve_count = 0
        LOGGER.info(
            f"[改进轮] {filename} 开始质量改进评估，共 {total} 句，{total_batches} 批"
        )

        for batch_no, start in enumerate(range(0, total, num_per_request), start=1):
            self._check_stop_requested()
            batch = valid[start : start + num_per_request]
            idx_tip = self._build_idx_tip(batch)
            # 每批按本批句子重新生成术语表（与翻译轮一致，按需注入）；
            # h/非h 场景分流：本批处于 h 区间注入 h 字典，否则只注入非 h 字典
            batch_gptdict = ""
            if gpt_dic is not None:
                try:
                    batch_gptdict = gpt_dic.gen_prompt(
                        batch,
                        scene="h" if self._group_is_h_scene(batch, filename) else "nh",
                    )
                except Exception:
                    batch_gptdict = ""
            # 输入携带当前生效译文（proofread_zh 优先，否则 pre_dst），与校对轮一致；
            # 可选携带译文问题（problem）供评估参考
            input_list, sig_list, n_symbol, input_src = self._build_input_jsonlines(
                batch,
                proofread=True,
                filename=filename,
                problem_types=problem_types,
            )
            conv = self._ensure_conversation(filename)
            is_first_round = len(conv) <= 1
            if is_first_round:
                user_content = self._build_improve_first_round_content(
                    input_src, batch_gptdict, filename
                )
            else:
                # 续轮同样注入本批术语表，与翻译轮行为一致
                user_content = (
                    batch_gptdict + "\n以下是本批次待评估内容：\n" + input_src
                    if batch_gptdict
                    else input_src
                )
            call_messages = conv + [{"role": "user", "content": user_content}]

            # 单 worker 模式下打印改进轮上下文摘要，便于调试
            if self.pj_config.active_workers == 1:
                _round = "首轮" if is_first_round else "续轮"
                LOGGER.info(
                    f"-> 改进输入[{_round}] | backend={self.eng_type} | "
                    f"sentences={len(batch)}"
                )
                LOGGER.info("->输出：")

            try:
                raw_resp, _token = await self._call_llm(
                    call_messages, filename, idx_tip, None
                )
            except JobCancelledError:
                raise
            except Exception as e:
                LOGGER.warning(
                    f"[改进轮][{filename}:{idx_tip}]LLM调用失败：{type(e).__name__}: {e}"
                )
                self._record_improve_runtime_error(
                    filename, idx_tip, f"{type(e).__name__}: {e}", None
                )
                self.reset_conversation(filename)
                continue

            # 解析结果：改进轮输出为稀疏序列（跳过无需改进的句子），
            # 必须按 id 匹配而非按输出顺序，故不复用翻译轮的顺序解析器。
            # 批内解析失败仅跳过本批，不标 (Failed)、不改写 pre_dst。
            result_text = raw_resp or ""
            if "</think>" in result_text:
                result_text = result_text.split("</think>")[-1]
            if "```json" in result_text:
                lang_list, code_list = extract_code_blocks(result_text)
                if lang_list and code_list:
                    result_text = code_list[0]
            sig_start = re.search(r"\b[a-z0-9]{3}\|\{\"id\"", result_text)
            if sig_start:
                result_text = result_text[sig_start.start() :]
            result_text = fix_quotes(result_text)
            success_count = self._parse_improve_jsonline_text(
                result_text, batch, n_symbol
            )

            # 追加 assistant 回复进对话，保持轮次交替（空输出也追加，确保续轮识别）
            self.conversations[filename] = self._trim_conversation(
                call_messages + [{"role": "assistant", "content": raw_resp or ""}]
            )
            improve_count += success_count
            LOGGER.debug(
                f"[改进轮] {filename} 批次 {batch_no}/{total_batches}（序号 {idx_tip}）"
                f"已评估，改进 {success_count} 句"
            )

        if improve_count > 0:
            LOGGER.info(f"[改进轮] {filename} 共改进 {improve_count} 句")
        else:
            LOGGER.info(f"[改进轮] {filename} 未发现可改进句子")
        return trans_list

    @staticmethod
    def _coerce_problem_type_list(raw_types) -> list:
        """把配置的问题类型规范为 CProblemType 列表。

        兼容：CProblemType 成员、其中文名字符串、逗号分隔字符串、YAML 列表。
        空列表表示"不限制类型"（注入全部已检测问题）。

        Args:
            raw_types: 配置值（None / str / list）。

        Returns:
            list[CProblemType]: 规范化后的类型列表。
        """
        if raw_types is None:
            return []
        if isinstance(raw_types, str):
            raw_types = [t.strip() for t in raw_types.split(",") if t.strip()]
        from GalTransl.ConfigHelper import CProblemType

        result = []
        for item in raw_types:
            name = item.name if hasattr(item, "name") else str(item)
            try:
                result.append(CProblemType[name])
            except KeyError:
                continue
        return result

    def _build_improve_first_round_content(
        self, input_src: str, gptdict: str, filename: str
    ) -> str:
        """改进轮首轮内容：以改进提示词为模板，注入术语表、剧情元数据与输入。"""
        prompt_req = self.trans_prompt
        prompt_req = prompt_req.replace(
            "[translation_guideline]", self.pj_config.translation_guideline
        )
        prompt_req = prompt_req.replace("[Input]", input_src)
        prompt_req = prompt_req.replace("[Glossary]", gptdict)
        metadata = self._resolve_file_metadata(filename)
        metadata_block = (
            self._format_file_metadata_block(metadata)
            if metadata is not None
            else ""
        )
        prompt_req = prompt_req.replace("[plot_metadata]", metadata_block)
        prompt_req = prompt_req.replace("[batch_metadata]", "")
        # 全局提示词（GlobalPrompt）：仅首轮注入，与翻译轮一致
        prompt_req = prompt_req.replace(
            "[global_prompt]", self._format_global_prompt_block(filename) or ""
        )
        prompt_req = prompt_req.replace("[SourceLang]", self.source_lang)
        prompt_req = prompt_req.replace("[TargetLang]", self.target_lang)
        prompt_req = self._apply_history_result(prompt_req, filename)
        return prompt_req

    def _parse_improve_jsonline_text(
        self, result_text: str, trans_list: CTransList, n_symbol: str
    ) -> int:
        """按 id 稀疏解析改进轮输出，把 better 写入对应句子的 alt_dst。

        改进轮只输出有改进空间的句子（稀疏序列，序号不连续），
        与翻译轮的"逐行按顺序对应"解析不兼容，故按 id 定位句子。

        Args:
            result_text: 模型返回的 jsonline 文本（可能含乱行/空行）。
            trans_list: 本批句子。
            n_symbol: 换行符标记（用于恢复译文中的换行）。

        Returns:
            int: 成功写入备选译文的句子数。
        """
        id_map = {t.index: t for t in trans_list}
        success_count = 0
        for line in result_text.splitlines():
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                # 带哈希锚点的行：取 | 后的 JSON 部分
                json_part = line.split("|", 1)[1].strip()
            else:
                # 容错：模型未带锚点时整行按 JSON 解析
                json_part = line
            try:
                obj = json.loads(json_part)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            line_id = obj.get("id")
            better = obj.get("better")
            if not isinstance(line_id, int) or not isinstance(better, str):
                continue
            tran = id_map.get(line_id)
            if tran is None:
                continue
            # 空译文 / 含乱码：与翻译轮解析一致地过滤，避免污染备选译文
            if tran.post_src != "" and better.strip() == "":
                continue
            if "�" in better:
                continue
            # 换行符替换与恢复：复用翻译轮的归一化（<BR>/真实换行→<br>→n_symbol），
            # 使 better 与送入模型的当前译文处于同一换行表示后再比较
            normalized = self._normalize_parsed_translation_text(better, tran, n_symbol)
            # 当前主译文：校对结果优先，否则初译
            current_dst = tran.proofread_zh if tran.proofread_zh != "" else tran.pre_dst
            # 未实际改进（含仅换行表示差异）：跳过，不写 alt_dst、不计改进数
            if current_dst != "" and normalized.strip() == current_dst.strip():
                LOGGER.debug(f"[改进轮] 句子 {line_id} 的 better 与当前译文相同，跳过")
                continue
            tran.alt_dst = normalized
            success_count += 1
        return success_count

    def _record_improve_runtime_error(
        self, filename: str, idx_tip: str, message: str, model: Optional[str]
    ) -> None:
        """改进轮运行态错误上报（工作台"最近错误"卡片）。"""
        try:
            from GalTransl.server import record_runtime_error

            record_runtime_error(
                getattr(
                    self.pj_config,
                    "runtime_project_dir",
                    self.pj_config.getProjectDir(),
                ),
                kind="parse",
                message=message,
                filename=filename,
                index_range=str(idx_tip),
                model=model or self.get_last_chatbot_model(),
                level="warning",
            )
        except Exception:
            pass
