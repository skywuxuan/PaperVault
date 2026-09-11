# PaperVault

[![Latest Release](https://img.shields.io/github/v/release/skywuxuan/PaperVault?display_name=tag&sort=semver)](https://github.com/skywuxuan/PaperVault/releases/latest)
[![Windows Release Build](https://github.com/skywuxuan/PaperVault/actions/workflows/windows-release.yml/badge.svg)](https://github.com/skywuxuan/PaperVault/actions/workflows/windows-release.yml)

## 桌面版

PaperVault 提供 Windows x64 和 Apple Silicon macOS 桌面包。两端复用同一套本地后端与界面，数据分别默认保存在 `%LOCALAPPDATA%\PaperVault` 和 `~/Library/Application Support/PaperVault`。

Windows 构建：

```powershell
.\build-windows.ps1
```

Apple Silicon Mac 构建：

```bash
./build-macos.sh
```

产物分别位于 `dist\PaperVault\PaperVault.exe` 和 `dist/PaperVault.app`。推送 `v*` 标签后，GitHub Actions 会同时生成 `windows-x64.zip` 与 `macos-arm64.zip` 发布附件。macOS 包目前为未公证的自分发应用；正式对外发布前应通过 `PAPER_VAULT_CODESIGN_IDENTITY` 配置 Developer ID 签名并完成 Apple notarization。

开发启动分别使用 `.\start-desktop.ps1` 和 `./start-desktop.sh`。完整说明见 [`docs/DESKTOP.md`](docs/DESKTOP.md)。

## Windows 桌面版

项目现在提供基于系统 WebView2 的 Windows 原生桌面入口，同时保留原有本地 Web 版本和全部 `/api/*` 接口。桌面版默认将数据库、论文和模型保存在 `%LOCALAPPDATA%\PaperVault`，不会写入安装目录。

开发模式启动：

```powershell
.\start-desktop.ps1
```

构建 Windows 桌面包：

```powershell
.\build-windows.ps1
```

推荐的一目录构建输出位于 `dist\PaperVault\PaperVault.exe`。桌面架构、数据目录覆盖、Windows 打包以及为 macOS 保留的平台接口详见 [`docs/DESKTOP.md`](docs/DESKTOP.md)。

## 版本与发布

当前公开版本是 [`v1.0.0`](https://github.com/skywuxuan/PaperVault/releases/tag/v1.0.0)，这是 PaperVault Windows 桌面版的首个正式发行版。

Windows 用户可在 [Releases](https://github.com/skywuxuan/PaperVault/releases) 页面下载 `PaperVault-v1.0.0-windows-x64.zip`。解压后运行 `PaperVault\PaperVault.exe` 即可；系统需要安装 Microsoft Edge WebView2 Runtime（Windows 10/11 通常已经自带）。用户数据保存在 `%LOCALAPPDATA%\PaperVault`，升级程序不会覆盖论文库。

版本变更记录见 [`CHANGELOG.md`](CHANGELOG.md)。发布流程由 `.github/workflows/windows-release.yml` 管理：推送形如 `v1.0.0` 的标签后，GitHub Actions 会在 Windows runner 上构建一目录桌面包、压缩 ZIP，并自动创建或更新对应的 GitHub Release。

### 发行日志

#### v1.0.0 · 2026-08-13

- 首个正式 Windows 桌面发行版，提供可解压即用的一目录安装包。
- 保留论文库、双语研究者摘要、公式渲染、Markdown/JSON 导出和本地数据目录隔离。
- 单词本弹窗增加已加入状态、美式音标和语音播放；移除“速读”、“图表分析”和“问论文”界面。
- 发布标签会自动触发 Windows 构建并上传 `PaperVault-v1.0.0-windows-x64.zip`。

完整变更记录见 [`CHANGELOG.md`](CHANGELOG.md)。

维护者发布新版本时执行：

```powershell
git add .
git commit -m "release: prepare v1.0.0"
git tag -a v1.0.0 -m "PaperVault v1.0.0"
git push origin main
git push origin v1.0.0
```

PaperVault 是一个完全运行在 Windows 本机的论文管理 WebUI。后端只监听 `127.0.0.1`，PDF、摘要、标签和模型配置均保存在项目的 `data` 目录，不需要外部数据库或公网服务。

## 启动

要求：Windows 10/11、Python 3.10 或更高版本。

双击 `start.bat`。第一次启动会创建隔离的 `.venv` 环境并安装 PDF 解析与预览依赖，随后浏览器访问：

```text
http://127.0.0.1:8765
```

PowerShell 也可运行：

```powershell
.\start.ps1
```

手动启动：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m backend.app
```

## 数据位置

```text
data/
  paper-vault.db       # SQLite 元数据、摘要、标签和设置
  uploads/             # 原始 PDF 文件
  assets/              # PDF 页面预览与关键图表页
  models/              # Argos 英译中离线模型
```

备份时复制整个 `data` 目录即可。SQLite 是元数据真源，PDF 和大型派生产物保存在文件系统中，数据库仅保存相对 `data` 根目录的稳定引用。论文删除操作只会将记录移入可恢复回收站，不会物理删除 PDF、摘要、图表、批注或版本记录。

### 个人资源包

顶部“备份与恢复”可以为当前本地论文库生成 `.pvault` 资源包。标准资源包包含 SQLite 数据库和全部原始 PDF，因此会保留论文记录、摘要（包括豆包解析）、笔记、引用、高亮、标签、评分、单词本、回收站和已有的分析记录；离线完整包还会包含 PDF 预览资产与本地翻译模型。资源包使用 SQLite 一致性快照和逐文件 SHA-256 校验，不会直接压缩正在使用的数据库。

API Key、`.env.local`、WebView 缓存和临时文件永远不会写入资源包。恢复时会先校验清单、路径、文件哈希和数据库完整性，再自动把当前库备份到数据目录旁的 `PaperVault-backups`，最后完整替换当前论文库；当前设备的模型配置不会从资源包恢复。资源包是数据备份，不包含 PaperVault 程序本身，可以在重新安装服务或桌面版后导入。

## 论文库与批量工作流

论文库采用紧凑列表和左侧分面筛选，可按摘要状态、推荐度、标签、关键词和入库/发表时间快速定位文献。摘要状态、推荐度和自定义标签都可再次点击当前项来取消；点击“全部论文”会同时清除全部筛选并恢复展示整个论文库。勾选论文后会显示批量工具条，支持：

- 批量添加或移除标签，统一设置 0-3 星推荐度；
- 批量导出 Markdown 摘要或 JSON 元数据；
- 批量将论文移入回收站，原始 PDF、图表、摘要与批注保持可恢复。

导入窗口支持一次选择或拖入多个 PDF。程序会逐篇解析并展示结果，公共标签与“上传后生成摘要”设置会应用到全部文件；失败文件会自动组成可重试队列。导入会先通过索引检查原始文件名，再在本地解析标题后检查标准化论文标题；命中现有论文时立即跳过，不生成图表、不调用模型，也不替换已有记录。并发导入造成的极少量竞态重复仍会在保存后安全合并，完整中英文摘要始终优先于只有英文或失败的副本。

## 双语摘要模型

默认使用“本地结构索引”，无需网络或密钥。该模式会提取论文结构、重点英文内容和关键图表页，并生成不复制英文原句的中文索引；它适合本地整理，但不等同于模型生成的研究者摘要。双语研究者摘要需要在模型设置中配置 OpenAI 兼容接口。

在右上角“模型设置”中切换到“OpenAI 兼容接口”后，可填写：

- API Base URL，例如 `https://api.openai.com/v1` 或本地兼容网关地址；
- 默认模型，以及可选的独立英文分析模型和中文翻译模型；
- 模型上下文长度（留空时按模型族自动识别）；
- 英文分析推理强度 `high` 或由用户显式选择的高成本 `max`；
- API Key（无需鉴权的本地兼容服务可留空）；

也可以在“模型设置”中选择“从本地配置导入”，读取仓库根目录自行维护的 `.env.local`：

```env
PAPER_VAULT_BASE_URL=https://api.openai.com/v1
PAPER_VAULT_API_KEY=your-key
PAPER_VAULT_MODEL=gpt-4.1-mini
PAPER_VAULT_ANALYSIS_MODEL=
PAPER_VAULT_TRANSLATION_MODEL=
PAPER_VAULT_CONTEXT_WINDOW_TOKENS=
PAPER_VAULT_ANALYSIS_REASONING_EFFORT=high
```

`.env.local` 已被 Git 忽略。源码开发启动时会检测仓库根目录的这个文件；页面会先询问是否导入，确认后才把配置（包括 Key）写入本机 SQLite。也可以在“模型设置”中手动选择文件导入。通过环境变量提供的 `PAPER_VAULT_BASE_URL`、`PAPER_VAULT_API_KEY`、模型变量和 `PAPER_VAULT_DATA_DIR` 会优先于数据库设置；可复制 `.env.example` 作为模板。`.env.local` 不会打入桌面安装包。
当数据库中尚未保存 Key 且检测到本地配置时，服务启动页面会弹出确认；取消后仍可打开“模型设置”手动填写。

深度总结实现位于 `backend/deep_summary.py`，通用模型适配位于 `backend/llm.py`。英文报告使用长文档 block 结构：

```json
{
  "paper_title": "Original paper title",
  "blocks": [{
    "id": "heading-001",
    "type": "heading",
    "level": 2,
    "text_en": "Experimental Results",
    "page_refs": [4]
  }]
}
```

程序逐页提取 PDF，并以 `[Page N]` 标签连同原始段落、标题、图注、表格文本和公式附近说明送入模型。输入预算根据上下文长度动态计算；DeepSeek V4 按 1M tokens 规划，普通论文尽量一次发送全文，只有确实超过安全预算时才按页、段落或完整句子边界分块归纳。DeepSeek 开放 API 不接收 PDF、`file` 或 `input_image`，因此 PaperVault 不实现伪 PDF 上传，也不会声称非视觉模型看过页面像素。

英文模型通读全文后，围绕核心问题、方法机制、关键证据和适用边界生成研究者摘要，避免逐节复述论文。摘要会先给出问题、核心改动、最强结果和结果边界，再按主张与证据组织方法和实验，并保留必要的模型、数据集、指标和数字。英文 blocks 会立即保存；第二次模型调用只翻译 `{id,type,text_en}`，后端按稳定 id 合并中文。翻译阶段允许在 block 内重排句子和合并紧密相关的分句，但不会改变事实或数字。翻译失败时英文报告仍可阅读，并可单独重新翻译，不会再次运行英文分析。对 429、临时 5xx、连接中断和空响应会进行有限退避重试；英文结构或页码校验失败会携带校验原因重新生成一次。DeepSeek 官方接口的英文分析开启 thinking 并使用 `high`/显式 `max`，翻译和 JSON 修复关闭 thinking；其他 OpenAI-compatible 网关保持其自身控制参数，`reasoning_content` 不保存也不展示。

中文合并会验证 block ID、顺序、非空内容以及所有阿拉伯数字 token 的值和重复次数。长报告从一开始就按输入预算拆成独立小块，最多两路并行；失败只重试对应块，避免整篇 40-60 个 block 因一个坏响应全部重跑。每块输出上限按实际输入动态计算。中文为保持自然语序可以连同完整分句调整数字出现顺序；后端按带标签占位符恢复每个原始数字，不按位置猜测。真正缺失或改写数字时只重译失败 block，并可继续缩小到句子或数字两侧的文本片段，不会重跑英文分析或接受不完整译文。中文汉字紧邻数字（例如“提升到9.1%”）也会被正确识别。

摘要阅读器以长文档方式渲染标题、段落和连续列表，不把每句话拆成卡片。单击任意英文或中文 block，会高亮对应内容并将另一栏滚动到相同视口高度；紧凑页码按钮可直接跳转 PDF。Markdown/JSON 导出支持新 blocks，旧数据库中的 `summary_pairs` 仍使用原视图兼容显示。

摘要栏会显示生成来源。OpenAI 兼容接口生成的摘要会记录并显示生成当时使用的模型名；本地索引、人工编辑和精校内容不会被误标为 AI 模型输出。

### 摘要来源与研究笔记

阅读器支持在“系统解析”和“豆包解析”之间切换。点击“豆包解析”并粘贴公开的
`https://www.doubao.com/thread/...` 分享链接后，PaperVault 会保留豆包原始 Markdown（包括标题、列表、表格、粗体和 LaTeX 公式），并按当前模型配置生成独立英文版；它不会覆盖原有的 PDF 页码对齐摘要。
导入界面会依次显示读取分享页、保存中文解析和生成英文版三个阶段。中文保存完成后即可返回阅读器继续使用，英文版在对应栏内继续生成。豆包中英文内容按标题、段落、列表项、引用和表格建立同序映射，点击任一侧内容会高亮并滚动到另一侧对应位置。

“我的笔记”是独立的右侧悬浮面板。可从 PDF、系统摘要或豆包摘要选中文字，使用“引用到笔记”保存文字快照；笔记不会因后续重新生成摘要而改变，点击引用可回到对应 PDF 页或摘要位置。

## 分层分析与任务版本

阅读器使用 `summary_blocks` 作为新版“双语深读”，并继续兼容旧 `summary_pairs`。每条事实附可点击 PDF 页码，英文分析与中文翻译使用独立存储与重新生成入口，不会相互覆盖。

PDF 中识别出的图表仍作为原文视觉资产预览，但当前版本不再提供独立的图表分析界面或生成任务。

历史分析任务表仍保留用于兼容旧数据库记录；当前版本不再创建或执行速读、图表分析任务。

阅读器提供“双语深读 / 笔记”模式切换。资料库列表展示已读/未读，桌面和小窗口使用同一套紧凑工作台布局。

PDF 页面会根据当前显示宽度、缩放倍率和屏幕像素密度按需生成高清预览，放大后自动替换为更高分辨率的页面图像，同时保留原始 PDF 和矢量内容不变。不同倍率的预览会分档缓存，避免连续缩放反复渲染。PDF 选区高亮会在同一文本行内连成连续色带；重新选中已高亮区域可直接取消高亮。双语深读选中任一语言句子时，对应句会滚动到另一栏的相同视口高度。资料库将推荐度、摘要状态和最近分析状态合并为右侧第一行，将页数、文件大小和入库日期合并为第二行；论文名完整换行展示，不使用省略裁切。“作者总结与简介”默认折叠并使用更易读的字号。导入时会用原始文件名保守验证并拼接 PDF 首页的多行标题，纯年份等无效作者元数据会被过滤，后续导入会尝试从首页标题下方补充识别作者。

## 单词本

在英文摘要或左侧 PDF 正文中双击单词或短语，会优先使用本机的 CTranslate2 + Argos 英译中模型生成中文释义，并显示“加入单词本”浮层。翻译过程不调用公网 API，结果也会缓存在本地；已经收藏过的词会优先复用单词本释义。

首次运行 `start.bat` 或 `start.ps1` 时会安装约 34 MB 的本地推理依赖，并下载约 70 MB 的英译中模型。模型下载后保存在 `data/models`，后续启动和翻译都可离线完成。也可以单独运行：

```powershell
.\setup-offline-translation.ps1
```

模型服务返回余额不足、鉴权失败、超时或连接失败时，界面会给出明确的中文原因；已有摘要不会被失败请求覆盖。

顶部“单词本”入口提供全文搜索、学习中/已掌握筛选、更新时间或字母排序、释义与笔记编辑、来源论文跳转和删除。词条保存上下文、来源摘要句或 PDF 页码；删除原论文后，词条和论文标题快照仍会保留。

## 架构

```text
frontend/              # 原生 HTML / CSS / JavaScript 单页应用
backend/app.py         # HTTP 服务、REST API、翻译与 PDF 预览接口
backend/database.py    # SQLite 数据访问层
backend/analysis.py    # 速读、图表分析、全文切分与引用问答
backend/deep_summary.py # 全文输入规划、英文长报告、独立翻译与结构校验
backend/pdf_parser.py  # PDF 文本、页面图像、可交互词层与关键图表提取
backend/llm.py         # 可替换的摘要供应商适配层
backend/offline_translation.py # 本地英译中推理与学术词典
tests/                 # 本地 API 回归测试
```

前后端通过 `/api/*` JSON 接口解耦。未来封装 Windows 客户端时，可以让 WebView2、Tauri 或其他桌面壳启动同一后端进程并加载本地地址，数据结构和前端无需重写。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 当前边界

- 扫描版 PDF 没有文本层时无法直接提取内容，后续可在 `pdf_parser.py` 接入本地 OCR。
- 扫描版 PDF 或提取不到文本的页面仍需要后续本地 OCR；无文本证据时不会生成深度总结。
- OpenAI 兼容模式使用 `/chat/completions` JSON 接口，供应商需要支持标准消息格式。
- PDF 预览由本地后端渲染页面图像并叠加可交互文本层，因此摘要和 PDF 正文中的英文都可双击翻译。
- 图表区展示自动识别出的关键架构图页和实验表页；点击缩略图会将左侧 PDF 跳转到对应页。
- 模型上下文或输出限制仍可能触发安全分块/续写；每段结果都必须通过 JSON、block id 和页码校验后才会持久化。
