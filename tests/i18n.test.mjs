import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import test from 'node:test';
const base = new URL('../src/tcg_toolkit/static/', import.meta.url);
const resource = await readFile(new URL('locales.js', base), 'utf8');
const asModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const {messages} = await import(asModule(resource));
const values = new Map();
globalThis.localStorage = {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value)};
Object.defineProperty(globalThis, 'navigator', {value: {language: 'en-US'}, configurable: true});
const source = (await readFile(new URL('i18n.js', base), 'utf8')).replace("'./locales.js'", JSON.stringify(asModule(resource)));
const {t, setLanguage, getLanguage, resolveLanguage} = await import(asModule(source));

test('both languages cover HTML and dynamic translation keys', async () => {
  assert.deepEqual(Object.keys(messages.en).sort(), Object.keys(messages['zh-CN']).sort());
  const html = await readFile(new URL('index.html', base), 'utf8');
  const app = await readFile(new URL('app.js', base), 'utf8') + await readFile(new URL('auth.js', base), 'utf8') + await readFile(new URL('cards-ui.js', base), 'utf8') + await readFile(new URL('password-ui.js', base), 'utf8');
  for (const match of html.matchAll(/data-i18n(?:-[\w-]+)?="([^"]+)"/g)) assert.ok(messages.en[match[1]], match[1]);
  for (const match of app.matchAll(/\bt\('([^']+)'/g)) assert.ok(messages.en[match[1]], match[1]);
  for (const key of Object.keys(messages.en)) {
    assert.deepEqual(messages.en[key].match(/\{\w+\}/g), messages['zh-CN'][key].match(/\{\w+\}/g)?.sort((a,b) => messages.en[key].indexOf(a)-messages.en[key].indexOf(b)) ?? null);
  }
});
test('language selection, persistence, interpolation and English fallback', () => {
  assert.equal(resolveLanguage(null, 'zh-TW'), 'zh-CN');
  assert.equal(resolveLanguage('en', 'zh-CN'), 'en');
  assert.equal(resolveLanguage('bad', 'fr'), 'en');
  setLanguage('zh-CN'); assert.equal(getLanguage(), 'zh-CN'); assert.equal(values.get('language'), 'zh-CN');
  assert.equal(t('search'), '搜索');
  assert.equal(t('counts_hint', {matching: 2, loaded: 8}), '已加载 8 条，匹配 2 条');
  messages.en.fallback_probe = 'English fallback';
  assert.equal(t('fallback_probe'), 'English fallback');
  setLanguage('en'); assert.equal(t('search'), 'Search');
});
