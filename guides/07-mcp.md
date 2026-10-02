# MCP 与外置 Agent

GalTransl 程序本体不内置 Agent，Agent 能力通过 **MCP**（Model Context Protocol）提供给外置程序使用（官方外置 Agent 为 dsh）。

## MCP 能做什么

MCP 服务提供检索与受限写入两类工具：检索缓存译文、原文、字典、元数据、任务状态等；写入限于剧情路线图与元数据，另有提交 / 停止翻译任务。写工具落盘前会校验目标确实是合法项目目录。

## H 门禁

「MCP H 门禁」开关控制写工具对 H（成人）内容的硬拦截：**开启时**，外部 Agent 无法经 MCP 写入 H 相关文件与区间；关闭后放行。MCP 服务说明中的门禁条款随开关联动。

## 工具开关

下方列表可逐个禁用 MCP 工具（黑名单）。禁用实时生效、无需重启后端；被禁工具在 Agent 会话中直接不可见 / 调用被拒。注意：服务说明与工具计数是 MCP 进程启动时的快照，改动后重开 Agent 会话即可刷新。

## 接入外置 Agent（dsh）

安装 dsh 后，把 galtransl 预设挂载到 dsh 的 home 层即可（用户只需新建 1 个文件，不影响其他配置）。详细步骤见仓库文档 `docx/MCP接入指南.md` 与 `agents/dsh-preset/README.md`。
