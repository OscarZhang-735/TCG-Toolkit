import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';

const base = new URL('../src/tcg_toolkit/static/', import.meta.url);
const asModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const exportsURL = asModule(await readFile(new URL('cards-export.js', base), 'utf8'));
const source = (await readFile(new URL('cards-transfer-ui.js', base), 'utf8'))
  .replace("import {t, getLanguage} from './i18n.js';", "const t = key => key, getLanguage = () => 'en';")
  .replace("'./cards-export.js'", JSON.stringify(exportsURL));
const {createCardTransfers} = await import(asModule(source));

function view() {
  const elements = new Map(), preferences = new Map();
  function node() {
    const children = new Map();
    return {handlers: {}, children: [], files: [], open: false, value: '', classList: {toggle() {}, add() {}, remove() {}},
      append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; },
      addEventListener(name, handler) { this.handlers[name] = handler; },
      querySelector(name) { if (!children.has(name)) children.set(name, node()); return children.get(name); },
      closest: () => node(), focus() {}, reset() {}, showModal() { this.open = true; },
      close() { const wasOpen = this.open; this.open = false; if (wasOpen) this.handlers.close?.(); }};
  }
  const root = {createElement: node, querySelectorAll: () => [], getElementById(id) {
    if (!elements.has(id)) elements.set(id, node()); return elements.get(id);
  }};
  const storage = {getItem: key => preferences.get(key) ?? null, setItem: (key, value) => preferences.set(key, value)};
  const inventory = {state: {ready: true, busy: false, loading: false, group: 'one', groups: [{id: 'one', name: 'One'}],
    records: [{game: 'WS', group_id: 'one'}, {game: 'PM', group_id: 'one'}, {game: 'WS', group_id: 'two'}]}};
  return {root, storage, inventory, get: root.getElementById, preferences};
}

test('transfer export uses the selected group and game filter; reset aborts it', async () => {
  const ui = view(), calls = [];
  let finish;
  const transfers = createCardTransfers({...ui, hiddenGames: () => new Set(['pm']), request: (path, options) => {
    calls.push({path, options}); return new Promise(resolve => { finish = resolve; });
  }});
  transfers.render(); ui.get('cards-export').handlers.click();
  assert.equal(ui.get('cards-export-dialog').open, true);
  ui.get('cards-export-form').handlers.submit({preventDefault() {}});
  assert.equal(calls.length, 1);
  const query = new URL(calls[0].path, 'https://test.local').searchParams;
  assert.equal(query.get('group_id'), 'one'); assert.deepEqual(query.getAll('games'), ['WS']);
  assert.equal(calls[0].options.signal.aborted, false);
  transfers.reset();
  assert.equal(calls[0].options.signal.aborted, true);
  assert.equal(ui.get('cards-export-dialog').open, false);
  // Resolving the old account's response must not attempt a browser download.
  finish({ok: true, blob: async () => new Blob(['{}'])});
  await new Promise(resolve => setImmediate(resolve));
});

test('transfer import owns errors and sends the chosen file to the inventory', async () => {
  const ui = view(), calls = [];
  ui.inventory.importFile = async (...args) => { calls.push(args); return {imported: 1, skipped: 0}; };
  const transfers = createCardTransfers({...ui, hiddenGames: () => new Set(), request: () => assert.fail('unexpected export')});
  transfers.render(); ui.get('cards-import').handlers.click();
  const submit = () => ui.get('cards-import-form').handlers.submit({preventDefault() {}});
  ui.get('cards-import-file').files = [{name: 'wrong.txt'}]; transfers.render(); await submit();
  assert.equal(ui.get('cards-import-error').textContent, 'cards_import_format'); assert.equal(calls.length, 0);
  const file = {name: 'cards.XLSX'};
  ui.get('cards-import-file').files = [file]; ui.get('cards-import-file').handlers.change(); await submit();
  assert.deepEqual(calls, [[file, 'xlsx']]);
  assert.equal(ui.get('cards-import-error').textContent, '');
  assert.equal(ui.get('cards-import-status').textContent, 'cards_import_result');
  transfers.reset(); assert.equal(ui.get('cards-import-dialog').open, false);
});
