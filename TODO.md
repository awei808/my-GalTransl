# TODO

## 后端（GalTransl/server_handlers_*）

- [ ] **part2 路由委托后无条件再补一次 404**（`server_handlers_project.py:1499`）：`route_project_api_part2(...)` 无返回值，调用方紧接着无条件 `_send_json({"error": "not found"}, 404)`。凡由 part2 处理的路径（`/progress`、`/problems`、`/cache/search` 等）都会在正确响应体之后再写一段 404 响应体。当前之所以没暴露：默认协议为 HTTP/1.0（`Content-Length` 决定一次响应边界，客户端读完第一段即返回），且前端/测试都只读第一段。风险：一旦启用 HTTP/1.1 keep-alive、改用严格解析的客户端或代理，多出的响应体会被当成下一个请求的响应（串响应/协议错误）。建议让 `route_project_api_part2` 返回「是否已处理」布尔值，调用方据此决定是否发 404（对齐 `server_cache` 等模块已有的返回值风格）。
- [ ] **`server_handlers_project2.py` import 块存在死导入**（AST 精确核对，仅出现在 import 行）：`unquote`、`_safe_project_dir`、`decode_project_dir`、`encode_project_dir`、`_deep_merge_defaults`、`_get_default_config`、`_REPLACE_FIELD_REJECTED_MSG`、`_build_cache_tree`、`_build_project_output`、`_cache_entry_key`、`_entry_modified`、`_fmt_indices`、`_list_dir_entries`、`_resolve_cache_h_ranges`。无行为影响，属拆分大文件时的残留；清理时顺带核对其它 `server_handlers_*` 拆分文件是否有同类残留（本次未逐个核查）。
- [ ] **`/alt-translations` 未统一缓存文件枚举口径**（`server_handlers_project2.py:836`）：仍用裸 `os.walk` + `name.endswith(".json")`，与 `/problems`、`/progress`、`/runtime` 进度、全量重检已统一的 `_collect_cache_files` 不一致。当前实际影响近零（pass0 元数据/断点分片均为 dict 形状、快照为 `.bak`，`_collect_alt` 对非 list 提前返回），但建议后续一并改用 `_collect_cache_files`，避免再次出现「问题统计有、其它端点无」的漂移。

## 前端（desktop/src/pages/review）

- [ ] **cacheWatcher 自动刷新 effect 为死代码**（`ReviewPage.tsx` 约 909-922 行）：`reviewMode() === "translate"` 与 `modeInfoOf(file).mode !== "translate"` 两道守卫作用在同一 `activeFilePath` 上、语义矛盾，任何情况下都会在 `loadFile` 调用前 return——"外部改动自动刷新译文文件"从未生效（cacheVersion 轮询形同虚设，仅手动「刷新」按钮可用）。需排查是历史语义变更后的残留还是有意禁用，再决定删除或修正守卫。
- [ ] **modeInfoOf 分类表不认识 `gendic_cache/`、`aftertrans_cache/` 等目录**（`ReviewPage.tsx` `modeInfoOf`）：这类目录下的单对象 JSON 文件会被默认判为 translate 模式，若出现在缓存树且可点击，会误入 `loadFile`，以字典形状 entries 触发 `snapshotKey` 的 `list.map` TypeError。可达性取决于 `/files` 的 cache_files 列举范围，待核实后在分类表中补充对应目录或对不可编辑目录做入口屏蔽。
