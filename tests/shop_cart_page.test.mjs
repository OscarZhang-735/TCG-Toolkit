import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const asModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const shops = await readFile(new URL('../src/tcg_toolkit/static/cart/shop-cart-page.js', import.meta.url), 'utf8');
const {SHOP_CART_PAGES} = await import(asModule(shops));
const view = (await readFile(new URL('../src/tcg_toolkit/static/catalog/card-view.js', import.meta.url), 'utf8'))
  .replace(/^import .*;$/gm, '');
async function imageResolver(apiBase) {
  const stubs = `const API_BASE_URL = ${JSON.stringify(apiBase)}, location = {origin:'https://toolkit.example'};
    const matchMedia=()=>({}), bindCopy=()=>{}, document={getElementById:()=>({addEventListener(){}})};`;
  return (await import(asModule(stubs + view))).cardImageUrl;
}

test('same-origin cart images have an absolute URL accepted by the image component', async () => {
  const imageUrl = await imageResolver('');
  assert.equal(imageUrl('https://www.hobbystation-single.jp/html/upload/save_image/ws/card.jpg'),
    'https://toolkit.example/api/images/hobbystation/ws/card.jpg');
});

test('Hobby Station cart images go through the image relay', async () => {
  const imageUrl = await imageResolver('https://api.example');
  assert.equal(imageUrl('https://www.hobbystation-single.jp/html/upload/save_image/lo/abc_1.jpg'),
    'https://api.example/api/images/hobbystation/lo/abc_1.jpg');
  assert.equal(imageUrl('https://other.example/x.jpg'), 'https://other.example/x.jpg');
  assert.equal(imageUrl(null), null);
});

test('each shop has its own cart route and credential message', () => {
  assert.deepEqual(Object.keys(SHOP_CART_PAGES), ['#cart', '#cart-hobbystation', '#cart-cardmax']);
  assert.equal(SHOP_CART_PAGES['#cart'].source, 'yuyutei');
  assert.equal(SHOP_CART_PAGES['#cart-hobbystation'].source, 'hobbystation');
  assert.equal(SHOP_CART_PAGES['#cart-cardmax'].needCredentials, 'cm_cart_need_credentials');
  assert.match(SHOP_CART_PAGES['#cart-cardmax'].cartUrl, /^https:\/\/www\.cardmax\.jp\//);
});
