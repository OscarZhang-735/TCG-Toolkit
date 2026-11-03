import {t, getLanguage} from './i18n.js';
const $ = id => document.getElementById(id);
const el = (tag, className = '', text = '') => {const node = document.createElement(tag); node.className = className; node.textContent = text; return node;};
const icon = name => el('i', `bi ${name}`);

// Group bar on the favorites page plus the create / rename / delete / move dialogs.
export function bindFavoriteGroups(favorites) {
  const state = favorites.state;
  let editing = null, deleting = null, moving = null;
  const selected = () => state.groups.find(group => group.id === state.group) ?? null;
  const count = id => state.records.filter(row => id === null || row.group_id === id).length;
  function tab(id, label) {
    const button = el('button', 'group-tab'); button.type = 'button';
    button.setAttribute('aria-pressed', String(state.group === id));
    button.append(el('span', 'group-tab-name', label), el('span', 'group-tab-count', count(id).toLocaleString(getLanguage())));
    button.title = label;
    button.disabled = state.running;
    button.addEventListener('click', () => favorites.select(id));
    return button;
  }
  function render() {
    const custom = Boolean(selected());
    $('favorite-groups-bar').hidden = !state.ready;
    $('favorite-groups').replaceChildren(tab(null, t('favorite_group_all')), ...state.groups.map(row => tab(row.id, row.name)));
    $('favorite-group-rename').hidden = !custom; $('favorite-group-delete').hidden = !custom;
    for (const id of ['favorite-group-create', 'favorite-group-rename', 'favorite-group-delete']) $(id).disabled = state.groupBusy || state.running;
    $('favorite-group-name').disabled = state.groupBusy; $('favorite-group-save').disabled = state.groupBusy;
    $('favorite-group-delete-confirm').disabled = state.groupBusy;
    const busy = moving && state.busy.has(moving.key);
    $('favorite-move-group').disabled = busy; $('favorite-move-save').disabled = busy;
    if (moving && !favorites.find(moving.item)) $('favorite-move-dialog').close();
  }
  function openEditor(group) {
    editing = group;
    $('favorite-group-dialog-title').textContent = t(group ? 'favorite_group_rename' : 'favorite_group_create');
    $('favorite-group-name').value = group ? group.name : '';
    $('favorite-group-error').textContent = '';
    $('favorite-group-dialog').showModal(); $('favorite-group-name').select();
  }
  $('favorite-group-create').addEventListener('click', () => openEditor(null));
  $('favorite-group-rename').addEventListener('click', () => { const group = selected(); if (group) openEditor(group); });
  $('favorite-group-form').addEventListener('submit', async event => {
    event.preventDefault();
    const name = $('favorite-group-name').value.trim();
    if (!name) { $('favorite-group-error').textContent = t('favorite_group_name_required'); return; }
    const error = await (editing ? favorites.renameGroup(editing.id, name) : favorites.createGroup(name));
    if (error) $('favorite-group-error').textContent = t(error);
    else $('favorite-group-dialog').close();
  });
  $('favorite-group-cancel').addEventListener('click', () => $('favorite-group-dialog').close());
  $('favorite-group-dialog').addEventListener('cancel', event => { if (state.groupBusy) event.preventDefault(); });
  $('favorite-group-dialog').addEventListener('close', () => { editing = null; });

  $('favorite-group-delete').addEventListener('click', () => {
    const group = selected(); if (!group) return;
    deleting = group;
    $('favorite-group-delete-message').textContent = t('favorite_group_delete_message', {name: group.name, count: count(group.id).toLocaleString(getLanguage())});
    $('favorite-group-delete-error').textContent = '';
    $('favorite-group-delete-dialog').showModal();
  });
  $('favorite-group-delete-confirm').addEventListener('click', async () => {
    if (!deleting) return;
    const error = await favorites.deleteGroup(deleting.id);
    if (error) $('favorite-group-delete-error').textContent = t(error);
    else $('favorite-group-delete-dialog').close();
  });
  $('favorite-group-delete-cancel').addEventListener('click', () => $('favorite-group-delete-dialog').close());
  $('favorite-group-delete-dialog').addEventListener('cancel', event => { if (state.groupBusy) event.preventDefault(); });
  $('favorite-group-delete-dialog').addEventListener('close', () => { deleting = null; });

  function openMove(item, key) {
    const saved = favorites.find(item); if (!saved) return;
    moving = {item, key};
    $('favorite-move-name').textContent = item.name;
    const none = el('option', '', t('favorite_group_none')); none.value = '';
    $('favorite-move-group').replaceChildren(none, ...state.groups.map(group => {
      const option = el('option', '', group.name); option.value = group.id; return option;
    }));
    $('favorite-move-group').value = saved.group_id ?? '';
    $('favorite-move-error').textContent = '';
    render();
    $('favorite-move-dialog').showModal();
  }
  $('favorite-move-form').addEventListener('submit', async event => {
    event.preventDefault(); if (!moving) return;
    const error = await favorites.move(moving.item, $('favorite-move-group').value || null);
    if (error) $('favorite-move-error').textContent = t(error);
    else $('favorite-move-dialog').close();
  });
  $('favorite-move-cancel').addEventListener('click', () => $('favorite-move-dialog').close());
  $('favorite-move-dialog').addEventListener('cancel', event => { if (moving && state.busy.has(moving.key)) event.preventDefault(); });
  $('favorite-move-dialog').addEventListener('close', () => { moving = null; });

  return {render, openMove, selected,
    close() { $('favorite-group-dialog').close(); $('favorite-group-delete-dialog').close(); $('favorite-move-dialog').close(); }};
}
