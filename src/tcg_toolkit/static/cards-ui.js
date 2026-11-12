import {lookupSource, lookupItems} from './card-sources.js';
import {createCards, filterCards, summarizeCards, yenPrice} from './cards.js';
import {fetchRate} from './currency.js';
import {parseQuantity} from './cart.js';
import {t, getLanguage} from './i18n.js';
import {rarityBadge, cardImage, markOrientation} from './card-view.js';
import {numberButton} from './copy-number.js';
import {setNavCount} from './nav-count.js';
import {EXPORT_FIELDS, fetchCardExport, downloadCardExport} from './cards-export.js';
import {raritiesForGame, rarityCategories, findRarity, rarityTier, rarityDescription} from './rarities.js';
import {bindGroups} from './group-ui.js';
import {priceSteps, sliderIndex, sliderBound} from './logic.js';
const $ = id => document.getElementById(id);
const fields = ['game', 'card_number', 'rarity', 'name', 'quantity', 'price', 'currency', 'notes', 'image_url'];
// List/grid choice is shared with search and favorites (app.js owns the toggle).
const currentView = () => localStorage.getItem('view') === 'grid' ? 'grid' : 'list';
let officialAPI, officialGeneration = 0, officialController;
const blankFilters = () => ({query: '', rarity: '', sort: 'original', hiddenGames: new Set(), priceMin: null, priceMax: null});
let filters = blankFilters(), pendingDelete = null;
const inventory = createCards(render);
// Multi-select for moving or deleting many cards at once, as on the favorites page. Bulk actions
// run one card at a time; finished cards are deselected and failed ones stay selected so the
// action can be retried. selectionGeneration drops a run when the account changes.
const blankSelection = () => ({active: false, ids: new Set(), running: false, done: 0, total: 0, failed: 0});
let selection = blankSelection(), selectionGeneration = 0;
const locked = () => inventory.state.busy || inventory.state.loading || selection.running;
const groups = bindGroups(inventory, {prefix: 'cards', deleteMessage: 'cards_group_delete_message', locked});
const groupName = id => inventory.state.groups.find(group => group.id === id)?.name ?? null;
let importResult = null;
function renderImport() {
  const {ready, busy, loading} = inventory.state;
  $('cards-import').disabled = !ready || busy || loading;
  $('cards-import-file').disabled = busy;
  $('cards-import-cancel').disabled = busy;
  const file = $('cards-import-file').files[0], importing = busy && $('cards-import-dialog').open;
  $('cards-import-confirm').disabled = !ready || busy || loading || !file;
  $('cards-import-confirm').querySelector('.bi').className = `bi ${importing ? 'bi-arrow-repeat spin' : 'bi-upload'}`;
  $('cards-import-confirm').querySelector('span').textContent = t(importing ? 'cards_importing' : 'cards_import');
  // The drop zone doubles as the file name display.
  $('cards-import-drop').classList.toggle('chosen', Boolean(file));
  $('cards-import-icon').className = `bi ${file ? 'bi-file-earmark-check' : 'bi-file-earmark-arrow-up'}`;
  $('cards-import-name').textContent = file ? file.name : t('cards_import_choose');
  const error = importResult?.error;
  let message = '';
  if (error?.code === 'cards_import_row') message = t('cards_import_row', {row: error.row});
  else if (error) {
    const known = ['cards_import_invalid', 'cards_import_format', 'cards_import_headers', 'cards_import_too_large'];
    message = t(known.includes(error) ? error : 'cards_import_failed');
  }
  $('cards-import-error').textContent = importing ? '' : message;
  $('cards-import-cancel').textContent = t(importResult && !error ? 'close' : 'detail_cancel');
  $('cards-import-status').textContent = !importing && importResult && !error
    ? t('cards_import_result', {imported: number(importResult.imported), skipped: number(importResult.skipped)}) : '';
}
// Export settings: format, heading language and fields are remembered; the games
// start from the page's game filter each time the dialog opens.
let exportController = null, exportError = '', exportHiddenGames = new Set(), exportGroup = null;
const EXPORT_KEY = 'cardsExport';
function loadExportSettings() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(EXPORT_KEY)) ?? {}; } catch { /* corrupt: use defaults */ }
  // Fields added after the settings were saved (not in saved.known) start out chosen.
  const known = Array.isArray(saved.known) ? saved.known : EXPORT_FIELDS.filter(field => !['group', 'price', 'currency'].includes(field));
  const chosen = Array.isArray(saved.fields)
    ? EXPORT_FIELDS.filter(field => saved.fields.includes(field) || !known.includes(field)) : [];
  return {
    format: ['xlsx', 'json'].includes(saved.format) ? saved.format : 'xlsx',
    language: ['zh-CN', 'en', 'keys'].includes(saved.language) ? saved.language : getLanguage(),
    fields: chosen.length ? chosen : [...EXPORT_FIELDS],
  };
}
let exportSettings = loadExportSettings();
const saveExportSettings = () => localStorage.setItem(EXPORT_KEY, JSON.stringify({...exportSettings, known: EXPORT_FIELDS}));
// Cards in the chosen export group (null: the whole library).
const exportRecords = () => inventory.state.records.filter(row => exportGroup === null || row.group_id === exportGroup);
// [key, label] for every game in the exported cards, sorted for display.
const exportGames = () => [...new Map(exportRecords().map(row => [row.game.toLowerCase(), row.game]))]
  .sort((a, b) => collator.compare(a[1], b[1]));
function renderExport() {
  const state = inventory.state;
  $('cards-export').disabled = !state.ready || state.loading || state.busy;
  const busy = Boolean(exportController), {format, fields: chosenFields} = exportSettings;
  const games = exportGames(), chosenGames = games.filter(([key]) => !exportHiddenGames.has(key));
  const cards = exportRecords().filter(row => !exportHiddenGames.has(row.game.toLowerCase())).length;
  $('cards-export-group-field').hidden = !state.groups.length; $('cards-export-group-field').disabled = busy;
  document.querySelectorAll('input[name="export-format"]').forEach(input => { input.checked = input.value === format; });
  document.querySelectorAll('input[name="export-language"]').forEach(input => { input.checked = input.value === exportSettings.language; });
  $('cards-export-format').closest('fieldset').disabled = busy;
  $('cards-export-language-group').disabled = busy || format === 'json';
  $('cards-export-language-hint').textContent = t(format === 'json' ? 'cards_export_language_json' : 'cards_export_language_hint');
  const gamesGroup = $('cards-export-games').closest('fieldset');
  gamesGroup.hidden = !games.length; gamesGroup.disabled = busy;
  $('cards-export-fields').closest('fieldset').disabled = busy;
  $('cards-export-games-all').textContent = t(chosenGames.length === games.length ? 'cards_export_clear_all' : 'cards_export_select_all');
  $('cards-export-fields-all').textContent = t(chosenFields.length === EXPORT_FIELDS.length ? 'cards_export_clear_all' : 'cards_export_select_all');
  $('cards-export-summary').textContent = t('cards_export_summary', {cards: number(cards), fields: number(chosenFields.length)});
  const problem = games.length && !chosenGames.length ? 'cards_export_need_game' : !chosenFields.length ? 'cards_export_need_field'
    : !cards ? 'cards_group_empty' : '';
  const message = problem || exportError;
  $('cards-export-error').textContent = !busy && message ? t(message) : '';
  const confirm = $('cards-export-confirm');
  confirm.disabled = busy || Boolean(problem) || !state.ready;
  confirm.querySelector('.bi').className = `bi ${busy ? 'bi-arrow-repeat spin' : 'bi-download'}`;
  confirm.querySelector('span').textContent = t(busy ? 'cards_exporting' : 'cards_export_download');
}
function exportChip(value, label, checked, onChange) {
  const chip = node('label', '', 'chip'), input = document.createElement('input');
  input.type = 'checkbox'; input.value = value; input.checked = checked;
  input.addEventListener('change', () => { exportError = ''; onChange(input.checked); renderExport(); });
  chip.append(input, icon('bi-check-lg'), node('span', label));
  return chip;
}
function exportGroupOptions() {
  const all = node('option', t('favorite_group_all')); all.value = '';
  $('cards-export-group').replaceChildren(all, ...inventory.state.groups.map(group => {
    const option = node('option', group.name); option.value = group.id; return option;
  }));
  $('cards-export-group').value = exportGroup ?? '';
}
function exportChips() {
  $('cards-export-games').replaceChildren(...exportGames().map(([key, label]) => exportChip(key, label, !exportHiddenGames.has(key),
    checked => { if (checked) exportHiddenGames.delete(key); else exportHiddenGames.add(key); })));
  $('cards-export-fields').replaceChildren(...EXPORT_FIELDS.map(field => exportChip(field, t(`cards_export_field_${field}`),
    exportSettings.fields.includes(field), checked => {
      const chosen = new Set(exportSettings.fields);
      if (checked) chosen.add(field); else chosen.delete(field);
      exportSettings.fields = EXPORT_FIELDS.filter(key => chosen.has(key)); saveExportSettings();
    })));
}
function openExport() {
  if ($('cards-export').disabled) return;
  exportError = ''; exportGroup = inventory.state.group;
  exportGroupOptions();
  const known = new Set(exportGames().map(([key]) => key));
  exportHiddenGames = new Set([...filters.hiddenGames].filter(key => known.has(key)));
  exportChips(); renderExport();
  $('cards-export-dialog').showModal();
  $('cards-export-confirm').focus();
}
async function runExport() {
  if ($('cards-export-confirm').disabled) return;
  const controller = new AbortController(), {format, language, fields: chosenFields} = exportSettings;
  const games = exportHiddenGames.size ? exportGames().filter(([key]) => !exportHiddenGames.has(key)).map(([, label]) => label) : null;
  exportController = controller; exportError = ''; renderExport();
  try {
    const blob = await fetchCardExport(officialAPI, {format, language, games, fields: chosenFields, group: exportGroup}, controller.signal);
    if (exportController === controller) { exportController = null; downloadCardExport(blob, format); $('cards-export-dialog').close(); }
  } catch (error) {
    if (exportController === controller && error.name !== 'AbortError') {
      exportError = error.message === 'cards_export_too_long' ? error.message : 'cards_export_failed';
    }
  } finally {
    if (exportController === controller) { exportController = null; renderExport(); }
  }
}
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
// New cards go into the group chosen here (the selected tab by default); edits keep their group.
function groupPicker() {
  const state = inventory.state, select = $('card-group');
  $('card-group-field').hidden = Boolean(state.editing) || !state.groups.length;
  const none = node('option', t('favorite_group_none')); none.value = '';
  select.replaceChildren(none, ...state.groups.map(group => { const option = node('option', group.name); option.value = group.id; return option; }));
  select.value = state.draftGroup ?? ''; select.disabled = state.busy;
}
function options(id, field) {
  const select = $(id), value = filters[field];
  const labels = new Map(inventory.visible().map(row => [row[field].toLowerCase(), row[field]]));
  const all = node('option', t('cards_all')); all.value = '';
  select.replaceChildren(all, ...[...labels.values()].sort().map(label => {const option = node('option', label); option.value = label; return option;}));
  if (value && ![...select.options].some(option => option.value === value)) {const option = node('option', value); option.value = value; select.append(option);}
  select.value = value;
}
const collator = new Intl.Collator(undefined, {numeric: true, sensitivity: 'base'});
// Prices sort by their yen value; cards without a comparable price sort last in both directions.
function sortCards(rows, sort) {
  if (sort === 'original') return rows;
  const [key, direction] = sort.split(':'), sign = direction === 'desc' ? -1 : 1;
  return [...rows].sort((a, b) => {
    if (key === 'price') {
      const x = yen(a), y = yen(b);
      return x == null || y == null ? (x == null) - (y == null) : sign * (x - y);
    }
    return sign * (key === 'quantity' ? a.quantity - b.quantity : collator.compare(a[key], b[key]));
  });
}
// Prices follow the sidebar's display currency, like shop prices: app.js reports its choice, and
// rates for the currencies cards were entered in are fetched here. rates[code] is how much of that
// currency one yen buys. Cards whose rate cannot be fetched show only their own amount and are left
// out of the price slider and price sort.
let displayCurrency = 'JPY', rates = {JPY: 1}, ratesPending = new Set(), ratesFailed = new Set(), ratesGeneration = 0;
const yen = row => yenPrice(row, rates);
export function setCardsCurrency(currency, rate) {
  displayCurrency = currency; rates[currency] = rate; ratesFailed.delete(currency);
  render();
}
function loadRates(rows) {
  for (const currency of new Set(rows.filter(row => row.price != null).map(row => row.currency))) {
    if (currency in rates || ratesPending.has(currency) || ratesFailed.has(currency)) continue;
    const generation = ratesGeneration;
    ratesPending.add(currency);
    fetchRate(currency, officialAPI).then(rate => { if (generation === ratesGeneration) rates[currency] = rate; },
      () => { if (generation === ratesGeneration) ratesFailed.add(currency); })
      .finally(() => { if (generation === ratesGeneration) { ratesPending.delete(currency); render(); } });
  }
}
// Converted amounts round like shop prices (whole yen); entered amounts keep their cents.
function money(amount, currency, converted = false) {
  const whole = currency === 'JPY' && (converted || Number.isInteger(amount));
  return new Intl.NumberFormat(getLanguage(), {style: 'currency', currency, currencyDisplay: 'code',
    minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: whole ? 0 : 2}).format(converted && whole ? Math.round(amount) : amount);
}
// The price in the display currency, with the entered amount underneath when it differs.
function priceDisplay(row) {
  const box = node('span', '', 'converted-price'), value = yen(row);
  if (row.currency === displayCurrency || value == null) box.append(node('span', money(row.price, row.currency)));
  else box.append(node('span', money(value * rates[displayCurrency], displayCurrency, true)), node('small', money(row.price, row.currency), 'original-price'));
  return box;
}
// Price range slider, as on the favorites page: thumbs step through the distinct yen values of the
// current group's prices, a thumb at either end means no limit, and labels use the display currency.
let steps = [];
const priceLabel = value => money(value * rates[displayCurrency], displayCurrency, true);
function priceSlider(rows) {
  steps = priceSteps(rows.map(row => ({price: yen(row)})).filter(row => row.price != null));
  const lo = $('cards-price-min'), hi = $('cards-price-max'), last = Math.max(steps.length - 1, 0);
  for (const input of [lo, hi]) { input.max = last; input.disabled = steps.length < 2; }
  lo.value = sliderIndex(steps, filters.priceMin, 'min');
  hi.value = sliderIndex(steps, filters.priceMax, 'max');
  const a = Number(lo.value), b = Number(hi.value), percent = index => last ? index / last * 100 : 0;
  $('cards-price-fill').style.left = `${percent(a)}%`;
  $('cards-price-fill').style.right = `${100 - (last ? percent(b) : 100)}%`;
  // Keep the min thumb reachable when both thumbs meet at the right end.
  lo.classList.toggle('on-top', a > last / 2);
  const open = filters.priceMin === null && filters.priceMax === null;
  $('cards-price-label').textContent = open ? t('any_price') : `${priceLabel(steps[a])} – ${priceLabel(steps[b])}`;
  $('cards-price-range').classList.toggle('active', !open);
}
function visibleCards(records) {
  const rows = filterCards(records, {query: filters.query, rarity: filters.rarity, min: filters.priceMin, max: filters.priceMax, priceOf: yen})
    .filter(row => !filters.hiddenGames.has(row.game.toLowerCase()));
  return sortCards(rows, filters.sort);
}
// Game logos shown after the game name in filter chips (keys match gameKey()).
const GAME_LOGOS = {ws: './game-ws-sticker.png', 'ws en': './game-ws-sticker.png', wsr: './game-wsr.webp', pm: './game-pm.webp', sev: './game-sev.png', 'sev en': './game-sev.png', lycee: './game-lycee.webp', chaostcg: './game-chaos.webp'};
// As on the favorites page, every library game always has a chip; games saved earlier
// under another name follow, so their cards can still be filtered.
function gameChips() {
  const games = new Map(GAMES.map(game => [game.toLowerCase(), game]));
  for (const row of inventory.visible()) if (!games.has(row.game.toLowerCase())) games.set(row.game.toLowerCase(), row.game);
  $('cards-game-chips').replaceChildren(...[...games].sort((a, b) => collator.compare(a[1], b[1])).map(([key, label]) => {
    const chip = node('label', '', 'chip'), input = document.createElement('input');
    input.type = 'checkbox'; input.checked = !filters.hiddenGames.has(key);
    input.addEventListener('change', () => { if (input.checked) filters.hiddenGames.delete(key); else filters.hiddenGames.add(key); render(); });
    chip.append(input, icon('bi-check-lg'), node('span', label));
    const logo = GAME_LOGOS[key];
    if (logo) { const img = document.createElement('img'); img.className = 'chip-logo'; img.src = logo; img.alt = ''; img.setAttribute('aria-hidden', 'true'); chip.append(img); }
    return chip;
  }));
}
function cardActions(row) {
  const actions = node('div', '', 'row-actions'), disabled = locked();
  if (inventory.state.groups.length) actions.append(iconButton('bi-folder-symlink', t('favorite_move'), () => openMove(row), disabled || inventory.state.groupBusy));
  actions.append(iconButton('bi-pencil', t('cards_edit'), () => openEditor(row.id), disabled),
    iconButton('bi-trash3', t('cards_delete'), () => confirmDelete(row), disabled));
  return actions;
}
function openMove(row) {
  groups.openMove(row.name, row.group_id ?? '', groupId => inventory.move(row.id, groupId),
    () => inventory.state.busy, () => inventory.state.records.some(card => card.id === row.id));
}
// Ungrouped cards get no tag.
function groupTag(row) {
  const name = groupName(row.group_id);
  if (!name) return null;
  const tag = node('span', name, 'group-tag'); tag.title = name;
  return tag;
}
// Checkbox for multi-select; only shown while selecting.
function selectBox(row) {
  const box = node('input', '', 'select-box'); box.type = 'checkbox';
  box.checked = selection.ids.has(row.id); box.disabled = selection.running;
  box.setAttribute('aria-label', t('favorite_select_card', {name: row.name}));
  box.addEventListener('click', event => event.stopPropagation());
  box.addEventListener('change', () => {
    if (selection.running) return;
    if (!selection.ids.delete(row.id)) selection.ids.add(row.id);
    render();
  });
  return box;
}
function setSelecting(active) {
  if (selection.running) return;
  selection = {...blankSelection(), active}; render();
}
// operation(id) resolves true when that card succeeded.
async function runBulk(operation) {
  const ids = [...selection.ids], current = selectionGeneration;
  if (selection.running || !ids.length) return;
  Object.assign(selection, {running: true, done: 0, total: ids.length, failed: 0}); render();
  for (const id of ids) {
    const ok = await operation(id);
    if (current !== selectionGeneration) return;
    if (ok) selection.ids.delete(id); else selection.failed++;
    selection.done++; render();
  }
  selection.running = false; render();
}
const bulkMove = groupId => runBulk(async id => !await inventory.move(id, groupId));
const bulkDelete = () => runBulk(async id => {
  await inventory.remove(id);
  return !inventory.state.records.some(row => row.id === id);
});
function renderSelection(rows) {
  const state = inventory.state;
  // Keep only cards still shown, so a bulk move never touches a card hidden by the group or filters.
  const shown = new Set(rows.map(row => row.id));
  for (const id of selection.ids) if (!shown.has(id)) selection.ids.delete(id);
  const count = selection.ids.size;
  $('cards-panel').classList.toggle('selecting', selection.active);
  $('cards-selection-bar').hidden = !selection.active;
  $('cards-select').setAttribute('aria-pressed', String(selection.active));
  $('cards-select').querySelector('span').textContent = t(selection.active ? 'favorite_select_done' : 'favorite_select');
  $('cards-select').disabled = selection.running || !state.ready;
  $('cards-select-all').checked = count > 0 && count === rows.length;
  $('cards-select-all').indeterminate = count > 0 && count < rows.length;
  $('cards-select-all').disabled = selection.running || !rows.length;
  $('cards-selection-count').textContent = selection.running ? t('favorite_bulk_progress', {done: number(selection.done), total: number(selection.total)})
    : t('favorite_selected_count', {count: number(count)}) + (selection.failed ? ' · ' + t('cards_bulk_failed', {count: number(selection.failed)}) : '');
  const idle = !selection.running && count > 0 && !state.busy && !state.loading;
  $('cards-bulk-move').disabled = !idle || !state.groups.length;
  $('cards-bulk-delete').disabled = !idle;
}
// Holdings stepper in the slot where favorites show the cart: − / typed number / +, styled like
// the cart stepper. Typing survives re-renders until Enter or leaving the box saves it; Escape
// restores the saved quantity. Errors appear in the page status line.
const quantityDrafts = new Map();
let savingQuantity = '';
async function setQuantity(row, value) {
  if (locked() || value === row.quantity) return;
  savingQuantity = row.id;
  inventory.edit(row.id); inventory.state.draft.quantity = value;
  await inventory.save();
  savingQuantity = ''; render();
}
function quantityStepper(row) {
  const box = node('div', '', 'cart-controls'), quantity = row.quantity, disabled = locked();
  const stepper = node('div', '', `cart-stepper${quantity ? ' active' : ''}`);
  stepper.setAttribute('role', 'group'); stepper.setAttribute('aria-label', t('cards_owned', {quantity: number(quantity)}));
  const step = (iconName, key, off, delta) => {
    const button = node('button', '', 'cart-step'); button.type = 'button'; button.append(icon(iconName));
    button.setAttribute('aria-label', t(key)); button.title = t(key); button.disabled = off;
    button.addEventListener('click', () => setQuantity(row, quantity + delta));
    return button;
  };
  let count;
  if (savingQuantity === row.id) { count = node('span', '', 'cart-qty'); count.append(icon('bi-arrow-repeat spin')); }
  else {
    count = document.createElement('input');
    count.className = 'cart-qty'; count.inputMode = 'numeric'; count.autocomplete = 'off';
    count.setAttribute('aria-label', t('cards_quantity_input')); count.title = t('cards_quantity_input');
    count.value = String(quantity); count.disabled = disabled;
    const draft = quantityDrafts.get(row.id);
    if (draft && !disabled) {
      count.value = draft.value;
      requestAnimationFrame(() => { if (count.isConnected) { count.focus(); count.setSelectionRange(draft.start, draft.end); } });
    }
    const remember = () => quantityDrafts.set(row.id, {value: count.value, start: count.selectionStart, end: count.selectionEnd});
    const commit = () => {
      quantityDrafts.delete(row.id);
      const value = parseQuantity(count.value);
      if (value === null || value === quantity) { count.value = String(quantity); return; }
      setQuantity(row, value);
    };
    count.addEventListener('focus', () => { if (!quantityDrafts.has(row.id)) count.select(); });
    count.addEventListener('input', remember);
    count.addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); commit(); count.blur(); }
      else if (event.key === 'Escape') { quantityDrafts.delete(row.id); count.value = String(quantity); count.blur(); }
      else queueMicrotask(remember);
    });
    // A blur caused by the list redrawing is not a commit; the new box takes over the draft.
    count.addEventListener('blur', () => { if (count.isConnected && quantityDrafts.has(row.id)) commit(); });
  }
  stepper.append(step('bi-dash-lg', 'cards_quantity_decrease', disabled || !quantity, -1), count,
    step('bi-plus-lg', 'cards_quantity_increase', disabled, 1));
  box.append(stepper);
  return box;
}
// List and grid follow the favorites layout; the holdings stepper takes the cart's place.
function cardRow(row) {
  const tr = document.createElement('tr');
  const cell = (child, className = '') => {const td = node('td', '', className); td.append(child); tr.append(td);};
  cell(selectBox(row), 'select-col'); tr.classList.toggle('selected', selection.ids.has(row.id));
  const product = node('div', '', 'product-summary');
  product.append(cardImage(row, {noReferrer: true}), node('span', row.name, 'cards-name'));
  cell(node('span', row.game, 'game-tag')); cell(rarityBadge(row)); cell(numberButton(row.card_number)); cell(product);
  cell(row.price == null ? node('span', '—', 'muted') : priceDisplay(row), 'numeric');
  cell(quantityStepper(row), 'cart-col'); cell(row.notes, 'cards-notes');
  cell(groupTag(row) ?? node('span', '—', 'muted'), 'group-col');
  cell(cardActions(row), 'action-col');
  return tr;
}
function cardTile(row) {
  const card = node('article', '', `card${row.quantity ? '' : ' out_of_stock'}`);
  const tags = node('div', '', 'card-tags'); tags.append(node('span', row.game, 'game-tag'), rarityBadge(row));
  const top = node('div', '', 'card-top'); top.append(tags, numberButton(row.card_number));
  const image = cardImage(row, {noReferrer: true});
  if (selection.active) { image.append(selectBox(row)); card.classList.toggle('selected', selection.ids.has(row.id)); }
  const bottom = node('div', '', 'card-bottom');
  bottom.append(row.price == null ? node('span', '—', 'muted') : priceDisplay(row));
  const owned = node('div', '', 'card-cart');
  owned.append(node('span', t('cards_owned_column'), 'card-cart-label'), quantityStepper(row));
  card.append(top, image, node('span', row.name, 'card-name'), bottom, owned);
  if (row.notes) card.append(node('p', row.notes, 'cards-notes'));
  // Footer as on the favorites page: card actions on the left, its group in the bottom-right corner.
  const foot = node('div', '', 'card-foot'); foot.append(cardActions(row));
  const tag = groupTag(row); if (tag) foot.append(tag);
  card.append(foot);
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
  renderExport();
  renderImport();
  indexOwned(state.records);
  // Editor dialog
  gameOptions();
  for (const field of fields) {
    const input = $('card-' + field);
    if (input.value !== String(state.draft[field])) input.value = state.draft[field];
    input.disabled = state.busy;
  }
  $('card-dialog-title').textContent = t(state.editing ? 'cards_edit_title' : 'cards_add');
  groupPicker();
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
  if (state.ready) loadRates(state.records);
  options('cards-rarity-filter', 'rarity'); gameChips(); priceSlider(inventory.visible());
  $('cards-sort').value = filters.sort;
  groups.render();
  const groupRows = inventory.visible(), group = groups.selected();
  const rows = visibleCards(groupRows), stats = summarizeCards(rows), cardsView = currentView();
  renderSelection(rows);
  $('cards-title').textContent = group ? group.name : t('cards_list');
  // Without any groups the group column would only hold dashes.
  $('cards-table').classList.toggle('without-groups', !state.groups.length);
  // Whole library, not the current filter: total copies owned.
  setNavCount($('cards-nav-count'), state.ready ? summarizeCards(state.records).quantity : 0);
  $('cards-stats').hidden = !state.ready || !groupRows.length;
  $('cards-kinds').textContent = number(stats.kinds);
  $('cards-quantity-total').textContent = number(stats.quantity);
  $('cards-by-game').replaceChildren(...chips(stats.games));
  $('cards-by-rarity').replaceChildren(...chips(stats.rarities));
  $('cards-count').hidden = !state.ready;
  $('cards-count').textContent = t('counts', {matching: number(rows.length), loaded: number(groupRows.length)});
  $('cards-filters').hidden = !state.ready || !groupRows.length;
  $('cards-rows').replaceChildren(...(cardsView === 'list' ? rows.map(cardRow) : []));
  $('cards-table').hidden = !rows.length || cardsView !== 'list';
  $('cards-grid').hidden = !rows.length || cardsView !== 'grid';
  $('cards-grid').replaceChildren(...(cardsView === 'grid' ? rows.map(cardTile) : []));
  $('cards-empty').hidden = !state.ready || rows.length > 0;
  $('cards-empty').querySelector('h3').textContent = t(groupRows.length ? 'no_matching_results' : state.records.length ? 'cards_group_empty' : 'cards_empty_all');
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
$('card-group').addEventListener('change', event => { inventory.state.draftGroup = event.target.value || null; });
$('cards-export-group').addEventListener('change', event => {
  exportGroup = event.target.value || null; exportError = '';
  const known = new Set(exportGames().map(([key]) => key));
  exportHiddenGames = new Set([...exportHiddenGames].filter(key => known.has(key)));
  exportChips(); renderExport();
});
$('cards-select').addEventListener('click', () => setSelecting(!selection.active));
$('cards-select-all').addEventListener('change', event => {
  if (selection.running) return;
  for (const row of visibleCards(inventory.visible())) event.target.checked ? selection.ids.add(row.id) : selection.ids.delete(row.id);
  render();
});
$('cards-bulk-move').addEventListener('click', () => {
  const count = selection.ids.size;
  if (count) groups.openBulkMove(count, bulkMove);
});
$('cards-bulk-delete').addEventListener('click', () => {
  const count = selection.ids.size; if (!count) return;
  $('cards-bulk-delete-message').textContent = t('cards_bulk_delete_message', {count: number(count)});
  $('cards-bulk-delete-dialog').showModal();
});
$('cards-bulk-delete-cancel').addEventListener('click', () => $('cards-bulk-delete-dialog').close());
$('cards-bulk-delete-confirm').addEventListener('click', () => { $('cards-bulk-delete-dialog').close(); bulkDelete(); });
$('cards-form').addEventListener('submit', async event => {
  event.preventDefault();
  await inventory.save();
  if (!inventory.state.error) $('card-dialog').close();
});
$('cards-add').addEventListener('click', () => openEditor());
$('cards-export').addEventListener('click', openExport);
$('cards-export-form').addEventListener('submit', event => { event.preventDefault(); runExport(); });
for (const [name, key] of [['export-format', 'format'], ['export-language', 'language']]) {
  document.querySelectorAll(`input[name="${name}"]`).forEach(input => input.addEventListener('change', () => {
    if (!input.checked) return;
    exportSettings[key] = input.value; exportError = ''; saveExportSettings(); renderExport();
  }));
}
$('cards-export-games-all').addEventListener('click', () => {
  exportHiddenGames = exportHiddenGames.size ? new Set() : new Set(exportGames().map(([key]) => key));
  exportError = ''; exportChips(); renderExport();
});
$('cards-export-fields-all').addEventListener('click', () => {
  exportSettings.fields = exportSettings.fields.length === EXPORT_FIELDS.length ? [] : [...EXPORT_FIELDS];
  exportError = ''; saveExportSettings(); exportChips(); renderExport();
});
$('cards-export-cancel').addEventListener('click', () => $('cards-export-dialog').close());
$('cards-export-close').addEventListener('click', () => $('cards-export-dialog').close());
// Closing the dialog (Esc, cancel, ×) abandons an export still in flight.
$('cards-export-dialog').addEventListener('close', () => { exportController?.abort(); exportController = null; exportError = ''; renderExport(); });
$('cards-import').addEventListener('click', () => {
  $('cards-import-form').reset(); importResult = null; renderImport(); $('cards-import-dialog').showModal();
});
$('cards-import-file').addEventListener('change', () => { importResult = null; renderImport(); });
for (const id of ['cards-import-cancel', 'cards-import-close']) $(id).addEventListener('click', () => { if (!inventory.state.busy) $('cards-import-dialog').close(); });
// Dropping a file onto the zone fills the hidden input, as if it was picked.
const importDrop = $('cards-import-drop');
for (const type of ['dragenter', 'dragover']) importDrop.addEventListener(type, event => {
  if (!event.dataTransfer?.types.includes('Files') || $('cards-import-file').disabled) return;
  event.preventDefault(); importDrop.classList.add('dragging');
});
for (const type of ['dragleave', 'drop']) importDrop.addEventListener(type, () => importDrop.classList.remove('dragging'));
importDrop.addEventListener('drop', event => {
  if (!event.dataTransfer?.files.length || $('cards-import-file').disabled) return;
  event.preventDefault();
  const files = new DataTransfer(); files.items.add(event.dataTransfer.files[0]);
  $('cards-import-file').files = files.files; importResult = null; renderImport();
});
$('cards-import-dialog').addEventListener('cancel', event => { if (inventory.state.busy) event.preventDefault(); });
$('cards-import-form').addEventListener('submit', async event => {
  event.preventDefault();
  if ($('cards-import-confirm').disabled) return;
  const file = $('cards-import-file').files[0];
  const format = file.name.toLowerCase().split('.').pop();
  if (['json', 'xlsx'].includes(format)) {
    importResult = null;
    const result = await inventory.importFile(file, format);
    if (!result || !$('cards-import-dialog').open) return;
    importResult = result;
    if (!result.error) $('cards-import-form').reset();
  } else importResult = {error: 'cards_import_format'};
  renderImport();
});
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
for (const [input, edge] of [[$('cards-price-min'), 'min'], [$('cards-price-max'), 'max']]) {
  input.addEventListener('input', () => {
    const lo = $('cards-price-min'), hi = $('cards-price-max');
    if (Number(lo.value) > Number(hi.value)) { if (edge === 'min') lo.value = hi.value; else hi.value = lo.value; }
    filters.priceMin = sliderBound(steps, Number(lo.value), 'min');
    filters.priceMax = sliderBound(steps, Number(hi.value), 'max');
    render();
  });
}
$('cards-reset').addEventListener('click', () => { filters = blankFilters(); $('cards-query').value = ''; render(); });
// app.js owns the shared list/grid choice and announces every change.
document.addEventListener('viewchange', render);
$('language').addEventListener('change', render);
export function clearCards() {
  exportController?.abort(); exportController = null; exportError = '';
  $('cards-export-dialog').close();
  $('cards-import-dialog').close(); importResult = null;
  clearOfficial();
  filters = blankFilters(); $('cards-query').value = '';
  ratesGeneration++; displayCurrency = 'JPY'; rates = {JPY: 1}; ratesPending = new Set(); ratesFailed = new Set();
  selectionGeneration++; selection = blankSelection(); groups.close(); exportGroup = null;
  $('cards-bulk-delete-dialog').close();
  quantityDrafts.clear(); savingQuantity = '';
  $('card-dialog').close(); $('cards-delete-dialog').close(); inventory.clear();
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

