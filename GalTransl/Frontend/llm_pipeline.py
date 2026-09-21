"""完整流水线编排器（0.4.10 拆分续，自 LLMTranslate.py 抽出）。

- `_run_full_pipeline`：按 `pipeline_stages.PIPELINE_STAGES` 遍历调度各阶段；
- `_run_stage_*`：单阶段实现，阶段间状态经 `stage_ctx` 传递。

阶段清单的静态定义（顺序/开关/依赖/后端槽位）不在此处，见 `pipeline_stages.py`。
翻译阶段需回调 `LLMTranslate._run_translation_phase`，故该处用函数内延迟导入，
避免本模块与 LLMTranslate 形成模块级循环依赖。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from GalTransl import LOGGER
from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.server_runtime import record_runtime_notice
from GalTransl.Frontend.llm_runtime import (
    _build_meta_file_totals,
    _check_stop_requested,
    _run_meta_worker_pool,
    _stage_pool,
    _update_runtime,
    ensure_model_available_if_needed,
)
from GalTransl.Frontend.llm_prepost import _has_nonempty_gpt_dict
from GalTransl.Frontend.pipeline_stages import (
    PIPELINE_STAGES,
    STAGES_BY_KEY,
    stage_display_label,
)


def _stage_enabled(projectConfig: CProjectConfig, stage_key: str) -> bool:
    """按阶段清单判定该阶段是否启用（缺省 True，保持与历史行为一致）。"""
    stage = STAGES_BY_KEY[stage_key]
    return bool(projectConfig.getKey(stage.enabled_key, True))


def _stage_skip(projectConfig: CProjectConfig, stage_key: str, reason: str) -> None:
    """统一的阶段跳过处理：写警告日志 + 上报运行时提示。"""
    stage = STAGES_BY_KEY[stage_key]
    label = stage_display_label(stage)
    LOGGER.warning(f"[流水线] {label} 跳过：{reason}")
    record_runtime_notice(projectConfig.getProjectDir(), f"{label}：{reason}")


def _stage_begin(projectConfig: CProjectConfig, stage_key: str, runtime_stage: str) -> None:
    """统一的阶段开始处理：写分隔日志 + 上报运行时阶段名。"""
    stage = STAGES_BY_KEY[stage_key]
    LOGGER.info(f"[流水线] {stage_display_label(stage)}")
    _update_runtime(projectConfig, stage=runtime_stage)


async def _run_stage_validate(
    projectConfig: CProjectConfig,
    file_json_lists: dict,
    file_list: list,
    stage_ctx: dict,
) -> None:
    """阶段：输入数据校验。校验失败直接抛错中止流水线。"""
    import os

    _stage_begin(projectConfig, "validate", "输入数据校验")

    if not _stage_enabled(projectConfig, "validate"):
        _stage_skip(projectConfig, "validate", "已禁用（enableValidate=false）")
        return

    from GalTransl.DataValidator import validate_input_json

    all_valid = True
    for file_path, json_list in file_json_lists.items():
        result = validate_input_json(json_list, file_path)
        if not result["valid"]:
            for err in result["errors"]:
                LOGGER.error(f"[校验失败] {file_path}: {err}")
            all_valid = False
        for warn in result["warnings"]:
            LOGGER.warning(f"[校验警告] {file_path}: {warn}")
        stats = result["stats"]
        LOGGER.info(
            f"[校验通过] {os.path.basename(file_path)}: "
            f"{stats['total_items']} 条，"
            f"name={stats['items_with_name']}，"
            f"无name={stats['items_without_name']}"
        )
    if not all_valid:
        raise RuntimeError("输入数据校验失败，流水线中止。请修复上述错误后重试。")
    LOGGER.info("[流水线] 阶段输入校验完成：所有输入文件校验通过")
    record_runtime_notice(
        projectConfig.getProjectDir(),
        f"输入校验通过（{len(file_list)} 个文件）",
    )


async def _run_stage_compress(
    projectConfig: CProjectConfig,
    file_json_lists: dict,
    file_list: list,
    stage_ctx: dict,
) -> None:
    """阶段：文本无损压缩。产出 compressed_texts 供全局分析使用。"""
    _stage_begin(projectConfig, "compress", "文本无损压缩")

    if not _stage_enabled(projectConfig, "compress"):
        _stage_skip(projectConfig, "compress", "已禁用（enableCompress=false）")
        # 置空以便全局分析阶段检查：全局分析依赖压缩文本，禁用后自动跳过
        stage_ctx["compressed_texts"] = {}
        return

    from GalTransl.TextCompressor import TextCompressor

    max_chars = projectConfig.getKey("internals.pipeline.maxInputChars", 950000)
    compressor = TextCompressor(max_chars=max_chars)

    # 逐文件压缩（保留文件边界，供 ForGlobalPrompt 按文件注入上下文）
    compressed_texts: Dict[str, str] = {}
    for file_path, json_list in file_json_lists.items():
        compressed = compressor.compress({file_path: json_list})
        compressed_texts[file_path] = compressed

    # 全局压缩（所有文件合并，供完整性校验用）
    all_compressed_text = compressor.compress(file_json_lists)

    # 校验压缩完整性：确保所有 message 和 name 完整保留
    verify_result = compressor.verify_compression(file_json_lists, all_compressed_text)
    if not verify_result.get("all_present", False):
        missing = verify_result.get("missing_messages", [])
        lost_names = verify_result.get("lost_names", [])
        if missing:
            LOGGER.error(
                f"[压缩错误] {len(missing)} 条 message 丢失！"
                f"示例：{missing[0][:80] if missing else ''}"
            )
        if lost_names:
            LOGGER.error(f"[压缩错误] 丢失角色名：{', '.join(lost_names[:10])}")
        raise RuntimeError("文本压缩完整性校验失败，流水线中止")

    LOGGER.info(
        f"[流水线] 文本压缩完成：压缩后 {len(all_compressed_text)} 字符 "
        f"全部 message 和角色名校验通过"
    )
    record_runtime_notice(
        projectConfig.getProjectDir(),
        f"文本压缩完成（{len(all_compressed_text)} 字符）",
    )
    stage_ctx["compressed_texts"] = compressed_texts


async def _run_stage_global_prompt(
    projectConfig: CProjectConfig,
    file_json_lists: dict,
    file_list: list,
    stage_ctx: dict,
) -> None:
    """阶段：全局游戏分析（GlobalPrompt.json）。"""
    import os

    _stage_begin(projectConfig, "global_prompt", "生成全局游戏分析")

    if not _stage_enabled(projectConfig, "global_prompt"):
        _stage_skip(projectConfig, "global_prompt", "已禁用（enableGlobalPrompt=false）")
        # 后续阶段通过 projectConfig.global_prompt 或缓存惰性读取，缺省时自动退化
        projectConfig.global_prompt = None
        return

    compressed_texts = stage_ctx.get("compressed_texts") or {}
    if not compressed_texts:
        _stage_skip(
            projectConfig, "global_prompt", "压缩文本为空（阶段 1 已禁用或未产出）"
        )
        projectConfig.global_prompt = None
        return

    from GalTransl.Backend.ForGlobalPrompt import (
        ForGlobalPrompt,
        load_global_prompt,
        _find_global_prompt_path,
        _select_compressed_paths,
        MERGE_FIELD_KEYS,
    )
    from GalTransl.DataValidator import validate_global_prompt

    # 全局分析范围：internals.pipeline.globalPromptFiles 指定文件子集（空=全量）。
    # 支持完整路径 / 文件名 / 去扩展名文件名，便于前端多选与路线化传入。
    raw_filter = projectConfig.getKey("internals.pipeline.globalPromptFiles", None)
    file_filter: Optional[List[str]] = None
    if isinstance(raw_filter, list) and raw_filter:
        file_filter = [str(x) for x in raw_filter if str(x or "").strip()]
    elif isinstance(raw_filter, str) and raw_filter.strip():
        file_filter = [s.strip() for s in raw_filter.replace("，", ",").split(",") if s.strip()]

    # 子集覆盖字段：internals.pipeline.globalPromptMergeFields（空=覆盖全部字段）
    raw_fields = projectConfig.getKey("internals.pipeline.globalPromptMergeFields", None)
    merge_fields: Optional[List[str]] = None
    if isinstance(raw_fields, list) and raw_fields:
        merge_fields = [
            str(x) for x in raw_fields if str(x or "").strip() in MERGE_FIELD_KEYS
        ]
        # 字段名全部无效时回退「覆盖全部」：空列表会让合并变成「一个字段都不覆盖」，
        # 用户会看到配置改了却毫无效果（静默失效）。
        if not merge_fields:
            LOGGER.warning(
                f"[流水线] globalPromptMergeFields 中的字段名均无效"
                f"（可用：{'、'.join(MERGE_FIELD_KEYS)}），本次按覆盖全部字段执行"
            )
            merge_fields = None

    gp_path = _find_global_prompt_path(projectConfig)
    force_regen_gp = projectConfig.getKey("internals.pipeline.forceRegenGlobal", False)

    if os.path.exists(gp_path) and not force_regen_gp:
        LOGGER.info("[流水线] 全局分析已存在，跳过生成")
        record_runtime_notice(projectConfig.getProjectDir(), "全局分析已存在，跳过")
        success = True
    else:
        # 子集筛选后可能无有效文件：提前降级为「全量」，避免阶段整体失败
        if file_filter is not None:
            matched = _select_compressed_paths(compressed_texts, file_filter)
            if not matched:
                LOGGER.warning(
                    "[流水线] globalPromptFiles 未匹配到任何文件，本次按全量分析执行"
                )
                file_filter = None

        await ensure_model_available_if_needed(projectConfig, stage="global_prompt")
        gptapi_global = ForGlobalPrompt(
            projectConfig, "ForGlobalPrompt",
            projectConfig.proxyPool, _stage_pool(projectConfig, "global_prompt"),
        )
        try:
            external_info = projectConfig.getKey("externals.gameInfo", "") or ""
            success = await gptapi_global.batch_translate(
                compressed_texts,
                external_info=external_info,
                file_filter=file_filter,
                merge_fields=merge_fields,
            )
        finally:
            if hasattr(gptapi_global, "shutdown"):
                await gptapi_global.shutdown()
        if not success:
            LOGGER.error("[流水线] 全局游戏分析生成失败，流水线中止")
            raise RuntimeError("全局游戏分析生成失败")

    # 校验 GlobalPrompt.json（跳过或重新生成后均需读取，供后续阶段复用）
    global_prompt = load_global_prompt(projectConfig)
    if global_prompt is None:
        LOGGER.error("[流水线] GlobalPrompt.json 校验失败，流水线中止")
        raise RuntimeError("GlobalPrompt.json 不存在或格式错误")

    gp_validation = validate_global_prompt(global_prompt)
    if not gp_validation["valid"]:
        for err in gp_validation["errors"]:
            LOGGER.error(f"[流水线] GlobalPrompt 内容校验失败: {err}")
        raise RuntimeError("GlobalPrompt 内容校验失败")
    for warn in gp_validation.get("warnings", []):
        LOGGER.warning(f"[流水线] GlobalPrompt 警告: {warn}")

    # 注入全局提示词到 projectConfig，供后续阶段复用
    projectConfig.global_prompt = global_prompt

    char_count = len(global_prompt.get("角色列表", []))
    LOGGER.info(f"[流水线] 全局分析完成：{char_count} 个角色")
    record_runtime_notice(
        projectConfig.getProjectDir(), f"全局分析完成（{char_count} 个角色）"
    )


async def _run_stage_gen_dic(
    projectConfig: CProjectConfig,
    file_json_lists: dict,
    file_list: list,
    stage_ctx: dict,
) -> None:
    """阶段：术语表构建（GenDic）。"""
    _stage_begin(projectConfig, "gen_dic", "构建术语表")

    if not _stage_enabled(projectConfig, "gen_dic"):
        _stage_skip(projectConfig, "gen_dic", "已禁用（enableGenDic=false）")
        return

    force_regen = projectConfig.getKey("internals.pipeline.forceRegenDic", False)

    # 跳过条件：项目级 gpt 字典已有非空有效条目（不再只看文件是否存在）
    if _has_nonempty_gpt_dict(projectConfig) and not force_regen:
        LOGGER.info("[流水线] 术语表已存在（非空），跳过生成")
        record_runtime_notice(
            projectConfig.getProjectDir(), "术语表已存在（非空），不重新生成"
        )
        return

    LOGGER.info("[流水线] 开始生成术语表")
    record_runtime_notice(projectConfig.getProjectDir(), "开始生成术语表")
    from GalTransl.Backend.GenDic import GenDic

    await ensure_model_available_if_needed(projectConfig, stage="gen_dic")
    gptapi_dic = GenDic(
        projectConfig, "GenDic",
        projectConfig.proxyPool, _stage_pool(projectConfig, "gen_dic"),
    )
    try:
        all_jsons = []
        for json_list in file_json_lists.values():
            all_jsons.extend(json_list)
        dic_ok = await gptapi_dic.batch_translate(all_jsons)
    finally:
        if hasattr(gptapi_dic, "shutdown"):
            await gptapi_dic.shutdown()

    # batch_translate 仅在硬失败（分词模型无法加载）时返回 False；分片级失败视为部分成功，
    # 与流水线容错设计一致，故默认不中止，避免误伤「文本无可提取词条」的合法场景。
    if not dic_ok:
        abort = projectConfig.getKey("internals.pipeline.abortOnDicFailure", False)
        if abort:
            LOGGER.error(
                "[流水线] 术语表生成失败，按 abortOnDicFailure 配置中止流水线"
            )
            raise RuntimeError(
                "术语表生成失败（分词模型加载失败），已按 abortOnDicFailure=true 中止流水线"
            )
        LOGGER.warning("[流水线] 术语表生成失败，abortOnDicFailure=false 继续流水线")
    else:
        LOGGER.info("[流水线] 术语表已生成")


async def _run_stage_file_meta(
    projectConfig: CProjectConfig,
    file_json_lists: dict,
    file_list: list,
    stage_ctx: dict,
) -> None:
    """阶段：文件级元数据（pass1_cache/FileMetaData.json）。"""
    _stage_begin(projectConfig, "file_meta", "生成文件级元数据")
    # 上报输入文件总行数：使前端文件进度面板在元数据阶段显示输入文件而非缓存文件
    _update_runtime(
        projectConfig,
        file_totals=_build_meta_file_totals(
            file_json_lists, projectConfig.getInputPath()
        ),
    )
    # 总文件数在文件级/批次级元数据阶段共用，写入 stage_ctx 供后者复用
    total_files = len(file_json_lists)
    stage_ctx["total_files"] = total_files

    if not _stage_enabled(projectConfig, "file_meta"):
        _stage_skip(projectConfig, "file_meta", "已禁用（enableFileMeta=false）")
        return

    from GalTransl.Backend.ForFileMetaData import ForFileMetaData
    from GalTransl.Backend.metadata import load_file_metadata_map

    await ensure_model_available_if_needed(projectConfig, stage="file_meta")
    gptapi_filemeta = ForFileMetaData(
        projectConfig, "ForFileMetaData",
        projectConfig.proxyPool, _stage_pool(projectConfig, "file_meta"),
    )
    try:
        # ForFileMetaData 会通过 projectConfig.global_prompt 自动使用全局分析
        # 已存在的文件级元数据映射：用于「已存在则跳过」，避免覆盖用户手改/既有产物
        existing_fm_map = load_file_metadata_map(projectConfig)
        force_regen_fm = projectConfig.getKey(
            "internals.pipeline.forceRegenFileMeta", False
        )
        # 多 worker 并发生成文件级元数据（绑定 WORKER_ID_CTX，提示词预览按 worker 分板块）
        # workersPerProject 解析统一走 CProjectConfig.get_workers_per_project（兼容字符串/非法回退 1）
        workers_per_project = projectConfig.get_workers_per_project()
        worker_count = max(1, workers_per_project)
        processed_fm = await _run_meta_worker_pool(
            projectConfig, gptapi_filemeta, file_json_lists,
            existing_map=existing_fm_map,
            worker_count=worker_count,
            tag="FileMetaData", stage_prefix="文件级元数据",
            force_regen=force_regen_fm,
        )
        skipped_files = total_files - processed_fm

        # 交叉验证 FileMetaData 条目数
        fm_map = load_file_metadata_map(projectConfig)
        fm_count = len(fm_map)
        if fm_count < total_files:
            LOGGER.warning(
                f"[流水线] 文件级元数据警告：{fm_count}/{total_files} 个文件"
                f"生成了元数据，缺失 {total_files - fm_count} 个"
            )
            record_runtime_notice(
                projectConfig.getProjectDir(),
                f"警告：{total_files - fm_count} 个文件未生成文件级元数据",
            )
        else:
            LOGGER.info(f"[流水线] 文件级元数据完成：{fm_count}/{total_files} 个文件")
            record_runtime_notice(
                projectConfig.getProjectDir(),
                f"文件级元数据完成（{fm_count}/{total_files} 个文件）",
            )
        if skipped_files:
            LOGGER.info(f"[流水线] 跳过 {skipped_files} 个已存在文件级元数据的文件")
    finally:
        if hasattr(gptapi_filemeta, "shutdown"):
            await gptapi_filemeta.shutdown()


async def _run_stage_plot_route(
    projectConfig: CProjectConfig,
    file_json_lists: dict,
    file_list: list,
    stage_ctx: dict,
) -> None:
    """阶段：剧情路线图（pass0_cache/PlotRouteMap.json）。"""
    _stage_begin(projectConfig, "plot_route", "生成剧情路线图")

    if not _stage_enabled(projectConfig, "plot_route"):
        _stage_skip(projectConfig, "plot_route", "已禁用（enablePlotRoute=false）")
        return
    if not _stage_enabled(projectConfig, "file_meta"):
        # 依赖文件级元数据：阶段被禁用时自动跳过（无剧情摘要可输入）
        _stage_skip(
            projectConfig, "plot_route", "依赖的文件级元数据阶段已禁用，无剧情摘要可输入"
        )
        return

    from GalTransl.Backend.ForPlotRouteMap import ForPlotRouteMap, load_plot_route_map

    force_regen_pr = projectConfig.getKey("internals.pipeline.forceRegenPlotRoute", False)
    if load_plot_route_map(projectConfig) and not force_regen_pr:
        LOGGER.info(
            "[流水线] 剧情路线图已存在，跳过生成（如需重新生成请启用 forceRegenPlotRoute）"
        )
        record_runtime_notice(
            projectConfig.getProjectDir(),
            "剧情路线图已存在，跳过（如需重新生成请启用 forceRegenPlotRoute）",
        )
        return

    gptapi_plotroute = ForPlotRouteMap(
        projectConfig, "ForPlotRouteMap",
        projectConfig.proxyPool, _stage_pool(projectConfig, "plot_route"),
    )
    await ensure_model_available_if_needed(projectConfig, stage="plot_route")
    try:
        structure_type = projectConfig.getKey("internals.plotroute.structureType", "树")
        user_outline = projectConfig.getKey("internals.plotroute.userOutline", "")
        ok = await gptapi_plotroute.batch_translate(
            structure_type=structure_type,
            user_outline=user_outline,
            force_regen=force_regen_pr,
        )
        if not ok:
            LOGGER.warning("[流水线] 剧情路线图生成失败或未生成，继续流水线")
    finally:
        if hasattr(gptapi_plotroute, "shutdown"):
            await gptapi_plotroute.shutdown()


async def _run_stage_batch_meta(
    projectConfig: CProjectConfig,
    file_json_lists: dict,
    file_list: list,
    stage_ctx: dict,
) -> None:
    """阶段：批次级元数据（pass2_cache/BatchMetadata.json）。"""
    _stage_begin(projectConfig, "batch_meta", "划分翻译区间")
    # 上报输入文件总行数：使前端文件进度面板在元数据阶段显示输入文件而非缓存文件
    _update_runtime(
        projectConfig,
        file_totals=_build_meta_file_totals(
            file_json_lists, projectConfig.getInputPath()
        ),
    )

    if not _stage_enabled(projectConfig, "batch_meta"):
        _stage_skip(projectConfig, "batch_meta", "已禁用（enableBatchMeta=false）")
        return

    from GalTransl.Backend.ForBatchMetaData import ForBatchMetaData
    from GalTransl.Backend.metadata import load_batch_metadata_map

    # 总文件数在文件级元数据阶段已算过；该阶段可能被禁用，此处兜底重算
    total_files = stage_ctx.get("total_files") or len(file_json_lists)

    await ensure_model_available_if_needed(projectConfig, stage="batch_meta")
    gptapi_batchmeta = ForBatchMetaData(
        projectConfig, "ForBatchMetaData",
        projectConfig.proxyPool, _stage_pool(projectConfig, "batch_meta"),
    )
    try:
        # ForBatchMetaData 会写入 transl_cache/pass2_cache/ 的 {filename}.batch.json
        # 已存在的批次级元数据映射：用于「已存在则跳过」，避免覆盖用户手改/既有产物
        existing_bm_map = load_batch_metadata_map(projectConfig)
        force_regen_bm = projectConfig.getKey(
            "internals.pipeline.forceRegenBatchMeta", False
        )
        # 多 worker 并发划分翻译区间（绑定 WORKER_ID_CTX，提示词预览按 worker 分板块）
        # workersPerProject 解析统一走 CProjectConfig.get_workers_per_project（兼容字符串/非法回退 1）
        workers_per_project = projectConfig.get_workers_per_project()
        worker_count = max(1, workers_per_project)
        processed_bm = await _run_meta_worker_pool(
            projectConfig, gptapi_batchmeta, file_json_lists,
            existing_map=existing_bm_map,
            worker_count=worker_count,
            tag="BatchMetaData", stage_prefix="批次划分",
            force_regen=force_regen_bm,
        )
        skipped_batches = total_files - processed_bm

        # 交叉验证 BatchMetadata 条目数
        bm_map = load_batch_metadata_map(projectConfig)
        bm_count = len(bm_map)
        if bm_count < total_files:
            LOGGER.warning(
                f"[流水线] 批次级元数据警告：{bm_count}/{total_files} 个文件"
                f"划分了批次，缺失 {total_files - bm_count} 个"
            )
            record_runtime_notice(
                projectConfig.getProjectDir(),
                f"警告：{total_files - bm_count} 个文件未划分翻译区间",
            )
        else:
            LOGGER.info(f"[流水线] 翻译区间划分完成：{bm_count}/{total_files} 个文件")
            record_runtime_notice(
                projectConfig.getProjectDir(),
                f"翻译区间划分完成（{bm_count}/{total_files} 个文件）",
            )
        if skipped_batches:
            LOGGER.info(f"[流水线] 跳过 {skipped_batches} 个已存在批次级元数据的文件")
    finally:
        if hasattr(gptapi_batchmeta, "shutdown"):
            await gptapi_batchmeta.shutdown()


async def _run_stage_translate(
    projectConfig: CProjectConfig,
    file_json_lists: dict,
    file_list: list,
    stage_ctx: dict,
) -> None:
    """阶段：翻译执行。复用 _run_translation_phase 的既有翻译流程。"""
    LOGGER.info(f"[流水线] {stage_display_label(STAGES_BY_KEY['translate'])}")
    record_runtime_notice(projectConfig.getProjectDir(), "开始翻译")
    _update_runtime(projectConfig, stage="翻译执行中")

    if not _stage_enabled(projectConfig, "translate"):
        _stage_skip(projectConfig, "translate", "已禁用（enableTranslate=false）")
        return

    # 逻辑本体在 LLMTranslate._run_translation_phase；此处按模块属性查找（而非 import 语句），
    # 既避免循环导入，也保持该函数可被 mock.patch("...LLMTranslate._run_translation_phase") 替换。
    from GalTransl.Frontend import LLMTranslate as _llm_translate_mod

    await _llm_translate_mod._run_translation_phase(
        projectConfig, file_json_lists, file_list
    )


# 阶段 key -> 处理函数（与 PIPELINE_STAGES 的 key 一一对应）
_STAGE_HANDLERS = {
    "validate": _run_stage_validate,
    "compress": _run_stage_compress,
    "global_prompt": _run_stage_global_prompt,
    "gen_dic": _run_stage_gen_dic,
    "file_meta": _run_stage_file_meta,
    "plot_route": _run_stage_plot_route,
    "batch_meta": _run_stage_batch_meta,
    "translate": _run_stage_translate,
}

# 未实现独立处理函数的阶段（复用译后处理链路，不参与本编排器遍历）
_UNHANDLED_STAGE_KEYS = frozenset(
    s.key for s in PIPELINE_STAGES if s.key not in _STAGE_HANDLERS
)


async def _run_full_pipeline(
    projectConfig: CProjectConfig,
    file_json_lists: dict,  # {file_path: json_list}
    file_list: list,
) -> None:
    """完整翻译流水线：按阶段清单顺序执行，每阶段输出校验后才进入下一阶段。

    阶段清单的唯一真源是 `Frontend/pipeline_stages.py` 的 PIPELINE_STAGES；
    本函数只负责遍历调度与阶段间状态传递（通过 stage_ctx），
    单阶段逻辑见 `_run_stage_*`。阶段开关、依赖关系均取自清单定义，
    不再在此处硬编码 if 串。
    """
    _check_stop_requested(projectConfig)
    _update_runtime(projectConfig, stage="完整流水线启动")

    # 阶段间共享状态：compressed_texts（供全局分析）、total_files（供元数据阶段）
    stage_ctx: dict = {"compressed_texts": {}, "total_files": len(file_json_lists)}

    for stage in PIPELINE_STAGES:
        handler = _STAGE_HANDLERS.get(stage.key)
        if handler is None:
            continue  # 无独立处理函数的阶段跳过（见 _UNHANDLED_STAGE_KEYS）
        _check_stop_requested(projectConfig)
        await handler(projectConfig, file_json_lists, file_list, stage_ctx)

    LOGGER.info("=" * 50)
    LOGGER.info("[流水线] 全部阶段执行完毕")
    record_runtime_notice(
        projectConfig.getProjectDir(), "流水线完成：全部阶段执行完毕"
    )
    _update_runtime(projectConfig, stage="流水线完成")
