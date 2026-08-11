"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const CURRENT_FIGURE_PROMPT_VERSION = "figure-analysis-v2";

const state = {
  papers: [],
  tags: [],
  settings: {},
  query: "",
  tagId: "",
  summaryFilter: "all",
  ratingFilter: "all",
  librarySort: "recent",
  libraryFacets: { total: 0, summary: {}, rating: {} },
  selectedPaperIds: new Set(),
  batchRunning: false,
  currentPaper: null,
  readerMode: "quick",
  analysisRuns: [],
  analysisJobs: [],
  analysisSelections: { quick_read: null, figure_analysis: null },
  analysisPollTimer: null,
  qaResult: null,
  qaError: "",
  qaBusy: false,
  activePair: null,
  pairClickTimer: null,
  vocabulary: [],
  vocabularyQuery: "",
  vocabularyStatus: "all",
  vocabularySort: "recent",
  vocabularyCandidate: null,
  vocabularyDraft: null,
  editingVocabularyId: null,
  pdfPaperId: null,
  pdfPageCount: 0,
  pdfCurrentPage: 1,
  pdfZoom: 1,
  pdfObserver: null,
  pdfScrollFrame: null,
  pendingPdfPage: null,
  pdfAnnotations: [],
  pdfSelection: null,
  pdfSelectionTimer: null,
  pdfPointerSelection: null,
  pdfSuppressClickUntil: 0,
  editingAnnotationId: null,
  editingAnnotationColor: "yellow",
  activeVisualAssetKey: null,
  panelCollapsed: { pdf: false, english: false, chinese: false },
};

const statusLabels = {
  pending: "待生成",
  generating: "生成中",
  ready: "AI 摘要",
  english_ready: "英文完成",
  translating: "中文翻译中",
  translation_error: "英文完成，中文翻译失败",
  draft: "离线索引",
  edited: "人工编辑",
  error: "生成失败",
};

async function activateDesktopRuntime() {
  if (!window.pywebview) return;
  document.documentElement.classList.add("desktop-runtime");
  const badge = $("#runtimeBadge");
  badge.textContent = "DESKTOP";
  badge.classList.add("desktop-badge");
  try {
    const info = await window.pywebview.api.app_info();
    document.documentElement.dataset.platform = info.platform || "desktop";
    badge.title = `${info.name} ${info.version}`;
  } catch (_) {
    document.documentElement.dataset.platform = "desktop";
  }
}

window.addEventListener("pywebviewready", activateDesktopRuntime);

function summarySource(paper) {
  if (["generating", "english_ready", "translating", "translation_error", "error"].includes(paper.summary_status)) {
    return statusLabels[paper.summary_status];
  }
  if (paper.summary_provider === "openai_compatible") {
    return paper.summary_model ? `AI · ${paper.summary_model}` : "AI 摘要";
  }
  if (paper.summary_provider === "curated") return "精校摘要";
  if (paper.summary_provider === "manual") return "人工编辑";
  if (paper.summary_provider === "local") return "离线索引";
  return statusLabels[paper.summary_status] || "待生成";
}

function make(tagName, className = "", text = "") {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text !== "") node.textContent = text;
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || `请求失败 (${response.status})`);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function toast(message, type = "success") {
  const item = make("div", `toast ${type === "error" ? "error" : ""}`, message);
  $("#toastRegion").append(item);
  window.setTimeout(() => item.remove(), 4200);
}

function setBusy(active, text = "正在处理...") {
  $("#busyText").textContent = text;
  $("#busyOverlay").hidden = !active;
}

function openDialog(id) {
  const dialog = document.getElementById(id);
  if (!dialog.open) dialog.showModal();
}

function closeDialog(id) {
  const dialog = document.getElementById(id);
  if (dialog.open) dialog.close();
}

function formatBytes(bytes) {
  if (!bytes) return "0 KB";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric" }).format(new Date(value));
}

function paperSnippet(paper) {
  const blocks = (paper.summary_blocks || []).filter((block) => block.type !== "heading");
  if (blocks.length) {
    const preferred = blocks.find((block) => block.text_zh) || blocks[0];
    return preferred.text_zh || preferred.text_en || "";
  }
  const pairs = paper.summary_pairs || [];
  if (!pairs.length) return paper.summary_error || "尚未生成摘要";
  const preferred = pairs.find((pair) => pair.zh);
  return preferred?.zh || pairs[0].en || "";
}

function paperAuthors(paper) {
  const authors = String(paper.authors || "").trim();
  if (!authors) return "";
  if (/^(?:(?:19|20)\d{2}[\s,;/·-]*)+$/.test(authors)) return "";
  if (/^(?:anonymous|unknown|admin(?:istrator)?|microsoft word|latex|arxiv)$/i.test(authors)) return "";
  if (/https?:\/\/|\bdoi\b|@/i.test(authors)) return "";
  return authors;
}

function paperAuthorDetails(paper) {
  return [
    paperAuthors(paper) || "作者信息未识别",
    paper.publication_year,
  ].filter(Boolean).join(" · ");
}

function latestPaperJob(paper) {
  const jobs = Object.values(paper.analysis_jobs || {});
  return jobs.sort((left, right) => String(right.updated_at).localeCompare(String(left.updated_at)))[0] || null;
}

function jobStatusLabel(job) {
  if (!job) return "";
  const type = job.job_type === "figure_analysis" ? "图表" : "速读";
  const status = {
    queued: "排队中",
    running: "分析中",
    succeeded: "已完成",
    failed: "失败",
  }[job.status] || job.status;
  return `${type} · ${status}`;
}

function tagChip(tag) {
  const chip = make("span", "tag-chip", tag.name);
  chip.style.setProperty("--chip-color", tag.color);
  return chip;
}

async function loadLibrary() {
  const params = new URLSearchParams();
  if (state.query) params.set("q", state.query);
  if (state.tagId) params.set("tag", state.tagId);
  if (state.summaryFilter !== "all") params.set("summary", state.summaryFilter);
  if (state.ratingFilter !== "all") params.set("rating", state.ratingFilter);
  params.set("sort", state.librarySort);
  const data = await api(`/api/papers?${params}`);
  state.papers = data.papers;
  state.libraryFacets = data.facets || state.libraryFacets;
  renderLibrary();
}

async function loadTags() {
  const data = await api("/api/tags");
  state.tags = data.tags;
  renderTagFilters();
  renderTagPickers();
  renderTagManager();
}

async function loadSettings() {
  const data = await api("/api/settings");
  state.settings = data.settings;
  updateProviderHint();
  renderQaPanel();
}

async function loadVocabulary() {
  const data = await api("/api/vocabulary");
  state.vocabulary = data.entries || [];
  renderVocabulary();
}

function renderLibrary() {
  const list = $("#paperList");
  list.replaceChildren();
  const total = Number(state.libraryFacets.total) || 0;
  $("#totalCount").textContent = total;
  $("#allPaperCount").textContent = total;
  $("#libraryTotalInline").textContent = total;
  const activeTag = state.tags.find((tag) => tag.id === state.tagId);
  $("#resultText").textContent = `${state.papers.length} 篇论文`;
  const filterLabels = [];
  if (state.query) filterLabels.push(`“${state.query}”`);
  if (activeTag) filterLabels.push(activeTag.name);
  if (state.summaryFilter !== "all") {
    filterLabels.push({ ready: "已有摘要", error: "生成失败", pending: "待处理" }[state.summaryFilter]);
  }
  if (state.ratingFilter !== "all") {
    filterLabels.push(state.ratingFilter === "0" ? "未推荐" : `${state.ratingFilter} 星推荐`);
  }
  $("#activeFilterLabel").textContent = filterLabels.filter(Boolean).join(" · ");
  $("#resetFiltersButton").hidden = filterLabels.length === 0;
  renderLibraryFacets();

  for (const paper of state.papers) {
    const selected = state.selectedPaperIds.has(paper.id);
    const row = make("article", `paper-row ${selected ? "selected" : ""} ${paper.read_state === "unread" ? "unread" : ""}`.trim());
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-label", `打开 ${paper.title}`);
    row.addEventListener("click", (event) => {
      if (event.target.closest("button, input, label, details, summary")) return;
      navigateToPaper(paper.id);
    });
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        navigateToPaper(paper.id);
      }
    });

    const checkboxLabel = make("label", "paper-checkbox");
    checkboxLabel.title = `选择《${paper.title}》`;
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selected;
    checkbox.setAttribute("aria-label", `选择《${paper.title}》`);
    checkbox.addEventListener("change", () => togglePaperSelection(paper.id, checkbox.checked));
    checkboxLabel.append(checkbox, make("span"));

    const content = make("div", "paper-main");
    const titleLine = make("div", "paper-title-line");
    const readButton = make("button", `read-state-button ${paper.read_state === "read" ? "read" : ""}`, paper.read_state === "read" ? "●" : "○");
    readButton.type = "button";
    readButton.title = paper.read_state === "read" ? "标记为未读" : "标记为已读";
    readButton.setAttribute("aria-label", readButton.title);
    readButton.addEventListener("click", (event) => {
      event.stopPropagation();
      setPaperReadState(paper.id, paper.read_state === "read" ? "unread" : "read").catch(handleError);
    });
    titleLine.append(make("span", "paper-type-mark", "PDF"), make("h2", "paper-title", paper.title), readButton);
    content.append(titleLine);
    const details = make("details", "paper-details");
    const detailsSummary = make("summary", "paper-details-toggle", "作者总结与简介");
    details.append(
      detailsSummary,
      make("p", "paper-authors", paperAuthorDetails(paper)),
      make("p", "paper-snippet", paperSnippet(paper)),
    );
    content.append(details);
    const classification = make("div", "paper-tags paper-classification");
    paper.tags.forEach((tag) => classification.append(tagChip(tag)));
    if (paper.tags.length) content.append(classification);

    const facts = make("div", "paper-facts");
    const statusLine = make("div", "paper-fact-line paper-status-line");
    statusLine.append(ratingControl(paper, "paper-row-rating"));
    const summaryState = make("span", `summary-state ${paper.summary_status || "pending"}`, statusLabels[paper.summary_status] || "待生成");
    statusLine.append(summaryState);
    const latestJob = latestPaperJob(paper);
    if (latestJob) statusLine.append(make("span", `analysis-state ${latestJob.status}`, jobStatusLabel(latestJob)));
    const metrics = make("div", "paper-fact-line paper-metrics");
    const pages = make("span");
    pages.append(make("strong", "", `${paper.page_count || "-"}`), document.createTextNode(" 页"));
    metrics.append(
      pages,
      make("span", "", formatBytes(paper.file_size)),
      make("span", "paper-added-date", `入库 ${formatDate(paper.created_at)}`),
    );
    facts.append(statusLine, metrics);
    row.append(checkboxLabel, content, facts, make("span", "row-arrow", "→"));
    list.append(row);
  }

  const visibleIds = state.papers.map((paper) => paper.id);
  const selectedVisible = visibleIds.filter((id) => state.selectedPaperIds.has(id)).length;
  const selectAll = $("#selectAllPapers");
  selectAll.checked = visibleIds.length > 0 && selectedVisible === visibleIds.length;
  selectAll.indeterminate = selectedVisible > 0 && selectedVisible < visibleIds.length;
  $("#paperListHeader").hidden = state.papers.length === 0;
  updateBatchToolbar();

  const empty = $("#emptyState");
  empty.hidden = state.papers.length !== 0;
  list.hidden = state.papers.length === 0;
  if (state.papers.length === 0) {
    const filtering = Boolean(
      state.query || state.tagId || state.summaryFilter !== "all" || state.ratingFilter !== "all"
    );
    $("#emptyTitle").textContent = filtering ? "没有匹配的论文" : "还没有论文";
    $("#emptyText").textContent = filtering ? "调整关键词或标签筛选后再试。" : "上传第一篇 PDF，建立你的本地研究资料库。";
    $("#emptyUploadButton").hidden = filtering;
  }
}

async function setPaperReadState(paperId, readState, render = true) {
  const result = await api(`/api/papers/${paperId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ read_state: readState }),
  });
  const index = state.papers.findIndex((paper) => paper.id === paperId);
  if (index >= 0) state.papers[index] = result.paper;
  if (state.currentPaper?.id === paperId) state.currentPaper.read_state = readState;
  if (render) renderLibrary();
  return result.paper;
}

function renderLibraryFacets() {
  const summary = state.libraryFacets.summary || {};
  const rating = state.libraryFacets.rating || {};
  $("#summaryAllCount").textContent = state.libraryFacets.total || 0;
  $("#summaryReadyCount").textContent = summary.ready || 0;
  $("#summaryErrorCount").textContent = summary.error || 0;
  $("#summaryPendingCount").textContent = summary.pending || 0;
  for (let value = 0; value <= 3; value += 1) {
    $(`#rating${value}Count`).textContent = rating[String(value)] || 0;
  }
  $$('[data-summary-filter]').forEach((button) => {
    button.classList.toggle("active", button.dataset.summaryFilter === state.summaryFilter);
  });
  $$('[data-rating-filter]').forEach((button) => {
    button.classList.toggle("active", button.dataset.ratingFilter === state.ratingFilter);
  });
}

function togglePaperSelection(paperId, selected) {
  if (selected) state.selectedPaperIds.add(paperId);
  else state.selectedPaperIds.delete(paperId);
  renderLibrary();
}

function clearPaperSelection(render = true) {
  state.selectedPaperIds.clear();
  if (render) renderLibrary();
}

function selectedPapers() {
  return state.papers.filter((paper) => state.selectedPaperIds.has(paper.id));
}

function updateBatchToolbar() {
  const count = state.selectedPaperIds.size;
  $("#batchSelectedCount").textContent = count;
  $("#batchToolbar").hidden = count === 0;
  $$("button", $("#batchToolbar")).forEach((control) => {
    control.disabled = state.batchRunning;
  });
  $$("summary", $("#batchToolbar")).forEach((control) => {
    control.setAttribute("aria-disabled", String(state.batchRunning));
  });
}

function ratingControl(paper, extraClass = "") {
  const control = make("div", `rating-control ${extraClass}`.trim());
  control.dataset.paperId = paper.id;
  control.setAttribute("role", "group");
  control.setAttribute("aria-label", `《${paper.title}》推荐度`);
  const currentRating = Number(paper.rating) || 0;
  for (let value = 1; value <= 3; value += 1) {
    const button = make("button", value <= currentRating ? "active" : "", "★");
    button.type = "button";
    button.title = `${value} 星推荐`;
    button.setAttribute("aria-label", `${value} 星推荐`);
    button.setAttribute("aria-pressed", String(value <= currentRating));
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      setPaperRating(paper.id, currentRating === value ? 0 : value).catch(handleError);
    });
    control.append(button);
  }
  return control;
}

async function setPaperRating(paperId, rating) {
  const result = await api(`/api/papers/${paperId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rating }),
  });
  const index = state.papers.findIndex((paper) => paper.id === paperId);
  if (index >= 0) state.papers[index] = result.paper;
  if (state.currentPaper?.id === paperId) {
    state.currentPaper = result.paper;
    renderReaderIdentity(result.paper);
  }
  renderLibrary();
}

function batchPaperIds() {
  return selectedPapers().map((paper) => paper.id);
}

function setBatchProgress(title, current, total, detail = "") {
  const panel = $("#batchProgress");
  panel.hidden = false;
  $("#batchProgressTitle").textContent = title;
  $("#batchProgressText").textContent = detail || `${current} / ${total}`;
  const progress = $("#batchProgressBar");
  progress.max = Math.max(1, total);
  progress.value = current;
}

function finishBatchProgress(title, detail) {
  setBatchProgress(title, 1, 1, detail);
  window.setTimeout(() => {
    if (!state.batchRunning) $("#batchProgress").hidden = true;
  }, 2600);
}

function closeBatchMenus() {
  $$(".batch-menu[open]").forEach((menu) => menu.removeAttribute("open"));
}

async function runBatchMutation(action, payload, successMessage) {
  const paperIds = batchPaperIds();
  if (!paperIds.length || state.batchRunning) return;
  state.batchRunning = true;
  closeBatchMenus();
  setBatchProgress("正在更新论文", 0, 1, `处理 ${paperIds.length} 篇论文`);
  updateBatchToolbar();
  try {
    const result = await api("/api/papers/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paper_ids: paperIds, action, ...payload }),
    });
    clearPaperSelection(false);
    await Promise.all([loadLibrary(), loadTags()]);
    finishBatchProgress("批量操作完成", `已处理 ${result.processed_count} 篇论文`);
    toast(successMessage.replace("{count}", String(result.processed_count)));
  } catch (error) {
    $("#batchProgress").hidden = true;
    handleError(error);
  } finally {
    state.batchRunning = false;
    updateBatchToolbar();
  }
}

async function queueSelectedQuickReads() {
  const papers = selectedPapers();
  if (!papers.length || state.batchRunning) return;
  const accepted = await confirmAction(
    "批量速读分析",
    `将为所选 ${papers.length} 篇论文创建后台任务；关闭页面或重启服务后任务记录仍会保留。`,
  );
  if (!accepted) return;
  state.batchRunning = true;
  updateBatchToolbar();
  closeBatchMenus();
  setBatchProgress("正在创建速读任务", 0, papers.length, `${papers.length} 篇论文`);
  try {
    const result = await api("/api/analysis-jobs/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paper_ids: papers.map((paper) => paper.id), job_type: "quick_read" }),
    });
    clearPaperSelection(false);
    await loadLibrary();
    finishBatchProgress("速读任务已提交", `后台任务 ${result.queued_count} 项`);
    toast(`已提交 ${result.queued_count} 项速读任务`);
  } catch (error) {
    $("#batchProgress").hidden = true;
    handleError(error);
  } finally {
    state.batchRunning = false;
    updateBatchToolbar();
  }
}

function showBatchTagsDialog() {
  const count = batchPaperIds().length;
  if (!count) return;
  $("#batchTagsPaperCount").textContent = count;
  $("#batchTagsForm").reset();
  renderTagPickers();
  openDialog("batchTagsDialog");
}

async function handleBatchTags(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const tagIds = $$("#batchTagPicker input:checked").map((input) => input.value);
  if (!tagIds.length) {
    toast("请至少选择一个标签", "error");
    return;
  }
  const adding = form.elements.mode.value === "add";
  closeDialog("batchTagsDialog");
  await runBatchMutation(
    adding ? "add_tags" : "remove_tags",
    { tag_ids: tagIds },
    adding ? "已为 {count} 篇论文添加标签" : "已从 {count} 篇论文移除标签",
  );
}

async function setSelectedRating(rating) {
  await runBatchMutation("set_rating", { rating }, `已更新 {count} 篇论文的推荐度`);
}

async function deleteSelectedPapers() {
  const count = batchPaperIds().length;
  if (!count) return;
  const accepted = await confirmAction(
    "批量删除论文",
    `所选 ${count} 篇论文将移入回收站，PDF、摘要、图表和批注会原样保留。`,
  );
  if (!accepted) return;
  await runBatchMutation("delete", {}, `已将 {count} 篇论文移入回收站`);
}

function exportSelectedPapers(format) {
  const papers = selectedPapers();
  if (!papers.length) return;
  closeBatchMenus();
  if (format === "json") {
    const exported = papers.map((paper) => ({
      title: paper.title,
      authors: paper.authors,
      publication_year: paper.publication_year,
      doi: paper.doi,
      rating: paper.rating,
      tags: paper.tags.map((tag) => tag.name),
      summary_status: paper.summary_status,
      summary_paper_title: paper.summary_paper_title,
      summary_blocks: paper.summary_blocks,
      summary_pairs: paper.summary_pairs,
    }));
    downloadTextFile(
      `paper-vault-${new Date().toISOString().slice(0, 10)}.json`,
      `${JSON.stringify(exported, null, 2)}\n`,
      "application/json;charset=utf-8",
    );
  } else {
    const lines = ["# PaperVault 论文摘要导出", "", `> 共 ${papers.length} 篇论文`, ""];
    papers.forEach((paper, paperIndex) => {
      lines.push(`## ${paperIndex + 1}. ${paper.title}`, "");
      const metadata = [paper.authors, paper.publication_year, paper.doi].filter(Boolean).join(" · ");
      if (metadata) lines.push(`> ${metadata}`, "");
      if (paper.tags.length) lines.push(`标签：${paper.tags.map((tag) => `\`${tag.name}\``).join(" ")}`, "");
      if (paper.summary_blocks?.length) {
        lines.push("### 中文完整总结", "", ...summaryBlocksToMarkdown(paper.summary_blocks, "zh", 1));
        lines.push("", "### Complete English Report", "", ...summaryBlocksToMarkdown(paper.summary_blocks, "en", 1));
      } else {
        lines.push("### 中文摘要", "");
        lines.push(...(paper.summary_pairs || []).map((pair) => `- ${pair.zh || pair.en}`).filter((line) => line !== "- "));
        lines.push("", "### English Summary", "");
        lines.push(...(paper.summary_pairs || []).map((pair) => `- ${pair.en || pair.zh}`).filter((line) => line !== "- "));
      }
      lines.push("", "---", "");
    });
    downloadTextFile(
      `paper-vault-${new Date().toISOString().slice(0, 10)}.md`,
      `${lines.join("\n")}\n`,
      "text/markdown;charset=utf-8",
    );
  }
  toast(`已导出 ${papers.length} 篇论文`);
}

function summaryBlocksToMarkdown(blocks, language, headingDepthOffset = 0) {
  const key = language === "zh" ? "text_zh" : "text_en";
  const fallbackKey = language === "zh" ? "text_en" : "text_zh";
  const lines = [];
  for (const block of blocks || []) {
    const text = String(block[key] || block[fallbackKey] || "").trim();
    if (!text) continue;
    const refs = Array.isArray(block.page_refs) && block.page_refs.length
      ? ` _(p. ${block.page_refs.join(", ")})_`
      : "";
    if (block.type === "heading") {
      const depth = Math.min(6, Math.max(2, Number(block.level) || 2) + headingDepthOffset);
      if (lines.length && lines.at(-1) !== "") lines.push("");
      lines.push(`${"#".repeat(depth)} ${text}${refs}`, "");
    } else if (block.type === "bullet") {
      lines.push(`- ${markdownEmphasis(text)}${refs}`);
    } else {
      lines.push(`${markdownEmphasis(text)}${refs}`, "");
    }
  }
  while (lines.at(-1) === "") lines.pop();
  return lines;
}

function downloadTextFile(filename, content, type) {
  const blobUrl = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = blobUrl;
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
}

function renderTagFilters() {
  const container = $("#tagFilters");
  container.replaceChildren();
  for (const tag of state.tags) {
    const active = state.tagId === tag.id;
    const count = Number(tag.paper_count) || 0;
    const button = make("button", `filter-item ${active ? "active" : ""}`);
    button.type = "button";
    button.dataset.tagId = tag.id;
    button.disabled = count === 0 && !active;
    button.setAttribute("aria-pressed", String(active));
    button.title = active ? `取消“${tag.name}”筛选` : count ? `筛选“${tag.name}”标签` : `暂无论文使用“${tag.name}”标签`;
    const dot = make("span", "filter-dot");
    dot.style.setProperty("--tag-color", tag.color);
    button.append(dot, make("span", "", tag.name), make("span", `filter-count ${active ? "filter-clear" : ""}`, active ? "×" : `${count} 篇`));
    button.addEventListener("click", () => selectTag(tag.id));
    container.append(button);
  }
  const allButton = $(".filter-item[data-tag-id='']");
  allButton.classList.toggle("active", !state.tagId);
  allButton.setAttribute("aria-pressed", String(!state.tagId));
  allButton.onclick = () => selectTag("");
}

function renderTagPickers() {
  for (const picker of [$("#uploadTagPicker"), $("#editTagPicker"), $("#batchTagPicker")]) {
    if (!picker) continue;
    const selected = new Set($$("input:checked", picker).map((input) => input.value));
    picker.replaceChildren();
    for (const tag of state.tags) {
      const label = make("label", "tag-option");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = tag.id;
      input.checked = selected.has(tag.id);
      const visual = make("span", "", tag.name);
      visual.style.setProperty("--tag-option-color", tag.color);
      label.append(input, visual);
      picker.append(label);
    }
  }
}

function renderTagManager() {
  const container = $("#tagManagerList");
  container.replaceChildren();
  if (!state.tags.length) {
    container.append(make("div", "summary-empty", "暂无标签"));
    return;
  }
  for (const tag of state.tags) {
    const row = make("div", "tag-manager-row");
    const color = document.createElement("input");
    color.type = "color";
    color.value = tag.color;
    color.title = "标签颜色";
    const name = document.createElement("input");
    name.type = "text";
    name.value = tag.name;
    name.maxLength = 80;
    const count = make("span", "tag-manager-count", `${tag.paper_count} 篇`);
    const remove = make("button", "mini-icon", "×");
    remove.type = "button";
    remove.title = "删除标签";
    remove.setAttribute("aria-label", `删除标签 ${tag.name}`);
    const save = () => saveTag(tag.id, name.value, color.value);
    color.addEventListener("change", save);
    name.addEventListener("change", save);
    name.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        name.blur();
      }
    });
    remove.addEventListener("click", () => deleteTag(tag));
    row.append(color, name, count, remove);
    container.append(row);
  }
}

function renderVocabulary() {
  const allEntries = state.vocabulary;
  const learningCount = allEntries.filter((entry) => entry.status === "learning").length;
  const masteredCount = allEntries.filter((entry) => entry.status === "mastered").length;
  $("#vocabularyTotal").textContent = String(allEntries.length);
  $("#vocabularyAllCount").textContent = String(allEntries.length);
  $("#vocabularyLearningCount").textContent = String(learningCount);
  $("#vocabularyMasteredCount").textContent = String(masteredCount);
  $$("[data-vocabulary-status]").forEach((button) => {
    button.classList.toggle("active", button.dataset.vocabularyStatus === state.vocabularyStatus);
  });

  const query = state.vocabularyQuery.toLocaleLowerCase();
  const entries = allEntries.filter((entry) => {
    if (state.vocabularyStatus !== "all" && entry.status !== state.vocabularyStatus) return false;
    if (!query) return true;
    return [
      entry.term_en, entry.phonetic_us, entry.translation_zh,
    ].some((value) => String(value || "").toLocaleLowerCase().includes(query));
  });
  entries.sort((left, right) => {
    if (state.vocabularySort === "alpha") return left.term_en.localeCompare(right.term_en, "en");
    if (state.vocabularySort === "oldest") return left.created_at.localeCompare(right.created_at);
    return right.updated_at.localeCompare(left.updated_at);
  });

  const list = $("#vocabularyList");
  list.replaceChildren();
  for (const entry of entries) list.append(vocabularyRow(entry));
  const empty = $("#vocabularyEmpty");
  empty.hidden = entries.length !== 0;
  list.hidden = entries.length === 0;
  $("#vocabularyColumns").hidden = entries.length === 0;
  $("h3", empty).textContent = allEntries.length ? "没有匹配的词条" : "单词本还是空的";
}

function vocabularyRow(entry) {
  const row = make("article", "vocabulary-row");
  const word = make("div", "vocabulary-word");
  word.append(
    make("h3", "", entry.term_en),
    make("p", "vocabulary-phonetic", entry.phonetic_us || "音标暂缺"),
  );

  const definition = make("div", "vocabulary-definition");
  definition.append(make("p", "", entry.translation_zh));

  const actions = make("div", "vocabulary-actions");
  const mastered = make("label", "vocabulary-mastered-toggle");
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = entry.status === "mastered";
  checkbox.setAttribute("aria-label", `${entry.term_en} 已掌握`);
  checkbox.addEventListener("change", () => setVocabularyStatus(entry, checkbox.checked));
  mastered.append(checkbox, make("span", "", "已掌握"));

  const edit = make("button", "mini-icon vocabulary-edit-button", "✎");
  edit.type = "button";
  edit.title = "编辑词条";
  edit.setAttribute("aria-label", `编辑 ${entry.term_en}`);
  edit.addEventListener("click", () => showVocabularyEditor(entry));
  const remove = make("button", "mini-icon", "×");
  remove.type = "button";
  remove.title = "删除词条";
  remove.setAttribute("aria-label", `删除 ${entry.term_en}`);
  remove.addEventListener("click", () => deleteVocabularyEntry(entry));
  actions.append(mastered, edit, remove);
  row.append(word, definition, actions);
  return row;
}

async function showVocabularyDialog() {
  try {
    await loadVocabulary();
    openDialog("vocabularyDialog");
    $("#vocabularySearch").focus();
  } catch (error) {
    handleError(error);
  }
}

function showVocabularyEditor(entry = null, draft = null) {
  const form = $("#vocabularyForm");
  state.editingVocabularyId = entry?.id || null;
  state.vocabularyDraft = draft;
  form.elements.term_en.value = entry?.term_en || draft?.term_en || "";
  form.elements.translation_zh.value = entry?.translation_zh || draft?.translation_zh || "";
  form.elements.phonetic_us.value = entry?.phonetic_us || draft?.phonetic_us || "";
  form.elements.status.value = entry?.status || "learning";
  $("#vocabularyFormTitle").textContent = entry ? "编辑词条" : "新增词条";
  $("#wordPopover").hidden = true;
  openDialog("vocabularyEditDialog");
  window.setTimeout(() => {
    const target = form.elements.translation_zh.value ? form.elements.term_en : form.elements.translation_zh;
    target.focus();
  }, 0);
}

async function addVocabularyCandidate() {
  const candidate = state.vocabularyCandidate;
  if (!candidate) return;
  if (!candidate.translation_zh) {
    showVocabularyEditor(null, candidate);
    return;
  }
  const button = $("#addVocabularyButton");
  button.disabled = true;
  button.textContent = "正在加入...";
  try {
    const result = await api("/api/vocabulary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        term_en: candidate.term_en,
        translation_zh: candidate.translation_zh,
        phonetic_us: candidate.phonetic_us || "",
        status: "learning",
      }),
    });
    await loadVocabulary();
    closeWordPopover();
    toast(result.created ? "已加入单词本" : "该词条已在单词本中");
  } catch (error) {
    button.disabled = false;
    button.textContent = "加入单词本";
    handleError(error);
  }
}

async function handleVocabularyForm(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {
    term_en: form.elements.term_en.value,
    translation_zh: form.elements.translation_zh.value,
    phonetic_us: form.elements.phonetic_us.value,
    status: form.elements.status.value,
  };
  const editing = state.editingVocabularyId;
  try {
    const result = await api(editing ? `/api/vocabulary/${editing}` : "/api/vocabulary", {
      method: editing ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    closeDialog("vocabularyEditDialog");
    state.editingVocabularyId = null;
    state.vocabularyDraft = null;
    await loadVocabulary();
    toast(!editing && result.created === false ? "该词条已在单词本中" : "词条已保存");
  } catch (error) {
    handleError(error);
  }
}

async function setVocabularyStatus(entry, mastered) {
  try {
    const result = await api(`/api/vocabulary/${entry.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: mastered ? "mastered" : "learning" }),
    });
    const index = state.vocabulary.findIndex((item) => item.id === entry.id);
    if (index >= 0) state.vocabulary[index] = result.entry;
    renderVocabulary();
  } catch (error) {
    handleError(error);
    await loadVocabulary();
  }
}

async function deleteVocabularyEntry(entry) {
  const accepted = await confirmAction("删除词条", `从单词本中删除“${entry.term_en}”？`);
  if (!accepted) return;
  try {
    await api(`/api/vocabulary/${entry.id}`, { method: "DELETE" });
    state.vocabulary = state.vocabulary.filter((item) => item.id !== entry.id);
    renderVocabulary();
    toast("词条已删除");
  } catch (error) {
    handleError(error);
  }
}

function selectTag(tagId) {
  state.tagId = tagId && state.tagId !== tagId ? tagId : "";
  clearPaperSelection(false);
  renderTagFilters();
  loadLibrary().catch(handleError);
}

function resetLibraryFilters() {
  state.query = "";
  state.tagId = "";
  state.summaryFilter = "all";
  state.ratingFilter = "all";
  $("#searchInput").value = "";
  clearPaperSelection(false);
  renderTagFilters();
  loadLibrary().catch(handleError);
}

function navigateToPaper(paperId) {
  location.hash = `paper/${paperId}`;
}

async function handleRoute() {
  closeWordPopover();
  stopAnalysisPolling();
  const match = location.hash.match(/^#paper\/([0-9a-f-]+)$/);
  if (!match) {
    state.currentPaper = null;
    state.analysisRuns = [];
    state.analysisJobs = [];
    $("#libraryView").hidden = false;
    $("#readerView").hidden = true;
    clearPdfPreview();
    return;
  }
  try {
    const data = await api(`/api/papers/${match[1]}`);
    state.currentPaper = data.paper;
    state.analysisRuns = [];
    state.analysisJobs = [];
    state.analysisSelections = { quick_read: null, figure_analysis: null };
    state.qaResult = null;
    state.qaError = "";
    renderReader();
    $("#libraryView").hidden = true;
    $("#readerView").hidden = false;
    window.scrollTo(0, 0);
    loadPaperAnalyses().catch(handleError);
    if (data.paper.read_state !== "read") {
      state.currentPaper.read_state = "read";
      setPaperReadState(data.paper.id, "read", false).catch(handleError);
    }
  } catch (error) {
    toast(error.message, "error");
    location.hash = "";
  }
}

function clearPdfPreview() {
  if (state.pdfScrollFrame) window.cancelAnimationFrame(state.pdfScrollFrame);
  state.pdfScrollFrame = null;
  state.pdfObserver?.disconnect();
  state.pdfObserver = null;
  state.pdfPaperId = null;
  state.pdfPageCount = 0;
  state.pdfCurrentPage = 1;
  state.pdfAnnotations = [];
  closePdfSelectionToolbar();
  closeAnnotationPopover();
  const viewer = $("#pdfViewer");
  if (viewer) viewer.replaceChildren();
}

function renderPdfPreview(paper) {
  clearPdfPreview();
  state.pdfPaperId = paper.id;
  state.pdfPageCount = Number(paper.page_count) || 0;
  state.pdfCurrentPage = 1;
  state.pdfZoom = 1;
  const viewer = $("#pdfViewer");
  viewer.style.setProperty("--pdf-page-width", "calc(100% - 24px)");
  $("#pdfPageTotal").textContent = String(state.pdfPageCount);
  $("#pdfZoomLabel").textContent = "100%";
  for (let page = 1; page <= state.pdfPageCount; page += 1) {
    const shell = make("section", "pdf-page loading", "正在加载页面...");
    shell.dataset.page = String(page);
    shell.setAttribute("aria-label", `PDF 第 ${page} 页`);
    viewer.append(shell);
  }

  state.pdfObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) loadPdfPage(entry.target);
    }
  }, { root: viewer, rootMargin: "700px 0px" });
  $$(".pdf-page", viewer).forEach((page) => state.pdfObserver.observe(page));
  updatePdfControls();
  const requestedPage = state.pendingPdfPage;
  state.pendingPdfPage = null;
  if (requestedPage) window.setTimeout(() => goToPdfPage(requestedPage), 0);
}

async function loadPdfPage(shell) {
  if (!shell || shell.dataset.loading === "true" || shell.dataset.loaded === "true") return;
  shell.dataset.loading = "true";
  const page = Number(shell.dataset.page);
  const paperId = state.pdfPaperId;
  try {
    const image = document.createElement("img");
    image.alt = `PDF 第 ${page} 页`;
    image.decoding = "async";
    const imageReady = new Promise((resolve, reject) => {
      image.addEventListener("load", resolve, { once: true });
      image.addEventListener("error", reject, { once: true });
    });
    image.src = `/api/papers/${paperId}/pages/${page}.png`;
    const [textData] = await Promise.all([
      api(`/api/papers/${paperId}/pages/${page}/text`),
      imageReady,
    ]);
    if (paperId !== state.pdfPaperId) return;
    shell.style.aspectRatio = `${textData.width} / ${textData.height}`;
    const layer = make("div", "pdf-text-layer");
    for (const [wordIndex, word] of (textData.words || []).entries()) {
      const span = make("span", "pdf-word", word.text);
      span.style.left = `${word.x}%`;
      span.style.top = `${word.y}%`;
      span.style.width = `${Math.max(word.width, 0.2)}%`;
      span.style.height = `${Math.max(word.height, 0.25)}%`;
      span.dataset.word = word.text;
      span.dataset.wordIndex = String(wordIndex);
      span.dataset.context = word.line || "";
      span.dataset.lineKey = `${word.block ?? 0}:${word.line_number ?? wordIndex}`;
      span.addEventListener("pointerdown", (event) => beginPdfPointerSelection(event, page, layer));
      span.addEventListener("click", (event) => openAnnotationFromWord(event, page));
      span.addEventListener("dblclick", (event) => translatePdfWord(event, page));
      layer.append(span);
    }
    layer.addEventListener("pointerup", (event) => queuePdfSelection(event, page, layer));
    const pageNumber = make("span", "pdf-page-number", String(page));
    shell.replaceChildren(image, layer, pageNumber);
    shell.classList.remove("loading");
    shell.dataset.loaded = "true";
    applyPdfAnnotations(page, layer);
  } catch (error) {
    shell.textContent = "页面加载失败";
    shell.dataset.loading = "false";
  }
}

function translatePdfWord(event, page) {
  event.preventDefault();
  event.stopPropagation();
  window.clearTimeout(state.pdfSelectionTimer);
  closePdfSelectionToolbar();
  closeAnnotationPopover();
  const target = event.currentTarget;
  const term = cleanSelectedEnglish(target.dataset.word);
  if (!term || !state.currentPaper) return;
  const rect = target.getBoundingClientRect();
  translateAndShowWord(
    {
      term_en: term,
      translation_zh: "",
      paper_id: state.currentPaper.id,
      paper_title: state.currentPaper.title,
      source_pair_index: null,
      source_page: page,
      context_en: target.dataset.context || "",
      context_zh: "",
    },
    rect,
  );
}

async function loadPdfAnnotations(paperId) {
  const data = await api(`/api/papers/${paperId}/annotations`);
  if (state.currentPaper?.id !== paperId) return;
  state.pdfAnnotations = data.annotations || [];
  updateAnnotationCount();
  renderNotesPanel();
  $$(".pdf-text-layer", $("#pdfViewer")).forEach((layer) => {
    const page = Number(layer.closest(".pdf-page")?.dataset.page);
    if (page) applyPdfAnnotations(page, layer);
  });
}

function applyPdfAnnotations(page, layer) {
  const words = $$(".pdf-word", layer);
  words.forEach((word) => {
    word.classList.remove("pdf-highlight", "has-annotation-note");
    delete word.dataset.annotationIds;
    delete word.dataset.highlightColor;
    word.style.removeProperty("--highlight-left");
    word.style.removeProperty("--highlight-right");
  });
  const annotations = state.pdfAnnotations.filter((annotation) => Number(annotation.page) === page);
  for (const annotation of annotations) {
    for (let index = Number(annotation.start_word); index <= Number(annotation.end_word); index += 1) {
      const word = words[index];
      if (!word) continue;
      word.classList.add("pdf-highlight");
      word.dataset.highlightColor = annotation.color || "yellow";
      const ids = word.dataset.annotationIds ? word.dataset.annotationIds.split(",") : [];
      ids.push(annotation.id);
      word.dataset.annotationIds = ids.join(",");
      if (annotation.note && index === Number(annotation.end_word)) word.classList.add("has-annotation-note");
    }
  }
  connectPdfHighlightRuns(words);
}

function connectPdfHighlightRuns(words) {
  const sharedAnnotation = (left, right) => {
    const rightIds = new Set((right.dataset.annotationIds || "").split(",").filter(Boolean));
    return (left.dataset.annotationIds || "").split(",").some((id) => rightIds.has(id));
  };
  const geometry = (word) => ({
    left: Number.parseFloat(word.style.left) || 0,
    width: Number.parseFloat(word.style.width) || 0,
    height: Number.parseFloat(word.style.height) || 0,
  });
  for (let index = 0; index < words.length; index += 1) {
    const word = words[index];
    if (!word.classList.contains("pdf-highlight")) continue;
    const current = geometry(word);
    const previous = words[index - 1];
    if (
      previous?.classList.contains("pdf-highlight")
      && previous.dataset.lineKey === word.dataset.lineKey
      && sharedAnnotation(previous, word)
    ) {
      const previousBox = geometry(previous);
      const gap = current.left - previousBox.left - previousBox.width;
      if (gap >= 0 && gap <= Math.max(0.5, current.height * 1.4)) {
        word.style.setProperty("--highlight-left", `${-(gap / 2 / Math.max(current.width, 0.01) * 100)}%`);
      }
    }
    const next = words[index + 1];
    if (
      next?.classList.contains("pdf-highlight")
      && next.dataset.lineKey === word.dataset.lineKey
      && sharedAnnotation(word, next)
    ) {
      const nextBox = geometry(next);
      const gap = nextBox.left - current.left - current.width;
      if (gap >= 0 && gap <= Math.max(0.5, current.height * 1.4)) {
        word.style.setProperty("--highlight-right", `${-(gap / 2 / Math.max(current.width, 0.01) * 100)}%`);
      }
    }
  }
}

function queuePdfSelection(event, page, layer) {
  if (Date.now() < state.pdfSuppressClickUntil) return;
  window.clearTimeout(state.pdfSelectionTimer);
  const anchor = { left: event.clientX, right: event.clientX, top: event.clientY, bottom: event.clientY };
  state.pdfSelectionTimer = window.setTimeout(() => {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.toString().trim()) return;
    const anchorWord = pdfWordFromNode(selection.anchorNode);
    const focusWord = pdfWordFromNode(selection.focusNode);
    if (!anchorWord || !focusWord || anchorWord.parentElement !== layer || focusWord.parentElement !== layer) return;
    const startWord = Math.min(Number(anchorWord.dataset.wordIndex), Number(focusWord.dataset.wordIndex));
    const endWord = Math.max(Number(anchorWord.dataset.wordIndex), Number(focusWord.dataset.wordIndex));
    const words = $$(".pdf-word", layer).slice(startWord, endWord + 1);
    const selectedText = words.map((word) => word.dataset.word || "").join(" ").trim();
    if (!selectedText) return;
    state.pdfSelection = {
      page,
      start_word: startWord,
      end_word: endWord,
      selected_text: selectedText,
      anchor,
    };
    closeWordPopover();
    closeAnnotationPopover();
    showPdfSelectionToolbar(anchor);
  }, 140);
}

function beginPdfPointerSelection(event, page, layer) {
  if (event.button !== 0) return;
  const wordIndex = Number(event.currentTarget.dataset.wordIndex);
  state.pdfPointerSelection = {
    pointerId: event.pointerId,
    page,
    layer,
    startWord: wordIndex,
    endWord: wordIndex,
    originX: event.clientX,
    originY: event.clientY,
    active: false,
  };
}

function trackPdfPointerSelection(event) {
  const drag = state.pdfPointerSelection;
  if (!drag || event.pointerId !== drag.pointerId || !(event.buttons & 1)) return;
  const distance = Math.hypot(event.clientX - drag.originX, event.clientY - drag.originY);
  if (distance < 4 && !drag.active) return;
  drag.active = true;
  const targetWord = event.target.closest?.(".pdf-word");
  if (targetWord?.parentElement === drag.layer) drag.endWord = Number(targetWord.dataset.wordIndex);
  updatePdfSelectionPreview(drag);
  event.preventDefault();
}

function finishPdfPointerSelection(event) {
  const drag = state.pdfPointerSelection;
  if (!drag || event.pointerId !== drag.pointerId) return;
  state.pdfPointerSelection = null;
  if (!drag.active) {
    clearPdfSelectionPreview(drag.layer);
    return;
  }
  event.preventDefault();
  state.pdfSuppressClickUntil = Date.now() + 400;
  const startWord = Math.min(drag.startWord, drag.endWord);
  const endWord = Math.max(drag.startWord, drag.endWord);
  const words = $$(".pdf-word", drag.layer).slice(startWord, endWord + 1);
  const selectedText = words.map((word) => word.dataset.word || "").join(" ").trim();
  clearPdfSelectionPreview(drag.layer);
  if (!selectedText) return;
  const anchor = { left: event.clientX, right: event.clientX, top: event.clientY, bottom: event.clientY };
  state.pdfSelection = {
    page: drag.page,
    start_word: startWord,
    end_word: endWord,
    selected_text: selectedText,
    anchor,
  };
  window.getSelection()?.removeAllRanges();
  closeWordPopover();
  closeAnnotationPopover();
  showPdfSelectionToolbar(anchor);
}

function updatePdfSelectionPreview(drag) {
  const startWord = Math.min(drag.startWord, drag.endWord);
  const endWord = Math.max(drag.startWord, drag.endWord);
  $$(".pdf-word", drag.layer).forEach((word, index) => {
    word.classList.toggle("pdf-selecting", index >= startWord && index <= endWord);
  });
}

function clearPdfSelectionPreview(layer = null) {
  const root = layer || $("#pdfViewer");
  if (root) $$(".pdf-word.pdf-selecting", root).forEach((word) => word.classList.remove("pdf-selecting"));
}

function pdfWordFromNode(node) {
  const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
  return element?.closest?.(".pdf-word") || null;
}

function annotationsForPdfSelection(selection = state.pdfSelection) {
  if (!selection) return [];
  return state.pdfAnnotations.filter((annotation) => (
    Number(annotation.page) === Number(selection.page)
    && Number(annotation.start_word) <= Number(selection.end_word)
    && Number(annotation.end_word) >= Number(selection.start_word)
  ));
}

function showPdfSelectionToolbar(anchor) {
  const toolbar = $("#pdfSelectionToolbar");
  const overlapping = annotationsForPdfSelection();
  $("#removeSelectionHighlightButton").hidden = overlapping.length === 0;
  toolbar.hidden = false;
  const box = toolbar.getBoundingClientRect();
  const left = Math.max(10, Math.min(window.innerWidth - box.width - 10, anchor.left - box.width / 2));
  let top = anchor.bottom + 9;
  if (top + box.height > window.innerHeight - 10) top = anchor.top - box.height - 9;
  toolbar.style.left = `${left}px`;
  toolbar.style.top = `${Math.max(10, top)}px`;
}

function closePdfSelectionToolbar(clearSelection = false) {
  const toolbar = $("#pdfSelectionToolbar");
  if (toolbar) toolbar.hidden = true;
  clearPdfSelectionPreview();
  state.pdfPointerSelection = null;
  state.pdfSelection = null;
  if (clearSelection) window.getSelection()?.removeAllRanges();
}

async function createPdfAnnotation(openEditor = false) {
  const selection = state.pdfSelection;
  const paper = state.currentPaper;
  if (!selection || !paper) return;
  const anchor = selection.anchor;
  const result = await api(`/api/papers/${paper.id}/annotations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...selection, color: "yellow", note: "" }),
  });
  state.pdfAnnotations.push(result.annotation);
  const layer = $(`.pdf-page[data-page="${selection.page}"] .pdf-text-layer`, $("#pdfViewer"));
  if (layer) applyPdfAnnotations(selection.page, layer);
  updateAnnotationCount();
  renderNotesPanel();
  closePdfSelectionToolbar(true);
  toast("高亮已保存");
  if (openEditor) showAnnotationPopover(result.annotation, anchor);
}

async function removePdfSelectionHighlights() {
  const selection = state.pdfSelection;
  const annotations = annotationsForPdfSelection(selection);
  if (!selection || !annotations.length) return;
  await Promise.all(annotations.map((annotation) => (
    api(`/api/annotations/${annotation.id}`, { method: "DELETE" })
  )));
  const removedIds = new Set(annotations.map((annotation) => annotation.id));
  state.pdfAnnotations = state.pdfAnnotations.filter((annotation) => !removedIds.has(annotation.id));
  const layer = $(`.pdf-page[data-page="${selection.page}"] .pdf-text-layer`, $("#pdfViewer"));
  if (layer) applyPdfAnnotations(Number(selection.page), layer);
  updateAnnotationCount();
  renderNotesPanel();
  closePdfSelectionToolbar(true);
  toast(annotations.length === 1 ? "高亮已取消" : `已取消 ${annotations.length} 处高亮`);
}

function openAnnotationFromWord(event, page) {
  if (Date.now() < state.pdfSuppressClickUntil) return;
  if (event.detail !== 1 || window.getSelection()?.toString().trim()) return;
  const annotationId = event.currentTarget.dataset.annotationIds?.split(",")[0];
  if (!annotationId) return;
  const annotation = state.pdfAnnotations.find((item) => item.id === annotationId);
  if (!annotation || Number(annotation.page) !== page) return;
  event.stopPropagation();
  showAnnotationPopover(annotation, event.currentTarget.getBoundingClientRect());
}

function showAnnotationPopover(annotation, anchor) {
  state.editingAnnotationId = annotation.id;
  state.editingAnnotationColor = annotation.color || "yellow";
  $("#annotationSelectedText").textContent = annotation.selected_text || "已高亮内容";
  $("#annotationNote").value = annotation.note || "";
  $$('[data-annotation-color]', $("#annotationColors")).forEach((button) => {
    button.classList.toggle("active", button.dataset.annotationColor === state.editingAnnotationColor);
  });
  const popover = $("#pdfAnnotationPopover");
  popover.hidden = false;
  const box = popover.getBoundingClientRect();
  const center = anchor.left + ((anchor.right || anchor.left) - anchor.left) / 2;
  const left = Math.max(10, Math.min(window.innerWidth - box.width - 10, center - box.width / 2));
  let top = anchor.bottom + 9;
  if (top + box.height > window.innerHeight - 10) top = anchor.top - box.height - 9;
  popover.style.left = `${left}px`;
  popover.style.top = `${Math.max(10, top)}px`;
  if (!annotation.note) window.setTimeout(() => $("#annotationNote").focus(), 0);
}

function closeAnnotationPopover() {
  const popover = $("#pdfAnnotationPopover");
  if (popover) popover.hidden = true;
  state.editingAnnotationId = null;
}

async function saveAnnotation() {
  const annotationId = state.editingAnnotationId;
  if (!annotationId) return;
  const result = await api(`/api/annotations/${annotationId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      note: $("#annotationNote").value,
      color: state.editingAnnotationColor,
    }),
  });
  const index = state.pdfAnnotations.findIndex((item) => item.id === annotationId);
  if (index >= 0) state.pdfAnnotations[index] = result.annotation;
  const layer = $(`.pdf-page[data-page="${result.annotation.page}"] .pdf-text-layer`, $("#pdfViewer"));
  if (layer) applyPdfAnnotations(Number(result.annotation.page), layer);
  renderNotesPanel();
  closeAnnotationPopover();
  toast("批注已保存");
}

async function deleteAnnotation() {
  const annotationId = state.editingAnnotationId;
  if (!annotationId) return;
  const annotation = state.pdfAnnotations.find((item) => item.id === annotationId);
  await api(`/api/annotations/${annotationId}`, { method: "DELETE" });
  state.pdfAnnotations = state.pdfAnnotations.filter((item) => item.id !== annotationId);
  if (annotation) {
    const layer = $(`.pdf-page[data-page="${annotation.page}"] .pdf-text-layer`, $("#pdfViewer"));
    if (layer) applyPdfAnnotations(Number(annotation.page), layer);
  }
  updateAnnotationCount();
  renderNotesPanel();
  closeAnnotationPopover();
  toast("高亮已取消");
}

function updateAnnotationCount() {
  $("#pdfAnnotationCount").textContent = `${state.pdfAnnotations.length} 处高亮`;
}

function goToPdfPage(pageNumber) {
  if (!state.pdfPageCount) return;
  const page = Math.max(1, Math.min(state.pdfPageCount, Number(pageNumber) || 1));
  const shell = $(`.pdf-page[data-page="${page}"]`, $("#pdfViewer"));
  if (!shell) return;
  loadPdfPage(shell);
  shell.scrollIntoView({ behavior: "smooth", block: "start" });
  state.pdfCurrentPage = page;
  updatePdfControls();
}

function setPdfZoom(value, focusPoint = null) {
  const previousZoom = state.pdfZoom;
  const nextZoom = Math.max(0.5, Math.min(3, Math.round(value * 20) / 20));
  if (nextZoom === previousZoom) return;
  const viewer = $("#pdfViewer");
  const viewerRect = viewer.getBoundingClientRect();
  const localX = focusPoint ? focusPoint.clientX - viewerRect.left : viewer.clientWidth / 2;
  const localY = focusPoint ? focusPoint.clientY - viewerRect.top : viewer.clientHeight / 2;
  const contentX = viewer.scrollLeft + localX;
  const contentY = viewer.scrollTop + localY;
  state.pdfZoom = nextZoom;
  viewer.style.setProperty(
    "--pdf-page-width", `calc(${state.pdfZoom * 100}% - 24px)`,
  );
  $("#pdfZoomLabel").textContent = `${Math.round(state.pdfZoom * 100)}%`;
  const ratio = nextZoom / previousZoom;
  window.requestAnimationFrame(() => {
    viewer.scrollLeft = contentX * ratio - localX;
    viewer.scrollTop = contentY * ratio - localY;
  });
}

function handlePdfWheel(event) {
  if (!event.ctrlKey) return;
  event.preventDefault();
  event.stopPropagation();
  const factor = Math.exp(-event.deltaY * 0.006);
  setPdfZoom(state.pdfZoom * factor, event);
}

function updatePdfCurrentPage() {
  state.pdfScrollFrame = null;
  const viewer = $("#pdfViewer");
  const viewerTop = viewer.getBoundingClientRect().top + 12;
  let closestPage = state.pdfCurrentPage;
  let closestDistance = Number.POSITIVE_INFINITY;
  for (const shell of $$(".pdf-page", viewer)) {
    const distance = Math.abs(shell.getBoundingClientRect().top - viewerTop);
    if (distance < closestDistance) {
      closestDistance = distance;
      closestPage = Number(shell.dataset.page);
    }
  }
  if (closestPage !== state.pdfCurrentPage) {
    state.pdfCurrentPage = closestPage;
    updatePdfControls();
  }
}

function updatePdfControls() {
  $("#pdfPageInput").value = String(state.pdfCurrentPage || 1);
  $("#pdfPreviousPage").disabled = state.pdfCurrentPage <= 1;
  $("#pdfNextPage").disabled = state.pdfCurrentPage >= state.pdfPageCount;
}

function renderReader() {
  const paper = state.currentPaper;
  if (!paper) return;
  state.activePair = null;
  renderReaderIdentity(paper);
  updatePanelLayout();
  $("#pdfAnnotationCount").textContent = "0 处高亮";
  const pdfUrl = `/api/papers/${paper.id}/file`;
  renderPdfPreview(paper);
  loadPdfAnnotations(paper.id).catch(handleError);
  $("#openPdfButton").href = pdfUrl;
  const status = $("#summaryStatus");
  status.className = `status-pill ${paper.summary_status || "pending"}`;
  status.textContent = summarySource(paper);
  status.title = paper.summary_provider === "openai_compatible"
    ? `英文分析：${paper.summary_analysis_model || paper.summary_model || "未记录"}；中文翻译：${paper.summary_translation_model || "未记录"}`
    : `摘要来源：${summarySource(paper)}（未调用外部模型）`;
  const usesBlocks = Boolean(paper.summary_blocks?.length);
  const termLabel = $("#termLinkLabel b");
  termLabel.textContent = usesBlocks ? "段落联动" : "句对联动";
  termLabel.parentElement.title = usesBlocks
    ? "单击任一侧段落可将对应译文对齐到相同高度"
    : "单击任一侧句子可同步显示对应句";
  const translateButton = $("#translateSummaryButton");
  translateButton.hidden = !usesBlocks || paper.summary_translation_status === "ready";
  translateButton.disabled = paper.summary_status === "translating";
  translateButton.title = paper.summary_status === "translation_error"
    ? "仅重新翻译中文，不重新运行英文分析"
    : "生成中文翻译";
  renderSummaries(
    paper.summary_pairs || [],
    paper.summary_error || paper.summary_translation_error,
    paper.visual_assets || [],
    paper.id,
    paper.summary_blocks || [],
  );
  renderAnalysisPanels();
  renderQaPanel();
  renderNotesPanel();
  setReaderMode(state.readerMode);
}

function renderReaderIdentity(paper) {
  $("#readerTitle").textContent = paper.title;
  const meta = $("#readerMeta");
  meta.replaceChildren();
  const details = [paperAuthors(paper), paper.publication_year, paper.doi].filter(Boolean);
  if (details.length) meta.append(make("span", "", details.join(" · ")));
  paper.tags.forEach((tag) => meta.append(tagChip(tag)));
  meta.append(ratingControl(paper, "reader-rating"));
  $("#pdfMeta").textContent = `${paper.page_count || "-"} 页 · ${formatBytes(paper.file_size)}`;
}

function setReaderMode(mode) {
  if (!["quick", "deep", "ask", "notes"].includes(mode)) return;
  state.readerMode = mode;
  $$('[data-reader-mode]').forEach((button) => {
    const active = button.dataset.readerMode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  const deep = mode === "deep";
  $("#englishPanel").hidden = !deep;
  $("#chinesePanel").hidden = !deep;
  $$('[data-analysis-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.analysisPanel !== mode;
  });
  $("#regenerateButton").hidden = !deep;
  updatePanelLayout();
  if (mode === "quick") renderAnalysisPanels();
  if (mode === "ask") renderQaPanel();
  if (mode === "notes") renderNotesPanel();
}

function focusReaderModePanel(mode) {
  if (window.innerWidth > 760) return;
  const target = mode === "deep"
    ? $("#englishPanel")
    : $(`[data-analysis-panel="${mode}"]`);
  const grid = $("#readerGrid");
  if (!target || !grid) return;
  grid.scrollTo({ left: target.offsetLeft, behavior: "smooth" });
}

function stopAnalysisPolling() {
  if (state.analysisPollTimer) window.clearTimeout(state.analysisPollTimer);
  state.analysisPollTimer = null;
}

function latestAnalysisJob(type) {
  return state.analysisJobs.find((job) => job.job_type === type) || null;
}

function selectedAnalysisRun(type) {
  const successful = state.analysisRuns.filter((run) => run.analysis_type === type && run.status === "succeeded");
  const selectedId = state.analysisSelections[type];
  return successful.find((run) => run.id === selectedId) || successful[0] || null;
}

async function loadPaperAnalyses() {
  const paperId = state.currentPaper?.id;
  if (!paperId) return;
  const hadActive = state.analysisJobs.some((job) => ["queued", "running"].includes(job.status));
  const data = await api(`/api/papers/${paperId}/analyses`);
  if (state.currentPaper?.id !== paperId) return;
  state.analysisRuns = data.runs || [];
  state.analysisJobs = data.jobs || [];
  renderAnalysisPanels();
  const active = state.analysisJobs.filter((job) => ["queued", "running"].includes(job.status));
  const latestJobs = [latestAnalysisJob("quick_read"), latestAnalysisJob("figure_analysis")].filter(Boolean);
  const failed = latestJobs.filter((job) => job.status === "failed");
  const summary = $("#readerTaskSummary");
  if (active.length) summary.textContent = `${active.length} 项分析任务进行中`;
  else if (failed.length) summary.textContent = `${failed.length} 项任务失败，可重试`;
  else if (state.analysisRuns.length) summary.textContent = `${state.analysisRuns.filter((run) => run.status === "succeeded").length} 个分析版本`;
  else summary.textContent = "分析任务就绪";
  stopAnalysisPolling();
  if (active.length) {
    state.analysisPollTimer = window.setTimeout(() => loadPaperAnalyses().catch(handleError), 700);
  } else if (hadActive) {
    loadLibrary().catch(handleError);
  }
}

function renderAnalysisPanels() {
  if (!$("#quickPanel")) return;
  renderAnalysisVersionSelect("quick_read", $("#quickRunSelect"));
  renderAnalysisVersionSelect("figure_analysis", $("#figureRunSelect"));
  renderAnalysisJob("quick_read", $("#quickJobStatus"), $("#quickReadButton"));
  renderAnalysisJob("figure_analysis", $("#figureJobStatus"), $("#figureAnalysisButton"));
  renderQuickReadContent(selectedAnalysisRun("quick_read"));
  renderFigureAnalysisContent(selectedAnalysisRun("figure_analysis"));
  const active = state.analysisJobs.some((job) => ["queued", "running"].includes(job.status));
  const failed = [latestAnalysisJob("quick_read"), latestAnalysisJob("figure_analysis")]
    .filter(Boolean)
    .some((job) => job.status === "failed");
  const status = $("#quickPanelStatus");
  status.className = `status-pill ${failed ? "error" : active ? "draft" : ""}`;
  status.textContent = active ? "任务进行中" : failed ? "有任务失败" : state.analysisRuns.length ? "已有版本" : "尚未分析";
}

function renderAnalysisVersionSelect(type, select) {
  const runs = state.analysisRuns.filter((run) => run.analysis_type === type && run.status === "succeeded");
  const selected = selectedAnalysisRun(type);
  select.replaceChildren();
  runs.forEach((run, index) => {
    const option = document.createElement("option");
    option.value = run.id;
    const legacyLabel = type === "figure_analysis"
      && run.prompt_version !== CURRENT_FIGURE_PROMPT_VERSION ? " · 旧版" : "";
    option.textContent = `${index === 0 ? "最新" : `历史 ${index}`} · ${formatDate(run.created_at)} · ${run.provider === "local" ? "本地" : run.model || "模型"}${legacyLabel}`;
    select.append(option);
  });
  select.hidden = runs.length < 2;
  if (selected) select.value = selected.id;
}

function renderAnalysisJob(type, container, actionButton) {
  const job = latestAnalysisJob(type);
  container.replaceChildren();
  const active = job && ["queued", "running"].includes(job.status);
  actionButton.disabled = Boolean(active);
  actionButton.lastChild.textContent = type === "quick_read"
    ? (selectedAnalysisRun(type) ? " 重新生成" : " 生成速读")
    : (selectedAnalysisRun(type) ? " 重新分析" : " 分析图表");
  if (!job) {
    container.hidden = true;
    return;
  }
  container.hidden = false;
  container.className = `analysis-job-status ${job.status}`;
  if (active) {
    const progress = document.createElement("progress");
    container.append(progress, make("span", "", job.status === "queued" ? "任务已入队，等待后台处理" : `正在分析 · 第 ${job.attempts} 次尝试`));
  } else if (job.status === "failed") {
    const message = make("span", "", job.error || "分析失败");
    container.append(message);
    if (Number(job.attempts) < Number(job.max_attempts)) {
      const retry = make("button", "text-button", "重试");
      retry.type = "button";
      retry.addEventListener("click", () => retryAnalysisJob(job.id));
      container.append(retry);
    } else {
      container.append(make("span", "", "已达重试上限"));
    }
  } else {
    container.append(make("span", "", `已完成 · ${job.duration_ms || 0} ms · 输入 ${String(job.input_hash || "").slice(0, 8)}`));
  }
}

async function queueAnalysis(type) {
  const paper = state.currentPaper;
  if (!paper) return;
  await api(`/api/papers/${paper.id}/analyses/${type === "quick_read" ? "quick-read" : "figure-analysis"}`, { method: "POST" });
  state.analysisSelections[type] = null;
  state.readerMode = "quick";
  await loadPaperAnalyses();
}

async function retryAnalysisJob(jobId) {
  try {
    await api(`/api/analysis-jobs/${jobId}/retry`, { method: "POST" });
    await loadPaperAnalyses();
  } catch (error) {
    handleError(error);
  }
}

function pageJumpButton(page, label = `第 ${page} 页`) {
  const button = make("button", "page-jump-button", label);
  button.type = "button";
  button.title = `跳到 PDF 第 ${page} 页`;
  button.addEventListener("click", () => goToPdfPage(page));
  return button;
}

function renderQuickReadContent(run) {
  const container = $("#quickReadContent");
  container.replaceChildren();
  const content = run?.content;
  if (!content?.headline) {
    container.append(make("p", "analysis-empty", "尚未生成速读分析。"));
    return;
  }
  const headline = make("div", "quick-headline");
  headline.append(make("span", "analysis-kicker", "一句话结论"), make("p", "", content.headline.text), pageJumpButton(content.headline.page));
  container.append(headline);
  for (const section of content.sections || []) {
    const block = make("section", "quick-section");
    block.append(make("h4", "", section.title));
    const list = make("ul", "quick-fact-list");
    for (const item of section.items || []) {
      const row = make("li");
      row.append(make("span", "", item.text), pageJumpButton(item.page, `p. ${item.page}`));
      list.append(row);
    }
    block.append(list);
    container.append(block);
  }
}

function renderFigureAnalysisContent(run) {
  const container = $("#figureAnalysisContent");
  container.replaceChildren();
  if (run && run.prompt_version !== CURRENT_FIGURE_PROMPT_VERSION) {
    container.append(make(
      "p",
      "analysis-empty",
      "当前结果来自旧版整页候选规则，已停止展示。点击“重新分析”即可按图表标题识别并裁剪真实图表区域。",
    ));
    return;
  }
  const figures = run?.content?.figures || [];
  if (!figures.length) {
    container.append(make(
      "p",
      "analysis-empty",
      run?.content?.empty_reason || "尚未生成关键图表分析。",
    ));
    return;
  }
  for (const figure of figures) {
    const item = make("article", "figure-analysis-item");
    const filename = String(figure.asset_path || "").split("/").at(-1);
    if (filename) {
      const image = document.createElement("img");
      image.src = `/api/papers/${state.currentPaper.id}/assets/${filename}`;
      image.alt = figure.title || `第 ${figure.page} 页图表`;
      image.loading = "lazy";
      image.addEventListener("error", () => image.remove(), { once: true });
      item.append(image);
    }
    const body = make("div", "figure-analysis-body");
    const heading = make("div", "figure-analysis-heading");
    const headingText = make("div", "figure-analysis-title");
    headingText.append(
      make("span", "figure-kind", figure.asset_kind === "table" ? "表格" : "图"),
      make("h4", "", figure.caption || figure.title || `第 ${figure.page} 页`),
    );
    heading.append(headingText, pageJumpButton(figure.page));
    body.append(heading);
    for (const [label, value] of [["看哪里", figure.look_for], ["证明什么", figure.evidence], ["为什么重要", figure.importance]]) {
      const line = make("p");
      line.append(make("strong", "", label), document.createTextNode(value || "—"));
      body.append(line);
    }
    item.append(body);
    container.append(item);
  }
}

function renderQaPanel() {
  if (!$("#qaAvailability")) return;
  const available = state.settings.provider === "openai_compatible" && state.settings.base_url && state.settings.model;
  const availability = $("#qaAvailability");
  availability.className = `status-pill ${available ? "" : "error"}`;
  availability.textContent = available ? `模型 · ${state.settings.model}` : "需要模型配置";
  $("#qaSubmitButton").disabled = state.qaBusy;
  const answer = $("#qaAnswer");
  const sources = $("#qaSourceList");
  answer.replaceChildren();
  sources.replaceChildren();
  if (state.qaBusy) {
    answer.append(make("p", "analysis-empty", "正在检索论文原文并生成带引用回答..."));
  } else if (state.qaError) {
    answer.append(make("p", "qa-error", state.qaError));
  } else if (state.qaResult) {
    answer.append(make("p", "qa-answer-text", state.qaResult.answer));
  } else {
    answer.append(make("p", "analysis-empty", available ? "回答只会使用本地检索到的论文原文。" : "请先在模型设置中配置 OpenAI-compatible 服务。"));
  }
  const resultSources = state.qaResult?.sources || [];
  $("#qaSourceCount").textContent = String(resultSources.length);
  if (!resultSources.length) {
    sources.append(make("p", "analysis-empty", "提交问题后显示引用页。"));
    return;
  }
  for (const source of resultSources) {
    const button = make("button", "qa-source-item");
    button.type = "button";
    button.append(
      make("span", "qa-source-id", `${source.id} · p. ${source.page}`),
      make("strong", "", source.paper_title),
      make("span", "qa-source-excerpt", source.excerpt),
    );
    button.addEventListener("click", () => openQaSource(source));
    sources.append(button);
  }
}

async function handleQaSubmit(event) {
  event.preventDefault();
  const question = $("#qaQuestion").value.trim();
  if (!question || !state.currentPaper || state.qaBusy) return;
  state.qaBusy = true;
  state.qaError = "";
  state.qaResult = null;
  renderQaPanel();
  try {
    state.qaResult = await api("/api/qa", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, scope: $("#qaScope").value, paper_id: state.currentPaper.id }),
    });
  } catch (error) {
    state.qaError = error.message;
  } finally {
    state.qaBusy = false;
    renderQaPanel();
  }
}

function openQaSource(source) {
  if (source.paper_id === state.currentPaper?.id) {
    goToPdfPage(source.page);
    return;
  }
  state.pendingPdfPage = source.page;
  navigateToPaper(source.paper_id);
}

function renderNotesPanel() {
  if (!$("#notesList")) return;
  const container = $("#notesList");
  container.replaceChildren();
  $("#notesCount").textContent = `${state.pdfAnnotations.length} 条`;
  if (!state.pdfAnnotations.length) {
    container.append(make("p", "analysis-empty", "当前论文还没有高亮或笔记。"));
    return;
  }
  const annotations = [...state.pdfAnnotations].sort((left, right) => Number(left.page) - Number(right.page));
  for (const annotation of annotations) {
    const button = make("button", "note-list-item");
    button.type = "button";
    button.dataset.color = annotation.color || "yellow";
    button.append(
      make("span", "note-page", `p. ${annotation.page}`),
      make("strong", "", annotation.selected_text || "高亮内容"),
      make("span", "note-text", annotation.note || "仅高亮，尚未添加笔记"),
    );
    button.addEventListener("click", () => goToPdfPage(annotation.page));
    container.append(button);
  }
}

function renderSummaries(pairs, error = "", visualAssets = [], paperId = "", blocks = []) {
  const english = $("#englishSummary");
  const chinese = $("#chineseSummary");
  english.replaceChildren();
  chinese.replaceChildren();
  state.activeVisualAssetKey = null;
  if (blocks.length) {
    renderStructuredReport(english, blocks, "en");
    renderStructuredReport(chinese, blocks, "zh");
    return;
  }
  if (!pairs.length) {
    const message = error || "尚未生成摘要";
    english.append(make("div", "summary-empty", message));
    chinese.append(make("div", "summary-empty", message));
    return;
  }
  renderMarkdownReport(english, pairs, "en", visualAssets, paperId);
  renderMarkdownReport(chinese, pairs, "zh", visualAssets, paperId);
}

function renderStructuredReport(container, blocks, language) {
  const heading = make("header", "markdown-document-heading structured-document-heading");
  heading.append(
    make("p", "markdown-document-kicker", "PAPER REPORT"),
    make(
      "h1",
      "markdown-document-title",
      language === "zh" ? "论文完整详细总结" : "Complete Detailed Paper Summary",
    ),
  );
  const reportTitle = state.currentPaper?.summary_paper_title || state.currentPaper?.title;
  if (reportTitle) heading.append(make("p", "markdown-document-subtitle", reportTitle));
  container.append(heading);

  let bulletList = null;
  for (const block of blocks) {
    if (!block || !block.id) continue;
    if (block.type !== "bullet") bulletList = null;
    if (block.type === "heading") {
      const level = Number(block.level) === 3 ? 3 : 2;
      container.append(structuredBlockElement(block, language, `h${level}`));
      continue;
    }
    if (block.type === "bullet") {
      if (!bulletList) {
        bulletList = make("ul", "structured-bullet-list");
        container.append(bulletList);
      }
      bulletList.append(structuredBlockElement(block, language, "li"));
      continue;
    }
    container.append(structuredBlockElement(block, language, "p"));
  }
}

function structuredBlockElement(block, language, tagName) {
  const node = make(tagName, `structured-block structured-${block.type}`);
  const key = language === "zh" ? "text_zh" : "text_en";
  const text = String(block[key] || "").trim();
  node.dataset.blockId = block.id;
  node.dataset.language = language;
  node.tabIndex = 0;
  if (text) {
    const textNode = make("span", "structured-block-text");
    appendSummaryText(textNode, text);
    node.append(textNode);
  } else {
    node.classList.add("translation-pending");
    node.append(make("span", "structured-block-text", "中文翻译生成中…"));
  }
  appendPageReferenceButtons(node, block.page_refs);
  node.addEventListener("click", (event) => {
    if (event.target.closest("[data-page-ref]")) return;
    activateSummaryBlock(block.id, language);
  });
  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activateSummaryBlock(block.id, language);
    }
  });
  if (language === "en" && block.type !== "heading") {
    node.addEventListener("dblclick", (event) => activateBlockTerm(block, event));
  }
  return node;
}

function appendPageReferenceButtons(container, pageRefs) {
  if (!Array.isArray(pageRefs) || !pageRefs.length) return;
  const references = make("span", "structured-page-references");
  for (const page of pageRefs) {
    const button = make("button", "structured-page-button", `p.${page}`);
    button.type = "button";
    button.dataset.pageRef = String(page);
    button.title = `跳转到 PDF 第 ${page} 页`;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      goToPdfPage(page);
    });
    references.append(button);
  }
  container.append(references);
}

function activateSummaryBlock(blockId, sourceLanguage) {
  state.activePair = blockId;
  $$(".structured-block").forEach((block) => {
    block.classList.toggle("active", block.dataset.blockId === blockId);
  });
  const targetLanguage = sourceLanguage === "en" ? "zh" : "en";
  const source = $(`.structured-block[data-language="${sourceLanguage}"][data-block-id="${blockId}"]`);
  const target = $(`.structured-block[data-language="${targetLanguage}"][data-block-id="${blockId}"]`);
  const targetScroller = target?.closest(".summary-content");
  if (source && target && targetScroller) {
    const delta = target.getBoundingClientRect().top - source.getBoundingClientRect().top;
    targetScroller.scrollTo({ top: targetScroller.scrollTop + delta, behavior: "smooth" });
  }
  const label = $("#termLinkLabel b");
  label.textContent = "段落已对齐";
}

function activateBlockTerm(block, event) {
  event.preventDefault();
  const selection = window.getSelection();
  const selected = selection?.toString().trim() || englishWordAtPoint(event);
  const selectionRect = selection?.rangeCount
    ? selection.getRangeAt(0).getBoundingClientRect()
    : { left: event.clientX, right: event.clientX, top: event.clientY, bottom: event.clientY, width: 0 };
  activateSummaryBlock(block.id, "en");
  const term = cleanSelectedEnglish(selected);
  if (term) {
    translateAndShowWord(
      {
        term_en: term,
        translation_zh: "",
        paper_id: state.currentPaper.id,
        paper_title: state.currentPaper.title,
        source_pair_index: null,
        context_en: block.text_en || "",
        context_zh: block.text_zh || "",
      },
      selectionRect,
    );
  }
  selection?.removeAllRanges();
}

function renderMarkdownReport(container, pairs, language, visualAssets = [], paperId = "") {
  const heading = make("header", "markdown-document-heading");
  heading.append(
    make("p", "markdown-document-kicker", language === "zh" ? "PAPER REPORT" : "RESEARCH NOTES"),
    make("h1", "markdown-document-title", language === "zh" ? "论文完整详细总结" : "Complete Paper Report"),
  );
  if (state.currentPaper?.title) {
    heading.append(make("p", "markdown-document-subtitle", state.currentPaper.title));
  }
  container.append(heading);

  const sections = [];
  pairs.forEach((pair, index) => {
    const title = language === "zh"
      ? pair.section_zh || "详细总结"
      : pair.section_en || "Detailed summary";
    const lastSection = sections.at(-1);
    if (!lastSection || lastSection.title !== title) {
      sections.push({ title, items: [{ pair, index }], assets: [] });
    } else {
      lastSection.items.push({ pair, index });
    }
  });
  assignVisualAssets(sections, visualAssets, language);

  sections.forEach((section, sectionIndex) => {
    const sectionNode = make("section", "markdown-section");
    sectionNode.append(summarySectionTitle(section.title, sectionIndex, language));
    const list = make("ol", "markdown-statement-list");
    section.items.forEach(({ pair, index }) => list.append(summarySegment(pair, index, language)));
    sectionNode.append(list);
    section.assets.forEach((asset) => sectionNode.append(renderVisualAsset(asset, paperId, language)));
    container.append(sectionNode);
  });
}

function assignVisualAssets(sections, assets, language) {
  for (const asset of assets) {
    let bestIndex = 0;
    let bestScore = Number.NEGATIVE_INFINITY;
    sections.forEach((section, index) => {
      const sectionTitle = section.title.toLocaleLowerCase();
      const sectionText = section.items.map(({ pair }) => pair[language] || "").join(" ").toLocaleLowerCase();
      const assetText = `${asset.title_en || ""} ${asset.title_zh || ""}`.toLocaleLowerCase();
      let score = section.items.some(({ pair }) => (pair.page_refs || []).includes(asset.page)) ? 8 : 0;
      const methodAsset = /architecture|framework|method|pipeline|system|架构|框架|方法|系统/.test(assetText);
      const resultAsset = /result|experiment|ablation|performance|table|结果|实验|消融|性能|表/.test(assetText);
      const methodTerms = /architecture|framework|method|pipeline|system|module|架构|框架|方法|系统|模块/;
      const resultTerms = /result|experiment|evaluation|ablation|performance|结果|实验|评测|消融|性能/;
      if (methodAsset && methodTerms.test(sectionTitle)) score += 48;
      else if (methodAsset && methodTerms.test(sectionText)) score += 16;
      if (resultAsset && /result|performance|结果|性能/.test(sectionTitle)) score += 72;
      else if (resultAsset && resultTerms.test(sectionTitle)) score += 40;
      else if (resultAsset && resultTerms.test(sectionText)) score += 16;
      if (score > bestScore) {
        bestScore = score;
        bestIndex = index;
      }
    });
    if (sections[bestIndex]) sections[bestIndex].assets.push(asset);
  }
}

function summarySectionTitle(text, sectionIndex = 0, language = "en") {
  const title = make("h2", "summary-section-title");
  const ordinal = language === "zh"
    ? `${chineseSectionNumber(sectionIndex + 1)}、`
    : `${String(sectionIndex + 1).padStart(2, "0")}.`;
  title.append(make("span", "summary-section-ordinal", ordinal), document.createTextNode(text));
  return title;
}

function chineseSectionNumber(number) {
  const numerals = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"];
  if (number <= 10) return numerals[number];
  if (number < 20) return `十${numerals[number - 10]}`;
  return String(number);
}

function summarySegment(pair, index, language) {
  const segment = make("li", "summary-segment");
  const text = pair[language] || "—";
  segment.tabIndex = 0;
  segment.setAttribute("role", "button");
  segment.setAttribute("aria-label", text);
  segment.dataset.index = String(index);
  segment.dataset.language = language;
  segment.dataset.number = String(index + 1).padStart(2, "0");
  const textNode = make("span", "segment-text");
  appendSummaryText(textNode, text);
  segment.append(textNode);
  if (Array.isArray(pair.page_refs) && pair.page_refs.length) {
    const pages = make("span", "page-reference", `p. ${pair.page_refs.join(", ")}`);
    pages.title = "论文证据页";
    segment.append(pages);
  }
  segment.addEventListener("click", (event) => {
    if (event.detail !== 1) return;
    window.clearTimeout(state.pairClickTimer);
    state.pairClickTimer = window.setTimeout(() => activatePair(index, language), 220);
  });
  segment.addEventListener("dblclick", (event) => activateTerm(index, language, event));
  segment.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activatePair(index, language);
    }
  });
  return segment;
}

function appendSummaryText(container, text) {
  const important = /\b(?:Qwen2\.5-7B|Conformer-MoE|LLM-ASR|GLCLAP|GRPO|RADA|LoRA|KER|SACC|WER|Top-?\d+|\d+(?:\.\d+)?(?:%|[kKMB])?|\d+(?:\.\d+)?e-\d+)\b/g;
  let cursor = 0;
  for (const match of text.matchAll(important)) {
    if (match.index > cursor) container.append(document.createTextNode(text.slice(cursor, match.index)));
    const numeric = /^\d/.test(match[0]);
    container.append(make(numeric ? "span" : "strong", numeric ? "summary-number" : "summary-key-term", match[0]));
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) container.append(document.createTextNode(text.slice(cursor)));
}

function downloadSummaryMarkdown() {
  const paper = state.currentPaper;
  if (!paper?.summary_blocks?.length && !paper?.summary_pairs?.length) {
    toast("当前没有可导出的摘要", "error");
    return;
  }
  if (paper.summary_blocks?.length) {
    const lines = [
      "# 论文完整详细总结",
      "",
      `> 论文：${paper.summary_paper_title || paper.title}`,
      "",
      ...summaryBlocksToMarkdown(paper.summary_blocks, "zh"),
      "",
      "---",
      "",
      "# Complete Detailed Paper Summary",
      "",
      ...summaryBlocksToMarkdown(paper.summary_blocks, "en"),
    ];
    downloadTextFile(
      `${paper.title.replace(/[\\/:*?"<>|]+/g, "-").slice(0, 120)}-完整总结.md`,
      `${lines.join("\n")}\n`,
      "text/markdown;charset=utf-8",
    );
    return;
  }
  const lines = ["# 论文完整详细总结", "", `> 论文：${paper.title}`, ""];
  let lastSection = "";
  let sectionIndex = 0;
  let itemIndex = 0;
  for (const pair of paper.summary_pairs) {
    const section = pair.section_zh || "详细总结";
    if (section !== lastSection) {
      sectionIndex += 1;
      itemIndex = 0;
      if (lastSection) lines.push("");
      lines.push(`## ${chineseSectionNumber(sectionIndex)}、${section}`, "");
      lastSection = section;
    }
    itemIndex += 1;
    const refs = Array.isArray(pair.page_refs) && pair.page_refs.length
      ? ` _（论文第 ${pair.page_refs.join("、")} 页）_`
      : "";
    lines.push(`${itemIndex}. ${markdownEmphasis(pair.zh || pair.en || "")}${refs}`);
  }
  const markdown = `${lines.join("\n")}\n`;
  const blobUrl = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = blobUrl;
  link.download = `${paper.title.replace(/[\\/:*?"<>|]+/g, "-").slice(0, 120)}-中文摘要.md`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
}

function markdownEmphasis(text) {
  return String(text).replace(
    /\b(Qwen2\.5-7B|Conformer-MoE|LLM-ASR|GLCLAP|GRPO|RADA|LoRA|KER|SACC|WER|Top-?\d+|\d+(?:\.\d+)?(?:%|[kKMB])?)\b/g,
    "**$1**",
  );
}

function activatePair(index, sourceLanguage) {
  state.activePair = index;
  $$(".summary-segment").forEach((segment) => {
    segment.classList.toggle("active", Number(segment.dataset.index) === index);
  });
  const targetLanguage = sourceLanguage === "en" ? "zh" : "en";
  const source = $(`.summary-segment[data-language="${sourceLanguage}"][data-index="${index}"]`);
  const target = $(`.summary-segment[data-language="${targetLanguage}"][data-index="${index}"]`);
  const targetScroller = target?.closest(".summary-content");
  if (source && target && targetScroller) {
    const delta = target.getBoundingClientRect().top - source.getBoundingClientRect().top;
    targetScroller.scrollTo({
      top: targetScroller.scrollTop + delta,
      behavior: "smooth",
    });
  }
  const pair = state.currentPaper?.summary_pairs?.[index];
  const label = $("#termLinkLabel");
  $("b", label).textContent = `句对 ${String(index + 1).padStart(2, "0")}`;
  if (pair) label.title = `${pair.en} ⇄ ${pair.zh}`;
}

function activateTerm(index, sourceLanguage, event) {
  event.preventDefault();
  window.clearTimeout(state.pairClickTimer);
  state.pairClickTimer = null;
  const selection = window.getSelection();
  const selected = selection?.toString().trim() || englishWordAtPoint(event);
  const selectionRect = selection?.rangeCount
    ? selection.getRangeAt(0).getBoundingClientRect()
    : { left: event.clientX, right: event.clientX, top: event.clientY, bottom: event.clientY, width: 0 };
  const pair = state.currentPaper?.summary_pairs?.[index];
  if (!pair) return;
  activatePair(index, sourceLanguage);
  const term = cleanSelectedEnglish(selected);
  if (sourceLanguage === "en" && term) {
    translateAndShowWord(
      {
        term_en: term,
        translation_zh: "",
        paper_id: state.currentPaper.id,
        paper_title: state.currentPaper.title,
        source_pair_index: index,
        context_en: pair.en || "",
        context_zh: pair.zh || "",
      },
      selectionRect,
    );
  }
  selection?.removeAllRanges();
}

function englishWordAtPoint(event) {
  let node = null;
  let offset = 0;
  if (document.caretPositionFromPoint) {
    const position = document.caretPositionFromPoint(event.clientX, event.clientY);
    node = position?.offsetNode || null;
    offset = position?.offset || 0;
  } else if (document.caretRangeFromPoint) {
    const range = document.caretRangeFromPoint(event.clientX, event.clientY);
    node = range?.startContainer || null;
    offset = range?.startOffset || 0;
  }
  if (!node || node.nodeType !== Node.TEXT_NODE) return "";
  const parent = node.parentElement;
  if (!parent?.closest(".segment-text")) return "";
  const text = node.textContent || "";
  const isWordCharacter = (character) => /[A-Za-z0-9+.#'\u2019-]/.test(character || "");
  let start = Math.min(offset, text.length);
  let end = start;
  while (start > 0 && isWordCharacter(text[start - 1])) start -= 1;
  while (end < text.length && isWordCharacter(text[end])) end += 1;
  return text.slice(start, end);
}

function cleanSelectedEnglish(value) {
  const cleaned = String(value || "")
    .trim()
    .replace(/^[^A-Za-z0-9]+|[^A-Za-z0-9+.#'’-]+$/g, "")
    .slice(0, 240);
  return /[A-Za-z]/.test(cleaned) ? cleaned : "";
}

async function translateAndShowWord(candidate, anchorRect) {
  const requestId = `${Date.now()}-${Math.random()}`;
  candidate.requestId = requestId;
  showWordPopover(candidate, anchorRect, true);
  try {
    const result = await api("/api/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: candidate.term_en,
        context: candidate.context_en || "",
        context_zh: candidate.context_zh || "",
      }),
    });
    if (state.vocabularyCandidate?.requestId !== requestId) return;
    candidate.translation_zh = result.translation_zh;
    candidate.phonetic_us = result.phonetic_us || "";
    candidate.definition_zh = result.definition_zh || "";
    state.vocabularyCandidate = candidate;
    $("#wordPopoverPhonetic").textContent = candidate.phonetic_us;
    $("#wordPopoverTranslation").textContent = result.translation_zh;
    $("#wordPopoverTranslation").classList.remove("missing");
    $("#wordPopoverDefinition").textContent = result.definition_zh || "";
    $("#addVocabularyButton").disabled = false;
    $("#addVocabularyButton").textContent = "加入单词本";
    window.requestAnimationFrame(() => positionWordPopover($("#wordPopover"), anchorRect));
  } catch (error) {
    if (state.vocabularyCandidate?.requestId !== requestId) return;
    $("#wordPopoverTranslation").textContent = "翻译失败，可手动补充释义";
    $("#wordPopoverTranslation").classList.add("missing");
    $("#wordPopoverDefinition").textContent = "";
    $("#addVocabularyButton").disabled = false;
    $("#addVocabularyButton").textContent = "编辑后加入";
  }
}

function showWordPopover(candidate, anchorRect, loading = false) {
  state.vocabularyCandidate = candidate;
  const popover = $("#wordPopover");
  $("#wordPopoverTerm").textContent = candidate.term_en;
  $("#wordPopoverPhonetic").textContent = candidate.phonetic_us || "";
  const translation = $("#wordPopoverTranslation");
  translation.textContent = loading ? "正在翻译..." : candidate.translation_zh || "添加时补充中文释义";
  translation.classList.toggle("missing", loading || !candidate.translation_zh);
  $("#wordPopoverDefinition").textContent = candidate.definition_zh || "";
  $("#addVocabularyButton").disabled = loading;
  $("#addVocabularyButton").textContent = loading ? "翻译中" : "加入单词本";
  popover.hidden = false;

  positionWordPopover(popover, anchorRect);
}

function positionWordPopover(popover, anchorRect) {
  const box = popover.getBoundingClientRect();
  const center = anchorRect.left + (anchorRect.width || 0) / 2;
  const left = Math.max(12, Math.min(window.innerWidth - box.width - 12, center - box.width / 2));
  let top = anchorRect.bottom + 10;
  if (top + box.height > window.innerHeight - 12) top = anchorRect.top - box.height - 10;
  popover.style.left = `${left}px`;
  popover.style.top = `${Math.max(12, top)}px`;
}

function closeWordPopover() {
  $("#wordPopover").hidden = true;
  state.vocabularyCandidate = null;
}

function renderVisualAsset(asset, paperId, language) {
  const figure = make("figure", "visual-item inline-visual");
  const assetKey = visualAssetKey(asset);
  figure.tabIndex = 0;
  figure.setAttribute("role", "button");
  figure.dataset.assetKey = assetKey;
  figure.dataset.language = language;
  const image = document.createElement("img");
  image.src = `/api/papers/${paperId}/assets/${asset.filename}`;
  image.alt = language === "zh" ? asset.title_zh : asset.title_en;
  image.loading = "lazy";
  const caption = make("figcaption", "", `${language === "zh" ? asset.title_zh : asset.title_en} · p. ${asset.page}`);
  const openPage = () => activateVisualAsset(asset, language);
  figure.addEventListener("click", openPage);
  figure.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openPage();
    }
  });
  figure.append(image, caption);
  return figure;
}

function visualAssetKey(asset) {
  return `${asset.page || 0}:${asset.filename || ""}`;
}

function activateVisualAsset(asset, language) {
  const assetKey = visualAssetKey(asset);
  state.activeVisualAssetKey = assetKey;
  const figures = $$(".visual-item", $("#readerGrid"));
  figures.forEach((figure) => figure.classList.toggle("active", figure.dataset.assetKey === assetKey));
  const counterpart = figures.find((figure) => (
    figure.dataset.assetKey === assetKey && figure.dataset.language !== language
  ));
  counterpart?.scrollIntoView({ behavior: "smooth", block: "center" });
  goToPdfPage(asset.page);
}

function showUploadDialog() {
  $("#uploadForm").reset();
  $("#dropTitle").textContent = "选择一个或多个 PDF";
  $("#dropMeta").textContent = "或拖放到此处，单个文件最大 100 MB";
  $("#uploadFileQueue").replaceChildren();
  $("#uploadFileQueue").hidden = true;
  $("#batchUploadNote").hidden = true;
  $("#singlePaperMetadata").hidden = false;
  $("#uploadSubmitButton").textContent = "开始导入";
  $("#uploadSubmitButton").disabled = false;
  renderTagPickers();
  updateProviderHint();
  openDialog("uploadDialog");
}

function showEditDialog() {
  const paper = state.currentPaper;
  if (!paper) return;
  const form = $("#editForm");
  form.elements.title.value = paper.title || "";
  form.elements.authors.value = paper.authors || "";
  form.elements.publication_year.value = paper.publication_year || "";
  form.elements.doi.value = paper.doi || "";
  form.elements.summary_en.value = (paper.summary_pairs || []).map((pair) => pair.en || "").join("\n");
  form.elements.summary_zh.value = (paper.summary_pairs || []).map((pair) => pair.zh || "").join("\n");
  const legacyEditor = $("#legacySummaryEditor");
  const usesBlocks = Boolean(paper.summary_blocks?.length);
  legacyEditor.hidden = usesBlocks;
  form.elements.summary_en.disabled = usesBlocks;
  form.elements.summary_zh.disabled = usesBlocks;
  renderTagPickers();
  const selected = new Set(paper.tags.map((tag) => tag.id));
  $$("input", $("#editTagPicker")).forEach((input) => { input.checked = selected.has(input.value); });
  openDialog("editDialog");
}

function updateProviderHint() {
  const remote = state.settings.provider === "openai_compatible";
  $("#uploadProviderHint").textContent = remote
    ? `${state.settings.analysis_model || state.settings.model || "OpenAI 兼容模型"}`
    : "本地结构索引（非机器翻译）";
}

function showSettingsDialog() {
  const form = $("#settingsForm");
  for (const [key, value] of Object.entries(state.settings)) {
    if (form.elements[key]) form.elements[key].value = value;
  }
  updateRemoteSettingsVisibility();
  openDialog("settingsDialog");
}

function updateRemoteSettingsVisibility() {
  const provider = $("#settingsForm").elements.provider.value;
  $("#remoteSettings").classList.toggle("disabled", provider !== "openai_compatible");
}

async function handleUpload(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const files = [...$("#pdfInput").files];
  if (!files.length || state.batchRunning) {
    toast("请选择 PDF 文件", "error");
    return;
  }
  const invalid = files.find((file) => !file.name.toLowerCase().endsWith(".pdf"));
  if (invalid) {
    toast(`“${invalid.name}”不是 PDF 文件`, "error");
    return;
  }
  const oversized = files.find((file) => file.size > 100 * 1024 * 1024);
  if (oversized) {
    toast(`“${oversized.name}”超过 100 MB`, "error");
    return;
  }

  state.batchRunning = true;
  const submit = $("#uploadSubmitButton");
  submit.disabled = true;
  const successes = [];
  const failures = [];
  let deduplicated = 0;
  const tagIds = $$("#uploadTagPicker input:checked").map((input) => input.value);
  for (let index = 0; index < files.length; index += 1) {
    const file = files[index];
    submit.textContent = `正在导入 ${index + 1} / ${files.length}`;
    setUploadFileStatus(index, "processing", form.elements.generate_summary.checked ? "解析并生成摘要" : "正在解析 PDF");
    const data = new FormData(form);
    data.delete("file");
    data.append("file", file, file.name);
    data.set("generate_summary", form.elements.generate_summary.checked ? "1" : "0");
    data.set("tag_ids", JSON.stringify(tagIds));
    if (files.length > 1) {
      for (const field of ["title", "authors", "publication_year", "doi"]) data.delete(field);
    }
    try {
      const result = await api("/api/papers", { method: "POST", body: data });
      successes.push(result.paper);
      deduplicated += Number(result.deduplicated_count) || 0;
      setUploadFileStatus(index, "success", result.paper.summary_status === "error" ? "已导入，摘要失败" : "导入完成");
    } catch (error) {
      failures.push({ file, message: error.message });
      setUploadFileStatus(index, "error", error.message || "导入失败");
    }
  }

  await Promise.all([loadLibrary(), loadTags()]).catch(handleError);
  state.batchRunning = false;
  submit.disabled = false;
  if (failures.length) {
    const transfer = new DataTransfer();
    failures.forEach(({ file }) => transfer.items.add(file));
    $("#pdfInput").files = transfer.files;
    showSelectedFiles(transfer.files);
    failures.forEach((failure, index) => setUploadFileStatus(index, "error", failure.message));
    submit.textContent = "重试失败项";
    toast(`${successes.length} 篇导入成功，${failures.length} 篇失败，可直接重试失败项`, "error");
    return;
  }
  closeDialog("uploadDialog");
  toast(deduplicated
    ? `${successes.length} 篇导入完成，并合并 ${deduplicated} 条同名旧记录`
    : `${successes.length} 篇论文已导入本地资料库`);
  if (successes.length === 1) navigateToPaper(successes[0].id);
  else location.hash = "";
}

async function handleEdit(event) {
  event.preventDefault();
  const paper = state.currentPaper;
  if (!paper) return;
  const form = event.currentTarget;
  const payload = {
    title: form.elements.title.value,
    authors: form.elements.authors.value,
    publication_year: form.elements.publication_year.value,
    doi: form.elements.doi.value,
    tag_ids: $$("#editTagPicker input:checked").map((input) => input.value),
  };
  if (!paper.summary_blocks?.length) {
    const english = form.elements.summary_en.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const chinese = form.elements.summary_zh.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const count = Math.max(english.length, chinese.length);
    payload.summary_pairs = Array.from({ length: count }, (_, index) => ({
      ...(paper.summary_pairs[index] || {}),
      en: english[index] || "",
      zh: chinese[index] || "",
    }));
  }
  try {
    const result = await api(`/api/papers/${paper.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.currentPaper = result.paper;
    renderReader();
    closeDialog("editDialog");
    await Promise.all([loadLibrary(), loadTags()]);
    toast("论文信息已更新");
  } catch (error) {
    handleError(error);
  }
}

async function regenerateSummary() {
  const paper = state.currentPaper;
  if (!paper) return;
  if (state.settings.provider !== "openai_compatible") {
    showSettingsDialog();
    toast("详细双语摘要需要先配置 OpenAI 兼容模型", "error");
    return;
  }
  setBusy(true, "模型正在通读全文并撰写英文详细总结...");
  try {
    const result = await api(`/api/papers/${paper.id}/generate-summary`, { method: "POST" });
    state.currentPaper = result.paper;
    renderReader();
    await loadLibrary();
    setBusy(false);
    toast("英文详细总结已完成，正在生成中文翻译");
    await retrySummaryTranslation({ quietStart: true });
  } catch (error) {
    if (error.data?.paper) {
      state.currentPaper = error.data.paper;
      renderReader();
    }
    handleError(error);
  } finally {
    setBusy(false);
  }
}

async function retrySummaryTranslation({ quietStart = false } = {}) {
  const paper = state.currentPaper;
  if (!paper?.summary_blocks?.length || paper.summary_status === "translating") return;
  state.currentPaper = {
    ...paper,
    summary_status: "translating",
    summary_translation_status: "translating",
    summary_translation_error: "",
  };
  renderReader();
  if (!quietStart) toast("正在重新生成中文翻译");
  try {
    const result = await api(`/api/papers/${paper.id}/translate-summary`, { method: "POST" });
    state.currentPaper = result.paper;
    renderReader();
    await loadLibrary();
    toast("中文翻译已完成");
  } catch (error) {
    if (error.data?.paper) {
      state.currentPaper = error.data.paper;
      renderReader();
      await loadLibrary().catch(() => {});
    }
    handleError(error);
  }
}

async function deleteCurrentPaper() {
  const paper = state.currentPaper;
  if (!paper) return;
  const accepted = await confirmAction("移入回收站", `“${paper.title}”将从资料库隐藏，PDF、摘要、图表和批注会原样保留。`);
  if (!accepted) return;
  setBusy(true, "正在删除论文...");
  try {
    await api(`/api/papers/${paper.id}`, { method: "DELETE" });
    state.currentPaper = null;
    location.hash = "";
    await Promise.all([loadLibrary(), loadTags()]);
    toast("论文已移入回收站");
  } catch (error) {
    handleError(error);
  } finally {
    setBusy(false);
  }
}

async function createTag(event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await api("/api/tags", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: form.elements.name.value, color: form.elements.color.value }),
    });
    form.elements.name.value = "";
    await loadTags();
    toast("标签已添加");
  } catch (error) {
    handleError(error);
  }
}

async function saveTag(tagId, name, color) {
  try {
    await api(`/api/tags/${tagId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, color }),
    });
    await Promise.all([loadTags(), loadLibrary()]);
    if (state.currentPaper) await refreshCurrentPaper();
    toast("标签已更新");
  } catch (error) {
    handleError(error);
    await loadTags();
  }
}

async function deleteTag(tag) {
  const accepted = await confirmAction("删除标签", `删除“${tag.name}”标签，不会删除关联论文。`);
  if (!accepted) return;
  try {
    await api(`/api/tags/${tag.id}`, { method: "DELETE" });
    if (state.tagId === tag.id) state.tagId = "";
    await Promise.all([loadTags(), loadLibrary()]);
    if (state.currentPaper) await refreshCurrentPaper();
    toast("标签已删除");
  } catch (error) {
    handleError(error);
  }
}

async function refreshCurrentPaper() {
  if (!state.currentPaper) return;
  const data = await api(`/api/papers/${state.currentPaper.id}`);
  state.currentPaper = data.paper;
  renderReader();
}

async function saveSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form).entries());
  try {
    const result = await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.settings = result.settings;
    updateProviderHint();
    renderQaPanel();
    closeDialog("settingsDialog");
    toast("模型设置已保存");
  } catch (error) {
    handleError(error);
  }
}

function confirmAction(title, message) {
  return new Promise((resolve) => {
    const dialog = $("#confirmDialog");
    $("#confirmTitle").textContent = title;
    $("#confirmMessage").textContent = message;
    const accept = $("#confirmAccept");
    const destructive = title.includes("删除");
    accept.textContent = destructive ? "确认删除" : "确认执行";
    accept.className = destructive ? "danger-button" : "primary-button";
    const onClick = (event) => {
      const button = event.target.closest("[data-confirm-value]");
      if (!button) return;
      dialog.removeEventListener("click", onClick);
      dialog.removeEventListener("cancel", onCancel);
      dialog.close();
      resolve(button.dataset.confirmValue === "true");
    };
    const onCancel = (event) => {
      event.preventDefault();
      dialog.removeEventListener("click", onClick);
      resolve(false);
      dialog.close();
    };
    dialog.addEventListener("click", onClick);
    dialog.addEventListener("cancel", onCancel, { once: true });
    dialog.showModal();
  });
}

function showSelectedFiles(fileList) {
  const files = [...(fileList || [])];
  const queue = $("#uploadFileQueue");
  queue.replaceChildren();
  if (!files.length) {
    $("#dropTitle").textContent = "选择一个或多个 PDF";
    $("#dropMeta").textContent = "或拖放到此处，单个文件最大 100 MB";
    queue.hidden = true;
    $("#batchUploadNote").hidden = true;
    $("#singlePaperMetadata").hidden = false;
    return;
  }
  const multiple = files.length > 1;
  $("#dropTitle").textContent = multiple ? `已选择 ${files.length} 个 PDF` : files[0].name;
  $("#dropMeta").textContent = multiple
    ? `合计 ${formatBytes(files.reduce((sum, file) => sum + file.size, 0))}`
    : formatBytes(files[0].size);
  $("#batchUploadNote").hidden = !multiple;
  $("#singlePaperMetadata").hidden = multiple;
  if (multiple) {
    const form = $("#uploadForm");
    for (const field of ["title", "authors", "publication_year", "doi"]) {
      form.elements[field].value = "";
    }
  }
  queue.hidden = false;
  files.forEach((file, index) => {
    const row = make("div", "upload-queue-row");
    row.dataset.fileIndex = String(index);
    const icon = make("span", "upload-queue-icon", "PDF");
    const identity = make("div", "upload-queue-identity");
    identity.append(make("strong", "", file.name), make("span", "", formatBytes(file.size)));
    row.append(icon, identity, make("span", "upload-queue-status", "等待导入"));
    queue.append(row);
  });
}

function setUploadFileStatus(index, status, text) {
  const row = $(`[data-file-index="${index}"]`, $("#uploadFileQueue"));
  if (!row) return;
  row.dataset.status = status;
  $(".upload-queue-status", row).textContent = text;
}

function handleError(error) {
  if (!error.data?.error_code) console.error(error);
  toast(error.message || "操作失败", "error");
}

function togglePanel(panelName) {
  if (!(panelName in state.panelCollapsed)) return;
  state.panelCollapsed[panelName] = !state.panelCollapsed[panelName];
  updatePanelLayout();
  closeWordPopover();
  closePdfSelectionToolbar(true);
  closeAnnotationPopover();
}

function updatePanelLayout() {
  if (state.readerMode !== "deep") {
    const compactAnalysis = window.innerWidth <= 760;
    $("#readerGrid").style.gridTemplateColumns = compactAnalysis
      ? "100% 100%"
      : "minmax(360px, .95fr) minmax(440px, 1.35fr)";
    $("#pdfPanel")?.classList.remove("collapsed");
    return;
  }
  const labels = { pdf: "原始论文", english: "英文摘要", chinese: "中文摘要" };
  const compact = window.innerWidth <= 760;
  const medium = window.innerWidth <= 1080;
  const expandedTracks = compact
    ? { pdf: "92vw", english: "84vw", chinese: "84vw" }
    : medium
      ? { pdf: "minmax(330px, 1fr)", english: "minmax(280px, .82fr)", chinese: "minmax(280px, .82fr)" }
      : { pdf: "minmax(360px, 1.16fr)", english: "minmax(300px, .92fr)", chinese: "minmax(300px, .92fr)" };
  const tracks = ["pdf", "english", "chinese"].map((name) => (
    state.panelCollapsed[name] ? "44px" : expandedTracks[name]
  ));
  $("#readerGrid").style.gridTemplateColumns = tracks.join(" ");
  for (const [name, collapsed] of Object.entries(state.panelCollapsed)) {
    const panel = $(`[data-panel="${name}"]`);
    const button = $(`[data-collapse-panel="${name}"]`);
    panel?.classList.toggle("collapsed", collapsed);
    if (button) {
      button.textContent = collapsed ? "+" : "−";
      button.setAttribute("aria-expanded", String(!collapsed));
      button.title = `${collapsed ? "展开" : "折叠"}${labels[name]}`;
      button.setAttribute("aria-label", button.title);
    }
  }
}

function bindEvents() {
  $("#uploadButton").addEventListener("click", showUploadDialog);
  $("#emptyUploadButton").addEventListener("click", showUploadDialog);
  $("#settingsButton").addEventListener("click", showSettingsDialog);
  $("#vocabularyButton").addEventListener("click", showVocabularyDialog);
  $("#manageTagsButton").addEventListener("click", () => { renderTagManager(); openDialog("tagsDialog"); });
  $("#sidebarManageTagsButton").addEventListener("click", () => { renderTagManager(); openDialog("tagsDialog"); });
  $("#backButton").addEventListener("click", () => { location.hash = ""; });
  $("#editPaperButton").addEventListener("click", showEditDialog);
  $("#regenerateButton").addEventListener("click", regenerateSummary);
  $$('[data-reader-mode]').forEach((button) => {
    button.addEventListener("click", () => {
      setReaderMode(button.dataset.readerMode);
      focusReaderModePanel(button.dataset.readerMode);
    });
  });
  $("#quickReadButton").addEventListener("click", () => queueAnalysis("quick_read").catch(handleError));
  $("#figureAnalysisButton").addEventListener("click", () => queueAnalysis("figure_analysis").catch(handleError));
  $("#quickRunSelect").addEventListener("change", (event) => {
    state.analysisSelections.quick_read = event.target.value;
    renderAnalysisPanels();
  });
  $("#figureRunSelect").addEventListener("change", (event) => {
    state.analysisSelections.figure_analysis = event.target.value;
    renderAnalysisPanels();
  });
  $("#qaForm").addEventListener("submit", handleQaSubmit);
  $("#downloadMarkdownButton").addEventListener("click", downloadSummaryMarkdown);
  $("#translateSummaryButton").addEventListener("click", () => retrySummaryTranslation());
  $("#deletePaperButton").addEventListener("click", deleteCurrentPaper);
  $("#pdfPreviousPage").addEventListener("click", () => goToPdfPage(state.pdfCurrentPage - 1));
  $("#pdfNextPage").addEventListener("click", () => goToPdfPage(state.pdfCurrentPage + 1));
  $("#pdfZoomOut").addEventListener("click", () => setPdfZoom(state.pdfZoom - 0.1));
  $("#pdfZoomIn").addEventListener("click", () => setPdfZoom(state.pdfZoom + 0.1));
  $("#pdfFitWidth").addEventListener("click", () => setPdfZoom(1));
  $("#pdfViewer").addEventListener("wheel", handlePdfWheel, { passive: false });
  $("#pdfPageInput").addEventListener("change", (event) => goToPdfPage(event.target.value));
  $("#pdfPageInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      goToPdfPage(event.currentTarget.value);
    }
  });
  $("#pdfViewer").addEventListener("scroll", () => {
    closeWordPopover();
    closePdfSelectionToolbar(true);
    closeAnnotationPopover();
    if (!state.pdfScrollFrame) state.pdfScrollFrame = requestAnimationFrame(updatePdfCurrentPage);
  });
  $("#uploadForm").addEventListener("submit", handleUpload);
  $("#batchTagsForm").addEventListener("submit", handleBatchTags);
  $("#editForm").addEventListener("submit", handleEdit);
  $("#newTagForm").addEventListener("submit", createTag);
  $("#settingsForm").addEventListener("submit", saveSettings);
  $("#vocabularyForm").addEventListener("submit", handleVocabularyForm);
  $("#addVocabularyButton").addEventListener("click", addVocabularyCandidate);
  $("#closeWordPopover").addEventListener("click", closeWordPopover);
  $("#highlightSelectionButton").addEventListener("click", () => createPdfAnnotation(false).catch(handleError));
  $("#noteSelectionButton").addEventListener("click", () => createPdfAnnotation(true).catch(handleError));
  $("#removeSelectionHighlightButton").addEventListener("click", () => removePdfSelectionHighlights().catch(handleError));
  $("#closeAnnotationPopover").addEventListener("click", closeAnnotationPopover);
  $("#saveAnnotationButton").addEventListener("click", () => saveAnnotation().catch(handleError));
  $("#deleteAnnotationButton").addEventListener("click", () => deleteAnnotation().catch(handleError));
  $("#annotationColors").addEventListener("click", (event) => {
    const button = event.target.closest("[data-annotation-color]");
    if (!button) return;
    state.editingAnnotationColor = button.dataset.annotationColor;
    $$('[data-annotation-color]', $("#annotationColors")).forEach((item) => {
      item.classList.toggle("active", item === button);
    });
  });
  $$('[data-collapse-panel]').forEach((button) => {
    button.addEventListener("click", () => togglePanel(button.dataset.collapsePanel));
  });
  $("#newVocabularyButton").addEventListener("click", () => showVocabularyEditor());
  $("#selectAllPapers").addEventListener("change", (event) => {
    state.papers.forEach((paper) => {
      if (event.target.checked) state.selectedPaperIds.add(paper.id);
      else state.selectedPaperIds.delete(paper.id);
    });
    renderLibrary();
  });
  $("#clearSelectionButton").addEventListener("click", () => clearPaperSelection());
  $("#batchQuickReadButton").addEventListener("click", () => queueSelectedQuickReads().catch(handleError));
  $("#batchTagsButton").addEventListener("click", showBatchTagsDialog);
  $("#batchDeleteButton").addEventListener("click", () => deleteSelectedPapers().catch(handleError));
  $("#batchToolbar").addEventListener("click", (event) => {
    const ratingButton = event.target.closest("[data-batch-rating]");
    if (ratingButton) setSelectedRating(Number(ratingButton.dataset.batchRating)).catch(handleError);
    const exportButton = event.target.closest("[data-batch-export]");
    if (exportButton) exportSelectedPapers(exportButton.dataset.batchExport);
  });
  $("#resetFiltersButton").addEventListener("click", resetLibraryFilters);
  $("#summaryFilters").addEventListener("click", (event) => {
    const button = event.target.closest("[data-summary-filter]");
    if (!button) return;
    state.summaryFilter = button.dataset.summaryFilter;
    clearPaperSelection(false);
    loadLibrary().catch(handleError);
  });
  $("#ratingFilters").addEventListener("click", (event) => {
    const button = event.target.closest("[data-rating-filter]");
    if (!button) return;
    state.ratingFilter = button.dataset.ratingFilter;
    clearPaperSelection(false);
    loadLibrary().catch(handleError);
  });
  $("#settingsForm").elements.provider.addEventListener("change", updateRemoteSettingsVisibility);
  $$("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => closeDialog(button.dataset.closeDialog)));
  $("#pdfInput").addEventListener("change", (event) => showSelectedFiles(event.target.files));

  const dropZone = $("#dropZone");
  for (const eventName of ["dragenter", "dragover"]) {
    dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.add("dragging"); });
  }
  for (const eventName of ["dragleave", "drop"]) {
    dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.remove("dragging"); });
  }
  dropZone.addEventListener("drop", (event) => {
    const files = [...event.dataTransfer.files].filter((file) => file.name.toLowerCase().endsWith(".pdf"));
    if (!files.length) return;
    const transfer = new DataTransfer();
    files.forEach((file) => transfer.items.add(file));
    $("#pdfInput").files = transfer.files;
    showSelectedFiles(transfer.files);
  });
  dropZone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      $("#pdfInput").click();
    }
  });

  let searchTimer;
  $("#searchInput").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.query = event.target.value.trim();
      clearPaperSelection(false);
      loadLibrary().catch(handleError);
    }, 220);
  });
  $("#clearSearchButton").addEventListener("click", () => {
    $("#searchInput").value = "";
    state.query = "";
    clearPaperSelection(false);
    loadLibrary().catch(handleError);
    $("#searchInput").focus();
  });
  $("#librarySort").addEventListener("change", (event) => {
    state.librarySort = event.target.value;
    loadLibrary().catch(handleError);
  });
  $("#vocabularySearch").addEventListener("input", (event) => {
    state.vocabularyQuery = event.target.value.trim();
    renderVocabulary();
  });
  $("#vocabularyStatusFilter").addEventListener("click", (event) => {
    const button = event.target.closest("[data-vocabulary-status]");
    if (!button) return;
    state.vocabularyStatus = button.dataset.vocabularyStatus;
    renderVocabulary();
  });
  $("#vocabularySort").addEventListener("change", (event) => {
    state.vocabularySort = event.target.value;
    renderVocabulary();
  });
  document.addEventListener("pointerdown", (event) => {
    const popover = $("#wordPopover");
    if (!popover.hidden && !popover.contains(event.target)) closeWordPopover();
    const selectionToolbar = $("#pdfSelectionToolbar");
    if (!selectionToolbar.hidden && !selectionToolbar.contains(event.target)) closePdfSelectionToolbar();
    const annotationPopover = $("#pdfAnnotationPopover");
    if (!annotationPopover.hidden && !annotationPopover.contains(event.target)) closeAnnotationPopover();
  });
  document.addEventListener("pointermove", trackPdfPointerSelection, { passive: false });
  document.addEventListener("pointerup", finishPdfPointerSelection, { passive: false });
  document.addEventListener("pointercancel", finishPdfPointerSelection, { passive: false });
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (!$("#libraryView").hidden) $("#searchInput").focus();
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "o") {
      event.preventDefault();
      if (!$("#libraryView").hidden) $("#uploadButton").click();
    }
    if (event.key === "Escape" && !$("#wordPopover").hidden) closeWordPopover();
    if (event.key === "Escape") {
      closePdfSelectionToolbar(true);
      closeAnnotationPopover();
    }
  });
  window.addEventListener("resize", () => {
    closeWordPopover();
    closePdfSelectionToolbar(true);
    closeAnnotationPopover();
    updatePanelLayout();
  });
  $("#englishSummary").addEventListener("scroll", closeWordPopover);
  window.addEventListener("hashchange", handleRoute);
}

async function init() {
  if (window.pywebview) await activateDesktopRuntime();
  bindEvents();
  try {
    await Promise.all([loadTags(), loadSettings()]);
    await loadLibrary();
    await handleRoute();
  } catch (error) {
    handleError(error);
  }
}

document.addEventListener("DOMContentLoaded", init);
