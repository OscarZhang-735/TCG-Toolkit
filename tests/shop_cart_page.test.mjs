import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const asModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const source = (await readFile(new URL('../src/tcg_toolkit/static/cart/shop-cart-page.js', import.meta.url), 'utf8'))
  .replace("import {t, getLanguage} from '../i18n.js';", "const t = k => k, getLanguage = () => 'en';")
  .replace("import {cardImage} from '../catalog/card-view.js';", 'const cardImage = () => null;')
  .replace("import {cartStepper} from './cart-stepper.js';", 'const cartStepper = () => null;')
  .replace("import {productKey} from '../catalog/product-identity.js';", 'const productKey = JSON.stringify;')
  .replace("import {API_BASE_URL} from '../api-config.js';", "const API_BASE_URL = 'https://api.example';");
const locationStub = "const location = {origin: 'https://toolkit.example'};";
const {hobbystationImageUrl, SHOP_CART_PAGES} = await import(asModule(locationStub + source));

test('same-origin cart images have an absolute URL accepted by the image component', async () => {
  const sameOrigin = await import(asModule(locationStub + source.replace("'https://api.example'", "''")));
  assert.equal(sameOrigin.hobbystationImageUrl('https://www.hobbystation-single.jp/html/upload/save_image/ws/card.jpg'),
    'https://toolkit.example/api/images/hobbystation/ws/card.jpg');
});

test('Hobby Station cart images go through the image relay', () => {
  assert.equal(hobbystationImageUrl('https://www.hobbystation-single.jp/html/upload/save_image/lo/abc_1.jpg'),
    'https://api.example/api/images/hobbystation/lo/abc_1.jpg');
  assert.equal(hobbystationImageUrl('https://other.example/x.jpg'), 'https://other.example/x.jpg');
  assert.equal(hobbystationImageUrl(null), null);
});

test('each new shop has its own cart route and credential message', () => {
  assert.deepEqual(Object.keys(SHOP_CART_PAGES), ['#cart-hobbystation', '#cart-cardmax']);
  assert.equal(SHOP_CART_PAGES['#cart-hobbystation'].source, 'hobbystation');
  assert.equal(SHOP_CART_PAGES['#cart-cardmax'].needCredentials, 'cm_cart_need_credentials');
  assert.match(SHOP_CART_PAGES['#cart-cardmax'].cartUrl, /^https:\/\/www\.cardmax\.jp\//);
});
