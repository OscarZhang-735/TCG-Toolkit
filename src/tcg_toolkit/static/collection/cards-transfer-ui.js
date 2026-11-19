import {t, getLanguage} from '../i18n.js';
import {EXPORT_FIELDS, EXPORT_KEY, loadExportSettings, fetchCardExport, downloadCardExport} from './cards-export.js';

// Owns transfer dialogs; the library supplies its model and current view filter.
export function createCardTransfers({inventory, request, hiddenGames, root = document, storage = localStorage}) {
  const $ = id => root.getElementById(id);
  const number = value => value.toLocaleString(getLanguage());
  const collator = new Intl.Collator(undefined, {numeric: true, sensitivity: 'base'});
  function node(tag, text = '', className = '') {
    const element = root.createElement(tag); element.textContent = text; element.className = className; return element;
  }
  const icon = name => node('i', '', `bi ${name}`);
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
  let exportSettings = loadExportSettings(storage, getLanguage());
  const saveExportSettings = () => storage.setItem(EXPORT_KEY, JSON.stringify(exportSettings));
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
    root.querySelectorAll('input[name="export-format"]').forEach(input => { input.checked = input.value === format; });
    root.querySelectorAll('input[name="export-language"]').forEach(input => { input.checked = input.value === exportSettings.language; });
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
    const chip = node('label', '', 'chip'), input = root.createElement('input');
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
    exportHiddenGames = new Set([...hiddenGames()].filter(key => known.has(key)));
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
      const blob = await fetchCardExport(request, {format, language, games, fields: chosenFields, group: exportGroup}, controller.signal);
      if (exportController === controller) { exportController = null; downloadCardExport(blob, format); $('cards-export-dialog').close(); }
    } catch (error) {
      if (exportController === controller && error.name !== 'AbortError') {
        exportError = error.message === 'cards_export_too_long' ? error.message : 'cards_export_failed';
      }
    } finally {
      if (exportController === controller) { exportController = null; renderExport(); }
    }
  }
  $('cards-export-group').addEventListener('change', event => {
    exportGroup = event.target.value || null; exportError = '';
    const known = new Set(exportGames().map(([key]) => key));
    exportHiddenGames = new Set([...exportHiddenGames].filter(key => known.has(key)));
    exportChips(); renderExport();
  });
  $('cards-export').addEventListener('click', openExport);
  $('cards-export-form').addEventListener('submit', event => { event.preventDefault(); runExport(); });
  for (const [name, key] of [['export-format', 'format'], ['export-language', 'language']]) {
    root.querySelectorAll(`input[name="${name}"]`).forEach(input => input.addEventListener('change', () => {
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

  function reset() {
    exportController?.abort(); exportController = null; exportError = ''; exportGroup = null;
    $('cards-export-dialog').close();
    $('cards-import-dialog').close(); importResult = null;
  }
  return {reset, render() { renderExport(); renderImport(); }};
}
