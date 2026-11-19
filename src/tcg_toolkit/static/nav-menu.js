// Portrait phones: each sidebar group ("YuYu-Tei", "Local") collapses into a dropdown.
// Wider layouts show the same groups as plain lists; there the toggles are hidden by CSS.
export const PORTRAIT_MENUS = '(max-width: 700px) and (orientation: portrait)';
const NARROW = '(max-width: 700px)';
const groups = root => [...root.querySelectorAll('#app-nav .nav-group')];
// Expandable entries inside a group (the cart: one page per shop).
const submenus = root => [...root.querySelectorAll('#app-nav .nav-submenu')];
function setSubmenuOpen(menu, open) {
  menu.classList.toggle('open', open);
  menu.querySelector('.nav-submenu-toggle').setAttribute('aria-expanded', String(open));
}
// Page name for a dropdown toggle; a submenu page reads "Cart · Hobby Station".
function pageName(link) {
  const name = link.querySelector('span')?.textContent ?? '';
  const parent = link.closest?.('.nav-submenu')?.querySelector('.nav-submenu-toggle span')?.textContent;
  return parent ? `${parent} · ${name}` : name;
}

// The toggle names the page you are on when it belongs to that group.
export function syncNavMenus(root = document) {
  for (const group of groups(root)) {
    const current = group.querySelector('.nav-group-items a[aria-current]');
    group.classList.toggle('has-current', Boolean(current));
    group.querySelector('.nav-group-current').textContent = current ? pageName(current) : '';
  }
  // Arriving on a page inside a submenu opens it; the user may close it again afterwards.
  for (const menu of submenus(root)) {
    const current = Boolean(menu.querySelector('.nav-submenu-items a[aria-current]'));
    // Not in the one-row layout, where the list is a dropdown that should stay shut.
    if (current && !menu.classList.contains('has-current') && !globalThis.matchMedia?.(NARROW).matches) setSubmenuOpen(menu, true);
    menu.classList.toggle('has-current', current);
  }
}
function setOpen(group, open) {
  group.classList.toggle('open', open);
  group.querySelector('.nav-group-toggle').setAttribute('aria-expanded', String(open));
}
export function closeNavMenus(root = document, except = null) {
  for (const group of groups(root)) if (group !== except) setOpen(group, false);
}
export function bindNavMenus(root = document, win = globalThis) {
  for (const menu of submenus(root)) {
    menu.querySelector('.nav-submenu-toggle').addEventListener('click', () => setSubmenuOpen(menu, !menu.classList.contains('open')));
    menu.querySelector('.nav-submenu-items').addEventListener('click', event => {
      if (event.target.closest('a') && win.matchMedia?.(NARROW).matches) setSubmenuOpen(menu, false);
    });
  }
  // The one-row dropdown closes when the pointer goes elsewhere.
  root.addEventListener('pointerdown', event => {
    if (!win.matchMedia?.(NARROW).matches) return;
    for (const menu of submenus(root)) if (!event.target.closest?.('.nav-submenu') ) setSubmenuOpen(menu, false);
  });
  for (const group of groups(root)) {
    group.querySelector('.nav-group-toggle').addEventListener('click', () => {
      const open = !group.classList.contains('open');
      closeNavMenus(root, group);  // only one menu at a time
      setOpen(group, open);
    });
    group.querySelector('.nav-group-items').addEventListener('click', event => {
      if (event.target.closest('a')) closeNavMenus(root);
    });
  }
  root.addEventListener('pointerdown', event => { if (!event.target.closest?.('#app-nav .nav-group')) closeNavMenus(root); });
  root.addEventListener('keydown', event => {
    const open = root.querySelector('#app-nav .nav-group.open');
    if (event.key !== 'Escape' || !open) return;
    closeNavMenus(root);
    open.querySelector('.nav-group-toggle').focus();
  });
  win.addEventListener?.('hashchange', () => closeNavMenus(root));
  // Rotating to landscape (or widening) switches back to plain lists.
  win.matchMedia?.(PORTRAIT_MENUS).addEventListener?.('change', () => closeNavMenus(root));
}
