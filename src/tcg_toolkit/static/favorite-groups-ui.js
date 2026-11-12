import {bindGroups} from './group-ui.js';
const $ = id => document.getElementById(id);
const el = (tag, className = '', text = '') => {const node = document.createElement(tag); node.className = className; node.textContent = text; return node;};

// Group bar and dialogs on the favorites page, plus the "save to group" menu on unsaved stars.
export function bindFavoriteGroups(favorites) {
  const state = favorites.state;
  const groups = bindGroups(favorites, {prefix: 'favorite', deleteMessage: 'favorite_group_delete_message', locked: () => state.running});
  function openMove(item, key) {
    const saved = favorites.find(item); if (!saved) return;
    groups.openMove(item.name, saved.group_id ?? '', groupId => favorites.move(item, groupId),
      () => state.busy.has(key), () => Boolean(favorites.find(item)));
  }

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

  return {render: groups.render, openBulkMove: groups.openBulkMove, selected: groups.selected, openMove, attachAddMenu,
    close() { hideMenu(); groups.close(); }};
}
