// User centre: Hobby Station sales-cart cookies. Saving reads the shop's cart once to verify them.
import {t, getLanguage} from './i18n.js';
import {cookieValue} from './hobbystation-client.js';
const $ = id => document.getElementById(id);

// Server error type → message key; anything else is a passing failure worth retrying.
export function hobbystationErrorKey(error) {
  if (error?.message === 'hobbystation_credentials_missing') return 'hs_cart_need_credentials';
  if (error?.detail === 'invalid_hobbystation_request') return 'hs_cart_invalid_input';
  return ({hobbystation_credentials_rejected: 'hs_cart_credentials_rejected',
    hobbystation_timeout: 'hs_cart_timeout'})[error?.detail?.type] ?? 'hs_cart_failed';
}

// onCart receives the cart read while verifying, or null when the saved values changed without one.
export function bindHobbystationSettings(client, {onCart = () => {}} = {}) {
  // expanded: null follows the state (open until verified); true/false is the user's choice.
  let statusKey = '', parameters = {}, verifying = false, verified = false, rejected = false, expanded = null, run = 0;
  function render() {
    let configured = false;
    try { configured = client.hasCredentials(); } catch { /* signed out */ }
    const ok = configured && verified, warn = configured && rejected;
    const open = verifying || (expanded ?? !ok);
    $('hs-cart-state-icon').className = ok ? 'bi bi-check-circle-fill' : warn ? 'bi bi-exclamation-circle' : '';
    $('hs-cart-state-label').textContent = t(!configured ? 'cart_not_configured'
      : warn ? 'cart_credentials_need_update' : ok ? 'cart_configured' : 'cart_unverified_badge');
    $('hs-cart-state').classList.toggle('ok', ok);
    $('hs-cart-state').classList.toggle('warn', warn);
    $('hs-cart-body').hidden = !open;
    $('hs-cart-settings').classList.toggle('collapsed', !open);
    $('hs-cart-toggle').hidden = verifying;
    $('hs-cart-toggle').setAttribute('aria-expanded', String(open));
    $('hs-cart-toggle-icon').className = `bi ${open ? 'bi-chevron-up' : 'bi-chevron-down'}`;
    $('hs-cart-toggle').setAttribute('aria-label', t(open ? 'cart_settings_collapse' : 'cart_settings_edit'));
    $('hs-cart-toggle').title = t(open ? 'cart_settings_collapse' : 'cart_settings_edit');
    $('hs-cart-clear').hidden = !configured;
    $('hs-cart-clear').disabled = verifying;
    $('hs-cart-save').disabled = verifying;
    $('hs-cart-status').textContent = statusKey ? t(statusKey, parameters) : '';
    $('hs-cart-status').classList.toggle('error', !verifying && Boolean(statusKey) && statusKey !== 'hs_cart_verified'
      && statusKey !== 'cart_cleared');
  }
  async function verify() {
    const current = ++run;
    verifying = true; verified = false; rejected = false; statusKey = 'hs_cart_verifying'; parameters = {};
    render();
    try {
      const cart = await client.query();
      if (current !== run) return;
      verified = true; expanded = null; statusKey = 'hs_cart_verified';
      onCart(cart);
      parameters = {quantity: (cart.total_quantity ?? 0).toLocaleString(getLanguage())};
    } catch (error) {
      if (current !== run || error?.name === 'AbortError') return;
      statusKey = hobbystationErrorKey(error); expanded = true;
      rejected = statusKey === 'hs_cart_credentials_rejected';
      onCart(null);
    } finally {
      if (current === run) { verifying = false; render(); }
    }
  }
  $('hs-cart-form').addEventListener('submit', event => {
    event.preventDefault();
    const eccube = cookieValue($('hs-cart-eccube').value, 'eccube');
    const botCheck = cookieValue($('hs-cart-bot-check').value, 'eccube_bot_check');
    if (!eccube || !botCheck) { statusKey = 'hs_cart_invalid_input'; render(); return; }
    try {
      client.saveCredentials({eccube, eccube_bot_check: botCheck});
    } catch (error) {
      statusKey = error.message === 'credential_storage_unavailable' ? 'cart_storage_unavailable' : 'hs_cart_invalid_input';
      render(); return;
    }
    $('hs-cart-eccube').value = ''; $('hs-cart-bot-check').value = '';
    verify();
  });
  $('hs-cart-toggle').addEventListener('click', () => { expanded = $('hs-cart-body').hidden; render(); });
  $('hs-cart-clear').addEventListener('click', () => {
    try { client.clearCredentials(); } catch { /* signed out */ }
    reset(); statusKey = 'cart_cleared'; render(); onCart(null);
  });
  // Signing out or switching account must not leave the previous status behind.
  function reset() {
    run++; statusKey = ''; parameters = {}; verifying = false; verified = false; rejected = false; expanded = null;
    $('hs-cart-eccube').value = ''; $('hs-cart-bot-check').value = '';
  }
  return {render, reset, verify};
}
