# PaperVault Development Handoff

Updated: 2026-08-08 (Asia/Shanghai)

## 1. Start Here

Project root:

```text
C:\Users\skywu\Documents\Codex\2026-08-07\f-d-s-f\outputs\paper-vault
```

Local URL:

```text
http://127.0.0.1:8765
```

Current health response:

```json
{"status":"ok","version":"1.8.0"}
```

Preferred startup on Windows:

```powershell
.\start.ps1
```

Or double-click `start.bat`. Manual startup:

```powershell
.\.venv\Scripts\python.exe -m backend.app
```

The service is currently running. Before starting another instance, check whether port `8765` is already listening.

## 2. Product Scope

PaperVault is a Windows-local paper knowledge base with a browser UI. It stores all PDFs, metadata, bilingual summaries, tags, ratings, vocabulary, extracted figures, and PDF annotations locally.

The longer-term direction is a Windows desktop client. The current frontend/backend separation and localhost HTTP API are intentionally compatible with a future WebView2, Tauri, or packaged Python desktop shell.

## 3. Technology

- Backend: Python standard-library `ThreadingHTTPServer` and JSON/Multipart APIs.
- Database: SQLite (`data/paper-vault.db`).
- Frontend: vanilla HTML, CSS, and JavaScript. There is no frontend build step.
- PDF parsing: `pypdf`.
- PDF page rendering, text coordinates, and figure extraction: `PyMuPDF`.
- Offline English-to-Chinese lookup: CTranslate2/Argos-compatible local model plus an academic glossary.
- Pronunciation: `cmudict` with local fallback logic.
- LLM: configurable OpenAI-compatible chat-completions adapter.

Dependencies are pinned by range in `requirements.txt`.

## 4. Data and Secrets

Local persistent data:

```text
data/
  paper-vault.db       SQLite records and settings
  uploads/             Original PDF files
  assets/              Extracted paper page/figure images
  models/              Offline translation models
```

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

There are 4 papers, all with usable summaries:

1. `FireRedASR: Open-Source Industrial-Grade`
2. `A Two-Stage Hierarchical Deep Filtering Framework for Real-Time Speech Enhancement`
3. `F5-TTS: A Fairytaler that Fakes Fluent`
4. `Contextual Biasing for LLM-Based ASR with Hotword Retrieval and Reinforcement Learning`

Current facets:

```text
summary: ready 4, error 0, pending 0
rating: 0 stars 4, 1/2/3 stars 0
```

Custom tags exist but are not assigned to any paper:

```text
ASR: 0 papers
hotword: 0 papers
speechLLM: 0 papers
WKS: 0 papers
```

The zero counts are real data, not a counting error. Zero-count tags are disabled in the filter sidebar until a paper is assigned through paper editing or batch tagging.

## 6. Implemented Features

### Paper ingestion and persistence

- Single and multi-PDF import.
- Per-file batch import queue with success/error states and retry of failed files.
- PDF title, author, year, page count, text, and visual-page extraction.
- Optional common tags and automatic summary generation during batch import.
- Local SQLite persistence across restarts.
- Duplicate consolidation by normalized PDF paper title.
- A newer successful duplicate replaces older failed/poorer records while preserving the union of tags and the highest rating.
- Duplicate PDF files and extracted assets are removed from disk.

### Detailed bilingual summary

- Configurable OpenAI-compatible LLM.
- DeepSeek official API currently active.
- Structured 40-55 sentence-pair report organized into paper information, background, architecture, data/training, experiments, quantitative results, conclusions, limitations, and innovations.
- English and Chinese are stored as aligned sentence pairs.
- Markdown-like document rendering with sections, numbered statements, evidence page references, and inline figures.
- Clicking either language sentence highlights and scrolls to the corresponding sentence.
- Clicking a summary figure synchronizes the corresponding figure in the other language and the source PDF page.
- Chinese summary can be exported as Markdown.
- Existing summaries are preserved if a regeneration request fails.
- Latest typography fix: decimal/scientific/numeric values such as `0.91`, `5e-5`, and `1.2M` inherit the exact body font size, weight, and line height instead of being rendered as bold keywords.

### PDF reader and notes

- Local PDF page renderer instead of an embedded browser PDF plugin.
- Page navigation, zoom, fit width, and trackpad `Ctrl`/pinch-style zoom scoped to the PDF panel.
- Text selection and double-click word translation.
- Persistent PDF highlights with four colors.
- Optional annotation notes.
- Three reader panels can be independently collapsed.

### Vocabulary

- Double-click an English word/phrase in the English summary or PDF.
- Offline Chinese translation/definition and US IPA.
- Add/edit/delete wordbook entries.
- Learning/mastered status and search/sort.
- Wordbook layout columns are word/US IPA, Chinese definition, and status.
- Context and paper-source columns were intentionally removed from the visible wordbook UI.

### Library and batch operations

- Hugging Face-inspired dense paper list and faceted sidebar.
- Search title, author, DOI, summary, and tags.
- Sort by recent import, rating, publication year, or title.
- Filter by custom tag, summary status, and rating.
- Custom tags are displayed above summary status.
- Active nonzero tags can be clicked again to clear; `All papers` also clears the tag filter.
- Zero-count tags display `0 papers` and cannot open an empty result accidentally.
- Multi-select and select all current results.
- Batch generate/regenerate summaries sequentially; a single failure does not stop the queue and failed items remain selected.
- Batch add/remove tags.
- Batch set 0-3 star rating.
- Batch delete papers, PDFs, extracted assets, and annotations.
- Batch export Markdown summaries or JSON metadata.

## 7. Important Files

```text
backend/app.py
  HTTP server, API routing, upload lifecycle, batch actions, deduplication,
  summary execution, settings, translation, annotations, and static serving.

backend/database.py
  SQLite schema/migrations and all paper/tag/vocabulary/annotation/settings queries.

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

tests/test_llm.py
  Summary parsing, glossary/IPA behavior, DeepSeek provider controls,
  alignment preservation, and publication-year inference.
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

`POST /api/papers/batch` accepts at most 500 unique paper IDs and supports `add_tags`, `remove_tags`, `set_rating`, and `delete`. Batch summary generation is intentionally scheduled client-side, one paper at a time, so progress and per-paper failures remain visible.

## 9. Verification

Run:

```powershell
node --check frontend\app.js
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Last verification result:

```text
13 tests passed
JavaScript syntax check passed
Desktop browser interaction and screenshot QA passed
No browser console warnings or errors in the final check
```

The API tests use a temporary data directory and do not alter the real library.

## 10. Operational Notes

- The backend only binds to `127.0.0.1` by default.
- The service must be restarted after Python changes. HTML/CSS/JS changes require a page reload.
- Prefer `start.ps1` or `start.bat`; they use the project `.venv`.
- System Python may not contain `pypdf`, so tests should always use `.\.venv\Scripts\python.exe`.
- `PaperVaultHandler.log_message` guards against `sys.stdout is None`, allowing a future/no-console Windows process to serve requests correctly.
- Large LLM requests depend on VPN/network/API balance. Friendly errors distinguish invalid key, insufficient balance, timeout, and unreachable model service.
- Do not expose the API key in handoff notes, screenshots, logs, tests, or commits.

## 11. Known Boundaries and Next Priorities

These are not regressions, but they are the most useful next development areas:

1. Package the app as a Windows desktop client using WebView2/Tauri or a Python desktop wrapper, while continuing to reuse the current local HTTP API and data directory.
2. Move long-running LLM summaries into a persistent backend job queue so work survives a browser refresh or desktop-client restart.
3. Optimize large libraries: the list API currently includes complete summary pairs, which will become unnecessarily heavy with hundreds of papers. Add lightweight list projections and pagination/virtualization.
4. Add automated frontend interaction tests for tag toggle, select-all, batch dialogs, sentence linkage, and numeric typography.
5. Add explicit backup/restore UI for the entire `data` directory.
6. Add optional automatic tag suggestions, but do not silently assign user tags without confirmation.

## 12. Prompt for the Next Conversation

Use this as the first message in the next development conversation:

```text
请先完整阅读：
C:\Users\skywu\Documents\Codex\2026-08-07\f-d-s-f\outputs\paper-vault\HANDOFF.md

继续开发 PaperVault。先检查 http://127.0.0.1:8765/api/health、当前服务进程和工作区代码，不要重置或覆盖 data 目录，也不要输出 API Key。沿用现有前后端结构、UI 风格和测试方式，然后处理我接下来提出的需求。
```
