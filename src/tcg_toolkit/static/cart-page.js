// Cart page and the +/− stepper shared with search results. Everything shown is
// what YuYu-Tei last reported; app.js owns the view toggle, prices and links.
import {t, getLanguage} from './i18n.js';
import {cardImage, rarityBadge} from './card-view.js';
import {productKey, parseQuantity} from './cart.js';
import {numberButton} from './copy-number.js';
import {showFeedback} from './feedback.js';
const $ = id => document.getElementById(id);
const el = (tag, className = '', text = '') => {const node = document.createElement(tag); node.className = className; node.textContent = text; return node;};
const icon = name => el('i', `bi ${name}`);
const number = value => value.toLocaleString(getLanguage());
// Cart rows name games by their shop path; the rest of the UI uses search game keys.
const GAME_KEYS = {lo: 'lycee'}, KNOWN_GAMES = ['wsr', 'lycee', 'chaos', 'ws', 'pm', 'sev'];
const targetOf = row => ({gid: row.gid, ver: row.ver, cid: row.cid, kizu: row.kizu});
const SHOP_CART_URL = 'https://yuyu-tei.jp/cart/sell';
const STOCK_ICONS = {in_stock: 'bi-check-circle-fill', out_of_stock: 'bi-x-circle', unknown: 'bi-question-circle'};

// Unsent typing survives re-renders (any cart change redraws the list).
const drafts = new Map();
export function cartStepper(cart, target, {quantity, onIncrease, onDecrease, onSet, draftKey = productKey(target),
  increaseKey = 'cart_increase', canIncrease = true, canDecrease = true, canEdit = true}) {
  const box = el('div', 'cart-controls'), busy = cart.isBusy(target);
  const pending = busy || (cart.state.loading && !cart.state.rows.length);
  const stepper = el('div', `cart-stepper${quantity ? ' active' : ''}`); stepper.setAttribute('role', 'group');
  stepper.setAttribute('aria-label', t('cart_quantity_label', {quantity}));
  const button = (iconName, key, disabled, action) => {
    const node = el('button', 'cart-step'); node.type = 'button'; node.append(icon(iconName));
    node.setAttribute('aria-label', t(key)); node.title = t(key); node.disabled = disabled;
    node.addEventListener('click', action); return node;
  };
  let count;
  if (busy) { count = el('span', 'cart-qty'); count.append(icon('bi-arrow-repeat spin')); }
  else {
    count = document.createElement('input');
    count.className = 'cart-qty'; count.inputMode = 'numeric'; count.autocomplete = 'off';
    count.setAttribute('aria-label', t('cart_quantity_input')); count.title = t('cart_quantity_input');
    count.value = String(quantity); count.disabled = pending || !onSet || !canEdit;
    const draft = drafts.get(draftKey);
    if (draft && !count.disabled) {
      count.value = draft.value;
      // Re-rendered while typing: put the caret back once the box is in the page.
      requestAnimationFrame(() => { if (count.isConnected) { count.focus(); count.setSelectionRange(draft.start, draft.end); } });
    }
    const remember = () => drafts.set(draftKey, {value: count.value, start: count.selectionStart, end: count.selectionEnd});
    const commit = () => {
      drafts.delete(draftKey);
      const desired = parseQuantity(count.value);
      // Not a whole number, or unchanged: put the real quantity back and send nothing.
      if (desired === null || desired === quantity) { count.value = String(quantity); return; }
      onSet(desired);
    };
    count.addEventListener('focus', () => { if (!drafts.has(draftKey)) count.select(); });
    count.addEventListener('input', remember);
    count.addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); commit(); count.blur(); }
      else if (event.key === 'Escape') { drafts.delete(draftKey); count.value = String(quantity); count.blur(); }
      else queueMicrotask(remember);
    });
    // A blur caused by the list redrawing is not a commit; the new box takes over the draft.
    count.addEventListener('blur', () => { if (count.isConnected && drafts.has(draftKey)) commit(); });
  }
  stepper.append(button('bi-dash-lg', 'cart_decrease', pending || !quantity || !canDecrease, onDecrease), count,
    button('bi-plus-lg', increaseKey, pending || !canIncrease, onIncrease));
  box.append(stepper);
  // No line of text under the stepper: a bubble while fresh, then a dot if it was an error.
  const message = cart.message(target);
  if (message) {
    const {key, params} = typeof message === 'string' ? {key: message, params: {}} : message;
    const text = t(key, Object.fromEntries(Object.entries(params).map(([k, v]) => [k, number(v)])));
    showFeedback(message, text, {anchor: box, control: stepper, started: cart.bubbleStarted(target)});
  }
  return box;
}

export function createCartPage({cart, stock, details, amountDisplay, gameLabel, cardLink, currentView}) {
  let pendingRemove = null;
  // Number and rarity come with rows added from search; others are looked up once.
  function info(row) {
    return row.card_number ? {status: 'done', card_number: row.card_number, rarity: row.rarity ?? null}
      : details.get(targetOf(row));
  }
  function item(row) {
    const known = info(row), rarity = known.rarity ?? null;
    // Cart names read "<rarity> <name>"; the rarity is shown as its own badge.
    const full = row.name ?? '', name = rarity && full.startsWith(rarity + ' ') ? full.slice(rarity.length + 1) : full;
    return {...row, name, rarity, card_number: known.card_number ?? '', game: GAME_KEYS[row.game] ?? row.game, price: row.unit_price};
  }
  function tags(row, {withRarity = false} = {}) {
    const box = el('div', 'card-tags'), shown = item(row);
    box.append(el('span', 'game-tag', KNOWN_GAMES.includes(shown.game) ? gameLabel(shown.game) : shown.game));
    if (withRarity) box.append(rarityBadge(shown));
    if (row.type === 'reserved') box.append(el('span', 'cart-tag', t('cart_reserved')));
    if (row.kizu) box.append(el('span', 'cart-tag', t('cart_damaged')));
    return box;
  }
  function cardNumber(row) {
    const known = info(row);
    if (known.status === 'done') return numberButton(known.card_number);
    const pending = el('span', 'mono muted');
    if (known.status === 'loading') { pending.append(icon('bi-arrow-repeat spin')); pending.title = t('cart_details_loading'); }
    else { pending.textContent = '—'; pending.title = t('cart_details_failed'); }
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
      pendingRemove = row;
      $('cart-remove-message').textContent = t('cart_confirm_remove', {name: item(row).name, quantity: number(row.quantity)});
      $('cart-remove-dialog').showModal();
    });
    return button;
  }
  // Same badge language as search results, plus a warning when the shop holds fewer than the cart.
  function stockBadge(row, state) {
    const result = state.status === 'done' ? state : state.last;
    if (!result) {
      const badge = el('span', `badge unknown${state.status === 'failed' ? ' stock-failed' : ''}`);
      if (state.status === 'failed') badge.append(icon('bi-exclamation-circle'), t('cart_stock_failed_short'));
      else { badge.textContent = '—'; badge.title = t('cart_stock_idle'); }
      return badge;
    }
    const quantity = result.quantity;
    const short = quantity !== null && quantity > 0 && quantity < row.quantity;
    const status = quantity === null ? 'unknown' : quantity === 0 ? 'out_of_stock' : 'in_stock';
    const badge = el('span', `badge ${status}${short ? ' stock-short' : ''}`);
    badge.append(icon(short ? 'bi-exclamation-triangle-fill' : STOCK_ICONS[status]),
      t(short ? 'cart_stock_short' : status) + (quantity === null ? '' : ` (${number(quantity)})`));
    if (short) badge.title = t('cart_stock_short_hint', {stock: number(quantity), quantity: number(row.quantity)});
    return badge;
  }
  function stockControl(row) {
    const target = targetOf(row), state = stock.get(target), loading = state.status === 'loading';
    const box = el('div', 'cart-stock-control');
    const badge = stockBadge(row, state); badge.setAttribute('role', 'status');
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
    if (!state.configured) return [t('cart_need_credentials'), link('cart_go_setup', '#account')];
    if (state.loading) return [t('cart_loading')];
    if (state.error) return [t(state.error)];
    if (!state.complete) return [t('cart_summary_incomplete')];
    return [t('cart_empty'), link('cart_go_search', '#search')];
  }
  function render() {
    const state = cart.state, rows = state.rows, view = currentView();
    const has = rows.length > 0;
    $('cart-stats').hidden = !state.configured || (!has && !state.complete);
    // Checkout happens on YuYu-Tei: open its own cart (after the usual leaving-the-site confirmation).
    const checkout = cardLink({name: t('cart_checkout_name'), product_url: SHOP_CART_URL}, 'cart-checkout');
    const logo = document.createElement('img');
    logo.src = './yuyutei-logo.webp'; logo.alt = ''; logo.className = 'cart-checkout-logo'; logo.setAttribute('aria-hidden', 'true');
    checkout.replaceChildren(logo, el('span', '', t('cart_checkout')), icon('bi-box-arrow-up-right'));
    checkout.title = t('cart_checkout_hint');
    $('cart-checkout-slot').replaceChildren(checkout);
    $('cart-kinds').textContent = number(rows.length);
    $('cart-quantity-total').textContent = state.totalQuantity === null ? '—' : number(state.totalQuantity);
    $('cart-subtotal').replaceChildren(state.subtotal === null ? '—' : amountDisplay(state.subtotal));
    $('cart-count').hidden = !has; $('cart-count').textContent = number(rows.length);
    $('cart-status').textContent = state.loading && has ? t('cart_loading') : state.error && has ? t(state.error) : '';
    $('cart-warning').hidden = state.complete;
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
    if (pendingRemove && !rows.some(row => productKey(row) === productKey(pendingRemove) && row.type === pendingRemove.type))
      $('cart-remove-dialog').close();
  }
  $('cart-get-stock').addEventListener('click', () => stock.fetchAll(cart.state.rows.map(targetOf)));
  $('cart-page-refresh').addEventListener('click', () => { details.retryFailed(); cart.refresh(); });
  $('cart-remove-cancel').addEventListener('click', () => $('cart-remove-dialog').close());
  $('cart-remove-confirm').addEventListener('click', () => {
    const row = pendingRemove; $('cart-remove-dialog').close();
    if (row) cart.remove(targetOf(row), row.type);
  });
  $('cart-remove-dialog').addEventListener('close', () => { pendingRemove = null; });
  return {render, close: () => $('cart-remove-dialog').close()};
}
