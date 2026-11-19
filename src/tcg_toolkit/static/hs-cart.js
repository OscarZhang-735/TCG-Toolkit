import {storefrontCartClient} from './storefront-cart.js';
// Hobby Station sales cart behind the same state machine as the YuYu-Tei cart (cart.js).
// A product is keyed like a YuYu-Tei one (gid 'hs', ver = product id) and each cart row's
// type is its product_class_id, so the shared stepper, ranges and messages work unchanged.
import {createCart} from './cart.js';

const HOST = 'www.hobbystation-single.jp';
const PRODUCT = /^\/([a-z0-9_-]+)\/product\/detail\/([0-9]+)\/?$/;

export function hobbystationTarget(item) {
  try {
    const url = new URL(item.product_url);
    const match = url.hostname === HOST && url.protocol === 'https:' && url.pathname.match(PRODUCT);
    if (match) return {gid: 'hs', ver: match[2], cid: '', kizu: 0, product_url: `https://${HOST}/${match[1]}/product/detail/${match[2]}`};
  } catch { /* not a product URL */ }
  return null;
}

export function hobbystationCartRows(cart) {
  return {...cart, items: cart.items.map(row => ({...row, gid: 'hs', ver: row.product_id, cid: '', kizu: 0,
    type: row.product_class_id}))};
}

export function hobbystationCartErrorKey(error) {
  if (error?.message === 'hobbystation_credentials_missing') return 'hs_cart_need_credentials';
  const detail = error?.detail, type = typeof detail === 'string' ? detail : detail?.type;
  if (type === 'hobbystation_stock_exceeded') {
    if (detail.reason === 'purchase_limit') return 'hs_cart_purchase_limit';
    if (detail.reason === 'stock') return 'hs_cart_stock_exceeded';
    return 'cart_limit_reached';
  }
  return ({hobbystation_credentials_rejected: 'hs_cart_credentials_rejected', hobbystation_product_access_check: 'hs_cart_access_check',
    hobbystation_cart_rejected: 'hs_cart_rejected', hobbystation_add_form_unavailable: 'hs_cart_not_addable',
    hobbystation_cart_item_ambiguous: 'hs_cart_ambiguous', hobbystation_cart_item_not_found: 'cart_out_of_date',
    hobbystation_quantity_mismatch: 'cart_unverified', hobbystation_timeout: 'cart_unverified',
    cart_operation_pending: 'cart_unverified'})[type]
    ?? (detail?.outcome === 'unknown' ? 'cart_unverified' : 'cart_failed');
}

export function hobbystationCartClient(client) {
  return storefrontCartClient(client, hobbystationCartRows);
}

export function createHobbystationCart(client, onView, newKey) {
  return createCart(hobbystationCartClient(client), onView, newKey, {errorKey: hobbystationCartErrorKey,
    missingError: 'hobbystation_credentials_missing', addReturnsCart: true});
}
