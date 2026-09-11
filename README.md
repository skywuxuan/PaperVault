# PaperVault

PaperVault 是一个运行在本机的论文资料库。它把 PDF、双语摘要、豆包解析、笔记、批注、标签、评分和单词本保存在本地，不需要云端数据库。

[Windows x64 桌面版 v1.1.0](https://github.com/skywuxuan/PaperVault/releases/tag/v1.1.0) · [下载 ZIP](https://github.com/skywuxuan/PaperVault/releases/download/v1.1.0/PaperVault-v1.1.0-windows-x64.zip) · [变更记录](CHANGELOG.md)

## 当前版本

当前正式发布的是 Windows x64 桌面版。桌面版使用 WebView2 加载同一套本地后端和前端，用户数据默认保存在：

```text
%LOCALAPPDATA%\PaperVault
```

Windows 10/11 通常已经安装 WebView2 Runtime；如果系统没有安装，需要先安装它。

下载 ZIP 后解压，运行：

```text
PaperVault\PaperVault.exe
```

桌面版升级不会覆盖论文库数据。当前 Release 只提供 Windows；仓库中的 macOS 脚本是后续平台预留，不属于本次发布。

## 本地 Web 版

要求：Windows 10/11、Python 3.10 或更高版本。

在仓库根目录运行：

```powershell
.\start.ps1
```

也可以双击 `start.bat`。启动脚本会检查依赖，并准备本地英译中模型。服务启动后访问：

```text
http://127.0.0.1:8765
```

手动启动后端：

```powershell
.\.venv\Scripts\python.exe -m backend.app --host 127.0.0.1 --port 8765
```

## 主要功能

### 论文库

- 导入单篇或多篇 PDF，自动识别标题、作者和年份。
- 按关键词、标签、摘要状态、推荐度和入库时间筛选。
- 支持 0-3 星推荐度、已读/未读、标签管理和批量操作。
- 支持 Markdown 摘要和 JSON 元数据导出。
- 删除论文会进入本地回收站，可恢复，不会立即删除 PDF。

### 论文阅读器

- PDF 页面预览、缩放、页码跳转、文本选择和高亮批注。
- 系统生成的双语研究者摘要，包含稳定段落结构和 PDF 页码引用。
- “系统解析”和“豆包解析”可以独立切换。
- 豆包解析支持公开分享链接导入，保留标题、列表、表格、粗体和 LaTeX 公式，并继续生成英文版。
- 系统摘要和豆包摘要支持中英文段落映射、高亮和同步滚动。
- “我的笔记”是独立的悬浮面板，可以引用 PDF、系统摘要或豆包解析中的内容。
- 英文单词或短语可以打开词典卡片、查看释义、播放美式发音并加入单词本。

### 备份与恢复

顶部“备份与恢复”会为当前本地论文库生成 `.pvault` 资源包。

- 标准资源包：SQLite 数据库和全部原始 PDF。
- 离线完整包：额外包含 PDF 预览资产和离线翻译模型。
- 资源包包含论文、摘要、豆包解析、笔记、引用、批注、标签、评分、单词本、回收站和已有任务记录。
- API Key、`.env.local`、WebView 缓存和临时文件永远不会写入资源包。
- 恢复前会校验清单、路径、SHA-256 和数据库完整性，并自动备份当前论文库。
- V1 只支持完整替换恢复，不支持把两个论文库合并。

当前库可在备份窗口中看到资源包估算大小。资源包是数据文件，不包含 PaperVault 程序本身；重新安装桌面版或本地服务后，再从“备份与恢复”导入即可。

## 模型配置

本地结构索引是默认模式，不需要 API Key。它用于提取论文结构和重点内容，不等同于模型生成的完整双语研究者摘要。

需要生成详细英文摘要和中文翻译时，在“模型设置”中选择“OpenAI 兼容接口”，填写：

- API Base URL，例如 `https://api.openai.com/v1` 或兼容网关地址；
- 默认模型；
- 可选的英文分析模型和中文翻译模型；
- 可选的上下文长度和英文分析推理强度；
- API Key。

也可以在仓库根目录创建 `.env.local`：

```env
PAPER_VAULT_DATA_DIR=
PAPER_VAULT_BASE_URL=https://api.openai.com/v1
PAPER_VAULT_API_KEY=your-key
PAPER_VAULT_MODEL=gpt-4.1-mini
PAPER_VAULT_ANALYSIS_MODEL=
PAPER_VAULT_TRANSLATION_MODEL=
PAPER_VAULT_CONTEXT_WINDOW_TOKENS=
PAPER_VAULT_ANALYSIS_REASONING_EFFORT=high
```

源码启动时会读取仓库根目录的 `.env.local`。环境变量优先于 SQLite 中保存的模型设置；`.env.local` 已被 Git 忽略，也不会打进桌面安装包。API Key 只用于当前设备的模型请求，不会进入资源包。

## 数据目录

本地 Web 版默认使用仓库根目录的 `data/`；可以通过 `PAPER_VAULT_DATA_DIR` 或启动参数切换目录。桌面版默认使用 `%LOCALAPPDATA%\PaperVault`。

```text
data/
  paper-vault.db       # SQLite 元数据、摘要、笔记、单词本和设置
  uploads/             # 原始 PDF
  assets/              # PDF 预览和图表资产
  models/              # 本地翻译模型
  backups/             # 资源包和本地备份文件
  webview/             # 桌面 WebView 本地缓存
```

论文、PDF、摘要和个人资料的真源是 SQLite 与 `uploads/`。`assets/`、`models/` 和 `webview/` 可以重新生成或重新下载，资源包会按选择决定是否包含它们。

## Windows 构建

在 Windows 上准备好 `.venv` 和桌面依赖后运行：

```powershell
.\build-windows.ps1 -SkipInstall
```

推荐的一目录输出为：

```text
dist\PaperVault\PaperVault.exe
```

不带 `-SkipInstall` 时，脚本会先安装 `requirements-desktop.txt`。推送 `v*` 标签后，`.github/workflows/windows-release.yml` 会在 Windows runner 上构建 ZIP 并创建 GitHub Release。

## 测试

运行资源包、豆包导入、模型配置和 API 核心回归测试：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_resource_package tests.test_doubao tests.test_api tests.test_config tests.test_llm -q
```

检查前端语法：

```powershell
node --check frontend\app.js
```

## 项目结构

```text
frontend/                    # 原生 HTML / CSS / JavaScript 界面
backend/app.py               # 本地 HTTP 服务和 REST API
backend/database.py          # SQLite 数据访问层
backend/resource_package.py  # 资源包生成、校验和恢复
backend/deep_summary.py      # 英文研究者摘要和中文翻译
backend/doubao.py            # 豆包分享页导入和英文转换
backend/pdf_parser.py        # PDF 文本、页面和资产提取
backend/offline_translation.py # 本地英译中模型
desktop/                     # Windows 桌面壳和平台数据目录
tests/                       # API、模型、豆包和资源包回归测试
```

## 当前边界

- 扫描版 PDF 没有文本层时，当前版本不会自动 OCR。
- 非视觉模型只接收 PDF 提取出的文本、图注和表格文本，不会读取页面像素。
- 本项目是本地单用户论文库，不提供云端账号、多人协作或云同步。
- 资源包恢复会完整替换当前库；恢复前的自动备份应保存在其他磁盘或同步目录，才能防止设备级损坏。
