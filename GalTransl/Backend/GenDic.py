import json, time, asyncio, os, traceback, re
from opencc import OpenCC
from typing import List, Set, Dict, Optional, Tuple, Any
from concurrent.futures import ThreadPoolExecutor

from alive_progress import alive_bar
from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProxyPool, initDictList
from GalTransl import LOGGER, LANG_SUPPORTED
from GalTransl.i18n import get_text, GT_LANG
from sys import exit
from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.Dictionary import CGptDict
from GalTransl.Utils import contains_katakana, is_all_chinese, decompress_file_lzma
from GalTransl.Backend.BaseEngine import BaseEngine, register_engine
from GalTransl.Backend.Prompts import (
    GENDIC_PROMPT,
    GENDIC_TERMS_PROMPT,
    GENDIC_SYSTEM,
    H_WORDS_LIST,
    FAILED_PREFIX,
)
import collections
from threading import Lock
from GalTransl.TerminalOutput import should_print_translation_logs, terminal_progress

# 维护状态：已重新纳入正常迭代维护（设计文档 gendic_terms_mode_design.md）。
# 当前同时支持 segments 模式（旧，传完整片段给 AI）与 terms 模式（新，本地提取词表后逐词翻译），
# 由配置 internals.gendic.mode 切换（默认 terms）。

# 正则补充层：连续片假名字串（含・）
_KATAKANA_SEQ_RE = re.compile(r"[ァ-ヶー・]{2,}")


def _is_katakana_only(text: str) -> bool:
    """判断是否为纯片假名字串（含ー・），且长度>=2"""
    if len(text) < 2:
        return False
    for ch in text:
        cp = ord(ch)
        if ch in ("ー", "・"):
            continue
        if not (0x30A0 <= cp <= 0x30FF):
            return False
    return True


def _extract_regex_terms(text: str) -> Set[str]:
    """用正则补充提取专有名词候选：连续片假名字串。"""
    words: Set[str] = set()
    for m in _KATAKANA_SEQ_RE.finditer(text):
        w = m.group(0)
        if len(w) >= 2:
            words.add(w)
    return words


# ==== terms 模式提取层（gendic_terms_mode_design.md 第二/七节）====

# POS 白名单：只留名詞大类；名詞内再排除 代名詞/数詞/助数詞/非自立/接尾辞 子类
_TERMS_POS_ALLOW = {"名詞"}
_TERMS_POS_SUB_BLOCK = ("代名詞", "数詞", "助数詞", "非自立", "接尾辞")
# 拟声特征：纯假名 + 叠音重复子串（AA/ABAB，如 ドキドキ/イクイク/ククク/パンパン）
_ONOMATOPOEIA_RE = re.compile(r"(.{1,3})\1")
# 名字占位符：全符号（？？？/…/--- 等），不视为真实说话人
_TERMS_PLACEHOLDER_NAME_RE = re.compile(r"^[^0-9A-Za-z\u4e00-\u9fff\u3040-\u30ff]+$")


def _is_pure_kana(text: str) -> bool:
    """是否纯假名（平假名/片假名/长音/中点，Unicode 范围判定，含浊音）。"""
    return all(
        (0x3040 <= ord(ch) <= 0x309F) or (0x30A0 <= ord(ch) <= 0x30FF) or ch in "ー・"
        for ch in text
    )


def _is_placeholder_name(name: str) -> bool:
    """占位符说话人判定（？？？/… 等纯符号）。"""
    return not name or bool(_TERMS_PLACEHOLDER_NAME_RE.match(name))


def _is_onomatopoeia(word: str) -> bool:
    """拟声组合条件：纯假名且含叠音重复子串；不单凭"含长音"判定（防误伤スイートルーム等）。"""
    return len(word) >= 2 and _is_pure_kana(word) and bool(_ONOMATOPOEIA_RE.search(word))


def _is_term_droppable(src: str, dst: str, note: str) -> bool:
    """落盘前过滤：拟声 note / H 词表 / NULL / 空 note 的未翻译回显（真实测试暴露）。"""
    if "NULL" in src or src in H_WORDS_LIST or "拟声" in note:
        return True
    return dst == src and not note.strip()


def _has_katakana(text: str) -> bool:
    """是否含片假名（Unicode 范围，含浊音/半浊音/ー・）。"""
    return any(0x30A0 <= ord(ch) <= 0x30FF or ch in "ー・" for ch in text)


def _is_alpha_abbr(text: str) -> bool:
    """是否大写字母组合（含全角，如 ＣＦ/ＳＮＳ/ＶＩＰ/AV）；排除 % 控制码与假名/汉字。"""
    if len(text) < 2 or "%" in text or "％" in text:
        return False
    if any("\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in text):
        return False
    if not re.search(r"[A-ZＡ-Ｚ]", text):
        return False
    return all(
        ("A" <= ch <= "Z") or ("Ａ" <= ch <= "Ｚ") or ("0" <= ch <= "9")
        or ("０" <= ch <= "９") or ch in "＆&・ー-"
        for ch in text
    )


def extract_terms_from_tokens(
    tokens: List[Tuple[str, str]],
    name_set: Set[str],
    name_counter: Dict[str, int],
    existing_dict_map: Optional[Dict[str, Tuple[str, str]]] = None,
    han_allowlist: Optional[Set[str]] = None,
    skip_existing: bool = True,
    ban_words: Optional[Set[str]] = None,
) -> Tuple[List[Tuple[str, int, str]], Dict[str, int]]:
    """
    terms 模式候选提取：单 token（汉字/片假名/大写字母组合）+ 复合词（2-3 token）规则。

    Args:
        tokens: 分词结果 [(surface, pos_tag0), ...]
        name_set: 兼容参数（人名不收录：名詞-固有名詞-人名 POS 直接排除，不再加权）
        name_counter: 兼容参数（人名不收录，不再使用）
        existing_dict_map: 已有 GPT 字典 {src: (dst, note)}，用于收录门槛与跳过
        han_allowlist: 汉字普通名词收录白名单（默认空集，H 术语等可配置）
        skip_existing: 已有 GPT 字典词不发送 AI 翻译（跳过，直接沿用字典），默认 True
        ban_words: 太过平常的词汇黑名单（代词/语气词/口语等），不发送 AI，默认空集

    Returns:
        (final_terms, stats)：final_terms 为 [(src, freq, category), ...]，
        category ∈ {固有名詞, 片假名普通名词, 汉字词(白名单/字典), 字母组合, 复合词}；
        stats 含 汉字丢弃/黑名单丢弃/低频假名固有名詞丢弃/组合覆盖剔除/已有字典跳过。
    """
    existing = existing_dict_map or {}
    allow = han_allowlist or set()
    ban = ban_words or set()
    word_counter: collections.Counter = collections.Counter()
    word_pos: Dict[str, str] = {}
    word_is_kata: Dict[str, bool] = {}

    def _token_ok(surf: str, tag: str) -> bool:
        """单 token 基础过滤（单 token 与复合词成分共用）：POS/长度/构成/拟声/人名排除。"""
        if tag is None:
            return False
        if tag.split("-")[0] not in _TERMS_POS_ALLOW:
            return False
        if any(b in tag for b in _TERMS_POS_SUB_BLOCK):
            return False
        if tag.startswith("名詞-固有名詞-人名"):
            return False  # 人名不收录（全局分析/name 人名替换表已覆盖）
        if len(surf) <= 1:
            return False
        if _is_alpha_abbr(surf):
            return True  # 大写字母组合（ＣＦ 等）单独规则
        has_han = any("\u4e00" <= ch <= "\u9fff" for ch in surf)
        if not (has_han or _has_katakana(surf)):
            return False
        if all(ch in "ー・" for ch in surf):
            return False
        if _is_onomatopoeia(surf):
            return False
        return True

    for surf, tag in tokens:
        if not _token_ok(surf, tag):
            continue
        word_counter[surf] += 1
        if surf not in word_pos:
            word_pos[surf] = tag
            has_han = any("\u4e00" <= ch <= "\u9fff" for ch in surf)
            word_is_kata[surf] = _is_katakana_only(surf) or (_is_pure_kana(surf) and not has_han)

    # 复合词组合统计：名詞+名詞（2-gram）；名詞+连接记号(★＆&・)+名詞（3-gram，排除「」）
    comp_counter: collections.Counter = collections.Counter()
    comp_parts: Dict[str, List[str]] = {}
    for i in range(len(tokens) - 1):
        s1, t1 = tokens[i]
        s2, t2 = tokens[i + 1]
        if _token_ok(s1, t1) and _token_ok(s2, t2):
            key = s1 + s2
            comp_counter[key] += 1
            comp_parts.setdefault(key, [s1, s2])
    for i in range(len(tokens) - 2):
        s1, t1 = tokens[i]
        s2, t2 = tokens[i + 1]
        s3, t3 = tokens[i + 2]
        if (
            t2 is not None and t2.startswith("補助記号") and s2 in "★＆&・"
            and _token_ok(s1, t1) and _token_ok(s3, t3)
        ):
            key = s1 + s2 + s3
            comp_counter[key] += 1
            comp_parts.setdefault(key, [s1, s2, s3])

    # 组合过滤：freq≥2、非拟声、非黑名单、无 H 词成分；汉字复合词都收（用户决策）
    comp_final: List[Tuple[str, int]] = []
    for comp, freq in comp_counter.items():
        if freq < 2 or _is_onomatopoeia(comp) or comp in ban:
            continue
        if any(p in H_WORDS_LIST for p in comp_parts[comp]):
            continue
        comp_final.append((comp, freq))

    # 组合优先：子 token 被组合覆盖后独立频次过低（<2）则剔除（オバ/グラ 类碎片）
    covered: collections.Counter = collections.Counter()
    for comp, freq in comp_final:
        for p in comp_parts[comp]:
            covered[p] += freq

    final_terms: List[Tuple[str, int, str]] = []
    stats: Dict[str, int] = {
        "固有名詞": 0, "片假名普通名词": 0, "汉字词(白名单/字典)": 0, "字母组合": 0,
        "复合词": 0, "汉字丢弃": 0, "黑名单丢弃": 0, "低频假名固有名詞丢弃": 0,
        "组合覆盖剔除": 0, "已有字典跳过": 0,
    }

    def _add(w: str, c: int, category: str) -> None:
        final_terms.append((w, c, category))
        stats[category] += 1

    for w, c in word_counter.items():
        indep = c - covered.get(w, 0)
        if covered.get(w, 0) and indep < 2:
            # 组合优先：词主要由复合词承载，碎片不单独翻译
            stats["组合覆盖剔除"] += 1
            continue
        if w in ban:
            stats["黑名单丢弃"] += 1
        elif _is_alpha_abbr(w):
            _add(w, indep, "字母组合")
        elif word_pos[w].startswith("名詞-固有名詞"):
            if indep == 1 and _is_pure_kana(w):
                stats["低频假名固有名詞丢弃"] += 1
                continue
            _add(w, indep, "固有名詞")
        elif word_is_kata[w]:
            if indep >= 2 or w in existing:
                _add(w, indep, "片假名普通名词")
        elif w in existing or w in allow:
            _add(w, indep, "汉字词(白名单/字典)")
        else:
            stats["汉字丢弃"] += 1

    # 复合词追加（与单 token 一并参与排序/截断）
    for comp, freq in comp_final:
        _add(comp, freq, "复合词")

    # 已有 GPT 字典词跳过：不重复发送 AI 翻译，直接沿用字典（用户决策）
    if skip_existing:
        kept: List[Tuple[str, int, str]] = []
        skipped = 0
        for t in final_terms:
            if t[0] in existing:
                skipped += 1
            else:
                kept.append(t)
        final_terms = kept
        stats["已有字典跳过"] = skipped

    return final_terms, stats


@register_engine("GenDic")
class GenDic(BaseEngine):
    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool],
        token_pool: COpenAITokenPool,
    ) -> None:
        super().__init__(config, eng_type, proxy_pool, token_pool)
        self.dic_counter = collections.Counter()
        self.dic_list = []
        self.dic_votes = collections.defaultdict(collections.Counter)
        # 兼容 YAML 中写成字符串（如 workersPerProject: '4'）的情况；
        # 复用基类 _coerce_positive_int（非法值回退默认、0 抬为 1，避免 Semaphore(0) 死锁）
        _w = config.getKey("workersPerProject")
        self.wokers = self._coerce_positive_int(_w, 1)
        self.counter_lock = Lock()
        self.list_lock = Lock()
        self.progress_lock = Lock()
        self.progress_display_name = "GenDic 术语提取"
        self.progress_cache_key = "gendic_progress"
        self.progress_append_path = ""
        self.trans_prompt = GENDIC_PROMPT
        self.system_prompt = GENDIC_SYSTEM
        self.init_chatbot(eng_type, config)
        self._apply_internal_prompt_template_overrides()
        backend_cfg = config.getBackendConfigSection("OpenAI-Compatible")
        raw_retry = backend_cfg.get("genDicMaxApiRetries", 6)
        self.gendic_max_api_retries = self._coerce_positive_int(raw_retry, 6)
        # terms/segments 模式配置（设计文档第七~九节）：非法值回退默认，0 表示不截断。
        # 键位于 internals 段，展平后带 internals. 前缀（见 ConfigHelper._flatten_dotted_keys）
        self.gendic_mode = config.getKey("internals.gendic.mode", "terms")
        if self.gendic_mode not in ("terms", "segments"):
            LOGGER.warning("gendic.mode 非法值 %s，回退 terms", self.gendic_mode)
            self.gendic_mode = "terms"
        self.gendic_batch_size = self._coerce_positive_int(config.getKey("internals.gendic.batch_size"), 50)
        # YAML 的 on/off 会解析为 bool（True/False），兼容字符串与 bool 两种写法
        raw_ctx = config.getKey("internals.gendic.context", "on")
        if isinstance(raw_ctx, str):
            self.gendic_context = raw_ctx.lower() in ("on", "true", "1", "yes")
        else:
            self.gendic_context = bool(raw_ctx)
        raw_max = config.getKey("internals.gendic.max_terms", 128)
        try:
            self.gendic_max_terms = int(raw_max) if raw_max else 0
        except Exception:
            self.gendic_max_terms = 2000
        if self.gendic_max_terms < 0:
            self.gendic_max_terms = 2000
        # 每词附带的示例句数量（2-10），非法值回退 3
        raw_samples = config.getKey("internals.gendic.context_samples", 3)
        try:
            samples = int(raw_samples) if raw_samples else 3
        except Exception:
            samples = 3
        self.gendic_context_samples = samples if 2 <= samples <= 10 else 3
        # 汉字普通名词收录白名单（默认空集 = 不收录，符合"H 术语不收录"决策）
        raw_allow = config.getKey("internals.gendic.han_allowlist", None)
        self.gendic_han_allowlist = set(raw_allow) if isinstance(raw_allow, (list, set, tuple)) else set()
        # 平常词黑名单（代词/语气词/口语等，默认空集，用户按需添加）
        raw_ban = config.getKey("internals.gendic.ban_words", None)
        self.gendic_ban_words = set(raw_ban) if isinstance(raw_ban, (list, set, tuple)) else set()

    def _load_existing_gpt_terms(self) -> Dict[str, Tuple[str, str]]:
        result_path = os.path.join(self.pj_config.getProjectDir(), "项目GPT字典-生成.txt")
        dict_cfg = self.pj_config.getDictCfgSection()
        gpt_dic_list = dict_cfg.get("gpt.dict", []) if dict_cfg else []
        default_dic_dir = dict_cfg.get("defaultDictFolder", "") if dict_cfg else ""
        dic_paths = initDictList(gpt_dic_list, default_dic_dir, self.pj_config.getProjectDir())

        existing_terms: Dict[str, Tuple[str, str]] = {}
        for dic_path in dic_paths:
            if os.path.abspath(dic_path) == os.path.abspath(result_path):
                continue
            dic_obj = CGptDict([dic_path])
            dic_list = getattr(dic_obj, "_dic_list", None) or []
            for dic in dic_list:
                if dic.search_word and dic.replace_word and dic.search_word not in existing_terms:
                    existing_terms[dic.search_word] = (dic.replace_word, getattr(dic, "note", "") or "")
        return existing_terms

    def _update_runtime(self, **kwargs: Any) -> None:
        try:
            from GalTransl.server import update_runtime_status

            update_runtime_status(self.runtime_project_dir, **kwargs)
        except Exception:
            return

    def _load_existing_generated_terms(self, result_path: str) -> Set[str]:
        terms: Set[str] = set()
        if not os.path.exists(result_path):
            return terms
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    sp = line.split("\t")
                    if sp:
                        src = sp[0].strip()
                        if src:
                            terms.add(src)
        except Exception:
            pass
        return terms

    def _build_final_list(
        self,
        word_counter: Optional[Dict[str, int]] = None,
        name_set: Optional[Set[str]] = None,
        existing_file_terms: Optional[Set[str]] = None,
    ) -> Tuple[List[List[str]], int]:
        counters = word_counter or {}
        names = name_set or set()
        file_terms = existing_file_terms or set()

        self.dic_list.sort(key=lambda x: self.dic_counter[x[0]], reverse=True)

        for i in range(len(self.dic_list)):
            src = self.dic_list[i][0]
            if src in self.dic_votes and self.dic_votes[src]:
                (best_dst, best_note), _ = self.dic_votes[src].most_common(1)[0]
                self.dic_list[i][1] = best_dst
                self.dic_list[i][2] = best_note

        existing_src_terms = set(getattr(self, "existing_dict_map", {}).keys())
        existing_src_terms.update(file_terms)
        final_set: Dict[str, List[str]] = {}
        duplicates = 0
        for item in self.dic_list:
            src = item[0]
            note = item[2]
            if src in final_set:
                duplicates += 1
                continue
            if src in existing_src_terms:
                duplicates += 1
                continue
            if "NULL" in src:
                continue
            if "拟声" in note:
                continue
            if src in H_WORDS_LIST:
                continue
            if "（" not in src and "（" in item[1]:
                continue

            if self.dic_counter[src] > 1:
                final_set[src] = item
            elif "人名" in item[2]:
                final_set[src] = item
            elif "地名" in item[2]:
                final_set[src] = item
            elif src in counters:
                final_set[src] = item
            elif src in names:
                final_set[src] = item

        return list(final_set.values()), duplicates

    def _save_generated_dictionary(self, final_list: List[List[str]], result_path: Optional[str] = None) -> str:
        # 覆盖写：每次运行全新生成（避免追加导致词条累积重复、突破 max_terms 上限）；
        # 历史人工维护请使用「项目GPT字典.txt」。
        path = result_path or os.path.join(self.pj_config.getProjectDir(), "项目GPT字典-生成.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 格式为日文[Tab]中文[Tab]解释(可不写)，参考项目wiki\n")
            for item in final_list:
                f.write(item[0] + "\t" + item[1] + "\t" + item[2] + "\n")
        return path

    def _prepare_runtime_progress(self, total_tasks: int) -> None:
        cache_dir = self.pj_config.getCachePath()
        os.makedirs(cache_dir, exist_ok=True)
        self.progress_append_path = os.path.join(
            cache_dir, f"{self.progress_cache_key}.append.jsonl"
        )
        try:
            if os.path.exists(self.progress_append_path):
                os.remove(self.progress_append_path)
        except Exception:
            pass

        self._update_runtime(
            stage="GenDic 术语提取中",
            current_file="准备生成任务",
            workers_active=0,
            workers_configured=int(self.wokers or 1),
            file_totals={self.progress_display_name: int(total_tasks)},
            cache_file_display_map={self.progress_cache_key: self.progress_display_name},
        )

    def _append_runtime_progress(self, task_index: int, success: bool, message: str = "") -> None:
        if not self.progress_append_path:
            return
        entry = {
            "__cache_key": f"gendic-task-{int(task_index)}",
            "pre_dst": "OK" if success else FAILED_PREFIX,
            "problem": "" if success else (message or "GenDic 任务失败"),
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self.progress_lock:
            with open(self.progress_append_path, "a", encoding="utf-8") as fp:
                fp.write(line)
                fp.write("\n")

    def _cleanup_runtime_progress(self) -> None:
        if not self.progress_append_path:
            return
        try:
            if os.path.exists(self.progress_append_path):
                os.remove(self.progress_append_path)
        except Exception:
            pass
        finally:
            self.progress_append_path = ""

    def _record_runtime_success(self, index: int, source_preview: str, translation_preview: str) -> None:
        super()._record_runtime_success(
            self.progress_display_name,
            index=int(index),
            source_preview=source_preview,
            translation_preview=translation_preview,
            trans_by=self.get_last_chatbot_model() or "GenDic",
        )

    async def llm_gen_dic(self, text: str, name_list: list[str] = [], task_index: int = 0) -> bool:
        self._check_stop_requested()
        hint = "无"
        name_hit = []
        for name in name_list:
            self._check_stop_requested()
            if name in text:
                name_hit.append(name)

        parts: List[str] = []
        existing_dict_map = getattr(self, "existing_dict_map", None) or {}
        if existing_dict_map:
            appeared = {
                k: v for k, v in existing_dict_map.items()
                if k in text
            }
            if appeared:
                lines = [f"{src}\t{dst}\t{note}" for src, (dst, note) in appeared.items()]
                parts.append("以下词汇已有确定翻译，请严格保持一致，不要重复提取：\n" + "\n".join(lines))
        if name_hit:
            parts.append("输入文本中的这些词是人名，要加入术语表: \n" + "\n".join(name_hit))
        if parts:
            hint = "\n\n".join(parts)

        prompt = self.trans_prompt
        if "{input}" in prompt:
            prompt = prompt.replace("{input}", text)
        if "{hint}" in prompt:
            prompt = prompt.replace("{hint}", hint)

        self._check_stop_requested()
        try:
            rsp, token = await self.ask_chatbot(
                prompt=prompt,
                system=self.system_prompt,
                file_name=self.progress_display_name,
                max_retry_count=self.gendic_max_api_retries,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            error_message = (
                f"GenDic 分片 {task_index} LLM请求失败，已重试{self.gendic_max_api_retries}次，放弃该分片: {e}"
            )
            LOGGER.error(error_message)
            self._record_runtime_error(
                kind="api",
                message=error_message,
                filename=self.progress_display_name,
                index_range=str(task_index),
                retry_count=self.gendic_max_api_retries,
                model=getattr(token, "model_name", "") if 'token' in locals() else None,
            )
            return False

        if should_print_translation_logs(self.pj_config):
            print(rsp)

        if not isinstance(rsp, str) or rsp.strip() == "":
            warning_message = f"GenDic 分片 {task_index} 返回空响应，放弃该分片"
            LOGGER.warning(warning_message)
            self._record_runtime_error(
                kind="parse",
                message=warning_message,
                filename=self.progress_display_name,
                index_range=str(task_index),
                level="warning",
            )
            return False

        lines = rsp.split("\n")
        valid_entries = []
        for line in lines:
            self._check_stop_requested()
            sp = line.split("\t")
            if len(sp) < 3:
                continue
            if "日文" in sp[0]:
                continue
            src = sp[0].strip()
            dst = sp[1].strip()
            note = sp[2].strip()
            if len(note) > 20:
                note = ""
            if not src or not dst:
                continue
            if src == "NULL" and dst == "NULL":
                return True
            valid_entries.append((src, dst, note))

        if not valid_entries:
            warning_message = f"GenDic 分片 {task_index} 未解析到有效词条，放弃该分片"
            LOGGER.warning(warning_message)
            self._record_runtime_error(
                kind="parse",
                message=warning_message,
                filename=self.progress_display_name,
                index_range=str(task_index),
                level="warning",
            )
            return False

        for idx, (src, dst, note) in enumerate(valid_entries):
            if idx < 3:
                self._record_runtime_success(
                    index=task_index,
                    source_preview=src,
                    translation_preview=f"{dst}｜{note}",
                )
            with self.counter_lock:
                self.dic_counter[src] += 1
                self.dic_votes[src][(dst, note)] += 1
                if self.dic_counter[src] == 1:
                    with self.list_lock:
                        self.dic_list.append([src, dst, note])
                elif self.dic_counter[src] == 2:
                    if should_print_translation_logs(self.pj_config):
                        print(f"{src}\t{dst}\t{note}")
        return True

    def _load_tokenizer(self):
        """加载 vaporetto 分词模型（解压到临时目录），失败返回 None。"""
        import tempfile

        try:
            import vaporetto

            tmp_dir = tempfile.gettempdir()
            model_path = os.path.join(tmp_dir, "bccwj-suw+unidic_pos+pron.model")
            if not os.path.exists(model_path):
                zst_path = "./res/bccwj-suw+unidic_pos+pron.model.xz"
                decompress_file_lzma(zst_path, model_path)
            with open(model_path, "rb") as fp:
                model = fp.read()
            return vaporetto.Vaporetto(model, predict_tags=True)
        except Exception as e:
            LOGGER.error(e)
            LOGGER.error("载入分词模型失败，请尝试重启程序")
            try:
                os.remove(model_path)
            except Exception:
                pass
            return None

    def _extract_terms_from_project(self, json_list: list) -> Optional[Tuple[List[Tuple[str, int, str]], Dict[str, int], Set[str]]]:
        """terms 模式本地提取：全文分词 + extract_terms_from_tokens（人名不收录，name_set 传空）。
        分词模型加载失败返回 None（调用方按硬失败处理，与 segments 模式一致）。"""
        tokenizer = self._load_tokenizer()
        if tokenizer is None:
            return None
        full_text = "".join(
            (item.get("name", "") + item.get("message", "") + "\n")
            if item.get("name")
            else (item.get("message", "") + "\n")
            for item in json_list
        )
        tokens = [(t.surface(), t.tag(0)) for t in tokenizer.tokenize(full_text)]
        final_terms, stats = extract_terms_from_tokens(
            tokens,
            set(),
            {},
            existing_dict_map=getattr(self, "existing_dict_map", None),
            han_allowlist=getattr(self, "gendic_han_allowlist", None),
            ban_words=getattr(self, "gendic_ban_words", None),
        )
        return final_terms, stats, set()

    @staticmethod
    def _find_contexts(word: str, json_list: list, max_samples: int = 3) -> List[str]:
        """返回含词的前 max_samples 个完整句（≤80 字/句，去重），无则空列表。"""
        seen: Set[str] = set()
        out: List[str] = []
        for item in json_list:
            text = item.get("message", "")
            if word in text:
                s = text.replace("\r\n", " ").strip()[:80]
                if s not in seen:
                    seen.add(s)
                    out.append(s)
                if len(out) >= max_samples:
                    break
        return out

    @staticmethod
    def _sort_and_truncate_terms(final_terms: List[Tuple[str, int, str]], max_terms: int = 0) -> List[Tuple[str, int, str]]:
        """按类别优先级（固有名詞>普通术语[片假名/复合词/字母组合按频次]>汉字词）+ 频次降序排序；
        max_terms>0 时截断：固有名詞优先保底，但总条目不超过 max_terms（硬上限，
        防保底类别本身超限导致生成字典过大影响后续翻译）。"""
        cat_rank = {
            "固有名詞": 0,
            "片假名普通名词": 1, "复合词": 1, "字母组合": 1,
            "汉字词(白名单/字典)": 2,
        }
        ordered = sorted(final_terms, key=lambda t: (cat_rank.get(t[2], 9), -t[1]))
        if max_terms > 0 and len(ordered) > max_terms:
            protected = [t for t in ordered if t[2] == "固有名詞"]
            if len(protected) >= max_terms:
                ordered = protected[:max_terms]
            else:
                rest = [t for t in ordered if t[2] != "固有名詞"]
                ordered = protected + rest[:max_terms - len(protected)]
        return ordered

    @staticmethod
    def _parse_terms_response(rsp: str, input_words: List[str]) -> Tuple[Dict[str, Tuple[str, str]], List[str]]:
        """解析 terms TSV 响应：按输入词精确匹配，未命中做去空白归一化容错；
        输出行日文不在输入词表则丢弃（grounding 防幻觉）。返回 (matched, extra)。"""
        input_set = set(input_words)
        norm_map: Dict[str, str] = {}
        for w in input_words:
            norm = w.replace(" ", "").replace("\u3000", "")
            norm_map.setdefault(norm, w)
        matched: Dict[str, Tuple[str, str]] = {}
        extra: List[str] = []
        for line in rsp.splitlines():
            line = line.strip()
            if not line or line.startswith("```") or "日文原词" in line:
                continue
            sp = line.split("\t")
            if len(sp) < 2:
                continue
            src = sp[0].strip()
            dst = sp[1].strip()
            if not src or not dst or dst == "（无法翻译）":
                continue
            key = src if src in input_set else norm_map.get(src.replace(" ", "").replace("\u3000", ""))
            if key is not None and key not in matched:
                note = sp[2].strip() if len(sp) >= 3 else ""
                if len(note) > 20:
                    note = ""
                matched[key] = (dst, note)
            else:
                extra.append(line)
        return matched, extra

    async def llm_translate_terms_batch(
        self, batch_terms: List[Tuple[str, int, str]], context_hint: str, task_index: int
    ) -> Dict[str, Tuple[str, str]]:
        """terms 模式单批翻译：构造 GENDIC_TERMS_PROMPT → ask_chatbot → 解析。"""
        self._check_stop_requested()
        lines = [f"{i + 1}. {w}" for i, (w, _, _) in enumerate(batch_terms)]
        prompt = GENDIC_TERMS_PROMPT.replace("{terms}", "\n".join(lines))
        if context_hint:
            prompt = prompt.replace("{context_hint}", context_hint)
        else:
            # context 关闭：整段移除「上下文提示」块，避免残留空标题
            prompt = re.sub(r"## 上下文提示[^\n]*\n\{context_hint\}\n\n", "", prompt)
        self._check_stop_requested()
        try:
            rsp, token = await self.ask_chatbot(
                prompt=prompt,
                system=GENDIC_SYSTEM,
                file_name=self.progress_display_name,
                max_retry_count=self.gendic_max_api_retries,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            error_message = (
                f"[GenDic][terms] 批次 {task_index} LLM 请求失败，已重试{self.gendic_max_api_retries}次，放弃该批: {e}"
            )
            LOGGER.error(error_message)
            self._record_runtime_error(
                kind="api",
                message=error_message,
                filename=self.progress_display_name,
                index_range=str(task_index),
                retry_count=self.gendic_max_api_retries,
                model=getattr(token, "model_name", "") if "token" in locals() else None,
            )
            return {}
        if not isinstance(rsp, str) or not rsp.strip():
            LOGGER.warning(f"[GenDic][terms] 批次 {task_index} 返回空响应，放弃该批")
            return {}
        input_words = [w for w, _, _ in batch_terms]
        matched, _ = self._parse_terms_response(rsp, input_words)
        return matched

    async def _batch_translate_terms(self, json_list: list) -> bool:
        """terms 模式主流程：本地提取 → 分批逐词翻译 → 缺失词二次补翻 → 落盘。"""
        from GalTransl.Service import JobCancelledError

        cancelled_error: Optional[JobCancelledError] = None
        self._check_stop_requested()
        try:
            self._update_runtime(stage="GenDic 分词处理中", current_file="terms 模式提取")
            existing_dict_map = self._load_existing_gpt_terms()
            self.existing_dict_map = existing_dict_map

            final_terms, stats, _name_set = self._extract_terms_from_project(json_list)
            if final_terms is None:
                LOGGER.error("[GenDic][terms] 分词模型加载失败，术语表生成失败")
                self._update_runtime(stage="", current_file="", workers_active=0)
                return False
            LOGGER.info(
                f"[GenDic][terms] 本地提取：固有名詞 {stats.get('固有名詞', 0)} / "
                f"片假名普通名词 {stats.get('片假名普通名词', 0)} / 复合词 {stats.get('复合词', 0)} / "
                f"字母组合 {stats.get('字母组合', 0)} / 汉字词 {stats.get('汉字词(白名单/字典)', 0)} / "
                f"汉字丢弃 {stats.get('汉字丢弃', 0)} / 已有字典跳过 {stats.get('已有字典跳过', 0)} / "
                f"黑名单丢弃 {stats.get('黑名单丢弃', 0)} / 低频假名固有名詞丢弃 {stats.get('低频假名固有名詞丢弃', 0)} / "
                f"组合覆盖剔除 {stats.get('组合覆盖剔除', 0)}，候选 {len(final_terms)} 词"
            )
            ordered = self._sort_and_truncate_terms(
                final_terms, max_terms=int(getattr(self, "gendic_max_terms", 0) or 0)
            )
            if not ordered:
                LOGGER.warning("[GenDic][terms] 提取结果为空，跳过生成")
                self._update_runtime(stage="", current_file="", workers_active=0)
                return True

            batch_size = max(1, int(getattr(self, "gendic_batch_size", 50)))
            batches = [ordered[i:i + batch_size] for i in range(0, len(ordered), batch_size)]
            self._prepare_runtime_progress(len(batches))
            LOGGER.info(f"[GenDic][terms] 共 {len(ordered)} 词，分 {len(batches)} 批，workers={self.wokers}")

            # 示例句缓存：每词只查一次（收集前 context_samples 个含词句），二次补翻复用
            ctx_cache: Dict[str, List[str]] = {}
            samples = max(2, min(10, int(getattr(self, "gendic_context_samples", 3) or 3)))
            if getattr(self, "gendic_context", True):
                for w, _, _ in ordered:
                    ctx_cache[w] = self._find_contexts(w, json_list, samples)

            def _ctx_hint(batch: list) -> str:
                if not getattr(self, "gendic_context", True):
                    return ""
                return "\n".join(
                    f"{i}. {w}：{'｜'.join(ctx_cache.get(w, []))}" if ctx_cache.get(w) else f"{i}. {w}"
                    for i, (w, _, _) in enumerate(batch, 1)
                )

            sem = asyncio.Semaphore(self.wokers)
            results: Dict[str, Tuple[str, str]] = {}
            missing: Set[str] = set(w for w, _, _ in ordered)
            completed = 0

            async def process_batch(batch: list, task_index: int) -> None:
                nonlocal completed
                async with sem:
                    self._check_stop_requested()
                    try:
                        matched = await self.llm_translate_terms_batch(batch, _ctx_hint(batch), task_index)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        if isinstance(e, JobCancelledError):
                            raise
                        LOGGER.error(f"[GenDic][terms] 批次 {task_index} 处理异常: {e}")
                        return
                    for w, pair in matched.items():
                        results[w] = pair
                        missing.discard(w)
                    completed += 1
                    self._append_runtime_progress(task_index, bool(matched))
                    self._update_runtime(
                        stage="GenDic 术语提取中",
                        current_file=f"已完成 {completed}/{len(batches)} 批",
                        workers_active=max(0, self.wokers - completed),
                    )

            tasks = [asyncio.create_task(process_batch(b, i)) for i, b in enumerate(batches)]
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

            # 二次补翻：缺失词单独成批再请求一轮
            if missing:
                LOGGER.warning(f"[GenDic][terms] {len(missing)} 词首次未匹配，发起二次补翻")
                retry_terms = [(w, 0, "术语") for w in sorted(missing)]
                retry_tasks = [
                    asyncio.create_task(process_batch(retry_terms[i:i + batch_size], len(batches) + i // batch_size))
                    for i in range(0, len(retry_terms), batch_size)
                ]
                await asyncio.gather(*retry_tasks)

        except JobCancelledError as ex:
            cancelled_error = ex
            self._update_runtime(stage="GenDic 停止处理中", current_file="整理当前结果", workers_active=0)
        finally:
            self._cleanup_runtime_progress()

        if not results:
            LOGGER.warning("[GenDic][terms] 全部批次未解析到词条，未生成字典")
            self._update_runtime(stage="", current_file="", workers_active=0)
            if cancelled_error is not None:
                raise cancelled_error
            return True

        # 落盘前过滤（真实测试暴露）：拟声 note / H 词表 / NULL / 空 note 的未翻译回显
        dropped = 0
        final_list: List[List[str]] = []
        for w, (dst, note) in results.items():
            if _is_term_droppable(w, dst, note):
                dropped += 1
                continue
            final_list.append([w, dst, note])
        if dropped:
            LOGGER.warning(f"[GenDic][terms] 落盘前过滤 {dropped} 条（拟声/H词/NULL/未翻译回显）")
        result_path = self._save_generated_dictionary(final_list)
        added = len(final_list)
        setattr(self.pj_config, "gendic_added_count", added)
        setattr(self.pj_config, "gendic_duplicated_count", 0)
        if cancelled_error is not None:
            setattr(self.pj_config, "gendic_partial_saved", True)
            LOGGER.info(f"[GenDic][terms] 已停止，使用当前结果生成字典，新增{added}条，保存到{result_path}")
            self._update_runtime(stage="", current_file="", workers_active=0)
            raise cancelled_error
        LOGGER.info(f"[GenDic][terms] 字典生成完成，新增{added}条，保存到{result_path}")
        try:
            self.pj_config.register_gpt_dict_file("项目GPT字典-生成.txt")
        except Exception as reg_err:
            LOGGER.warning(f"GenDic 字典登记到配置失败（界面可能看不到）: {reg_err}")
        self._update_runtime(stage="", current_file="", workers_active=0)
        return True

    async def batch_translate(
        self,
        json_list: list,
    ) -> bool:
        from GalTransl.Service import JobCancelledError

        # terms/segments 双模式分派（设计文档第八节，默认 terms）
        if getattr(self, "gendic_mode", "terms") == "terms":
            return await self._batch_translate_terms(json_list)

        word_counter: Dict[str, int] = {}
        name_set: Set[str] = set()
        cancelled_error: Optional[JobCancelledError] = None

        try:
            self._check_stop_requested()
            self._update_runtime(stage="GenDic 分词处理中", current_file="准备分词")
            with terminal_progress(should_print_translation_logs(self.pj_config), title="载入分词……") as bar:
                # get tmp dir
                import tempfile

                tmp_dir = tempfile.gettempdir()
                model_path = os.path.join(tmp_dir, "bccwj-suw+unidic_pos+pron.model")
                if not os.path.exists(model_path):
                    zst_path = "./res/bccwj-suw+unidic_pos+pron.model.xz"
                    decompress_file_lzma(zst_path, model_path)
                bar()
                import vaporetto

                try:
                    with open(model_path, "rb") as fp:
                        model = fp.read()
                    tokenizer = vaporetto.Vaporetto(model, predict_tags=True)
                except Exception as e:
                    LOGGER.error(e)
                    LOGGER.error("载入分词模型失败，请尝试重启程序")
                    os.remove(model_path)
                    return False
                bar()

                word_counter = collections.Counter()
                segment_list = []
                segment_words_list = []
                name_set = set()
                max_len = 512
                tmp_text = ""
                for item in json_list:
                    self._check_stop_requested()
                    if len(tmp_text) > max_len:
                        segment_list.append(tmp_text)
                        tmp_text = ""

                    if "name" in item and item["name"] != "":
                        name_set.add(item["name"])
                        tmp_text += item["name"] + item["message"] + "\n"
                        word_counter[item["name"]] += 2
                    else:
                        tmp_text += item["message"] + "\n"

                segment_list.append(tmp_text)
                bar.title = "处理分词……"

                # 收集已有 GPT 字典翻译（排除当前生成文件），用于提示与最终结果去重
                existing_dict_map = self._load_existing_gpt_terms()
                self.existing_dict_map = existing_dict_map
                all_text = "\n".join(segment_list)

                for item in segment_list:
                    self._check_stop_requested()
                    tmp_words = set()
                    tokens = tokenizer.tokenize(item)
                    for token in tokens:
                        self._check_stop_requested()
                        surf = token.surface()
                        tag = token.tag(0)
                        if len(surf) <= 1:
                            continue
                        if is_all_chinese(surf):
                            continue
                        if tag is None:
                            if contains_katakana(surf):
                                tmp_words.add(surf)
                                word_counter[surf] += 1

                    # 正则补充层：片假名序列、引号/括号内词组
                    for w in _extract_regex_terms(item):
                        tmp_words.add(w)
                        word_counter[w] += 1

                    # 名字强制保留到 Set Cover（确保仅出现一次的名字也被覆盖）
                    for name in name_set:
                        if name in item and len(name) >= 2:
                            tmp_words.add(name)
                            if word_counter[name] < 2:
                                word_counter[name] += 1

                    segment_words_list.append(tmp_words)
                    bar()

            # 放宽过滤：名字和纯片假名词允许出现1次，其他仍需>=2
            word_counter = {
                word: count for word, count in word_counter.items()
                if count >= 2 or word in name_set or _is_katakana_only(word)
            }
            segment_words_list_new = []
            for item in segment_words_list:
                self._check_stop_requested()
                item_new = set()
                for word in item:
                    if word in word_counter:
                        item_new.add(word)
                segment_words_list_new.append(item_new)

            index_list = solve_sentence_selection(segment_words_list_new, max_select=128, name_set=name_set)
            self._prepare_runtime_progress(len(index_list))
            LOGGER.info(f"启动{self.wokers}个工作线程，共{len(index_list)}个任务")
            sem = asyncio.Semaphore(self.wokers)
            completed_tasks = 0

            async def process_item_async(idx):
                async with sem:
                    self._check_stop_requested()
                    try:
                        item = segment_list[idx]
                        ok = await self.llm_gen_dic(item, name_list=list(name_set), task_index=idx)
                        return idx, bool(ok), ""
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        from GalTransl.Service import JobCancelledError

                        if isinstance(e, JobCancelledError):
                            raise
                        LOGGER.error(f"处理任务时出错: {e}")
                        return idx, False, str(e)

            tasks = [asyncio.create_task(process_item_async(idx)) for idx in index_list]

            with terminal_progress(
                should_print_translation_logs(self.pj_config),
                title="生成中……",
                total=len(tasks),
            ) as bar:
                self.pj_config.bar = bar
                self._update_runtime(
                    stage="GenDic 术语提取中",
                    current_file="开始并发生成",
                    workers_active=int(self.wokers or 1),
                )
                try:
                    for f in asyncio.as_completed(tasks):
                        idx, ok, error_message = await f
                        self._check_stop_requested()
                        completed_tasks += 1
                        self._append_runtime_progress(idx, ok, error_message)
                        remaining = len(tasks) - completed_tasks
                        self._update_runtime(
                            stage="GenDic 术语提取中",
                            current_file=f"已完成 {completed_tasks}/{len(tasks)}",
                            workers_active=min(int(self.wokers or 1), remaining),
                        )
                        bar()
                except BaseException:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise

        except JobCancelledError as ex:
            cancelled_error = ex
            self._update_runtime(stage="GenDic 停止处理中", current_file="整理当前结果", workers_active=0)
        finally:
            self._cleanup_runtime_progress()

        result_path = os.path.join(self.pj_config.getProjectDir(), "项目GPT字典-生成.txt")
        existing_file_terms = self._load_existing_generated_terms(result_path)
        final_list, duplicates = self._build_final_list(
            word_counter=word_counter, name_set=name_set, existing_file_terms=existing_file_terms
        )
        result_path = self._save_generated_dictionary(final_list, result_path)
        added_count = len(final_list)
        setattr(self.pj_config, "gendic_added_count", added_count)
        setattr(self.pj_config, "gendic_duplicated_count", duplicates)

        if cancelled_error is not None:
            setattr(self.pj_config, "gendic_partial_saved", True)
            LOGGER.info(f"GenDic 已停止，使用当前结果生成字典，新增{added_count}条，重复{duplicates}条，保存到{result_path}")
            self._update_runtime(stage="", current_file="", workers_active=0)
            raise cancelled_error

        LOGGER.info(f"字典生成完成，新增{added_count}条，重复{duplicates}条，保存到{result_path}")

        # 登记到 config.yaml 的 dictionary.gpt.dict，使字典界面可显示、后续翻译阶段会加载。
        # 异常绝不影响已生成的字典结果。
        try:
            self.pj_config.register_gpt_dict_file("项目GPT字典-生成.txt")
        except Exception as _reg_err:
            LOGGER.warning(f"GenDic 字典登记到配置失败（界面可能看不到）: {_reg_err}")

        self._update_runtime(stage="", current_file="", workers_active=0)
        return True


def solve_sentence_selection(sentences: list[list[str]], max_select: int = 128, name_set: Optional[set] = None) -> list[list[str]]:
    """
    加权贪心集合覆盖 + 逆向精简。

    策略：
    1. 词权重 = 1 / doc_freq，越稀有的词权重越高；
    2. name_set 中的词额外乘高系数，确保名字相关切片优先入选；
    3. 贪心阶段每次选带来最大加权新覆盖的句子；
    4. 若选出的句子超过 max_select，逆向精简：
       计算每个句子的边际贡献（该句独有的词加权总和），
       若移除会导致名字词完全丢失，则大幅抬高边际贡献避免被剔除，
       循环剔除边际贡献最小的句子直到 <= max_select。
    """
    if not sentences:
        return []

    name_set = name_set or set()

    # 1) 词频
    doc_freq = collections.Counter()
    for s in sentences:
        for w in s:
            doc_freq[w] += 1

    # 2) 词权重函数
    def _weight(word: str) -> float:
        w = 1.0 / doc_freq[word]
        if word in name_set:
            w *= 5.0
        return w

    # 3) 加权贪心选择
    covered = set()
    selected = []
    remaining = set(range(len(sentences)))

    while remaining and len(selected) < max_select:
        best_idx = -1
        best_score = -1.0

        for idx in remaining:
            s = sentences[idx]
            new_words = s - covered
            if not new_words:
                continue
            score = sum(_weight(w) for w in new_words)
            # 平局打破：新覆盖相同则优先选总长度更短/更精炼的句子
            if score > best_score or (
                abs(score - best_score) < 1e-9 and len(s) < len(sentences[best_idx])
            ):
                best_score = score
                best_idx = idx

        if best_idx == -1:
            break  # 没有新覆盖可带来

        selected.append(best_idx)
        covered.update(sentences[best_idx])
        remaining.discard(best_idx)

    # 4) 逆向精简：若超过 max_select，剔除冗余
    if len(selected) > max_select:
        cover_count = collections.Counter()
        for idx in selected:
            for w in sentences[idx]:
                cover_count[w] += 1

        while len(selected) > max_select:
            min_idx = -1
            min_contrib = float("inf")

            for i, idx in enumerate(selected):
                contrib = 0.0
                would_lose_name = False
                for w in sentences[idx]:
                    if cover_count[w] == 1:
                        contrib += _weight(w)
                        if w in name_set:
                            would_lose_name = True
                # 若移除会导致名字词丢失，大幅抬高边际贡献使其不被剔除
                if would_lose_name:
                    contrib += 1e6
                if contrib < min_contrib:
                    min_contrib = contrib
                    min_idx = i

            if min_idx == -1:
                break

            removed = selected.pop(min_idx)
            for w in sentences[removed]:
                cover_count[w] -= 1

    return selected
