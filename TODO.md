# TODO

## 前端（desktop/src/pages/review）

- [ ] **cacheWatcher 自动刷新 effect 为死代码**（`ReviewPage.tsx` 约 909-922 行）：`reviewMode() === "translate"` 与 `modeInfoOf(file).mode !== "translate"` 两道守卫作用在同一 `activeFilePath` 上、语义矛盾，任何情况下都会在 `loadFile` 调用前 return——"外部改动自动刷新译文文件"从未生效（cacheVersion 轮询形同虚设，仅手动「刷新」按钮可用）。需排查是历史语义变更后的残留还是有意禁用，再决定删除或修正守卫。
- [ ] **modeInfoOf 分类表不认识 `gendic_cache/`、`aftertrans_cache/` 等目录**（`ReviewPage.tsx` `modeInfoOf`）：这类目录下的单对象 JSON 文件会被默认判为 translate 模式，若出现在缓存树且可点击，会误入 `loadFile`，以字典形状 entries 触发 `snapshotKey` 的 `list.map` TypeError。可达性取决于 `/files` 的 cache_files 列举范围，待核实后在分类表中补充对应目录或对不可编辑目录做入口屏蔽。
