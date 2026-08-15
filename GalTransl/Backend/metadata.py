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
from GalTransl.Backend.utils import parse_interval


class FileMetaData:
    """文件级元数据类

    用于在多轮对话的第一轮向 LLM 提供文件级的剧情上下文，
    帮助模型在后续轮次中保持人物译名、语气与剧情基调的一致性。

    属性（与 gt_input 中的 ``FileMetaData.json`` 顶层键一一对应；
    类内使用英文属性名，JSON 数据键保持中文）：

        id        标识：文件级元数据的字符串标识（可空）
        character 角色：角色/人物设定（字符串或字符串列表）
        costume   服装：角色服装/外观描述（字符串）
        plot      剧情：剧情梗概/背景（字符串）
        tags      标签：题材/关键词标签（字符串或字符串列表）
    """

    def __init__(
        self,
        id: object = "",
        character: object = "",
        costume: object = "",
        plot: object = "",
        tags: object = None,
    ) -> None:
        """
        初始化文件级元数据

        :param id: 文件级元数据标识（str，可空）
        :param character: 角色设定（str 或 list[str]），对应 JSON 键「角色」
        :param costume: 服装/外观描述（str），对应 JSON 键「服装」
        :param plot: 剧情梗概（str），对应 JSON 键「剧情」
        :param tags: 标签（str 或 list[str]），对应 JSON 键「标签」
        """
        self.id = id if id is not None else ""
        self.character = character
        self.costume = costume
        self.plot = plot
        self.tags = tags if tags is not None else []

    def __repr__(self):
        return (
            f"FileMetaData(id={self.id!r}, "
            f"character={self.character!r}, "
            f"costume={self.costume!r}, "
            f"plot={self.plot!r}, "
            f"tags={self.tags!r})"
        )


def load_file_metadata(projectConfig: "CProjectConfig", filename: str = "") -> Optional[FileMetaData]:
    """从 per-file 缓存载入单个文件级元数据。

    每个源文件的元数据独立存储在 ``{filename}.meta.json`` 中（由 ForFileMetaData
    后端生成），路径为 ``transl_cache/pass1_cache/{filename}.meta.json``。

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

    return FileMetaData(
        id=data.get("id") or "",
        character=_to_list(data.get("角色")),
        costume=data.get("服装") or "",
        plot=data.get("剧情") or "",
        tags=_to_list(data.get("标签")),
    )


def load_file_metadata_map(projectConfig: "CProjectConfig") -> dict:
    """遍历 pass1_cache/*.meta.json，载入「文件名 -> 文件级元数据」映射。

    每个源文件的元数据独立存储在 ``{filename}.meta.json`` 中。本函数遍历
    ``transl_cache/pass1_cache/`` 下所有 ``.meta.json`` 文件，解析为
    ``{id: FileMetaData}`` 字典。
    """
    from GalTransl import PASS1_CACHE_DIR

    pass1_dir = os.path.join(projectConfig.getCachePath(), PASS1_CACHE_DIR)
    if not os.path.isdir(pass1_dir):
        return {}

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
            result[fid] = FileMetaData(
                id=fid,
                character=_to_list(item.get("角色")),
                costume=item.get("服装") or "",
                plot=item.get("剧情") or "",
                tags=_to_list(item.get("标签")),
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
                 ``{"区间":[lo,hi], "视角":str, "氛围":str, "h":bool, "用词色彩":str}``
                 其中 ``区间`` 为文件内**全局行号**闭区间（从 1 起，与句子的
                 runtime_index 对应）。
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