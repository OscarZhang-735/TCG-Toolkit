import {t, getLanguage} from '../i18n.js';

export function bindStorefrontSettings(client, {prefix, keys, fields, errorKey, prepareCredentials = values => values}, {onCart = () => {}} = {}) {
  const node = id => document.getElementById(prefix + '-cart-' + id);
  // expanded: null follows the state (open until verified); true/false is the user's choice.
  let statusKey = '', parameters = {}, verifying = false, verified = false, rejected = false, expanded = null, run = 0;
  function render() {
    let configured = false;
    try { configured = client.hasCredentials(); } catch { /* signed out */ }
    const ok = configured && verified, warn = configured && rejected;
    const open = verifying || (expanded ?? !ok);
    node('state-icon').className = ok ? 'bi bi-check-circle-fill' : warn ? 'bi bi-exclamation-circle' : '';
    node('state-label').textContent = t(!configured ? 'cart_not_configured'
      : warn ? 'cart_credentials_need_update' : ok ? 'cart_configured' : 'cart_unverified_badge');
    node('state').classList.toggle('ok', ok);
    node('state').classList.toggle('warn', warn);
    node('body').hidden = !open;
    node('settings').classList.toggle('collapsed', !open);
    node('toggle').hidden = verifying;
    node('toggle').setAttribute('aria-expanded', String(open));
    node('toggle-icon').className = `bi ${open ? 'bi-chevron-up' : 'bi-chevron-down'}`;
    node('toggle').setAttribute('aria-label', t(open ? 'cart_settings_collapse' : 'cart_settings_edit'));
    node('toggle').title = t(open ? 'cart_settings_collapse' : 'cart_settings_edit');
    node('clear').hidden = !configured;
    node('clear').disabled = verifying;
    node('save').disabled = verifying;
    node('status').textContent = statusKey ? t(statusKey, parameters) : '';
    node('status').classList.toggle('error', !verifying && Boolean(statusKey) && statusKey !== keys.verified
      && statusKey !== 'cart_cleared');
  }
  async function verify() {
    const current = ++run;
    verifying = true; verified = false; rejected = false; statusKey = keys.verifying; parameters = {};
    render();
    try {
      const cart = await client.query();
      if (current !== run) return;
      verified = true; expanded = null; statusKey = keys.verified;
      onCart(cart);
      parameters = {quantity: (cart.total_quantity ?? 0).toLocaleString(getLanguage())};
    } catch (error) {
      if (current !== run || error?.name === 'AbortError') return;
      statusKey = errorKey(error); expanded = true;
      rejected = statusKey === keys.rejected;
      onCart(null);
    } finally {
      if (current === run) { verifying = false; render(); }
    }
  }
  node('form').addEventListener('submit', event => {
    event.preventDefault();
    const values = Object.fromEntries(fields.map(([id, name, parse]) => [name, parse(node(id).value, name)]));
    if (Object.values(values).some(value => !value)) { statusKey = keys.invalid; render(); return; }
    try {
      client.saveCredentials(prepareCredentials(values));
    } catch (error) {
      statusKey = error.message === 'credential_storage_unavailable' ? 'cart_storage_unavailable' : keys.invalid;
      render(); return;
    }
    for (const [id] of fields) node(id).value = '';
    verify();
  });
  node('toggle').addEventListener('click', () => { expanded = node('body').hidden; render(); });
  node('clear').addEventListener('click', () => {
    try { client.clearCredentials(); } catch { /* signed out */ }
    reset(); statusKey = 'cart_cleared'; render(); onCart(null);
  });
  // Signing out or switching account must not leave the previous status behind.
  function reset() {
    run++; statusKey = ''; parameters = {}; verifying = false; verified = false; rejected = false; expanded = null;
    for (const [id] of fields) node(id).value = '';
  }
  return {render, reset, verify};
}
