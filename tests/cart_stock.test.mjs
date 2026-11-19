import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
const source = (await readFile(new URL('../src/tcg_toolkit/static/cart/cart-stock.js', import.meta.url), 'utf8'))
  .replace("import {productKey} from '../catalog/product-identity.js';", 'const productKey = t => JSON.stringify([t.gid, t.ver, t.cid, t.kizu]);');
const {createCartStock} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const target = (cid, kizu = 0) => ({gid: 8, ver: 'yuzu5.0', cid, kizu});
const tick = () => new Promise(r => setTimeout(r, 0));
const settle = async () => {for (let i = 0; i < 20; i++) await tick();};
test('fetch is explicit, refreshes cached results, and distinguishes zero and unknown', async () => {
  const urls = []; let quantity = 25;
  const stock = createCartStock();
  stock.activate(async url => {urls.push(url); return new Response(JSON.stringify({stock_quantity: quantity}));});
  assert.equal(stock.get(target('1')).status, 'idle'); assert.equal(urls.length, 0);
  for (const value of [25, 0, null]) {
    quantity = value; stock.fetch(target('1')); await settle();
    assert.deepEqual(stock.get(target('1')), {status: 'done', quantity: value});
  }
  assert.equal(urls.length, 3); assert.match(urls[0], /kizu=0&stock=true/);
});
test('batch deduplicates products, respects conditions and bounds concurrency', async () => {
  let active = 0, peak = 0, count = 0;
  const stock = createCartStock();
  stock.activate(async () => {count++; active++; peak = Math.max(peak, active); await tick(); active--; return new Response('{"stock_quantity":1}');});
  stock.fetchAll([target('1'),target('1'),target('1',1), ...[2,3,4,5].map(n => target(String(n)))]);
  stock.fetch(target('1'));
  await settle(); assert.equal(count, 6); assert.equal(peak, 3);
});
test('failed rows retry independently and stale account replies are discarded', async () => {
  const stock = createCartStock(); let fail = true;
  stock.activate(async () => fail ? new Response('{}', {status: 502}) : new Response('{"stock_quantity":2}'));
  stock.fetch(target('1')); await settle(); assert.equal(stock.get(target('1')).status, 'failed');
  fail = false; stock.fetch(target('1')); await settle(); assert.equal(stock.get(target('1')).quantity, 2);
  let resolve;
  stock.activate(() => new Promise(r => {resolve = r;})); stock.fetch(target('1'));
  stock.activate(async () => new Response('{"stock_quantity":7}'));
  resolve(new Response('{"stock_quantity":99}')); await settle();
  assert.equal(stock.get(target('1')).status, 'idle');
  stock.fetch(target('1')); await settle(); assert.equal(stock.get(target('1')).quantity, 7);
});

import {readFile as read} from 'node:fs/promises';
test('cart page exposes bulk and per-card stock actions in both views', async () => {
  let view = 'list'; const elements = new Map(), calls = [];
  function node(tag = 'div') {
    return {tag, children: [], handlers: {}, style: {}, classList: {toggle() {}},
      append(...items) {this.children.push(...items);}, replaceChildren(...items) {this.children = items;},
      setAttribute() {}, addEventListener(name, fn) {this.handlers[name] = fn;},
      querySelector() {return node();}, close() {}, showModal() {}};
  }
  globalThis.document = {createElement: node, getElementById(id) {if (!elements.has(id)) elements.set(id, node()); return elements.get(id);}};
  const cartDir = new URL('../src/tcg_toolkit/static/cart/', import.meta.url);
  const moduleSource = async name => (await read(new URL(name, cartDir), 'utf8')).replace(/^import .*;$/gm, '');
  const moduleURL = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
  const helpers = "const t=key=>key, getLanguage=()=> 'en', productKey=t=>JSON.stringify([t.gid,t.ver,t.cid,t.kizu]);\n";
  const stepper = moduleURL(helpers + "const parseQuantity=Number, showFeedback=()=>{};\n" + await moduleSource('cart-stepper.js'));
  const source = helpers + `import {cartStepper} from '${stepper}';\n` +
    "const cardImage=()=>document.createElement('img'), rarityBadge=()=>document.createElement('span'), numberButton=()=>document.createElement('button');\n" +
    await moduleSource('cart-page.js');
  const {createCartPage} = await import(moduleURL(source));
  const row = {...target('1'), type:'normal', quantity:2, unit_price:30, game:'chaos', name:'Card', removable:true};
  const cart = {state:{rows:[row],configured:true,complete:true,totalQuantity:2,subtotal:60},
    range:()=>({min:0,max:99}), isBusy:()=>false, message:()=>null};
  const stock = {get:()=>({status:'done',quantity:25}), fetch:t=>calls.push(['one',t]), fetchAll:ts=>calls.push(['all',ts])};
  const page = createCartPage({cart,stock,details:{get:()=>({status:'done',card_number:'N'})},
    amountDisplay:()=>node(),gameLabel:x=>x,cardLink:()=>node(),currentView:()=>view});
  function flatten(n) {return [n,...(n.children??[]).filter(x=>typeof x==='object').flatMap(flatten)];}
  for (view of ['list','grid']) {
    page.render();
    const root=elements.get(view==='list'?'cart-rows':'cart-grid');
    // Icon-only per-card button: found by class, named by its tooltip (stock already fetched here).
    const button=flatten(root).find(n=>n.tag==='button' && String(n.className).includes('cart-stock-fetch'));
    assert.ok(button); assert.equal(button.title,'cart_refresh_stock'); button.handlers.click();
  }
  elements.get('cart-get-stock').handlers.click();
  assert.deepEqual(calls.map(c=>c[0]),['one','one','all']);
  const html=await read(new URL('../src/tcg_toolkit/static/index.html',import.meta.url),'utf8');
  assert.ok(html.indexOf('id="cart-get-stock"')<html.indexOf('id="cart-page-refresh"'));
});
