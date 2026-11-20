import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
const identity = await readFile(new URL('../src/tcg_toolkit/static/catalog/product-identity.js', import.meta.url), 'utf8');
const identityUrl = 'data:text/javascript;base64,' + Buffer.from(identity).toString('base64');
const source = (await readFile(new URL('../src/tcg_toolkit/static/cart/cart-stock.js', import.meta.url), 'utf8'))
  .replace("'../catalog/product-identity.js'", JSON.stringify(identityUrl));
const {createCartStock} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const target = (cid, kizu = 0) => ({gid: 8, ver: 'yuzu5.0', cid, kizu});
const tick = () => new Promise(r => setTimeout(r, 0));
const settle = async () => {for (let i = 0; i < 20; i++) await tick();};
test('fetch is explicit, refreshes cached results, and distinguishes zero and unknown', async () => {
  const urls = []; let quantity = 25;
  const stock = createCartStock();
  stock.activate(async url => {
    urls.push(url);
    return new Response(JSON.stringify({stock_quantity: quantity,
      stock_status: quantity === null ? 'unknown' : quantity === 0 ? 'out_of_stock' : 'in_stock'}));
  });
  assert.equal(stock.get(target('1')).status, 'idle'); assert.equal(urls.length, 0);
  for (const [value, status] of [[25, 'in_stock'], [0, 'out_of_stock'], [null, 'unknown']]) {
    quantity = value;
    const result = await stock.fetch(target('1'));
    assert.deepEqual(result, {status: 'done', quantity: value, stockStatus: status});
    assert.deepEqual(stock.get(target('1')), result);
  }
  assert.equal(urls.length, 3); assert.match(urls[0], /kizu=0&stock=true/);
});
test('storefronts use the exact product URL, share duplicate displays, and preserve unnumbered stock', async () => {
  const hs = {gid: 'hs', ver: '123', product_url: 'https://www.hobbystation-single.jp/ws/product/detail/123'};
  const cm = {gid: 'cm', ver: '123', product_url: 'https://www.cardmax.jp/shopdetail/123'};
  const requests = [];
  const stock = createCartStock();
  stock.activate(async (path, options) => {
    const url = new URL(path, 'https://testserver');
    requests.push([url.pathname, Object.fromEntries(url.searchParams), options.cache]);
    return Response.json(url.pathname.includes('cardmax')
      ? {stock_status: 'in_stock', stock_quantity: 5, rarity: 'PR'}
      : {stock_status: 'in_stock', stock_quantity: null, stock_text: '在庫数：◯', game: 'ws', card_number: 'WS-W125-070S'});
  });
  stock.fetchAll([hs, {...hs, product_url: hs.product_url + '/'}, cm]);
  const result = await stock.fetch(hs);
  const hsEntry = {status: 'done', stockStatus: 'in_stock', quantity: null, stockText: '在庫数：◯',
    game: 'ws', card_number: 'WS-W125-070S'};
  assert.deepEqual(result, hsEntry);
  assert.deepEqual(stock.get(hs), hsEntry);
  assert.deepEqual(stock.get(cm), {status: 'done', stockStatus: 'in_stock', quantity: 5, rarity: 'PR'});
  assert.equal(requests.length, 2);
  for (const [path, params, cache] of requests) {
    const isHobby = path === '/api/hobbystation/product/stock';
    if (!isHobby) assert.equal(path, '/api/cardmax/product/stock');
    assert.deepEqual(Object.keys(params), ['product_url']);
    assert.equal(params.product_url.replace(/\/$/, ''), isHobby ? hs.product_url : cm.product_url);
    assert.equal(cache, 'no-store');
  }
  stock.fetch(hs);
  assert.deepEqual(stock.get(hs).last, hsEntry);
  await settle();
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
  const stock = createCartStock(undefined, 1); let fail = true;
  stock.activate(async () => fail ? new Response('{}', {status: 502}) : new Response('{"stock_quantity":2}'));
  assert.equal((await stock.fetch(target('1'))).status, 'failed');
  fail = false; assert.equal((await stock.fetch(target('1'))).quantity, 2);
  let resolve, started;
  const pending = new Promise(r => {started = r;});
  stock.activate(() => new Promise(r => {resolve = r; started();}));
  const active = stock.fetch(target('1')), queued = stock.fetch(target('2'));
  await pending;
  stock.activate(async () => new Response('{"stock_quantity":7}'));
  for (const result of await Promise.all([active, queued])) assert.equal(result.status, 'idle');
  const fresh = stock.fetch(target('1'));
  resolve(new Response('{"stock_quantity":99}'));
  assert.equal((await fresh).quantity, 7);
  assert.equal(stock.get(target('1')).quantity, 7);
  assert.equal(stock.get(target('2')).status, 'idle');
});

import {readFile as read} from 'node:fs/promises';
test('all cart routes expose the same stock actions in list and grid views', async () => {
  let view = 'list'; const elements = new Map(), calls = [];
  function node(tag = 'div') {
    return {tag, children: [], handlers: {}, style: {}, classList: {toggle() {}, add() {}},
      append(...items) {this.children.push(...items);}, replaceChildren(...items) {this.children = items;},
      setAttribute() {}, addEventListener(name, fn) {this.handlers[name] = fn;},
      querySelector() {return node();}, close() {}, showModal() {}};
  }
  globalThis.document = {createElement: node, getElementById(id) {if (!elements.has(id)) elements.set(id, node()); return elements.get(id);}};
  globalThis.location = {hash: '#cart', origin: 'https://toolkit.example'};
  const cartDir = new URL('../src/tcg_toolkit/static/cart/', import.meta.url);
  const moduleSource = async name => (await read(new URL(name, cartDir), 'utf8')).replace(/^import .*;$/gm, '');
  const moduleURL = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
  const helpers = "const t=(key,p={})=>key+Object.values(p).join(','), getLanguage=()=> 'en', productKey=t=>JSON.stringify([t.gid,t.ver,t.cid,t.kizu]);\n";
  const stepper = moduleURL(helpers + "const parseQuantity=Number, showFeedback=()=>{};\n" + await moduleSource('cart-stepper.js'));
  const shops = moduleURL(await moduleSource('shop-cart-page.js'));
  const badge = moduleURL(helpers + await moduleSource('../catalog/stock-badge.js'));
  const source = helpers + `import {cartStepper} from '${stepper}'; import {SHOP_CART_PAGES} from '${shops}'; import {stockBadge} from '${badge}';\n` +
    "const cardImage=()=>document.createElement('img'), rarityBadge=()=>document.createElement('span'), numberButton=()=>document.createElement('button');\n" +
    await moduleSource('cart-page.js');
  const {createCartPage} = await import(moduleURL(source));
  const row = {...target('1'), type:'normal', quantity:2, unit_price:30, game:'chaos', name:'Card', removable:true};
  const targets = {
    yuyutei: target('1'),
    hobbystation: {gid:'hs', ver:'123', cid:'', kizu:0, product_url:'https://www.hobbystation-single.jp/ws/product/detail/123'},
    cardmax: {gid:'cm', ver:'456', cid:'', kizu:0, product_url:'https://www.cardmax.jp/shopdetail/456'},
  };
  const carts = Object.fromEntries(Object.entries(targets).map(([source, target]) => [source, {
    state:{rows:[{...row, ...target}], configured:true, complete:true, totalQuantity:2, subtotal:60},
    range:()=>({min:0,max:99}), isBusy:()=>false, message:()=>null,
  }]));
  let result = {status:'done', quantity:25, stockStatus:'in_stock'};
  const stock = {get:()=>result, fetch:t=>calls.push(['one',t]), fetchAll:ts=>calls.push(['all',ts])};
  const page = createCartPage({carts,stock,details:{get:()=>({status:'done',card_number:'N'}),peek:()=>undefined},
    amountDisplay:()=>node(),gameLabel:x=>x,cardLink:()=>node(),currentView:()=>view});
  function flatten(n) {return [n,...(n.children??[]).filter(x=>typeof x==='object').flatMap(flatten)];}
  for (const [source, target] of Object.entries(targets)) {
    location.hash = source === 'yuyutei' ? '#cart' : '#cart-' + source;
    for (view of ['list','grid']) {
      const exact = {status:'done', quantity:25, stockStatus:'in_stock'};
      const cases = source === 'hobbystation' ? [
        [exact, 'in_stock', 'stock_count_exact25'],
        [{...exact, quantity:1}, 'in_stock', 'cart_stock_short_hint1,2'],
        [{...exact, quantity:null, stockText:'在庫数：△'}, 'in_stock', 'stock_count_unknown 在庫数：△'],
        [{...exact, quantity:0, stockStatus:'out_of_stock'}, 'out_of_stock', 'stock_count_exact0'],
      ] : [[exact, 'in_stock (25)', undefined]];
      for (const [value, label, hint] of cases) {
        result = value;
        page.render();
        assert.equal(page.current(), source);
        const nodes = flatten(elements.get(view==='list'?'cart-rows':'cart-grid'));
        const badge = nodes.find(n=>String(n.className).startsWith('badge '));
        assert.equal(badge.children[1], label);
        assert.equal(badge.title, hint);
        const button = nodes.find(n=>n.tag==='button' && String(n.className).includes('cart-stock-fetch'));
        assert.equal(button.title, 'cart_refresh_stock');
        button.handlers.click();
        elements.get('cart-get-stock').handlers.click();
        assert.deepEqual(calls.splice(0), [['one', target], ['all', [target]]]);
      }
    }
  }
  const html=await read(new URL('../src/tcg_toolkit/static/index.html',import.meta.url),'utf8');
  assert.doesNotMatch(html, /id="shop-cart-panel"/);
  assert.ok(html.indexOf('id="cart-get-stock"')<html.indexOf('id="cart-page-refresh"'));
});
