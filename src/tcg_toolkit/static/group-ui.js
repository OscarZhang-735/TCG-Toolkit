import {t, getLanguage} from './i18n.js';
const $ = id => document.getElementById(id);
const el = (tag, className = '', text = '') => {const node = document.createElement(tag); node.className = className; node.textContent = text; return node;};

// Group bar plus the create / rename / delete / move dialogs, shared by favorites and the card
// library. Element ids start with prefix (`${prefix}-groups-bar`, `${prefix}-group-…`, `${prefix}-move-…`).
// model.state holds records (each with group_id), groups, group (selected id, null = all), ready
// and groupBusy; locked() is true while the page runs something the bar must not interrupt.
export function bindGroups(model, {prefix, deleteMessage, locked}) {
  const state = model.state, id = suffix => $(`${prefix}-${suffix}`);
  let editing = null, deleting = null, moving = null;
  const selected = () => state.groups.find(group => group.id === state.group) ?? null;
  const count = groupId => state.records.filter(row => groupId === null || row.group_id === groupId).length;
  function tab(groupId, label) {
    const button = el('button', 'group-tab'); button.type = 'button';
    button.setAttribute('aria-pressed', String(state.group === groupId));
    button.append(el('span', 'group-tab-name', label), el('span', 'group-tab-count', count(groupId).toLocaleString(getLanguage())));
    button.title = label;
    button.disabled = locked();
    button.addEventListener('click', () => model.select(groupId));
    return button;
  }
  function render() {
    const custom = Boolean(selected());
    id('groups-bar').hidden = !state.ready;
    id('groups').replaceChildren(tab(null, t('favorite_group_all')), ...state.groups.map(row => tab(row.id, row.name)));
    id('group-rename').hidden = !custom; id('group-delete').hidden = !custom;
    for (const name of ['group-create', 'group-rename', 'group-delete']) id(name).disabled = state.groupBusy || locked();
    id('group-name').disabled = state.groupBusy; id('group-save').disabled = state.groupBusy;
    id('group-delete-confirm').disabled = state.groupBusy;
    const busy = Boolean(moving?.busy());
    id('move-group').disabled = busy; id('move-save').disabled = busy;
    if (moving && !moving.exists()) id('move-dialog').close();
  }
  function openEditor(group) {
    editing = group;
    id('group-dialog-title').textContent = t(group ? 'favorite_group_rename' : 'favorite_group_create');
    id('group-name').value = group ? group.name : '';
    id('group-error').textContent = '';
    id('group-dialog').showModal(); id('group-name').select();
  }
  id('group-create').addEventListener('click', () => openEditor(null));
  id('group-rename').addEventListener('click', () => { const group = selected(); if (group) openEditor(group); });
  id('group-form').addEventListener('submit', async event => {
    event.preventDefault();
    const name = id('group-name').value.trim();
    if (!name) { id('group-error').textContent = t('favorite_group_name_required'); return; }
    const error = await (editing ? model.renameGroup(editing.id, name) : model.createGroup(name));
    if (error) id('group-error').textContent = t(error);
    else id('group-dialog').close();
  });
  id('group-cancel').addEventListener('click', () => id('group-dialog').close());
  id('group-dialog').addEventListener('cancel', event => { if (state.groupBusy) event.preventDefault(); });
  id('group-dialog').addEventListener('close', () => { editing = null; });

  id('group-delete').addEventListener('click', () => {
    const group = selected(); if (!group) return;
    deleting = group;
    id('group-delete-message').textContent = t(deleteMessage, {name: group.name, count: count(group.id).toLocaleString(getLanguage())});
    id('group-delete-error').textContent = '';
    id('group-delete-dialog').showModal();
  });
  id('group-delete-confirm').addEventListener('click', async () => {
    if (!deleting) return;
    const error = await model.deleteGroup(deleting.id);
    if (error) id('group-delete-error').textContent = t(error);
    else id('group-delete-dialog').close();
  });
  id('group-delete-cancel').addEventListener('click', () => id('group-delete-dialog').close());
  id('group-delete-dialog').addEventListener('cancel', event => { if (state.groupBusy) event.preventDefault(); });
  id('group-delete-dialog').addEventListener('close', () => { deleting = null; });

  // One dialog serves single-card and bulk moves; submit resolves to an error key ('' on success).
  // exists() turning false (the card was removed meanwhile) closes the dialog.
  function openMove(name, value, submit, busy = () => false, exists = () => true) {
    moving = {submit, busy, exists};
    id('move-name').textContent = name;
    const none = el('option', '', t('favorite_group_none')); none.value = '';
    id('move-group').replaceChildren(none, ...state.groups.map(group => {
      const option = el('option', '', group.name); option.value = group.id; return option;
    }));
    id('move-group').value = value;
    id('move-error').textContent = '';
    render();
    id('move-dialog').showModal();
  }
  // Bulk moves report progress in the selection bar, so the dialog closes right away.
  function openBulkMove(total, start) {
    openMove(t('favorite_bulk_move_target', {count: total.toLocaleString(getLanguage())}), '',
      async groupId => { start(groupId); return ''; });
  }
  id('move-form').addEventListener('submit', async event => {
    event.preventDefault(); if (!moving) return;
    const error = await moving.submit(id('move-group').value || null);
    if (error) id('move-error').textContent = t(error);
    else id('move-dialog').close();
  });
  id('move-cancel').addEventListener('click', () => id('move-dialog').close());
  id('move-dialog').addEventListener('cancel', event => { if (moving?.busy()) event.preventDefault(); });
  id('move-dialog').addEventListener('close', () => { moving = null; });

  return {render, openMove, openBulkMove, selected,
    close() { id('group-dialog').close(); id('group-delete-dialog').close(); id('move-dialog').close(); }};
}
