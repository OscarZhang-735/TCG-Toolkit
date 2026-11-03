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
    const busy = Boolean(moving?.busy());
    $('favorite-move-group').disabled = busy; $('favorite-move-save').disabled = busy;
    if (moving?.item && !favorites.find(moving.item)) $('favorite-move-dialog').close();
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

  // One dialog serves single-card and bulk moves; submit resolves to an error key ('' on success).
  function openMoveDialog(name, value, submit, busy = () => false, item = null) {
    moving = {submit, busy, item};
    $('favorite-move-name').textContent = name;
    const none = el('option', '', t('favorite_group_none')); none.value = '';
    $('favorite-move-group').replaceChildren(none, ...state.groups.map(group => {
      const option = el('option', '', group.name); option.value = group.id; return option;
    }));
    $('favorite-move-group').value = value;
    $('favorite-move-error').textContent = '';
    render();
    $('favorite-move-dialog').showModal();
  }
  function openMove(item, key) {
    const saved = favorites.find(item); if (!saved) return;
    openMoveDialog(item.name, saved.group_id ?? '', groupId => favorites.move(item, groupId), () => state.busy.has(key), item);
  }
  // Bulk moves report progress in the selection bar, so the dialog closes right away.
  function openBulkMove(count, start) {
    openMoveDialog(t('favorite_bulk_move_target', {count: count.toLocaleString(getLanguage())}), '',
      async groupId => { start(groupId); return ''; });
  }
  $('favorite-move-form').addEventListener('submit', async event => {
    event.preventDefault(); if (!moving) return;
    const error = await moving.submit($('favorite-move-group').value || null);
    if (error) $('favorite-move-error').textContent = t(error);
    else $('favorite-move-dialog').close();
  });
  $('favorite-move-cancel').addEventListener('click', () => $('favorite-move-dialog').close());
  $('favorite-move-dialog').addEventListener('cancel', event => { if (moving?.busy()) event.preventDefault(); });
  $('favorite-move-dialog').addEventListener('close', () => { moving = null; });

  // Hover menu on an unsaved card's star: pick the group to save it into. It lives in <body>
  // with fixed positioning so scrolling tables and card images cannot clip it.
  const menu = $('favorite-add-menu');
  let menuItem = null, hideTimer = 0;
  function hideMenu() { clearTimeout(hideTimer); menu.hidden = true; menuItem = null; }
  function scheduleHide() { clearTimeout(hideTimer); hideTimer = setTimeout(hideMenu, 150); }
  function showMenu(button, item) {
    clearTimeout(hideTimer);
    if (!state.ready || !state.groups.length || favorites.find(item) || button.disabled) return;
    menuItem = item;
    $('favorite-add-items').replaceChildren(...state.groups.map(group => {
      const option = el('button', 'favorite-add-option', group.name); option.type = 'button';
      option.title = group.name; option.setAttribute('role', 'menuitem');
      option.addEventListener('click', () => { const target = menuItem; hideMenu(); if (target) favorites.toggle(target, group.id); });
      return option;
    }));
    menu.hidden = false;
    const rect = button.getBoundingClientRect(), width = menu.offsetWidth, height = menu.offsetHeight;
    menu.style.left = `${Math.max(8, Math.min(rect.right - width, innerWidth - width - 8))}px`;
    menu.style.top = `${rect.bottom + 4 + height > innerHeight - 8 ? Math.max(8, rect.top - 4 - height) : rect.bottom + 4}px`;
  }
  function attachAddMenu(button, item) {
    button.addEventListener('pointerenter', event => { if (event.pointerType === 'mouse') showMenu(button, item); });
    button.addEventListener('pointerleave', scheduleHide);
    button.addEventListener('click', hideMenu);
    button.addEventListener('keydown', event => {
      if (event.key !== 'ArrowDown') return;
      event.preventDefault(); showMenu(button, item); menu.querySelector('button')?.focus();
    });
  }
  menu.addEventListener('pointerenter', () => clearTimeout(hideTimer));
  menu.addEventListener('pointerleave', scheduleHide);
  menu.addEventListener('focusin', () => clearTimeout(hideTimer));
  menu.addEventListener('focusout', event => { if (!menu.contains(event.relatedTarget)) scheduleHide(); });
  menu.addEventListener('keydown', event => {
    const options = [...menu.querySelectorAll('button')], index = options.indexOf(document.activeElement);
    if (event.key === 'Escape') hideMenu();
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); options[(index + (event.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length]?.focus();
    }
  });
  addEventListener('scroll', event => { if (!menu.contains(event.target)) hideMenu(); }, true);
  addEventListener('resize', hideMenu);

  return {render, openMove, openBulkMove, selected, attachAddMenu,
    close() { hideMenu(); $('favorite-group-dialog').close(); $('favorite-group-delete-dialog').close(); $('favorite-move-dialog').close(); }};
}
