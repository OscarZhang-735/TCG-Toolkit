import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../src/tcg_toolkit/static/shared/bubble.js', import.meta.url), 'utf8');
const {showBubble} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
function fixture(t) {
  const timers = new Map(); let id = 0;
  const original = {setTimeout, clearTimeout, now: Date.now};
  globalThis.setTimeout = (fn, delay) => { timers.set(++id, {fn, delay}); return id; };
  globalThis.clearTimeout = key => timers.delete(key);
  Date.now = () => 10000;
  t.after(() => {Object.assign(globalThis, {setTimeout: original.setTimeout, clearTimeout: original.clearTimeout}); Date.now = original.now;});
  const classes = new Set();
  const nodes = [];
  const anchor = {classList: {contains: k => classes.has(k), add: k => classes.add(k), remove: k => classes.delete(k)},
    ownerDocument: {createElement: () => ({style: {setProperty(k,v) {this[k] = v;}}, setAttribute(k,v) {this[k] = v;}, remove() {this.removed = true;}})},
    append: node => nodes.push(node)};
  return {anchor, nodes, timers, classes};
}
test('bubble uses safe text, default lifetime and cleans up', t => {
  const {anchor, nodes, timers, classes} = fixture(t);
  showBubble(anchor, '<img src=x>');
  assert.equal(nodes[0].textContent, '<img src=x>');
  assert.equal(nodes[0].role, 'alert');
  assert.equal(nodes[0].style.animationDelay, '2000ms');
  assert.equal([...timers.values()][0].delay, 2300);
  [...timers.values()][0].fn();
  assert.equal(nodes[0].removed, true); assert.equal(classes.has('bubble-anchor'), false);
});
test('rerender retains elapsed time; expired messages do not appear', t => {
  const {anchor, nodes, timers} = fixture(t);
  showBubble(anchor, 'warning', {started: 7900});
  assert.equal(nodes[0].style.animationDelay, '-100ms');
  assert.equal([...timers.values()][0].delay, 200);
  showBubble(anchor, 'expired', {started: 7000});
  assert.equal(nodes.length, 1); assert.equal(timers.size, 0);
});
test('replacement, custom duration, success tone and manual dismissal', t => {
  const {anchor, nodes, timers} = fixture(t);
  showBubble(anchor, 'first');
  const close = showBubble(anchor, 'saved', {tone: 'success', duration: 500, fade: 100});
  assert.equal(nodes[0].removed, true); assert.equal(timers.size, 1);
  assert.equal(nodes[1].role, 'status'); assert.equal(nodes[1].style['--bubble-fade'], '100ms');
  assert.equal([...timers.values()][0].delay, 600);
  close(); close(); assert.equal(timers.size, 0);
});
