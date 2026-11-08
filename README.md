# 课堂笔记自动整理

## 中文与简体转写修复（2026-11-08）

- 本地视频、实时录课及批量视频导入（如当前版本提供）默认按**中文**转写，并明确使用原文转写任务，不将中文翻译成英文。旧设置未指定转写语言时也默认使用中文。
- 主界面新增“转写语言”：中文（默认）、英文、自动识别。英文课程可选英文；确需多语言识别时可选自动识别，各音频片段独立检测，不沿用首段误判的语言。
- 所有新写入转写稿的汉字通过 OpenCC 统一为**简体中文**，保留英文术语、数字、公式和时间标记。此转换处理简繁字形，不会把已识别为英文的句子翻译回中文。
- 首次更新后请关闭旧窗口，再双击启动器；启动器会安装新增的简繁转换依赖。已保存的错误转写不会自动重写：请重新导入原视频，或重新录制后生成笔记。

## 项目简介 / Project Overview

**中文**

课堂笔记自动整理是一款面向 Windows 的 AI 辅助笔记工具，可将本地课程视频或录课时采集的画面与系统音频整理为按知识主题组织的学习笔记。程序在本机转写讲解，并结合截图生成便于理解、复习与自测的内容。支持视频片段选择与画面框选、多种 AI 服务、生成检查点恢复，以及按本机时区或 GMT 固定时差设置 AI 提交时段；可导出 Word、PDF、LaTeX 和 Markdown。

**English**

Lesson Notes Automation is an AI-assisted note-taking tool for Windows that turns local lecture videos or captured screen visuals and system audio into topic-organized study notes. It transcribes speech locally and combines transcripts with screenshots to support understanding, review, and self-assessment. Features include video time-range and region selection, multiple AI providers, checkpoint-based generation recovery, and request scheduling using local time or fixed GMT offsets. Notes can be exported to Word, PDF, LaTeX, and Markdown.

## 2026-11-07 更新

- 视频支持指定起始与终止时间，只用此时间段内的截图和音轨生成笔记；“仅转写”也可选择时间段。窗口标题带“视频片段版”。
- 本地视频支持预览后拖动框选截图区域；可指定秒数或 `HH:MM:SS` 查看另一帧再框选，也可选择完整画面。选区仅用于本次导入，换视频时重新确认。
- AI 提交时段增加时区选择：本机时间、常用 GMT 时差及自填 `GMT+8`、`GMT-5`、`GMT+05:30` 等。支持分钟级偏移（绝对值小于 24 小时）；GMT 为固定时差，不随夏令时切换。
- 旧配置默认沿用本机时间。限额重试和跨午夜等待按所选时区计算，恢复记录时保留已保存延后时间代表的实际时刻。
- 更新后关闭旧版窗口，再双击原桌面快捷方式；窗口标题带 `2026.11.07` 即为新版。

## 重要声明：仅供学习使用

本软件仅供学习和个人研究使用。**禁止使用本软件扒取、录制、转写、复制或传播未取得授权的受版权保护视频、课程资料、录音或讲义。** 使用者须自行确认并取得必要授权，并遵守内容平台条款及适用法律。

因使用者未获授权处理或传播受版权保护内容而引发的版权纠纷、索赔、平台处罚或其他损失，由使用者自行承担；软件作者/提供者对该等纠纷和损失概不负责，但适用法律不允许排除的责任除外。本声明不授予任何第三方作品的使用权，也不构成法律意见。

## 软件入口与界面

在 Windows 资源管理器中双击 **`启动课堂笔记.bat`**，即可打开桌面配置界面。主界面可选择 ChatGPT 登录授权、OpenAI API Key、DeepSeek、豆包或兼容 API，并设置素材目录、笔记文件名、输出格式、目标显示器、PPT 截图范围、截图模式、定时间隔、结束静止时间以及 Clash Verge 路径和端口。

界面会启用 Windows DPI 感知并使用较大的默认字体，以适配高分辨率和系统缩放。Clash Verge 会先在常见安装位置和 Windows 应用注册表中自动检测；也可点“自动检测”重新查找。旧版文件选择器只筛选 `.exe`，因此桌面快捷方式（`.lnk`）不会显示。新版“选择…”支持 `.exe` 与 `.lnk`，可直接选桌面上的 Clash Verge 快捷方式。

`lesson_notes_automation.py` 是应用主程序；`requirements.txt` 是运行依赖清单。首次双击启动器时，它会在软件目录下创建私有 `.venv`，并自动安装清单里的依赖；这一步需要互联网，可能需要几分钟。安装过程会显示在控制台窗口中。安装失败时窗口会提示原因，修复网络后重新双击即可继续。

```powershell
cd <本目录>
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

完成安装后再次双击启动入口。或者在已激活的环境中运行 `python .\lesson_notes_automation.py`。请使用启动器创建的 `.venv` 运行，避免系统 Python 和软件依赖不一致。

需要 Windows 10/11 和 Python 3.10 或更新版本。首次加载本地 Whisper `base` 模型时需要联网下载模型文件；具体耗时取决于网络和电脑性能。

## 功能流程

### 从本地视频生成完整笔记

填写并保存 AI 平台、模型、素材/笔记目录和输出格式，然后点击 **“导入视频并生成…”**，选择 MP4、MKV、MOV、AVI、WMV、WebM 等本地视频文件。程序直接解码文件，不需要打开播放器、录制屏幕或采集扬声器声音。视频处理在后台进行，界面会显示视频时间进度，可暂停、继续或取消导入。

素材模式沿用“仅截图”“仅转写”“转写＋截图”：画面按所选的“画面变化时”或“定时截图”提取，定时模式沿用截图间隔和“跳过重复画面”；音轨在本机通过 Whisper 转写，并在原文中标记原视频时间。音频按 30 秒分段处理，避免长视频的全部音频同时占用内存。选择文件后，视频导入窗口提供“起始时间”和“终止时间”，支持秒数、`MM:SS`、`HH:MM:SS` 和小数秒。例如起始 `00:10:00`、终止 `00:25:00`，只整理第 10 至 25 分钟；起始默认 0，终止留空表示直到视频结束。开始包含在内、结束不包含在内，且须满足起始早于终止、时间不超过视频时长。截图和送往 Whisper 的音频均仅取此范围；生成后的截图索引及转写时间仍对应原视频，不从 0 重新计时。

含截图的模式可继续拖动鼠标框选，再点“使用框选区域并导入”；也可点“使用完整画面并导入”。“完整画面”指不裁剪图像空间范围，仍然遵守填写的起止时间。仅转写模式填写时间后点击“导入此时间段”。开头为黑屏时，可填写另一预览时间并点“查看此帧”，选区保持不变；“预览时间”仅用于查看，导入范围由单独的起始与终止时间决定。

截图先按原视频像素裁剪，再按比例缩放至不超过 1920×1080；画面变化检测和重复画面判断也仅比较选区内的内容，选区外的人像、字幕或播放器变化不会触发截图。图像框选不影响音轨内容，转写使用所选时间段的完整音轨。桌面显示器选区与视频选区互不影响。若视频中途改变分辨率，区域截图将报错并保留已提取素材，避免错误裁剪；可重新导入并使用完整画面。编码是否可读取以本机 PyAV 解码结果为准；不处理加密视频。

素材准备完成后，自动进入原有截图检查和笔记生成流程：检查或删除图片并确认开始生成，随后按所选整章/分组模式调用 AI，导出 Word、PDF、LaTeX、Markdown 或自定义文本格式。仅转写模式跳过图片检查。AI 请求失败后可以直接使用已保存素材和生成检查点重试，不需要重复导入视频。原视频不移动、不修改，也不直接上传；生成时发送选定的截图和转写。首次加载 Whisper 模型可能需要下载。

无音轨视频在“转写＋截图”模式下使用截图生成；“仅转写”模式会提示改用其他模式。导入取消、失败或程序意外关闭后，已完成的素材会保留，可选择该记录并检查后生成；**视频导入中断不续接解码位置**，需要完整材料时请重新导入原视频，新的记录不会覆盖旧记录。暂停/取消会在解码和转写的检查点响应；模型加载或正在进行的本地推理可能需要等待。

视频记录额外保存 `video_frames.json`（图片与原视频时间对应关系）及 `session_state.json` 中的 `video_source`（来源、时长、音轨情况、处理完成标记、原视频尺寸 `frame_size`、选区 `capture_region`、时间段 `start_seconds`/`end_seconds` 及 `selected_duration_seconds`；完整画面时选区为 null，直到视频结束时终止时间为 null）。导入使用 PyAV 自带的解码库，无需另装系统 FFmpeg。原视频只读取，不会裁切或覆盖。

1. 选择显示器后，可勾选“只截取 PPT 区域”，在开始录课前拖动鼠标框选完整 PPT 页面。之后保存的是裁剪后的 PPT 图片；取消勾选则监测整台所选显示器。切换目标显示器后需要重新框选。
2. 截图可选“画面变化时”或“定时截图”。定时模式可设置 1–3600 秒；“跳过重复画面”默认开启，相同页面不会反复写入文件。变化模式会等待画面短暂稳定，再保存变化后的页面。
3. 通过 Windows 默认扬声器的系统回环采集音频，在本机使用 Whisper 每约 20 秒转写一段，并追加到 `transcript.txt`。录制时会显示一个可移动的小控制窗；点击“暂停录课”会同时暂停截图和音频采集，点击“继续录课”恢复。画面与系统音频达到“静止自动暂停秒数”后会自动暂停，素材保留在当前课程中，不会自动结束录课；需要继续时点击“继续录课”。暂停时会先完成暂停前已经缓存的转写片段。
4. 点击“结束录课”后，素材保存为本次会话目录下的 `screenshots`、`transcript.txt` 和 `session_state.json`。这一步不会立即向模型发送内容；你可以检查或修改 AI 平台、模型、推理强度、笔记名和输出格式。点击“保存设置并生成 / 重试”后会先打开截图预览窗口，你可以分页检查并删除冗余图片，再确认开始生成。也可点击“合并多份记录并生成…”选择两份或更多课程记录，程序会保留原记录、创建合并副本，并用合并后的转写和截图生成一份笔记。
5. 对 ChatGPT 登录授权、OpenAI API/自定义 API，可选择启动 Clash Verge；所选本地 HTTP/mixed-port 代理只用于课堂笔记自身的请求，不修改 Windows 系统代理设置。DeepSeek 和豆包自动跳过此步骤。
6. 模型每成功分析一组（最多 6 张）截图后，结果写入 `generation_checkpoint.json`。生成期间可点“暂停后续提交”：当前正在处理的模型请求会先完成并写入检查点，然后暂停后续请求；点“继续生成”即可从检查点接着处理。需要立即停止时，点“终止 AI 生成”会关闭本机当前 AI 请求并停止后续提交；已完成的截图组检查点会留在课程记录中；如果删改图片，重试时会按新的图片集合重新分析。已经送达 AI 平台的数据无法撤回。网络中断或响应提前结束后，修复连接、按需修改并保存设置，再点“保存设置并生成 / 重试”；已完成的截图组会复用，失败的当前组会重新请求。最终笔记内容也会保存为检查点，若只是本地导出失败，无需重复请求模型。

### 设置 AI 整理请求的提交时段

在主界面“AI 提交时间”中勾选“仅在每天此时段提交”，填写 24 小时制的开始、结束时间（如 `01:00` 至 `07:00`），然后保存设置并点击“保存设置并生成 / 重试”，完成截图确认。程序将排队等待，在允许时段自动继续。**只保存设置不会自动创建生成任务。** 默认关闭限制，旧配置仍可立即生成。

- 时间按“时区”所选的本机时间或 GMT 固定时差每天重复。下拉框可以直接输入，例如 `GMT+8`、`GMT-5`、`GMT+05:45`。开始时刻包含在内，结束时刻不包含在内。支持 `23:00` 至 `03:00` 这样的跨午夜时段；开始、结束不能相同。需要全天提交时请取消勾选。GMT 偏移不跟随任何城市的夏令时变化；本机时间跟随电脑时区。
- 每次截图组分析和最终整理在发送前都会检查时间。时段结束后不再发起新请求；已经发送的请求可以继续完成并保存检查点。已完整生成、只需本地导出的内容不必等待时段。
- 可勾选“遇到用量/速率限制时，延后到下一时段重试”。HTTP 429 及可识别的额度限制响应会延后到**下一次开始时刻**；平台提供更晚的 `Retry-After` 时会继续等待，并按允许时段提交。每次启动生成最多自动重试 3 次，仍失败则保留素材和检查点并显示错误；其他错误直接报告。不会在当前时段连续重试。该功能不能保证额度届时恢复，也不会切换账号、模型或付费方案。
- 等待期间可以“暂停后续提交”“继续生成”或“终止 AI 生成”。暂停不会解除时间限制；终止保留素材及已完成的检查点。生成期间设置固定，修改时段请先终止，再保存设置并重试。
- **必须保持软件运行、电脑唤醒并联网。** 本功能不设置 Windows 唤醒任务，也不会在关闭软件后发送请求。软件意外退出后，重新选择未完成记录并点击生成 / 重试；已保存的检查点会复用。若此前因限额延后且仍开启限额重试，会保留不早于该时间的约束。

全局设置存于 `%APPDATA%\LessonNotesAutomation\settings.json` 的 `ai_schedule_enabled`、`ai_schedule_start`、`ai_schedule_end`、`ai_schedule_timezone`、`ai_retry_limits` 字段。课程的 `session_state.json` 记录 `ai_schedule`（含 timezone）、带时区偏移的 `generation_not_before`、`limit_retries` 和 `generation_waiting` 状态；不会保存密钥或 OAuth 令牌。旧版未带偏移的延后时间按本机时间读取；更换时区不会改变已有延后时间对应的实际时刻。时间设置不影响生成检查点的内容指纹，调整时段无需重做已经完成的截图分析。

更新文件后，请关闭旧版窗口并重新双击启动入口，新界面才会显示“AI 提交时间”。默认不启用时间限制，需要用户自行勾选并保存。

2026-10-26 验证：提交时段、跨午夜、暂停/终止、限额延后、凭据刷新、界面配置、检查点恢复及 LaTeX 公式共 13 项回归测试通过；真实课程的 13 张截图和转写在等待测试时段后成功生成 PDF 与 LaTeX，原素材保持不变。同时修复了中文 Windows 下编译日志的解码异常，并支持 LaTeX 导出中的常用行内/独立公式及对齐公式；不支持的 LaTeX 命令按普通文字转义。

### 恢复未完成记录

软件启动时会检查素材目录中的未完成课程记录，并在界面提示最近一条。录制时应用意外关闭，可以选择该记录后点“继续未完成录课”；此前保存的截图和转写原文会保留并接着追加。录制已结束或模型请求失败时，可选中记录并重新生成。也可点“选择已有记录…”手动载入 `课程记录_时间戳` 文件夹，包括旧版保存的素材；点“合并多份记录并生成…”可多选记录并生成一份合并笔记。

断点以已完成的模型请求为边界；如果网络恰好在当前截图组的响应中断，该组需要重新请求，因为服务端没有提供可恢复的部分响应。已完成的前序截图组不会从头分析。生成暂停或由用户终止的状态也会记录在 `session_state.json`；若应用意外关闭，重新载入该课程记录并点击生成 / 重试即可继续使用检查点。

### Sign in with ChatGPT

如果希望使用 ChatGPT 账号授权而不是 OpenAI API Key：

1. 在“AI 平台”选择 **ChatGPT 登录授权（Sign in with ChatGPT）**。
2. 按 **Continue with ChatGPT**。软件会打开系统浏览器并启动本机回调；请在浏览器中登录、检查权限并自行批准。
3. 授权完成后，软件会读取此账号可用模型目录。选定模型和推理强度后，再保存设置并开始。

**多个账号 / 工作区：** 首次使用另一个账号或工作区时，点击 **“添加账号 / 工作区…”**，在浏览器中选择目标账号和工作区并授权。授权完成后可在连接下拉框中切换；点“重命名…”可将连接标为“个人”“学校”“公司”等。每个连接分别保存客户端 ID 和加密令牌，即使邮箱相同也不会混用。旧版的单一授权会自动保留为第一个连接，新增取消或失败不会删除原连接；本机标识保持不变。

如果看到“此应用仅对某工作区成员可用”，说明正在用已有连接登录不同工作区。OpenAI 将已签发的客户端 ID 绑定到注册时的账号和工作区，重新登录该连接不会更换工作区。回到软件，先点“取消登录”，再点“添加账号 / 工作区…”。以后切换使用下拉框，无需重复注册。每个工作区仍需正常授权并满足 OpenAI 的资格和管理员设置。模型列表读取失败时，授权仍会保留，可单独点击“刷新模型”重试；生成期间需先结束当前生成再切换连接。

该功能使用 OpenAI 面向符合条件的开源/本地客户端提供的 OAuth 动态注册流程与 Responses API。它**不会读取或复用电脑上 ChatGPT 桌面客户端的登录状态，也不能访问 ChatGPT 对话历史**。OAuth 令牌使用 Windows 当前用户的 DPAPI 加密后保存在 `%LOCALAPPDATA%\LessonNotesAutomation\siwc-credentials.bin`；旧版本保存在 Windows 凭据库中的令牌会在首次读取时迁移。API Key 模式与此授权彼此独立。模型目录按当前授权账号实时读取，并完整显示接口返回且带模型 ID 的条目；非 `list` 项会附带接口标记，但不保证该 ChatGPT 授权可调用。模型框也允许直接输入模型 ID（例如 `gpt-6.1-sol`、`gpt-6-luna`），但具体账号是否能通过 ChatGPT 方案调用仍由授权端校验。该目录不保证与 ChatGPT 客户端内的模型选择器相同。推理强度选项会随已知模型调整。ChatGPT 方案的可用资格、用量限制及授权范围由 OpenAI 和账号决定；若账号未提供所需权限，仍可选择 API Key 模式。ChatGPT Plus 的应用用量可能与其他使用 ChatGPT 方案的应用共享。

如果授权后出现 Windows `CredWrite`/`1783` 错误，请使用新版启动器重试。新版将 OAuth 令牌保存到 DPAPI 加密文件，不再把长令牌组写入 Windows Credential Manager。

选择该授权方式后，如果所在网络不能直接访问 OpenAI，可勾选 Clash Verge 代理并填写程序路径与本地 HTTP/mixed-port。连接、刷新模型及生成笔记时会使用该代理；若端口已在运行则直接复用，否则启动 Clash Verge。程序不修改或恢复 Windows 系统代理设置。尚未完成 ChatGPT 授权时，“刷新模型”会提示先授权，不会启动 Clash Verge。DeepSeek 和豆包仍会跳过 Clash Verge。

OpenAI 官方说明：[Sign in with ChatGPT Quickstart](https://developers.openai.com/siwc/quickstart)、[本地客户端注册和登录](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)、[模型和 Responses API](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)、[令牌管理](https://developers.openai.com/siwc/token-sharing-open-source/token-reference)。

其他 API 平台采用 OpenAI Chat Completions 兼容格式并要求模型支持图像输入。请在界面填写平台控制台提供的准确 API 地址、模型标识和 API Key。豆包的 API 地址和模型/Endpoint ID 以火山方舟控制台为准。运行阶段的截图和转写文本会发送至你选择的 AI 服务。

选择“其他（自定义）”后，可填写例如 `HTML (.html)`、`JSON (.json)` 或 `CSV (.csv)`。自定义要求会一并写入发送给 AI 的最终整理提示，并按填写的类型保存模型文本结果。当前自定义模式用于文本类格式；Word/PDF 请选内置格式，PPTX/XLSX 等二进制格式暂不支持自定义生成。

静止结束检测属于启发式判断；静态课件但讲解仍在继续时，音频活动会阻止自动结束。定时截图模式按设置的间隔取样；启用“跳过重复画面”时只有与上次保存内容明显不同的页面会写入磁盘。转写处理较慢时，音频片段会排队。LaTeX PDF 格式始终保存 `.tex`；若本机没有 `xelatex`/`pdflatex`，会提示并保留源文件。

## GitHub 开源组件与许可说明

本项目的界面和工作流由本项目源码实现。以下是通过 Python 包管理器安装的直接运行依赖；它们是库，不是复制进本项目的插件代码。仓库链接和许可类型用于署名与合规核对，分发时应保留各上游许可及版权声明：

| 组件 | 用途 | 上游仓库 | 上游许可证 |
|---|---|---|---|
| NumPy | 音频波形处理 | [numpy/numpy](https://github.com/numpy/numpy) | [BSD-3-Clause](https://github.com/numpy/numpy/blob/main/LICENSE.txt)（仓库亦声明含若干附加组件许可） |
| Pillow | 截图图像处理 | [python-pillow/Pillow](https://github.com/python-pillow/Pillow) | [MIT-CMU](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| MSS | 屏幕截图与显示器枚举 | [BoboTiG/python-mss](https://github.com/BoboTiG/python-mss) | [MIT](https://github.com/BoboTiG/python-mss/blob/main/LICENSE.txt) |
| SoundCard | 系统回环录音 | [bastibe/SoundCard](https://github.com/bastibe/SoundCard) | [BSD-3-Clause](https://github.com/bastibe/SoundCard) |
| faster-whisper | 本机语音转写 | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | [MIT](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE) |
| opencc-python-reimplemented | 转写文本的繁体转简体；在本机转换，不调用 AI 翻译 | [yichen0831/opencc-python](https://github.com/yichen0831/opencc-python) | [Apache-2.0](https://github.com/yichen0831/opencc-python/blob/master/LICENSE.txt) |
| PyAV | 本地视频和音轨解码 | [PyAV-Org/PyAV](https://github.com/PyAV-Org/PyAV) | [BSD-3-Clause](https://github.com/PyAV-Org/PyAV/blob/main/LICENSE.txt)；其二进制包所含解码库有各自许可 |
| Requests | AI API 网络请求 | [psf/requests](https://github.com/psf/requests) | [Apache-2.0](https://github.com/psf/requests/blob/main/LICENSE) |
| keyring | 保存 API Key；OAuth 令牌使用 Windows DPAPI 加密文件 | [jaraco/keyring](https://github.com/jaraco/keyring) | [MIT](https://github.com/jaraco/keyring/blob/main/pyproject.toml) |
| PyJWT + cryptography extras | 验证 Sign in with ChatGPT 的 OIDC ID Token 签名与声明 | [jpadilla/pyjwt](https://github.com/jpadilla/pyjwt) | [MIT](https://github.com/jpadilla/pyjwt/blob/master/LICENSE) |
| python-docx | Word 文件生成 | [python-openxml/python-docx](https://github.com/python-openxml/python-docx) | [MIT](https://github.com/python-openxml/python-docx/blob/master/LICENSE) |
| ReportLab | PDF 文件生成 | [reportlab](https://www.reportlab.com/dev/opensource/) | BSD |

**可选外部程序：** Clash Verge Rev 由用户自行安装，本软件不包含或再分发它；其上游仓库声明 GPL-3.0：[Clash-Verge-rev/clash-verge-rev](https://github.com/Clash-Verge-rev/clash-verge-rev)。本软件仅在用户启用代理时启动用户指定的安装程序。

Python 包可能引入传递依赖和模型权重。上表不是完整 SBOM；在重新分发安装包前，应针对实际安装版本生成完整依赖/许可证清单，并逐项核对其许可证和模型卡。列出开源组件并不代表获得课程视频、PPT、录音或第三方服务内容的版权许可。

## 文件结构

- `启动课堂笔记.bat`：Windows 双击启动入口
- `lesson_notes_automation.py`：桌面应用源码
- `ai_schedule.py`：每天 AI 请求提交时段、跨午夜计算及限额延后规则
- `video_import.py`：本地视频画面提取、音轨分段转写及视频时间索引
- `video_region.py`：本地视频预览、跳转指定时间及原视频像素区域框选
- `siwc_auth.py`：Sign in with ChatGPT 的本机 OAuth/PKCE 回调和令牌管理
- `requirements.txt`：Python 运行依赖
- `README.md`：安装、使用与版权/第三方组件说明
- `tests/test_ai_schedule.py`：提交时段、限额延后与断点续做回归测试；在软件目录运行 `.venv\Scripts\python.exe -m unittest discover -s tests -v`
- 每次录课生成的 `session_state.json`：录制状态和恢复信息，不包含 API Key 或 OAuth 令牌
- 生成笔记时生成的 `generation_checkpoint.json`：已完成的截图组分析及最终模型文本，用于网络中断后重试


## 学习笔记生成风格

笔记以知识主题为主线整合截图与讲解，用连贯段落讲清概念及其理由，并根据内容需要加入对比表格、案例分析、概念辨析、学习要点、复习提纲和自测题。保留核心细节，合并重复内容，减少零碎标题，便于系统理解与复习。内容以所提供的资料为依据，不补造资料外的事实。


## 生成模式

默认“整章一次生成”：截图筛选后，将全部保留截图（压缩后）与完整转写放在同一次请求里，直接按学习笔记提示词生成。可在输出格式旁切换“分组分析后合成”，每组六张先分析再合成。超过60张截图、原始图片40 MB或转写10万字符时，会提示选择分组、继续一次生成或返回筛选；这些为本地提示阈值，并非模型硬限制。两种模式均支持终止请求，切换模式会使旧生成缓存失效。


## 课程笔记文件夹命名

新录课及合并记录使用界面填写的笔记文件名作为文件夹名。同名目录存在时增加（2）、（3）等序号，避免覆盖。生成前修改笔记名称时，成功生成后会同步改名该记录目录。旧的课程记录目录仍可恢复和合并。


## 素材模式

笔记文件名旁可选“仅截图”“仅转写”“转写＋截图”（默认）。录制时只启动所选素材的采集，生成时只发送所选素材。仅转写跳过截图预览；仅截图不采集或发送音频转写。可以重新选择已有记录的生成素材范围，不删除原始素材；选中范围无内容时提示错误。素材模式与整章/分组生成模式分别选择。
