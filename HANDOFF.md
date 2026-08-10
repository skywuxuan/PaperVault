# PaperVault Development Handoff

Updated: 2026-08-10 (Asia/Shanghai)

## 1. Start Here

Project root:

```text
C:\Users\skywu\Documents\Codex\PaperVault
```

Windows desktop development startup:

```powershell
.\start-desktop.ps1
```

Desktop health response:

```json
{"status":"ok","version":"2.0.1"}
```

The desktop shell chooses an ephemeral localhost port. The legacy browser mode
is still available at `http://127.0.0.1:8765` through:

```powershell
.\start.ps1
```

The Windows desktop package is built with:

```powershell
.\build-windows.ps1
```

The verified portable executable is `dist\PaperVault\PaperVault.exe`. No
packaged process is intentionally left running after QA. The source browser
service is expected at `http://127.0.0.1:8765` with the active project data directory.

## 2. Product Scope

PaperVault is a local paper knowledge base with a native Windows desktop shell and a retained browser UI. It stores all PDFs, metadata, bilingual summaries, tags, ratings, vocabulary, extracted figures, and PDF annotations locally.

The Windows client uses pywebview with the system WebView2 runtime. The platform boundary already defines macOS WKWebView data and icon contracts so a future macOS bundle can reuse the same frontend, backend, and `/api/*` surface.

## 3. Technology

- Backend: Python standard-library `ThreadingHTTPServer` and JSON/Multipart APIs.
- Database: SQLite (`data/paper-vault.db`).
- Frontend: vanilla HTML, CSS, and JavaScript. There is no frontend build step.
- PDF parsing: `pypdf`.
- PDF page rendering, text coordinates, and figure extraction: `PyMuPDF`.
- Offline English-to-Chinese lookup: CTranslate2/Argos-compatible local model plus an academic glossary.
- Pronunciation: `cmudict` with local fallback logic.
- LLM: configurable OpenAI-compatible chat-completions adapter.
- Desktop: pywebview 6.x, WebView2 on Windows, and a PyInstaller one-directory package.

Dependencies are pinned by range in `requirements.txt`.
Desktop and packaging dependencies are in `requirements-desktop.txt`.

## 4. Data and Secrets

Local persistent data:

```text
data/
  paper-vault.db       SQLite records and settings
  uploads/             Original PDF files
  assets/              Extracted paper page/figure images
  models/              Offline translation models
```

The active development library is located at:

```text
C:\Users\skywu\Documents\Codex\PaperVault\data
```

It was copied byte-for-byte on 2026-08-10 from the previous library at:

```text
C:\Users\skywu\Documents\Codex\2026-08-07\f-d-s-f\outputs\paper-vault\data
```

The previous library was retained unchanged as a rollback copy. Future Codex
development uses `C:\Users\skywu\Documents\Codex\PaperVault` as the project
root and its `data` directory as the active development library. Do not reset,
replace, or use either real library for automated tests; tests must continue to
use temporary data directories.

The packaged Windows desktop app still defaults to `%LOCALAPPDATA%\PaperVault`
when no override is configured. This workstation has a machine-local
`%LOCALAPPDATA%\PaperVault\desktop.json` pointing to the active project
library, so directly opening the packaged executable and source desktop startup
both use the same active library. The file contains no credentials and is
not tracked by Git. An explicit source launch remains available:

```powershell
.\start-desktop.ps1 --data-dir ".\data"
```

`PAPER_VAULT_DATA_DIR` and `--data-dir` remain the cross-platform override
contract and take precedence over the local config. The selected data directory
now also controls offline translation model loading. Do not perform another
migration without first stopping PaperVault, creating a consistent backup, and
validating the destination database.

The API key source file exists at:

```text
C:\Users\skywu\Documents\Codex\2026-08-07\f-d-s-f\outputs\key.txt.txt
```

Do not print, commit, or copy its contents into logs or documentation. The active key is already stored in SQLite and `/api/settings` masks it.

Current model configuration:

```text
provider: openai_compatible
base_url: https://api.deepseek.com
model: deepseek-v4-flash
max_input_chars: 60000
api_key: stored locally, masked by API
```

`backend/llm.py` disables DeepSeek thinking mode for the official DeepSeek host and `deepseek-v4*` models. This prevents reasoning tokens from consuming the response budget before the structured summary is returned.

## 5. Current Library State

The previous handoff described 4 papers. A read-only check on 2026-08-10 found
9 distinct physical paper records in the active database, each with 49–60
existing bilingual deep-read pairs. Treat 9 as the current observed baseline;
do not delete or consolidate records to force the old count. The source database
was opened with SQLite `mode=ro&immutable=1` only. Its schema remained at
`user_version = 0`, with no `read_state` or `deleted_at` columns, after the
compatibility check.

An exact schema-only clone, containing no settings, credentials, paper rows, or
PDF content, migrated successfully to schema version 2 and added analysis jobs,
analysis runs, page chunks, read state, and recycle-bin fields. Automated tests
must continue to use temporary data directories.

## 6. Implemented Features

### Paper ingestion and persistence

- Single and multi-PDF import.
- Per-file batch import queue with success/error states and retry of failed files.
- PDF title, author, year, page count, text, and visual-page extraction.
- Optional common tags and automatic summary generation during batch import.
- Local SQLite persistence across restarts.
- Duplicate consolidation by normalized PDF paper title.
- A newer successful duplicate replaces older failed/poorer records while preserving the union of tags and the highest rating.
- Duplicate records are moved to the recoverable recycle bin; their PDFs and extracted assets are retained.

### Detailed bilingual summary

- Configurable OpenAI-compatible LLM.
- DeepSeek official API currently active.
- Structured 40-55 sentence-pair report organized into paper information, background, architecture, data/training, experiments, quantitative results, conclusions, limitations, and innovations.
- English and Chinese are stored as aligned sentence pairs.
- Markdown-like document rendering with sections, numbered statements, evidence page references, and inline figures.
- Clicking either language sentence highlights the pair and aligns the corresponding sentence to the same viewport height in the other column.
- Clicking a summary figure synchronizes the corresponding figure in the other language and the source PDF page.
- Chinese summary can be exported as Markdown.
- Existing summaries are preserved if a regeneration request fails.
- Latest typography fix: decimal/scientific/numeric values such as `0.91`, `5e-5`, and `1.2M` inherit the exact body font size, weight, and line height instead of being rendered as bold keywords.

### Layered analysis and versioned jobs

- Existing 40–55 pair bilingual summaries remain the independent deep-read source in `papers.summary_pairs`.
- Quick read is stored separately and covers the headline, motivation, method, findings, contributions, limitations, and reading guide with PDF page evidence.
- Figure analysis requires numbered Figure/Fig./Table captions and uses PyMuPDF to crop the matching image, vector, or table region instead of treating incidental body references or complete pages as figures.
- Figure analysis is safely text-grounded when the configured model has no image capability; it never claims pixel inspection.
- Analysis input hashes include paper content and, for figures, actual image-file content hashes rather than only file paths.
- `analysis_jobs` persists queued/running/succeeded/failed states, attempts, errors, provider/model, tokens, duration, input hash, and prompt version.
- `analysis_runs` preserves successful and failed historical versions; a new quick read never overwrites the deep read.
- Running jobs are recovered after service restart and failed jobs remain retryable up to the stored limit.

### Retrieval and cited questions

- Page text is split into stable chunks with paper ID, page, section hint, ordinal, and content hash.
- SQLite FTS5 is used when available, with a local `LIKE` fallback for constrained environments.
- Questions support current-paper and whole-library scope.
- Model answers are limited to retrieved context and must return at least one valid retrieved source ID.
- Invalid or invented source IDs are discarded; no OpenAI-compatible configuration returns an explicit unavailable state.
- Source entries include paper title, page, excerpt, and content hash; the frontend jumps to the cited PDF page.

### PDF reader and notes

- Local PDF page renderer instead of an embedded browser PDF plugin.
- Page navigation, zoom, fit width, and trackpad `Ctrl`/pinch-style zoom scoped to the PDF panel.
- Text selection and double-click word translation.
- Persistent PDF highlights with four colors and connected same-line highlight bands.
- Selecting an already highlighted range exposes a direct cancel-highlight action; the annotation popover uses the same action wording.
- Optional annotation notes.
- The deep-read reader panels can be independently collapsed.
- The reader has compact Quick read / Bilingual deep read / Ask paper / Notes modes.

### Vocabulary

- Double-click an English word/phrase in the English summary or PDF.
- Offline Chinese translation/definition and US IPA.
- Add/edit/delete wordbook entries.
- Learning/mastered status and search/sort.
- Wordbook layout columns are word/US IPA, Chinese definition, and status.
- Context and paper-source columns were intentionally removed from the visible wordbook UI.

### Library and batch operations

- Hugging Face-inspired compact paper list and faceted sidebar; paper rows remain dense on desktop and narrow windows.
- Ratings are right-aligned, while authors and introductions are collapsed by default under an explicit details disclosure.
- Pure publication years and generic PDF metadata are not displayed as authors; future imports reject those values and conservatively infer a likely first-page author line.
- Search title, author, DOI, summary, and tags.
- Sort by recent import, rating, publication year, or title.
- Filter by custom tag, summary status, and rating.
- Custom tags are displayed above summary status.
- Active nonzero tags can be clicked again to clear; `All papers` also clears the tag filter.
- Zero-count tags display `0 papers` and cannot open an empty result accidentally.
- Multi-select and select all current results.
- Batch quick-read analysis is submitted to the persistent backend queue in one request rather than browser-side serial state.
- Batch add/remove tags.
- Batch set 0-3 star rating.
- Batch delete moves papers to the recoverable recycle bin without removing PDFs, assets, summaries, vocabulary links, or annotations.
- Batch export Markdown summaries or JSON metadata.

### Native Windows desktop

- Native resizable window backed by the installed Microsoft Edge WebView2 Runtime.
- Desktop-owned localhost backend on an ephemeral port with clean shutdown.
- Native file chooser, working Markdown/JSON downloads, and external-link handling.
- Windows application icon and version metadata.
- Default app data under `%LOCALAPPDATA%\PaperVault`, separate from installation files.
- `PAPER_VAULT_DATA_DIR` and `--data-dir` override contract for migration and QA.
- macOS platform contract for WKWebView, Application Support data, and `.icns` icon.
- Windows `onedir` and optional `onefile` PyInstaller build modes.

## 7. Important Files

```text
backend/app.py
  HTTP server, API routing, upload lifecycle, batch actions, deduplication,
  summary execution, settings, translation, annotations, and static serving.

backend/database.py
  SQLite schema/migrations and all paper/tag/vocabulary/annotation/settings,
  analysis job/run, chunk-index, and recycle-bin queries.

backend/analysis.py
  Quick-read, figure-analysis, stable text-chunking, input hashing, and
  retrieval-constrained cited-question logic.

backend/llm.py
  Detailed summary prompt, OpenAI-compatible request, provider controls,
  structured response parsing, local fallback summary, and term alignment.

backend/pdf_parser.py
  PDF metadata/text extraction, preview rendering, word coordinates,
  publication-year inference, and visual page extraction.

backend/offline_translation.py
  Offline translation model loading, glossary lookup, and definition enrichment.

backend/pronunciation.py
  US IPA generation.

frontend/index.html
  Library, reader, dialogs, wordbook, settings, batch toolbar, and popovers.

frontend/app.js
  Client state, API calls, library filtering, batch workflows, PDF reader,
  bilingual rendering/linkage, wordbook, annotations, and dialogs.

frontend/styles.css
  Complete design system and responsive layout.

tests/test_api.py
  API lifecycle, upload/dedup, facets, batch actions, annotations,
  translation, vocabulary, range requests, settings, and cleanup.

tests/test_analysis.py
  Legacy-schema migration, restart recovery, chunk retrieval, analysis
  versions, citation filtering, unavailable QA state, and recycle restore.

tests/test_llm.py
  Summary parsing, glossary/IPA behavior, DeepSeek provider controls,
  alignment preservation, and publication-year inference.

desktop/app.py
  Native window, desktop bridge, and localhost backend lifecycle.

desktop/platforms.py
  Windows/macOS/Linux data, renderer, storage, resource, and icon contracts.

tests/test_desktop.py
  Desktop paths, macOS compatibility contract, bridge, version, and server lifecycle.

build-windows.ps1
  Reproducible PyInstaller packaging for the Windows desktop bundle.

docs/DESKTOP.md
  Desktop development, packaging, data-directory, and platform documentation.
```

## 8. Main API Surface

```text
GET    /api/health
GET    /api/papers
POST   /api/papers
GET    /api/papers/{id}
PUT    /api/papers/{id}
DELETE /api/papers/{id}
POST   /api/papers/{id}/generate-summary
POST   /api/papers/batch

GET    /api/papers/{id}/analyses
POST   /api/papers/{id}/analyses/quick-read
POST   /api/papers/{id}/analyses/figure-analysis
GET    /api/analysis-jobs
GET    /api/analysis-jobs/{id}
POST   /api/analysis-jobs/batch
POST   /api/analysis-jobs/{id}/retry
GET    /api/search/chunks
POST   /api/qa
GET    /api/trash
POST   /api/trash/{id}/restore

GET    /api/papers/{id}/file
GET    /api/papers/{id}/assets/{filename}
GET    /api/papers/{id}/pages/{page}.png
GET    /api/papers/{id}/pages/{page}/text

GET    /api/papers/{id}/annotations
POST   /api/papers/{id}/annotations
PUT    /api/annotations/{id}
DELETE /api/annotations/{id}

GET    /api/tags
POST   /api/tags
PUT    /api/tags/{id}
DELETE /api/tags/{id}

GET    /api/vocabulary
POST   /api/vocabulary
PUT    /api/vocabulary/{id}
DELETE /api/vocabulary/{id}
POST   /api/translate

GET    /api/settings
PUT    /api/settings
```

`POST /api/papers/batch` accepts at most 500 unique paper IDs and supports `add_tags`, `remove_tags`, `set_rating`, and recoverable `delete`. `POST /api/analysis-jobs/batch` accepts the selected paper IDs and creates persistent quick-read or figure-analysis jobs.

## 9. Verification

Run:

```powershell
node --check frontend\app.js
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Last verification result:

```text
33 tests passed
JavaScript syntax check passed
Windows PyInstaller `onedir` build passed
Packaged EXE health check returned version 2.0.1 using a temporary data directory
Desktop and 390px-wide browser interaction and screenshot QA passed against temporary data
Connected/cancelable highlights, cropped figure/table assets, bilingual sentence alignment, compact library rows, and invalid-author fallback were exercised
No browser console warnings or errors in the final check
```

The API tests use a temporary data directory and do not alter the real library.

## 10. Operational Notes

- The backend only binds to `127.0.0.1` by default.
- The service must be restarted after Python changes. HTML/CSS/JS changes require a page reload.
- Prefer `start.ps1` or `start.bat`; they use the project `.venv`.
- Prefer `start-desktop.ps1` for native Windows development and `build-windows.ps1` for packaging.
- The Windows desktop app requires the Microsoft Edge WebView2 Runtime.
- System Python may not contain `pypdf`, so tests should always use `.\.venv\Scripts\python.exe`.
- `PaperVaultHandler.log_message` guards against `sys.stdout is None`, allowing a future/no-console Windows process to serve requests correctly.
- Large LLM requests depend on VPN/network/API balance. Friendly errors distinguish invalid key, insufficient balance, timeout, and unreachable model service.
- Do not expose the API key in handoff notes, screenshots, logs, tests, or commits.

## 11. Known Boundaries and Next Priorities

These are not regressions, but they are the most useful next development areas:

1. Add a signed Windows installer, code signing, and an update delivery strategy around the verified portable desktop bundle.
2. Move detailed bilingual deep-read generation into the persistent job/run model while preserving the current `summary_pairs` compatibility contract.
3. Optimize large libraries: add lightweight list projections and pagination/virtualization instead of returning complete summary pairs.
4. Add automated frontend interaction coverage for the four reader modes and source-page navigation.
5. Add explicit backup/restore and recycle-bin management UI for the entire `data` directory.
6. Add optional local OCR for scanned PDFs before indexing; never synthesize evidence for pages without extractable text.

## 12. Prompt for the Next Conversation

Use this as the first message in the next development conversation:

```text
请先完整阅读：
C:\Users\skywu\Documents\Codex\PaperVault\HANDOFF.md

继续开发 PaperVault 2.0.1。先检查 Git 工作区、桌面构建状态和当前服务进程，不要重置、迁移或覆盖任何真实 data 目录，也不要输出或复制 API Key。真实库最近只读观察为 9 条记录，不要按旧交接强制改回 4 条。沿用现有 `/api/*`、持久化分析任务、pywebview 平台边界、Hugging Face 风格 UI 和测试方式，然后处理我接下来提出的需求。
```
