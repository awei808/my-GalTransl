
<div align=center><img width="150" height="150" src="./img/logo.png"/></div>

<h1><p align='center' >GalTransl</p></h1>
<p align='center' >支持Deepseek等大语言模型的Galgame自动化翻译解决方案</p>

  [English](./README_EN.md)

  GalTransl是一套将数个基础功能上的微小创新与对GPT提示工程（Prompt Engineering）的深度利用相结合的Galgame自动化翻译工具，用于制作内嵌式翻译补丁。重构后的版本提供**桌面端图形界面**，无需命令行操作即可完成翻译全流程。

## 前言
&ensp;&ensp;&ensp;&ensp;GalTransl的核心是一组自动化翻译脚本与配套的桌面工作台，解决了使用大模型自动化翻译Gal过程中已知的大部分问题，并提高了整体的翻译质量。同时，通过与其他项目的组合，打通了制作补丁的完整流程，一定程度降低了上手门槛。对此感兴趣的朋友可以通过本项目更容易的构建具有一定质量的机翻补丁，并(或许)可以尝试在此框架的基础上高效的构建更高质量的汉化补丁。  

* **原作者声明**：GalTransl 的原作者是 [xd2333](https://github.com/XD2333/GalTransl)，本项目（my-GalTransl）是基于原项目**个人重构并扩展功能**的派生版本，非原作者官方维护。原项目的核心设计、提示工程与算法思路均归原作者所有，特此致谢。本项目的任何问题与缺陷不代表原作者。

* **关于本项目**：my-GalTransl 是个人使用 AI（vibecoding）迭代开发的版本。**代码质量较差，未来会优化**——当前代码中仍存在一些层级混乱、尚未重构的重复代码，未来在功能完善后会逐步整理与重构。当前阶段以功能可用与快速迭代为先。

## 安全风险须知
&ensp;&ensp;&ensp;&ensp;本项目为个人重构版本，沿用原项目部分实现，存在以下已知安全风险，使用前请知悉：

- **文件读取和写入默认不鉴权**：接口默认可对任意路径进行读取、读写文件。原因：原项目即存在此问题。缓解：后端仅绑定 `127.0.0.1` 并配合 CORS 白名单，远程攻击面较小，请勿在不可信网络中暴露本机服务。
- **API 密钥明文显示**：项目配置中的 API 密钥以明文形式存储与展示。原因：原项目即存在此问题。影响：最多造成密钥泄漏，且仅在本机被恶意入侵或主动分享项目配置（含密钥）时才会发生泄漏，请勿将含密钥的配置分享给他人或提交至公开仓库。

## 与原项目的不同处速览
&ensp;&ensp;&ensp;&ensp;本项目的重构目的是**探索如何实现更高质量的 AI 汉化**，必定带来更高的token开销。在原项目基础上做了以下主要调整：

1. **前端界面完全重构**：采用 Tauri 2 + SolidJS 重写桌面端，界面风格与原项目存在巨大差异，重新划分界面。
2. **仅适配 Galgame 汉化**：重点面向 galgame 文本翻译场景；epub、字幕等原项目的通用文件功能**可能无法正常使用**。
3. **更多流水线与更丰富的提示词**：在翻译流程中加入更多阶段，并在提示词中注入更多信息与规范（如全局分析、文件剧情元数据、剧情路线图，**所有阶段均可跳过和人工编辑缓存文件**），以追求更好的翻译质量。
4. **翻译后端采用多轮对话形式**：不再沿用原项目的单轮对话形式，采用多轮对话形式以获得更全面的上下文，打造更好的译文。
5. **翻译后 AI 改善译文**：翻译流程完成后，支持调用 AI 对译文进行改善（ForImproveTranslation）。
6. **新增问题检测项并可跳过检查**：在原问题检测基础上新增 **长句丢失换行** 与 **换行符位置异常** 两项，并且允许为译文打上跳过检测标记，不再被记录问题。
7. **备选译文功能**：让 AI 评价并输出可改善的译文，或针对特定问题项让 AI 提供修复后的译文作为备选；用户可在校对审核页自由切换备选译文与当前译文。
8. **新建项目向导添加更多设置**：可在新建项目向导中，设置游戏外部信息、剧情大纲，设置翻译流水线各阶段是否执行等。
8. **缓存结构变化**：为适配多流程翻译（全局分析 → 文件级元数据 → 翻译执行等多阶段），翻译缓存结构做了必要调整，与原项目不兼容。
9. **翻译项目路径**：新建的翻译项目只能位于应用程序同目录下，旧项目不受此限制。
10. **已知待修复问题**：项目中的**全局撤销/重做机制、查找替换功能、翻译控制台部分板块显示存在明显 bug，暂未修复**。
11. **聚焦大模型、放弃本地小模型**：完全针对云端大模型（GPT-4/Claude/Deepseek 等 OpenAI 兼容接口）做适配与优化；本地小模型仅保留配置接口，**可能无法正常使用**。
12. **命令行未适配**：重构主要围绕桌面端展开，命令行版本（CLI）**尚未做适配，可能无法正常使用**。
13. **其他细小变化**：JSON 内统一使用 `\n` 换行符（替代 `\r\n`），字典统一使用 PSV 格式等。

  * 特性：   
  1. 🖥️ **桌面端图形界面**——基于 Tauri 2 + Rust + **SolidJS** 构建的现代桌面应用，无需命令行操作即可完成新建项目、导入文件、配置后端、翻译、校对审核、构建输出全流程
  2. 支持**GPT-4/Claude/Deepseek**等大语言模型，后端配置统一管理多套 OpenAI 兼容接口（本地小模型接口已保留但可能无法正常使用）
  3. **GPT字典**，让模型了解人设，准确翻译人名、人称代词与生词
  4. 通过译前（预处理）、译后（后处理）字典与条件字典实现灵活的自动化字典系统，并支持**人名替换表**（CSV/XLSX）
  5. 实时保存缓存、自动断点续翻，翻译过程中可视化展示提示词、译文拼接、并发进度与速度
  6. 完整流水线：输入校验 → 文本压缩 → 全局游戏分析 → 自动构建术语表 → 文件级元数据 → 批次划分 → 翻译执行，多阶段分批提示词注入提升翻译质量
  7. 内置**校对审核**工作台：逐句检查修改、问题检测、备选译文交换、撤销/重做、跨文件查找替换
  8. 重点适配 galgame 文本翻译场景，其他通用文件格式（srt、epub 等）可能无法正常使用
  9. 支持**插件系统**：自定义文件格式与文本处理流水线，扩展性强

<b>❗❗使用本工具翻译并在未做全文校对/润色的前提下发布时，请在最显眼的位置标注"GPT翻译/AI翻译补丁"，而不是"个人汉化"或"AI汉化"补丁。</b>

## 近期更新
* 2026.8：个人重构 v0.1.0，使用**桌面端图形界面**（Tauri 2 + SolidJS + Rust），支持可视化翻译工作台、校对审核、后端配置统一管理、人名替换表、完整流水线等
* 2026.4: 更新v7，新增**桌面端图形界面**（Tauri + React），支持深色模式、自定义背景、多项目管理、可视化翻译工作台等
* 2025.5: 更新v6，新增翻译模板ForGal、新增GalTransl-14B-v3模型
* 2024.5：更新v5，新增GalTransl-7B模型，新增多种文件类型支持   
* 2024.2：更新 v4 版，主要支持了插件系统  
* 2023.12：更新 v3 版，支持基于文件的多线程
* 2023.7：更新 v2 版，主要重构了代码
* 2023.6：v1 初版发布

## 导航
* [环境准备](#环境准备)：环境与软件的安装   
* [上手教程](#上手教程)：全流程介绍如何制作一个机翻补丁，**只想看怎么使用本工具的话，可以直接跳转第2章的2.2节**   
* [配置文件与翻译引擎设置](#配置文件与翻译引擎设置)：介绍翻译后端（OpenAI 兼容接口/SakuraLLM）的配置方式。   
* [GalTransl核心功能介绍](#galtransl核心功能介绍)：介绍GPT字典、缓存、普通字典、找问题等功能。

## 环境准备
  * **桌面版（推荐）**   
  从 [Release](https://github.com/awei808/my-GalTransl/releases) 下载最新版压缩包，解压后双击 `GalTransl Desktop.exe` 即可使用，**无需安装Python或任何依赖**。桌面端会自动启动后端服务。
   
  * **命令行版（开发者/高级用户）**   
  如需使用命令行版本或参与开发：

  1. 下载本项目或 clone 仓库，解压到任意位置
  2. 安装 Python 3.11+。（推荐3.12） [下载](https://www.python.org/downloads/)   
  **安装时勾选下方 add Python to path**
  3. 安装Python依赖：执行 `pip install -r requirements.txt`
  4. （桌面端开发）安装 Node.js 18+，在 `desktop` 目录执行 `npm install`，然后运行 `run_desktop_dev.bat`

## 实用工具
| 名称 | 说明 |
| --- | --- |
| GARbro | 引擎工具：神一样的解包工具。[下载](https://github.com/morkt/GARbro/releases/download/v1.5.44/GARbro-v1.5.44.2904.rar) |
| [KirikiriTools](https://github.com/arcusmaximus/KirikiriTools) | 引擎工具：Krkr、krkrz 提取、注入工具 |
| [UniversalInjectorFramework](https://github.com/AtomCrafty/UniversalInjectorFramework) | 引擎工具：sjis隧道、sjis替换模式通用注入框架 |
| [VNTextProxy](https://github.com/arcusmaximus/VNTranslationTools) | 引擎工具：sjis隧道模式通用注入框架 |
| GalTransl_DumpInjector | 脚本工具：[VNTextPatch](https://github.com/arcusmaximus/VNTranslationTools)的图形化界面，综合脚本文本提取导入工具 |
| [SExtractor](https://github.com/satan53x/SExtractor) | 脚本工具：综合脚本文本提取导入工具 |
| [msg-tool](https://github.com/lifegpc/msg-tool) | 脚本工具：综合脚本文本提取导入工具 |
| [DBTXT2Json_jp](https://github.com/XD2333/DBTXT2Json_jp) | 脚本工具：双行文本与json_jp互转脚本 |
| [EmEditor](https://www.ghxi.com/emeditor.html) | 文本工具：神一样的文本编辑器，主语用于修缓存文件。  |
| [VSCode](https://code.visualstudio.com/) | 文本工具：神一样的文本编辑器，主语用于修缓存文件。  |
| [KeywordGacha](https://github.com/neavo/KeywordGacha) | 文本工具：使用 OpenAI 兼容接口自动生词语表 |

## 上手教程
做一个gal内嵌翻译补丁的大致流程是：   
1. 识别引擎 -> 解包资源包拿到脚本 -> 接2.   
2. 解包脚本为日文文本 -> 翻译为中文文本 -> 构建中文脚本 -> 接3.   
3. 封包为资源包/免封包 -> 接4.
4. 引擎支持unicode的话，直接玩 -> 引擎是shift jis的，尝试2种路线使其支持显示中文   

原作者会分成以上4个模块分步讲解，这个段落为了让没做过的朋友也能有机会上手，会写的更照顾小白一些。   

* 建议先只跑开头一个文件的翻译，或先随便添加一些中文，导回游戏确认可以正常显示再全部翻译   
   
（点击展开详细说明）   
<details>

<summary>

### 第一章 识别与解包   

</summary>
识别引擎其实很简单，通常来说，使用GARbro打开游戏目录内的任意资源包，在左下方的状态栏中就会显示引擎名称： 

或者，参考[资源包后缀表](https://morkt.github.io/GARbro/supported.html)，比较资源包的后缀。   

剧情脚本一般在一些有明显关键字的资源包，或在资源包中明显关键字的目录内，例如：scene、scenario、message、script等字样。并且脚本通常是由许多明显分章节、分人物，有的还分出了剧情和hs(例如带_h)，通常多翻找几个资源包就能找到。   

或者，参考[Dir-A佬的教程](https://space.bilibili.com/8144708/)   

特别的，针对新的krkrz引擎，GARbro已经无法打开资源包，可以用[KrkrzExtract项目](https://github.com/xmoezzz/KrkrzExtract/releases/tag/1.0.0.0)，将游戏拖到exe上启动。然后下一个全cg存档，直接把所有剧情ctrl一遍，也可以获取到脚本文件。   

</details>
<details>

<summary>

### 第二章 提取与翻译   

</summary>

* **【2.1. 提取脚本文本】**   
&ensp;&ensp;&ensp;&ensp;通常情况下，本项目是结合[VNTextPatch工具](https://github.com/arcusmaximus/VNTranslationTools)来解包脚本的。 VNTextPatch是由外国大佬arcusmaximus开发的[支持许多引擎](https://github.com/arcusmaximus/VNTranslationTools#vntextpatch)脚本的提取与注入的通用工具。（但并不是这些引擎都能搞定了，实测有的游戏是会提取失败的）   
   
&ensp;&ensp;&ensp;&ensp;VNTextPatch是使用cmd操作的，为了降低上手难度，原作者搓了一个图形化的界面，你可以在项目的useful_tools/GalTransl_DumpInjector内找到，点击GalTransl_DumpInjector.exe运行。   
&ensp;&ensp;&ensp;&ensp;现在，你只需要选择日文脚本目录，然后选择保存提取的日文json的目录，这里一般将日文脚本放到叫script_jp的文件夹，再新建一个gt_input目录，用于存储提取出的脚本：   
![图1](./img/img_dumper.png)
&ensp;&ensp;&ensp;&ensp;需要注意GalTransl全程是使用name-message格式的JSON输入、处理和输出的。[JSON是什么](http://c.biancheng.net/json/what-is-json.html)   
提取出来的json文件可以用emeditor打开，一般是这个样子的：   
```json
[
  {
    "name": "咲來",
    "message": "「ってか、白鷺学園だったらあたしと一緒じゃん。\r\nセンパイだったんですねー」"
  }
]
```
&ensp;&ensp;&ensp;&ensp;其中，每个{object(对象)}是一句话，`message`是消息内容，如果object还带了`name`，说明是对话。不过可能并不是所有类型的脚本都可以带name提取，**当可以正确提取name时，GalTransl的翻译质量会更好**。   
&ensp;&ensp;&ensp;&ensp;PS. GalTransl只支持指定格式的json文件输入，但并不是说GalTransl就与VNTextPatch工具绑定了，也可以使用SExtractor工具，现在也支持导出GalTransl需要的name-message格式JSON   

* **【2.2. 使用桌面端翻译（推荐）】**
&ensp;&ensp;&ensp;&ensp;从 [Release](https://github.com/awei808/my-GalTransl/releases) 下载最新版，解压后双击 `GalTransl Desktop.exe` 启动桌面端。桌面端会自动启动后端服务，无需手动操作。

&ensp;&ensp;&ensp;&ensp;启动后通过左侧活动栏切换功能视图：**翻译控制台 / 校对审核 / 查找替换 / 问题检测 / 查看备选 / 字典管理 / 构建输出 / 设置**。

&ensp;&ensp;&ensp;&ensp;**① 新建项目（5步向导）**：在首页点击"新建项目"，按向导依次完成：
1. **项目位置**：输入项目名称，自动创建于后端工作区根目录（含 `gt_input` / `gt_output` / `transl_cache` 与 `config.yaml`）
2. **导入文件**：将待翻译的 JSON 文件拖放到窗口或点击"选择文件"导入到 `gt_input`
3. **翻译后端**：选择全局"后端配置"中的某个方案，或跳过使用项目自身配置
4. **常用设置**：文件插件、文本插件、并发文件数、单次翻译句数、目标语言、翻译规范
5. **提取人名**：完成并打开项目时自动从源文件提取人名表

&ensp;&ensp;&ensp;&ensp;**② 配置翻译后端**：在"后端配置"页面预先配置多套方案（`OpenAI-Compatible` 类型支持多 token、endpoint、modelName，`SakuraLLM` 类型支持多端点），并可将某套设为"默认"。翻译时直接选用，无需手动编辑 YAML。

&ensp;&ensp;&ensp;&ensp;**③ 设置字典**：在"字典管理"页面配置四类字典：**预处理（译前）**、**GPT 字典**、**后处理（译后）**、**人名替换**（建议至少配置人名字典与 GPT 字典）。

&ensp;&ensp;&ensp;&ensp;**④ 开始翻译**：在"翻译控制台"选择后端并点击"启动流程"，实时查看翻译进度、速度、当前提示词与译文拼接结果，可随时停止。

<details>
<summary>④-详解：翻译流水线各阶段与文件流向</summary>

&nbsp;&nbsp;&nbsp;&nbsp;"启动流程"默认走 `ForGal-full-pipeline`，按固定顺序执行：**文本压缩 → 全局剧情/角色分析 → 文件级剧情元数据 → 批次划分 → 多轮对话翻译 →（可选）译后改进**。各阶段中间产物写入 `transl_cache` 下 `pass0~pass3` 三个文件夹。切换"启动流程"上方的后端选择框，可单独运行某一阶段或改用其它后端。

&nbsp;&nbsp;&nbsp;&nbsp;**通用规则：缓存命中跳过（所有阶段通用）**
&nbsp;&nbsp;&nbsp;&nbsp;每一阶段在调用模型前先查自身缓存，**缓存存在则跳过该阶段、直接复用旧产物**，支持断点续翻。

&nbsp;&nbsp;&nbsp;&nbsp;**阶段一 · 文本压缩（TextCompressor）**
&nbsp;&nbsp;&nbsp;&nbsp;对全文做 JSON 结构无损压缩以降低 token 消耗，属流水线内部步骤，不产生独立落盘文件，压缩结果仅驻留内存供全局分析使用。

&nbsp;&nbsp;&nbsp;&nbsp;**阶段二 · 全局剧情/角色分析（ForGlobalPrompt）**
&nbsp;&nbsp;&nbsp;&nbsp;读取压缩后的游戏全文与外部信息（名称、制作公司等），生成全局剧情概要、角色档案与行文风格，写入 `transl_cache/pass0_cache/GlobalPrompt.json`（存在即跳过）。作为全局上下文注入后续翻译提示词，保证全作风格与角色口吻统一，并为下游的翻译补充必要信息。

&nbsp;&nbsp;&nbsp;&nbsp;**阶段三 · 文件级剧情元数据（ForFileMetaData）**
&nbsp;&nbsp;&nbsp;&nbsp;逐文件分析剧本，生成该文件剧情梗概与出场角色等元数据（不翻译、无多轮），写入缓存 `transl_cache/pass1_cache/{文件名}.meta.json`（存在即跳过该文件）。

&nbsp;&nbsp;&nbsp;&nbsp;**阶段四 · 批次划分（ForBatchMetaData）**
&nbsp;&nbsp;&nbsp;&nbsp;依据文件级元数据，将每个剧本切成若干**连续区间（批次）**，标注视角/氛围/H 场景/用词色彩，写入缓存 `transl_cache/pass2_cache/{文件名}.batch.json`（存在即跳过）。批次是多轮对话的上下文边界：同批内模型可见前文，跨批则上下文重置。

&nbsp;&nbsp;&nbsp;&nbsp;**阶段五 · 多轮对话翻译（ForGal-json-multi-chat）**
&nbsp;&nbsp;&nbsp;&nbsp;按批次以多轮对话逐句翻译，保留批次内上下文，并注入 GPT 字典与预处理（译前）字典。返回结果写入 `transl_cache/pass3_cache/{文件名}.append.jsonl`（逐句追加、已翻译句命中缓存则跳过），每句含 `pre_jp`(原文)、`post_jp`(清洗后日文)、`pre_zh`(初译)、`proofread_zh`(校对后)、`trans_by`、`problem` 等字段。文件全部句子译完后，生成 `transl_cache/pass3_cache/{文件名}.json`

&nbsp;&nbsp;&nbsp;&nbsp;**（可选）译后改进（ForImproveTranslation / ForBRStation）**
&nbsp;&nbsp;&nbsp;&nbsp;整文件译完后评估质量，对可改进句生成备选译文写入缓存的 `alt_dst` 字段（如换行位置异常修复），可在"校对审核"页替换为正文。

&nbsp;&nbsp;&nbsp;&nbsp;**字典的生效位置**
&nbsp;&nbsp;&nbsp;&nbsp;- 预处理（译前）字典：翻译前替换日文原文中的专名/术语。
&nbsp;&nbsp;&nbsp;&nbsp;- GPT 字典：作为术语表注入提示词，约束译名统一。
&nbsp;&nbsp;&nbsp;&nbsp;- 人名替换表：专用于 `name` 字段的翻译/替换。
&nbsp;&nbsp;&nbsp;&nbsp;- 后处理（译后）字典：在生成 `gt_output` 时对中文译文做最终替换；改动后须重跑以重新生成 `gt_output` 方生效。

&nbsp;&nbsp;&nbsp;&nbsp;**生成文件的使用与回改**
&nbsp;&nbsp;&nbsp;&nbsp;- `gt_output/` 为最终产物，可直接交注入工具导回游戏（见 2.3 节）。
&nbsp;&nbsp;&nbsp;&nbsp;- 改单句译文：编辑 `pass3_cache/{文件名}.json` 中的 `proofread_zh`，或删除整行 `pre_zh` 触发该句重翻。
&nbsp;&nbsp;&nbsp;&nbsp;- `transl_cache` 下 `pass1/pass2/pass0` 的所有缓存文件均可直接修改，文件自动保存，pass3内的文件除外。

&nbsp;&nbsp;&nbsp;&nbsp;文件流向可记为：`gt_input/*.json`（原始剧本）→ `pass0~pass3` 缓存（各阶段中间产物，带缓存跳过）→ `gt_output/*.json`（最终成品）。

</details>

&ensp;&ensp;&ensp;&ensp;**⑤ 校对审核**：翻译完成后到"校对审核"页面逐条检查、修改译文，可利用问题检测、备选译文交换、查找替换与撤销/重做提升效率，改好后点击"构建输出"生成最终文件。

&ensp;&ensp;&ensp;&ensp;桌面端支持同时打开多个项目、深色模式、自定义背景等功能，具体可在"设置"页面调整。

* **【2.2b. 使用命令行翻译（高级用户）】**
&ensp;&ensp;&ensp;&ensp;如需使用命令行版本，在项目示例文件夹`sampleProject`中，将`config.inc.yaml`重命名为`config.yaml`，将日文json文件放入`gt_input`文件夹，将`项目GPT字典.txt`、`项目字典_译前.txt`、`项目字典_译后.txt`复制到项目根目录，然后在`config.yaml`中配置翻译后端：

```yaml
# 翻译后端相关设置
backendSpecific:
  OpenAI-Compatible: # (ForGal/ForNovel/GenDic)OpenAI API兼容接口通用
    tokens:
      - token: sk-example-key1
        endpoint: https://api.deepseek.com # 请求地址，加不加v1都可以
        modelName: deepseek-chat
      - token: sk-example-key2
        endpoint: https://openrouter.ai/api/v1/chat/completions # /chat/completions结尾则不自动补v1
        modelName: deepseek/deepseek-chat-v3-0324:free
        stream: true # 支持为单个token设置流式请求
```   

* 一些收费api转发项目，例如：[硅基流动](https://cloud.siliconflow.cn/i/SvDatvsk)（modelName: "deepseek-ai/DeepSeek-V3.1-Terminus"）、[oaipro](https://api.oaipro.com/register?aff=ceAU)等等，以上只是举例，更多中转可以谷歌，本项目不担保它们的稳定性及可用性。   
   
&ensp;&ensp;&ensp;&ensp;但要注意这里获取的key填入的同时要修改endpoint地址，一般在对应平台的说明里能找到：   
```yaml
      - token: sk-example-key1
        endpoint: https://api.siliconflow.cn # 请求地址，加不加v1都可以
```   
   
&ensp;&ensp;&ensp;&ensp;修改好项目设置后，确保你已经安装了需要的依赖（见环境准备），然后双击`run_GalTransl_terminal.bat`，输入项目路径即可开始翻译。也可以直接用命令行调用：

```bash
python -m GalTransl -p <项目目录> -t <翻译引擎> [-l info]
```

&ensp;&ensp;&ensp;&ensp;**但是，不建议就这样开始翻译了**，请至少要先学会[GPT字典的使用](#gpt字典)，或者选择GenDic来生成一个人名字典，为你要翻译的gal设定好各角色的人名字典，这样才能保证基本的翻译质量。   

&ensp;&ensp;&ensp;&ensp;翻译完成后，**记得修修缓存**，因为大模型经常会犯错。GalTransl会自动查找一些常见问题并记录于缓存中。可以对缓存进行修正，并重新运行程序来基于缓存重新生成结果json，见[自动化找错章节](#自动化找错)和[翻译缓存章节](#翻译缓存)

* **【2.3. 构建中文脚本】**   
&ensp;&ensp;&ensp;&ensp;如果你是使用GalTransl提取注入工具提取的脚本，构建同理，选择日文脚本目录、中文json目录、中文脚本保存目录，然后点'注入'，即可将文本注入回脚本。但这里面有一些坑，第四章会提到。

注：   
1. 这里一般把中文脚本保存目录叫script_cn，因为日文脚本目录叫script_jp   
2. 一般使用什么工具导出，就用什么工具导入。所以要先尝试导入导出是否都正常再开始翻译。   


</details>

<details>

<summary> 

### 第三章 封包或免封   

 </summary>

&ensp;&ensp;&ensp;&ensp;构建好中文脚本后，下一步就是想办法让游戏读取。首先目前主流引擎基本都是支持免封包读取的，可以继续参考Dir-A佬的[教程](https://space.bilibili.com/8144708/)，看看你要搞的引擎支不支持免封包读取。   
&ensp;&ensp;&ensp;&ensp;特别的，针对krkr/krkrz引擎，可以使用arcusmaximus大佬的[KirikiriTools工具](https://github.com/arcusmaximus/KirikiriTools)，下载里面的version.dll，丢到游戏目录里，然后在游戏目录里新建一个"unencrypted"文件夹，将脚本直接丢进去（不用新建二级目录），就可以让krkr读取   

</details>

<details>

<summary>

### 第四章 引擎与编码   

</summary>

&ensp;&ensp;&ensp;&ensp;在这一章首先需要了解一下unicode、sjis(shift jis)、gbk编码的基础知识，为了偷懒在这里原作者还是放[Dir-A佬的文章](https://www.bilibili.com/read/cv12367744/)，如果你对这块不了解的话，先去读一下。   

如果你在做的引擎支持unicode编码，例如krkr、Artemis引擎等，一般就可以直接玩了。但如果引擎是使用sjis编码的话，直接打开会是乱码，这时候需要通过2种路线尝试使其可以正常显示中文：   

路线1：使用GBK编码注入脚本，然后修改引擎程序使其支持GBK编码   
路线2：仍然使用jis编码注入脚本，但通过jis隧道或jis替换（推荐）2种方式，结合通用注入dll在运行过程中通过动态替换来显示中文   

GalTransl提取注入工具的VNTextPatch模式注入脚本时默认是以sjis或unicode(utf8)编码注入的，这取决于引擎类型。

* **使用路线1**   
（注：这个模式现在有bug，有的引擎会卡死）在注入前勾选"GBK编码注入"，在这个模式下所有GBK编码不支持的字符将被替换成空白，例如音符♪   
然后需要ollydbg或windbg工具，[在这里下载](https://down.52pojie.cn/Tools/Debuggers/)，用于修改引擎。   
最后还是去看[Dir-A佬的教程](https://space.bilibili.com/8144708/)，里面有教如何下断点、修改，完全没接触过逆向的话这可能很难，但没办法，照着视频多试试。   

* **使用路线2**   
在注入脚本时先什么都不勾选，如果有提示"sjis_ext.bin包含文字：xxx"的话，说明程序是以sjis编码注入的，并把这些不支持显示的字符放到script_cn目录内的sjis_ext.bin里供sjis隧道模式调用了。   

**jis隧道**：仍然来自arcusmaximus大佬的VNTranslationTools项目中的[VNTextProxy组件](https://github.com/arcusmaximus/VNTranslationTools#vntextproxy)。VNTextPatch在将文本注入回脚本时，会将sjis编码不支持的字符临时替换为sjis编码中未定义的字符，VNTextProxy通过DLL劫持技术HOOK游戏，并在遇到这些字符时再把它还原回去。   

当使用sjis隧道模式时，将`script_cn`内的`sjis_ext.bin`文件移动到游戏目录内，然后将useful_tools\VNTextProxy内的所有dll逐个丢到游戏目录内(一般推荐先试version.dll，或使用PEID/DIE等工具查输入表)，运行游戏，看有没有哪个dll可以正确的hook游戏并让不显示的文本可以正常显示（不正常的话那些地方会是空的）。不正常的话，删掉这个DLL，换下一个。[详细设置见此](https://github.com/XD2333/GalTransl/tree/main/useful_tools/VNTextProxy)

**jis替换**：来自AtomCrafty大佬的[UniversalInjectorFramework(通用注入框架)](https://github.com/AtomCrafty/UniversalInjectorFramework#character-substitution)项目，也是通过DLL劫持技术HOOK游戏，并可以将某个字符根据设置替换成指定的另一个字符，不限编码。原作者建立了[一套替换字典](https://github.com/XD2333/GalTransl_DumpInjector/blob/main/hanzi2kanji_table.txt)，按一些规则梳理了jis编码内不支持的简中汉字与jis支持的日文汉字的映射关系，可以满足99.99%常用简体中文汉字的正常显示(见hanzi2kanji_table.txt)，并将替换功能写在了GalTransl提取注入工具内(新：现在[SExtractor](https://github.com/satan53x/SExtractor)也支持替换，并且更好用)。在替换后结合UniversalInjectorFramework的动态Hook替换功能在游戏中将这些日文汉字替换回简中文字，实现游戏的正常显示。

当使用sjis替换模式时，可以先运行一遍GalTransl提取注入工具的注入文本，获取游戏不支持的文字列表（注入后会提示"sjis_ext.bin包含文字：xxx"），然后，勾选"sjis替换模式注入"，把这些文字复制到右边的文本框内，再点击注入。注入后会获得一个sjis替换模式配置。

打开useful_tools/UniversalInjectorFramework文件夹，里面也是很多dll，也是逐个尝试，一般推荐先试winmm.dll，把目录内的uif_config.json一并复制到游戏目录，然后编辑这个json，按GalTransl提取注入工具提供的配置填写`source_characters`和`target_characters`。   
然后运行游戏，如果游戏可以正常运行，并且弹出了一个像这样的控制台：   
![img_terminal](./img/img_terminal.png)
那多半就搞定了。如果不正常的话，删掉这个DLL，尝试换下一个。   
注：UniversalInjectorFramework也支持sjis隧道模式，可以设置`tunnel_decoder`为`True`然后在`mapping`里填入sjis_ext.bin包含文字。   
注：UniversalInjectorFramework的控制台窗口可以隐藏，[详细配置文件设置见此](https://github.com/XD2333/GalTransl/tree/main/useful_tools/UniversalInjectorFramework)   

</details>

## GalTransl核心功能介绍
介绍GPT字典、缓存、普通字典、找问题等功能。    
（点击展开详细说明）     
<details>

<summary>   
   
### GPT字典
&ensp;&ensp;&ensp;&ensp;GPT字典系统是使用GalTransl翻译时想提高质量的关键功能，通过补充设定的方式大幅提高翻译质量，是GPT翻译区别于传统机翻的核心。适用于各类 OpenAI 兼容后端。   
在程序目录中，`Dict`文件夹内有"通用GPT字典.txt"，在项目文件夹内可以新建"项目GPT字典.txt"，一般人名定义写进项目字典，通用提高翻译质量的词汇写进通用字典。在桌面端"字典管理"页面的 **GPT 字典** 标签页中即可编辑项目/通用 GPT 字典。   
   
</summary>   

* 举例来说，你可以提前在这里对每个角色名的中文翻译进行定义，并说明这个角色的设定，例如性别、大致年龄、职业等。通过自动给GPT喂这些设定，可以自动调整合适的人称代词他/她、称谓等，并固定人名为假名时的中文翻译。   
* 再比如，可以在这里为GPT补充一些它总是翻不对的词语，如果提供一定的解释，它会理解的更好。 
   
---   
   
* 通过下面这个例子认识GPT字典喂人物设定的用法，每行的格式为`日文[Tab]中文[Tab]解释(可不写)`，注意中间的连接符为**TAB**   
```
フラン	芙兰	name, lady, teacher
笠間	笠间	笠間 陽菜乃’s lastname, girl
陽菜乃	阳菜乃	笠間 陽菜乃's firstname, girl
张三	张三	player's name, boy
$str20	$str20	player's codename, boy
```
这几条字典都是定义角色用的：   
* 第一条可以理解为原作者想告诉GPT：“假名フラン的翻译是芙兰，这是人名，是位女士，是老师”。这样GPT在翻译フラン先生的时候就会翻译成芙兰老师而不会是芙兰医生。   
* 二三条是同一个人的日本姓和名，经测试姓名必须拆成两行写，不然GPT3.5会不认识。
* 第四条是设定主角的推荐写法。**注意即使日文和中文相同，也要再重复一遍**   
* 第五条是主角在脚本中使用占位符而不是名字时的推荐写法。
* **设定不要太复杂**，否则会让GPT多很多奇怪脑补。     

---   
   
* 通过下面这个例子认识GPT字典喂生词的用法，每行的格式亦为`日文[Tab]中文[Tab]解释(可不写)`，注意中间的连接符为**TAB**   
```
大家さん  房东
あたし	我/人家	use '人家' when being cute
```
* 当你发现GPT不太认识这个词，例如“大家さん”，并且这个词含义比较唯一，那么就可以像这样加进通用GPT字典里，解释不是必要的。   
* 第二行的中文写了一个多义词“我/人家”，并且在解释中写了“当扮可爱时用人家”。GPT3.5没那么聪明，但GPT4基本可以灵活运用。
* 想让GPT更瑟？自己加字典（   

在程序目录中，`Dict`文件夹内有"通用GPT字典.txt"，在`sampleProject`文件夹内会有"项目GPT字典.txt"，一般人名定义写进项目字典，通用提高翻译质量的词汇写进通用字典。   
只有当本次发送给GPT的人名和句子中有这个词，这个词的解释才会被送进本轮的对话中。   
**但不要什么词都往里加**，~~什么都往里加只会害了你~~，推荐只写**各角色的设定**和**总是会翻错的词**。 

此外，你还可以使用 **GenDic** 后端让 AI 从源文本自动生成 GPT 字典，或使用**人名替换表**（CSV/XLSX）集中管理角色名的替换。

</details>   
   
<details>

<summary>   

### 常规字典
在GalTransl中，常规字典是分为"译前字典"与"译后字典"的（桌面端"字典管理"页面对应为 **预处理** 与 **后处理** 标签页）。译前字典是在翻译前对日文的 a to b 替换处理，译后字典是对译后中文的 a to b 替换处理。   

</summary>   

译前字典多用于一些口齿不清的矫正情况，以及多个词代表同个意思的话，可以用译前字典先统一，减少GPT字典的输入。   
   
译后字典就是比较常见的字典，在译后将某个词替换成另一个词，但是此处原作者改进了一个叫"条件字典"的东西。条件字典实际上就是在替换前增加了一步判断，用于避免误替换、过度替换等情况。   
每行格式为`pre_jp/post_jp[tab]判断词[tab]查找词[tab]替换词`   
* pre_jp/post_jp代表判断词查找的位置，定义在"翻译缓存"章节讲
* 判断词：如果在查找位置(pre_jp/post_jp)中找到判断词，才会激活后面的替换。   
* 判断词可以在开头加"!"代表"不存在则替换"，否则一般是代表"存在则替换"。   
* 判断词可以使用`[or]`或`[and]`关键字连接，多个`[or]`连接代表"有一个条件满足就进入替换"，多个`[and]`连接代表"条件都满足才进入替换"。   
* 查找词、替换词，同普通字典，将a替换成b。   
* 桌面端"字典管理"页面的 **人名替换** 标签页提供独立的人名替换表（CSV/XLSX），可批量维护角色名。

</details>

<details>

<summary>   

### 翻译缓存
开始翻译后，可以在 `transl_cache/pass3_cache` 目录内找到翻译缓存文件。在桌面端可直接在"校对审核"页面打开缓存并逐条修改。   
</summary>  

翻译缓存与 `gt_input` 中的源 JSON 一一对应，在翻译过程中，翻译结果会优先写进缓存里，当一个文件被翻译完成后，才会出现在 `gt_output`（结果 JSON）里。   

首先，总结一些要点：   
1. 当你想重翻某句时，打开对应的翻译缓存文件，删掉该句的 pre_zh 整行(**不要留空行**)   
2. 当你想整段重翻时，直接删对应的数个 object 块，重翻某文件时，直接删对应的翻译缓存文件。   
3. 当GalTransl正在翻译时，不要修改正在翻译的文件的缓存，改了也会被覆写回去。   
4. `gt_output` 结果文件 = 翻译缓存内的 pre_zh/proofread_zh + 译后字典替换 + 恢复对话框   
5. 当新的 post_jp 与缓存内的 post_jp 不一致时，会触发重翻，一般发生在添加了新的译前字典时

下面是翻译缓存的典型样例：   
```json
    {
        "index": 4,
        "name": "",
        "pre_jp": "欠品していたコーヒー豆を受け取ったまでは良かったが、\r\n帰り道を歩いていると汗が吹き出してくる。",
        "post_jp": "欠品していたコーヒー豆を受け取ったまでは良かったが、\r\n帰り道を歩いていると汗が吹き出してくる。",
        "pre_zh": "领取了缺货的咖啡豆还好，\r\n但是走在回去的路上就汗流浃背了。",
        "proofread_zh": "领了缺货的咖啡豆倒是没问题，\r\n可是走在回去的路上，汗水就冒了出来。",
        "trans_by": "NewBing",
        "proofread_by": "NewBing",
    },
```
解释一下每个字段的含义:  
* 基本参数：   
`index`  序号   
`name`  人名   
`pre_jp`  原始日文（`pre_src` 的兼容别名）   
`post_jp`  处理后日文（`post_src`）。一般来讲，post_jp = pre_jp 去除对话框 + 译前字典替换。你会代码的话也可以在此处加入自己的处理   
`pre_zh`  原始中文（`pre_dst` 的兼容别名）   
`proofread_zh`  校对的中文（`proofread_dst`）   
（没有 post_zh，post_zh 在结果文件夹里。）   
`trans_by`  翻译引擎/翻译者   
`proofread_by`  校对引擎/校对者    
`problem`  存储问题。见下方自动化找错。   
`post_zh_preview`  用于预览 `gt_output`，但**对它的修改并不会应用到输出**，要修改 `pre_zh`/`proofread_zh`

* **推荐使用桌面端修缓存**：在"校对审核"页面直接打开缓存文件，逐条修改译文，支持撤销/重做、问题筛选、备选译文交换与"保存并重检问题"，改好后点击"构建输出"即可生成新的结果 JSON。

* 命令行用户也可以用 **EmEditor** 修缓存：选中一个文件，右键-EmEditor 打开，然后把 `transl_cache` 内所有文件全选拖进去。   
这时候标签可能会占很大位置，右键标签-自定义标签页，将"标签不合适时"改成"无"，这样标签就只会在一行了（需要使用Emeditor专业版）。   
接着 ctrl+f 搜索，搜索你感兴趣的关键字（如 problem、doub_content），勾选"搜索组群中所有文档"，即可快速在所有文件中搜索，或点提取快速预览所有的问题。

* **VSCode**也是非常好的修缓存工具，只要使用 VsCode 打开缓存文件夹，然后全局搜索如 problem，就可以快速定位所有问题   

* 在确定需要修改的内容后，直接修改对应句子的`pre_zh`，或`proofread_zh`，然后**重新跑一遍 GalTransl**，很快就会生成新的结果 JSON
  
</details>

<details>

<summary>   

### 自动化找错

GalTransl根据长期对翻译结果的观察建立了一套根据规则自动找问题的系统。
</summary>  
找问题系统的开启是在各个项目的`config.yaml`里，默认配置是这样的

```yaml
# 自动问题分析配置，在-前面加#号可以禁用
problemAnalyze:
  problemList: # 要发现的问题清单
    - 词频过高 # 重复大于20次
    - 标点错漏 # 标点符号多加或漏加
    - 残留日文 # 日文平假名片假名残留
    - 丢失换行 # 缺少换行符，一般没事
    - 多加换行 # 换行符比原句多，可能导致溢出屏幕
    - 比日文长 # 比日文长1.3倍以上
    - 字典使用 # 没有按GPT字典要求翻译
    - 语言不通 # 疑似没有被翻译成目标语言，翻译为中文时检查是否包含非GBK字符
    #- 引入英文 # 本来没有英文，译文引入了英文
    #- 比日文长严格 # 严格查找，不能比日文长
```

目前支持找以上问题，有的项目被#号注释，可以取消来开启，或手动加上#号关闭对应问题的查找。

找到问题后会存在翻译缓存里，见翻译缓存章节。桌面端在"校对审核"页面可使用 **问题检测** 侧栏批量查看所有问题，并通过修改缓存来修正问题。

（新） 现在还可以通过在 config.yaml 中配置 `retranslKey` 来批量重翻某个问题，例如  `retranslKey: "残留日文"`   

</details> 

## 配置文件与翻译引擎设置

桌面端通过图形界面管理翻译后端配置（"后端配置"页面），无需手动编辑 YAML。支持两种类型的配置方案：

* **OpenAI-Compatible**：OpenAI 兼容接口通用，可配置多个 token，每个 token 含 `endpoint`、`modelName`、`stream` 等参数，适配 DeepSeek、OpenRouter、硅基流动等各类中转服务。
* **SakuraLLM**：本地/远程 Sakura 模型，可配置多个端点。

可将某套方案设为"默认"，翻译时在"翻译控制台"直接选用即可。项目级配置可在"项目配置"页面修改。

命令行版本的详细设置项可以直接阅读 `config.yaml` 配置文件注释，目前已经比较详细。



