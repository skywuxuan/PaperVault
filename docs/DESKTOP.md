# PaperVault 桌面版

[返回首页](../README.md) · [下载稳定版](https://github.com/skywuxuan/PaperVault/releases/tag/v1.1.0) · [版本记录](../CHANGELOG.md)

桌面版将 PaperVault 的论文库与阅读器放进独立窗口。打开应用时会自动启动本地服务，关闭窗口时停止服务，无需另外打开终端或浏览器。

## 平台与发行状态

**稳定版：v1.1.0。当前源码：v1.2.0-rc.1 本地候选版，尚未上传 GitHub Releases。** 本文同时说明稳定版的安装方式和候选版的试用流程。

| 平台 | 当前状态 | 窗口引擎 | 默认数据目录 |
| --- | --- | --- | --- |
| Windows 10/11 x64 | v1.1.0 稳定 ZIP；v1.2.0-rc.1 本地候选版 | Microsoft Edge WebView2 | `%LOCALAPPDATA%\PaperVault` |
| Apple Silicon macOS | 提供源码启动与 `.app` 构建脚本 | 系统 WKWebView | `~/Library/Application Support/PaperVault` |
| Linux | 代码中保留平台适配，未提供正式发行包 | 系统 WebView | `$XDG_DATA_HOME/papervault`，未设置时使用 `~/.local/share/papervault` |

macOS 和 Linux 不属于当前 Windows 正式发行版的支持范围。仓库中的发布流水线目前只构建 Windows x64。

## 安装与首次使用

1. 在 [v1.1.0 发行页](https://github.com/skywuxuan/PaperVault/releases/tag/v1.1.0) 中下载 `PaperVault-v1.1.0-windows-x64.zip`。
2. 将压缩包完整解压到一个固定目录，保留其中所有文件与子目录。
3. 打开 `PaperVault\PaperVault.exe`，导入 PDF 即可开始使用。

发行包已经包含 Python 运行所需的程序依赖，不需要单独安装 Python。系统需要 Microsoft Edge WebView2 Runtime；若启动时提示缺少 WebView2，请先安装该组件。

首次使用可以直接导入、整理和阅读论文。本地结构索引无需 API Key；详细双语摘要需要在“模型设置”中配置兼容的模型服务。

## 试用本地候选版

`v1.2.0-rc.1` 用于验证新界面、笔记保存与资料迁移流程。当前没有公开下载页；使用本地构建提供的 `PaperVault-v1.2.0-rc.1-windows-x64.zip`，或按下文从源码启动。

如果预览包附有 SHA-256 校验文件，先计算 ZIP 的哈希值，与校验文件中的值逐字符比较：

```powershell
Get-FileHash .\PaperVault-v1.2.0-rc.1-windows-x64.zip -Algorithm SHA256
```

完整解压预览包，在解压目录中打开 PowerShell，使用独立数据目录启动：

```powershell
.\PaperVault\PaperVault.exe --data-dir "$env:LOCALAPPDATA\PaperVault-Preview"
```

这样会创建一份独立的试用论文库。可导入少量测试 PDF，也可以先在稳定版导出 `.pvault`，再将其恢复到试用库中；恢复操作会替换试用库。API Key 需在候选版中重新填写。

建议依次验证以下使用流程：

1. 导入 PDF，检查作者、年份、标签、搜索与筛选。
2. 打开阅读器，检查 PDF 与双语内容排版、页码定位、窄窗口显示。
3. 编辑笔记并切换论文，确认内容保留在原论文；关闭应用后重开确认已保存。
4. 生成本地结构索引；已配置模型服务时，再检查双语摘要与翻译。
5. 导出 `.pvault`，恢复到另一个空的试用目录，核对论文、PDF 和笔记，再导入一篇新 PDF。

试用结束后，关闭候选版，正常启动稳定版即可继续使用原论文库。上述流程是试用建议；具体构建的验证结果以随包说明为准。

## 升级与迁移

### 升级应用

先在“备份与恢复”中导出一份资源包，关闭 PaperVault，再将新版 ZIP 解压到新目录并启动。程序目录与论文库目录彼此独立，替换程序不会覆盖默认数据目录。

不要只复制 `PaperVault.exe`；一目录发行包依赖其旁边的文件。升级后需要回退旧版时，优先恢复对应版本的备份，避免旧版直接读取已升级的数据结构。

### 迁移到另一台电脑

在原电脑导出 `.pvault` 资源包，在新电脑安装 PaperVault，再从“备份与恢复”导入。标准资源包包含数据库和 PDF；离线完整包还包含已有的预览资产与本地翻译模型。

**恢复会完整替换当前论文库，不会合并两边的数据。** 恢复前会自动创建备份，默认放在数据目录旁的 `PaperVault-backups/` 中。API Key 不随资源包迁移，新电脑需要重新配置模型服务。

## 更改数据目录

Windows 桌面版默认把所有论文数据保存在：

```text
%LOCALAPPDATA%\PaperVault
```

如果要长期使用其他磁盘，在上述默认目录中创建 `desktop.json`，填写绝对路径：

```json
{
  "data_dir": "D:\\PaperVaultData"
}
```

重新启动后，应用将使用指定目录。**修改路径只会切换当前论文库，不会自动移动已有资料。** 需要迁移时，先从旧目录导出资源包，切换目录后再恢复。

也可以通过命令行临时指定目录：

```powershell
# 从源码启动
.\start-desktop.ps1 --data-dir "D:\PaperVaultData"

# 启动已解压的发行版
.\PaperVault\PaperVault.exe --data-dir "D:\PaperVaultData"
```

目录选择优先级为：

1. 启动参数 `--data-dir`。
2. 环境变量 `PAPER_VAULT_DATA_DIR`。
3. 平台默认目录中的 `desktop.json`。
4. 平台默认数据目录。

PDF、数据库、预览资产、离线模型和桌面缓存都会使用选定目录。`desktop.json` 是本机配置，无需提交到仓库。

## 模型与环境配置

一般使用场景直接在应用的“模型设置”中配置即可。开发时可复制仓库根目录的 `.env.example` 为 `.env.local`：

```dotenv
PAPER_VAULT_DATA_DIR=
PAPER_VAULT_BASE_URL=https://api.openai.com/v1
PAPER_VAULT_API_KEY=your-key
PAPER_VAULT_MODEL=your-model-name
```

`PAPER_VAULT_DATA_DIR` 留空时使用默认目录；相对路径以 `.env.local` 所在目录为基准解析。

配置加载规则如下：

- 系统环境变量优先于 `.env.local`；非空的模型环境配置优先于应用中保存的设置。
- 源码启动首先查找仓库根目录的 `.env.local`，也支持当前工作目录中的文件。
- 打包后的应用还会查找可执行文件旁的 `.env.local`。多个候选文件不会合并，只读取首个找到的文件。
- 设置 `PAPER_VAULT_ENV_FILE` 可明确指定要读取的环境文件。
- `.env.local` 被 Git 忽略，不会嵌入发行包，也不会包含在 `.pvault` 资源包中。

配置远程模型后，摘要所需的论文内容会发送到所选服务。API Key 保留在当前设备的模型配置中，不随论文库资源包导出。

## 开发与打包

### Windows

开发环境需要 Windows 10/11、Python 3.10+ 和 Microsoft Edge WebView2 Runtime。

从源码启动独立窗口：

```powershell
.\start-desktop.ps1
```

构建默认的一目录发行包：

```powershell
.\build-windows.ps1
```

脚本会准备 Python 环境、安装 `requirements-desktop.txt` 并构建程序，输出为 `dist\PaperVault\PaperVault.exe`。已有依赖时可以跳过安装：

```powershell
.\build-windows.ps1 -SkipInstall
```

单文件模式用于分发测试：

```powershell
.\build-windows.ps1 -OneFile
```

其输出为 `dist\PaperVault.exe`。正式发布采用启动更快的一目录格式；程序使用系统已安装的 WebView2 Runtime，不额外打包完整浏览器。

### Apple Silicon macOS

需要 M 系列 Mac、arm64 Python 3.10+ 和 Xcode Command Line Tools。构建须在 macOS arm64 环境中进行。

```bash
# 从源码启动
./start-desktop.sh

# 构建应用
./build-macos.sh
```

输出为 `dist/PaperVault.app`。可以通过环境变量传入签名身份：

```bash
export PAPER_VAULT_CODESIGN_IDENTITY="Developer ID Application: Example Company (TEAMID)"
./build-macos.sh
```

构建脚本不会自动完成 Apple 公证。面向公开分发时，需另行完成签名、公证与票据附加；当前仓库未提供 macOS 自动发布任务。

### GitHub Release

发布前修改 `backend/__init__.py` 中的 `__version__`，并在 `CHANGELOG.md` 中添加对应的 `## [版本号] - 日期` 条目。Windows 文件属性与 macOS 应用版本由构建脚本自动生成，无需再手动维护第二份版本号。候选版使用 `1.2.0-rc.1` 这样的格式，也支持 `alpha.N` 和 `beta.N` 后缀。

准备公开发布时，同步修改 README、本文和对应版本记录中的发行状态，确保 Release 正文不再写“本地预览，尚未发布”。只有发布新的稳定版时，才更新文档中的稳定版下载入口。

推送 `v*` 标签后，[Desktop Release 工作流](../.github/workflows/windows-release.yml) 会先检查标签是否与源码版本完全一致，以及是否存在对应版本记录；检查通过后构建 Windows x64 ZIP。Release 正文只取该版本的变更，候选版会自动标记为 GitHub 预发布。例如，`v1.2.0-rc.1` 只使用 `[1.2.0-rc.1]` 条目，不包含未发布内容和其他版本的历史。

可在本地检查版本并预览生成的说明：

```powershell
.\.venv\Scripts\python.exe tools\desktop_version.py --check-tag v1.2.0-rc.1
.\.venv\Scripts\python.exe tools\release_notes.py --version v1.2.0-rc.1
```

如果找不到对应版本，生成过程会报错，以免发布空白或错误的说明。本地生成版本记录、构建 EXE 或创建 ZIP 都不会发布到 GitHub；推送版本标签才会触发远端发布。

## 桌面版与 Web 版的关系

两者复用相同的前端与 `/api/*` 接口。桌面版自动选择一个可用的本机端口，只监听 `127.0.0.1`；关闭窗口时结束本次启动的服务。桌面桥仅提供应用名称、版本、平台与数据目录等窗口信息。

Web 版通过 `start.ps1` 或 `start.bat` 启动，默认使用仓库的 `data/` 目录。两种入口的默认数据目录不同；如需访问同一份资料，可选择同一目录，但不要让两个实例同时操作同一个论文库。
