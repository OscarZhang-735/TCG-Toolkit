import {createCart} from './cart.js';
import {storefrontCartClient} from './storefront-cart.js';
import {bindStorefrontSettings} from './storefront-settings.js';
import {cardmaxCookie} from './cardmax-client.js';

export function cardmaxTarget(item) {
  const match = /^https:\/\/www\.cardmax\.jp\/shopdetail\/(\d+)\/?$/.exec(item.product_url);
  return match ? {gid: 'cm', ver: match[1], cid: '', kizu: 0, product_url: match[0]} : null;
}

export function cardmaxCartRows(cart) {
  return {...cart, items: cart.items.map(row => ({...row, gid: 'cm', ver: row.brandcode, cid: '', kizu: 0,
    type: row.brandcode}))};
}

export function cardmaxErrorKey(error) {
  if (error?.message === 'cardmax_credentials_missing') return 'cm_cart_need_credentials';
  if (error?.detail === 'invalid_cardmax_request') return 'cm_cart_invalid_input';
  return ({cardmax_credentials_rejected: 'cm_cart_credentials_rejected', cardmax_stock_exceeded: 'cart_no_stock',
    cardmax_add_form_unavailable: 'cm_cart_not_addable', cardmax_option_required: 'cm_cart_option_required',
    cardmax_cart_item_ambiguous: 'cm_cart_ambiguous', cardmax_cart_item_not_found: 'cart_out_of_date',
    cardmax_quantity_mismatch: 'cart_unverified', cardmax_timeout: 'cart_unverified',
    cart_operation_pending: 'cart_unverified'})[error?.detail?.type]
    ?? (error?.detail?.outcome === 'unknown' ? 'cart_unverified' : 'cart_failed');
}

export function createCardmaxCart(client, onView, newKey) {
  return createCart(storefrontCartClient(client, cardmaxCartRows), onView, newKey,
    {errorKey: cardmaxErrorKey, missingError: 'cardmax_credentials_missing', addReturnsCart: true});
}

export function bindCardmaxSettings(client, options) {
  return bindStorefrontSettings(client, {prefix: 'cm', fields: [
    ['tempid', 'tempid', cardmaxCookie], ['login-id', 'login_id', cardmaxCookie],
    ['member-name', 'member_name', cardmaxCookie], ['member-value', 'member_value', cardmaxCookie]],
    prepareCredentials: ({tempid, login_id, member_name, member_value}) => ({
      tempid, login_id, member_key: `${member_name}=${member_value}`
    }),
    keys: {verified: 'cm_cart_verified', verifying: 'cm_cart_verifying', rejected: 'cm_cart_credentials_rejected',
      invalid: 'cm_cart_invalid_input'}, errorKey: cardmaxErrorKey}, options);
}
