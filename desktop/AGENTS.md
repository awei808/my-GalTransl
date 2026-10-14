# my-GalTransl 前端手册（desktop/）

本文件是桌面端（Tauri 2 + SolidJS）架构与开发手册，与代码同目录维护。全局硬性规则见仓库根 `AGENTS.md`。后端 HTTP 端点定义**唯一归属** `GalTransl/AGENTS.md`「API 接口文档」，本文件不复制端点表。改动页面/store/API 封装/Tauri 壳时同步更新本文件。

## 目录职责

| 目录 | 职责 |
|---|---|
| `src/pages/` | 页面视图：home（首页）、translate（翻译控制台）、review（校对审核）、settings（设置）、project-config（项目配置）、wizard（新建项目向导 5 步）、dictionary（字典）、logs（日志）、backends（API 配置）、plugins（插件）、prompts（提示词模板）、mcp（MCP 服务与门禁：H 门禁开关 + 工具逐个开关，主设置页仅留入口）、routeAgent（路线图工作台：RouteMapViewer/RunPanel） |
| `src/components/` | 布局组件：TitleBar/ActivityBar/SidebarPanel/MainArea/StatusBar，及 toast/confirm 弹窗宿主、icons |
| `src/lib/api/` | HTTP API 层：`client.ts`（请求封装/后端 URL/token/项目 ID 编解码）、`project.ts`/`general.ts`（各 API 函数）、`types.ts`（TS 类型）、`preferences.ts`（localStorage 偏好） |
| `src/stores/` | SolidJS store 状态管理：`appStore`（全局状态+导航）、`undoStore`（校对撤销/重做）、`confirmStore`/`toastStore`/`logStore` |
| `src/lib/cacheWatcher.ts` | 缓存文件夹轮询监控（3s），驱动文件树/问题侧栏刷新 |
| `src/lib/replaceEntries.ts` | 缓存查找替换的前端字段口径（`dst`/`all`）与替换逻辑 |
| `src/lib/afterTranslation.ts` + `src/components/AfterTranslationOrderEditor.tsx` | 阶段 8 后处理有序数组的类型/编辑逻辑与项目配置页顺序编辑器 |
| `src/lib/usePageAutosave.ts` | 页面卸载自动保存统一骨架 |
| `src/lib/globalSave.ts` | 全局保存注册表：Ctrl+S / 菜单「保存」统一入口（页面注册 `save` 函数，未注册视图提示无可保存内容） |
| `src/lib/customBackground.ts` + `src/styles/custom-background.css` | 自定义背景图设置与样式 |
| `src-tauri/` | Rust 侧：后端生命周期管理 + Tauri command（`ensure_backend_ready` 等） |

## 前端架构

- **框架**：SolidJS（`solid-js`）+ `solid-js/store` 状态管理（非 React）
- **路由**：无 URL 路由，`MainArea` 用 `Switch`/`Match` 基于 `appState.activeView` 切换视图（视图清单与 `MainArea` 的分支一致：`home`、`translate`、`review`、`settings`、`new-project`、`logs`、`dict`、`backend-profiles`、`plugins`、`prompt-templates`、`mcp`、`project-config`、`route-agent`；`@solidjs/router` 虽在依赖中但未使用）
- **API 通信**：`src/lib/api/client.ts` 的 `apiRequest<T>()`（30s 超时、可选 Bearer token、项目 ID base64 编解码）；Tauri 侧 `invoke("ensure_backend_ready")` 管理后端进程生命周期
- **状态管理**：`appStore`（全局：视图/项目/连接/侧边栏/缓存树）、`undoStore`（校对撤销重做）、`confirmStore`/`toastStore`/`logStore`
- **主题与外观**：浅色/深色 × 扁平/鲜艳双风格（`theme.ts` + `styles/tokens.css` 设计令牌）；支持自定义背景图（`customBackground.ts` + `custom-background.css`）
- **自动保存**：页面卸载自动保存统一骨架 `usePageAutosave.ts`（配合显式保存按钮）
- **测试**：Vitest + jsdom + `@solidjs/testing-library`
- **代码质量**：ESLint（`eslint-plugin-solid`，`no-explicit-any: error`）+ Prettier

## 全局快捷键（App.tsx `handleGlobalKeyDown`）

| 快捷键 | 功能 | 作用域 |
|---|---|---|
| Ctrl+F | 当前文件内查找（校对页监听 `galtransl:find-in-file` 事件） | 全局（浮层在校对页呈现） |
| Ctrl+H | 打开查找替换侧边栏 | 全局 |
| Ctrl+B | 切换侧边栏 | 全局 |
| Ctrl+S | 全局保存注册表（`globalSave.ts`）：调用当前视图注册的保存入口（review/dict/project-config/backend-profiles/prompt-templates/wizard currentStep≥3 各步）；未注册视图提示「当前页面没有可保存的内容」 | 全局 |
| Ctrl+Z / Ctrl+Y | 校对审核页撤销 / 重做 | 仅校对审核页 |

## Tauri Rust 命令（IPC `invoke`，非 HTTP）

| 命令 | 参数 | 说明 |
|---|---|---|
| `ensure_backend_ready` | `{hideConsole, timeoutMs}` | 检测 12333 端口，未监听则拉起 Python 后端 |
| `open_folder` | `{path}` | 系统资源管理器打开目录 |
| `reveal_file` | `{path}` | 定位并高亮文件 |

> 注：文件读写类命令（`create_dir`/`write_text_file`/`copy_files`）已移除。项目创建/导入统一走后端 `/api/projects/init` 与 `/import`，前端日志统一走 `/api/log`。

## 关键依赖项

完整清单以 `desktop/package.json` 为准：`solid-js` ^1.9（UI 框架 + store）、`@tauri-apps/api` ^2.10 + `plugin-dialog`/`plugin-shell`（Tauri 2 桌面壳）、`vite` ^5 + `vite-plugin-solid`（构建，dev server 绑定 127.0.0.1:1420）、`mermaid` 11（路线图渲染 `PlotRoutePanel`）、`dompurify`（富文本消毒）；测试链 `vitest` + `jsdom` + `@solidjs/testing-library`；质量链 `eslint`（`eslint-plugin-solid`）+ `prettier` + `typescript`。

## 开发环境配置和运行指南（前端）

### 环境要求

- **Node.js 18+**（前端构建，Tauri 需要）
- **Rust 工具链**（仅构建桌面壳时需要；前端纯浏览器开发模式不需要）

### 常用命令

```bash
# 安装依赖
cd desktop && npm install

# 完整桌面开发（自动拉起后端）
npm run tauri:dev
# 或 Windows 一键：run_desktop_dev.bat

# 仅前端开发（需后端已在 12333 端口运行）
npm run dev          # dev server: 127.0.0.1:1420

# 前端测试 / 代码检查
npm run test           # vitest
npm run test:watch
npm run typecheck      # tsc --noEmit（npm run build 时自动先执行）
npm run lint / lint:fix
npm run format         # Prettier
```

### 测试（前端）

- 框架 `vitest` + `jsdom` + `@solidjs/testing-library`：`cd desktop && npm run test`（也可用仓库根 `python run_vitest.py` 绕过 PowerShell 编码问题）
- 测试文件在 `desktop/src/__tests__/`
