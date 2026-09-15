# 版本记录

这里记录 PaperVault 的用户可见变化。当前 Windows 稳定版为 [v1.1.0](https://github.com/skywuxuan/PaperVault/releases/tag/v1.1.0)；`v1.2.0-rc.1` 为 Windows 与 Apple Silicon Mac 的候选版，尚未上传 GitHub Releases。安装和升级方式见[桌面版指南](docs/DESKTOP.md)。

## [Unreleased]

暂无候选版之外的变更。

## [1.2.0-rc.1] - 2026-09-15

本次候选版重新整理了论文库与阅读器的界面，重点改善笔记保存、摘要任务恢复和资料迁移的可靠性，并加入 Apple Silicon Mac 桌面预览构建。**候选版尚未上传 GitHub Releases；Windows 稳定发行包仍是 v1.1.0。**

### 试用与数据保护

- 本地 Windows x64 预览包命名为 `PaperVault-v1.2.0-rc.1-windows-x64.zip`。完整解压后运行 `PaperVault\PaperVault.exe`；不要直接从压缩包中运行，也不要只复制 EXE。
- Apple Silicon Mac 预览包命名为 `PaperVault-v1.2.0-rc.1-macos-arm64.zip`，解压后将完整的 `PaperVault.app` 放入“应用程序”。通过 GitHub Actions 分发，下载与校验步骤见[macOS 安装指南](docs/DESKTOP.md#macos-apple-silicon-预览版)。预览包使用 ad hoc 签名，未完成 Developer ID 签名和 Apple 公证。
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

[Unreleased]: https://github.com/skywuxuan/PaperVault/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/skywuxuan/PaperVault/releases/tag/v1.1.0
[1.0.0]: https://github.com/skywuxuan/PaperVault/releases/tag/v1.0.0
