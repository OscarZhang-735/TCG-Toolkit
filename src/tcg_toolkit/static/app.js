import {createProxySettings} from './catalog/proxy-settings.js';
import {clearCards, activateCards, ownedQuantity, setCardsCurrency} from './collection/cards-ui.js';
import {clearGraded, activateGraded, setGradedCurrency} from './collection/graded-cards-ui.js';
import {createFavorites, cardKey} from './collection/favorites.js';
import {bindFavoriteGroups} from './collection/favorite-groups-ui.js';
import {createSelection} from './collection/favorite-selection.js';
import {createSubscriptions, subscriptionKey} from './subscriptions/subscriptions.js';
import {t, applyLanguage, setLanguage, getLanguage} from './i18n.js';
import {loadResults} from './catalog/logic.js';
import {rarityBadge, cardImage} from './catalog/card-view.js';
import {stockBadge} from './catalog/stock-badge.js';
import {numberButton} from './shared/copy-number.js';
import {setNavCount} from './shared/nav-count.js';
import {createFilterPanel} from './catalog/filters.js';
import {readHistory, appendHistory, saveHistory} from './catalog/history.js';
import {fetchRate, formatPrice} from './shared/currency.js';
import {yuyuteiClient} from './cart/yuyutei-client.js';
import {createCart} from './cart/cart.js';
import {cartTarget} from './catalog/product-identity.js';
import {bindCartSettings} from './cart/cart-settings.js';
import {cardmaxClient} from './cart/cardmax-client.js';
import {bindCardmaxSettings, createCardmaxCart, cardmaxTarget} from './cart/cardmax-cart.js';
import {hobbystationClient} from './cart/hobbystation-client.js';
import {bindHobbystationSettings} from './cart/hs-cart-settings.js';
import {createHobbystationCart, hobbystationTarget} from './cart/hs-cart.js';
import {SHOP_CART_PAGES} from './cart/shop-cart-page.js';
import {createCartPage} from './cart/cart-page.js';
import {cartStepper} from './cart/cart-stepper.js';
import {createProductDetails} from './catalog/product-details.js';
import {showFeedback, markDot} from './shared/feedback.js';
import {messageTier} from './shared/notices.js';
import {createCartStock} from './cart/cart-stock.js';
import {SEARCH_SOURCES, searchTargets, searchParams} from './catalog/search-sources.js';
const $ = id => document.getElementById(id);
const checked = name => [...document.querySelectorAll(`input[name="${name}"]:checked`)].map(el => el.value);
const gameKeys = {"wsr": "wsr", "lycee": "lycee", "chaos": "chaostcg", "ws": "ws", "pm": "pm", "sev": "sev", "sev_en": "sev_en"};
const gameLabel = game => t(gameKeys[game]);
const sourceLabel = source => t(SEARCH_SOURCES[source]?.label ?? source);
let items = [], limit = 100, controller = null, batch = 0, started = false;
let view = localStorage.getItem('view') === 'grid' ? 'grid' : 'list';
const el = (tag, className = '', text = '') => {const node = document.createElement(tag); node.className = className; node.textContent = text; return node;};
const icon = name => el('i', `bi ${name}`);
let displayCurrency = 'JPY', exchangeRate = 1, currencyStatus = null, currencyRequest = 0;
// Converted amount with the original yen price underneath when another currency is shown.
function amountDisplay(amount) {
  const price = el('span', 'converted-price');
  price.append(el('span', '', formatPrice(amount, displayCurrency, exchangeRate, getLanguage())));
  if (displayCurrency !== 'JPY') price.append(el('small', 'original-price', formatPrice(amount, 'JPY', 1, getLanguage())));
  return price;
}
const priceDisplay = item => amountDisplay(item.price);
const money = price => formatPrice(price, displayCurrency, exchangeRate, getLanguage());
const searchFilters = createFilterPanel($('search-filters'), {onChange: () => {limit = 100; render();}, formatPrice: money});
const favoriteFilters = createFilterPanel($('favorites-filters'), {onChange: () => renderFavorites(), formatPrice: money, sources: true,
  isSubscribed: item => Boolean(subscriptions.find(item))});
function renderCurrencyStatus() { $('currency-status').hidden = !currencyStatus; $('currency-status').textContent = t('currency_fetched_at', {time: currencyStatus ? new Date(currencyStatus * 1000).toLocaleString(getLanguage()) : '—'}); }
function renderCurrency() {
  renderCurrencyStatus(); render(); renderFavorites(); renderCartSummary(); cartPage.render();
  setCardsCurrency(displayCurrency, exchangeRate);
  setGradedCurrency(displayCurrency, exchangeRate);
}
$('currency').addEventListener('change', async () => {
  const target = $('currency').value, request = ++currencyRequest;
  displayCurrency = 'JPY'; exchangeRate = 1;
  currencyStatus = null;
  renderCurrency();
  try {
    let fetchedAt = null;
    const rate = await fetchRate(target, requestAPI, value => { fetchedAt = value; });
    if (request !== currencyRequest) return;
    displayCurrency = target; exchangeRate = rate;
    currencyStatus = fetchedAt;
  } catch {
    if (request !== currencyRequest) return;
    $('currency').value = 'JPY'; currencyStatus = null;
  }
  renderCurrency();
});
const proxySettings = createProxySettings();
// Effective subscription recipient (custom notification email or verified account email).
let userId = null, requestAPI = fetch, notificationEmail = null;
let history = [];
const favorites = createFavorites(removedItem => {
  if (removedItem) subscriptions.forget(removedItem);
  for (const {item} of favorites.state.records) rememberProduct(item);
  render(); renderFavorites(); cartPage?.render();
});
const favoriteGroups = bindFavoriteGroups(favorites);
const subscriptions = createSubscriptions(() => {render(); renderFavorites(); renderNotice();});
const selection = createSelection(favorites, subscriptions, () => renderFavorites());
let cartPage = null;
const cartStock = createCartStock(() => cartPage?.render());
const productDetails = createProductDetails(() => cartPage?.render());
const renderShopCart = () => {render(); renderFavorites(); renderCartSummary(); renderNotice(); renderCartNav(); cartPage?.render();};
const cart = createCart(yuyuteiClient, () => { renderShopCart(); cartSettings.render(); });
const hsCart = createHobbystationCart(hobbystationClient, renderShopCart);
const cmCart = createCardmaxCart(cardmaxClient, renderShopCart);
// Account-level problems (credentials, notification email) would hit every card the same way,
// so they appear once, here, until dismissed or the next successful request.
function renderNotice() {
  const notice = cart.state.notice || hsCart.state.notice || cmCart.state.notice || subscriptions.state.notice;
  $('page-notice').hidden = !notice || location.hash === '#account';
  $('page-notice-text').textContent = notice ? t(notice) : '';
}
const dismissNotice = () => { cart.dismissNotice(); hsCart.dismissNotice(); cmCart.dismissNotice(); subscriptions.dismissNotice(); };
$('page-notice-close').addEventListener('click', dismissNotice);
$('page-notice-action').addEventListener('click', dismissNotice);
const cartSettings = bindCartSettings(yuyuteiClient, cart);
const hobbystationSettings = bindHobbystationSettings(hobbystationClient, hsCart);
const cardmaxSettings = bindCardmaxSettings(cardmaxClient, cmCart);
const shopCarts = {yuyutei: cart, hobbystation: hsCart, cardmax: cmCart};
const cartTargets = {yuyutei: cartTarget, hobbystation: hobbystationTarget, cardmax: cardmaxTarget};
function rememberProduct(item) {
  const target = cartTargets[item.source ?? 'yuyutei'];
  productDetails.remember(target(item), item);
}
const summaryRows = Object.entries(SHOP_CART_PAGES).map(([href, {source}]) => {
  const row = el('div', 'cart-summary'), link = el('a'), status = el('span'), refresh = el('button', 'icon-button');
  row.dataset.source = source;
  refresh.type = 'button'; refresh.append(icon('bi-arrow-clockwise'));
  refresh.addEventListener('click', () => shopCarts[source].refresh());
  row.append(link, status, refresh); $('cart-summary').append(row);
  return {source, href, row, link, status, refresh};
});
function renderCartSummary() {
  const selected = checked('merchant');
  for (const {source, href, row, link, status, refresh} of summaryRows) {
    row.hidden = !selected.includes(source);
    if (row.hidden) continue;
    const state = shopCarts[source].state;
    link.href = state.configured ? href : '#account';
    link.textContent = state.configured ? sourceLabel(source) : sourceLabel(source) + ' · ' + t('cart_setup');
    status.textContent = !state.configured ? '' : state.loading && !state.rows.length ? t('cart_loading')
      : state.error ? t(state.error) : state.totalQuantity == null || state.subtotal == null ? t('cart_summary_incomplete')
      : t('cart_summary', {quantity: state.totalQuantity.toLocaleString(getLanguage()), subtotal: money(state.subtotal)});
    refresh.hidden = !state.configured; refresh.disabled = state.loading;
    refresh.title = t('cart_refresh') + ' · ' + sourceLabel(source);
    refresh.setAttribute('aria-label', refresh.title);
    refresh.firstChild.classList.toggle('spin', state.loading);
  }
}
cartPage = createCartPage({carts: shopCarts,
  stock: cartStock, details: productDetails, amountDisplay, gameLabel, cardLink, currentView: () => view});
document.addEventListener('viewchange', () => cartPage.render());
// Sidebar cart menu: each shop's count on its entry, their sum on the menu itself.
function renderCartNav() {
  const counts = [[cart, 'cart-nav-count'], [hsCart, 'hs-cart-nav-count'], [cmCart, 'cm-cart-nav-count']].map(([shop, id]) => {
    const count = shop.state.configured ? shop.state.totalQuantity ?? 0 : 0;
    setNavCount($(id), count); return count;
  });
  setNavCount($('cart-nav-total'), counts.reduce((sum, count) => sum + count, 0));
}
// The displayed quantity comes from the corresponding merchant's cart.
function cartControls(item) {
  const source = item.source ?? 'yuyutei', cart = shopCarts[source];
  const target = cartTargets[source](item);
  if (!target || !cart.state.configured) { const box = el('div', 'cart-controls'); box.append(el('span', 'muted', '—')); return box; }
  const quantity = cart.quantity(target);
  const soldOut = item.stock_status === 'out_of_stock' || (item.stock_quantity != null && quantity >= item.stock_quantity);
  // Stock is the cart's upper bound here; typed numbers are clamped to it.
  const stock = item.stock_status === 'out_of_stock' ? 0 : item.stock_quantity ?? null, limits = cart.range(target, {stock});
  return cartStepper(cart, target, {shop: sourceLabel(source), quantity, canIncrease: !soldOut, increaseKey: soldOut ? 'cart_no_stock' : 'cart_increase',
    canEdit: limits.min < limits.max, onSet: value => cart.setTo(target, value, {stock, item}),
    onDecrease: () => cart.decrease(target), onIncrease: () => cart.increase(target, item)});
}
function closeHistory() { $('search-history').hidden = true; $('keyword').setAttribute('aria-expanded', 'false'); }
function showHistory() {
  const list = $('history-items');
  list.replaceChildren(...[...history].reverse().map(keyword => {
    const row = el('div', 'history-row');
    const button = el('button', 'history-item'); button.type = 'button'; button.title = keyword;
    button.append(icon('bi-clock-history'), el('span', 'history-text', keyword));
    button.addEventListener('click', () => { $('keyword').value = keyword; $('keyword').focus(); closeHistory(); validateSearch(); });
    const remove = el('button', 'history-delete'); remove.type = 'button';
    remove.append(icon('bi-x-lg'));
    remove.title = t('history_delete', {keyword});
    remove.setAttribute('aria-label', remove.title);
    remove.addEventListener('click', event => {
      event.preventDefault(); event.stopPropagation();
      history = history.filter(value => value !== keyword);
      saveHistory(localStorage, history, userId);
      $('keyword').focus(); showHistory();
    });
    row.append(button, remove);
    return row;
  }));
  $('history-empty').hidden = history.length > 0;
  $('history-clear').disabled = history.length === 0;
  $('search-history').hidden = false; $('keyword').setAttribute('aria-expanded', 'true');
}
$('keyword').addEventListener('focus', showHistory);
$('history-clear').addEventListener('click', () => {
  history = []; saveHistory(localStorage, history, userId);
  $('keyword').focus(); showHistory();
});
$('keyword').addEventListener('click', showHistory);
$('keyword').addEventListener('keydown', event => {
  if (event.key === 'ArrowDown') {event.preventDefault(); showHistory(); $('history-items').querySelector('button')?.focus();}
  if (event.key === 'Escape') closeHistory();
});
$('search-history').addEventListener('keydown', event => {
  const buttons = [...$('history-items').querySelectorAll('button')];
  const index = buttons.indexOf(document.activeElement);
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault(); buttons[(index + (event.key === 'ArrowDown' ? 1 : buttons.length - 1)) % buttons.length]?.focus();
  }
  if (event.key === 'Escape') { $('keyword').focus(); closeHistory(); }
});
document.addEventListener('pointerdown', event => {if (!event.target.closest('#keyword-wrapper')) closeHistory();});
$('keyword-wrapper').addEventListener('focusout', event => {if (!$('keyword-wrapper').contains(event.relatedTarget)) closeHistory();});
let statusKey = '', statusIcon = '', errors = [];
function setStatus(key, iconName = '') {
  statusKey = key; statusIcon = iconName;
  const text = key ? t(key) : '';
  $('status').replaceChildren(...(iconName ? [icon(iconName)] : []), text);
}
function validateSearch() {
  const games = checked('source'), sources = checked('merchant');
  $('cardmax-stock-option').hidden = !sources.includes('cardmax');
  const message = !$('keyword').value.trim() ? t('enter_a_keyword') : !games.length ? t('select_at_least_one_game')
    : !searchTargets(games, sources).length ? t('no_supported_source') : '';
  $('search-button').title = message;
  $('search-button').disabled = Boolean(message);
}
function setView(next) {
  view = next; localStorage.setItem('view', view);
  document.querySelectorAll('[data-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.view === view)));
  render(); renderFavorites();
  // Other pages (My cards) re-render on this instead of relying on click-handler order.
  document.dispatchEvent(new CustomEvent('viewchange', {detail: view}));
}
const linkShops = new Map(Object.values(SHOP_CART_PAGES).map(({cartUrl, source}) => [new URL(cartUrl).hostname, source]));
function cardLink(item, className = '') {
  const link = el('a', className, item.name);
  const url = new URL(item.product_url, location.origin);
  const source = linkShops.get(url.hostname);
  if (url.protocol === 'https:' && source) link.href = url.href;
  link.target = '_blank'; link.rel = 'noopener noreferrer';
  const confirm = event => {
    event.preventDefault();
    if (!link.hasAttribute('href')) return;
    $('detail-description').textContent = t('detail_description', {shop: sourceLabel(source)});
    $('detail-name').textContent = item.name;
    $('detail-url').textContent = link.href;
    $('detail-confirm').href = link.href;
    $('detail-dialog').showModal();
  };
  link.addEventListener('click', confirm);
  link.addEventListener('auxclick', event => {if (event.button === 1) confirm(event);});
  return link;
}
function favoriteToggle(item) {
  const saved = favorites.find(item), subscribed = saved && subscriptions.find(item);
  const busy = favorites.state.busy.has(cardKey(item)) || subscriptions.state.busy.has(subscriptionKey(item));
  const button = el('button', 'icon-button fav-toggle'); button.type = 'button';
  button.append(icon(busy ? 'bi-arrow-repeat spin' : saved ? 'bi-star-fill' : 'bi-star'));
  button.setAttribute('aria-pressed', String(Boolean(saved)));
  button.setAttribute('aria-label', t(subscribed ? 'favorite_remove_subscribed' : saved ? 'favorite_remove' : 'favorite_add')); button.title = button.getAttribute('aria-label');
  button.disabled = !favorites.state.ready || busy;
  button.addEventListener('click', () => favorites.toggle(item));
  if (!saved) favoriteGroups.attachAddMenu(button, item);
  const message = favorites.state.messages.get(cardKey(item));
  if (message && messageTier(message) === 'error') markDot(button, t(message));
  return button;
}
function subscriptionToggle(item) {
  const saved = subscriptions.find(item), busy = subscriptions.state.busy.has(subscriptionKey(item));
  const button = el('button', 'icon-button fav-subscribe'); button.type = 'button';
  button.append(icon(busy ? 'bi-arrow-repeat spin' : saved ? 'bi-bell-fill' : 'bi-bell'));
  button.setAttribute('aria-pressed', String(Boolean(saved)));
  const label = busy ? (saved ? 'subscription_removing' : 'subscription_adding') : saved ? 'subscription_remove'
    : notificationEmail ? 'subscription_add' : 'subscription_email_required';
  button.setAttribute('aria-label', t(label)); button.title = button.getAttribute('aria-label');
  button.disabled = !subscriptions.state.ready || busy || favorites.state.busy.has(cardKey(item)) || (!saved && !notificationEmail);
  button.addEventListener('click', () => subscriptions.toggle(item));
  const message = subscriptions.state.messages.get(subscriptionKey(item));
  if (message && !busy) markDot(button, t(message));
  else if (saved?.last_error && !busy) markDot(button, t('subscription_check_failed'), {tone: 'warning'});
  return button;
}
// Refresh and subscribe act on saved cards, so they only appear on the favorites page.
function favoritePageActions(item) {
  if (!favorites.find(item) || location.hash !== '#favorites') return [];
  return [favoriteRefresh(item), ...(favorites.state.groups.length ? [favoriteMove(item)] : []), subscriptionToggle(item)];
}
function favoriteMove(item) {
  const busy = favorites.state.busy.has(cardKey(item));
  const button = el('button', 'icon-button fav-move'); button.type = 'button';
  button.append(icon('bi-folder-symlink'));
  button.setAttribute('aria-label', t('favorite_move')); button.title = button.getAttribute('aria-label');
  button.disabled = favorites.state.running || favorites.state.groupBusy || busy;
  button.addEventListener('click', () => favoriteGroups.openMove(item, cardKey(item)));
  return button;
}
function favoriteRefresh(item) {
  const busy = favorites.state.busy.has(cardKey(item));
  const button = el('button', 'icon-button fav-refresh'); button.type = 'button';
  button.append(icon(busy ? 'bi-arrow-clockwise spin' : 'bi-arrow-clockwise'));
  button.setAttribute('aria-label', t(busy ? 'favorite_refreshing' : 'favorite_refresh')); button.title = button.getAttribute('aria-label');
  button.disabled = favorites.state.running || busy;
  button.addEventListener('click', () => favorites.refresh(item));
  return button;
}
// Update / check times appear only while the favorites page's details toggle is on.
let showDetails = false;
const favoriteGroup = item => favorites.state.groups.find(group => group.id === favorites.find(item)?.group_id);
// Ungrouped cards get no tag.
function groupTag(item) {
  const group = favoriteGroup(item);
  if (!group) return null;
  const tag = el('span', 'group-tag', group.name); tag.title = group.name;
  return tag;
}
// Update and check times, shown only on the favorites page while details are on.
function favoriteMeta(item) {
  const saved = favorites.find(item);
  if (!saved || location.hash !== '#favorites' || !showDetails) return null;
  const subscribed = subscriptions.find(item);
  const box = el('div', 'fav-meta');
  const when = seconds => new Date(seconds * 1000).toLocaleString(getLanguage(), {dateStyle: 'short', timeStyle: 'short'});
  const refreshed = el('span', 'fav-time');
  refreshed.append(icon('bi-clock'), t('favorite_time', {time: saved.refreshed_at ? when(saved.refreshed_at) : t('favorite_never')}));
  const checked = el('span', 'fav-time');
  checked.append(icon('bi-bell'), t('subscription_checked', {time: subscribed ? when(subscribed.checked_at) : t('subscription_none')}));
  box.append(refreshed, checked);
  return box;
}
// Fresh favorite / subscription messages as a bubble on the card; errors also leave a dot
// on the star or bell (see favoriteToggle / subscriptionToggle). A successful refresh shows
// through its new update time instead.
function favoriteBubbles(item, anchor) {
  const reported = favorites.state.messages.get(cardKey(item));
  if (reported && reported !== 'favorite_updated')
    showFeedback(reported, t(reported), {anchor, control: null, started: favorites.bubbleStarted(item)});
  const subscribed = location.hash === '#favorites' && subscriptions.state.messages.get(subscriptionKey(item));
  if (subscribed) showFeedback(subscribed, t(subscribed), {anchor, control: null, started: subscriptions.bubbleStarted(item)});
}
function favoriteCell(item) {
  const box = el('div', 'favorite-controls'), actions = el('div', 'fav-actions');
  actions.append(favoriteToggle(item), ...favoritePageActions(item));
  box.append(actions);
  const meta = favoriteMeta(item); if (meta) box.append(meta);
  favoriteBubbles(item, box);
  return box;
}
// Checkbox for multi-select; only shown while selecting (CSS hides it otherwise).
function selectBox(item) {
  const box = el('input', 'select-box'); box.type = 'checkbox';
  box.checked = selection.state.keys.has(cardKey(item)); box.disabled = selection.state.running;
  box.setAttribute('aria-label', t('favorite_select_card', {name: item.name}));
  box.addEventListener('click', event => event.stopPropagation());
  box.addEventListener('change', () => selection.toggle(item));
  return box;
}
const filteredFavorites = (rows = favorites.visible()) => favoriteFilters.apply(rows.map(row => row.item));
function favoriteCartSource() {
  const sources = favoriteFilters.chosenSources();
  return sources.length === 1 && shopCarts[sources[0]].state.configured ? sources[0] : null;
}
function renderSelection(matches) {
  const sel = selection.state, count = sel.keys.size, busy = sel.running || favorites.state.running;
  $('favorites-panel').classList.toggle('selecting', sel.active);
  $('favorites-selection-bar').hidden = !sel.active;
  $('favorites-select').setAttribute('aria-pressed', String(sel.active));
  $('favorites-select').querySelector('span').textContent = t(sel.active ? 'favorite_select_done' : 'favorite_select');
  $('favorites-select').disabled = sel.running || !favorites.state.ready;
  $('favorites-select-all').checked = count > 0 && count === matches.length;
  $('favorites-select-all').indeterminate = count > 0 && count < matches.length;
  $('favorites-select-all').disabled = busy || !matches.length;
  const selectedCount = t('favorite_selected_count', {count});
  let status = sel.running ? t('favorite_bulk_progress', {done: sel.done, total: sel.total}) : selectedCount;
  if (sel.action === 'maximize') {
    status = sel.running ? t('favorite_cart_progress', {done: sel.done, total: sel.total})
      : selectedCount + ' · ' + t('favorite_cart_result', {done: sel.done - sel.failed, total: sel.total});
  } else if (!sel.running && sel.failed) {
    status += ' · ' + t('favorite_bulk_failed', {count: sel.failed});
  }
  $('favorites-selection-count').textContent = status;
  const maximize = $('favorites-bulk-maximize'), source = favoriteCartSource();
  maximize.disabled = !sel.active || busy || !source || !matches.length;
  maximize.querySelector('span').textContent = t('favorite_cart_maximize', {count: matches.length});
  maximize.title = source ? '' : t('favorite_cart_requirements');
  $('favorites-bulk-refresh').disabled = busy || !count;
  // Subscribing needs a verified notification address, same as the per-card bell.
  $('favorites-bulk-subscribe').disabled = busy || !count || !subscriptions.state.ready || !notificationEmail;
  $('favorites-bulk-subscribe').title = notificationEmail ? '' : t('subscription_email_required');
  $('favorites-bulk-unsubscribe').disabled = busy || !count || !subscriptions.state.ready;
  $('favorites-bulk-move').disabled = busy || !count || !favorites.state.groups.length;
  $('favorites-bulk-remove').disabled = busy || !count;
}
function renderFavorites() {
  const state = favorites.state, rows = favorites.visible(), group = favoriteGroups.selected();
  const matches = filteredFavorites(rows);
  selection.prune(matches); renderSelection(matches);
  setNavCount($('favorites-nav-count'), state.ready ? state.records.length : 0);
  favoriteGroups.render();
  $('favorites-title').textContent = group ? group.name : t('favorite_cards');
  // Without any groups the group column would only hold dashes.
  $('favorites-list').classList.toggle('without-groups', !state.groups.length);
  favoriteFilters.element.hidden = !state.ready;
  $('favorites-rows').replaceChildren(...(view === 'list' ? matches.map(item => tableRow(item, true)) : []));
  $('favorites-grid').replaceChildren(...(view === 'grid' ? matches.map(item => gridCard(item, true)) : []));
  $('favorites-list').hidden = view !== 'list' || !matches.length;
  $('favorites-grid').hidden = view !== 'grid' || !matches.length;
  $('favorites-empty').hidden = !state.ready || matches.length > 0;
  $('favorites-empty').querySelector('h3').textContent = t(rows.length ? 'no_matching_results' : state.records.length ? 'favorite_group_empty' : 'favorite_empty');
  $('favorites-refresh').disabled = !state.ready || !rows.length || state.running || state.busy.size > 0 || selection.state.running;
  const error = state.error || subscriptions.state.error;
  $('favorites-retry').hidden = !error;
  $('favorites-status').textContent = error ? t(error) : !state.ready ? t('favorite_loading') : state.total ? t('favorite_progress', {done: state.done, total: state.total}) : '';
  $('favorites-count').textContent = t('counts', {matching: matches.length.toLocaleString(getLanguage()), loaded: rows.length.toLocaleString(getLanguage())});
  $('favorites-count').hidden = !state.ready;
  $('favorites-progress').hidden = !state.running;
  $('favorites-progress-bar').style.width = `${state.total ? state.done / state.total * 100 : 0}%`;
  $('favorites-refresh').querySelector('.bi').classList.toggle('spin', state.running);
  $('favorites-details').setAttribute('aria-pressed', String(showDetails));
  $('favorites-details').querySelector('span').textContent = t(showDetails ? 'favorite_details_hide' : 'favorite_details_show');
}
$('favorites-refresh').addEventListener('click', () => favorites.refreshAll());
$('favorites-details').addEventListener('click', () => {showDetails = !showDetails; renderFavorites();});
$('favorites-select').addEventListener('click', () => selection.setActive(!selection.state.active));
$('favorites-select-all').addEventListener('change', event => {
  selection.setAll(filteredFavorites(), event.target.checked);
});
$('favorites-bulk-maximize').addEventListener('click', () => {
  const source = favoriteCartSource();
  if (!source || favorites.state.running) return;
  selection.maximize(filteredFavorites(), item => {
    const target = cartTargets[source](item);
    return target ? shopCarts[source].maximize(target, () => cartStock.fetch(target)) : false;
  });
});
$('favorites-bulk-refresh').addEventListener('click', () => selection.refresh());
$('favorites-bulk-subscribe').addEventListener('click', () => selection.subscribe());
$('favorites-bulk-unsubscribe').addEventListener('click', () => selection.unsubscribe());
$('favorites-bulk-move').addEventListener('click', () => {
  const count = selection.state.keys.size;
  if (count) favoriteGroups.openBulkMove(count, groupId => selection.moveTo(groupId));
});
$('favorites-bulk-remove').addEventListener('click', () => {
  const count = selection.state.keys.size; if (!count) return;
  $('favorites-bulk-remove-message').textContent = t('favorite_bulk_remove_message', {count: count.toLocaleString(getLanguage())});
  $('favorites-bulk-remove-dialog').showModal();
});
$('favorites-bulk-remove-cancel').addEventListener('click', () => $('favorites-bulk-remove-dialog').close());
$('favorites-bulk-remove-confirm').addEventListener('click', () => { $('favorites-bulk-remove-dialog').close(); selection.remove(); });
$('favorites-retry').addEventListener('click', () => {
  if (favorites.state.error) favorites.load();
  if (subscriptions.state.error) subscriptions.load();
});
window.addEventListener('hashchange', () => {
  render(); renderFavorites(); renderNotice();
  const shop = cartPage.current();
  if (shop && userId && !shopCarts[shop].state.loading) shopCarts[shop].refresh();
  cartPage.render();
});
document.addEventListener('inventorychange', () => {render(); renderFavorites();});
// Round badge with the merchant's site icon; the name is in the tooltip and accessible label.
function sourceBadge(source) {
  const badge = el('span', `source-tag source-${source}`), label = sourceLabel(source);
  badge.title = label; badge.setAttribute('role', 'img'); badge.setAttribute('aria-label', label);
  const iconUrl = SEARCH_SOURCES[source]?.icon;
  if (iconUrl) { const image = el('img'); image.src = iconUrl; image.alt = ''; image.setAttribute('aria-hidden', 'true'); badge.append(image); }
  else badge.textContent = label.slice(0, 1);
  return badge;
}
// Search results carry their merchant; saved items (favorites) have none and show only the game.
function gameTags(item) {
  const tags = [el('span', 'game-tag', gameLabel(item.game))];
  if (item.source) tags.push(sourceBadge(item.source));
  return tags;
}
function tagStack(item) { const box = el('span', 'tag-stack'); box.append(...gameTags(item)); return box; }
function ownedIndicator(item, inFavorites, badge = false) {
  const quantity = ownedQuantity(item);
  const hsSearch = !inFavorites && item.source === 'hobbystation';
  if (quantity === undefined || (!quantity && !hsSearch)) return null;
  const count = (quantity ?? 0).toLocaleString(getLanguage());
  const value = el('span', badge ? 'owned-badge' : '');
  if (badge) value.append(icon('bi-collection'), t('cards_owned_badge', {quantity: count}));
  else value.textContent = count;
  if (quantity === null) value.title = t('hs_owned_unmatched');
  return value;
}
function tableRow(item, inFavorites = false) {
  const row = document.createElement('tr');
  const cell = (child, className = '') => {const td = el('td', className); td.append(child); row.append(td);};
  if (inFavorites) { cell(selectBox(item), 'select-col'); row.classList.toggle('selected', selection.state.keys.has(cardKey(item))); }
  const product = el('div', 'product-summary'); product.append(cardImage(item), cardLink(item));
  cell(tagStack(item)); cell(rarityBadge(item)); cell(numberButton(item.card_number)); cell(product);
  const owned = ownedIndicator(item, inFavorites);
  cell(priceDisplay(item), 'numeric'); cell(stockBadge(item));
  cell(owned ?? '—', `numeric owned-cell${owned ? '' : ' muted'}`);
  cell(cartControls(item), 'cart-col');
  if (inFavorites) cell(groupTag(item) ?? el('span', 'muted', '—'), 'group-col');
  cell(favoriteCell(item), 'action-col');
  return row;
}
function gridCard(item, inFavorites = false) {
  const card = el('article', `card ${item.stock_status}`);
  const tags = el('div', 'card-tags'); tags.append(tagStack(item), rarityBadge(item));
  const top = el('div', 'card-top'); top.append(tags, numberButton(item.card_number));
  const bottom = el('div', 'card-bottom'); bottom.append(priceDisplay(item), stockBadge(item));
  const image = cardImage(item); image.append(favoriteToggle(item));
  if (inFavorites && selection.state.active) {
    image.append(selectBox(item)); card.classList.toggle('selected', selection.state.keys.has(cardKey(item)));
  }
  const owned = ownedIndicator(item, inFavorites, true);
  if (owned) image.append(owned);
  card.append(top, image, cardLink(item, 'card-name'), bottom);
  const cartRow = el('div', 'card-cart');
  cartRow.append(el('span', 'card-cart-label', t('cart_column')), cartControls(item));
  card.append(cartRow);
  const meta = favoriteMeta(item); if (meta) card.append(meta);
  // Favorites page footer: card actions on the left, its group in the bottom-right corner.
  const actions = favoritePageActions(item);
  if (actions.length) {
    const foot = el('div', 'card-foot'), box = el('div', 'fav-actions');
    box.append(...actions); foot.append(box); const tag = groupTag(item); if (tag) foot.append(tag); card.append(foot);
  }
  favoriteBubbles(item, card);
  return card;
}
function render() {
  const matches = searchFilters.apply(items);
  $('counts').textContent = t('counts', {matching: matches.length.toLocaleString(getLanguage()), loaded: items.length.toLocaleString(getLanguage())});
  $('counts').title = t('counts_hint', {matching: matches.length.toLocaleString(getLanguage()), loaded: items.length.toLocaleString(getLanguage())});
  const shown = matches.slice(0, limit);
  const fragment = document.createDocumentFragment();
  fragment.append(...shown.map(item => view === 'grid' ? gridCard(item) : tableRow(item)));
  $('rows').replaceChildren(...(view === 'list' ? [fragment] : []));
  $('grid-view').replaceChildren(...(view === 'grid' ? [fragment] : []));
  $('list-view').hidden = view !== 'list' || !matches.length;
  $('grid-view').hidden = view !== 'grid' || !matches.length;
  $('empty').hidden = matches.length > 0;
  $('empty').querySelector('h3').textContent = items.length ? t('no_matching_results')
    : controller ? t('searching') : started ? t('no_results_found') : t('no_results');
  $('more').hidden = matches.length <= limit;
}
function wait(signal) {
  return new Promise(resolve => {
    const done = () => {clearTimeout(timer); signal.removeEventListener('abort', done); resolve();};
    const timer = setTimeout(done, 1000);
    signal.addEventListener('abort', done, {once: true});
    if (signal.aborted) done();
  });
}
$('search-form').addEventListener('submit', async event => {
  event.preventDefault(); if (!userId) return; validateSearch(); if ($('search-button').disabled) return;
  history = appendHistory(history, $('keyword').value); saveHistory(localStorage, history, userId); closeHistory();
  controller?.abort(); const current = ++batch;
  const active = new AbortController(); controller = active;
  items = []; started = true; searchFilters.reset(); errors = []; renderErrors();
  setStatus('searching', 'bi-arrow-repeat'); $('stop').hidden = false;
  const cardmaxStock = $('cardmax-stock').checked;
  const failed = await loadResults({targets: searchTargets(checked('source'), checked('merchant')), keyword: $('keyword').value.trim(), signal: active.signal,
    fetchPage: async (target, keyword, page, signal) => {
      const response = await requestAPI(`/api/search?${searchParams(target, keyword, page, {cardmaxStock})}`, {signal});
      const data = await response.json();
      if (!response.ok) {
        const key = response.status === 504 ? 'the_source_timed_out' : response.status === 422 ? 'invalid_search_parameters' : ['SearchParseError', 'SearchPaginationError'].includes(data.detail?.type) ? 'could_not_read_the_source_results' : 'could_not_retrieve_results_from_the_source';
        const error = new Error(); error.translationKey = key; throw error;
      }
      return data;
    },
    onPage: result => {if (current === batch) {
      const found = result.items.map(item => ({...item, source: result.source}));
      items.push(...found);
      for (const item of found) rememberProduct(item);
      render();
    }},
    onError: ({game, source}, error) => {if (current === batch) {errors.push({game, source, key: error.translationKey || 'unable_to_search_this_game'}); renderErrors();}}, wait});
  if (current !== batch) return;
  controller = null; $('stop').hidden = true;
  if (active.signal.aborted) setStatus('stopped', 'bi-stop-circle'); else if (failed) setStatus('incomplete', 'bi-exclamation-triangle'); else setStatus('done', 'bi-check2');
  render();
});
$('stop').addEventListener('click', () => {controller?.abort(); setStatus('stopped', 'bi-stop-circle'); $('stop').hidden = true;});
$('keyword').addEventListener('input', validateSearch);
document.querySelectorAll('[name="source"], [name="merchant"]').forEach(el => el.addEventListener('change', validateSearch));
document.querySelectorAll('[name="merchant"]').forEach(el => el.addEventListener('change', renderCartSummary));
$('more').addEventListener('click', () => {limit += 100; render();});
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
function renderErrors() {
  $('errors').replaceChildren(...errors.map(({game, source, key}) => {
    const message = source && source !== 'yuyutei' ? t('source_error', {source: sourceLabel(source), game: gameLabel(game), message: t(key)})
      : t('game_error', {game: gameLabel(game), message: t(key)});
    const p = el('p'); p.append(icon('bi-exclamation-triangle'), message); return p;
  }));
}
// Hovering or focusing a source chip lists the games that merchant sells.
function renderSourceTips() {
  const allGames = [...document.querySelectorAll('input[name="source"]')].map(input => input.value);
  document.querySelectorAll('input[name="merchant"]').forEach(input => {
    const chip = input.closest('.chip'), supported = SEARCH_SOURCES[input.value]?.games;
    const games = allGames.filter(game => !supported || supported.includes(game));
    let tip = chip.querySelector('.tip');
    if (!tip) {
      tip = el('span', 'tip'); tip.id = `source-tip-${input.value}`; tip.setAttribute('role', 'tooltip');
      chip.append(tip); input.setAttribute('aria-describedby', tip.id);
    }
    tip.textContent = t('source_supports', {games: games.map(gameLabel).join(t('list_separator'))});
  });
}
function refreshLanguage() {
  applyLanguage(document); $('language').value = getLanguage(); renderSourceTips();
  setStatus(statusKey, statusIcon); renderErrors(); renderCurrencyStatus(); validateSearch(); render(); renderFavorites(); renderCartSummary(); renderCartNav(); cartSettings.render(); cartPage.render(); renderNotice(); hobbystationSettings.render(); cardmaxSettings.render();
}
$('language').addEventListener('change', () => {setLanguage($('language').value); refreshLanguage();});
$('detail-cancel').addEventListener('click', () => $('detail-dialog').close());
$('detail-confirm').addEventListener('click', () => $('detail-dialog').close());
$('detail-dialog').addEventListener('close', () => $('detail-confirm').removeAttribute('href'));
refreshLanguage(); setView(view);

export function clearUser() {
  ++batch; ++currencyRequest; controller?.abort(); controller = null;
  proxySettings.clear(); favoriteGroups.close(); $('favorites-bulk-remove-dialog').close(); selection.clear(); favorites.clear(); subscriptions.clear(); clearCards(); clearGraded(); cartSettings.reset(); hobbystationSettings.reset(); cardmaxSettings.reset(); hobbystationSettings.render(); cardmaxSettings.render(); hsCart.clear(); cmCart.clear(); cartPage.close(); productDetails.clear(); cartStock.clear(); cart.clear();
  userId = null; notificationEmail = null; showDetails = false; items = []; history = []; started = false; errors = [];
  displayCurrency = 'JPY'; exchangeRate = 1; currencyStatus = null;
  $('currency').value = 'JPY'; $('keyword').value = ''; $('stop').hidden = true;
  $('search').inert = true;
  $('history-items').replaceChildren(); closeHistory();
  $('detail-dialog').close(); $('image-dialog').close(); $('detail-name').textContent = ''; $('detail-url').textContent = '';
  $('detail-confirm').removeAttribute('href');
  setStatus(''); renderErrors(); renderCurrencyStatus(); searchFilters.reset(); favoriteFilters.reset(); validateSearch();
}
export function activateUser(id, request, email = null) {
  clearUser(); userId = id; requestAPI = request; notificationEmail = email;
  history = readHistory(localStorage, userId); $('search').inert = false;
  proxySettings.activate(); favorites.activate(requestAPI); subscriptions.activate(requestAPI); activateCards(requestAPI); activateGraded(requestAPI); productDetails.activate(requestAPI); cartStock.activate(requestAPI); cart.refresh();
  refreshLanguage();
  // Credentials kept in this tab across a reload are re-checked once, like the YuYu-Tei cart.
  for (const [shop, settings] of [[hobbystationClient, hobbystationSettings], [cardmaxClient, cardmaxSettings]]) {
    let saved = false;
    try { saved = shop.hasCredentials(); } catch { /* not activated */ }
    if (saved) settings.verify();
  }
}
// The account page reports the effective address after loading or changing it.
export function setNotificationEmail(email) {
  notificationEmail = email || null; renderFavorites();
}
