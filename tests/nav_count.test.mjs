import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = (await readFile(new URL('../src/tcg_toolkit/static/shared/nav-count.js', import.meta.url), 'utf8'))
  .replace("import {getLanguage} from '../i18n.js';", "const getLanguage = () => 'en';");
const {navCountText, setNavCount} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('counts up to 9999 are shown in full, larger ones as 9999+', () => {
  assert.equal(navCountText(1), '1');
  assert.equal(navCountText(1200), '1,200');
  assert.equal(navCountText(9999), '9,999');
  assert.equal(navCountText(10000), '9999+');
  assert.equal(navCountText(250000), '9999+');
});

test('the bubble hides for zero or unknown counts and keeps the exact value as a tooltip', () => {
  const el = {};
  for (const value of [0, null, undefined, NaN]) {
    setNavCount(el, value);
    assert.equal(el.hidden, true); assert.equal(el.textContent, '');
  }
  setNavCount(el, 42);
  assert.deepEqual([el.hidden, el.textContent, el.title], [false, '42', '']);
  setNavCount(el, 1234);
  assert.deepEqual([el.hidden, el.textContent, el.title], [false, '1,234', '']);
  setNavCount(el, 12345);
  assert.deepEqual([el.hidden, el.textContent, el.title], [false, '9999+', '12,345']);
});
