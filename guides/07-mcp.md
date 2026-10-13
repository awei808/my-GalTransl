# MCP 与外置 Agent

GalTransl 程序本体不内置 Agent，Agent 能力通过 **MCP**（Model Context Protocol）提供给外置程序使用（官方外置 Agent 为 dsh）。

## MCP 能做什么

MCP 服务提供检索与受限写入两类工具：检索缓存译文、原文、字典与项目字典、元数据、任务状态等；写入限于剧情路线图、元数据、项目字典词条（`category` = gpt/pre/post，按词条增改 / 删除，不做整体覆盖）与缓存条目（逐条标记/取消跳过问题检查、交换备选译文），另有提交 / 停止翻译任务。写工具落盘前会校验目标确实是合法项目目录。完整工具清单见 MCP 服务下发的使用说明与程序目录 `skills/galtransl-mcp/SKILL.md`（接入 agent 的完整契约文档，随发布包分发）。

## H 门禁

「MCP H 门禁」开关控制**内容写入类写工具**（`write_route_map` 路线图 / `save_metadata` 元数据 / `write_glossary` 项目字典）对 H（成人）内容的硬拦截：**开启时**，外部 Agent 无法经 MCP 把这些写入的文本落在 H 相关文件与区间（命中即拒绝并报错）；关闭后放行。MCP 服务说明中的门禁条款随开关联动。

另有两个缓存条目工具**不受门禁约束**：`mark_skip_check`（标记/取消跳过问题检查）只改标志位、`swap_alt_translation`（交换备选译文）只在两条已有译文之间对调，都不写入新文本——**H 区间的条目同样可以标记或交换备选**（交换既有 H 译文属允许操作）。作业域的 `submit_job` / `stop_job` 不写内容，也不涉及门禁。故门禁只覆盖 3 个工具，不能当作「任何 MCP 写入都有 H 兜底」；读取侧的 H 纪律由接入的 Agent 自行遵守（见 `skills/galtransl-mcp/SKILL.md` §1）。

## 工具开关

下方列表可逐个禁用 MCP 工具（黑名单）。禁用实时生效、无需重启后端；被禁工具在 Agent 会话中直接不可见 / 调用被拒。注意：服务说明与工具计数是 MCP 进程启动时的快照，改动后重开 Agent 会话即可刷新。

## 接入外置 Agent（dsh）

安装 dsh 后，把 galtransl 预设挂载到 dsh 的 home 层即可（用户只需新建 1 个文件，不影响其他配置）。详细步骤见仓库文档 `docx/MCP接入指南.md` 与 `agents/dsh-preset/README.md`。
