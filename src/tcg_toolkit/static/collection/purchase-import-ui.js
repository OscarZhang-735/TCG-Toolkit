import {t, getLanguage} from '../i18n.js';
import {PURCHASE_GAMES} from './purchase-import.js';

const $ = id => document.getElementById(id);
const node = (tag, text, className) => {
  const element = document.createElement(tag);
  if (text != null) element.textContent = text;
  if (className) element.className = className;
  return element;
};
const number = value => value.toLocaleString(getLanguage());
const cardLabel = card => [card.game, card.card_number, card.rarity, card.name].filter(Boolean).join(' · ');

const importId = () => crypto.randomUUID?.()
  ?? Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');

export function createPurchaseImport({inventory, preview, confirm}) {
  const dialog = $('purchase-dialog'), text = $('purchase-text'), source = $('purchase-source');
  const submit = $('purchase-submit'), games = $('purchase-games');
  for (const game of PURCHASE_GAMES) {
    const chip = node('label', null, 'chip'), checkbox = node('input'), mark = node('i', null, 'bi bi-check-lg');
    checkbox.type = 'checkbox'; checkbox.value = game;
    mark.setAttribute('aria-hidden', 'true');
    chip.append(checkbox, mark, node('span', game));
    games.querySelector('.chips').append(chip);
  }
  const selectedGames = () => [...games.querySelectorAll('input:checked')].map(input => input.value);
  let rows = null, controller = null, attempt = null, added = null, error = '', rowLanguage = '';

  function additions() {
    const items = [];
    for (const {item, quantity} of rows ?? []) {
      if (item.status !== 'matched') continue;
      if (!Number.isSafeInteger(quantity) || quantity < 0) return null;
      if (!quantity) continue;
      const {game, card_number, rarity, name, image_url} = item.matches[0];
      items.push({game, card_number, rarity, name, image_url: image_url || null, quantity});
    }
    return items;
  }

  function previewRow(row) {
    const {item} = row, li = node('li', null, 'purchase-row'), info = node('div', null, 'purchase-row-info');
    const original = [item.card_number, item.rarity, item.name].filter(Boolean).join(' · ');
    info.append(node('strong', original || item.text));
    if (item.quantity != null) {
      let receipt = t('purchase_receipt_row', {quantity: number(item.quantity)});
      if (item.price != null) {
        const price = new Intl.NumberFormat(getLanguage(), {style: 'currency', currency: item.currency}).format(item.price);
        receipt += ' · ' + t('purchase_unit_price', {price});
      }
      info.append(node('p', receipt));
    }
    li.append(info);
    if (item.status !== 'matched') {
      const key = {invalid: 'purchase_invalid_row', failed: 'purchase_row_failed', missing: 'purchase_unmatched',
        ambiguous: 'purchase_ambiguous'}[item.status];
      info.append(node('p', t(key)));
      return li;
    }
    const label = node('label', null, 'field'), quantity = node('input');
    label.append(node('span', t('purchase_quantity')), quantity);
    quantity.type = 'number'; quantity.min = '0'; quantity.step = '1'; quantity.max = String(Number.MAX_SAFE_INTEGER);
    quantity.value = Number.isNaN(row.quantity) ? '' : row.quantity;
    quantity.disabled = Boolean(attempt);
    quantity.setAttribute('aria-label', t('purchase_quantity_for', {card: item.card_number}));
    quantity.addEventListener('input', () => { row.quantity = quantity.valueAsNumber; error = ''; render(); });
    info.append(node('p', t('purchase_match', {card: cardLabel(item.matches[0])})));
    li.append(label);
    return li;
  }

  function renderRows() {
    $('purchase-rows').replaceChildren(...(rows ?? []).map(previewRow));
    rowLanguage = getLanguage();
  }
  function render() {
    const busy = Boolean(controller), finished = added !== null;
    const reviewing = rows !== null && !finished, saving = busy && reviewing;
    $('cards-purchase-import').disabled = !inventory.state.ready || inventory.state.busy || inventory.state.loading;
    $('purchase-entry').hidden = reviewing || finished;
    $('purchase-preview').hidden = !reviewing;
    if (reviewing && rowLanguage !== getLanguage()) renderRows();
    source.disabled = busy; text.disabled = busy;
    games.hidden = finished; games.disabled = busy || Boolean(attempt);
    $('purchase-close').disabled = saving;
    $('purchase-cancel').disabled = saving;
    $('purchase-cancel').textContent = t(finished ? 'close' : 'detail_cancel');
    $('purchase-back').hidden = !reviewing || Boolean(attempt);
    submit.hidden = finished;
    const items = reviewing ? additions() : null;
    submit.disabled = busy || finished || (reviewing ? !items?.length : !text.value.trim() || !selectedGames().length);
    submit.querySelector('.bi').className = 'bi ' + (busy ? 'bi-arrow-repeat spin' : reviewing ? 'bi-check-lg' : 'bi-search');
    submit.querySelector('span').textContent = t(reviewing ? (attempt && !busy ? 'purchase_retry' : 'purchase_confirm') : 'purchase_lookup');
    const status = $('purchase-status');
    status.classList.toggle('pending', busy || reviewing);
    status.textContent = busy ? t(saving ? 'cards_importing' : 'purchase_searching')
      : finished ? t('purchase_result', {quantity: number(added)})
      : reviewing && items ? t('purchase_summary', {lines: number(items.length),
        quantity: number(items.reduce((sum, item) => sum + item.quantity, 0)), skipped: number(rows.length - items.length)}) : '';
    $('purchase-error').textContent = error ? t(error) : reviewing && !items ? t('purchase_invalid_quantity') : '';
  }

  async function lookup() {
    const active = new AbortController();
    controller = active; error = ''; render();
    try {
      const result = await preview({source: source.value, text: text.value, games: selectedGames()}, active.signal);
      if (controller !== active) return;
      if (result.items.length) {
        rows = result.items.map(item => ({item, quantity: item.quantity}));
        renderRows();
      } else error = 'purchase_empty';
    } catch {
      if (controller === active) error = 'purchase_lookup_failed';
    } finally {
      if (controller === active) { controller = null; render(); }
    }
  }

  async function save() {
    const active = new AbortController();
    attempt ??= {source: source.value, items: additions(), groupId: inventory.state.group, importId: importId()};
    controller = active; error = ''; renderRows(); render();
    try {
      const result = await confirm(attempt, active.signal);
      if (controller !== active) return;
      added = result.quantity;
      attempt = null;
    } catch (failure) {
      if (controller !== active) return;
      // After an uncertain result, retry the frozen request with its original identity.
      if (failure.status >= 400 && failure.status < 500 && ![408, 425, 429].includes(failure.status)
          && !String(failure.detail?.type ?? failure.detail).includes('pending')) {
        attempt = null;
        error = {card_quantity_overflow: 'purchase_quantity_overflow', card_group_not_found: 'favorite_group_missing'}[failure.detail]
          ?? 'cards_import_failed';
        renderRows();
      } else error = 'purchase_retry_hint';
    } finally {
      if (controller === active) { controller = null; render(); }
    }
  }
  function reset() {
    controller?.abort(); controller = null;
    rows = null; attempt = null; added = null; error = '';
    $('purchase-form').reset(); renderRows(); dialog.close();
  }
  $('cards-purchase-import').addEventListener('click', () => {
    if ($('cards-purchase-import').disabled) return;
    if (added !== null) reset();
    render(); dialog.showModal();
    if (!rows) text.focus();
  });
  for (const input of [source, text]) input.addEventListener('input', () => { error = ''; render(); });
  games.addEventListener('change', () => { rows = null; error = ''; renderRows(); render(); });
  $('purchase-form').addEventListener('submit', event => {
    event.preventDefault(); if (!submit.disabled) (rows ? save() : lookup());
  });
  $('purchase-back').addEventListener('click', () => { rows = null; error = ''; renderRows(); render(); text.focus(); });
  for (const id of ['purchase-close', 'purchase-cancel']) $(id).addEventListener('click', () => dialog.close());
  dialog.addEventListener('cancel', event => { if (attempt && controller) event.preventDefault(); });
  dialog.addEventListener('close', () => { controller?.abort(); controller = null; });
  return {render, reset};
}
