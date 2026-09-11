# Changelog

所有重要变更都会记录在这里。

## [Unreleased]

### 新增

- 新增 Apple Silicon macOS 桌面启动与 `.app` 构建脚本。
- 发布流水线同时构建 Windows x64 和 macOS arm64 压缩包。
- 源码启动自动加载 `.env.local` 中的 LLM 与数据目录配置。

### 修复

- 修复 Windows 发布流水线在干净 runner 中因缺少 `.venv` 而直接失败的问题。
- 环境变量中的 API Base URL、模型和 API Key 现在统一覆盖 SQLite 设置。

## [1.1.0] - 2026-09-11

### 新增

- 发布 Windows x64 桌面版，支持从 GitHub Releases 下载一目录 ZIP 包。
- 新增个人资源包备份与恢复，支持标准资源包和离线完整包。
- 资源包包含论文、PDF、摘要、豆包解析、笔记、批注、标签、评分和单词本；API Key 与 `.env.local` 不会写入资源包。
- 恢复资源包前会校验路径、哈希和数据库完整性，并自动备份当前论文库。

## [1.0.0] - 2026-08-13

PaperVault Windows 桌面版首个正式发行版。

### 新增

- 提供基于 WebView2 的 Windows 原生桌面入口，默认将论文库和配置保存到 `%LOCALAPPDATA%\PaperVault`。
- 提供一目录 Windows 发布包，解压后可直接运行 `PaperVault\PaperVault.exe`。
- 摘要阅读器支持中英文结构化内容、页码定位和 Markdown/JSON 导出。
- 中英文摘要中的数学公式统一使用可渲染的 Markdown 数学表达式。
- 单词本支持重复状态提示、英式/美式发音信息中的美音展示，以及浏览器语音朗读。
- GitHub Actions 在推送 `v*` 标签后自动构建并上传 Windows ZIP 到 GitHub Release。

### 移除

- 移除“速读”、“图表分析”和“问论文”界面及对应入口，聚焦论文库和双语摘要阅读流程。

### 使用说明

- Windows 10/11 需要 Microsoft Edge WebView2 Runtime。
- 首次启动时如果尚未配置模型，可先使用本地索引；需要生成详细双语摘要时，在“模型设置”中配置 OpenAI-compatible API。
- 用户数据位于 `%LOCALAPPDATA%\PaperVault`，应用升级或替换安装包不会覆盖该目录。
