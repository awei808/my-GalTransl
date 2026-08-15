"""换行位置异常修复后端：针对译文「换行位置异常」问题，用 AI 修复换行位置。

仅向 AI 发送带有「换行位置异常」问题标注的译文，AI 返回修复换行后的备选译文，
后端解析（含换行防御替换，与多轮对话后端同策略）、按 id 稀疏匹配、筛查后写入
各句 alt_dst，不破坏既有主译文。

作为完整流水线的独立修复阶段使用；也可被独立选中，对已翻译文件单独执行。
引擎标识：ForBRStation
"""

import json
import re
from typing import List, Optional, Union

from GalTransl import LOGGER
from GalTransl.CSentense import CTransList
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.ForGalJsonMulitChat import ForGalJsonMulitChat
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_BRSTATION_PROMPT,
    FORBR_SYSTEM,
)
from GalTransl.ConfigHelper import CProblemType
from GalTransl.Service import JobCancelledError
from GalTransl.Utils import extract_code_blocks, fix_quotes


@register_engine("ForBRStation")
class ForBRStation(ForGalJsonMulitChat):
    """
    换行位置异常修复后端：向 AI 发送「文件级元数据 + 翻译规范 + 字典 + 换行异常
    说明与解决方法 + 当前译文（仅含换行位置异常问题标注的句子）」，让模型按 3 级
    优先级修复换行位置，并把备选译文写入各句 alt_dst。

    引擎标识：ForBRStation
    """

    def __init__(
        self,
        config,
        eng_type: str,
        proxy_pool=None,
        token_pool=None,
    ) -> None:
        """
        初始化换行位置异常修复后端。

        Args:
            config: 项目配置对象。
            eng_type: 引擎标识（ForBRStation）。
            proxy_pool: 代理池对象，为 None 时不使用代理。
            token_pool: API Token 池。
        """
        super().__init__(config, eng_type, proxy_pool, token_pool)
        # 覆盖基类（翻译轮）的系统提示词为换行修复轮专用角色声明
        self.system_prompt = FORBR_SYSTEM
        self.trans_prompt = FORGAL_JSON_BRSTATION_PROMPT
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
        翻译接口：对整文件中带有「换行位置异常」问题的译文执行换行修复。

        与 ForGalJsonMulitChat.batch_translate 同名同签名（"翻译接口"），
        使 LLMTranslate 能以统一的 batch_translate 驱动本后端。
        本后端不改写 pre_dst / trans_by，仅把模型给出的修复译文写入各句 alt_dst；
        输入输出的 trans_list 保持一致。

        Args:
            filename: 原始文件名（对话分桶键，不带分块后缀）。
            cache_file_path: 缓存文件路径（本后端不读写缓存，仅兼容签名）。
            trans_list: 全文件句子（含原文与当前译文）。
            num_pre_request: 每批句子数（受 gpt.numPerRequestBetter 覆盖）。
            gpt_dic: 术语表对象（每批按本批句子注入，供术语一致性参考）。

        Returns:
            CTransList: 与输入一致（alt_dst 已就地更新）。
        """
        # 仅筛选带有「换行位置异常」问题标注、且已有有效译文的句子。
        # 显式排除 skip_check：用户标记「跳过检查」的句子一律不再送审（独立硬过滤，
        # 与「重检清除 problem」的间接副作用解耦——避免重检失败时 problem 残留
        # 导致该句仍被发送给 AI 的不一致行为）。
        target_trans_list = [
            t
            for t in trans_list
            if t.post_src != ""
            and t.pre_dst != ""
            and "(Failed)" not in t.pre_dst
            and not getattr(t, "skip_check", False)
            and self._has_newline_anomaly(t)
        ]
        total = len(target_trans_list)
        if total == 0:
            LOGGER.info(f"[换行修复] {filename} 无可处理的新行位置异常译文，跳过")
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
        fix_count = 0
        LOGGER.info(
            f"[换行修复] {filename} 开始换行位置异常修复，共 {total} 句，"
            f"{total_batches} 批"
        )

        for batch_no, start in enumerate(range(0, total, num_per_request), start=1):
            self._check_stop_requested()
            batch = target_trans_list[start : start + num_per_request]
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
            # 输入携带当前生效译文（proofread_zh 优先，否则 pre_dst）与问题标注，
            # 并强制注入 problem（换行位置异常），供 AI 定位异常行。
            # 仅注入译文 dst、不注入原文 src：换行修复是纯中文任务，模型无需原文，
            # 同时从根上避免模型回显原文（含 src 时模型曾原样回显输入而漏出 better）。
            input_list, sig_list, n_symbol, input_src = self._build_input_jsonlines(
                batch,
                proofread=True,
                filename=filename,
                problem_types=[CProblemType.换行位置异常],
                include_src=False,
            )
            LOGGER.debug(
                f"[换行修复][{filename}] 本批仅注入译文(dst)不含原文(src)，共 {len(batch)} 句"
            )
            conv = self._ensure_conversation(filename)
            is_first_round = len(conv) <= 1
            if is_first_round:
                user_content = self._build_br_first_round_content(
                    input_src, batch_gptdict, filename
                )
            else:
                # 续轮同样注入本批术语表，与翻译轮行为一致
                user_content = (
                    batch_gptdict + "\n以下是本批次待处理内容：\n" + input_src
                    if batch_gptdict
                    else input_src
                )
            call_messages = conv + [{"role": "user", "content": user_content}]

            # 单 worker 模式下打印输入上下文摘要，便于调试
            if self.pj_config.active_workers == 1:
                _round = "首轮" if is_first_round else "续轮"
                LOGGER.info(
                    f"-> 换行修复输入[{_round}] | backend={self.eng_type} | "
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
                    f"[换行修复][{filename}:{idx_tip}]LLM调用失败："
                    f"{type(e).__name__}: {e}"
                )
                self._record_br_runtime_error(
                    filename, idx_tip, f"{type(e).__name__}: {e}", None
                )
                self.reset_conversation(filename)
                continue

            # 解析结果：与改进轮一致，输出为稀疏序列（跳过无需修复的句子），
            # 必须按 id 匹配而非按输出顺序，故不复用翻译轮的顺序解析器。
            # 批内解析失败仅跳过本批，不标 (Failed)、不改写主译文。
            result_text = raw_resp or ""
            if "</think>" in result_text:
                result_text = result_text.split("</think>")[-1]
            if "```json" in result_text:
                _lang_list, code_list = extract_code_blocks(result_text)
                if code_list:
                    # 合并所有代码块而非只取第一个：模型偶尔把输出切成多段 jsonline
                    # 代码块，只取 [0] 会丢失后续句子的修复译文
                    if len(code_list) > 1:
                        LOGGER.debug(
                            f"[换行修复][{filename}] 模型输出 {len(code_list)} 个代码块，"
                            f"已合并解析"
                        )
                    result_text = "\n".join(code_list)
            sig_start = re.search(r"\b[a-z0-9]{3}\|\{\"id\"", result_text)
            if sig_start:
                result_text = result_text[sig_start.start() :]
            result_text = fix_quotes(result_text)
            success_count, found_count = self._parse_br_jsonline_text(
                result_text, batch, n_symbol
            )

            # 修复 B：模型响应非空、却未解析到任何可用 better——多为输出格式异常
            # （如漏 better 键、把输入回显、或多段代码块外残留），日志警告并上报
            # 控制台"最近错误"，便于定位模型输出格式问题。
            if result_text.strip() and found_count == 0:
                _br_warn_msg = (
                    f"[换行修复][{filename}:{idx_tip}] 模型响应非空但未解析到任何 "
                    f"better（输出格式异常或内容问题），本次 0 句修复"
                )
                LOGGER.warning(_br_warn_msg)
                self._record_br_runtime_error(filename, idx_tip, _br_warn_msg, None)

            # 追加 assistant 回复进对话，保持轮次交替（空输出也追加，确保续轮识别）
            self.conversations[filename] = self._trim_conversation(
                call_messages + [{"role": "assistant", "content": raw_resp or ""}]
            )
            fix_count += success_count
            LOGGER.debug(
                f"[换行修复] {filename} 批次 {batch_no}/{total_batches}（序号 {idx_tip}）"
                f"已评估，修复 {success_count} 句"
            )

        if fix_count > 0:
            LOGGER.info(f"[换行修复] {filename} 共修复 {fix_count} 句")
        else:
            LOGGER.info(f"[换行修复] {filename} 未发现需修复的换行异常")
        return trans_list

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    @staticmethod
    def _has_newline_anomaly(tran) -> bool:
        """判断句子是否带有「换行位置异常」问题标注。

        tran.problem 为程序检测后写入的逗号分隔字符串（如 "换行位置异常：第1行"），
        复用父类 _filter_problem_by_types 按类型白名单精确匹配，避免直接字符串
        臆测导致漏判/误判，与改进轮注入 problem 的逻辑保持一致。
        """
        problem = getattr(tran, "problem", "")
        if not problem:
            return False
        kept = ForGalJsonMulitChat._filter_problem_by_types(
            problem, [CProblemType.换行位置异常]
        )
        return bool(kept)

    @staticmethod
    def _build_br_issue_guide() -> str:
        """动态生成「换行位置异常」说明与解决方法（避免与检测侧定义不同步）。

        允许换行字符集直接复用 Problem._ALLOWED_BREAK_CHARS，确保与检测逻辑一致。
        """
        try:
            from GalTransl.Problem import _ALLOWED_BREAK_CHARS

            allowed = _ALLOWED_BREAK_CHARS
        except Exception:
            allowed = "。！？…—”’」』）】、，"
        allowed_str = "、".join(list(allowed))
        lines = [
            "1. 【优先，推荐度最高】调整换行符位置：",
            "   把被拆断在行首/行尾的中文词语或短语合并回上一行，或把换行移动到",
            f"   合理的中文断句点（仅允许落在以下字符之后：{allowed_str}）。",
            "   例：把「她突然站了起<br>来，跑了出去。」改为「她突然站起来，跑了出去。」"
            "（删除不当换行）。",
            "2. 【次选，推荐度中等】在 `<br>` 前面增加一个中文逗号「，」：",
            "   仅当该处确实需要一个停顿、且补逗号后语义与节奏更自然时使用。",
            "   例：把「好痛<br>啊」改为「好痛，<br>啊」。",
            "3. 【最后手段，推荐度最低】直接删除 `<br>`：",
            "   当该处无需任何换行、删除后整句单行阅读更顺畅时使用，且最多删除一个<br>。",
            "   注意：优先用方法1，方法3会丢失原有的视觉分段意图，仅在确无分段必要时采用。",
        ]
        return "\n".join(lines)

    def _build_br_first_round_content(
        self, input_src: str, gptdict: str, filename: str
    ) -> str:
        """换行修复首轮内容：以专用提示词为模板，注入术语表、剧情元数据与输入。"""
        prompt_req = self.trans_prompt
        prompt_req = prompt_req.replace(
            "[translation_guideline]", self.pj_config.translation_guideline
        )
        prompt_req = prompt_req.replace("[Input]", input_src)
        prompt_req = prompt_req.replace("[Glossary]", gptdict)
        prompt_req = prompt_req.replace("[br_issue_guide]", self._build_br_issue_guide())
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

    @staticmethod
    def _decode_json_part(json_part: str) -> Optional[dict]:
        """容错解析单行 JSON 对象。

        优先严格 `json.loads`；失败时从首个 `{` 起用 `json.JSONDecoder.raw_decode`
        解析第一个 JSON 值并忽略对象后的尾随垃圾（模型可能误加 `</br>`、`；` 等），
        从而不丢本可用的修复译文。非 dict 或无法解析返回 None。
        """
        try:
            obj = json.loads(json_part)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
        start = json_part.find("{")
        if start == -1:
            return None
        try:
            obj, _end = json.JSONDecoder().raw_decode(json_part[start:])
        except Exception:
            return None
        return obj if isinstance(obj, dict) else None

    def _parse_br_jsonline_text(
        self, result_text: str, trans_list: CTransList, n_symbol: str
    ) -> tuple:
        """按 id 稀疏解析换行修复输出，把 better 写入对应句子的 alt_dst。

        与改进轮解析一致：稀疏序列、按 id 定位、换行防御替换、与当前主译文比对去重。

        Returns:
            (success_count, found_count)
            - success_count: 实际写入 alt_dst 的句子数（better 与当前译文不同）
            - found_count: 模型给出了可用 better 且命中本批句子的行数（含与当前
              译文相同而跳过的行）。供调用方判断「响应非空但未解析到 better」。
        """
        id_map = {t.index: t for t in trans_list}
        success_count = 0
        found_count = 0
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
            obj = self._decode_json_part(json_part)
            if obj is None:
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
            # 模型确实给出了可用 better，计入 found_count
            found_count += 1
            # 换行符防御替换与恢复：复用翻译轮的归一化（<BR>/真实换行→<br>→n_symbol），
            # 使 better 与送入模型的当前译文处于同一换行表示后再比较
            normalized = self._normalize_parsed_translation_text(
                better, tran, n_symbol
            )
            # 当前主译文：校对结果优先，否则初译
            current_dst = (
                tran.proofread_zh if tran.proofread_zh != "" else tran.pre_dst
            )
            # 未实际改进（含仅换行表示差异）：跳过，不写 alt_dst、不计修复数
            if current_dst != "" and normalized.strip() == current_dst.strip():
                LOGGER.debug(
                    f"[换行修复] 句子 {line_id} 的 better 与当前译文相同，跳过"
                )
                continue
            tran.alt_dst = normalized
            success_count += 1
        return success_count, found_count

    def _record_br_runtime_error(
        self, filename: str, idx_tip: str, message: str, model: Optional[str]
    ) -> None:
        """换行修复运行态错误上报（工作台"最近错误"卡片）。"""
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
