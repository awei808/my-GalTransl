<div align=center><img width="150" height="150" src="./img/logo.png"/></div>

<h1><p align='center' >GalTransl</p></h1>
<p align='center' >Visual Novel Automatic Translation Solution Supporting GPT-4/Claude/Deepseek and More</p>

  [中文](./README.md)

  GalTransl is a set of Galgame automatic translation tools that combines several minor innovations in basic functions with deep use of GPT prompt engineering (Prompt Engineering) to create embedded translation patches. The refactored version provides a **desktop graphical interface** — no command-line experience needed to complete the full translation workflow.

## Preface
&ensp;&ensp;&ensp;&ensp;The core of GalTransl is a set of automatic translation scripts with a companion desktop workbench, which solves most of the known problems in using LLMs to automatically translate Galgames, and greatly improves overall translation quality. At the same time, by combining with other projects, it connects the complete process of making patches, lowering the entry barrier to some extent. Those interested can more easily build machine-translation patches of reasonable quality through this project, and (perhaps) try to efficiently build higher-quality localization patches on top of this framework.

* **Original Author Statement**: The original author of GalTransl is [xd2333](https://github.com/XD2333/GalTransl). This project (my-GalTransl) is a **personally refactored and extended** derivative version based on the original, not officially maintained by the original author. The core design, prompt engineering, and algorithmic ideas of the original project belong to the original author — with thanks. Any issues or defects in this project do not represent the original author.

* **About This Project**: my-GalTransl is a version iteratively developed by an individual using AI (vibecoding). **But please don't worry too much about code quality** — the current code still contains some messy layers and unreconstructed duplicate code, which will be gradually cleaned up and refactored after features are complete. The current stage prioritizes usability and fast iteration.

## Security Risk Notice
&ensp;&ensp;&ensp;&ensp;This project is a personal refactored version that reuses parts of the original project's implementation. The following known security risks exist; please be aware before use:

- **File read/write is unauthenticated by default**: The interface can read and read/write files at arbitrary paths by default. Reason: the original project already had this issue. Mitigation: the backend only binds `127.0.0.1` with a CORS whitelist, so the remote attack surface is small — do not expose the local service on untrusted networks.
- **API keys are shown in plaintext**: The API keys in the project configuration are stored and displayed in plaintext. Reason: the original project already had this issue. Impact: at most it causes key leakage, and only happens when the local machine is maliciously compromised or the project configuration (including keys) is actively shared — do not share configurations containing keys with others or commit them to public repositories.

## Differences from the Original Project
&ensp;&ensp;&ensp;&ensp;The purpose of this refactoring is to **explore how to achieve higher-quality AI localization**, which inevitably brings higher token costs. The main adjustments compared to the original project are:

1. **Fully refactored frontend**: The desktop app is rewritten with Tauri 2 + SolidJS, with a drastically different UI style from the original and re-divided interface.
2. **More pipelines and richer prompts**: More stages are added to the translation pipeline, and more information and rules (e.g. global analysis, file-level metadata, plot route map, **all stages can be skipped and cache files manually edited**) are injected into the prompts to pursue better translation quality.
3. **Multi-turn conversation translation backend**: Instead of the original single-turn conversation style, multi-turn conversations are used to obtain more comprehensive context and produce better translations.
4. **AI post-translation improvement**: After the translation pipeline completes, multiple post-processing engines can be run in order via the `gpt.afterTranslation` ordered array: translation improvement (improve), line-break fixing (brfix), Japanese-residue fixing (jpfix), forbidden-word fixing (banfix), semantic detection (semcheck), and second-round confirmation of hit sentences (semcheckagain).
5. **New problem detection items and skip-check**: On top of the original problem detection, new items are added: **long sentence missing line break**, **abnormal line break position**, **attributive/adverbial overlong**, **H-scene inappropriate wording**, **suspected error** (AI semantic detection), etc.; translations can also be marked with a skip-detection flag so they are no longer recorded as problems.
6. **Different detection thresholds and handling for H/non-H intervals**: H scenes (H intervals marked in the batch metadata) and non-H scenes use differentiated strategies in detection and translation — **long-sentence-missing-line-break** uses an H-scene-specific sentence length threshold (`avgSentenceLengthThresholdH`, default 24, vs 17 for non-H scenes); **inappropriate wording** detection hits the H word-list inside H scenes and the forbidden-word list outside them; the **GPT dictionary is split into H/non-H libraries** and injected into translation prompts by scene, so H vocabulary does not pollute normal plot translation.
7. **Alternative translation feature**: Let AI evaluate and output improvable translations, or provide AI-fixed alternative translations for specific problem items; users can freely switch between the alternative and current translation on the proofreading page.
8. **New project wizard with more settings**: In the new-project wizard, you can set the game's external information, plot outline and plot structure type, and toggle whether each pipeline stage runs.
9. **Auto-save**: Dictionaries, cache metadata files and config files auto-save on focus loss; the translation editing view does not support auto-save due to its complexity and the need for undo.
10. **Cache structure change**: To adapt to multi-pipeline translation (global analysis → file-level metadata → translation execution and other stages), the translation cache structure has been adjusted as necessary and is incompatible with old translation projects.
11. **Translation project path**: Newly created translation projects can only be located in the application's own directory; old projects are not subject to this restriction.
12. **Known unfixed issues**: Some UI issues such as occasional duplicate popups in the translation console and file progress display bugs in individual cases are **not yet fully fixed**; undo/redo (including cross-file) and project-switch state residue issues have been largely resolved.
13. **Galgame localization only**: Focused on the Galgame text translation scenario; the original project's general file features such as epub and subtitles **cannot work properly**.
14. **Focus on LLMs, partially compatible with local small models**: Fully adapted and optimized for cloud LLMs (GPT-4/Claude/Deepseek and other OpenAI-compatible interfaces); local small models compatible with the OpenAI format can also be connected and used.
15. **CLI not adapted**: The refactoring mainly revolves around the desktop app; the command-line version (CLI) **has not been adapted and may not work properly**.
16. **Other minor changes**: JSON uniformly uses `\n` line breaks (instead of `\r\n`), dictionaries uniformly use PSV format, etc.

  * Features:
  1. 🖥️ **Desktop GUI** — Modern desktop app built with Tauri 2 + Rust + **SolidJS**, no command-line needed for the full process: new project, import files, configure backend, translate, proofread, build output
  2. Supports **GPT-4/Claude/Deepseek** and other LLMs, with unified management of multiple OpenAI-compatible backend profiles (local small model interfaces retained but may not work properly)
  3. **GPT Dictionary**, letting the model understand character settings and accurately translate names, pronouns, and new words
  4. Flexible automated dictionary system via pre-translation, post-translation, and conditional dictionaries, with support for **H/non-H dual GPT dictionaries**, forbidden-word dictionary and **name replacement tables** (CSV/XLSX)
  5. Real-time cache saving, automatic breakpoint resume; visual display of prompts, translation concatenation, concurrent progress and speed during translation
  6. Complete pipeline: input validation → text compression → global game analysis → automatic glossary building → file-level metadata → plot route map → batch division → translation execution → (optional) post-translation improvement/fixing/semantic detection, with multi-stage prompt injection for better quality
  7. Built-in **proofreading workbench**: sentence-by-sentence review/edit, problem detection, alternative translation swap, undo/redo, cross-file find-replace
  8. Focused on the Galgame text translation scenario; other general file formats (srt, epub, etc.) may not work properly
  9. Supports **plugin system**: custom file formats and text processing pipelines, highly extensible

<b>❗❗When publishing translations made with this tool without full manual proofreading/polishing, please clearly label them as "GPT translation/AI translation patch", not "personal translation" or "AI localization".</b>

## Recent Updates
* 2026.8: Updated v0.3.0, added **semantic detection** (AI flags suspected mistranslation/omission/line-merging, with second-round confirmation of hit sentences), **H/non-H dual GPT dictionaries** and forbidden-word dictionary, Japanese-residue/forbidden-word fix engines, attributive/adverbial-overlong and H-scene wording detection, cross-file undo/redo; backend refactored with BaseEngine split (API client decoupled from translation rounds)
* 2026.8: Updated v0.2.0, completed the **full pipeline** (input validation → text compression → global analysis → glossary building → file-level metadata → plot route map → batch division → translation execution), per-stage pipeline switches, translation improvement (ForImproveTranslation) and line-break fixing (ForBRStation), alternative translations, long-sentence-missing-linebreak / abnormal-linebreak-position detection
* 2026.8: Personal refactor v0.1.0, using a **desktop GUI** (Tauri 2 + SolidJS + Rust), with visual translation workbench, proofreading, unified backend configuration management, name replacement tables, etc.
* 2026.4: Updated v7, added **desktop GUI** (Tauri + React), with dark mode, custom backgrounds, multi-project management, visual translation workbench, etc.
* 2025.5: Updated v6, added ForGal translation template, GalTransl-14B-v3 model
* 2024.5: Updated v5, added GalTransl-7B model, multiple file type support
* 2024.2: Updated v4, mainly added plugin system
* 2023.12: Updated v3, file-based multithreading
* 2023.7: Updated v2, major code refactoring
* 2023.6: v1 initial release

## Navigation
* [Environment Preparation](#environment-preparation): Installing environment and software
* [Getting Started Tutorial](#getting-started-tutorial): Full process introduction on making a machine-translated patch. **If you only want to know how to use this tool, jump directly to section 2.2 of Chapter 2**
* [Configuration and Engine Settings](#configuration-and-engine-settings): Introduction to translation backend (OpenAI-compatible interface / SakuraLLM) configuration
* [GalTransl Core Features](#galtransl-core-features): GPT dictionary, cache, ordinary dictionary, problem finding, etc.

## Environment Preparation
  * **Desktop Version (Recommended)**
  Download the latest release zip from [Release](https://github.com/awei808/my-GalTransl/releases), extract it, and double-click `GalTransl Desktop.exe` to start. **No Python or any dependencies required.** The desktop app automatically starts the backend service.

  * **Command-line Version (Developers / Advanced Users)**
  To use the command-line version or participate in development:

  1. Download this project or clone the repository, extract to any location
  2. Install Python 3.11+. [Download](https://www.python.org/downloads/)
  **Check "Add Python to PATH" during installation**
  3. Install Python dependencies: run `pip install -r requirements.txt`
  4. (Desktop development) Install Node.js 18+, run `npm install` in the `desktop` directory, then run `run_desktop_dev.bat`

## Practical Tools
| Name | Description |
| --- | --- |
| GARbro | Engine tool: Universal unpacker. [Download](https://github.com/morkt/GARbro/releases/download/v1.5.44/GARbro-v1.5.44.2904.rar) |
| [KirikiriTools](https://github.com/arcusmaximus/KirikiriTools) | Engine tool: Krkr, krkrz extraction and injection tool |
| [UniversalInjectorFramework](https://github.com/AtomCrafty/UniversalInjectorFramework) | Engine tool: Shift-JIS tunnel, Shift-JIS replacement mode universal injection framework |
| [VNTextProxy](https://github.com/arcusmaximus/VNTranslationTools) | Engine tool: Shift-JIS tunnel mode universal injection framework |
| GalTransl_DumpInjector | Script tool: [VNTextPatch](https://github.com/arcusmaximus/VNTranslationTools) GUI, comprehensive script text extraction/injection tool |
| [SExtractor](https://github.com/satan53x/SExtractor) | Script tool: Comprehensive script text extraction/injection tool |
| [msg-tool](https://github.com/lifegpc/msg-tool) | Script tool: Comprehensive script text extraction/injection tool |
| [DBTXT2Json_jp](https://github.com/XD2333/DBTXT2Json_jp) | Script tool: Double-line text and json_jp conversion script |
| [EmEditor](https://www.ghxi.com/emeditor.html) | Text tool: Powerful text editor, mainly for editing cache files |
| [VSCode](https://code.visualstudio.com/) | Text tool: Powerful text editor, mainly for editing cache files |
| [KeywordGacha](https://github.com/neavo/KeywordGacha) | Text tool: Automatic glossary generation using OpenAI-compatible API |

## Getting Started Tutorial
The general process of making a Galgame embedded translation patch is:
1. Identify the engine -> unpack the resource pack to get the script -> go to 2.
2. Dump the script into Japanese text -> translate into Chinese text -> build the Chinese script -> go to 3.
3. Pack as resource pack / non-pack -> go to 4.
4. If the engine supports Unicode, just play -> if the engine uses Shift-JIS, try 2 approaches to make it display Chinese.

This section is split into the above 4 modules with step-by-step explanations, written to be beginner-friendly so those who haven't done it before can get started.

* It is recommended to only run the translation of the first file, or just add some Chinese randomly, and return to the game to confirm it displays normally before translating everything.

(Click to expand detailed instructions)
<details>

<summary>

### Part 1 Identification and Unpacking

</summary>
Identifying the engine is actually very simple. Usually, using GARbro to open any resource pack in the game directory, the engine name will be displayed in the lower left corner of the status bar.

Or, refer to the [supported formats](https://morkt.github.io/GARbro/supported.html), and compare the suffixes of the resource packs.

Scripts are usually in some resource packs with obvious keywords, or in directories with obvious keywords in the resource packs, such as: scene, scenario, message, script, etc. And scripts are usually divided into obvious chapters and characters, some of which are also divided into main route and erotic (such as with _h); usually you can find them by looking through a few resource packs.

Or, refer to [Dir-A's tutorial](https://space.bilibili.com/8144708/).

Especially for the new krkrz engine, GARbro can no longer open the resource pack, you can use the [KrkrzExtract project](https://github.com/xmoezzz/KrkrzExtract/releases/tag/1.0.0.0), drag the game to the exe to start. Then download a full cg save, and skip all the plots directly, you can also get the script file.

</details>
<details>

<summary>

### Part 2 Extraction and Translation

</summary>

* **【2.1. Extract script text】**
&ensp;&ensp;&ensp;&ensp;Usually, this project is combined with the [VNTextPatch tool](https://github.com/arcusmaximus/VNTranslationTools) to unpack the script. VNTextPatch is a universal tool developed by arcusmaximus that supports extraction and injection of scripts for [many engines](https://github.com/arcusmaximus/VNTranslationTools#vntextpatch). (But not all these engines are guaranteed to work — some games fail extraction in practice.)

&ensp;&ensp;&ensp;&ensp;VNTextPatch is operated using cmd. To lower the difficulty, a graphical interface was built; you can find it in the project's `useful_tools/GalTransl_DumpInjector`, click `GalTransl_DumpInjector.exe` to run.
&ensp;&ensp;&ensp;&ensp;Now, you only need to select the Japanese script directory, then select the directory to save the extracted Japanese json. Generally put the Japanese script in a folder called `script_jp`, then create a new `gt_input` directory to store the extracted script:
![Picture 1](./img/img_dumper.png)
&ensp;&ensp;&ensp;&ensp;Note that GalTransl uses name-message format JSON for input, processing and output throughout. [What is JSON](http://c.biancheng.net/json/what-is-json.html)
The extracted json file can be opened with EmEditor, and generally looks like this:
```json
[
  {
    "name": "咲來",
    "message": "「ってか、白鷺学園だったらあたしと一緒じゃん。\r\nセンパイだったんですねー」"
  }
]
```
&ensp;&ensp;&ensp;&ensp;Each `{object}` is a sentence, `message` is the message content, and if the object also has a `name`, it means it is a dialogue. But not all script types can extract `name` — **when names can be correctly extracted, GalTransl's translation quality will be better**.
&ensp;&ensp;&ensp;&ensp;PS. GalTransl only supports input of json files in a specified format, but that does not mean GalTransl is bound to the VNTextPatch tool. You can also use the SExtractor tool, which now supports exporting the name-message format JSON that GalTransl needs.

* **【2.2. Using Desktop GUI (Recommended)】**
&ensp;&ensp;&ensp;&ensp;Download the latest version from [Release](https://github.com/awei808/my-GalTransl/releases), extract it, and double-click `GalTransl Desktop.exe` to start the desktop app. The desktop app automatically starts the backend service, no manual operation needed.

&ensp;&ensp;&ensp;&ensp;After starting, switch functional views via the left activity bar: **Translation Console / Proofreading / Find-Replace / Problem Detection / View Alternatives / Dictionary Management / Build Output / Settings**.

&ensp;&ensp;&ensp;&ensp;**① New Project (5-step wizard)**: Click "New Project" on the home page, and complete the wizard in order:
1. **Project Location**: Enter the project name, automatically created under the backend workspace root (containing `gt_input` / `gt_output` / `transl_cache` and `config.yaml`)
2. **Import Files**: Drag the JSON files to be translated into the window, or click "Select Files" to import them into `gt_input`
3. **Translation Backend**: Select a profile from the global "Backend Configuration", or skip to use the project's own configuration
4. **Common Settings**: File plugin, text plugin, concurrent file count, sentences per translation, target language, translation rules
5. **Extract Names**: Automatically extract the name table from the source files when the project is opened

&ensp;&ensp;&ensp;&ensp;**② Configure Translation Backend**: On the "Backend Configuration" page, pre-configure multiple profiles in advance (`OpenAI-Compatible` type supports multiple tokens, endpoint, modelName; `SakuraLLM` type supports multiple endpoints), and set one as "Default". Select directly when translating, no need to manually edit YAML.

&ensp;&ensp;&ensp;&ensp;**③ Set Dictionaries**: On the "Dictionary Management" page, configure four types of dictionaries: **Pre-processing (pre-translation)**, **GPT Dictionary**, **Post-processing (post-translation)**, **Name Replacement** (at minimum, configure the name dictionary and GPT dictionary).

&ensp;&ensp;&ensp;&ensp;**④ Start Translation**: On the "Translation Console", select the backend and click "Start Pipeline", view translation progress, speed, current prompt and translation concatenation results in real time, and stop at any time.

&ensp;&ensp;&ensp;&ensp;**⑤ Proofread**: After translation, go to the "Proofreading" page to check and modify translations sentence by sentence. Use problem detection, alternative translation swap, find-replace and undo/redo to improve efficiency. After fixing, click "Build Output" to generate the final file.

&ensp;&ensp;&ensp;&ensp;The desktop app supports opening multiple projects simultaneously, dark mode, custom backgrounds, etc., adjustable on the "Settings" page.

* **【2.2b. Using Command Line (Advanced Users)】**
&ensp;&ensp;&ensp;&ensp;To use the command-line version, in the project's sample folder `sampleProject`, rename `config.inc.yaml` to `config.yaml`, put the Japanese json files into the `gt_input` folder, and copy `项目GPT字典.txt`, `项目字典_译前.txt`, `项目字典_译后.txt` to the project root, then configure the translation backend in `config.yaml`:

```yaml
# Translation backend settings
backendSpecific:
  OpenAI-Compatible: # (ForGal/ForNovel/GenDic) OpenAI API compatible interface
    tokens:
      - token: sk-example-key1
        endpoint: https://api.deepseek.com # Request URL, v1 can be added or not
        modelName: deepseek-chat
      - token: sk-example-key2
        endpoint: https://openrouter.ai/api/v1/chat/completions # Ending with /chat/completions means v1 is not auto-appended
        modelName: deepseek/deepseek-chat-v3-0324:free
        stream: true # Supports streaming requests for a single token
```

* Some paid API forwarding projects, e.g.: [SiliconFlow](https://cloud.siliconflow.cn/i/SvDatvsk) (modelName: "deepseek-ai/DeepSeek-V3.1-Terminus"), [oaipro](https://api.oaipro.com/register?aff=ceAU), etc. The above are just examples; more forwards can be found on Google. This project does not guarantee their stability or availability.

&ensp;&ensp;&ensp;&ensp;But note that the key obtained here must be filled in while modifying the endpoint address, which can generally be found in the corresponding platform's documentation:
```yaml
      - token: sk-example-key1
        endpoint: https://api.siliconflow.cn # Request URL, v1 can be added or not
```

&ensp;&ensp;&ensp;&ensp;After modifying the project settings, make sure you have installed the required dependencies (see Environment Preparation), then double-click `run_GalTransl_terminal.bat` and enter the project path to start translating. You can also call it directly from the command line:

```bash
python -m GalTransl -p <project directory> -t <translation engine> [-l info]
```

&ensp;&ensp;&ensp;&ensp;**However, it is not recommended to start translating right away.** Please at least learn about [GPT Dictionary usage](#gpt-dictionary) first, or use GenDic to generate a name dictionary, setting up the name dictionary for each character of the gal you want to translate, so as to ensure basic translation quality.

&ensp;&ensp;&ensp;&ensp;After translation is complete, **remember to review the cache**, as LLMs often make mistakes. GalTransl automatically finds some common problems and records them in the cache. You can fix the cache and rerun the program to regenerate the result json based on the cache. See the [Automatic Error Finding](#automatic-error-finding) and [Translation Cache](#translation-cache) sections.

* **【2.3. Build Chinese script】**
&ensp;&ensp;&ensp;&ensp;If you used the GalTransl extraction and injection tool to extract the script, build the same way: select the Japanese script directory, the Chinese json directory, and the Chinese script save directory, then click 'inject' to inject the text back into the script. But there are some pitfalls here, mentioned in Chapter 4.

Notes:
1. The Chinese script save directory is generally called `script_cn`, because the Japanese script directory is called `script_jp`.
2. Generally use the same tool for both export and import. So test both import and export before starting translation.

</details>

<details>

<summary>

### Part 3 Pack or Non-pack

</summary>

&ensp;&ensp;&ensp;&ensp;After building the Chinese script, the next step is to find a way to make the game read it. Most mainstream engines basically support non-pack reading; you can continue to refer to Dir-A's [tutorial](https://space.bilibili.com/8144708/) to see if the engine you are working with supports non-pack reading.
&ensp;&ensp;&ensp;&ensp;Especially for krkr/krkrz engines, you can use arcusmaximus's [KirikiriTools](https://github.com/arcusmaximus/KirikiriTools), download the version.dll inside, put it in the game directory, then create a new "unencrypted" folder in the game directory, and put the script directly in (no secondary directory needed), and krkr can read it.

</details>

<details>

<summary>

### Part 4 Engines and Encoding

</summary>

&ensp;&ensp;&ensp;&ensp;In this chapter you first need to understand the basics of Unicode, SJIS (Shift-JIS), and GBK encoding. To be lazy, I'll put [Dir-A's article](https://www.bilibili.com/read/cv12367744/) here; if you don't know about this, read it first.

If the engine you are working with supports Unicode encoding, such as krkr, Artemis engine, etc., you can generally play directly. But if the engine uses SJIS encoding, it will be garbled when opened directly, and you need to try 2 approaches to make it display Chinese normally:

Route 1: Inject scripts using GBK encoding, then modify the engine program to support GBK encoding
Route 2: Still inject scripts using JIS encoding, but use JIS tunnel or JIS replacement (recommended) combined with universal injection DLL to dynamically replace characters at runtime to display Chinese

The VNTextPatch mode of the GalTransl extraction and injection tool injects scripts in SJIS or Unicode (utf8) encoding by default, depending on the engine type.

* **Using Route 1**
(Note: this mode now has a bug that freezes some engines) Before injecting, check "GBK encoding injection". In this mode, all characters not supported by GBK will be replaced with blanks, such as the music note ♪.
Then you need ollydbg or windbg tools, [download here](https://down.52pojie.cn/Tools/Debuggers/), to modify the engine.
Finally, go to [Dir-A's tutorial](https://space.bilibili.com/8144708/), which teaches how to set breakpoints and modify. If you have never touched reverse engineering, this may be difficult, but there is no other way — follow the video and try more.

* **Using Route 2**
When injecting the script, don't check anything first. If there is a prompt "sjis_ext.bin contains text: xxx", it means the program injected in SJIS encoding and put these unsupported characters into sjis_ext.bin in the script_cn directory for the SJIS tunnel mode to call.

**JIS tunnel**: Also from arcusmaximus's [VNTextProxy component](https://github.com/arcusmaximus/VNTranslationTools#vntextproxy) in the VNTranslationTools project. When VNTextPatch injects text back into the script, it temporarily replaces characters not supported by SJIS encoding with undefined characters in SJIS encoding. VNTextProxy uses DLL hijacking to HOOK the game, and restores them when encountering these characters.

When using SJIS tunnel mode, move the `sjis_ext.bin` file in `script_cn` to the game directory, then put all the dlls in `useful_tools\VNTextProxy` into the game directory one by one (generally try version.dll first, or use PEID/DIE and other tools to check the import table), run the game, and see if any dll can correctly hook the game and make the hidden text display normally (if not normal, those places will be empty). If not normal, delete this DLL and try the next one. [See details here](https://github.com/XD2333/GalTransl/tree/main/useful_tools/VNTextProxy)

**JIS replacement**: From AtomCrafty's [UniversalInjectorFramework](https://github.com/AtomCrafty/UniversalInjectorFramework#character-substitution) project, also uses DLL hijacking to HOOK the game, and can replace a character with another specified character according to settings, regardless of encoding. I built [a replacement dictionary](https://github.com/XD2333/GalTransl_DumpInjector/blob/main/hanzi2kanji_table.txt) that sorts out the mapping between simplified Chinese characters not supported in JIS encoding and Japanese characters supported in JIS according to some rules, which can meet 99.99% of common simplified Chinese characters' normal display (see hanzi2kanji_table.txt), and wrote the replacement function into the GalTransl extraction and injection tool (new: now [SExtractor](https://github.com/satan53x/SExtractor) also supports replacement and is easier to use). After replacement, combined with UniversalInjectorFramework's dynamic Hook replacement function, these Japanese characters are replaced back to simplified Chinese in the game, achieving normal display of the game.

When using SJIS replacement mode, you can first run the GalTransl extraction and injection tool's inject text once to get the list of characters not supported by the game (after injection it will prompt "sjis_ext.bin contains text: xxx"), then check "SJIS replacement mode injection", copy these characters into the text box on the right, and click inject. After injection you will get an SJIS replacement mode configuration.

Open the `useful_tools/UniversalInjectorFramework` folder, which also has many dlls, try them one by one, generally try winmm.dll first, copy the `uif_config.json` in the directory to the game directory, then edit this json, and fill in `source_characters` and `target_characters` according to the configuration provided by the GalTransl extraction and injection tool.
Then run the game, if the game runs normally, and a console like this pops up:
![img_terminal](./img/img_terminal.png)
It's mostly done. If not normal, delete this DLL and try the next one.
Note: UniversalInjectorFramework also supports SJIS tunnel mode, you can set `tunnel_decoder` to `True` and fill in the text contained in sjis_ext.bin in `mapping`.
Note: UniversalInjectorFramework's console window can be hidden, [see detailed configuration file settings here](https://github.com/XD2333/GalTransl/tree/main/useful_tools/UniversalInjectorFramework)

</details>

## GalTransl Core Features
Introduces GPT dictionary, cache, ordinary dictionary, problem finding and other functions.
(Click to expand detailed instructions)
<details>

<summary>

### GPT Dictionary
&ensp;&ensp;&ensp;&ensp;The GPT dictionary system is a key function to improve quality when using GalTransl for translation. It greatly improves translation quality by supplementing settings, and is the core that distinguishes GPT translation from traditional machine translation. Applicable to all OpenAI-compatible backends.
In the program directory, there is "通用GPT字典.txt" (General GPT Dictionary) in the `Dict` folder, and you can create "项目GPT字典.txt" (Project GPT Dictionary) in the project folder. Generally, name definitions are written into the project dictionary, and common words that improve translation quality are written into the general dictionary. You can edit the project/general GPT dictionary in the **GPT Dictionary** tab of the desktop app's "Dictionary Management" page.

</summary>

* For example, you can pre-define the Chinese translation of each character name here, and explain the character's setting, such as gender, approximate age, occupation, etc. By automatically feeding GPT these settings, you can automatically adjust the appropriate pronouns (he/she), titles, etc., and fix the translation of names when they are in kana.
* Also, you can supplement some words that GPT always translates incorrectly here. If you provide some explanation, it will understand better.

---

* Learn how to use GPT dictionary to feed character settings through the following example. The format of each line is `Japanese[Tab]Chinese[Tab]Explanation(optional)`, note the connector is **TAB**
```
フラン	Flan	name, lady, teacher
笠間	Kasama	笠間 陽菜乃's lastname, girl
陽菜乃	Hinano	笠間 陽菜乃's firstname, girl
张三	Zhang San	player's name, boy
$str20	$str20	player's codename, boy
```
These dictionary entries are all for defining characters:
* The first one can be understood as telling GPT: "The translation of フラン is Flan, this is a name, a lady, a teacher". This way GPT will translate フラン先生 as Flan teacher instead of Flan doctor.
* The second and third are the Japanese surname and given name of the same person. Tests show the name must be written in two lines, otherwise GPT-3.5 won't recognize it.
* The fourth is the recommended way to write the protagonist. **Note: even if Japanese and Chinese are the same, repeat them again**
* The fifth is the recommended way to write when the protagonist uses a placeholder instead of a name in the script.
* **Don't make the settings too complex**, otherwise GPT will have a lot of weird imagination.

---

* Learn how to use GPT dictionary to feed new words:
```
大家さん  landlord
あたし	I/ic	use 'ic' when being cute
```
* When you find GPT doesn't know this word well, e.g. "大家さん", and the meaning is relatively unique, you can add it to the general GPT dictionary like this; explanation is not necessary.
* The second line's Chinese writes a polysemous word "I/ic", and the explanation says "use 'ic' when being cute". GPT-3.5 is not that smart, but GPT-4 can basically use it flexibly.
* Want GPT to be more ecchi? Add dictionaries yourself ( ͡° ͜ʖ ͡°)

In the program directory, there is "通用GPT字典.txt" in the `Dict` folder, and "项目GPT字典.txt" in the `sampleProject` folder. Generally, name definitions are written into the project dictionary, and common words that improve translation quality are written into the general dictionary.
Only when the name and sentence sent to GPT this time contain this word, will this word's explanation be sent into this round of conversation.
**But don't add every word to it** — ~~adding everything will only hurt you~~, it is recommended to only write **the settings of each character** and **words that are always translated wrong**.

In addition, you can use the **GenDic** backend to let AI automatically generate a GPT dictionary from the source text, or use the **name replacement table** (CSV/XLSX) to centrally manage character name replacements.

</details>

<details>

<summary>

### Ordinary Dictionary
In GalTransl, the ordinary dictionary is divided into "pre-translation dictionary" and "post-translation dictionary" (corresponding to the **Pre-processing** and **Post-processing** tabs in the desktop app's "Dictionary Management" page). The pre-translation dictionary does a-to-b replacement of Japanese before translation, and the post-translation dictionary does a-to-b replacement of translated Chinese after translation.

</summary>

The pre-translation dictionary is mostly used for unclear speech correction, and if multiple words represent the same meaning, you can use the pre-translation dictionary to unify them first, reducing GPT dictionary input.

The post-translation dictionary is the more common one, doing a-to-b replacement of a word after translation. But here I improved a "conditional dictionary". The conditional dictionary actually adds a step of judgment before replacement, to avoid misreplacement and overreplacement.
Each line format is `pre_jp/post_jp[tab]judgment word[tab]search word[tab]replacement word`
* pre_jp/post_jp represents the position where the judgment word is searched, defined in the "Translation Cache" section
* Judgment word: If the judgment word is found in the search position (pre_jp/post_jp), the subsequent replacement will be activated.
* The judgment word can be prefixed with "!" to mean "replace if not present", otherwise it generally means "replace if present".
* The judgment word can use `[or]` or `[and]` keywords to connect. Multiple `[or]` connections mean "enter replacement if any condition is met", multiple `[and]` connections mean "enter replacement only if all conditions are met".
* Search word, replacement word: same as ordinary dictionary, replace a with b.
* The desktop app's "Dictionary Management" page's **Name Replacement** tab provides an independent name replacement table (CSV/XLSX) for batch maintenance of character names.

</details>

<details>

<summary>

### Translation Cache
After starting translation, you can find the translation cache files in the `transl_cache/pass3_cache` directory. In the desktop app, you can directly open the cache in the "Proofreading" page and modify it sentence by sentence.

</summary>

The translation cache corresponds to the source JSON in `gt_input` one by one. During translation, the translation result is preferentially written into the cache. When a file is translated, it appears in `gt_output` (result JSON).

First, a summary of key points:
1. When you want to re-translate a sentence, open the corresponding translation cache file and delete the whole line of `pre_zh` for that sentence (**do not leave a blank line**)
2. When you want to re-translate a whole paragraph, just delete the corresponding number of object blocks. When you want to re-translate a file, just delete the corresponding translation cache file.
3. When GalTransl is translating, do not modify the cache of the file being translated. It will be overwritten anyway.
4. `gt_output` result file = `pre_zh`/`proofread_zh` in the translation cache + post-translation dictionary replacement + restore dialogue box
5. When the new `post_jp` is inconsistent with the `post_jp` in the cache, it will trigger re-translation, which generally happens when a new pre-translation dictionary is added

A typical translation cache sample:
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
Explanation of each field:
* Basic parameters:
`index` — serial number
`name` — character name
`pre_jp` — original Japanese (`pre_src` compatible alias)
`post_jp` — processed Japanese (`post_src`). Generally, post_jp = pre_jp with dialogue box removed + pre-translation dictionary replacement. You can add your own processing here if you code.
`pre_zh` — original Chinese (`pre_dst` compatible alias)
`proofread_zh` — proofread Chinese (`proofread_dst`)
(no `post_zh`; `post_zh` is in the result folder)
`trans_by` — translation engine/translator
`proofread_by` — proofreading engine/proofreader
`problem` — stored problems. See Automatic Error Finding below.
`post_zh_preview` — for previewing `gt_output`, but **modifying it will not apply to output**; modify `pre_zh`/`proofread_zh`

* **Recommended to use the desktop app to fix the cache**: In the "Proofreading" page, directly open the cache file and modify translations sentence by sentence, supporting undo/redo, problem filtering, alternative translation swap and "Save and re-check problems". After fixing, click "Build Output" to generate a new result JSON.

* Command-line users can also use **EmEditor** to fix the cache: select a file, right-click - Open with EmEditor, then drag all files in `transl_cache` into it.
At this time the tabs may take up a lot of space. Right-click the tab - Customize tab, change "When tabs don't fit" to "None", so the tabs will only be on one line (EmEditor Professional required).
Then ctrl+f to search, search for keywords you are interested in (such as problem, doub_content), check "Search all documents in group", to quickly search in all files, or click extract to quickly preview all problems.

* **VSCode** is also a very good cache-fixing tool. Just open the cache folder with VSCode, then globally search for e.g. problem, to quickly locate all problems.

* After determining the content that needs to be modified, directly modify the `pre_zh` or `proofread_zh` of the corresponding sentence, then **rerun GalTransl** to quickly generate a new result JSON.

</details>

<details>

<summary>

### Automatic Error Finding

GalTransl has built a rule-based system for automatically finding problems based on long-term observation of translation results.

</summary>

The error finding system is enabled in each project's `config.yaml`. The default configuration is:

```yaml
# Automatic problem analysis config, add # before - to disable
problemAnalyze:
  problemList: # Problem checklist
    - 词频过高 # Repeated more than 20 times
    - 标点错漏 # Punctuation added or missing
    - 残留日文 # Japanese hiragana/katakana remaining
    - 丢失换行 # Missing line breaks, usually fine; combined with the long-sentence-missing-line-break check to ensure actual reading experience
    - 多加换行 # More line breaks than original, may cause screen overflow
    - 比日文长 # 1.3x longer than Japanese
    - 字典使用 # Not following GPT dictionary requirements
    - 语言不通 # Suspected not translated to target language; when translating to Chinese, checks for non-GBK characters
    - 缺控制符 # Detects lost ruby or other control characters in translation
    - 独白男他 # "他" appears in monologue (no name), excluding "其他/他们/他人/他乡/他国/他日/他山"
    #- 引入英文 # Originally no English, translation introduced English
    #- 比日文长严格 # Strict check, cannot be longer than Japanese
    #- 长句丢失换行 # Average sentence length exceeds the configured threshold, suspected missing line break
    #- 换行位置异常 # Line break not following a Chinese punctuation mark (comma/period etc.)
    - 疑似错误 # AI semantic detection: extreme semantic divergence (mistranslation/omission/line-merging), flagged by ForSemCheck via suspected_error
  avgSentenceLengthThreshold: 17 # Sentence length threshold for "long sentence missing line break", default 17, suggested 15~25
  avgSentenceLengthThresholdH: 24 # H-scene specific threshold, default 24, suggested 20~30
```

Currently supports finding the above problems. Some items are commented out with #, you can uncomment to enable, or add # to disable the corresponding problem search. There are also **attributive/adverbial overlong** (detects overlong modifiers like "XX的" and "在XX") and **inappropriate wording** (H-scene vs non-H-scene flagged by forbidden-word/word-list hits) checks that can be enabled via configuration.

After finding problems, they are stored in the translation cache. See the Translation Cache section. In the desktop app, you can use the **Problem Detection** sidebar in the "Proofreading" page to batch view all problems, and fix them by modifying the cache.

(New) You can now also configure `retranslKey` in config.yaml to batch re-translate a specific problem, e.g. `retranslKey: "残留日文"`

</details>

## Configuration and Engine Settings

The desktop app manages translation backend configuration through the graphical interface (the "Backend Configuration" page), no manual YAML editing required. Two types of configuration profiles are supported:

* **OpenAI-Compatible**: General OpenAI-compatible interface, can configure multiple tokens, each token contains `endpoint`, `modelName`, `stream` and other parameters, adapting to DeepSeek, OpenRouter, SiliconFlow and other forwarding services.
* **SakuraLLM**: Local/remote Sakura model, can configure multiple endpoints.

You can set a profile as "Default", and select it directly in the "Translation Console" when translating. Project-level configuration can be modified in the "Project Configuration" page.

For the command-line version, detailed settings can be found directly in the `config.yaml` configuration file comments, which are now quite comprehensive.
