import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = (await readFile(new URL('../src/tcg_toolkit/static/shared/copy-number.js', import.meta.url), 'utf8'))
  .replace("import {t} from '../i18n.js';", 'const t = (key, p = {}) => key + (p.number ? ":" + p.number : "");');
const {copyText, bindCopy} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

function fakeDoc(execResult = true) {
  const log = [];
  return {log, body: {append(node) { log.push(['append', node.value]); }},
    createElement() { return {style: {}, setAttribute() {}, select() { log.push(['select']); }, remove() { log.push(['remove']); }}; },
    execCommand(name) { log.push(['exec', name]); return execResult; }};
}

test('secure contexts use the Clipboard API', async () => {
  const written = [];
  await copyText('OS01/R01-053SEC', {secure: true, nav: {clipboard: {writeText: async v => written.push(v)}}, doc: fakeDoc()});
  assert.deepEqual(written, ['OS01/R01-053SEC']);
});

test('plain HTTP falls back to a temporary textarea and always cleans it up', async () => {
  const doc = fakeDoc();
  await copyText('LO-1315-X', {secure: false, nav: {clipboard: {writeText: () => assert.fail('Not allowed on HTTP')}}, doc});
  assert.deepEqual(doc.log, [['append', 'LO-1315-X'], ['select'], ['exec', 'copy'], ['remove']]);
  const failing = fakeDoc(false);
  await assert.rejects(copyText('x', {secure: false, nav: {}, doc: failing}), /copy_failed/);
  assert.deepEqual(failing.log.at(-1), ['remove']);
});

test('click copies once, does not bubble, and shows the result', async () => {
  globalThis.document = {getElementById: () => ({textContent: ''})};
  const handlers = {}, classes = new Set();
  const button = {dataset: {}, addEventListener: (n, f) => { handlers[n] = f; },
    classList: {toggle(c, on) { on ? classes.add(c) : classes.delete(c); }, remove(c) { classes.delete(c); }}};
  const copied = [];
  bindCopy(button, () => 'YZ-001', async v => copied.push(v));
  let stopped = false;
  await handlers.click({stopPropagation() { stopped = true; }});
  assert.deepEqual(copied, ['YZ-001']);
  assert.equal(stopped, true);
  assert.equal(button.dataset.copied, 'number_copied');
  bindCopy(button, () => 'YZ-002', async () => { throw new Error('denied'); });
  await handlers.click({stopPropagation() {}});
  assert.equal(button.dataset.copied, 'number_copy_failed');
  assert.equal(classes.has('copy-failed'), true);
});
