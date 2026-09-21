"""翻译缓存的读取 / 重建 / 输出构建与问题检测（0.4.10 从 server.py 抽出）。

职责：
- 缓存条目与缓存文件的读取、校验、文件名归一化（_collect_cache_files 等）；
- 输出构建（_build_project_output，含对话符号提取与 name 表替换）；
- 缓存行偏移与 H 区间解析（_resolve_cache_row_offset / _resolve_cache_h_ranges）；
- 问题重建链路：_load_rebuild_deps → _rebuild_trans_list_with_postprocess
  → _run_problem_detection → recheck_pass3_cache_files；
- 目录树与批次预检（_build_cache_tree / _check_batch_size）；
- 系统文件管理器定位（_open_in_file_manager，供 /reveal 端点复用）。

注意「不可拆分簇」：recheck_pass3_cache_files 调用 _run_problem_detection，
后者调用 _rebuild_trans_list_with_postprocess；三者必须同模块 —— 测试对前两者
做 mock.patch，跨模块会静默打空（patch 只作用于调用方查找名字的命名空间）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, Optional, Tuple

from GalTransl import (
    LOGGER,
    CACHE_FOLDERNAME,
    INPUT_FOLDERNAME,
    OUTPUT_FOLDERNAME,
    PASS2_CACHE_DIR,
    PASS3_CACHE_DIR,
)
from GalTransl.Cache import CACHE_TEMP_SUFFIX
from GalTransl.ConfigHelper import CProjectConfig
from GalTransl.ConfigHelper import detect_config_file as _detect_config_file
from GalTransl.CSerialize import save_json
from GalTransl.CSplitter import DictionaryCountSplitter, EqualPartsSplitter
from GalTransl.Utils import get_n_symbol
from GalTransl.Backend.utils import coerce_h_value, is_h_value, resolve_h_thresholds
from GalTransl.server_config_schema import _read_yaml_file
from GalTransl.server_dict import (
    _ensure_common_dicts_in_config,
    _migrate_h_check_dict_config,
)
from GalTransl.server_meta import _load_project_name_dict, _lookup_name


# cache 替换端点（/cache/replace、/cache/replace-entry）对 src/problem 字段的统一拒绝文案
def _open_in_file_manager(path: str, is_file: bool) -> None:
    """在系统默认文件管理器中定位（文件）/ 打开（文件夹）。

    文件：Windows 用 explorer /select, 选中；macOS 用 open -R 定位；其余用 xdg-open 打开父目录。
    文件夹：直接打开该目录。timeout 防止资源管理器异常时线程被长期挂起。
    """
    if os.name == "nt":
        if is_file:
            subprocess.run(["explorer", "/select," + path], check=False, timeout=15)
        else:
            subprocess.run(["explorer", path], check=False, timeout=15)
    elif sys.platform == "darwin":
        subprocess.run(["open", "-R" if is_file else path], check=False, timeout=15)
    else:
        parent = os.path.dirname(path) if is_file else path
        subprocess.run(["xdg-open", parent], check=False, timeout=15)


def _load_rebuild_deps(project_dir: str, config_name: str) -> Tuple[Any, Any, Any, Any, list, list, list]:
    """加载问题重建所需的项目配置与字典；任一失败时返回 (None, None, None, None, [], [], [])。

    Returns 7 元组：proj_config, pre_dic, post_dic, gpt_dic, tPlugins, h_check_words, forbidden_words。
    - h_check_words：h 场景禁用词（forbiddenDictH，回退 hCheckDict）。
    - forbidden_words：非 h 场景禁用词（forbiddenDictNonH）。

    Args:
        project_dir: 项目绝对路径。
        config_name: 请求中的配置文件名，不存在时回退 config.inc.yaml / config.yaml。

    Returns:
        (proj_config, pre_dic, post_dic, gpt_dic, tPlugins, h_check_words, forbidden_words)
    """
    proj_config = pre_dic = post_dic = gpt_dic = None
    tPlugins = []
    h_check_words = []
    forbidden_words = []
    try:
        from GalTransl.ConfigHelper import CProjectConfig, initDictList
        from GalTransl.Dictionary import CNormalDic, CGptDict
        from GalTransl.Problem import load_h_check_words
        # Resolve the real config file: real projects use config.inc.yaml,
        # but the request may default to config.yaml.
        if not os.path.isfile(os.path.join(project_dir, config_name)):
            for _cand in ("config.inc.yaml", "config.yaml"):
                if os.path.isfile(os.path.join(project_dir, _cand)):
                    config_name = _cand
                    break
        _migrate_h_check_dict_config(project_dir, config_name)
        # 兜底：项目字典配置缺少公共字典时自动补全（幂等，失败不影响主流程）
        _ensure_common_dicts_in_config(project_dir, config_name)
        proj_config = CProjectConfig(project_dir, config_name)
        dict_cfg = proj_config.getDictCfgSection()
        pre_dic_list = dict_cfg.get("preDict", [])
        post_dic_list = dict_cfg.get("postDict", [])
        gpt_dic_list = dict_cfg.get("gpt.dict", [])
        # 禁用词字典：h 部分优先 forbiddenDictH，未配置时回退旧 hCheckDict
        h_dict_list = dict_cfg.get("forbiddenDictH", dict_cfg.get("hCheckDict", []))
        # 禁用词字典：非 h 部分
        nh_dict_list = dict_cfg.get("forbiddenDictNonH", [])
        default_dic_dir = dict_cfg.get("defaultDictFolder", "")
        pre_dic = CNormalDic(initDictList(pre_dic_list, default_dic_dir, project_dir))
        post_dic = CNormalDic(initDictList(post_dic_list, default_dic_dir, project_dir))
        gpt_dic = CGptDict(initDictList(gpt_dic_list, default_dic_dir, project_dir))
        h_check_words = load_h_check_words(initDictList(h_dict_list, default_dic_dir, project_dir))
        forbidden_words = load_h_check_words(initDictList(nh_dict_list, default_dic_dir, project_dir))
        if dict_cfg.get("sortDict", True):
            pre_dic.sort_dic()
            post_dic.sort_dic()
            gpt_dic.sort_dic()
        try:
            tPlugins = proj_config.tPlugins
        except Exception:
            tPlugins = []
    except Exception:
        proj_config = pre_dic = post_dic = gpt_dic = None
        tPlugins = []
        h_check_words = []
        forbidden_words = []
    return proj_config, pre_dic, post_dic, gpt_dic, tPlugins, h_check_words, forbidden_words


def _rebuild_trans_list_with_postprocess(
    entries: list,
    proj_config: Any,
    pre_dic: Any,
    post_dic: Any,
    tPlugins: list,
) -> list:
    """把缓存条目重建为 CSentense 列表，并执行完整的前/后处理（与旧分支构建输出一致）。

    逐条重建 CSentense（pre_src/post_src/pre_dst/proofread_zh 等），链接
    prev/next 上下文后，依次运行 preprocess_trans_list（插件 before_src →
    analyse_dialogue 剥引号 → pre_dic 替换）与 postprocess_trans_list
    （插件 before_dst → recover_dialogue_symbol 补回引号 → post_dic 替换 →
    插件 after_dst）。返回的 trans_list 中 tran.post_dst 即最终构建输出译文。

    Args:
        entries: 缓存条目列表（dict），字段与缓存文件一致。
        proj_config / pre_dic / post_dic / tPlugins: _load_rebuild_deps 的产物。

    Returns:
        重建并后处理完成的 CSentense 列表（跳过 post_src 为空的条目）。
    """
    from GalTransl.CSentense import CSentense
    from GalTransl.Frontend.LLMTranslate import preprocess_trans_list, postprocess_trans_list

    trans_list = []
    for e in entries:
        speaker = e.get("name", "")
        if isinstance(speaker, list):
            speaker = "/".join(speaker)
        pre_src = e.get("pre_src", "") or e.get("pre_jp", "")
        post_src = e.get("post_src", "") or e.get("post_jp", "")
        pre_dst = e.get("pre_dst", "") or e.get("pre_zh", "")
        proofread_dst = e.get("proofread_dst", "") or e.get("proofread_zh", "")
        if post_src == "":
            continue
        s = CSentense(pre_src, speaker if speaker else "", e.get("index", 0))
        s.post_src = pre_src
        s.pre_dst = pre_dst
        s.proofread_zh = proofread_dst
        s.post_dst = proofread_dst if proofread_dst else pre_dst
        s.trans_by = e.get("trans_by", "")
        s.proofread_by = e.get("proofread_by", "")
        s.trans_conf = e.get("trans_conf", 0)
        s.doub_content = e.get("doub_content", "")
        s.unknown_proper_noun = e.get("unknown_proper_noun", "")
        # bool 归一化：请求体/缓存里非 bool 值（如 "false"）不误判为真
        s.skip_check = bool(e.get("skip_check", False))
        s.suspected_error = e.get("suspected_error", "")
        s.tone_issue = e.get("tone_issue", "")
        trans_list.append(s)

    for i, s in enumerate(trans_list):
        if i > 0:
            s.prev_tran = trans_list[i - 1]
        if i < len(trans_list) - 1:
            s.next_tran = trans_list[i + 1]

    if pre_dic and proj_config:
        preprocess_trans_list(trans_list, proj_config, pre_dic, tPlugins or None)
    if post_dic and proj_config:
        postprocess_trans_list(trans_list, proj_config, post_dic, tPlugins or None)
    return trans_list


def _run_problem_detection(
    entries: list,
    proj_config: Any,
    pre_dic: Any,
    post_dic: Any,
    gpt_dic: Any,
    tPlugins: list,
    h_ranges: list = None,
    h_check_words: list = None,
    forbidden_words: list = None,
) -> Tuple[list, bool]:
    """重建 CSentense 并全量运行问题检测（只算不落盘）。

    Args:
        entries: 缓存条目列表（dict）。
        proj_config / pre_dic / post_dic / gpt_dic / tPlugins: _load_rebuild_deps 的产物。
        h_ranges: H 剧情区间列表 [(lo, hi), ...]（缓存条目 index 口径），默认 None 不检测。
        h_check_words: H 场景用词不当检测词库（list[str]），默认 None 不检测。
        forbidden_words: 非 h 场景禁用词库（list[str]），默认 None 不检测（本次未搭建）。

    Returns:
        (results, ok)：results 与 entries 等长，每项为
        {"index", "problem", "post_dst_preview", "skip_check"}；
        ok 为 False 表示 find_problems 抛出异常，此时所有 problem 均为空，
        调用方不得据此覆写已有 problem（避免误删检测结果）。
    """
    from GalTransl.Problem import find_problems

    trans_list = _rebuild_trans_list_with_postprocess(
        entries, proj_config, pre_dic, post_dic, tPlugins
    )

    ok = True
    if trans_list:
        try:
            find_problems(trans_list, proj_config, gpt_dic, h_ranges, h_check_words, forbidden_words)
        except Exception:
            ok = False
            LOGGER.error("问题检测 find_problems 执行失败", exc_info=True)

    results = []
    ti = 0
    for e in entries:
        post_src_val = e.get("post_src", "") or e.get("post_jp", "")
        if post_src_val == "":
            results.append({
                "index": e.get("index", 0),
                "problem": "",
                "post_dst_preview": None,
                "skip_check": bool(e.get("skip_check", False)),
            })
            continue
        tran = trans_list[ti]
        ti += 1
        results.append({
            "index": e.get("index", 0),
            "problem": tran.problem if ok else "",
            "post_dst_preview": tran.post_dst,
            "skip_check": tran.skip_check,
        })
    return results, ok


def recheck_pass3_cache_files(
    cache_dir: str,
    proj_config: Any,
    pre_dic: Any,
    post_dic: Any,
    gpt_dic: Any,
    tPlugins: list,
    h_check_words: list = None,
    forbidden_words: list = None,
    target_files: list[str] | None = None,
) -> int:
    """对 pass3_cache 下的缓存 json 重新运行问题检测并写回 problem。

    停止翻译合并增量缓存后调用，等效于对每个文件执行一次前端"保存并重检问题"，
    使新合并入的译文立即带上最新的 problem / post_dst_preview 标注。

    Args:
        cache_dir: 缓存根目录（projectDir/transl_cache）。
        target_files: 待重检的主缓存 json 绝对路径列表（通常为 compact 合并的
            结果）；为 None 时全量扫描 pass3_cache 下的 *.json。
        其余参数同 _run_problem_detection。

    Returns:
        int: 成功重检并写回的文件数。
    """
    import orjson

    pass3_dir = os.path.join(cache_dir, PASS3_CACHE_DIR)
    if not os.path.isdir(pass3_dir):
        return 0

    if target_files is None:
        targets = [
            os.path.join(pass3_dir, n)
            for n in sorted(os.listdir(pass3_dir))
            if n.endswith(".json")
        ]
    else:
        targets = [p for p in target_files if p.endswith(".json") and os.path.isfile(p)]

    rechecked = 0
    for file_path in targets:
        try:
            with open(file_path, "rb") as f:
                entries = orjson.loads(f.read())
            if not isinstance(entries, list) or not entries:
                continue

            project_dir = os.path.dirname(cache_dir)
            h_ranges = [
                (r["lo"], r["hi"])
                for r in _resolve_cache_h_ranges(
                    project_dir, os.path.relpath(file_path, cache_dir)
                ).get("h_ranges", [])
            ]
            results, ok = _run_problem_detection(
                entries, proj_config, pre_dic, post_dic, gpt_dic, tPlugins, h_ranges, h_check_words, forbidden_words
            )
            if not ok:
                # 检测失败时不得覆写已有 problem（与 /cache/check 语义一致），跳过该文件
                continue

            # 与 /cache/check persist=true 相同的写回逻辑
            for e, r in zip(entries, results):
                post_src_val = e.get("post_src", "") or e.get("post_jp", "")
                if post_src_val == "":
                    continue
                if r["problem"]:
                    e["problem"] = r["problem"]
                elif "problem" in e:
                    del e["problem"]
                e["post_dst_preview"] = r["post_dst_preview"]
                if r["skip_check"]:
                    e["skip_check"] = True
                elif "skip_check" in e:
                    del e["skip_check"]

            with open(file_path, "wb") as f:
                f.write(orjson.dumps(entries, option=orjson.OPT_INDENT_2))
            rechecked += 1
        except Exception as exc:
            LOGGER.warning(f"自动重检缓存失败：{file_path}: {exc}")

    return rechecked


def _append_engine_log(project_dir: str, message: str) -> None:
    """把消息追加写入项目目录的 GalTransl.log（绕过 LOGGER handler 生命周期）。

    校对保存等操作发生在翻译任务之外，此时 Runner 动态挂载的 job 级 handler 已移除，
    LOGGER.info 无处可去；此处直接以追加模式写文件，保证审计日志始终可查。
    仅写后端引擎日志文件，不经过 /api/log，因此不会进入 frontend.log。
    """
    try:
        pdir = str(project_dir or "").strip()
        if not pdir:
            return
        os.makedirs(pdir, exist_ok=True)
        ts = time.strftime("%m-%d %H:%M:%S")
        with open(os.path.join(pdir, "GalTransl.log"), "a", encoding="utf-8") as _f:
            _f.write(f"[{ts}][INFO] {message}\n")
    except OSError:
        return


def _cache_entry_key(entry: Any) -> Any:
    """缓存条目的稳定标识：优先 __cache_key，其次 index，最后 (name, pre_src)。"""
    if not isinstance(entry, dict):
        return id(entry)
    ck = entry.get("__cache_key")
    if ck:
        return ("__cache_key", ck)
    idx = entry.get("index")
    if idx is not None:
        return ("index", idx)
    return ("src", entry.get("name", ""), entry.get("pre_src", ""))


def _entry_modified(old: dict, new: dict) -> bool:
    """判断新旧条目在译文相关字段上是否有差异（用于保存时审计 diff）。"""
    for f in ("pre_dst", "proofread_dst", "alt_dst", "problem", "skip_check"):
        if old.get(f) != new.get(f):
            return True
    return False


def _fmt_indices(indices: list[Any]) -> str:
    """把条目 index 列表格式化为日志文本，过长时截断，避免日志膨胀。"""
    cleaned = [str(i) for i in indices if i is not None]
    if len(cleaned) > 20:
        return "[" + ",".join(cleaned[:20]) + f"...共{len(cleaned)}条]"
    return "[" + ",".join(cleaned) + "]"




def _collect_cache_files(cache_dir: str) -> list[str]:
    """递归收集可构建的翻译缓存文件（相对 cache_dir 的 '/' 路径），跳过元数据。

    翻译缓存位于 pass3_cache/*.txt.json（或顶层 *.json）；pass0 GlobalPrompt、
    pass1 *.meta.json、pass2 *.batch.json 为元数据，不参与构建。
    """
    files: list[str] = []
    for root, _dirs, names in os.walk(cache_dir):
        for name in sorted(names):
            if not name.endswith(".json"):
                continue
            # 跳过元数据与示例/临时文件（init 会生成 _示例缓存文件.json）
            if name.endswith(".meta.json") or name.endswith(".batch.json"):
                continue
            if name in ("GlobalPrompt.json", "PlotRouteMap.json") or name.startswith("_"):
                continue
            rel = os.path.relpath(os.path.join(root, name), cache_dir).replace("\\", "/")
            files.append(rel)
    return sorted(files)


def _extract_dialogue_symbols(text: str) -> tuple[str, str]:
    """从原文提取对话引号对（「」/『』，支持嵌套），返回 (左符号, 右符号)。

    与 CSentense.analyse_dialogue 的剥离规则保持一致：仅当首字符属于
    「『 且尾字符属于 」』 且 ord 差为 1（成对）时逐层剥离。
    """
    left, right = "", ""
    s = text
    while s and s[:1] in "「『" and s[-1:] in "」』" and ord(s[-1]) - ord(s[:1]) == 1:
        left = left + s[:1]
        right = s[-1:] + right
        s = s[1:-1]
    return left, right


def _pick_output_dst(ce: dict) -> tuple[str, bool]:
    """从缓存条目挑选构建输出的译文，返回 (译文, 是否需要补对话引号)。

    优先级 proofread_dst > post_dst_preview > pre_dst：
    - proofread_dst 为用户校对文本（无引号），需要补对话引号；
    - post_dst_preview 为翻译后处理产物（含引号、后处理字典与插件结果），
      仅当剥引号后与当前 pre_dst 一致（即用户未在校对页改过译文）时采用，
      否则视为过期快照丢弃（用户改 pre_dst 后 preview 不会同步更新）；
    - pre_dst 为初译或用户校对修改后的译文（无引号），需要补对话引号。
    """
    if ce.get("proofread_dst"):
        return ce["proofread_dst"], True
    preview = ce.get("post_dst_preview") or ""
    if preview:
        left_sym, right_sym = _extract_dialogue_symbols(preview)
        inner = preview[len(left_sym):]
        if right_sym:
            inner = inner[:-len(right_sym)]
        if inner == (ce.get("pre_dst") or ""):
            # preview 与当前 pre_dst 一致：含引号+后处理，直接采用
            return preview, False
    return ce.get("pre_dst") or "", True


def _build_project_output(
    project_dir: str,
    *,
    filenames: list[str] | None = None,
) -> dict[str, Any]:
    """从缓存文件构建输出文件（output/gt_output/）。

    读取缓存（transl_cache，含 pass3_cache 嵌套）与 input/gt_input 中的原始 JSON，
    将缓存中的译文（proofread_dst / post_dst_preview / pre_dst）合并回原始 JSON，
    恢复对话引号（「」/『』）并应用 name 替换表后写出到 output/gt_output/。
    """
    import orjson

    input_dir = os.path.join(project_dir, INPUT_FOLDERNAME)
    output_dir = os.path.join(project_dir, OUTPUT_FOLDERNAME)
    cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)

    if not os.path.isdir(input_dir):
        return {"success": False, "error": f"input dir not found: {input_dir}"}
    if not os.path.isdir(cache_dir):
        return {"success": False, "error": f"cache dir not found: {cache_dir}"}

    # 确定要处理的文件列表（支持相对子路径如 pass3_cache/xx.txt.json，或纯文件名）
    cache_names = _normalize_cache_filenames(filenames) if filenames else _collect_cache_files(cache_dir)

    # 项目级依赖只加载一次（全部文件共用）：配置 + 字典 + 插件，用于重建译文路径；
    # 失败时降级为缓存直读（不影响构建）。
    deps_ok = False
    _proj_config = _pre_dic = _post_dic = None
    _tPlugins = []
    try:
        _deps = _load_rebuild_deps(project_dir, _detect_config_file(project_dir))
        _proj_config, _pre_dic, _post_dic, _gpt_dic, _tPlugins, _h_words, _forbidden = _deps
        deps_ok = _proj_config is not None
    except Exception:
        LOGGER.warning(f"[build-output] 项目依赖加载失败，降级为缓存直读：{project_dir}")

    # name 替换表（SRC→DST），供全部输出文件 name/names 字段替换
    name_dict = _load_project_name_dict(project_dir)

    built_files: list[str] = []
    errors: list[str] = []
    total_built = 0

    for cache_name in cache_names:
        cache_path = os.path.join(cache_dir, cache_name)
        if not os.path.isfile(cache_path):
            errors.append(f"cache file not found: {cache_name}")
            continue

        # 读取缓存文件（CacheEntry[]）
        try:
            with open(cache_path, "rb") as f:
                cache_entries: list[dict] = orjson.loads(f.read())
        except Exception as exc:
            errors.append(f"read cache {cache_name} failed: {exc}")
            continue

        # 找对应的 input 文件（gt_input 顶层，按缓存 basename 匹配；保留分块后缀回退）
        base = os.path.basename(cache_name)
        input_path = os.path.join(input_dir, base)
        input_base = base
        if not os.path.isfile(input_path):
            alt_base = base.rsplit("-", 1)[0] + ".json" if "-" in base else None
            if alt_base and os.path.isfile(os.path.join(input_dir, alt_base)):
                input_path = os.path.join(input_dir, alt_base)
                input_base = alt_base
            else:
                errors.append(f"input file not found for {cache_name}")
                continue

        try:
            with open(input_path, "rb") as f:
                input_data: list[dict] = orjson.loads(f.read())
        except Exception as exc:
            errors.append(f"read input {os.path.basename(input_path)} failed: {exc}")
            continue

        # 缓存条目建立索引映射：pre_src → 条目
        cache_map: dict[str, dict] = {}
        for ce in cache_entries:
            src = ce.get("pre_src", "")
            if src:
                cache_map[src] = ce

        # 优先走"重建 CSentense + 前后处理"路径（与旧分支构建输出一致：恢复引号、
        # 重跑后处理字典、尊重用户校对值）；依赖加载失败时降级为缓存直读。
        rebuilt_map: dict[str, str] = {}
        if deps_ok:
            try:
                rebuilt_trans = _rebuild_trans_list_with_postprocess(
                    cache_entries, _proj_config, _pre_dic, _post_dic, _tPlugins
                )
                for _t in rebuilt_trans:
                    if _t.pre_src:
                        rebuilt_map[_t.pre_src] = _t.post_dst
            except Exception:
                LOGGER.warning(f"[build-output] 单文件重建失败，降级为缓存直读：{cache_name}")

        # 合并译文回 input JSON
        updated_count = 0
        for item in input_data:
            msg = item.get("message", "")
            if msg in cache_map:
                ce = cache_map[msg]
                if deps_ok and msg in rebuilt_map:
                    # 重建路径：post_dst 已含引号+后处理字典+用户校对值
                    dst = rebuilt_map[msg]
                else:
                    # 降级路径：优先 proofread_dst，其次 post_dst_preview，兜底 pre_dst
                    dst, needs_quotes = _pick_output_dst(ce)
                    if dst and needs_quotes:
                        # 来源无引号（proofread_dst/pre_dst，或 preview 过期回退 pre_dst）时，
                        # 按原句补回对话引号「」/『』
                        left_sym, right_sym = _extract_dialogue_symbols(
                            ce.get("pre_src", msg)
                        )
                        if left_sym or right_sym:
                            dst = left_sym + dst + right_sym
                if dst:
                    # 将译文换行统一为原 message 的换行符类型（原文无换行则保持原样）
                    n_symbols = get_n_symbol(msg)
                    if n_symbols:
                        dst = dst.replace("\r\n", "\n").replace("\n", n_symbols[0])
                    item["message"] = dst
                    updated_count += 1
            # name/names 字段按替换表替换（无替换表时原样保留）
            if "name" in item:
                item["name"] = _lookup_name(item.get("name", ""), name_dict)
            if "names" in item and isinstance(item.get("names"), list):
                item["names"] = _lookup_name(item["names"], name_dict)

        # 写出 output（与 input 同名，位于 gt_output 顶层）
        output_path = os.path.join(output_dir, input_base)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        try:
            save_json(output_path, input_data)
            built_files.append(input_base)
            total_built += 1
        except Exception as exc:
            errors.append(f"write output {cache_name} failed: {exc}")

    return {
        "success": True,
        "project_dir": project_dir,
        "built_files": built_files,
        "total_built": total_built,
        "errors": errors,
    }


def _normalize_cache_filenames(filenames: list[str]) -> list[str]:
    """规范化构建/校验的文件列表：支持相对子路径与纯文件名，过滤非 .json 与穿越路径。"""
    out: list[str] = []
    for f in filenames:
        norm = os.path.normpath(str(f).replace("\\", "/"))
        if not norm.endswith(".json"):
            continue
        if os.path.isabs(norm) or norm == ".." or norm.startswith("../"):
            continue
        out.append(norm)
    return out


def _is_numeric_index(value: Any) -> bool:
    """判断条目 index 是否为数字（int 或数字字符串，口径与 CSplitter 一致）。"""
    if isinstance(value, int):
        return True
    return isinstance(value, str) and value.isdigit()


def _resolve_cache_row_offset(
    project_dir: str, base: str, input_base: str
) -> int:
    """计算缓存文件首条相对输入文件的全局行号偏移（0 表示条目 index 即全局行号）。

    仅在「原文件无显式 index 且 splitFile 分片」时非 0：分片后每个缓存文件
    的条目 index 从 1 重新计，需加上该分片在整文件中的起始偏移（含交叉句）。
    其余场景（原文件带 index / 单文件不切分）偏移恒为 0，与批次区间直接对齐。

    Args:
        project_dir: 项目根目录。
        base: 缓存文件名（含扩展名，如 xxx_0.json）。
        input_base: 对应输入文件名（如 xxx.json）。

    Returns:
        行号偏移（>=0）。
    """
    m = re.match(r"^(.*)_(\d+)\.json$", base)
    if not m:
        # 无分块后缀：单文件，条目 index 即全局行号
        LOGGER.debug(f"[h-ranges] {base}: 无分块后缀，offset=0")
        return 0
    chunk_index = int(m.group(2))
    input_path = os.path.join(project_dir, INPUT_FOLDERNAME, input_base)
    if not os.path.isfile(input_path):
        LOGGER.debug(f"[h-ranges] {base}: 输入文件不存在 {input_path}，offset=0")
        return 0
    try:
        with open(input_path, "rb") as f:
            input_data = json.load(f)
    except Exception:
        LOGGER.debug(f"[h-ranges] {base}: 输入文件解析失败 {input_path}，offset=0")
        return 0
    if not isinstance(input_data, list) or not input_data:
        LOGGER.debug(f"[h-ranges] {base}: 输入数据为空，offset=0")
        return 0
    # 原文件显式带 index（int 或数字字符串，与 CSplitter 口径一致）：
    # 缓存条目 index 即原 index（全局行号），无需偏移
    first = input_data[0] if isinstance(input_data[0], dict) else {}
    if _is_numeric_index(first.get("index")):
        LOGGER.debug(f"[h-ranges] {base}: 原文件带显式 index，offset=0")
        return 0
    # 按当前配置重建切分，取 chunk_index 对应分片的起始偏移（含交叉句）
    try:
        cfg = CProjectConfig(project_dir)
        common = cfg.getCommonConfigSection()
    except Exception:
        LOGGER.debug(f"[h-ranges] {base}: 配置读取失败，offset=0")
        return 0
    val = common.get("splitFile", "no")
    if val not in ("Num", "Equal"):
        LOGGER.debug(f"[h-ranges] {base}: splitFile={val} 不分片，offset=0")
        return 0
    try:
        split_file_num = int(common.get("splitFileNum", -1))
        cross_num = int(common.get("splitFileCrossNum", 0))
        if split_file_num <= 0:
            split_file_num = int(common.get("workersPerProject", 1)) or 1
    except (TypeError, ValueError):
        LOGGER.debug(f"[h-ranges] {base}: 分片参数非法，offset=0")
        return 0
    try:
        if val == "Num":
            splitter = DictionaryCountSplitter(split_file_num, cross_num)
        else:
            splitter = EqualPartsSplitter(split_file_num, cross_num)
        chunks = splitter.split(input_data, file_path=input_path)
    except Exception:
        LOGGER.debug(f"[h-ranges] {base}: 重建切分失败，offset=0")
        return 0
    if chunk_index >= len(chunks):
        LOGGER.debug(f"[h-ranges] {base}: chunk 索引越界（{chunk_index} >= {len(chunks)}），offset=0")
        return 0
    chunk = chunks[chunk_index]
    offset = max(0, chunk.start_index - chunk.cross_num)
    LOGGER.debug(
        f"[h-ranges] {base}: chunk={chunk_index}, splitFile={val}, "
        f"splitFileNum={split_file_num}, crossNum={cross_num}, offset={offset}"
    )
    return offset


# 项目 H 档位阈值进程级缓存：project_dir -> (配置文件 mtime, 阈值或 None)
_PROJECT_H_THRESHOLD_CACHE: dict[str, Tuple[float, Optional[float]]] = {}


def _project_h_threshold(project_dir: str) -> Optional[float]:
    """读取项目配置的 H 场景判定阈值（internals.hLevels.intimate 百分比 / 100）。

    仅做轻量配置读取（不载入字典），以配置文件 mtime 为签名做进程级缓存，
    使校对界面的 H 区间与该项目的提示词注入侧阈值保持一致。
    无配置文件 / 读取失败 / 阈值非法时返回 None，调用方回退默认 0.5。
    """
    config_path = ""
    for cand in ("config.inc.yaml", "config.yaml"):
        p = os.path.join(project_dir, cand)
        if os.path.isfile(p):
            config_path = p
            break
    if not config_path:
        return None
    try:
        mtime = os.path.getmtime(config_path)
    except OSError:
        return None
    cached = _PROJECT_H_THRESHOLD_CACHE.get(project_dir)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    threshold: Optional[float] = None
    try:
        from GalTransl.ConfigHelper import CProjectConfig
        cfg = CProjectConfig(project_dir, os.path.basename(config_path))
        threshold = resolve_h_thresholds(cfg)[1]
    except Exception as exc:
        LOGGER.debug(f"[h-ranges] 读取项目 H 档位阈值失败，回退默认：{exc}")
    _PROJECT_H_THRESHOLD_CACHE[project_dir] = (mtime, threshold)
    return threshold


def _resolve_cache_h_ranges(project_dir: str, cache_name: str) -> dict[str, Any]:
    """计算给定翻译缓存文件中的 H 剧情区间（换算为缓存条目 index 口径）。

    数据源：transl_cache/pass2_cache/{输入名}.batch.json 的「批次」数组中
    h 达到项目判定线（internals.hLevels.intimate，默认 0.5）的区间。相邻 h 批次
    （下一区间 lo <= 上一区间 hi + 1）合并为一条，故多个分散 H 段各自成区间。区间的 lo/hi 已换算为该缓存文件
    条目 index 的口径（splitFile 分片时含偏移），前端可直接按条目 index 匹配画线。
    每个区间额外带 h（合并段内的峰值强度），供前端展示 H 强度分级。

    Args:
        project_dir: 项目根目录。
        cache_name: 缓存文件相对路径或纯文件名（如 pass3_cache/xx.txt.json）。

    Returns:
        {"batch_exists": bool, "has_h": bool,
         "h_ranges": [{"lo": int, "hi": int, "h": float}, ...]}
    """
    cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)
    norm = os.path.normpath(cache_name.replace("\\", "/"))
    if norm == ".." or norm.startswith(".." + os.sep) or os.path.isabs(norm):
        return {"batch_exists": False, "has_h": False, "h_ranges": []}
    if not os.path.isfile(os.path.join(cache_dir, norm)):
        return {"batch_exists": False, "has_h": False, "h_ranges": []}

    base = os.path.basename(cache_name)
    # 候选输入名：缓存名即输入名（输入文件本身带扩展名，如 story.txt.json），
    # 分片缓存为 {输入名}_{N}.json，再剥离 _N 后缀作为第二候选。
    # 注意 group(1) 已含输入文件扩展名，不能再补 ".json"。
    input_candidates = [base]
    m = re.match(r"^(.*)_(\d+)\.json$", base)
    if m:
        input_candidates.append(m.group(1))

    batch_path = None
    input_base = None
    for cand in input_candidates:
        p = os.path.join(project_dir, CACHE_FOLDERNAME, PASS2_CACHE_DIR, f"{cand}.batch.json")
        if os.path.isfile(p):
            batch_path = p
            input_base = cand
            break
    if batch_path is None:
        return {"batch_exists": False, "has_h": False, "h_ranges": []}

    try:
        with open(batch_path, "r", encoding="utf-8") as f:
            batch_data = json.load(f)
    except Exception:
        # 批次文件存在但损坏：与「文件不存在」区分开，标记 batch_exists=true 并告警
        LOGGER.warning(f"[h-ranges] 批次文件损坏，无法解析 H 区间: {batch_path}")
        return {"batch_exists": True, "has_h": False, "h_ranges": []}

    batches = batch_data.get("批次", []) if isinstance(batch_data, dict) else []
    # H 场景判定线取项目配置（internals.hLevels.intimate），与该项目的提示词注入侧
    # 口径一致；配置缺失时回退默认 0.5。
    h_threshold = _project_h_threshold(project_dir)
    # h_global 每项为 [lo, hi, h_value]（h 值经 coerce_h_value 归一，旧布尔兼容）
    h_global: list[list] = []
    for b in batches:
        if not isinstance(b, dict):
            continue
        h_val = coerce_h_value(b.get("h", b.get("H", False)))
        if not is_h_value(h_val, h_threshold):
            continue
        seg = b.get("区间")
        if not isinstance(seg, list) or len(seg) < 2:
            continue
        try:
            lo, hi = int(seg[0]), int(seg[1])
        except (TypeError, ValueError):
            continue
        if lo <= hi:
            h_global.append([lo, hi, h_val])
    if not h_global:
        return {"batch_exists": True, "has_h": False, "h_ranges": []}
    h_global.sort(key=lambda x: (x[0], x[1]))

    # 合并相邻 h 批次为多条连续区间；合并段 h 值取峰值强度（前端展示该合并段的最高
    # 强度。仅作概要标签；问题检测/字典分流只看 lo/hi，不受峰值影响）
    merged: list[list] = [list(h_global[0])]
    for lo, hi, h_val in h_global[1:]:
        if lo <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], hi)
            merged[-1][2] = max(merged[-1][2], h_val)
        else:
            merged.append([lo, hi, h_val])

    offset = _resolve_cache_row_offset(project_dir, base, input_base)
    h_ranges = []
    for lo, hi, h_val in merged:
        # 半段跨分片边界时 lo 可能落在 offset 之前，clamp 到 1 避免负 index；
        # 整段都在 offset 之前（hi 也小于 1）则丢弃
        lo_shifted = max(1, lo - offset)
        hi_shifted = hi - offset
        if hi_shifted >= 1:
            h_ranges.append({"lo": lo_shifted, "hi": hi_shifted, "h": round(h_val, 3)})
    return {"batch_exists": True, "has_h": bool(h_ranges), "h_ranges": h_ranges}


def _validate_build(project_dir: str, filenames: list[str] | None = None) -> dict[str, Any]:
    """构建前校验：缓存与输入文件数量、缓存内容完整性。仅提示、不阻断构建。

    Returns:
        {"ok", "input_total", "cache_total", "missing_files", "content_issues"}
    """
    import orjson

    input_dir = os.path.join(project_dir, INPUT_FOLDERNAME)
    cache_dir = os.path.join(project_dir, CACHE_FOLDERNAME)

    cache_names = (
        _normalize_cache_filenames(filenames) if filenames else _collect_cache_files(cache_dir)
    )

    input_names = []
    if os.path.isdir(input_dir):
        input_names = sorted(n for n in os.listdir(input_dir) if n.endswith(".json"))
    cache_bases = {os.path.basename(c) for c in cache_names}

    def _has_cache(inp: str) -> bool:
        # 同名缓存，或分块缓存（input 名去掉 .json 后加 "-N.json"）
        if inp in cache_bases:
            return True
        stem = inp[:-5]
        return any(c.startswith(stem + "-") and c.endswith(".json") for c in cache_bases)

    missing_files = [i for i in input_names if not _has_cache(i)]

    content_issues: list[dict[str, Any]] = []
    for cache_name in cache_names:
        cache_path = os.path.join(cache_dir, cache_name)
        if not os.path.isfile(cache_path):
            content_issues.append({"file": cache_name, "issue": "缓存文件不存在"})
            continue
        try:
            with open(cache_path, "rb") as f:
                entries = orjson.loads(f.read())
        except Exception as exc:
            content_issues.append({"file": cache_name, "issue": f"JSON 解析失败：{exc}"})
            continue
        if not isinstance(entries, list):
            content_issues.append({"file": cache_name, "issue": "内容不是 JSON 数组"})
            continue
        indexes: list[int] = []
        no_index = 0
        for e in entries:
            if not isinstance(e, dict) or e.get("index") is None:
                no_index += 1
            elif isinstance(e.get("index"), int):
                indexes.append(e["index"])
        if no_index:
            content_issues.append({"file": cache_name, "issue": f"{no_index} 个条目缺少 index"})
        idxs = sorted(indexes)
        gaps = []
        if idxs:
            if idxs[0] != 1:
                # 起始缺失：index 从 idxs[0] 开始，1 到 idxs[0]-1 全部缺失
                gaps.append(f"起始缺失 1→{idxs[0] - 1}（{idxs[0] - 1} 条）")
            gaps.extend(f"{a}→{b}" for a, b in zip(idxs, idxs[1:]) if b - a != 1)
        if gaps:
            content_issues.append(
                {"file": cache_name, "issue": f"索引不连续：{'、'.join(gaps[:5])}"}
            )

    return {
        "ok": not missing_files and not content_issues,
        "input_total": len(input_names),
        "cache_total": len(cache_names),
        "missing_files": missing_files,
        "content_issues": content_issues,
    }


def _check_batch_size(project_dir: str, config_name: str) -> dict[str, Any]:
    """批次划分预检（纯计算，无模型调用）：按 internals.forbatchmeta 计算最大可自然划分行数。

    Returns:
        {"max_natural_lines", "oversize_files": [{"filename", "lines"}], "applicable": True}；
        配置读取失败时抛异常，由调用方决定如何呈现。
    """
    config_data = _read_yaml_file(os.path.join(project_dir, config_name))
    fb = (config_data.get("internals") or {}).get("forbatchmeta") or {}
    try:
        max_batch_size = max(1, int(fb.get("max_batch_size", 64)))
    except (TypeError, ValueError):
        max_batch_size = 64
    try:
        max_batches = max(1, int(fb.get("max_batches", 20)))
    except (TypeError, ValueError):
        max_batches = 20
    max_natural_lines = int(0.9 * max_batch_size * max_batches)
    oversize_files: list[dict[str, Any]] = []
    input_dir = os.path.join(project_dir, INPUT_FOLDERNAME)
    for entry in _list_dir_entries(input_dir, count_json_entries=True):
        name = entry.get("name", "")
        if not name.endswith(".json"):
            continue
        if name in ("FileMetaData.json", "BatchMetadata.json"):
            continue
        lines = entry.get("entry_count") or 0
        if lines > max_natural_lines:
            oversize_files.append({"filename": name, "lines": lines})
    return {
        "max_natural_lines": max_natural_lines,
        "oversize_files": oversize_files,
        "applicable": True,
    }


def _list_dir_entries(
    dir_path: str,
    *,
    count_json_entries: bool = False,
    skip_suffixes: tuple = (),
) -> list[dict[str, Any]]:
    """List files in a directory with basic metadata.

    skip_suffixes 用于过滤不该露面的中间文件：缓存目录传 (CACHE_TEMP_SUFFIX,)，
    免得界面把 <缓存>.json.tmp 这种快照残留当成一个缓存文件（没有条目数、读不全）。
    """
    entries = []
    if not os.path.isdir(dir_path):
        return entries
    for name in sorted(os.listdir(dir_path)):
        if skip_suffixes and name.endswith(tuple(skip_suffixes)):
            continue
        full = os.path.join(dir_path, name)
        stat = os.stat(full) if os.path.isfile(full) else None
        entry = {
            "name": name,
            "is_file": os.path.isfile(full),
            "size": stat.st_size if stat else 0,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat() if stat else "",
        }
        if count_json_entries and name.endswith(".json") and os.path.isfile(full):
            try:
                with open(full, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    entry["entry_count"] = len(data)
            except Exception:
                pass
        entries.append(entry)
    return entries


def _build_cache_tree(dir_path: str, prefix: str = "", count_entries: bool = True) -> list[dict[str, Any]]:
    """递归构建缓存目录树（含子目录 pass1_cache / pass2_cache 等）。

    返回节点列表，每个节点:
      - 文件: {name, path(相对缓存根, '/'分隔), is_file:True, size, modified, is_metadata?, entry_count?}
      - 目录: {name, path, is_file:False, size:0, modified:"", children:[...]}
    """
    nodes: list[dict[str, Any]] = []
    if not os.path.isdir(dir_path):
        return nodes
    for name in sorted(os.listdir(dir_path)):
        if name.endswith(CACHE_TEMP_SUFFIX):
            # 快照中间文件残留：没有条目数、读不全，不作为缓存文件展示
            continue
        full = os.path.join(dir_path, name)
        rel = os.path.join(prefix, name) if prefix else name
        rel = rel.replace(os.sep, "/")
        if os.path.isfile(full):
            st = os.stat(full)
            node: dict[str, Any] = {
                "name": name,
                "path": rel,
                "is_file": True,
                "size": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
                "is_metadata": any(
                    rel == d or rel.startswith(d + "/")
                    for d in ("pass0_cache", "pass1_cache", "pass2_cache")
                ),
            }
            if count_entries and name.endswith(".json"):
                try:
                    with open(full, "r", encoding="utf-8") as _f:
                        _d = json.load(_f)
                    if isinstance(_d, list):
                        node["entry_count"] = len(_d)
                except Exception:
                    pass
            nodes.append(node)
        elif os.path.isdir(full):
            nodes.append({
                "name": name,
                "path": rel,
                "is_file": False,
                "size": 0,
                "modified": "",
                "children": _build_cache_tree(full, rel, count_entries),
            })
    return nodes


_REPLACE_FIELD_REJECTED_MSG = "原文/问题字段不支持替换，仅支持译文（dst）或全部（all，仅译文侧字段）"
