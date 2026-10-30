import {lookupSource, lookupItems} from './card-sources.js';
import {createCards, filterCards, summarizeCards} from './cards.js';
import {t, getLanguage} from './i18n.js';
import {rarityBadge, cardImage, markOrientation} from './card-view.js';
import {numberButton} from './copy-number.js';
import {setNavCount} from './nav-count.js';
import {raritiesForGame, rarityCategories, findRarity, rarityTier, rarityDescription} from './rarities.js';
const $ = id => document.getElementById(id);
const fields = ['game', 'card_number', 'rarity', 'name', 'quantity', 'notes', 'image_url'];
// List/grid choice is shared with search and favorites (app.js owns the toggle).
const currentView = () => localStorage.getItem('view') === 'grid' ? 'grid' : 'list';
let officialAPI, officialGeneration = 0, officialController;
const blankFilters = () => ({query: '', rarity: '', sort: 'original', hiddenGames: new Set()});
let filters = blankFilters(), pendingDelete = null;
const inventory = createCards(render);
function node(tag, text = '', className = '') {const el = document.createElement(tag); el.textContent = text; if (className) el.className = className; return el;}
function icon(name) {return node('i', '', `bi ${name}`);}
const number = value => value.toLocaleString(getLanguage());
const editorOpen = () => $('card-dialog').open;
// Games are picked from a fixed list. A record saved earlier with another game name
// keeps it as an extra option, so editing never silently changes the stored game.
const GAMES = ['WS', 'WS EN', 'WSR', 'PM', 'SEV', 'SEV EN', 'Lycee', 'ChaosTCG'];
function gameOptions() {
  const draft = inventory.state.draft, select = $('card-game');
  const canonical = GAMES.find(game => game.toLowerCase() === draft.game.trim().toLowerCase());
  if (canonical) draft.game = canonical;
  const placeholder = node('option', t('cards_choose_game')); placeholder.value = '';
  const choices = GAMES.map(game => { const option = node('option', game); option.value = game; return option; });
  if (draft.game && !canonical) { const legacy = node('option', draft.game); legacy.value = draft.game; choices.push(legacy); }
  select.replaceChildren(placeholder, ...choices);
}
function rarityOptions() {
  const state = inventory.state, draft = state.draft;
  const entries = raritiesForGame(draft.game), mapped = entries.length > 0;
  const select = $('card-rarity'), custom = $('card-rarity-custom');
  select.hidden = !mapped; select.disabled = !mapped || state.busy; select.required = mapped;
  custom.hidden = mapped; custom.disabled = mapped || state.busy; custom.required = !mapped;
  custom.value = draft.rarity;
  const placeholder = node('option', t('cards_choose_rarity')); placeholder.value = '';
  select.replaceChildren(placeholder);
  for (const category of rarityCategories) {
    const group = node('optgroup'); group.label = category;
    for (const entry of entries.filter(entry => entry.category === category)) {
      const option = node('option', `${entry.code} · ${getLanguage() === 'zh-CN' ? entry.chinese : entry.english}`);
      option.value = entry.code; group.append(option);
    }
    if (group.children.length) select.append(group);
  }
  const match = findRarity(draft.game, draft.rarity);
  const original = state.records.find(row => row.id === state.editing);
  const legacy = original && original.game === draft.game && original.rarity === draft.rarity;
  if (mapped && !match && draft.rarity && (legacy || official.items.some(item => item.game === draft.game && item.card_number === draft.card_number && item.name === draft.name && item.rarity === draft.rarity))) {
    const option = node('option', draft.rarity); option.value = draft.rarity; select.append(option);
  } else if (mapped) draft.rarity = match?.code ?? '';
  select.value = draft.rarity;
}
function options(id, field) {
  const select = $(id), value = filters[field];
  const labels = new Map(inventory.state.records.map(row => [row[field].toLowerCase(), row[field]]));
  const all = node('option', t('cards_all')); all.value = '';
  select.replaceChildren(all, ...[...labels.values()].sort().map(label => {const option = node('option', label); option.value = label; return option;}));
  if (value && ![...select.options].some(option => option.value === value)) {const option = node('option', value); option.value = value; select.append(option);}
  select.value = value;
}
const collator = new Intl.Collator(undefined, {numeric: true, sensitivity: 'base'});
function sortCards(rows, sort) {
  if (sort === 'original') return rows;
  const [key, direction] = sort.split(':'), sign = direction === 'desc' ? -1 : 1;
  return [...rows].sort((a, b) => sign * (key === 'quantity' ? a.quantity - b.quantity : collator.compare(a[key], b[key])));
}
function visibleCards(records) {
  const rows = filterCards(records, {query: filters.query, rarity: filters.rarity})
    .filter(row => !filters.hiddenGames.has(row.game.toLowerCase()));
  return sortCards(rows, filters.sort);
}
// Game logos shown after the game name in filter chips (keys match gameKey()).
const GAME_LOGOS = {ws: './game-ws-sticker.png', 'ws en': './game-ws-sticker.png', wsr: './game-wsr.webp', pm: './game-pm.webp', sev: './game-sev.png', 'sev en': './game-sev.png', lycee: './game-lycee.webp', chaostcg: './game-chaos.webp'};
function gameChips() {
  const games = new Map(inventory.state.records.map(row => [row.game.toLowerCase(), row.game]));
  $('cards-game-chips').replaceChildren(...[...games].sort((a, b) => collator.compare(a[1], b[1])).map(([key, label]) => {
    const chip = node('label', '', 'chip'), input = document.createElement('input');
    input.type = 'checkbox'; input.checked = !filters.hiddenGames.has(key);
    input.addEventListener('change', () => { if (input.checked) filters.hiddenGames.delete(key); else filters.hiddenGames.add(key); render(); });
    chip.append(input, icon('bi-check-lg'), node('span', label));
    const logo = GAME_LOGOS[key];
    if (logo) { const img = document.createElement('img'); img.className = 'chip-logo'; img.src = logo; img.alt = ''; img.setAttribute('aria-hidden', 'true'); chip.append(img); }
    return chip;
  }));
  $('cards-game-chips').hidden = games.size < 2;
}
function cardActions(row) {
  const actions = node('div', '', 'row-actions'), locked = inventory.state.busy || inventory.state.loading;
  actions.append(iconButton('bi-pencil', t('cards_edit'), () => openEditor(row.id), locked),
    iconButton('bi-trash3', t('cards_delete'), () => confirmDelete(row), locked));
  return actions;
}
// Clicking a quantity opens a small popup that changes only the quantity.
function quantityButton(row, label, iconName = '') {
  const button = node('button', '', 'cards-qty'); button.type = 'button';
  if (iconName) button.append(icon(iconName));
  button.append(label);
  button.title = t('cards_quantity_edit'); button.setAttribute('aria-label', `${t('cards_quantity_edit')}: ${row.name} (${row.quantity})`);
  button.disabled = inventory.state.busy || inventory.state.loading;
  button.addEventListener('click', () => openQuantity(row));
  return button;
}
let quantityTarget = null;
function openQuantity(row) {
  if (inventory.state.busy) return;
  quantityTarget = row.id;
  $('quantity-card').replaceChildren(node('span', row.game, 'game-tag'), rarityBadge(row), numberButton(row.card_number));
  $('quantity-name').textContent = row.name;
  $('quantity-input').value = row.quantity; $('quantity-error').textContent = '';
  $('quantity-dialog').showModal();
  $('quantity-input').focus(); $('quantity-input').select();
}
function stepQuantity(delta) {
  const input = $('quantity-input'), value = Math.max(0, (Number.parseInt(input.value, 10) || 0) + delta);
  input.value = value; input.focus();
}
async function saveQuantity() {
  const value = Number($('quantity-input').value);
  if (!$('quantity-input').checkValidity() || !Number.isInteger(value)) { $('quantity-error').textContent = t('cards_invalid'); return; }
  const row = inventory.state.records.find(card => card.id === quantityTarget);
  if (!row) { $('quantity-dialog').close(); return; }
  if (value === row.quantity) { $('quantity-dialog').close(); return; }
  inventory.edit(row.id); inventory.state.draft.quantity = value;
  $('quantity-save').disabled = true;
  await inventory.save();
  $('quantity-save').disabled = false;
  if (inventory.state.error) { $('quantity-error').textContent = t(inventory.state.error); inventory.edit(); return; }
  $('quantity-dialog').close();
}
function cardRow(row) {
  const tr = document.createElement('tr');
  const cell = (child, className = '') => {const td = node('td', '', className); td.append(child); tr.append(td);};
  const product = node('div', '', 'product-summary');
  product.append(cardImage(row, {noReferrer: true}), node('span', row.name, 'cards-name'));
  cell(node('span', row.game, 'game-tag')); cell(rarityBadge(row)); cell(numberButton(row.card_number)); cell(product);
  cell(quantityButton(row, number(row.quantity)), `numeric${row.quantity ? '' : ' muted'}`); cell(row.notes, 'cards-notes');
  cell(cardActions(row), 'action-col');
  return tr;
}
function cardTile(row) {
  const card = node('article', '', `card${row.quantity ? '' : ' out_of_stock'}`);
  const tags = node('div', '', 'card-tags'); tags.append(node('span', row.game, 'game-tag'), rarityBadge(row));
  const top = node('div', '', 'card-top'); top.append(tags, numberButton(row.card_number));
  const quantity = quantityButton(row, t('cards_owned', {quantity: number(row.quantity)}), 'bi-collection');
  const bottom = node('div', '', 'card-bottom'); bottom.append(quantity, cardActions(row));
  card.append(top, cardImage(row, {noReferrer: true}), node('span', row.name, 'card-name'), bottom);
  if (row.notes) card.append(node('p', row.notes, 'cards-notes'));
  return card;
}
function chips(groups) {
  return groups.map(group => {
    const chip = node('li', '', 'stat-chip');
    chip.append(node('span', group.label, 'stat-chip-label'), node('span', t('cards_totals', {kinds: number(group.kinds), quantity: number(group.quantity)}), 'stat-chip-value'));
    return chip;
  });
}
function iconButton(name, label, handler, disabled) {
  const button = node('button', '', 'icon-button row-action'); button.type = 'button';
  button.append(icon(name)); button.setAttribute('aria-label', label); button.title = label;
  button.disabled = disabled; button.addEventListener('click', handler);
  return button;
}
function openEditor(id = '') {
  clearOfficial();
  inventory.edit(id);
  if (!editorOpen()) $('card-dialog').showModal();
  $('card-game').focus();
}
// Owned quantities for search and favorites. Search uses game codes (wsr, chaos),
// the inventory uses display names (WSR, ChaosTCG); both reduce to one key.
const gameKey = game => ({chaos: 'chaostcg', sev_en: 'sev en', ws_en: 'ws en'})[game.trim().toLowerCase()] ?? game.trim().toLowerCase();
const fold = value => (value ?? '').trim().toLowerCase();
let ownedExact = new Map(), ownedByNumber = new Map(), ownedSignature = '';
function indexOwned(records) {
  const signature = records.map(row => `${row.id}:${row.quantity}:${row.game}:${row.card_number}:${row.rarity}`).join('|');
  if (signature === ownedSignature) return;
  ownedSignature = signature; ownedExact = new Map(); ownedByNumber = new Map();
  for (const row of records) {
    const base = `${gameKey(row.game)}|${fold(row.card_number)}`, exact = `${base}|${fold(row.rarity)}`;
    ownedExact.set(exact, (ownedExact.get(exact) ?? 0) + row.quantity);
    ownedByNumber.set(base, (ownedByNumber.get(base) ?? 0) + row.quantity);
  }
  document.dispatchEvent(new CustomEvent('inventorychange'));
}
// Quantity of this card in My cards: by game, number and rarity, or by game and number when rarity is unknown.
export function ownedQuantity(item) {
  const base = `${gameKey(item.game)}|${fold(item.card_number)}`;
  return item.rarity ? ownedExact.get(`${base}|${fold(item.rarity)}`) ?? 0 : ownedByNumber.get(base) ?? 0;
}
function render() {
  const state = inventory.state, open = editorOpen();
  indexOwned(state.records);
  // Editor dialog
  gameOptions();
  for (const field of fields) {
    const input = $('card-' + field);
    if (input.value !== String(state.draft[field])) input.value = state.draft[field];
    input.disabled = state.busy;
  }
  $('card-dialog-title').textContent = t(state.editing ? 'cards_edit_title' : 'cards_add');
  rarityOptions();
  officialButton();
  $('cards-save').disabled = !state.ready || state.busy || state.loading;
  $('cards-save').querySelector('.bi').className = `bi ${state.busy ? 'bi-arrow-repeat spin' : 'bi-check-lg'}`;
  $('cards-cancel').disabled = state.busy;
  $('card-dialog-error').textContent = open && state.error ? t(state.error) : '';
  $('cards-existing').hidden = !open || !state.duplicate;
  // Page status: loading, or errors that happened outside the editor (load, delete).
  $('cards-status').textContent = state.loading ? t('cards_loading') : !open && state.error ? t(state.error) : '';
  $('cards-retry').hidden = state.ready || !state.error || state.loading;
  $('cards-add').disabled = !state.ready;
  options('cards-rarity-filter', 'rarity'); gameChips();
  $('cards-sort').value = filters.sort;
  const rows = visibleCards(state.records), stats = summarizeCards(rows), cardsView = currentView();
  // Whole library, not the current filter: total copies owned.
  setNavCount($('cards-nav-count'), state.ready ? summarizeCards(state.records).quantity : 0);
  $('cards-stats').hidden = !state.ready || !state.records.length;
  $('cards-kinds').textContent = number(stats.kinds);
  $('cards-quantity-total').textContent = number(stats.quantity);
  $('cards-by-game').replaceChildren(...chips(stats.games));
  $('cards-by-rarity').replaceChildren(...chips(stats.rarities));
  $('cards-count').hidden = !state.ready;
  $('cards-count').textContent = t('counts', {matching: number(rows.length), loaded: number(state.records.length)});
  $('cards-filters').hidden = !state.ready || !state.records.length;
  $('cards-rows').replaceChildren(...(cardsView === 'list' ? rows.map(cardRow) : []));
  $('cards-table').hidden = !rows.length || cardsView !== 'list';
  $('cards-grid').hidden = !rows.length || cardsView !== 'grid';
  $('cards-grid').replaceChildren(...(cardsView === 'grid' ? rows.map(cardTile) : []));
  $('cards-empty').hidden = !state.ready || rows.length > 0;
  $('cards-empty').querySelector('h3').textContent = t(state.records.length ? 'no_matching_results' : 'cards_empty_all');
}
function confirmDelete(row) {
  pendingDelete = row.id;
  $('cards-delete-message').textContent = t('cards_confirm_delete', {name: row.name});
  $('cards-delete-dialog').showModal();
}
for (const field of fields) $('card-' + field).addEventListener('input', event => {
  inventory.state.draft[field] = field === 'quantity' ? (event.target.value === '' ? '' : Number(event.target.value)) : event.target.value;
  if (field === 'game') rarityOptions();
  if (field === 'game' || field === 'card_number') {scheduleOfficial(); officialButton();}
});
$('card-rarity-custom').addEventListener('input', event => { inventory.state.draft.rarity = event.target.value; });
$('cards-form').addEventListener('submit', async event => {
  event.preventDefault();
  await inventory.save();
  if (!inventory.state.error) $('card-dialog').close();
});
$('cards-add').addEventListener('click', () => openEditor());
$('cards-cancel').addEventListener('click', () => $('card-dialog').close());
$('card-dialog-close').addEventListener('click', () => $('card-dialog').close());
// Esc closes the dialog natively; block it while a save is in flight.
$('card-dialog').addEventListener('cancel', event => { if (inventory.state.busy) event.preventDefault(); });
$('card-dialog').addEventListener('close', () => { clearOfficial(); if (!inventory.state.busy) inventory.edit(); });
$('cards-retry').addEventListener('click', () => inventory.load());
$('cards-existing').addEventListener('click', () => openEditor(inventory.state.duplicate));
$('cards-delete-cancel').addEventListener('click', () => $('cards-delete-dialog').close());
$('cards-delete-confirm').addEventListener('click', () => { $('cards-delete-dialog').close(); if (pendingDelete) inventory.remove(pendingDelete); });
$('cards-delete-dialog').addEventListener('close', () => { pendingDelete = null; });
for (const [id, field] of [['cards-query', 'query'], ['cards-rarity-filter', 'rarity'], ['cards-sort', 'sort']]) {
  $(id).addEventListener(field === 'query' ? 'input' : 'change', event => {filters[field] = event.target.value; render();});
}
$('cards-reset').addEventListener('click', () => { filters = blankFilters(); $('cards-query').value = ''; render(); });
// app.js owns the shared list/grid choice and announces every change.
document.addEventListener('viewchange', render);
$('language').addEventListener('change', render);
$('quantity-form').addEventListener('submit', event => { event.preventDefault(); saveQuantity(); });
$('quantity-decrease').addEventListener('click', () => stepQuantity(-1));
$('quantity-increase').addEventListener('click', () => stepQuantity(1));
document.querySelectorAll('#quantity-dialog [data-step]').forEach(button => button.addEventListener('click', () => stepQuantity(Number(button.dataset.step))));
$('quantity-cancel').addEventListener('click', () => $('quantity-dialog').close());
$('quantity-dialog').addEventListener('cancel', event => { if (inventory.state.busy) event.preventDefault(); });
$('quantity-dialog').addEventListener('close', () => { quantityTarget = null; });
export function clearCards() {
  clearOfficial();
  filters = blankFilters(); $('cards-query').value = '';
  $('card-dialog').close(); $('cards-delete-dialog').close(); $('quantity-dialog').close(); inventory.clear();
}
export function activateCards(api) {officialAPI = api; clearCards(); inventory.activate(api);}

// Official card lookup: typing a number searches after a pause; the first five
// matches show as large cards, and choosing one fills the rest of the form.
const OFFICIAL_LIMIT = 5, OFFICIAL_DELAY = 500;
let official = {status: 'idle', items: [], total: 0}, officialTimer = 0;
const officialGame = () => inventory.state.draft.game.trim().toLowerCase();
const supportsOfficial = () => Boolean(lookupSource(officialGame(), ''));
const isMarketplace = () => Boolean(lookupSource(officialGame(), '')?.marketplace);
function officialButton() {
  $('official-search').hidden = !supportsOfficial();
  $('official-search').disabled = inventory.state.busy || !inventory.state.draft.card_number.trim();
  renderOfficial();
}
function clearOfficial() {
  officialGeneration++; officialController?.abort(); clearTimeout(officialTimer);
  official = {status: 'idle', items: [], total: 0};
  renderOfficial();
}
function scheduleOfficial() {
  clearOfficial();
  if (supportsOfficial() && inventory.state.draft.card_number.trim()) officialTimer = setTimeout(runOfficial, OFFICIAL_DELAY);
}
async function runOfficial() {
  clearOfficial();
  const generation = officialGeneration, word = inventory.state.draft.card_number.trim();
  if (!supportsOfficial() || !word) return;
  officialController = new AbortController();
  official.status = 'loading'; renderOfficial();
  try {
    const source = lookupSource(officialGame(), word);
    const response = await officialAPI(source.url, {signal: officialController.signal});
    if (!response.ok) throw new Error('official search failed');
    const data = await response.json();
    if (generation !== officialGeneration || !editorOpen()) return;
    official = {status: 'done', items: lookupItems(source, data.items).slice(0, OFFICIAL_LIMIT), total: data.total ?? data.items.length};
  } catch (error) {
    if (generation !== officialGeneration || error.name === 'AbortError') return;
    official = {status: 'error', items: [], total: 0};
  }
  renderOfficial();
}
function chooseOfficial(item) {
  if (inventory.state.busy) return;
  Object.assign(inventory.state.draft, {game: item.game, card_number: item.card_number, name: item.name, rarity: item.rarity, image_url: item.image_url || ''});
  render();
  $('card-quantity').focus(); $('card-quantity').select();
}
function officialCard(item) {
  const draft = inventory.state.draft, chosen = draft.card_number === item.card_number && draft.name === item.name;
  const button = node('button', '', `official-card${chosen ? ' chosen' : ''}`); button.type = 'button';
  button.setAttribute('aria-pressed', String(chosen));
  button.setAttribute('aria-label', t('official_pick', {number: item.card_number, name: item.name}));
  button.disabled = inventory.state.busy;
  const frame = node('span', '', 'official-image'), fallback = node('span', t('no_image'), 'image-fallback');
  frame.append(fallback);
  if (item.image_url) {
    const image = document.createElement('img');
    image.alt = item.name; image.loading = 'lazy'; image.decoding = 'async'; image.referrerPolicy = 'no-referrer';
    image.addEventListener('load', () => { fallback.hidden = true; markOrientation(image); });
    image.addEventListener('error', () => { image.hidden = true; fallback.hidden = false; }, {once: true});
    image.src = item.image_url; frame.append(image);
  }
  if (chosen) frame.append(node('span', '', 'official-check bi bi-check-lg'));
  const meta = node('span', '', 'official-meta'), tags = node('span', '', 'official-tags');
  const rarity = node('span', item.rarity, `rarity-tag tier-${rarityTier(item.game, item.rarity)}`);
  rarity.title = rarityDescription(item.game, item.rarity, getLanguage()) ?? t('rarity_value', {rarity: item.rarity});
  tags.append(node('span', item.card_number, 'mono'), rarity);
  meta.append(tags, node('span', item.name, 'official-name'));
  button.append(frame, meta);
  button.addEventListener('click', () => chooseOfficial(item));
  return button;
}
function renderOfficial() {
  $('sev-hint').hidden = officialGame() !== 'sev';
  $('sev-en-hint').hidden = !['sev en', 'sev_en'].includes(officialGame());
  const panel = $('official-panel'), supported = supportsOfficial();
  panel.hidden = !supported;
  if (!supported) return;
  const marketplace = isMarketplace();
  const label = t(marketplace ? 'marketplace_search' : 'official_search');
  $('official-search').title = label; $('official-search').setAttribute('aria-label', label);
  $('official-source-title').textContent = t(marketplace ? 'marketplace_title' : 'official_title');
  $('official-source-icon').className = marketplace ? 'bi bi-shop' : 'bi bi-patch-check';
  const message = key => t((marketplace ? 'marketplace_' : 'official_') + key);
  const {status, items, total} = official;
  $('official-status').textContent = status === 'loading' ? message('loading')
    : status === 'error' ? message('failed')
    : status === 'done' ? (total ? (marketplace ? t('marketplace_count', {shown: items.length}) : total > items.length ? t('official_count', {shown: items.length, total}) : message('choose')) : message('empty'))
    : message('hint');
  $('official-results').replaceChildren(...(status === 'loading'
    ? Array.from({length: OFFICIAL_LIMIT}, () => node('span', '', 'official-card skeleton'))
    : items.map(officialCard)));
  $('official-results').hidden = status !== 'loading' && !items.length;
}
$('official-search').addEventListener('click', runOfficial);
$('card-card_number').addEventListener('keydown', event => {
  // Enter searches the official list instead of submitting a half-filled form.
  if (event.key === 'Enter' && supportsOfficial()) { event.preventDefault(); runOfficial(); }
});

