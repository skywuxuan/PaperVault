// Run with: node --test tests/test_frontend.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function app() {
  const nodes = new Map();
  const document = {
    body: { classList: { add() {}, remove() {}, toggle() {} } },
    querySelector: selector => {
      if (!nodes.has(selector)) {
        const classes = new Set();
        const attributes = new Map();
        nodes.set(selector, {
          value: '', textContent: '', hidden: false, disabled: false,
          replaceChildren() {}, focus() {}, querySelectorAll() { return []; },
          setAttribute(name, value) { attributes.set(name, String(value)); },
          getAttribute(name) { return attributes.get(name) ?? null; },
          classList: {
            add(...names) { names.forEach(name => classes.add(name)); },
            remove(...names) { names.forEach(name => classes.delete(name)); },
            contains(name) { return classes.has(name); },
            toggle(name, force) {
              const active = force ?? !classes.has(name);
              if (active) classes.add(name);
              else classes.delete(name);
              return active;
            },
          },
        });
      }
      return nodes.get(selector);
    },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  const context = vm.createContext({
    document, URLSearchParams, console, location: { hash: '' },
    window: { addEventListener() {}, clearTimeout() {}, setTimeout() { return 1; }, scrollTo() {} },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8'), context);
  const run = code => vm.runInContext(code, context);
  run('renderLibrary = () => {}; handleError = () => {};');
  return { context, run, nodes };
}

function prepareRoutes(run) {
  run(`closeWordPopover = closeNotesDrawer = stopAnalysisPolling = clearPdfPreview = renderReader = () => {};
    loadPaperAnalyses = async () => {};`);
}

async function settle() {
  for (let index = 0; index < 5; index += 1) await Promise.resolve();
}

test('a delayed library response cannot replace newer search results', async () => {
  const { context, run } = app();
  const first = deferred();
  const second = deferred();
  const requests = [first, second];
  context.api = () => requests.shift().promise;
  const older = run('state.query = "old"; loadLibrary()');
  const newer = run('state.query = "new"; loadLibrary()');
  second.resolve({ papers: [{ id: 'new' }] });
  await newer;
  first.resolve({ papers: [{ id: 'old' }] });
  await older;
  assert.equal(run('state.papers[0].id'), 'new');
});

test('note autosave keeps the original paper and captured text after navigation', async () => {
  const { context, run } = app();
  const calls = [];
  context.api = async (url, options) => {
    calls.push({ url, body: JSON.parse(options.body) });
    return { note: JSON.parse(options.body) };
  };
  run(`state.currentPaper = { id: 'paper-a' }; state.notePaperId = 'paper-a'; state.note = {};
    $('#noteTitleInput').value = 'A title'; $('#noteBodyInput').value = 'A draft'; queueNoteSave();
    state.currentPaper = { id: 'paper-b' }; state.notePaperId = 'paper-b'; state.note = { body: 'B text' };
    $('#noteBodyInput').value = 'B text';`);
  await run('flushNoteSave()');
  assert.equal(calls[0].url, '/api/papers/paper-a/notes');
  assert.equal(calls[0].body.body, 'A draft');
  assert.equal(run('state.note.body'), 'B text');
});

test('successive note writes are serialized and old responses preserve newer drafts', async () => {
  const { context, run } = app();
  const first = deferred();
  const calls = [];
  context.api = (url, options) => {
    const body = JSON.parse(options.body);
    calls.push(body);
    return calls.length === 1 ? first.promise : Promise.resolve({ note: body });
  };
  run(`state.currentPaper = { id: 'a' }; state.notePaperId = 'a'; state.note = {};
    $('#noteBodyInput').value = 'first'; queueNoteSave();`);
  const firstSave = run('flushNoteSave()');
  await Promise.resolve();
  await Promise.resolve();
  run(`$('#noteBodyInput').value = 'latest'; queueNoteSave();`);
  const lastSave = run('flushNoteSave()');
  assert.equal(calls.length, 1);
  first.resolve({ note: { body: 'first' } });
  await firstSave;
  assert.equal(run('state.note.body'), 'latest');
  await lastSave;
  assert.equal(calls[1].body, 'latest');
  assert.equal(run("$('#noteSaveState').textContent"), '已保存');
});

test('a failed note save retains its draft for retry', async () => {
  const { context, run } = app();
  context.api = async () => { throw new Error('offline'); };
  run(`state.currentPaper = { id: 'a' }; state.notePaperId = 'a'; state.note = {};
    $('#noteBodyInput').value = 'keep me'; queueNoteSave();`);
  await assert.rejects(run('flushNoteSave()'), /offline/);
  assert.equal(run('state.noteDraft.payload.body'), 'keep me');
  context.api = async (_, options) => ({ note: JSON.parse(options.body) });
  await run('flushNoteSave()');
  assert.equal(run('state.noteDraft'), null);
  assert.equal(run('state.note.body'), 'keep me');
});

test('navigation waits for the captured note save before loading another paper', async () => {
  const { context, run } = app();
  prepareRoutes(run);
  const save = deferred();
  const calls = [];
  context.api = (url, options) => {
    calls.push(url);
    return options?.method === 'PUT' ? save.promise : Promise.resolve({ paper: { id: 'b', read_state: 'read' } });
  };
  run(`state.currentPaper = { id: 'a' }; state.notePaperId = 'a'; state.note = {};
    $('#noteBodyInput').value = 'save before leaving'; queueNoteSave(); location.hash = '#paper/b';`);
  const navigation = run('handleRoute()');
  await settle();
  assert.deepEqual(calls, ['/api/papers/a/notes']);
  assert.equal(run('state.currentPaper.id'), 'a');
  save.resolve({ note: { body: 'save before leaving' } });
  await navigation;
  assert.equal(run('state.currentPaper.id'), 'b');
  assert.equal(run("$('#noteBodyInput').disabled"), true);
});

test('failed navigation keeps the original note draft and restores its route', async () => {
  const { context, run } = app();
  prepareRoutes(run);
  const calls = [];
  context.api = async url => { calls.push(url); throw new Error('offline'); };
  run(`state.currentPaper = { id: 'a' }; state.notePaperId = 'a'; state.note = {};
    $('#noteBodyInput').value = 'do not lose this'; queueNoteSave(); location.hash = '#paper/b';`);
  await run('handleRoute()');
  assert.deepEqual(calls, ['/api/papers/a/notes']);
  assert.equal(run('state.currentPaper.id'), 'a');
  assert.equal(run('state.noteDraft.payload.body'), 'do not lose this');
  assert.equal(run("location.hash.replace(/^#/, '')"), 'paper/a');
});

test('navigation also saves edits made while the first save is still pending', async () => {
  const { context, run } = app();
  prepareRoutes(run);
  const first = deferred();
  const second = deferred();
  const saves = [];
  context.api = (_, options) => {
    if (options?.method !== 'PUT') return Promise.resolve({ paper: { id: 'b', read_state: 'read' } });
    saves.push(JSON.parse(options.body));
    return saves.length === 1 ? first.promise : second.promise;
  };
  run(`state.currentPaper = { id: 'a' }; state.notePaperId = 'a'; state.note = {};
    $('#noteBodyInput').value = 'first draft'; queueNoteSave(); location.hash = '#paper/b';`);
  const navigation = run('handleRoute()');
  await settle();
  run("$('#noteBodyInput').value = 'last edit before leaving'; queueNoteSave();");
  first.resolve({ note: { body: 'first draft' } });
  await settle();
  assert.equal(run('state.currentPaper.id'), 'a');
  assert.equal(saves.length, 2);
  assert.equal(saves[1].body, 'last edit before leaving');
  second.resolve({ note: { body: 'last edit before leaving' } });
  await navigation;
  assert.equal(run('state.currentPaper.id'), 'b');
  assert.equal(run('state.noteDraft'), null);
});

test('note inputs stay disabled until the requested note is loaded', async () => {
  const { context, run } = app();
  const request = deferred();
  context.api = () => request.promise;
  run(`state.currentPaper = { id: 'b' }; state.note = null; state.notePaperId = null;
    $('#noteTitleInput').disabled = $('#noteBodyInput').disabled = true;`);
  const loading = run("loadPaperNote('b')");
  await settle();
  assert.equal(run("$('#noteBodyInput').disabled"), true);
  request.resolve({ note: { title: 'Loaded note', body: 'Saved text', quotes: [] } });
  await loading;
  assert.equal(run("$('#noteBodyInput').disabled"), false);
  assert.equal(run("$('#noteBodyInput').value"), 'Saved text');
  assert.equal(run('state.notePaperId'), 'b');
});

test('a previous note cannot unlock the editor while another paper is loading', async () => {
  const { context, run } = app();
  prepareRoutes(run);
  const oldNote = deferred();
  const newPaper = deferred();
  context.api = url => url.endsWith('/notes') ? oldNote.promise : newPaper.promise;
  run("state.currentPaper = { id: 'a' }; state.notePaperId = 'a'; state.note = null;");
  const noteLoading = run("loadPaperNote('a')");
  await settle();
  run("location.hash = '#paper/b'");
  const navigation = run('handleRoute()');
  await settle();
  oldNote.resolve({ note: { body: 'Old paper note', quotes: [] } });
  await noteLoading;
  assert.equal(run('state.currentPaper'), null);
  assert.equal(run('state.note'), null);
  assert.equal(run("$('#noteBodyInput').disabled"), true);
  newPaper.resolve({ paper: { id: 'b', read_state: 'read' } });
  await navigation;
});

test('long notes use ordinary autosave requests without the keepalive body limit', async () => {
  const { context, run } = app();
  let options;
  context.api = async (_, requestOptions) => {
    options = requestOptions;
    return { note: JSON.parse(requestOptions.body) };
  };
  run(`state.currentPaper = { id: 'a' }; state.notePaperId = 'a'; state.note = {};
    $('#noteBodyInput').value = '研究笔记'.repeat(10000); queueNoteSave();`);
  await run('flushNoteSave()');
  assert.ok(Buffer.byteLength(options.body) > 65536);
  assert.equal(Boolean(options.keepalive), false);
  assert.equal(run('state.note.body.length'), 40000);
});

test('a delayed route response cannot return the reader to the previous paper', async () => {
  const { context, run } = app();
  prepareRoutes(run);
  const first = deferred();
  const second = deferred();
  context.api = url => url === '/api/papers/a' ? first.promise : second.promise;
  run("location.hash = '#paper/a'");
  const older = run('handleRoute()');
  await settle();
  run("location.hash = '#paper/b'");
  const newer = run('handleRoute()');
  await settle();
  second.resolve({ paper: { id: 'b', read_state: 'read' } });
  await newer;
  first.resolve({ paper: { id: 'a', read_state: 'read' } });
  await older;
  assert.equal(run('state.currentPaper.id'), 'b');
});

test('a delayed paper refresh cannot overwrite the newly selected paper', async () => {
  const { context, run } = app();
  prepareRoutes(run);
  const request = deferred();
  context.api = () => request.promise;
  run("state.currentPaper = { id: 'a' }");
  const refresh = run('refreshCurrentPaper()');
  run("state.currentPaper = { id: 'b' }; state.routeRequestId += 1;");
  request.resolve({ paper: { id: 'a' } });
  await refresh;
  assert.equal(run('state.currentPaper.id'), 'b');
});

test('resource inspection cannot select a file that the user already replaced', async () => {
  const { context, run } = app();
  const request = deferred();
  context.fetch = () => request.promise;
  run('setResourcePackageProgress = () => {}; renderResourceRestoreSummary = () => {};');
  const pending = run("state.resourcePackageFile = { name: 'old.pvault' }; inspectResourcePackageUpload(state.resourcePackageFile)");
  run("state.resourcePackageFile = { name: 'new.pvault' }; state.resourcePackageId = 'new-id';");
  request.resolve({ ok: true, json: async () => ({ package_id: 'old-id', manifest: {} }) });
  await pending;
  assert.equal(run('state.resourcePackageId'), 'new-id');
});

test('a failed obsolete inspection cannot replace the current validation state', async () => {
  const { context, run } = app();
  const request = deferred();
  context.fetch = () => request.promise;
  run('setResourcePackageProgress = () => {};');
  const pending = run("state.resourcePackageFile = { name: 'old.pvault' }; inspectResourcePackageUpload(state.resourcePackageFile)");
  run(`state.resourcePackageFile = { name: 'new.pvault' }; state.resourcePackageId = 'new-id';
    $('#resourceRestoreFileMeta').textContent = 'Current file passed';`);
  request.reject(new Error('old file failed'));
  await pending;
  assert.equal(run('state.resourcePackageId'), 'new-id');
  assert.equal(run("$('#resourceRestoreFileMeta').textContent"), 'Current file passed');
});

test('startup does not require model configuration for the local library', async () => {
  const { run } = app();
  run(`bindEvents = () => {}; loadTags = loadSettings = loadLibrary = handleRoute = async () => {};
    handleError = error => { throw error; };
    bootstrapLocalSettings = showSettingsDialog = () => { throw new Error('unexpected model prompt'); };`);
  await run('init()');
});

function prepareReaderOperations(run) {
  run(`renderReader = renderSummarySourceControls = renderActiveSummarySource = closeDialog = toast = () => {};
    loadLibrary = loadTags = async () => {};`);
}

test('Markdown export downloads the displayed Doubao variant even without a native summary', () => {
  const { context, run } = app();
  const downloads = [];
  context.downloadTextFile = (...args) => downloads.push(args);
  run(`state.currentPaper = { id: 'a', title: 'Paper / A', summary_variants: [
    { id: 'new', provider: 'doubao', content_markdown: 'New report' },
    { id: 'cited', provider: 'doubao', title: 'Cited report', content_markdown: '## 原始章节\\n中文内容', english_markdown: '## Original section\\nEnglish content' }
  ] }; state.activeSummarySource = 'doubao'; state.activeSummaryVariantId = 'cited';
  downloadSummaryMarkdown();`);
  assert.equal(downloads.length, 1);
  assert.equal(downloads[0][0], 'Paper - A-豆包解析.md');
  assert.match(downloads[0][1], /## 原始章节\n中文内容/);
  assert.match(downloads[0][1], /## Original section\nEnglish content/);
  assert.doesNotMatch(downloads[0][1], /New report/);
});

test('an empty displayed Doubao report cannot silently export the native summary', () => {
  const { context, run } = app();
  const messages = [];
  context.toast = message => messages.push(message);
  context.downloadTextFile = () => assert.fail('unexpected download');
  run(`state.currentPaper = { id: 'a', title: 'A', summary_blocks: [{ text_zh: 'Native content' }],
    summary_variants: [{ id: 'empty', provider: 'doubao' }] };
    state.activeSummarySource = 'doubao'; downloadSummaryMarkdown();`);
  assert.deepEqual(messages, ['当前没有可导出的豆包解析']);
});

test('generating a summary after navigation translates the original paper without changing the reader', async () => {
  const { context, run } = app();
  prepareReaderOperations(run);
  const generation = deferred();
  const translation = deferred();
  const calls = [];
  context.api = url => {
    calls.push(url);
    return url.endsWith('/generate-summary') ? generation.promise : translation.promise;
  };
  run(`state.settings.provider = 'openai_compatible'; state.currentPaper = { id: 'a' }; state.papers = [state.currentPaper];`);
  const pending = run('regenerateSummary()');
  run("state.currentPaper = { id: 'b', title: 'Keep B' };");
  generation.resolve({ paper: { id: 'a', summary_status: 'english_ready', summary_blocks: [{ text_en: 'A' }] } });
  await settle();
  assert.deepEqual(calls, ['/api/papers/a/generate-summary', '/api/papers/a/translate-summary']);
  assert.equal(run('state.currentPaper.id'), 'b');
  translation.resolve({ paper: { id: 'a', summary_status: 'ready', summary_blocks: [{ text_zh: '甲' }] } });
  await pending;
  assert.equal(run('state.currentPaper.title'), 'Keep B');
  assert.equal(run('state.papers[0].summary_status'), 'ready');
});

test('a failed generation response cannot replace the newly selected paper', async () => {
  const { context, run } = app();
  prepareReaderOperations(run);
  const request = deferred();
  context.api = () => request.promise;
  run("state.settings.provider = 'openai_compatible'; state.currentPaper = { id: 'a' }; state.papers = [state.currentPaper];");
  const pending = run('regenerateSummary()');
  run("state.currentPaper = { id: 'b' };");
  const error = new Error('generation failed');
  error.data = { paper: { id: 'a', summary_status: 'error' } };
  request.reject(error);
  await pending;
  assert.equal(run('state.currentPaper.id'), 'b');
  assert.equal(run('state.papers[0].summary_status'), 'error');
});

test('saving metadata cannot replace another paper opened while the request is pending', async () => {
  const { context, run } = app();
  prepareReaderOperations(run);
  const request = deferred();
  context.api = () => request.promise;
  context.editEvent = { preventDefault() {}, currentTarget: { elements: Object.fromEntries(
    ['title', 'authors', 'publication_year', 'doi'].map(key => [key, { value: key === 'title' ? 'Edited A' : '' }]),
  ) } };
  run("state.currentPaper = { id: 'a', summary_blocks: [{}] }; state.papers = [state.currentPaper];");
  const pending = run('handleEdit(editEvent)');
  run("state.currentPaper = { id: 'b' };");
  request.resolve({ paper: { id: 'a', title: 'Edited A' } });
  await pending;
  assert.equal(run('state.currentPaper.id'), 'b');
  assert.equal(run('state.papers[0].title'), 'Edited A');
});

test('Doubao import keeps its paper and continues English generation after navigation', async () => {
  const { context, run } = app();
  prepareReaderOperations(run);
  run('setDoubaoImportLocked = setDoubaoImportProgress = () => {};');
  context.FormData = class { entries() { return [['url', 'https://example.test/share']]; } };
  context.importEvent = { preventDefault() {}, currentTarget: { elements: { generate_english: { checked: true } } } };
  const request = deferred();
  const calls = [];
  context.api = url => {
    calls.push(url);
    return url.endsWith('/summary-variants') ? request.promise : Promise.resolve({ variant: {
      id: 'variant-a', provider: 'doubao', english_status: 'ready', english_markdown: 'English A',
    } });
  };
  run("state.currentPaper = { id: 'a' }; state.papers = [state.currentPaper];");
  const pending = run('importDoubaoSummary(importEvent)');
  run("state.currentPaper = { id: 'b', summary_variants: [] }; state.activeSummarySource = 'native';");
  request.resolve({ variant: { id: 'variant-a', provider: 'doubao', english_status: 'pending', content_markdown: '中文 A' } });
  await pending;
  assert.deepEqual(calls, ['/api/papers/a/summary-variants', '/api/summary-variants/variant-a/translate-english']);
  assert.equal(run('state.currentPaper.summary_variants.length'), 0);
  assert.equal(run('state.activeSummarySource'), 'native');
  assert.equal(run('state.papers[0].summary_variants[0].english_status'), 'ready');
});

test('an older Doubao translation preserves reports imported while it was pending', async () => {
  const { context, run } = app();
  prepareReaderOperations(run);
  const request = deferred();
  context.api = () => request.promise;
  run(`state.currentPaper = { id: 'a', summary_variants: [{ id: 'old', provider: 'doubao', english_status: 'pending' }] };`);
  const pending = run("retryExternalEnglish('old')");
  run(`state.currentPaper = { id: 'a', summary_variants: [
    { id: 'new', provider: 'doubao', content_markdown: 'Keep this import' }, ...state.currentPaper.summary_variants] };
    state.activeSummaryVariantId = 'new';`);
  request.resolve({ variant: { id: 'old', provider: 'doubao', english_status: 'ready' } });
  await pending;
  assert.equal(run('state.currentPaper.summary_variants.length'), 2);
  assert.equal(run('currentDoubaoVariant().content_markdown'), 'Keep this import');
});

test('note citation navigation opens its saved Doubao variant and expands the correct panel', () => {
  const { context, run } = app();
  prepareReaderOperations(run);
  const calls = [];
  const target = { textContent: 'Cited original passage', scrollIntoView() { calls.push('scroll'); }, classList: { add() {} } };
  context.target = target;
  context.togglePanel = panel => calls.push(panel);
  context.closeNotesDrawer = () => calls.push('close');
  run(`state.currentPaper = { id: 'a', summary_variants: [
    { id: 'new', provider: 'doubao' }, { id: 'cited', provider: 'doubao' }] };
    state.activeSummarySource = 'native'; state.panelCollapsed.english = true;
    $('#englishSummary').querySelectorAll = () => [target];
    jumpToNoteQuote({ source_type: 'doubao_en', source_variant_id: 'cited', quote_text: 'Cited original passage' });`);
  assert.equal(run('state.activeSummarySource'), 'doubao');
  assert.equal(run('currentDoubaoVariant().id'), 'cited');
  assert.deepEqual(calls, ['english', 'close', 'scroll']);
});

test('native citations switch away from Doubao while missing external sources stay explicit', () => {
  const { context, run } = app();
  prepareReaderOperations(run);
  const messages = [];
  context.toast = message => messages.push(message);
  run(`state.currentPaper = { id: 'a', summary_variants: [{ id: 'new', provider: 'doubao' }] };
    state.activeSummarySource = 'doubao';
    jumpToNoteQuote({ source_type: 'native_zh', quote_text: 'Original text' });`);
  assert.equal(run('state.activeSummarySource'), 'native');
  run("jumpToNoteQuote({ source_type: 'doubao_zh', source_variant_id: 'deleted', quote_text: 'Original text' });");
  assert.equal(run('state.activeSummarySource'), 'native');
  assert.equal(messages.at(-1), '这条引用对应的豆包解析已不可用');
});

test('a highlight created on another paper cannot appear in the current PDF', async () => {
  const { context, run } = app();
  const request = deferred();
  context.api = () => request.promise;
  run("state.currentPaper = { id: 'a' }; state.pdfSelection = { page: 1, selected_text: 'A text' };");
  const pending = run('createPdfAnnotation(true)');
  run("state.currentPaper = { id: 'b' }; state.pdfAnnotations = [{ id: 'b-note', page: 1 }];");
  request.resolve({ annotation: { id: 'a-note', page: 1 } });
  await pending;
  assert.equal(run('state.pdfAnnotations.length'), 1);
  assert.equal(run('state.pdfAnnotations[0].id'), 'b-note');
});

test('an edit dialog retains its original paper when the route changes before submission', async () => {
  const { context, run } = app();
  prepareReaderOperations(run);
  const calls = [];
  context.api = async (url, options) => {
    calls.push({ url, payload: JSON.parse(options.body) });
    return { paper: { id: 'a', title: 'Edited A' } };
  };
  context.editEvent = { preventDefault() {}, currentTarget: { elements: Object.fromEntries(
    ['title', 'authors', 'publication_year', 'doi'].map(key => [key, { value: key === 'title' ? 'Edited A' : '' }]),
  ) } };
  run("state.editingPaper = { id: 'a', summary_blocks: [{}] }; state.currentPaper = { id: 'b' };");
  await run('handleEdit(editEvent)');
  assert.equal(calls[0].url, '/api/papers/a');
  assert.equal(calls[0].payload.title, 'Edited A');
  assert.equal(run('state.currentPaper.id'), 'b');
});

test('switching summary sources hides the native translation action and clears stale selections', () => {
  const { run } = app();
  run(`renderSummaries = renderExternalSummary = () => {};
    state.currentPaper = { id: 'a', summary_blocks: [{}], summary_variants: [{ id: 'external', provider: 'doubao' }] };
    state.summarySelection = { text: 'Native selection', language: 'en' };
    state.activeSummarySource = 'doubao'; renderActiveSummarySource();`);
  assert.equal(run("$('#translateSummaryButton').hidden"), true);
  assert.equal(run('state.summarySelection'), null);
  run("state.activeSummarySource = 'native'; renderActiveSummarySource();");
  assert.equal(run("$('#translateSummaryButton').hidden"), false);
});

test('English summary clicks select a paragraph and dictionary lookup requires a double click', () => {
  const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
  const segment = source.slice(source.indexOf('function summarySegment'), source.indexOf('function appendSummaryText'));
  const external = source.slice(source.indexOf('function decorateExternalBlock'), source.indexOf('function activateExternalTerm'));
  assert.match(segment, /segment\.addEventListener\("dblclick"[\s\S]*activateTerm/);
  assert.doesNotMatch(segment.slice(segment.indexOf('segment.addEventListener("click"'), segment.indexOf('segment.addEventListener("dblclick"')), /activateTerm|showEnglishTermAtEvent/);
  assert.match(external, /node\.addEventListener\("dblclick"[\s\S]*activateExternalTerm/);
  assert.doesNotMatch(external.slice(external.indexOf('node.addEventListener("click"'), external.indexOf('node.addEventListener("dblclick"')), /activateExternalTerm|showEnglishTermAtEvent/);
});

test('summary source buttons keep their labels and source when clicked repeatedly', () => {
  const { context, run, nodes } = app();
  const renderedSources = [];
  context.renderActiveSummarySource = () => renderedSources.push(run('state.activeSummarySource'));
  run(`state.currentPaper = { summary_blocks: [{}], summary_variants: [{ id: 'd', provider: 'doubao' }] };
    renderSummarySourceControls();`);

  for (const [action, expectedSource] of [
    ['handleDoubaoAction()', 'doubao'],
    ['handleDoubaoAction()', 'doubao'],
    ['handleNativeSummaryAction()', 'native'],
    ['handleNativeSummaryAction()', 'native'],
    ['handleDoubaoAction()', 'doubao'],
  ]) {
    run(action);
    assert.equal(run('state.activeSummarySource'), expectedSource);
    assert.equal(renderedSources.at(-1), expectedSource);
    assert.equal(nodes.get('#summaryActionLabel').textContent, '默认解析');
    assert.equal(nodes.get('#doubaoImportLabel').textContent, '豆包解析');
    assert.equal(nodes.get('#regenerateButton').classList.contains('active-source'), expectedSource === 'native');
    assert.equal(nodes.get('#doubaoImportButton').classList.contains('active-source'), expectedSource === 'doubao');
    assert.equal(nodes.get('#regenerateButton').getAttribute('aria-pressed'), String(expectedSource === 'native'));
    assert.equal(nodes.get('#doubaoImportButton').getAttribute('aria-pressed'), String(expectedSource === 'doubao'));
  }
});

test('missing summaries use generation and import actions independently', () => {
  const { context, run, nodes } = app();
  const calls = [];
  context.regenerateSummary = () => calls.push('generate');
  context.showDoubaoImportDialog = () => calls.push('import');
  context.renderActiveSummarySource = () => { throw new Error('An absent source must not be selected'); };
  run(`state.currentPaper = { summary_variants: [{ id: 'd', provider: 'doubao' }] };
    state.activeSummarySource = 'doubao'; renderSummarySourceControls(); handleNativeSummaryAction();`);
  assert.equal(nodes.get('#summaryActionLabel').textContent, '生成摘要');
  assert.equal(nodes.get('#doubaoImportLabel').textContent, '豆包解析');
  assert.equal(run('state.activeSummarySource'), 'doubao');
  assert.equal(nodes.get('#regenerateButton').classList.contains('active-source'), false);
  run(`state.currentPaper = { summary_pairs: [{}], summary_variants: [] };
    state.activeSummarySource = 'native'; renderSummarySourceControls(); handleDoubaoAction();`);
  assert.equal(nodes.get('#summaryActionLabel').textContent, '默认解析');
  assert.equal(nodes.get('#doubaoImportLabel').textContent, '导入豆包');
  assert.equal(run('state.activeSummarySource'), 'native');
  assert.equal(nodes.get('#doubaoImportButton').classList.contains('active-source'), false);
  assert.deepEqual(calls, ['generate', 'import']);
});

test('refreshing source controls synchronizes both buttons after import, navigation, or a missing variant', () => {
  const { run, nodes } = app();
  run(`state.currentPaper = { summary_pairs: [{}], summary_variants: [] };
    renderSummarySourceControls();
    state.currentPaper.summary_variants = [{ id: 'imported', provider: 'doubao' }];
    state.activeSummarySource = 'doubao'; state.activeSummaryVariantId = 'imported';
    renderSummarySourceControls();`);
  assert.equal(nodes.get('#regenerateButton').classList.contains('active-source'), false);
  assert.equal(nodes.get('#doubaoImportButton').classList.contains('active-source'), true);
  assert.equal(nodes.get('#doubaoImportLabel').textContent, '豆包解析');
  run(`state.currentPaper.summary_variants = []; renderSummarySourceControls();`);
  assert.equal(run('state.activeSummarySource'), 'native');
  assert.equal(run('state.activeSummaryVariantId'), null);
  assert.equal(nodes.get('#regenerateButton').classList.contains('active-source'), true);
  assert.equal(nodes.get('#doubaoImportButton').classList.contains('active-source'), false);
  assert.equal(nodes.get('#doubaoImportLabel').textContent, '导入豆包');
  run(`state.currentPaper = {}; renderSummarySourceControls();`);
  assert.equal(nodes.get('#summaryActionLabel').textContent, '生成摘要');
  assert.equal(nodes.get('#regenerateButton').getAttribute('aria-pressed'), 'false');
  assert.equal(nodes.get('#doubaoImportButton').getAttribute('aria-pressed'), 'false');
});

test('the library offers one tag management entry and uses the category label', () => {
  const htmlSource = fs.readFileSync(path.join(__dirname, '../frontend/index.html'), 'utf8');
  assert.doesNotMatch(htmlSource, /id="manageTagsButton"/);
  assert.match(htmlSource, />标签类别</);
});
