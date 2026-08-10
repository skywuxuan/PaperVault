# PaperVault

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

## 论文库与批量工作流

论文库采用紧凑列表和左侧分面筛选，可按摘要状态、推荐度、标签、关键词和入库/发表时间快速定位文献。勾选论文后会显示批量工具条，支持：

- 一次提交多篇论文的持久化速读任务，任务由后台处理并可跨页面刷新恢复；
- 批量添加或移除标签，统一设置 0-3 星推荐度；
- 批量导出 Markdown 摘要或 JSON 元数据；
- 批量将论文移入回收站，原始 PDF、图表、摘要与批注保持可恢复。

导入窗口支持一次选择或拖入多个 PDF。程序会逐篇解析并展示结果，公共标签与“上传后生成摘要”设置会应用到全部文件；失败文件会自动组成可重试队列。同名标题仍按论文标题去重，新导入的有效记录会替换旧的失败记录并继承已有标签与最高推荐度。

## 双语摘要模型

默认使用“本地结构索引”，无需网络或密钥。该模式会提取论文结构、重点英文内容和关键图表页，并生成不复制英文原句的中文索引；它适合本地整理，但不等同于模型生成的详细翻译。详细双语总结需要在模型设置中配置 OpenAI 兼容接口。

在右上角“模型设置”中切换到“OpenAI 兼容接口”后，可填写：

- API Base URL，例如 `https://api.openai.com/v1` 或本地兼容网关地址；
- 默认模型，以及可选的独立英文分析模型和中文翻译模型；
- 模型上下文长度（留空时按模型族自动识别）；
- 英文分析推理强度 `high` 或由用户显式选择的高成本 `max`；
- API Key（无需鉴权的本地兼容服务可留空）；

密钥也可通过环境变量提供，此时优先于数据库设置：

```powershell
$env:PAPER_VAULT_API_KEY = "your-key"
.\start.ps1
```

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

英文模型通读全文后，按照论文自己的叙事顺序生成不限数量的二级标题、真实方法子模块三级标题、连续说明段落和必要的并列列表。英文 blocks 会立即保存；第二次模型调用只翻译 `{id,type,text_en}`，后端按稳定 id 合并中文。翻译失败时英文报告仍可阅读，并可单独重新翻译，不会再次运行英文分析。DeepSeek 英文分析开启 thinking 并使用 `high`/显式 `max`，翻译和 JSON 修复关闭 thinking；`reasoning_content` 不保存也不展示。

摘要阅读器以长文档方式渲染标题、段落和连续列表，不把每句话拆成卡片。单击任意英文或中文 block，会高亮对应内容并将另一栏滚动到相同视口高度；紧凑页码按钮可直接跳转 PDF。Markdown/JSON 导出支持新 blocks，旧数据库中的 `summary_pairs` 仍使用原视图兼容显示。

摘要栏会显示生成来源。OpenAI 兼容接口生成的摘要会记录并显示生成当时使用的模型名；本地索引、人工编辑和精校内容不会被误标为 AI 模型输出。

## 分层分析与任务版本

阅读器使用 `summary_blocks` 作为新版“深读”，并继续兼容旧 `summary_pairs`；独立“速读”包含一句话结论、动机、方法步骤、关键发现、贡献、局限和精读建议。每条事实附可点击 PDF 页码，速读和深读使用独立存储与重新生成入口，不会相互覆盖。

关键图表分析只接受带编号的 Figure/Fig./Table 标题，并用 PyMuPDF 裁剪标题附近的真实图片、矢量图或表格区域，不再把正文中提及图号的整页当成图表。每项分析结构化说明“看哪里、证明什么、为什么重要”，并可直接跳到原 PDF 页。分析输入哈希包含论文内容、模型、prompt 版本以及裁剪资产内容哈希；模型路径始终使用提取文本和元数据作为安全依据，不会在不支持图片时声称看过像素。

速读和图表分析通过 SQLite 持久化任务运行，记录排队、运行、成功、失败、重试次数、错误、模型、token、耗时、输入哈希和 prompt 版本。服务重启会恢复未完成任务，失败任务可重试，阅读器可切换查看历史成功版本。

## 带引用问答

论文文本按页切分到 SQLite FTS5 索引，每个块保存论文 ID、页码、章节、顺序和内容哈希。问答支持当前论文或全库范围，回答只能使用检索到的上下文，并必须返回有效来源卡片；点击来源可跳到对应论文页。没有 OpenAI-compatible 配置时接口会明确返回不可用状态，不会生成伪回答。

阅读器提供“速读 / 双语深读 / 问论文 / 笔记”模式切换。资料库列表展示已读/未读及最近分析任务状态，桌面和小窗口使用同一套紧凑工作台布局。

PDF 选区高亮会在同一文本行内连成连续色带；重新选中已高亮区域可直接取消高亮。双语深读选中任一语言句子时，对应句会滚动到另一栏的相同视口高度。资料库将推荐度放在行右侧，作者和简介默认折叠；纯年份等无效作者元数据会被过滤，后续导入会尝试从首页标题下方补充识别作者。

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
