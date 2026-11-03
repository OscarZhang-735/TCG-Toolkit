import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
const source = await readFile(new URL('../src/tcg_toolkit/static/cards-export.js', import.meta.url), 'utf8');
const {fetchCardExport, downloadCardExport, exportQuery, EXPORT_FIELDS} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('exports use authenticated API wrapper and propagate cancellation', async () => {
  const signal = new AbortController().signal, blob = new Blob(['{}']);
  for (const format of ['xlsx', 'json']) {
    assert.equal(await fetchCardExport(async (path, options) => {
      assert.equal(path, `/api/cards/export?format=${format}`);
      assert.equal(options.cache, 'no-store');
      assert.equal(options.signal, signal);
      return {ok: true, blob: async () => blob};
    }, {format}, signal), blob);
  }
});

test('export settings become query parameters', () => {
  assert.equal(exportQuery({format: 'xlsx', language: 'zh-CN'}), 'format=xlsx&language=zh-CN');
  assert.equal(exportQuery({format: 'xlsx', language: 'keys'}), 'format=xlsx');
  // JSON keys never change, so language is not sent.
  assert.equal(exportQuery({format: 'json', language: 'en'}), 'format=json');
  // Fields go out in file order; all fields means no filter.
  assert.equal(exportQuery({format: 'json', fields: ['name', 'game']}), 'format=json&fields=game&fields=name');
  assert.equal(exportQuery({format: 'json', fields: [...EXPORT_FIELDS]}), 'format=json');
  assert.equal(exportQuery({format: 'json', games: ['WS EN', 'PM']}), 'format=json&games=WS+EN&games=PM');
});

test('failed downloads report errors instead of saving error responses', async () => {
  await assert.rejects(fetchCardExport(async () => ({ok: false, json: async () => ({detail: 'export_xlsx_text_too_long'})}), {format: 'xlsx'}), /cards_export_too_long/);
  await assert.rejects(fetchCardExport(async () => ({ok: false, json: async () => {throw new Error();}}), {format: 'json'}), /cards_export_failed/);
});

test('download creates a file and releases its object URL', () => {
  let clicked = false, removed = false, revoked, cleanup;
  const link = {click() {clicked = true;}, remove() {removed = true;}};
  const original = {document: globalThis.document, timeout: globalThis.setTimeout, create: URL.createObjectURL, revoke: URL.revokeObjectURL};
  try {
    globalThis.document = {createElement: () => link, body: {append: element => assert.equal(element, link)}};
    globalThis.setTimeout = fn => {cleanup = fn;};
    URL.createObjectURL = () => 'blob:cards';
    URL.revokeObjectURL = url => {revoked = url;};
    downloadCardExport(new Blob(['{}']), 'json');
    assert.match(link.download, /^cards-\d{4}-\d{2}-\d{2}\.json$/);
    assert.equal(link.href, 'blob:cards');
    assert.ok(clicked && removed);
    cleanup();
    assert.equal(revoked, 'blob:cards');
  } finally {
    globalThis.document = original.document; globalThis.setTimeout = original.timeout;
    URL.createObjectURL = original.create; URL.revokeObjectURL = original.revoke;
  }
});
