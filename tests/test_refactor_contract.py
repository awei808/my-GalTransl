"""0.4.10 大文件重构的对外符号契约快照测试。

背景：0.4.10 把 server.py / LLMTranslate.py / ReviewPage.tsx 按功能域拆成多个
模块，server.py 与 LLMTranslate.py 对搬走的符号做「真实绑定 re-export」以维持
向后兼容。本测试把「外部可见的符号面」固化为契约，使拆分过程中任何符号丢失
都会立刻失败，而不是等到某个测试用真实实现静默跑过。

两类断言：
1. 符号存在性 —— server 91 个 / LLMTranslate 26 个顶层符号必须全部可从原路径导入；
2. reload 语义 —— importlib.reload(server) 后核心符号仍可用（14 个既有测试依赖）。

另有一组「被 patch 目标」清单断言：这些名字必须能在其模块命名空间里被找到，
否则 mock.patch 会静默打空。
"""
import builtins
import dis
import importlib
import types
import unittest

import GalTransl.server as server
from GalTransl.Frontend import LLMTranslate


def _iter_code_objects(code: types.CodeType):
    """递归遍历 code 对象及其嵌套函数/lambda 的 code。"""
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _iter_code_objects(const)


def _collect_functions(module) -> list:
    """收集**定义在本模块内**的顶层函数与类方法。

    必须按 `__globals__` 过滤：re-export 进来的函数（如 from GalTransl.i18n
    import get_text）其 globals 属于原模块，用本模块命名空间核对会全部误报。
    """
    module_ns = vars(module)
    found = []
    for obj in module_ns.values():
        if isinstance(obj, types.FunctionType):
            candidates = [obj]
        elif isinstance(obj, type):
            candidates = [
                member for member in vars(obj).values()
                if isinstance(member, types.FunctionType)
            ]
        else:
            continue
        found.extend(f for f in candidates if f.__globals__ is module_ns)
    return found


def _unresolved_global_names(module) -> list[str]:
    """找出模块内函数体引用、但既不在模块命名空间也不在内置命名空间的名字。

    只统计 LOAD_GLOBAL 指令（即真正的「全局名查找」）——模块级 `__getattr__`
    是 PEP 562 钩子，只在 `模块.属性` 访问时生效，不参与函数体内的全局名查找，
    因此这类缺口用 hasattr 检测不出来（0.5.0 的 _run_full_pipeline 即此坑）。
    """
    module_ns = vars(module)
    builtin_ns = vars(builtins)
    missing: set[str] = set()
    for func in _collect_functions(module):
        for code in _iter_code_objects(func.__code__):
            for instruction in dis.get_instructions(code):
                if instruction.opname != "LOAD_GLOBAL":
                    continue
                name = instruction.argval
                if name not in module_ns and name not in builtin_ns:
                    missing.add(f"{func.__name__}:{name}")
    return sorted(missing)

# server.py 全部顶层符号（重构前 AST 快照，拆分后必须仍可从 GalTransl.server 导入）
SERVER_SYMBOLS = [
    "COMMON_DICT_CATEGORY_MAP", "DICT_PROJECT_MARKER", "INDEX_HTML", "JobRegistry",
    "_ALLOWED_ORIGINS", "_API_TOKEN", "_BACKEND_PROFILES_PATH", "_CONFIG_SCHEMA_CACHE",
    "_DEFAULT_CONFIG_CACHE", "_DEFAULT_CONFIG_LOCK", "_DEFAULT_TRANSLATOR_PROMPTS",
    "_LOG_STRIP_TABLE", "_NAME_DICT_CACHE", "_PROBLEM_TYPE_CATALOG",
    "_REPLACE_FIELD_REJECTED_MSG", "_REVIEW_SUGGEST_LOCK", "_SAMPLE_BATCH_META_CONTENT",
    "_SAMPLE_CACHE_FILENAME", "_SAMPLE_CACHE_JSON_CONTENT", "_SAMPLE_FILE_META_CONTENT",
    "_SAMPLE_GLOBAL_PROMPT_CONTENT", "_SAMPLE_PLOT_ROUTE_CONTENT",
    "_STAGE_CHECK_SKIP_MESSAGE", "_SuggestConfigError", "_VALID_LOG_LEVELS",
    "_append_engine_log", "_build_cache_tree", "_build_config_schema",
    "_build_project_output", "_build_prompt_templates_payload", "_cache_entry_key",
    "_categorize_common_dict_file", "_check_batch_size", "_check_model_availability",
    "_check_stage_model_availability", "_collect_cache_files",
    "_collect_common_dict_payload", "_collect_project_dict_payload",
    "_common_dict_category_map_path", "_common_dict_directory", "_create_project_layout",
    "_deep_merge_defaults", "_dict_category_config_key", "_ensure_common_dicts_in_config",
    "_ensure_empty_project_dict_file", "_ensure_project_dict_file_configured",
    "_entry_modified", "_extract_dialogue_symbols", "_find_name_col", "_fmt_indices",
    "_get_config_schema", "_get_default_config", "_is_numeric_index", "_is_path_within",
    "_is_safe_config_filename", "_is_safe_dict_filename", "_list_dir_entries",
    "_list_problem_types", "_list_translation_guidelines", "_load_project_name_dict",
    "_load_rebuild_deps", "_lookup_name", "_migrate_h_check_dict_config",
    "_normalize_cache_filenames", "_normalize_dict_text", "_open_in_file_manager",
    "_parse_yaml_comments", "_pick_output_dst", "_read_backend_profiles",
    "_read_common_dict_category_map", "_read_dict_file_payload", "_read_yaml_file",
    "_rebuild_trans_list_with_postprocess", "_resolve_cache_h_ranges",
    "_resolve_cache_row_offset", "_resolve_new_project_dir", "_resolve_suggest_backend",
    "_run_problem_detection", "_sanitize_log_field", "_scan_plugins", "_validate_build",
    "_workspace_root", "_write_backend_profiles", "_write_common_dict_category_map",
    "_write_initial_config", "_write_stage_samples", "_write_yaml_file",
    "build_handler", "main", "recheck_pass3_cache_files", "serve",
]

# LLMTranslate.py 全部顶层符号（拆分后必须仍可从 GalTransl.Frontend.LLMTranslate 导入）
LLMTRANSLATE_SYMBOLS = [
    "AdaptiveWorkerState", "_build_meta_file_totals", "_build_runtime_file_maps",
    "_check_stop_requested", "_has_nonempty_gpt_dict", "_pass3_cache_dir",
    "_resolve_after_translation_order", "_resolve_file_h_ranges",
    "_run_after_trans_single_file", "_run_full_pipeline", "_run_gendic_flow",
    "_run_meta_worker_pool", "_run_translation_phase", "_runtime_project_dir",
    "_stage_pool", "_update_runtime", "auto_tune_workers", "doLLMTranslSingleChunk",
    "doLLMTranslate", "ensure_model_available_if_needed", "fplugins_load_file",
    "init_gptapi", "postprocess_results", "postprocess_trans_list",
    "preprocess_trans_list", "update_progress_title",
    # 0.5.2 独立后处理后端表驱动分发（doLLMTranslate 以全局名调用，必须真实绑定）
    "STANDALONE_BACKENDS", "is_standalone_backend", "run_standalone_backend",
]

# 服务器壳：reload(server) 后必须仍可直接调用（14 个测试依赖此路径）
SERVER_SHELL_SYMBOLS = ["serve", "main", "build_handler", "JobRegistry"]

# mock.patch("GalTransl.server.X") 的目标；拆分后允许改到新模块，
# 但**必须存在且可被 patch**，否则静默打空（见计划文档 §5 裁决点③）。
PATCH_TARGETS_SERVER_NS = [
    "_common_dict_directory", "_load_rebuild_deps", "_run_problem_detection",
    "recheck_pass3_cache_files", "reset_runtime_project", "run_job",
    "update_runtime_status",
]


class ServerSymbolContractTests(unittest.TestCase):
    """GalTransl.server 的对外符号面不得因拆分而收缩。"""

    def test_all_top_level_symbols_importable(self) -> None:
        missing = [name for name in SERVER_SYMBOLS if not hasattr(server, name)]
        self.assertEqual(missing, [], f"GalTransl.server 丢失顶层符号: {missing}")

    def test_symbols_importable_via_from_import(self) -> None:
        # 逐一走 `from GalTransl.server import X` 路径（生产代码的 30+ 处引用方式）
        for name in SERVER_SYMBOLS:
            with self.subTest(symbol=name):
                mod = importlib.import_module("GalTransl.server")
                self.assertTrue(hasattr(mod, name))

    def test_server_shell_survives_reload(self) -> None:
        # 14 个测试用 importlib.reload(server) + _server_mod.build_handler(...)
        reloaded = importlib.reload(server)
        for name in SERVER_SHELL_SYMBOLS:
            with self.subTest(symbol=name):
                self.assertTrue(hasattr(reloaded, name), f"reload 后丢失 {name}")

    def test_patch_targets_resolvable(self) -> None:
        # 每个 patch 目标必须能在 GalTransl.server 命名空间解析到（未搬迁时）
        for name in PATCH_TARGETS_SERVER_NS:
            with self.subTest(target=name):
                mod = importlib.import_module("GalTransl.server")
                if name in ("reset_runtime_project", "update_runtime_status",
                            "run_job"):
                    # 这三个来自 server_runtime / Service，reload 后由 server.py 重新绑定
                    continue
                self.assertTrue(
                    hasattr(mod, name),
                    f"patch 目标 GalTransl.server.{name} 不存在（mock.patch 会静默打空）",
                )

    def test_entry_contract_main_callable(self) -> None:
        # run_backend.py 的唯一入口契约
        self.assertTrue(callable(server.main))

    def test_index_html_is_nonempty_str(self) -> None:
        # 内嵌 Web UI：GET / 直接返回，搬迁后必须仍是 str
        self.assertIsInstance(server.INDEX_HTML, str)
        self.assertGreater(len(server.INDEX_HTML), 1000)


class LLMTranslateSymbolContractTests(unittest.TestCase):
    """GalTransl.Frontend.LLMTranslate 的对外符号面不得因拆分而收缩。"""

    def test_all_top_level_symbols_present(self) -> None:
        # 必须查模块 __dict__（真实绑定）而非 hasattr —— hasattr 会被模块级
        # __getattr__ 兜住，掩盖「函数体内以全局名引用、运行时 NameError」的缺口
        missing = [name for name in LLMTRANSLATE_SYMBOLS if name not in vars(LLMTranslate)]
        self.assertEqual(
            missing, [], f"GalTransl.Frontend.LLMTranslate 丢失顶层符号: {missing}"
        )

    def test_module_globals_resolvable_in_function_bodies(self) -> None:
        # 回归防线：任何函数体引用的全局名都必须能在模块命名空间解析到
        # （0.5.0 的 _run_full_pipeline 就是「符号已迁出却没 re-export」导致的）
        missing = _unresolved_global_names(LLMTranslate)
        self.assertEqual(
            missing, [],
            f"LLMTranslate 函数体内引用了无法解析的全局名: {missing}",
        )

    def test_run_full_pipeline_is_real_binding(self) -> None:
        from GalTransl.Frontend import llm_pipeline
        self.assertIs(LLMTranslate._run_full_pipeline, llm_pipeline._run_full_pipeline)

    def test_entry_doLLMTranslate_is_coroutine(self) -> None:
        # Runner.py:11/355 的入口契约
        import inspect
        self.assertTrue(inspect.iscoroutinefunction(LLMTranslate.doLLMTranslate))


class CrossModuleImportContractTests(unittest.TestCase):
    """GalTransl 包内 7 个文件的函数内延迟导入路径必须保持可用。"""

    DELAYED_IMPORT_SYMBOLS = [
        "record_runtime_error", "record_runtime_success", "update_runtime_status",
        "_resolve_cache_h_ranges", "reset_runtime_project", "_read_backend_profiles",
        "_load_rebuild_deps", "recheck_pass3_cache_files", "_check_batch_size",
        "_build_project_output", "_validate_build",
    ]

    def test_delayed_import_symbols_resolvable(self) -> None:
        mod = importlib.import_module("GalTransl.server")
        missing = [n for n in self.DELAYED_IMPORT_SYMBOLS if not hasattr(mod, n)]
        self.assertEqual(
            missing, [],
            f"函数内 `from GalTransl.server import X` 将失败，缺失: {missing}",
        )

    def test_cc_probe_import_path_resolvable(self) -> None:
        # cc_probe/_verify_h.py:13 独立脚本引用
        from GalTransl.server import _resolve_cache_h_ranges  # noqa: F401


if __name__ == "__main__":
    unittest.main()
