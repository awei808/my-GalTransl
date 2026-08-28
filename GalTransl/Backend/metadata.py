"""文件级 / 批次级元数据实体与缓存加载器。

FileMetaData / BatchMetadata 数据类与对应的 *.meta.json / *.batch.json 加载器，
与具体后端无耦合。被 ForGalJsonMulitChat、ForFileMetaData、ForBatchMetaData、
ForPlotRouteMap、LLMTranslate 等自由复用。
"""

from __future__ import annotations

import json
import os
from typing import Optional

from GalTransl import LOGGER
from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.Backend.utils import coerce_bool, parse_interval


def save_metadata_json(
    projectConfig: "CProjectConfig",
    cache_dir_name: str,
    filename: str,
    suffix: str,
    meta: dict,
    tag: str,
) -> str:
    """原子写入 per-file 元数据 JSON（``{filename}.{suffix}.json``）。

    ForFileMetaData（pass1_cache/*.meta.json）与 ForBatchMetaData
    （pass2_cache/*.batch.json）共用同一写盘路径：先写同目录临时文件再
    os.replace 原子替换，避免写一半崩溃留下截断文件。返回写入的文件路径。

    Args:
        projectConfig: 项目配置（取 getCachePath()）。
        cache_dir_name: 缓存子目录常量（PASS1_CACHE_DIR / PASS2_CACHE_DIR）。
        filename: 元数据归属的文件名。
        suffix: 文件名后缀（"meta" / "batch"）。
        meta: 待写入的元数据 dict。
        tag: 日志前缀（"FileMetaData" / "BatchMetaData"）。
    """
    out_dir = os.path.join(projectConfig.getCachePath(), cache_dir_name)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{filename}.{suffix}.json")
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise
    LOGGER.debug(f"[{tag}] 已保存 {path}")
    return path


class FileMetaData:
    """文件级元数据类

    用于在多轮对话的第一轮向 LLM 提供文件级的剧情上下文，
    帮助模型在后续轮次中保持人物译名、语气与剧情基调的一致性。

    属性（与 gt_input 中的 ``FileMetaData.json`` 顶层键一一对应；
    类内使用英文属性名，JSON 数据键保持中文）：

        id          标识：文件级元数据的字符串标识（可空）
        character   角色：角色/人物设定（字符串或字符串列表）
        costume     服装：角色服装/外观描述（字符串）
        plot        剧情：剧情梗概/背景（字符串）
        tags        标签：题材/关键词标签（字符串或字符串列表）
        address_map 称呼映射：称谓决策表（list[dict]，见 __init__）
    """

    def __init__(
        self,
        id: object = "",
        character: object = "",
        costume: object = "",
        plot: object = "",
        tags: object = None,
        address_map: object = None,
    ) -> None:
        """
        初始化文件级元数据

        :param id: 文件级元数据标识（str，可空）
        :param character: 角色设定（str 或 list[str]），对应 JSON 键「角色」
        :param costume: 服装/外观描述（str），对应 JSON 键「服装」
        :param plot: 剧情梗概（str），对应 JSON 键「剧情」
        :param tags: 标签（str 或 list[str]），对应 JSON 键「标签」
        :param address_map: 称呼映射（list[dict]，对应 JSON 键「称呼映射」）。
            每项含 被称呼者/原文/译文（称呼者可省略），见 _normalize_meta 约定。
        """
        self.id = id if id is not None else ""
        self.character = character
        self.costume = costume
        self.plot = plot
        self.tags = tags if tags is not None else []
        self.address_map = (
            address_map if isinstance(address_map, list) else []
        )

    def __repr__(self):
        return (
            f"FileMetaData(id={self.id!r}, "
            f"character={self.character!r}, "
            f"costume={self.costume!r}, "
            f"plot={self.plot!r}, "
            f"tags={self.tags!r}, "
            f"address_map={self.address_map!r})"
        )


def format_file_metadata_block(
    metadata: "FileMetaData", include_guidance: bool = True
) -> str:
    """把文件级元数据格式化为提示词附加段落（<plot_metadata> 包裹版）。

    供 ForGalJsonMulitChat（翻译轮首轮）与 ForBatchMetaData（批次划分提示词）
    共用同一形态，避免「文件级元数据」在两条提示词链里的口径漂移。

    Args:
        metadata: 文件级元数据对象。
        include_guidance: 是否附带翻译轮专属指导语（“保持人物译名…后续轮次…”）。
            翻译轮传 True；批次划分等非翻译场景传 False，仅保留 <plot_metadata> 标签块。
    """
    def _join(value: object) -> str:
        """把 str 或 list[str] 规范为「、」分隔串；空值返回 None。"""
        if value is None or value == "":
            return None
        if isinstance(value, list):
            items = [str(x).strip() for x in value if str(x).strip() != ""]
            return "、".join(items) if items else None
        s = str(value).strip()
        return s if s else None

    id_line = f"id: {metadata.id}\n" if metadata.id else ""
    character = _join(metadata.character) or "无"
    costume = _join(metadata.costume) or "无"
    plot = _join(metadata.plot) or "无"
    tags = _join(metadata.tags) or "无"
    address_block = _format_address_map_block(metadata.address_map)
    block = (
        "\n<plot_metadata>\n"
        f"{id_line}"
        f"角色: {character}\n"
        f"服装: {costume}\n"
        f"剧情: {plot}\n"
        f"标签: {tags}\n"
        f"{address_block}"
        "</plot_metadata>\n"
    )
    if include_guidance:
        block += (
            "请参考上述 <plot_metadata> 中的剧情元数据：保持人物译名"
            "（与「角色」列表一致）、语气与剧情基调前后统一。"
            "后续轮次将只提供待翻译句子，无需重复翻译要求。\n"
        )
        if metadata.address_map:
            block += (
                "同时保持人物称谓：同一角色被不同人称呼时，按上述「称呼映射」"
                "使用对应的译文称谓（原文→译文）。\n"
            )
    return block


def _format_address_map_block(address_map: list) -> str:
    """把称呼映射格式化为提示词「称呼映射」段落（含换行缩进）。

    每项至少含 原文/译文，可选含 被称呼者/称呼者。形态：
        - 被称呼者（由称呼者称呼）：原文「原文」→ 译文「译文」
        - 被称呼者：原文「原文」→ 译文「译文」
    """
    if not address_map:
        return ""
    lines = []
    for item in address_map:
        if not isinstance(item, dict):
            continue
        src = str(item.get("原文", "") or "").strip()
        dst = str(item.get("译文", "") or "").strip()
        if not src or not dst:
            continue
        subject = str(item.get("被称呼者", "") or "").strip()
        caller = str(item.get("称呼者", "") or "").strip()
        if subject and caller:
            head = f"{subject}（由{caller}称呼）"
        elif subject:
            head = subject
        else:
            head = src
        lines.append(f"- {head}：原文「{src}」→ 译文「{dst}」")
    if not lines:
        return ""
    return "称呼映射:\n" + "\n".join(lines) + "\n"


def build_glossary_prompt_text(
    json_list: list, projectConfig: "CProjectConfig", tag: str
) -> str:
    """按需注入 GPT 字典：仅将当前文件中实际出现的条目格式化为 Markdown 译表。

    ForFileMetaData / ForBatchMetaData 共用（元数据阶段不分流、仅注入非 h 字典，
    避免 h 词条污染整体剧情描述）。先用 json_list 构造临时 CTransList，再经
    CGptDict.gen_prompt 只注入命中条目；无字典/无命中时返回空串。

    Args:
        json_list: 待分析条目列表（每项含 name/message）。
        projectConfig: 项目配置（读取 dictionary 段）。
        tag: 日志前缀（"FileMetaData" / "BatchMetaData"）。
    """
    if not json_list:
        return ""
    dict_cfg = projectConfig.getDictCfgSection()
    if not dict_cfg:
        return ""
    gpt_dic_list = dict_cfg.get("gpt.dict", [])
    if not gpt_dic_list:
        return ""
    try:
        from GalTransl.ConfigHelper import initDictList
        from GalTransl.Dictionary import CGptDict
        from GalTransl.CSentense import CSentense

        paths = initDictList(
            gpt_dic_list, dict_cfg.get("defaultDictFolder", ""), projectConfig.getProjectDir()
        )
        gpt_dic = CGptDict(paths)
    except Exception as e:
        LOGGER.warning(f"[{tag}] 载入 GPT 字典失败，元数据将不含专名译表：{e}")
        return ""

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

    glossary = gpt_dic.gen_prompt(trans_list, scene="nh") if trans_list else ""
    if glossary:
        LOGGER.debug(
            f"[{tag}] 按需注入 GPT 字典，命中 {glossary.count(chr(10)) - 3} 条"
        )
    else:
        LOGGER.debug(f"[{tag}] 当前文件无命中 GPT 字典条目")
    return glossary


def _to_dict_list(value: object) -> list:
    """把「称呼映射」字段规范为 list[dict]；非 list[dict] 一律回退空列表。

    Args:
        value: 原始值（None / list[dict] / 其它）。

    Returns:
        过滤后的 list[dict]（非法元素剔除）。
    """
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _resolve_address_map_enabled(
    projectConfig: "CProjectConfig", enable_address_map: bool = None
) -> bool:
    """解析「称呼映射」读路径开关：None 时读取项目配置，显式传值则覆盖。

    与生成阶段 ForFileMetaData 的 internals.forfilemeta.address_map 保持一致，
    避免「生成阶段关闭、读路径仍注入旧缓存」的口径漂移。

    Args:
        projectConfig: 项目配置对象。
        enable_address_map: 显式开关；None 时读取配置项（默认开启）。

    Returns:
        是否注入「称呼映射」字段。
    """
    if enable_address_map is None:
        enable_address_map = coerce_bool(
            projectConfig.getKey("internals.forfilemeta.address_map", True),
            default=True,
        )
    return enable_address_map


def load_file_metadata(
    projectConfig: "CProjectConfig",
    filename: str = "",
    enable_address_map: bool = None,
) -> Optional[FileMetaData]:
    """从 per-file 缓存载入单个文件级元数据。

    每个源文件的元数据独立存储在 ``{filename}.meta.json`` 中（由 ForFileMetaData
    后端生成），路径为 ``transl_cache/pass1_cache/{filename}.meta.json``。

    Args:
        projectConfig: 项目配置对象。
        filename: 待载入的文件名。
        enable_address_map: 是否注入「称呼映射」；None 时读取项目配置开关。

    文件不存在或解析失败时返回 None。
    """
    from GalTransl import PASS1_CACHE_DIR

    if not filename:
        return None

    path = os.path.join(
        projectConfig.getCachePath(), PASS1_CACHE_DIR, f"{filename}.meta.json"
    )
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        LOGGER.warning(f"读取 {filename}.meta.json 失败：{e}")
        return None

    if not isinstance(data, dict):
        return None

    def _to_list(value):
        if value is None:
            return []
        if isinstance(value, list):
            return [str(x) for x in value]
        return [str(value)]

    address_map = _to_dict_list(data.get("称呼映射"))
    if not _resolve_address_map_enabled(projectConfig, enable_address_map):
        address_map = []

    return FileMetaData(
        id=data.get("id") or "",
        character=_to_list(data.get("角色")),
        costume=data.get("服装") or "",
        plot=data.get("剧情") or "",
        tags=_to_list(data.get("标签")),
        address_map=address_map,
    )


def load_file_metadata_map(
    projectConfig: "CProjectConfig", enable_address_map: bool = None
) -> dict:
    """遍历 pass1_cache/*.meta.json，载入「文件名 -> 文件级元数据」映射。

    每个源文件的元数据独立存储在 ``{filename}.meta.json`` 中。本函数遍历
    ``transl_cache/pass1_cache/`` 下所有 ``.meta.json`` 文件，解析为
    ``{id: FileMetaData}`` 字典。

    Args:
        projectConfig: 项目配置对象。
        enable_address_map: 是否注入「称呼映射」；None 时读取项目配置开关。
    """
    from GalTransl import PASS1_CACHE_DIR

    pass1_dir = os.path.join(projectConfig.getCachePath(), PASS1_CACHE_DIR)
    if not os.path.isdir(pass1_dir):
        return {}

    resolve_enabled = _resolve_address_map_enabled(projectConfig, enable_address_map)

    def _to_list(value):
        if value is None:
            return []
        if isinstance(value, list):
            return [str(x) for x in value]
        return [str(value)]

    result: dict = {}
    try:
        for entry in os.listdir(pass1_dir):
            if not entry.endswith(".meta.json"):
                continue
            fpath = os.path.join(pass1_dir, entry)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    item = json.load(f)
            except Exception as e:
                LOGGER.warning(f"读取 {entry} 失败，跳过：{e}")
                continue
            if not isinstance(item, dict):
                continue
            fid = item.get("id") or ""
            if not fid:
                continue
            address_map = _to_dict_list(item.get("称呼映射")) if resolve_enabled else []
            result[fid] = FileMetaData(
                id=fid,
                character=_to_list(item.get("角色")),
                costume=item.get("服装") or "",
                plot=item.get("剧情") or "",
                tags=_to_list(item.get("标签")),
                address_map=address_map,
            )
    except OSError as e:
        LOGGER.warning(f"遍历 pass1_cache 失败：{e}")

    return result


class BatchMetadata:
    """批次级元数据类

    描述一个剧本文件被划分出的若干「翻译区间(批次)」，用于在多轮对话首轮向
    LLM 提供比文件级更细的翻译指导（视角/氛围/H/用词色彩），使同一文件内不同
    段落采用恰当的用词色彩，并在区间之间自然过渡。

    属性：
        id       标识：对应 gt_input 中一个待翻译文件名
        batches  批次数组：每项为 dict
                 ``{"区间":[lo,hi], "视角":str, "氛围":str, "h":float, "用词色彩":str}``
                 其中 ``区间`` 为文件内**全局行号**闭区间（从 1 起，与句子的
                 runtime_index 对应）；``h`` 为 0-1 浮点 H 强度（兼容旧布尔）。
                 h >= 0.5 视为 H 场景（is_h_value 判定）。
    """

    def __init__(self, id: object = "", batches: object = None) -> None:
        self.id = id if id is not None else ""
        self.batches = batches if isinstance(batches, list) else []

    def segments_in_range(self, lo: int, hi: int) -> list:
        """返回与全局行号闭区间 [lo, hi] 有交集的批次（按起始行号升序）。

        用于把当前待译批次（chunk/请求）实际覆盖的行号范围，映射到它所涉及的
        一个或多个翻译区间，仅注入相关区间的翻译指导，避免注入整份区间表。
        """
        result = []
        for b in self.batches:
            if not isinstance(b, dict):
                continue
            rng = parse_interval(b.get("区间") or b.get("interval"))
            if rng is None:
                continue
            b_lo, b_hi = rng
            # 判定两闭区间是否相交
            if b_hi >= lo and b_lo <= hi:
                result.append(b)
        result.sort(
            key=lambda x: (
                int((x.get("区间") or x.get("interval"))[0]),
                int((x.get("区间") or x.get("interval"))[1]),
            )
        )
        return result

    def __repr__(self):
        return f"BatchMetadata(id={self.id!r}, batches={len(self.batches)})"


def load_batch_metadata_map(projectConfig: "CProjectConfig") -> dict:
    """遍历 pass2_cache/*.batch.json，载入「文件名 -> 批次级元数据」映射。

    每个源文件的批次元数据独立存储在 ``{filename}.batch.json`` 中。本函数遍历
    ``transl_cache/pass2_cache/`` 下所有 ``.batch.json`` 文件。
    """
    from GalTransl import PASS2_CACHE_DIR

    pass2_dir = os.path.join(projectConfig.getCachePath(), PASS2_CACHE_DIR)
    if not os.path.isdir(pass2_dir):
        return {}

    result: dict = {}
    try:
        for entry in os.listdir(pass2_dir):
            if not entry.endswith(".batch.json"):
                continue
            fpath = os.path.join(pass2_dir, entry)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    item = json.load(f)
            except Exception as e:
                LOGGER.warning(f"读取 {entry} 失败，跳过：{e}")
                continue
            if not isinstance(item, dict):
                continue
            fid = item.get("id") or ""
            if not fid:
                continue
            batches = item.get("批次") or item.get("batches") or []
            result[fid] = BatchMetadata(
                id=fid, batches=batches if isinstance(batches, list) else []
            )
    except OSError as e:
        LOGGER.warning(f"遍历 pass2_cache 失败：{e}")

    return result