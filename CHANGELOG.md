# 版本记录

这里记录 PaperVault 的用户可见变化。最新候选版为 [v1.2.0-rc.2](https://github.com/skywuxuan/PaperVault/releases/tag/v1.2.0-rc.2)，Windows 与 Apple Silicon Mac 下载位于同一发行页；Windows 稳定版为 [v1.1.0](https://github.com/skywuxuan/PaperVault/releases/tag/v1.1.0)。安装和升级方式见[桌面版指南](docs/DESKTOP.md)。

## [Unreleased]

- 明确中文翻译的 JSON 输出格式，校验空译文与异常段落；翻译失败时保留英文报告和已有中文内容。
- 中文栏区分未生成、翻译中与失败状态，并提供重试和模型设置入口；网络中断后可刷新服务端状态，不再一直显示误导的“生成中”。
- 翻译错误提示区分模型权限、模型或地址不存在、请求格式不兼容等原因，避免仅显示笼统的生成失败。
- Windows 桌面启动前检查关键 .NET DLL 的下载阻止标记；发现阻止时显示中文处理步骤，不再进入 Python.NET 的难读异常。程序不会自动解除文件锁定。
- 桌面指南补充 `Python.Runtime.Loader.Initialize` 错误的排查方法：解除可信 ZIP 的锁定后重新完整解压。

## [1.2.0-rc.2] - 2026-09-16

本次候选版精简论文库与阅读器，修正解析来源按钮和英文摘要查词交互。Windows 桌面版、Apple Silicon Mac 桌面版与同版本 Web 服务共用前端和后端代码。

### 下载与使用

| 平台 | 下载 | 打开方式 |
| --- | --- | --- |
| Windows 10/11 x64 | [PaperVault-v1.2.0-rc.2-windows-x64.zip](https://github.com/skywuxuan/PaperVault/releases/download/v1.2.0-rc.2/PaperVault-v1.2.0-rc.2-windows-x64.zip) | 完整解压后运行 `PaperVault\PaperVault.exe` |
| macOS 14+ · Apple Silicon | [PaperVault-v1.2.0-rc.2-macos-arm64.zip](https://github.com/skywuxuan/PaperVault/releases/download/v1.2.0-rc.2/PaperVault-v1.2.0-rc.2-macos-arm64.zip) | 解压后将 `PaperVault.app` 拖入“应用程序” |

- 两个平台的 ZIP 与 SHA-256 校验文件位于本次 Release 的 Assets 中。
- **Windows 为便携包**：保留 EXE 旁的 `_internal` 等全部文件，不要直接在压缩包中运行。无需安装 Python，但需要系统具备 Microsoft Edge WebView2 Runtime。程序不会自动创建快捷方式或出现在“已安装的应用”中；可手动为 `PaperVault.exe` 创建桌面快捷方式。
- **macOS 包使用 ad hoc 签名，尚未进行 Developer ID 签名和 Apple 公证**；首次打开可能需要按系统提示允许打开。仅提供 Apple Silicon arm64 包。
- 升级前导出 `.pvault` 备份并关闭旧版，再解压或替换程序。Windows 升级到新目录后请更新快捷方式。默认数据目录仍为 `%LOCALAPPDATA%\PaperVault`（Windows）与 `~/Library/Application Support/PaperVault`（Mac）。
- Windows 卸载时删除程序文件夹与快捷方式即可；Mac 删除 `PaperVault.app` 即可。保留数据目录以保留论文库。

### 界面与交互

- 论文库改为紧凑布局，作者过多时单行省略，避免作者数量撑高论文项；标题最多两行。
- “摘要预览”缩为作者同行的小按钮，展开或收起时按钮保持原位，内容在下方显示。
- 阅读器有默认摘要时显示“默认解析”，没有时显示“生成摘要”；有豆包内容时显示“豆包解析”，没有时显示“导入豆包”。已有内容可直接点击对应按钮切换，来源名称保持固定，并高亮当前来源。
- 移除阅读器中重复的来源切换、模型标记、“双语阅读”“阅读器就绪”和“段落已对齐”等提示，减少工具栏占用。
- 英文摘要单击只选择并对齐段落，双击单词才打开词典；默认解析与豆包解析使用相同交互。
- 移除论文库中重复的“标签管理”入口，保留“管理标签”，将侧栏“按主题整理”改为“标签类别”。

### 构建与文档

- 统一从同一个 Git 提交构建 Windows x64 与 macOS arm64，校验包内前端与源码内容一致，并将两平台附件放入同一份 Release 草稿，核对后公开发布。
- 更新 README 与桌面指南，直接提供本版双平台下载入口，补充便携版再次打开、创建快捷方式、升级与卸载保留资料的说明。

## [1.2.0-rc.1] - 2026-09-15

本次候选版重新整理了论文库与阅读器的界面，重点改善笔记保存、摘要任务恢复和资料迁移的可靠性，并加入 Apple Silicon Mac 桌面预览构建。Windows 与 macOS 包保留在 [v1.2.0-rc.1 历史发行页](https://github.com/skywuxuan/PaperVault/releases/tag/v1.2.0-rc.1)。

### 试用与数据保护

- Windows x64 预览包命名为 `PaperVault-v1.2.0-rc.1-windows-x64.zip`。完整解压后运行 `PaperVault\PaperVault.exe`；不要直接从压缩包中运行，也不要只复制 EXE。
- Apple Silicon Mac 预览包命名为 `PaperVault-v1.2.0-rc.1-macos-arm64.zip`，解压后将完整的 `PaperVault.app` 放入“应用程序”。[构建运行记录](https://github.com/skywuxuan/PaperVault/actions/runs/34952729621)已验证 arm64、macOS 14.0 最低版本声明和 ad hoc 签名；未完成 Developer ID 签名和 Apple 公证。
- 建议通过 `--data-dir` 指定独立试用目录，并导入旧版导出的 `.pvault` 资源包。例如在解压目录中运行 `PaperVault\PaperVault.exe --data-dir "D:\PaperVault-Preview"`。
- 需要使用原论文库时，先用旧版导出备份，再关闭旧版并启动候选版。两个版本不要同时操作同一数据目录。
- 回退时使用旧版对应的备份；不要让旧版直接读取候选版已修改的数据。

### 新增

- 提供 Apple Silicon macOS `.app` 预览构建，使用系统 WKWebView，默认把论文库保存在 `~/Library/Application Support/PaperVault`；安装包包含 Python 运行依赖。
- 增加独立的 macOS arm64 GitHub Actions 构建任务，手动生成 ZIP、校验文件与验证记录，不自动创建公开 Release。
- 源码启动支持自动读取 `.env.local` 中的模型与数据目录配置。

### 改进

- 统一桌面与 Web 界面的配色、图标与排版，改善论文列表、三栏阅读器、笔记和设置窗口的阅读层次，并适配窄屏。
- 论文列表直接展示作者、年份与标签，摘要预览单独展开；模型的高级选项折叠收纳。
- 本地索引模式打开即可使用，不再要求首次启动时填写 API Key。
- 重新组织 README 和桌面版指南，按产品功能、快速开始、数据管理与开发构建说明使用方式。
- 统一桌面与 Web 版的数据目录、模型配置优先级和平台支持说明。
- GitHub Release 正文仅使用对应版本的更新记录，避免混入未发布内容和完整历史。
- Windows 与 macOS 的应用版本统一从源码版本号生成；发布前检查标签与版本记录，候选版自动标记为 GitHub 预发布。
- 桌面图标与浏览器页签统一使用新的紫色书页标识，桌面窗口标题显示完整版本号。

### 修复

- 原生桌面窗口关闭前等待笔记保存，保存期间锁定编辑；失败时保留窗口，允许重试。
- 笔记保存绑定原论文，切换前等待保存；失败时保留草稿，避免快速切换导致串写或丢失。
- 防止过期的搜索、阅读器和资源包校验响应覆盖当前选择；修复列表内按钮的键盘事件误触发论文打开。
- 生成摘要、导入豆包解析、保存论文信息或高亮时，后台结果不再覆盖后来打开的论文；英文摘要完成后的中文翻译继续处理原论文。
- Markdown 导出使用正在阅读的摘要来源，支持导出豆包中文解析及已生成的英文对照。
- 点击笔记中的摘要引用时，自动切换到对应的系统摘要或豆包解析版本，并展开目标栏。
- 服务重启后恢复被中断的摘要与翻译状态，保留已经生成的内容。
- 资源包恢复后可以再次导入；恢复前校验实际数据库兼容性和 Windows 文件路径，避免错误数据替换当前库。
- 手动摘要通过 API 更新时清理旧报告内容；异常模型地址和响应返回可处理的错误。
- 修复弹窗出现双滚动条，以及 PDF 折叠后点击摘要页码无法直接查看原文的问题。
- 修复 Windows 发布流水线在没有预建 `.venv` 的环境中无法构建的问题。
- 修复 macOS 启动与构建脚本的 Python 版本检查，避免在满足版本要求时仍抛出异常。
- API 服务地址、模型与 API Key 的环境配置现在统一优先于数据库中保存的设置。
- 配置示例不再指向特定开发者的数据路径，留空时使用默认目录。

## [1.1.0] - 2026-09-11

本次更新为 Windows 桌面版加入论文库备份与恢复，方便定期留存资料和迁移到另一台电脑。

### 新增

- **标准资源包**：将数据库与全部原始 PDF 导出为一个 `.pvault` 文件。
- **离线完整包**：在标准资源包的基础上，加入已有的 PDF 预览资产和离线翻译模型。
- **完整资料迁移**：保留论文、摘要、豆包解析、笔记、引用、批注、标签、评分、单词本、回收站和任务记录。
- **恢复前校验与备份**：检查文件路径、SHA-256 和数据库完整性，并在替换前自动备份当前论文库。

### 下载与升级

- 发行包：`PaperVault-v1.1.0-windows-x64.zip`，适用于 Windows 10/11 x64。
- 完整解压后，运行 `PaperVault\PaperVault.exe`。需要 Microsoft Edge WebView2 Runtime。
- 升级时先关闭旧版，再启动新版。默认数据目录仍为 `%LOCALAPPDATA%\PaperVault`，不会因替换程序而被覆盖。
- 资源包恢复会完整替换当前库，不支持合并；API Key 和 `.env.local` 不会写入资源包。

## [1.0.0] - 2026-08-13

PaperVault 首个 Windows 桌面正式版，将本地论文库和双语阅读器整合进独立窗口。

### 新增

- **Windows 桌面入口**：使用 WebView2 加载本地界面，用户数据默认保存在 `%LOCALAPPDATA%\PaperVault`。
- **解压即用的发行包**：提供一目录 ZIP，完整解压后运行 `PaperVault\PaperVault.exe`。
- **双语摘要阅读**：支持中英文结构化摘要、原文页码定位，以及 Markdown 和 JSON 导出。
- **数学公式显示**：统一中英文摘要中的 Markdown 数学表达式，改善公式渲染。
- **单词本**：支持重复收藏提示、美式发音展示与浏览器语音朗读。
- **自动发布**：推送 `v*` 标签后，由 GitHub Actions 构建 Windows ZIP 并上传到 Release。

### 调整

- 移除“速读”“图表分析”和“问论文”入口，将主要阅读流程集中到论文库与双语摘要。

### 首次使用

- 需要 Windows 10/11 x64 和 Microsoft Edge WebView2 Runtime。
- 未配置模型时可使用本地结构索引；详细双语摘要需要在“模型设置”中配置 OpenAI 兼容接口。
- 程序与用户数据分开存放，替换发行包不会覆盖默认数据目录。

[Unreleased]: https://github.com/skywuxuan/PaperVault/compare/v1.2.0-rc.2...HEAD
[1.2.0-rc.2]: https://github.com/skywuxuan/PaperVault/releases/tag/v1.2.0-rc.2
[1.2.0-rc.1]: https://github.com/skywuxuan/PaperVault/releases/tag/v1.2.0-rc.1
[1.1.0]: https://github.com/skywuxuan/PaperVault/releases/tag/v1.1.0
[1.0.0]: https://github.com/skywuxuan/PaperVault/releases/tag/v1.0.0
