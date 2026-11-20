import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../src/tcg_toolkit/static/shared/nav-menu.js', import.meta.url), 'utf8');
const {syncNavMenus, closeNavMenus, bindNavMenus} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

function fakeNav() {
  const handlers = {}, win = {};
  const node = extra => ({handlers: {}, attrs: {}, addEventListener(n, f) { this.handlers[n] = f; }, setAttribute(k, v) { this.attrs[k] = v; }, ...extra});
  function group(name, pages) {
    const classes = new Set();
    const links = pages.map(p => ({page: p, current: false, querySelector: () => ({textContent: p})}));
    const g = {name, links, classes, classList: {toggle(c, on) { on ? classes.add(c) : classes.delete(c); }, contains: c => classes.has(c)},
      toggle: node({focused: false, focus() { this.focused = true; }}), current: {textContent: ''}, items: node()};
    g.querySelector = sel => sel === '.nav-group-toggle' ? g.toggle : sel === '.nav-group-current' ? g.current
      : sel === '.nav-group-items' ? g.items : sel === '.nav-group-items a[aria-current]' ? links.find(l => l.current) ?? null : null;
    return g;
  }
  const shop = group('shop', ['搜索', '收藏夹', '购物车']), local = group('local', ['卡牌库']);
  const root = {addEventListener(n, f) { handlers[n] = f; }, querySelectorAll: selector => selector.includes('.nav-submenu') ? [] : [shop, local],
    querySelector: () => [shop, local].find(g => g.classes.has('open')) ?? null};
  win.addEventListener = (n, f) => { handlers['win:' + n] = f; };
  return {root, win, shop, local, handlers};
}

test('toggle names the current page of its group', () => {
  const {root, shop, local} = fakeNav();
  shop.links[2].current = true;
  syncNavMenus(root);
  assert.equal(shop.current.textContent, '购物车'); assert.equal(shop.classes.has('has-current'), true);
  assert.equal(local.current.textContent, ''); assert.equal(local.classes.has('has-current'), false);
});

test('one menu open at a time; links, outside taps, Escape and navigation close it', () => {
  const {root, win, shop, local, handlers} = fakeNav();
  bindNavMenus(root, win);
  shop.toggle.handlers.click();
  assert.equal(shop.classes.has('open'), true); assert.equal(shop.toggle.attrs['aria-expanded'], 'true');
  local.toggle.handlers.click();
  assert.deepEqual([shop.classes.has('open'), local.classes.has('open')], [false, true]);
  local.toggle.handlers.click();
  assert.equal(local.classes.has('open'), false, 'Second tap closes');
  shop.toggle.handlers.click();
  shop.items.handlers.click({target: {closest: s => s === 'a' ? {} : null}});
  assert.equal(shop.classes.has('open'), false, 'Choosing a page closes');
  shop.toggle.handlers.click();
  handlers.pointerdown({target: {closest: () => null}});
  assert.equal(shop.classes.has('open'), false, 'Outside tap closes');
  shop.toggle.handlers.click();
  handlers.pointerdown({target: {closest: () => ({})}});
  assert.equal(shop.classes.has('open'), true, 'Tap inside keeps it open');
  handlers.keydown({key: 'Escape'});
  assert.equal(shop.classes.has('open'), false); assert.equal(shop.toggle.focused, true, 'Focus returns to the toggle');
  shop.toggle.handlers.click(); handlers['win:hashchange']();
  assert.equal(shop.classes.has('open'), false);
  closeNavMenus(root);
});
