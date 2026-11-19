// Cart page shared by Hobby Station and Card Max (#cart-hobbystation, #cart-cardmax).
// Everything shown is what the shop last reported; the YuYu-Tei page (cart-page.js) is separate.
import {t, getLanguage} from './i18n.js';
import {cardImage} from './card-view.js';
import {cartStepper} from './cart-page.js';
import {productKey} from './product-identity.js';
import {API_BASE_URL} from './api-config.js';
const $ = id => document.getElementById(id);
const el = (tag, className = '', text = '') => {const node = document.createElement(tag); node.className = className; node.textContent = text; return node;};
const icon = name => el('i', `bi ${name}`);
const number = value => value.toLocaleString(getLanguage());
const targetOf = row => ({gid: row.gid, ver: row.ver, cid: row.cid, kizu: row.kizu});

// Hobby Station blocks hotlinked images; show them through this site's image relay.
const HOBBY_IMAGE = /^https:\/\/www\.hobbystation-single\.jp\/html\/upload\/save_image\/(lo|ws|wr)\/([^/?#]+)$/;
export function hobbystationImageUrl(url) {
  const match = HOBBY_IMAGE.exec(url ?? '');
  return match ? new URL(`${API_BASE_URL}/api/images/hobbystation/${match[1]}/${match[2]}`, location.origin).href : url ?? null;
}

// Per shop: route, label, icon, the shop's own cart page and how its row images are shown.
export const SHOP_CART_PAGES = {
  '#cart-hobbystation': {source: 'hobbystation', label: 'source_hobbystation', icon: './source-hobbystation.png',
    cartUrl: 'https://www.hobbystation-single.jp/cart', needCredentials: 'hs_cart_need_credentials', image: hobbystationImageUrl},
  '#cart-cardmax': {source: 'cardmax', label: 'source_cardmax', icon: './source-cardmax.png',
    cartUrl: 'https://www.cardmax.jp/shop/basket.html', needCredentials: 'cm_cart_need_credentials', image: url => url ?? null},
};

// carts: source → cart state (cart.js); the page follows location.hash.
export function createShopCartPage({carts, amountDisplay, cardLink, currentView}) {
  let pendingRemove = null;
  const page = () => SHOP_CART_PAGES[location.hash] ?? null;
  const shopName = shop => t(shop.label);
  const item = (shop, row) => ({...row, source: shop.source, price: row.unit_price, image_url: shop.image(row.image_url)});
  function stepper(cart, row) {
    const target = targetOf(row), limits = cart.range(target, {type: row.type});
    return cartStepper(cart, target, {quantity: row.quantity, canDecrease: row.removable, draftKey: productKey(target) + row.type,
      canEdit: limits.min < limits.max, onSet: value => cart.setTo(target, value, {type: row.type, item: row}),
      onDecrease: () => cart.decrease(target, row.type), onIncrease: () => cart.increase(target, row)});
  }
  function removeButton(shop, cart, row) {
    const button = el('button', 'icon-button row-action'); button.type = 'button';
    button.append(icon('bi-trash3'));
    button.setAttribute('aria-label', t('cart_remove')); button.title = t('cart_remove');
    button.disabled = row.removable === false || cart.isBusy(targetOf(row));
    button.addEventListener('click', () => {
      pendingRemove = {cart, row};
      $('shop-cart-remove-message').textContent = t('shop_cart_confirm_remove',
        {name: row.name ?? '', quantity: number(row.quantity), shop: shopName(shop)});
      $('shop-cart-remove-dialog').showModal();
    });
    return button;
  }
  function tableRow(shop, cart, row) {
    const tr = document.createElement('tr');
    const cell = (child, className = '') => {const td = el('td', className); td.append(child); tr.append(td);};
    const shown = item(shop, row), product = el('div', 'product-summary'); product.append(cardImage(shown), cardLink(shown));
    cell(product); cell(amountDisplay(row.unit_price), 'numeric'); cell(stepper(cart, row), 'cart-col');
    cell(amountDisplay(row.unit_price * row.quantity), 'numeric cart-line-total'); cell(removeButton(shop, cart, row), 'action-col');
    return tr;
  }
  function gridCard(shop, cart, row) {
    const card = el('article', 'card'), shown = item(shop, row);
    const bottom = el('div', 'card-bottom');
    const total = el('span', 'cart-line-total'); total.append(el('span', 'cart-line-label', t('cart_line_subtotal')), amountDisplay(row.unit_price * row.quantity));
    bottom.append(amountDisplay(row.unit_price), total);
    const actions = el('div', 'card-cart'); actions.append(stepper(cart, row), removeButton(shop, cart, row));
    card.append(cardImage(shown), cardLink(shown, 'card-name'), bottom, actions);
    return card;
  }
  function emptyState(shop, state) {
    const link = (key, href) => { const a = el('a', 'cart-empty-link', t(key)); a.href = href; return a; };
    if (!state.configured) return [t(shop.needCredentials), link('cart_go_setup', '#account')];
    if (state.loading) return [t('cart_loading')];
    if (state.error) return [t(state.error)];
    if (!state.complete) return [t('cart_summary_incomplete')];
    return [t('shop_cart_empty', {shop: shopName(shop)}), link('cart_go_search', '#search')];
  }
  function render() {
    const shop = page();
    if (!shop) { $('shop-cart-remove-dialog').close(); return; }
    const cart = carts[shop.source], state = cart.state, rows = state.rows, view = currentView(), has = rows.length > 0;
    const title = t('shop_cart_title', {shop: shopName(shop)});
    $('shop-cart-title').textContent = title; $('shop-cart-items-title').textContent = title;
    $('shop-cart-stats').hidden = !state.configured || (!has && !state.complete);
    // Checkout happens on the shop's own site (after the usual leaving-the-site confirmation).
    const checkout = cardLink({name: title, product_url: shop.cartUrl}, 'cart-checkout');
    const logo = document.createElement('img');
    logo.src = shop.icon; logo.alt = ''; logo.className = 'cart-checkout-logo'; logo.setAttribute('aria-hidden', 'true');
    checkout.replaceChildren(logo, el('span', '', t('shop_cart_checkout', {shop: shopName(shop)})), icon('bi-box-arrow-up-right'));
    checkout.title = t('shop_cart_checkout_hint', {shop: shopName(shop)});
    $('shop-cart-checkout-slot').replaceChildren(checkout);
    $('shop-cart-kinds').textContent = number(rows.length);
    $('shop-cart-quantity-total').textContent = state.totalQuantity === null ? '—' : number(state.totalQuantity);
    $('shop-cart-subtotal').replaceChildren(state.subtotal === null ? '—' : amountDisplay(state.subtotal));
    $('shop-cart-count').hidden = !has; $('shop-cart-count').textContent = number(rows.length);
    $('shop-cart-status').textContent = state.loading && has ? t('cart_loading') : state.error && has ? t(state.error) : '';
    $('shop-cart-warning').hidden = state.complete;
    $('shop-cart-warning-text').textContent = t('shop_cart_incomplete_warning', {shop: shopName(shop)});
    $('shop-cart-refresh').disabled = !state.configured || state.loading;
    $('shop-cart-refresh').querySelector('.bi').classList.toggle('spin', state.loading);
    $('shop-cart-rows').replaceChildren(...(view === 'list' ? rows.map(row => tableRow(shop, cart, row)) : []));
    $('shop-cart-grid').replaceChildren(...(view === 'grid' ? rows.map(row => gridCard(shop, cart, row)) : []));
    $('shop-cart-table').hidden = view !== 'list' || !has;
    $('shop-cart-grid').hidden = view !== 'grid' || !has;
    $('shop-cart-empty').hidden = has;
    if (!has) {
      const [message, action] = emptyState(shop, state);
      $('shop-cart-empty').querySelector('h3').textContent = message;
      $('shop-cart-empty-action').replaceChildren(...(action ? [action] : []));
    }
    if (pendingRemove && (pendingRemove.cart !== cart || !rows.some(row => productKey(row) === productKey(pendingRemove.row)
        && row.type === pendingRemove.row.type))) $('shop-cart-remove-dialog').close();
  }
  $('shop-cart-refresh').addEventListener('click', () => { const shop = page(); if (shop) carts[shop.source].refresh(); });
  $('shop-cart-remove-cancel').addEventListener('click', () => $('shop-cart-remove-dialog').close());
  $('shop-cart-remove-confirm').addEventListener('click', () => {
    const pending = pendingRemove; $('shop-cart-remove-dialog').close();
    if (pending) pending.cart.remove(targetOf(pending.row), pending.row.type);
  });
  $('shop-cart-remove-dialog').addEventListener('close', () => { pendingRemove = null; });
  return {render, current: () => page()?.source ?? null, close: () => $('shop-cart-remove-dialog').close()};
}
