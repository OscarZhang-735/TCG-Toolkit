// The three merchant routes share this page; each cart owns its own rows and writes.
import {t, getLanguage} from '../i18n.js';
import {cardImage, rarityBadge} from '../catalog/card-view.js';
import {stockBadge} from '../catalog/stock-badge.js';
import {productKey} from '../catalog/product-identity.js';
import {cartStepper} from './cart-stepper.js';
import {numberButton} from '../shared/copy-number.js';
import {SHOP_CART_PAGES} from './shop-cart-page.js';
const $ = id => document.getElementById(id);
const el = (tag, className = '', text = '') => {const node = document.createElement(tag); node.className = className; node.textContent = text; return node;};
const icon = name => el('i', `bi ${name}`);
const number = value => value.toLocaleString(getLanguage());
// Cart rows name games by their shop path; the rest of the UI uses search game keys.
const GAME_KEYS = {lo: 'lycee', wr: 'wsr'}, KNOWN_GAMES = ['wsr', 'lycee', 'chaos', 'ws', 'pm', 'sev'];
function targetOf({gid, ver, cid, kizu, product_url}) {
  return gid === 'hs' || gid === 'cm' ? {gid, ver, cid, kizu, product_url} : {gid, ver, cid, kizu};
}
export function createCartPage({carts, stock, details, amountDisplay, gameLabel, cardLink, currentView}) {
  let shop, cart, pendingRemove = null;
  const current = () => SHOP_CART_PAGES[location.hash]?.source ?? null;
  function info(row) {
    const target = targetOf(row);
    if (shop.source === 'yuyutei') {
      return row.card_number ? row : details.get(target);
    }
    const entry = stock.get(target), fetched = entry.status === 'done' ? entry : entry.last;
    const pathGame = row.gid === 'hs' && row.product_url?.match(/\.jp\/([^/]+)\/product\//)?.[1];
    const known = fetched?.card_number || !row.product_url || (row.card_number && row.rarity && (row.game || pathGame))
      ? details.peek(target) ?? {} : details.get(target);
    return {status: known.status, card_number: fetched?.card_number ?? known.card_number ?? row.card_number,
      rarity: fetched?.rarity ?? known.rarity ?? row.rarity, game: fetched?.game ?? known.game ?? (row.game || pathGame)};
  }
  function item(row) {
    const known = info(row), rarity = known.rarity ?? null;
    // Cart names read "<rarity> <name>"; the rarity is shown as its own badge.
    const full = row.name ?? '', name = shop.source === 'yuyutei' && rarity && full.startsWith(rarity + ' ')
      ? full.slice(rarity.length + 1) : full;
    const game = known.game || row.game;
    return {...row, source: shop.source, name, rarity, card_number: known.card_number ?? '', game: GAME_KEYS[game] ?? game, price: row.unit_price};
  }
  function tags(row, {withRarity = false} = {}) {
    const box = el('div', 'card-tags'), shown = item(row);
    if (shown.game) box.append(el('span', 'game-tag', KNOWN_GAMES.includes(shown.game) ? gameLabel(shown.game) : shown.game));
    if (withRarity) box.append(rarityBadge(shown));
    if (row.type === 'reserved') box.append(el('span', 'cart-tag', t('cart_reserved')));
    if (row.kizu) box.append(el('span', 'cart-tag', t('cart_damaged')));
    return box;
  }
  function cardNumber(row) {
    const known = info(row);
    if (known.card_number) return numberButton(known.card_number);
    const pending = el('span', 'mono muted');
    if (known.status === 'loading') { pending.append(icon('bi-arrow-repeat spin')); pending.title = t('cart_details_loading'); }
    else { pending.textContent = '—'; if (known.status === 'failed') pending.title = t('cart_details_failed'); }
    return pending;
  }
  function stepper(row) {
    const target = targetOf(row);
    // Lowering re-reads the row's removal control, so rows without one stay read-only.
    const limits = cart.range(target, {type: row.type});
    return cartStepper(cart, target, {quantity: row.quantity, canDecrease: row.removable, draftKey: productKey(target) + row.type,
      canEdit: limits.min < limits.max, onSet: value => cart.setTo(target, value, {type: row.type, item: row}),
      onDecrease: () => cart.decrease(target, row.type), onIncrease: () => cart.increase(target, row)});
  }
  function removeButton(row) {
    const button = el('button', 'icon-button row-action'); button.type = 'button';
    button.append(icon('bi-trash3'));
    button.setAttribute('aria-label', t('cart_remove')); button.title = t(row.removable ? 'cart_remove' : 'cart_not_removable');
    button.disabled = !row.removable || cart.isBusy(targetOf(row));
    button.addEventListener('click', () => {
      pendingRemove = {row, cart};
      $('cart-remove-message').textContent = t('shop_cart_confirm_remove',
        {shop: t(shop.label), name: item(row).name, quantity: number(row.quantity)});
      $('cart-remove-dialog').showModal();
    });
    return button;
  }
  // Same badge language as search results, plus a warning when the shop holds fewer than the cart.
  function cartStockBadge(row, state) {
    const result = state.status === 'done' ? state : state.last;
    if (!result) {
      const badge = el('span', `badge unknown${state.status === 'failed' ? ' stock-failed' : ''}`);
      if (state.status === 'failed') badge.append(icon('bi-exclamation-circle'), t('cart_stock_failed_short'));
      else { badge.textContent = '—'; badge.title = t('cart_stock_idle'); }
      return badge;
    }
    return stockBadge({source: shop.source, stock_status: result.stockStatus, stock_quantity: result.quantity,
      stock_text: result.stockText}, row.quantity);
  }
  function stockControl(row) {
    const target = targetOf(row), state = stock.get(target), loading = state.status === 'loading';
    const box = el('div', 'cart-stock-control');
    const badge = cartStockBadge(row, state); badge.setAttribute('role', 'status');
    const key = loading ? 'cart_stock_loading' : state.status === 'failed' ? 'cart_retry_stock'
      : state.status === 'idle' ? 'cart_get_stock' : 'cart_refresh_stock';
    const button = el('button', 'icon-button cart-stock-fetch'); button.type = 'button';
    button.append(icon(loading ? 'bi-arrow-repeat spin' : state.status === 'idle' ? 'bi-box-seam' : 'bi-arrow-clockwise'));
    button.setAttribute('aria-label', t(key)); button.title = t(key);
    button.disabled = loading;
    button.addEventListener('click', () => stock.fetch(target));
    box.append(badge, button); return box;
  }
  function tableRow(row) {
    const tr = document.createElement('tr');
    const cell = (child, className = '') => {const td = el('td', className); td.append(child); tr.append(td);};
    const shown = item(row), product = el('div', 'product-summary'); product.append(cardImage(shown), cardLink(shown));
    cell(tags(row)); cell(rarityBadge(shown)); cell(cardNumber(row)); cell(product);
    cell(amountDisplay(row.unit_price), 'numeric'); cell(stockControl(row)); cell(stepper(row), 'cart-col');
    cell(amountDisplay(row.unit_price * row.quantity), 'numeric cart-line-total'); cell(removeButton(row), 'action-col');
    return tr;
  }
  function gridCard(row) {
    const card = el('article', 'card');
    const top = el('div', 'card-top'); top.append(tags(row, {withRarity: true}), cardNumber(row));
    const bottom = el('div', 'card-bottom');
    const total = el('span', 'cart-line-total'); total.append(el('span', 'cart-line-label', t('cart_line_subtotal')), amountDisplay(row.unit_price * row.quantity));
    bottom.append(amountDisplay(row.unit_price), total);
    const actions = el('div', 'card-cart'); actions.append(stepper(row), removeButton(row));
    const shown = item(row);
    card.append(top, cardImage(shown), cardLink(shown, 'card-name'), bottom, stockControl(row), actions);
    return card;
  }
  function emptyState(state) {
    const link = (key, href) => { const a = el('a', 'cart-empty-link', t(key)); a.href = href; return a; };
    if (!state.configured) return [t(shop.needCredentials), link('cart_go_setup', '#account')];
    if (state.loading) return [t('cart_loading')];
    if (state.error) return [t(state.error)];
    if (!state.complete) return [t('cart_summary_incomplete')];
    return [t('shop_cart_empty', {shop: t(shop.label)}), link('cart_go_search', '#search')];
  }
  function render() {
    shop = SHOP_CART_PAGES[location.hash];
    if (!shop) { close(); return; }
    cart = carts[shop.source];
    const state = cart.state, rows = state.rows, view = currentView();
    const has = rows.length > 0;
    const shopLabel = {shop: t(shop.label)};
    $('cart-page-title').textContent = t('shop_cart_title', shopLabel);
    $('cart-stats').hidden = !state.configured || (!has && (state.totalQuantity == null || !state.complete));
    const checkout = cardLink({name: t('shop_cart_title', shopLabel), product_url: shop.cartUrl}, 'cart-checkout');
    const logo = document.createElement('img');
    logo.src = shop.icon; logo.alt = ''; logo.className = 'cart-checkout-logo'; logo.setAttribute('aria-hidden', 'true');
    checkout.replaceChildren(logo, el('span', '', t('shop_cart_checkout', shopLabel)), icon('bi-box-arrow-up-right'));
    checkout.title = t('shop_cart_checkout_hint', shopLabel);
    $('cart-checkout-slot').replaceChildren(checkout);
    $('cart-kinds').textContent = number(rows.length);
    $('cart-quantity-total').textContent = state.totalQuantity === null ? '—' : number(state.totalQuantity);
    $('cart-subtotal').replaceChildren(state.subtotal === null ? '—' : amountDisplay(state.subtotal));
    $('cart-count').hidden = !has; $('cart-count').textContent = number(rows.length);
    $('cart-status').textContent = state.loading && has ? t('cart_loading') : state.error && has ? t(state.error) : '';
    $('cart-warning').hidden = state.complete;
    $('cart-warning-text').textContent = t('shop_cart_incomplete_warning', shopLabel);
    $('cart-get-stock').disabled = !has || !state.configured || rows.some(row => stock.get(targetOf(row)).status === 'loading');
    $('cart-page-refresh').disabled = !state.configured || state.loading;
    $('cart-page-refresh').querySelector('.bi').classList.toggle('spin', state.loading);
    $('cart-rows').replaceChildren(...(view === 'list' ? rows.map(tableRow) : []));
    $('cart-grid').replaceChildren(...(view === 'grid' ? rows.map(gridCard) : []));
    $('cart-table').hidden = view !== 'list' || !has;
    $('cart-grid').hidden = view !== 'grid' || !has;
    $('cart-empty').hidden = has;
    if (!has) {
      const [message, action] = emptyState(state);
      $('cart-empty').querySelector('h3').textContent = message;
      $('cart-empty-action').replaceChildren(...(action ? [action] : []));
    }
    if (pendingRemove && (pendingRemove.cart !== cart || !rows.some(row => productKey(row) === productKey(pendingRemove.row)
        && row.type === pendingRemove.row.type))) close();
  }
  function close() { pendingRemove = null; $('cart-remove-dialog').close(); }
  $('cart-get-stock').addEventListener('click', () => stock.fetchAll(cart.state.rows.map(targetOf)));
  $('cart-page-refresh').addEventListener('click', () => {
    details.retryFailed();
    cart.refresh();
  });
  $('cart-remove-cancel').addEventListener('click', close);
  $('cart-remove-confirm').addEventListener('click', () => {
    const pending = pendingRemove; close();
    if (pending && pending.cart === carts[current()]) pending.cart.remove(targetOf(pending.row), pending.row.type);
  });
  $('cart-remove-dialog').addEventListener('close', () => { pendingRemove = null; });
  return {render, close, current};
}
