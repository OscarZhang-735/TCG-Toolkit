// Graded cards page: saved certificates, the queue of slow lookups shown as placeholder
// cards with a spinner and elapsed time, the add dialog with QR scanning, and details,
// where either side's image can be replaced by an uploaded or scanned photo.
import {createGradedCards, filterGraded, formatElapsed, slabImages, fullCorners, displayName} from './graded-cards.js';
import {parseScan, getDecoder, decodePhoto, startCamera, cameraSupported} from './grading-scan.js';
import {t, getLanguage} from './i18n.js';
import {cardImage, openPreview} from './card-view.js';
import {loadImage} from './avatar-crop-ui.js';
import {createCropEditor} from './graded-crop.js';
import {numberButton} from './copy-number.js';
import {setNavCount} from './nav-count.js';
import {yenPrice, priceDisplay} from './card-money.js';
import {createRateCache, formatPrice} from './currency.js';
import {bindGroups} from './group-ui.js';
const $ = id => document.getElementById(id);
// List/grid choice is shared with the other pages (app.js owns the toggle).
const currentView = () => localStorage.getItem('view') === 'grid' ? 'grid' : 'list';
const PROVIDER_KEY = 'gradedProvider';
const PROVIDER_LOGOS = {psa: './grading-psa.png', cgc: './grading-cgc.png', ars: './grading-ars.png', pcgcn: './grading-pcgcn.png'};
// A code held in front of the camera is read many times; act on it once per interval.
const SCAN_REPEAT_MS = 4000;
const STATUS_MS = 6000;
const blankFilters = () => ({query: '', sort: 'newest', hiddenProviders: new Set()});
// pendingDelete: a card id, or BULK for the selected cards.
const BULK = Symbol('bulk');
let filters = blankFilters(), pendingDelete = null, detailId = null, detailSignature = '', chipsSignature = '', englishOpen = false;
// Multi-select as in the card library: bulk delete runs one card at a time (done cards are
// deselected, failed ones stay selected); bulk re-query hands every card to the lookup queue.
const blankSelection = () => ({active: false, ids: new Set(), running: false, done: 0, total: 0, failed: 0});
let selection = blankSelection(), selectionGeneration = 0;
let ticker = 0, statusNotice = null, statusTimer = 0, dialogError = '', linkNotice = null;
// Add dialog scanning: one camera session at a time; generations drop late results.
let scanner = null, scanStarting = false, scanGeneration = 0, photoBusy = false, photoGeneration = 0;
let scanMessage = null, scanAdded = [], recentScans = new Map();
const graded = createGradedCards(render);
// Group bar and dialogs shared with the card library; nothing switches tabs mid bulk action.
const groups = bindGroups(graded, {prefix: 'graded', deleteMessage: 'graded_group_delete_message',
  locked: () => selection.running});
const groupName = id => graded.state.groups.find(group => group.id === id)?.name ?? null;
let displayCurrency = 'JPY', requestAPI;
const rateCache = createRateCache(() => render());

export function setGradedCurrency(currency, rate) {
  displayCurrency = currency;
  rateCache.set(currency, rate);
  render();
}

function node(tag, text = '', className = '') { const el = document.createElement(tag); el.textContent = text; if (className) el.className = className; return el; }
function icon(name) { const el = node('i', '', `bi ${name}`); el.setAttribute('aria-hidden', 'true'); return el; }
function iconButton(name, label, handler, disabled = false) {
  const button = node('button', '', 'icon-button row-action'); button.type = 'button';
  button.append(icon(name)); button.setAttribute('aria-label', label); button.title = label;
  button.disabled = disabled; button.addEventListener('click', handler);
  return button;
}
function textButton(name, label, handler, className = 'secondary compact') {
  const button = node('button', '', className); button.type = 'button';
  button.append(icon(name), node('span', label)); button.addEventListener('click', handler);
  return button;
}
const number = value => value.toLocaleString(getLanguage());
const readPrice = prefix => ({price: $(`${prefix}-price`).value || null, currency: $(`${prefix}-currency`).value});
function fillPrice(prefix, {price, currency = 'JPY'}) {
  $(`${prefix}-price`).value = price ?? '';
  $(`${prefix}-currency`).value = currency;
}
// Unit price in the sidebar's display currency, with the entered amount underneath.
const priceOf = row => priceDisplay(row, {currency: displayCurrency, rates: rateCache.rates, language: getLanguage()});
const providerName = id => graded.providerName(id);
// Shown name: the user's display name, else the certificate's card name.
const nameOf = row => displayName(row) || t('graded_untitled');
const labelName = row => displayName(row) || row.cert_number;
const dateText = seconds => new Date(seconds * 1000).toLocaleString(getLanguage(), {dateStyle: 'medium', timeStyle: 'short'});
const busyStatus = status => status === 'queued' || status === 'loading';
const labelOf = (provider, certNumber) => `${providerName(provider)} ${certNumber}`;

function providerTag(id) { return node('span', providerName(id), 'game-tag graded-provider'); }
function gradeBadge(grade) { return grade ? node('span', grade, 'graded-grade') : node('span', '—', 'graded-grade empty'); }

// One side of the slab; missing or broken images keep a labelled placeholder.
function slabSide(row, image, side, {compact = false} = {}) {
  const label = t(side === 'front' ? 'graded_front' : 'graded_back');
  const frame = cardImage({name: `${labelName(row)} · ${label}`, card_number: row.cert_number,
    image_url: image ? image.thumbnail_url || image.url : ''}, {noReferrer: true, large: image?.url, tilt: false,
    fallback: compact ? label : t(side === 'front' ? 'graded_no_front' : 'graded_no_back')});
  if (compact) return frame;
  const figure = node('figure', '', 'graded-side');
  figure.append(frame, node('figcaption', label));
  return figure;
}
function slabPair(row, compact = false) {
  const {front, back} = slabImages(row.images);
  const box = node('div', '', compact ? 'graded-thumbs' : 'graded-images');
  box.append(slabSide(row, front, 'front', {compact}), slabSide(row, back, 'back', {compact}));
  return box;
}

// Elapsed time plus "can take minutes"; the ticker rewrites the time and, past the
// provider's limit, swaps the hint for an "overdue" note.
function waitingText(label, startedAt, provider) {
  const text = node('div', '', 'graded-waiting-text');
  const elapsed = node('span', '', 'graded-elapsed');
  elapsed.dataset.started = String(startedAt); elapsed.dataset.limit = String(graded.timeoutSeconds(provider) * 1000);
  const hint = node('span', '', 'graded-wait-hint');
  hint.dataset.normal = t('graded_wait_hint'); hint.dataset.late = t('graded_overdue');
  text.append(node('strong', label), elapsed, hint);
  updateElapsed(elapsed, Date.now());
  return text;
}
function ring() { const el = node('span', '', 'loading-ring'); el.setAttribute('aria-hidden', 'true'); return el; }
// Re-query state on a saved card: spinner with time and a cancel button.
function waiting(label, startedAt, provider, actions = []) {
  const box = node('div', '', 'graded-waiting');
  const text = waitingText(label, startedAt, provider);
  if (actions.length) { const row = node('div', '', 'graded-actions'); row.append(...actions); text.append(row); }
  box.append(ring(), text);
  return box;
}
function queued(text, actions = []) {
  const box = node('div', '', 'graded-waiting queued');
  const body = node('div', '', 'graded-waiting-text');
  body.append(node('strong', text));
  box.append(icon('bi-hourglass-split'), body);
  if (actions.length) { const row = node('div', '', 'graded-actions'); row.append(...actions); body.append(row); }
  return box;
}
function failure(error, provider, title, actions) {
  const box = node('div', '', 'graded-error');
  const text = node('div', '', 'graded-error-text');
  text.append(node('strong', title), node('span', t(error.key, {provider: providerName(provider)})));
  if (error.code) text.append(node('span', error.code, 'graded-error-code mono'));
  const row = node('div', '', 'graded-actions'); row.append(...actions);
  text.append(row);
  box.append(icon('bi-exclamation-triangle'), text);
  return box;
}

// A lookup not saved yet is laid out like a saved card (same header, image area and footer)
// so the grid and list keep their rhythm; the image area carries the spinner.
const cancelLabel = item => t(item.status === 'loading' ? 'graded_cancel_lookup' : 'detail_cancel');
function pendingMarker(item) {
  const marker = node('span', '', 'graded-pending-marker');
  marker.append(item.status === 'loading' ? ring() : icon(item.status === 'queued' ? 'bi-hourglass-split' : 'bi-exclamation-triangle'));
  return marker;
}
function pendingImages(item, compact = false) {
  const box = node('div', '', compact ? 'graded-thumbs graded-pending-images' : 'graded-images graded-pending-images');
  for (const side of ['front', 'back']) {
    const frame = node('span', '', 'card-image');
    if (compact) { box.append(frame); continue; }
    const figure = node('figure', '', 'graded-side');
    figure.append(frame, node('figcaption', t(side === 'front' ? 'graded_front' : 'graded_back')));
    box.append(figure);
  }
  box.append(pendingMarker(item));
  return box;
}
function pendingText(item) {
  const label = t('graded_querying', {provider: providerName(item.provider), number: item.cert_number});
  if (item.status === 'loading') return waitingText(label, item.startedAt, item.provider);
  if (item.status === 'queued') {
    const ahead = graded.queuePosition('add', item.key), text = node('div', '', 'graded-waiting-text');
    text.append(node('strong', label), node('span', ahead > 0 ? t('graded_queued', {count: number(ahead)}) : t('graded_queued_next')));
    return text;
  }
  const actions = [];
  if (item.error.retryable) actions.push(textButton('bi-arrow-counterclockwise', t('favorite_retry'), () => graded.retry(item.key), 'compact'));
  actions.push(textButton('bi-pencil', t('graded_edit_number'), () => editPending(item)),
    textButton('bi-x-lg', t('graded_remove_pending'), () => graded.dismiss(item.key)));
  return failure(item.error, item.provider, t('graded_failed_title'), actions);
}
function pendingTile(item) {
  const card = node('article', '', `card graded-card graded-pending ${item.status}`);
  card.dataset.key = item.key;
  if (busyStatus(item.status)) card.setAttribute('aria-busy', 'true');
  const tags = node('div', '', 'card-tags'); tags.append(providerTag(item.provider));
  const top = node('div', '', 'card-top'); top.append(tags, node('span', item.cert_number, 'mono'));
  card.append(top, pendingImages(item), pendingText(item));
  if (busyStatus(item.status)) {
    const foot = node('div', '', 'card-foot');
    foot.append(textButton('bi-x-lg', cancelLabel(item), () => graded.dismiss(item.key)));
    card.append(foot);
  }
  return card;
}
function pendingRow(item) {
  const tr = node('tr', '', `graded-pending-row ${item.status}`);
  tr.dataset.key = item.key;
  if (busyStatus(item.status)) tr.setAttribute('aria-busy', 'true');
  const cell = (child, className = '') => { const td = node('td', '', className); if (child) td.append(child); tr.append(td); return td; };
  cell(null, 'select-col'); cell(pendingImages(item, true)); cell(providerTag(item.provider)); cell(gradeBadge(''));
  cell(pendingText(item), 'graded-title-cell'); cell(node('span', '—', 'muted'), 'numeric');
  cell(node('span', item.cert_number, 'mono'));
  cell(node('span', '—', 'graded-date')); cell(null, 'group-col');
  const actions = node('div', '', 'row-actions');
  if (busyStatus(item.status)) actions.append(iconButton('bi-x-lg', cancelLabel(item), () => graded.dismiss(item.key)));
  cell(actions, 'action-col');
  return tr;
}

// Re-query state shown on a saved card; the saved data stays visible underneath.
function refreshStatus(row) {
  const entry = graded.state.refreshing[row.id];
  if (!entry) return null;
  if (entry.status === 'loading') {
    return waiting(t('graded_requerying'), entry.startedAt, row.provider,
      [textButton('bi-x-lg', t('graded_cancel_lookup'), () => graded.dismissRefresh(row.id))]);
  }
  if (entry.status === 'queued') {
    return queued(t('graded_refresh_queued'), [textButton('bi-x-lg', t('detail_cancel'), () => graded.dismissRefresh(row.id))]);
  }
  return failure(entry.error, row.provider, t('graded_refresh_failed'), [
    ...(entry.error.retryable ? [textButton('bi-arrow-counterclockwise', t('favorite_retry'), () => graded.refresh(row.id), 'compact')] : []),
    textButton('bi-x-lg', t('graded_dismiss'), () => graded.dismissRefresh(row.id))]);
}
function sourceLink(row) {
  let url;
  try { url = new URL(row.source_url); } catch { return null; }
  if (!['http:', 'https:'].includes(url.protocol)) return null;
  const link = node('a', '', 'icon-button row-action'); link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
  const label = t('graded_open_source', {provider: providerName(row.provider)});
  link.append(icon('bi-box-arrow-up-right')); link.setAttribute('aria-label', label); link.title = label;
  return link;
}
function cardActions(row) {
  const locked = busyStatus(graded.state.refreshing[row.id]?.status) || graded.state.removing.has(row.id);
  const actions = node('div', '', 'row-actions');
  actions.append(iconButton('bi-info-circle', t('graded_details'), () => openDetail(row.id)));
  if (graded.state.groups.length) actions.append(iconButton('bi-folder-symlink', t('favorite_move'), () => openMove(row),
    locked || graded.state.moving.has(row.id) || graded.state.groupBusy));
  actions.append(iconButton('bi-arrow-clockwise', t('graded_refresh'), () => graded.refresh(row.id), locked));
  const link = sourceLink(row); if (link) actions.append(link);
  actions.append(iconButton('bi-trash3', t('cards_delete'), () => confirmDelete(row), locked));
  return actions;
}
function openMove(row) {
  groups.openMove(labelName(row), row.group_id ?? '', groupId => graded.move(row.id, groupId),
    () => graded.state.moving.has(row.id), () => graded.state.records.some(card => card.id === row.id));
}
// Ungrouped cards get no tag.
function groupTag(row) {
  const name = groupName(row.group_id);
  if (!name) return null;
  const tag = node('span', name, 'group-tag'); tag.title = name;
  return tag;
}
function titleButton(row, className) {
  const button = node('button', nameOf(row), `graded-title ${className}`); button.type = 'button';
  // A custom name keeps the certificate's card name one hover away.
  button.title = row.display_name && row.title ? `${row.title} · ${t('graded_details')}` : t('graded_details');
  button.addEventListener('click', () => openDetail(row.id));
  return button;
}
function gradedTile(row) {
  const card = node('article', '', 'card graded-card'); card.dataset.id = row.id;
  if (busyStatus(graded.state.refreshing[row.id]?.status)) card.setAttribute('aria-busy', 'true');
  const tags = node('div', '', 'card-tags'); tags.append(providerTag(row.provider), gradeBadge(row.grade));
  const top = node('div', '', 'card-top'); top.append(tags, numberButton(row.cert_number));
  const bottom = node('div', '', 'card-bottom graded-price'); bottom.append(priceOf(row));
  const images = slabPair(row);
  if (selection.active) { images.append(selectBox(row)); card.classList.toggle('selected', selection.ids.has(row.id)); }
  card.append(top, images, titleButton(row, 'card-name'),
    node('span', t('graded_refreshed_at', {time: dateText(row.refreshed_at)}), 'graded-meta'), bottom);
  const status = refreshStatus(row); if (status) card.append(status);
  // Footer as in the card library: actions on the left, the group in the bottom-right corner.
  const foot = node('div', '', 'card-foot'); foot.append(cardActions(row));
  const tag = groupTag(row); if (tag) foot.append(tag);
  card.append(foot);
  return card;
}
function gradedRow(row) {
  const tr = node('tr'); tr.dataset.id = row.id;
  if (busyStatus(graded.state.refreshing[row.id]?.status)) tr.setAttribute('aria-busy', 'true');
  const cell = (child, className = '') => { const td = node('td', '', className); td.append(child); tr.append(td); return td; };
  cell(selectBox(row), 'select-col'); tr.classList.toggle('selected', selection.ids.has(row.id));
  cell(slabPair(row, true)); cell(providerTag(row.provider)); cell(gradeBadge(row.grade));
  const title = cell(titleButton(row, 'cards-name'), 'graded-title-cell');
  const status = refreshStatus(row); if (status) title.append(status);
  cell(priceOf(row), 'numeric'); cell(numberButton(row.cert_number)); cell(node('span', dateText(row.refreshed_at), 'graded-date'));
  cell(groupTag(row) ?? node('span', '—', 'muted'), 'group-col');
  cell(cardActions(row), 'action-col');
  return tr;
}

// Checkbox for multi-select; only shown while selecting.
function selectBox(row) {
  const box = node('input', '', 'select-box'); box.type = 'checkbox';
  box.checked = selection.ids.has(row.id); box.disabled = selection.running;
  box.setAttribute('aria-label', t('favorite_select_card', {name: labelName(row)}));
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
async function bulkDelete() {
  const ids = [...selection.ids], current = selectionGeneration;
  if (selection.running || !ids.length) return;
  Object.assign(selection, {running: true, done: 0, total: ids.length, failed: 0}); render();
  for (const id of ids) {
    const error = await graded.remove(id);
    if (current !== selectionGeneration) return;
    if (!error) selection.ids.delete(id); else selection.failed++;
    selection.done++; render();
  }
  selection.running = false; render();
}
async function bulkMove(groupId) {
  const ids = [...selection.ids], current = selectionGeneration;
  if (selection.running || !ids.length) return;
  Object.assign(selection, {running: true, done: 0, total: ids.length, failed: 0}); render();
  for (const id of ids) {
    const error = await graded.move(id, groupId);
    if (current !== selectionGeneration) return;
    if (!error) selection.ids.delete(id); else selection.failed++;
    selection.done++; render();
  }
  selection.running = false; render();
}
// Cards already being looked up again are left as they are; the queue runs two at a time.
function bulkRefresh() {
  if (selection.running) return;
  let count = 0;
  for (const id of [...selection.ids]) {
    if (busyStatus(graded.state.refreshing[id]?.status) || graded.state.removing.has(id)) continue;
    graded.refresh(id); selection.ids.delete(id); count++;
  }
  if (count) {
    statusNotice = {key: 'graded_bulk_refresh_queued', count}; clearTimeout(statusTimer);
    statusTimer = setTimeout(() => { statusNotice = null; render(); }, STATUS_MS);
  }
  render();
}
function renderSelection(rows) {
  const state = graded.state;
  // Only cards still shown stay selected, so a bulk action never touches a filtered-out card.
  const shown = new Set(rows.map(row => row.id));
  for (const id of selection.ids) if (!shown.has(id)) selection.ids.delete(id);
  const count = selection.ids.size;
  $('graded-panel').classList.toggle('selecting', selection.active);
  $('graded-selection-bar').hidden = !selection.active;
  $('graded-select').setAttribute('aria-pressed', String(selection.active));
  $('graded-select').querySelector('span').textContent = t(selection.active ? 'favorite_select_done' : 'favorite_select');
  $('graded-select').disabled = selection.running || !state.ready;
  $('graded-select-all').checked = count > 0 && count === rows.length;
  $('graded-select-all').indeterminate = count > 0 && count < rows.length;
  $('graded-select-all').disabled = selection.running || !rows.length;
  $('graded-selection-count').textContent = selection.running ? t('favorite_bulk_progress', {done: number(selection.done), total: number(selection.total)})
    : t('favorite_selected_count', {count: number(count)}) + (selection.failed ? ' · ' + t('cards_bulk_failed', {count: number(selection.failed)}) : '');
  const idle = !selection.running && count > 0 && state.ready;
  $('graded-bulk-move').disabled = !idle || !state.groups.length || state.groupBusy;
  $('graded-bulk-refresh').disabled = !idle;
  $('graded-bulk-delete').disabled = !idle;
}

function providerChips() {
  const state = graded.state, counts = new Map();
  for (const row of graded.visible()) counts.set(row.provider, (counts.get(row.provider) ?? 0) + 1);
  const ids = [...new Set([...state.providers.map(item => item.id), ...counts.keys()])];
  const signature = [getLanguage(), ...ids.map(id => `${id}:${counts.get(id) ?? 0}:${filters.hiddenProviders.has(id)}`)].join('|');
  if (signature === chipsSignature) return;
  chipsSignature = signature;
  $('graded-provider-chips').replaceChildren(...ids.map(id => {
    const chip = node('label', '', 'chip'), input = node('input');
    input.type = 'checkbox'; input.checked = !filters.hiddenProviders.has(id);
    input.addEventListener('change', () => {
      input.checked ? filters.hiddenProviders.delete(id) : filters.hiddenProviders.add(id);
      render();
    });
    chip.append(input, icon('bi-check-lg'), node('span', providerName(id)), node('span', number(counts.get(id) ?? 0), 'chip-count'));
    // Same logo chips as the card library's game filter; a provider without a logo keeps a plain chip.
    if (PROVIDER_LOGOS[id]) {
      const logo = document.createElement('img'); logo.className = 'chip-logo'; logo.src = PROVIDER_LOGOS[id];
      logo.alt = ''; logo.setAttribute('aria-hidden', 'true'); chip.append(logo);
    }
    return chip;
  }));
}

function statChips(rows, labelFor) {
  const counts = new Map();
  for (const row of rows) {
    const label = labelFor(row);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return Array.from(counts, ([label, count]) => {
    const chip = node('li', '', 'stat-chip');
    chip.append(node('span', label, 'stat-chip-label'), node('span', number(count), 'stat-chip-value'));
    return chip;
  });
}

function renderPriceTotal(rows = filterGraded(graded.state.records, filters)) {
  let total = 0, counted = 0;
  for (const row of rows) {
    const value = yenPrice(row, rateCache.rates);
    if (value != null) { total += value; counted++; }
  }
  $('graded-price-total').textContent = counted
    ? formatPrice(total, displayCurrency, rateCache.rates[displayCurrency], getLanguage()) : `${displayCurrency} -`;
  const note = $('graded-price-counted');
  note.textContent = t('graded_price_counted', {counted: number(counted), total: number(rows.length)});
  note.title = t('graded_price_counted_hint');
}

function render() {
  const state = graded.state, view = currentView();
  setNavCount($('graded-nav-count'), state.ready ? state.records.length : 0);
  announce();
  $('graded-add').disabled = !state.ready;
  $('graded-retry').hidden = state.ready || !state.error || state.loading;
  $('graded-status').textContent = state.loading ? t('graded_loading') : state.error ? t(state.error)
    : statusNotice ? t(statusNotice.key, {provider: providerName(statusNotice.provider), number: statusNotice.cert_number,
      count: number(statusNotice.count ?? 0)}) : '';
  groups.render();
  const groupRows = graded.visible(), group = groups.selected();
  const rows = filterGraded(groupRows, filters), pending = state.pending;
  renderSelection(rows);
  $('graded-list-title').textContent = group ? group.name : t('graded_list');
  // Without any groups the group column would only hold dashes.
  $('graded-table').classList.toggle('without-groups', !state.groups.length);
  if (state.ready) rateCache.load(state.records, requestAPI);
  $('graded-stats').hidden = !state.ready;
  $('graded-quantity-total').textContent = number(rows.length);
  renderPriceTotal(rows);
  $('graded-by-grade').replaceChildren(...statChips(rows, row => row.grade || t('unknown')));
  $('graded-by-provider').replaceChildren(...statChips(rows, row => providerName(row.provider)));
  $('graded-count').hidden = !state.ready;
  $('graded-count').textContent = t('counts', {matching: number(rows.length), loaded: number(groupRows.length)});
  $('graded-filters').hidden = !state.ready;
  providerChips();
  $('graded-sort').value = filters.sort;
  // Placeholders for lookups in progress come first and ignore the filters.
  $('graded-rows').replaceChildren(...(view === 'list' ? [...pending.map(pendingRow), ...rows.map(gradedRow)] : []));
  $('graded-grid').replaceChildren(...(view === 'grid' ? [...pending.map(pendingTile), ...rows.map(gradedTile)] : []));
  const shown = pending.length + rows.length > 0;
  $('graded-table').hidden = !shown || view !== 'list';
  $('graded-grid').hidden = !shown || view !== 'grid';
  $('graded-empty').hidden = !state.ready || shown;
  $('graded-empty').querySelector('h3').textContent = t(groupRows.length ? 'no_matching_results'
    : state.records.length ? 'favorite_group_empty' : 'graded_empty');
  renderDialog(); renderDetail(); syncTicker(); flash();
}

// Lookup results are announced once through the page status, which stays for a while.
function announce() {
  const notice = graded.state.notice;
  if (!notice) return;
  graded.state.notice = null;
  statusNotice = notice;
  clearTimeout(statusTimer);
  statusTimer = setTimeout(() => { statusNotice = null; render(); }, STATUS_MS);
}
// Scrolls to and briefly marks a card (a duplicate add points at the saved one).
function flash() {
  const id = graded.state.highlight;
  if (!id) return;
  if (!filterGraded(graded.visible(), filters).some(row => row.id === id)) {
    if (!graded.state.records.some(row => row.id === id)) { graded.state.highlight = ''; return; }
    filters = blankFilters(); $('graded-query').value = ''; chipsSignature = '';
    if (!graded.visible().some(row => row.id === id)) { graded.select(null); return; }
    render();
    return;
  }
  graded.state.highlight = '';
  requestAnimationFrame(() => {
    const target = [...document.querySelectorAll('#graded-panel [data-id]')].find(el => el.dataset.id === id);
    if (!target) return;
    target.scrollIntoView({block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
    target.classList.remove('graded-flash'); void target.offsetWidth; target.classList.add('graded-flash');
    setTimeout(() => target.classList.remove('graded-flash'), 2400);
  });
}

function updateElapsed(element, now) {
  const elapsed = now - Number(element.dataset.started);
  element.textContent = t('graded_elapsed', {time: formatElapsed(elapsed)});
  const late = elapsed > Number(element.dataset.limit), box = element.parentElement;
  box?.classList.toggle('overdue', late);
  const hint = box?.querySelector('.graded-wait-hint');
  if (hint) hint.textContent = late ? hint.dataset.late : hint.dataset.normal;
}
// One timer while anything is in flight; it only rewrites the elapsed-time text.
function syncTicker() {
  if (graded.busy() && !ticker) ticker = setInterval(() => {
    const now = Date.now();
    document.querySelectorAll('#graded-panel [data-started], #graded-detail-dialog [data-started]').forEach(el => updateElapsed(el, now));
  }, 1000);
  else if (!graded.busy() && ticker) { clearInterval(ticker); ticker = 0; }
}

// Add dialog
function providerOptions() {
  const select = $('graded-provider'), providers = graded.state.providers, value = select.value;
  const signature = providers.map(item => `${item.id}:${item.name}`).join('|');
  if (select.dataset.signature !== signature) {
    select.dataset.signature = signature;
    select.replaceChildren(...providers.map(item => { const option = node('option', item.name); option.value = item.id; return option; }));
    if (value) select.value = value;
  }
  if (!providers.some(item => item.id === select.value)) {
    const saved = localStorage.getItem(PROVIDER_KEY);
    select.value = providers.some(item => item.id === saved) ? saved : providers[0]?.id ?? '';
  }
}
function renderDialog() {
  providerOptions();
  const camera = cameraSupported(), scanning = Boolean(scanner) || scanStarting;
  $('graded-submit').disabled = !graded.state.ready;
  $('graded-scan').disabled = !camera || scanning;
  $('graded-scan-unavailable').hidden = camera;
  $('graded-photo-button').disabled = photoBusy;
  $('graded-photo-button').querySelector('.bi').className = `bi ${photoBusy ? 'bi-arrow-repeat spin' : 'bi-image'}`;
  $('graded-scanner').hidden = !scanning;
  const message = scanMessage ?? linkNotice;
  $('graded-scan-status').textContent = message ? t(message.key, message.params) : '';
  $('graded-scan-status').className = `graded-scan-status${message?.tone ? ` ${message.tone}` : ''}`;
  $('graded-scan-added').replaceChildren(...scanAdded.slice(0, 6).map(entry => {
    const item = node('li', '', entry.status); item.append(icon(entry.status === 'queued' ? 'bi-check2-circle' : 'bi-info-circle'), node('span', entry.label));
    return item;
  }));
  $('graded-scan-added').hidden = !scanAdded.length;
  $('graded-dialog-error').textContent = dialogError ? t(dialogError) : '';
  $('graded-cancel').textContent = t(scanAdded.length ? 'graded_done' : 'detail_cancel');
}
function groupOptions(value) {
  const groupsList = graded.state.groups, select = $('graded-add-group');
  $('graded-add-group-field').hidden = !groupsList.length;
  const none = node('option', t('favorite_group_none')); none.value = '';
  select.replaceChildren(none, ...groupsList.map(group => { const option = node('option', group.name); option.value = group.id; return option; }));
  select.value = groupsList.some(group => group.id === value) ? value : '';
}
function openAdd({provider = '', cert_number = '', price = null, currency = 'JPY', group_id} = {}) {
  if (!graded.state.ready) return;
  dialogError = ''; scanMessage = null; linkNotice = null; scanAdded = []; recentScans = new Map();
  providerOptions();
  if (provider && graded.state.providers.some(item => item.id === provider)) $('graded-provider').value = provider;
  $('graded-cert').value = cert_number;
  fillPrice('graded-add', {price, currency});
  groupOptions(group_id === undefined ? graded.state.group : group_id);
  renderDialog();
  if (!$('graded-dialog').open) $('graded-dialog').showModal();
  $('graded-cert').focus();
}
function editPending(item) {
  graded.dismiss(item.key);
  openAdd(item);
}
// Queues a lookup; returns the add() result so callers can explain duplicates.
function queueLookup(provider, certNumber) {
  localStorage.setItem(PROVIDER_KEY, provider);
  return graded.add({provider, cert_number: certNumber, ...readPrice('graded-add'), group_id: $('graded-add-group').value || null});
}
function stopScan() {
  scanGeneration++; scanStarting = false;
  scanner?.stop(); scanner = null;
}
async function startScan() {
  if (scanner || scanStarting) return;
  const session = ++scanGeneration;
  scanStarting = true; scanMessage = {key: 'graded_scan_starting'}; renderDialog();
  try {
    const decoder = await getDecoder();
    if (session !== scanGeneration) return;
    const camera = await startCamera($('graded-video'), {decoder, onText: text => { if (session === scanGeneration) handleScan(text, true); }});
    if (session !== scanGeneration) { camera.stop(); return; }
    scanner = camera; scanMessage = {key: 'graded_scan_aim'};
  } catch (error) {
    if (session === scanGeneration) scanMessage = {key: error.key || 'graded_scan_failed', tone: 'error'};
  } finally {
    if (session === scanGeneration) { scanStarting = false; renderDialog(); }
  }
}
// A certificate link closes the dialog once queued; bare digits fill
// the number field so the provider can be confirmed first.
function handleScan(text, fromCamera) {
  const value = String(text).trim(), now = Date.now();
  if (now - (recentScans.get(value) ?? -Infinity) < SCAN_REPEAT_MS) return;
  recentScans.set(value, now);
  const parsed = parseScan(value, graded.state.providers);
  linkNotice = null;
  if (parsed?.provider) {
    if (!$('graded-add-price').reportValidity()) return;
    const result = queueLookup(parsed.provider, parsed.cert_number), label = labelOf(parsed.provider, parsed.cert_number);
    if (result.status === 'queued') {
      stopScan();
      $('graded-dialog').close();
      return;
    }
    scanAdded.unshift({label, status: result.status});
    scanMessage = {key: {queued: 'graded_scan_added', exists: 'graded_scan_exists'}[result.status] ?? 'graded_scan_pending',
      params: {label}, tone: result.status === 'queued' ? 'success' : ''};
  } else if (parsed) {
    $('graded-cert').value = parsed.cert_number;
    scanMessage = {key: 'graded_scan_digits', params: {number: parsed.cert_number}};
    if (fromCamera) stopScan();
  } else {
    scanMessage = {key: 'graded_scan_unknown', params: {text: value.length > 120 ? value.slice(0, 117) + '…' : value}, tone: 'error'};
  }
  renderDialog();
}
async function readPhoto(file) {
  const session = ++photoGeneration;
  photoBusy = true; scanMessage = {key: 'graded_photo_reading'}; renderDialog();
  try {
    const decoder = await getDecoder();
    const text = await decodePhoto(file, {decoder});
    if (session !== photoGeneration) return;
    if (text) { recentScans.delete(text.trim()); handleScan(text, false); }
    else scanMessage = {key: 'graded_photo_none', tone: 'error'};
  } catch (error) {
    if (session === photoGeneration) scanMessage = {key: error.key || 'graded_scan_failed', tone: 'error'};
  } finally {
    if (session === photoGeneration) { photoBusy = false; renderDialog(); }
  }
}

// Detail dialog: rebuilt only when the card, its re-query state or the language changes.
function fieldList(entries) {
  const list = node('dl', '', 'graded-fields');
  for (const [label, value] of entries) {
    const item = node('div'); item.append(node('dt', label), node('dd', value));
    list.append(item);
  }
  return list;
}
const POPULATION_LABELS = {at_grade: 'graded_pop_at_grade', higher: 'graded_pop_higher', total_grading: 'graded_pop_total_grading',
  total_submission: 'graded_pop_total_submission', remarks: 'graded_pop_remarks', grades: 'graded_pop_grades'};
const populationLabel = key => POPULATION_LABELS[key] ? t(POPULATION_LABELS[key]) : key;
function populationSection(population) {
  const entries = Object.entries(population ?? {});
  if (!entries.length) return null;
  const section = node('section', '', 'graded-section');
  section.append(node('h3', t('graded_population')));
  const scalars = entries.filter(([, value]) => value === null || typeof value !== 'object');
  if (scalars.length) section.append(fieldList(scalars.map(([key, value]) => [populationLabel(key),
    value === null ? t('graded_pop_unknown') : typeof value === 'number' ? number(value) : String(value)])));
  for (const [key, value] of entries) {
    if (value === null || typeof value !== 'object' || Array.isArray(value)) continue;
    // Counts per grade (ARS): one small table.
    const table = node('table', '', 'graded-pop-table'), head = node('tr'), body = node('tbody');
    head.append(node('th', t('graded_grade')), node('th', t('graded_pop_count')));
    for (const [grade, count] of Object.entries(value)) {
      const row = node('tr'); row.append(node('td', grade), node('td', String(count))); body.append(row);
    }
    const thead = node('thead'); thead.append(head); table.append(thead, body);
    section.append(node('h4', populationLabel(key)), table);
  }
  return section;
}
// Detail images: each side is a button opening its options (view, upload, scan, remove).
const SIDES_UI = ['front', 'back'];
const sideLabel = side => t(side === 'front' ? 'graded_front' : 'graded_back');
let imageMenu = null;
function detailGallery(row) {
  const {front, back} = slabImages(row.images);
  const box = node('div', '', 'graded-images graded-detail-images');
  box.append(detailSide(row, 'front', front), detailSide(row, 'back', back));
  return box;
}
function detailSide(row, side, image) {
  const label = sideLabel(side), busy = graded.state.imageBusy.has(`${row.id}:${side}`);
  const figure = node('figure', '', 'graded-side');
  const button = node('button', '', 'card-image graded-image-button'); button.type = 'button';
  button.setAttribute('aria-haspopup', 'menu'); button.setAttribute('aria-expanded', 'false');
  button.setAttribute('aria-label', t('graded_image_options', {side: label})); button.title = t('graded_image_options', {side: label});
  // An empty side invites an upload (the menu opens on click).
  const placeholder = node('span', t(side === 'front' ? 'graded_detail_upload_front' : 'graded_detail_upload_back'), 'image-fallback');
  button.append(placeholder);
  if (image) {
    const picture = document.createElement('img');
    picture.alt = `${labelName(row)} · ${label}`; picture.decoding = 'async'; picture.referrerPolicy = 'no-referrer';
    picture.addEventListener('load', () => { placeholder.hidden = true; });
    picture.addEventListener('error', () => { picture.hidden = true; placeholder.hidden = false; }, {once: true});
    picture.src = image.url;
    button.append(picture);
  }
  const badge = node('span', '', 'graded-image-edit'); badge.append(icon(busy ? 'bi-arrow-repeat spin' : 'bi-camera'));
  button.append(badge);
  button.disabled = busy;
  button.addEventListener('click', () => toggleImageMenu(figure, button, row, side, image));
  const caption = node('figcaption', label);
  // Where the shown image comes from: the user's photo or the grading company's.
  if (image) caption.append(node('span', t(image.uploaded ? 'graded_image_uploaded' : 'graded_image_official'),
    `graded-source-tag ${image.uploaded ? 'uploaded' : 'official'}`));
  figure.append(button, caption);
  return figure;
}
function closeImageMenu() {
  if (!imageMenu) return;
  imageMenu.menu.remove(); imageMenu.button.setAttribute('aria-expanded', 'false');
  imageMenu = null;
}
function toggleImageMenu(figure, button, row, side, image) {
  const same = imageMenu?.button === button;
  closeImageMenu();
  if (same) return;
  const menu = node('div', '', 'graded-image-menu'); menu.setAttribute('role', 'menu');
  const item = (iconName, label, handler) => {
    const entry = node('button', '', 'graded-image-menu-item'); entry.type = 'button'; entry.setAttribute('role', 'menuitem');
    entry.append(icon(iconName), node('span', label));
    entry.addEventListener('click', () => { closeImageMenu(); button.focus(); handler(); });
    return entry;
  };
  if (image) {
    menu.append(item('bi-zoom-in', t('graded_image_view'), () => openPreview(
      {name: `${labelName(row)} · ${sideLabel(side)}`, card_number: row.cert_number}, image.thumbnail_url || image.url, image.url)));
  }
  menu.append(item('bi-upload', t('graded_image_upload'), () => pickImage(row.id, side, 'upload')),
    item('bi-camera', t('graded_image_scan'), () => pickImage(row.id, side, 'scan')));
  if (image?.uploaded) menu.append(item('bi-arrow-counterclockwise', t('graded_image_restore'), () => restoreImage(row.id, side)));
  menu.addEventListener('keydown', event => {
    const items = [...menu.querySelectorAll('button')], index = items.indexOf(document.activeElement);
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeImageMenu(); button.focus(); }
    else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      items[(index + (event.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus();
    }
  });
  figure.append(menu);
  button.setAttribute('aria-expanded', 'true');
  imageMenu = {menu, button};
  menu.querySelector('button').focus();
}
async function restoreImage(id, side) {
  $('graded-detail-image-error').textContent = '';
  const error = await graded.restoreImage(id, side);
  if (error) $('graded-detail-image-error').textContent = t(error);
}

// Photo -> crop dialog. Photos are shrunk in the browser before upload; the server
// detects the outline and later crops the same pixels, so corners need no rescaling.
const MAX_UPLOAD_SIDE = 2400;
let imageTarget = null, crop = null, cropGeneration = 0;
const cropEditor = createCropEditor($('graded-crop-overlay'));
function pickImage(id, side, mode) {
  imageTarget = {id, side, mode};
  $('graded-detail-image-error').textContent = '';
  $(mode === 'scan' ? 'graded-image-camera' : 'graded-image-file').click();
}
async function prepareImage(file) {
  let image;
  try { image = await loadImage(file); } catch { throw Object.assign(new Error('unreadable'), {key: 'graded_image_invalid'}); }
  const scale = Math.min(1, MAX_UPLOAD_SIDE / Math.max(image.naturalWidth, image.naturalHeight));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale)); canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
  canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.92));
  if (!blob) throw Object.assign(new Error('encode'), {key: 'graded_image_invalid'});
  return {blob, width: canvas.width, height: canvas.height};
}
async function openCrop(file) {
  const target = imageTarget, session = ++cropGeneration;
  if (!target) return;
  // original / calibrated: both versions stay in memory so calibration can be undone at once.
  crop = {...target, blob: null, url: '', width: 0, height: 0, status: 'preparing', hint: '', error: '',
    original: null, calibrated: null, calibratedOn: false};
  renderCrop();
  if (!$('graded-crop-dialog').open) $('graded-crop-dialog').showModal();
  try {
    const prepared = await prepareImage(file);
    if (session !== cropGeneration) return;
    Object.assign(crop, prepared, {url: URL.createObjectURL(prepared.blob)});
    crop.original = {blob: crop.blob, url: crop.url};
    cropEditor.set(prepared.width, prepared.height, fullCorners(prepared.width, prepared.height));
    await detectOutline(session);
  } catch (error) {
    if (session !== cropGeneration) return;
    Object.assign(crop, {status: 'failed', error: error.key || 'graded_image_invalid'});
    renderCrop();
  }
}
async function detectOutline(session = cropGeneration) {
  crop.status = 'detecting'; crop.error = ''; renderCrop();
  try {
    const result = await graded.detectCard(crop.blob);
    if (session !== cropGeneration) return;
    const scale = result.width ? crop.width / result.width : 1;
    if (result.corners) cropEditor.set(crop.width, crop.height, result.corners.map(([x, y]) => [x * scale, y * scale]));
    crop.hint = result.corners ? 'graded_crop_detected' : 'graded_crop_not_found';
  } catch (error) {
    if (session !== cropGeneration) return;
    crop.error = error.key || 'graded_image_failed'; crop.hint = 'graded_crop_manual';
  }
  crop.status = 'ready'; renderCrop();
}
// Brightness calibration: the server returns a same-size JPEG, so the corners still fit.
async function toggleCalibration() {
  if (!crop?.original || crop.status !== 'ready') return;
  if (crop.calibratedOn) { useVersion('original'); return; }
  const session = cropGeneration;
  if (!crop.calibrated) {
    crop.status = 'calibrating'; crop.error = ''; renderCrop();
    try {
      const blob = await graded.calibrateImage(crop.original.blob);
      if (session !== cropGeneration) return;
      crop.calibrated = {blob, url: URL.createObjectURL(blob)};
    } catch (error) {
      if (session !== cropGeneration) return;
      crop.status = 'ready'; crop.error = error.key || 'graded_image_failed'; renderCrop();
      return;
    }
  }
  useVersion('calibrated');
}
function useVersion(kind) {
  Object.assign(crop, {blob: crop[kind].blob, url: crop[kind].url, calibratedOn: kind === 'calibrated', status: 'ready'});
  renderCrop();
}
function renderCrop() {
  if (!crop) return;
  const working = ['preparing', 'detecting', 'saving', 'calibrating'].includes(crop.status);
  $('graded-crop-title').textContent = t(crop.side === 'front' ? 'graded_crop_title_front' : 'graded_crop_title_back');
  $('graded-crop-hint').textContent = t(crop.status === 'preparing' || crop.status === 'detecting' ? 'graded_crop_detecting'
    : crop.status === 'saving' ? 'graded_crop_saving' : crop.status === 'calibrating' ? 'graded_crop_calibrating'
      : crop.calibratedOn ? 'graded_crop_calibrated' : crop.hint || 'graded_crop_manual');
  $('graded-crop-error').textContent = crop.error ? t(crop.error) : '';
  const image = $('graded-crop-image');
  if (crop.url && image.getAttribute('src') !== crop.url) image.src = crop.url;
  $('graded-crop-stage').hidden = !crop.url;
  $('graded-crop-busy').hidden = !working;
  for (const id of ['graded-crop-detect', 'graded-crop-full', 'graded-crop-save', 'graded-crop-calibrate']) $(id).disabled = working || !crop.blob;
  const calibrate = $('graded-crop-calibrate');
  calibrate.setAttribute('aria-pressed', String(crop.calibratedOn));
  calibrate.querySelector('.bi').className = `bi ${crop.status === 'calibrating' ? 'bi-arrow-repeat spin'
    : crop.calibratedOn ? 'bi-arrow-counterclockwise' : 'bi-brightness-high'}`;
  calibrate.querySelector('span').textContent = t(crop.calibratedOn ? 'graded_crop_uncalibrate' : 'graded_crop_calibrate');
  $('graded-crop-save').querySelector('.bi').className = `bi ${crop.status === 'saving' ? 'bi-arrow-repeat spin' : 'bi-check-lg'}`;
  cropEditor.setLabels(['graded_corner_tl', 'graded_corner_tr', 'graded_corner_br', 'graded_corner_bl'].map(key => t(key)));
  requestAnimationFrame(() => cropEditor.redraw());
}
async function saveCrop() {
  if (!crop?.blob || crop.status !== 'ready') return;
  const session = cropGeneration, {id, side, blob} = crop;
  crop.status = 'saving'; crop.error = ''; renderCrop();
  const error = await graded.uploadImage(id, side, blob, cropEditor.corners());
  if (session !== cropGeneration) return;
  if (error) { crop.status = 'ready'; crop.error = error; renderCrop(); return; }
  $('graded-crop-dialog').close();
}
function closeCrop() {
  cropGeneration++;
  for (const version of [crop?.original, crop?.calibrated]) if (version?.url) URL.revokeObjectURL(version.url);
  crop = null; imageTarget = null;
  $('graded-crop-image').removeAttribute('src');
}

function openDetail(id) {
  detailId = id; detailSignature = ''; englishOpen = false;
  renderDetail(true);
}
function renderDetail(opening = false) {
  const dialog = $('graded-detail-dialog');
  if (!opening && !dialog.open) return;
  const row = graded.state.records.find(card => card.id === detailId);
  if (!row) { if (dialog.open) dialog.close(); return; }
  if (opening) {
    fillPrice('graded-detail', row);
    $('graded-detail-name').value = row.display_name ?? '';
    $('graded-detail-name').placeholder = row.title || t('graded_display_name_placeholder');
    $('graded-price-status').textContent = ''; $('graded-name-status').textContent = '';
  }
  const priceBusy = graded.state.priceBusy.has(row.id), nameBusy = graded.state.nameBusy.has(row.id);
  for (const id of ['graded-detail-price', 'graded-detail-currency', 'graded-price-save']) $(id).disabled = priceBusy;
  for (const id of ['graded-detail-name', 'graded-name-save']) $(id).disabled = nameBusy;
  const entry = graded.state.refreshing[row.id];
  const locked = busyStatus(entry?.status) || graded.state.removing.has(row.id);
  $('graded-detail-refresh').disabled = locked;
  $('graded-detail-delete').disabled = locked;
  const busyImages = SIDES_UI.map(side => graded.state.imageBusy.has(`${row.id}:${side}`)).join(',');
  const signature = [row.id, row.refreshed_at, entry?.status ?? '', entry?.startedAt ?? '', getLanguage(),
    ...row.images.map(image => image.url), busyImages, groupName(row.group_id) ?? '', row.display_name ?? ''].join('|');
  if (signature !== detailSignature) {
    detailSignature = signature;
    $('graded-detail-title').textContent = nameOf(row);
    const info = node('div', '', 'graded-detail-info');
    const tags = node('div', '', 'card-tags'); tags.append(providerTag(row.provider), gradeBadge(row.grade));
    const tag = groupTag(row); if (tag) tags.append(tag);
    const cert = node('div', '', 'graded-detail-cert');
    cert.append(node('span', t('graded_cert_number'), 'graded-detail-label'), numberButton(row.cert_number));
    // Under a custom name, the certificate's own card name stays visible.
    if (row.display_name && row.title) info.append(node('p', t('graded_original_name', {name: row.title}), 'graded-original-name'));
    info.append(tags, cert, node('p', t('graded_refreshed_at', {time: dateText(row.refreshed_at)}), 'graded-meta'));
    const status = refreshStatus(row); if (status) info.append(status);
    const fields = Object.entries(row.fields ?? {}), english = Object.entries(row.english_fields ?? {});
    if (fields.length || english.length) {
      const section = node('section', '', 'graded-section');
      section.append(node('h3', t('graded_fields')));
      if (fields.length) section.append(fieldList(fields));
      if (english.length) {
        // Translations of the fields above (ARS): folded until asked for.
        const more = node('details', '', 'graded-english'), summary = node('summary', t('graded_english_fields'));
        more.open = englishOpen;
        more.addEventListener('toggle', () => { englishOpen = more.open; });
        more.append(summary, fieldList(english));
        section.append(more);
      }
      info.append(section);
    }
    const population = populationSection(row.population); if (population) info.append(population);
    closeImageMenu();
    // The editors are one element moved under each rebuilt gallery, so a draft survives.
    const side = node('div', '', 'graded-detail-side');
    side.append(detailGallery(row), $('graded-detail-edit'));
    $('graded-detail-body').replaceChildren(side, info);
    const link = sourceLink(row);
    $('graded-detail-source').hidden = !link;
    if (link) $('graded-detail-source').href = link.href;
    $('graded-detail-source').querySelector('span').textContent = t('graded_open_source', {provider: providerName(row.provider)});
  }
  if (opening && !dialog.open) dialog.showModal();
}

function confirmDelete(row) {
  pendingDelete = row.id;
  $('graded-delete-title').textContent = t('graded_delete_title');
  $('graded-delete-message').textContent = t('graded_confirm_delete',
    {provider: providerName(row.provider), number: row.cert_number, name: nameOf(row)});
  $('graded-delete-error').textContent = '';
  $('graded-delete-confirm').disabled = false;
  $('graded-delete-dialog').showModal();
}

$('graded-add').addEventListener('click', () => openAdd());
$('graded-retry').addEventListener('click', () => graded.load());
$('graded-form').addEventListener('submit', event => {
  event.preventDefault();
  const parsed = parseScan($('graded-cert').value, graded.state.providers);
  if (!parsed) { dialogError = 'graded_invalid_input'; renderDialog(); $('graded-cert').focus(); return; }
  const provider = parsed.provider ?? $('graded-provider').value;
  if (!provider) { dialogError = 'graded_choose_provider'; renderDialog(); return; }
  const result = queueLookup(provider, parsed.cert_number);
  if (result.status === 'pending') { dialogError = 'graded_already_pending'; renderDialog(); return; }
  $('graded-dialog').close();
});
// Pasting a certificate link picks its provider and keeps just the number.
$('graded-cert').addEventListener('input', () => {
  dialogError = '';
  const parsed = parseScan($('graded-cert').value, graded.state.providers);
  if (parsed?.provider) {
    $('graded-provider').value = parsed.provider; $('graded-cert').value = parsed.cert_number;
    scanMessage = null;
    linkNotice = {key: 'graded_link_recognized', params: {provider: providerName(parsed.provider), number: parsed.cert_number}, tone: 'success'};
  }
  renderDialog();
});
$('graded-provider').addEventListener('change', () => { dialogError = ''; renderDialog(); });
$('graded-scan').addEventListener('click', startScan);
$('graded-scan-stop').addEventListener('click', () => { stopScan(); scanMessage = null; renderDialog(); });
$('graded-photo-button').addEventListener('click', () => $('graded-photo').click());
$('graded-photo').addEventListener('change', event => {
  const file = event.target.files?.[0];
  event.target.value = '';
  if (file) readPhoto(file);
});
$('graded-cancel').addEventListener('click', () => $('graded-dialog').close());
$('graded-dialog-close').addEventListener('click', () => $('graded-dialog').close());
$('graded-dialog').addEventListener('close', () => {
  stopScan(); photoGeneration++; photoBusy = false;
  scanMessage = null; linkNotice = null; scanAdded = []; dialogError = '';
});
$('graded-detail-close').addEventListener('click', () => $('graded-detail-dialog').close());
$('graded-price-form').addEventListener('input', () => { $('graded-price-status').textContent = ''; });
$('graded-price-form').addEventListener('submit', async event => {
  event.preventDefault();
  const id = detailId;
  if (!id || graded.state.priceBusy.has(id)) return;
  $('graded-price-status').textContent = '';
  const error = await graded.setPrice(id, readPrice('graded-detail'));
  if (id !== detailId || !$('graded-detail-dialog').open) return;
  $('graded-price-status').textContent = t(error || 'graded_price_saved');
});
$('graded-name-form').addEventListener('input', () => { $('graded-name-status').textContent = ''; });
$('graded-name-form').addEventListener('submit', async event => {
  event.preventDefault();
  const id = detailId;
  if (!id || graded.state.nameBusy.has(id)) return;
  $('graded-name-status').textContent = '';
  const error = await graded.setDisplayName(id, $('graded-detail-name').value.trim());
  if (id !== detailId || !$('graded-detail-dialog').open) return;
  if (!error) $('graded-detail-name').value = graded.state.records.find(row => row.id === id)?.display_name ?? '';
  $('graded-name-status').textContent = t(error || 'graded_display_name_saved');
});
$('graded-detail-refresh').addEventListener('click', () => { if (detailId) graded.refresh(detailId); });
$('graded-detail-delete').addEventListener('click', () => {
  const row = graded.state.records.find(card => card.id === detailId);
  if (row) confirmDelete(row);
});
$('graded-detail-dialog').addEventListener('close', () => { detailId = null; detailSignature = ''; closeImageMenu();
  $('graded-detail-image-error').textContent = ''; });
// Clicks elsewhere in the details close an open image menu.
$('graded-detail-dialog').addEventListener('pointerdown', event => {
  if (imageMenu && !imageMenu.menu.contains(event.target) && !imageMenu.button.contains(event.target)) closeImageMenu();
});
for (const id of ['graded-image-file', 'graded-image-camera']) {
  $(id).addEventListener('change', event => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (file) openCrop(file);
  });
}
$('graded-crop-detect').addEventListener('click', () => detectOutline());
$('graded-crop-calibrate').addEventListener('click', toggleCalibration);
$('graded-crop-full').addEventListener('click', () => {
  if (!crop?.blob) return;
  cropEditor.set(crop.width, crop.height, fullCorners(crop.width, crop.height, 0));
  crop.hint = 'graded_crop_manual'; renderCrop();
});
$('graded-crop-save').addEventListener('click', saveCrop);
$('graded-crop-cancel').addEventListener('click', () => $('graded-crop-dialog').close());
$('graded-crop-close').addEventListener('click', () => $('graded-crop-dialog').close());
// Esc must not drop a crop that is being saved.
$('graded-crop-dialog').addEventListener('cancel', event => { if (crop?.status === 'saving') event.preventDefault(); });
$('graded-crop-dialog').addEventListener('close', closeCrop);
$('graded-crop-image').addEventListener('load', () => cropEditor.redraw());
addEventListener('resize', () => { if ($('graded-crop-dialog').open) cropEditor.redraw(); });
$('graded-delete-cancel').addEventListener('click', () => $('graded-delete-dialog').close());
$('graded-select').addEventListener('click', () => setSelecting(!selection.active));
$('graded-select-all').addEventListener('change', event => {
  if (selection.running) return;
  for (const row of filterGraded(graded.visible(), filters)) event.target.checked ? selection.ids.add(row.id) : selection.ids.delete(row.id);
  render();
});
$('graded-bulk-refresh').addEventListener('click', bulkRefresh);
$('graded-bulk-move').addEventListener('click', () => {
  const count = selection.ids.size;
  if (count && !selection.running) groups.openBulkMove(count, bulkMove);
});
$('graded-bulk-delete').addEventListener('click', () => {
  const count = selection.ids.size;
  if (!count || selection.running) return;
  pendingDelete = BULK;
  $('graded-delete-title').textContent = t('graded_bulk_delete_title');
  $('graded-delete-message').textContent = t('graded_bulk_delete_message', {count: number(count)});
  $('graded-delete-error').textContent = '';
  $('graded-delete-confirm').disabled = false;
  $('graded-delete-dialog').showModal();
});
$('graded-delete-confirm').addEventListener('click', async () => {
  const id = pendingDelete;
  if (!id) return;
  if (id === BULK) { $('graded-delete-dialog').close(); bulkDelete(); return; }
  $('graded-delete-confirm').disabled = true;
  const error = await graded.remove(id);
  if (pendingDelete !== id) return;
  $('graded-delete-confirm').disabled = false;
  if (error) { $('graded-delete-error').textContent = t(error); return; }
  $('graded-delete-dialog').close();
  if (detailId === id) $('graded-detail-dialog').close();
});
$('graded-delete-dialog').addEventListener('close', () => { pendingDelete = null; });
$('graded-query').addEventListener('input', event => { filters.query = event.target.value; render(); });
$('graded-sort').addEventListener('change', event => { filters.sort = event.target.value; render(); });
$('graded-reset').addEventListener('click', () => { filters = blankFilters(); $('graded-query').value = ''; chipsSignature = ''; render(); });
// app.js owns the shared list/grid choice and announces every change.
document.addEventListener('viewchange', render);
$('language').addEventListener('change', () => { chipsSignature = ''; detailSignature = ''; render(); });

export function clearGraded() {
  requestAPI = undefined; displayCurrency = 'JPY'; rateCache.reset();
  stopScan(); photoGeneration++; photoBusy = false;
  for (const id of ['graded-dialog', 'graded-detail-dialog', 'graded-delete-dialog', 'graded-crop-dialog']) $(id).close();
  filters = blankFilters(); $('graded-query').value = ''; chipsSignature = ''; pendingDelete = null; detailId = null;
  clearTimeout(statusTimer); statusNotice = null;
  selectionGeneration++; selection = blankSelection(); groups.close();
  graded.clear();
}
export function activateGraded(api) { clearGraded(); requestAPI = api; graded.activate(api); }
