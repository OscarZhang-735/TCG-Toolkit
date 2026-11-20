// User centre: YuYu-Tei session credentials for the cart. Values are written to
// this tab's sessionStorage by the client and never read back into the form.
import {t, getLanguage} from '../i18n.js';
import {parseCurlCredentials} from './curl-credentials.js';
const $ = id => document.getElementById(id);

// Errors that mean the saved values themselves are wrong, not a passing outage.
const CREDENTIAL_ERRORS = ['cart_credentials_rejected', 'cart_invalid_credentials', 'cart_need_credentials'];

export function bindCartSettings(client, cart) {
  // expanded: null follows the state (open until configured), true/false is the user's choice.
  let statusKey = '', parameters = {}, verifying = false, expanded = null;
  function render() {
    let configured = false;
    try { configured = client.hasCredentials(); } catch { /* signed out */ }
    // Finish a verification first, so this same render already shows its outcome.
    if (verifying && !cart.state.loading) {
      verifying = false;
      const error = cart.state.error;
      statusKey = error || 'cart_verified';
      // Verified: fold the panel away; a failure keeps it open for corrections.
      expanded = error ? true : null;
      parameters = {quantity: (cart.state.totalQuantity ?? 0).toLocaleString(getLanguage())};
    }
    const state = cart.state;
    const rejected = configured && CREDENTIAL_ERRORS.includes(state.error);
    // A successful read of the shop's cart is what verifies the saved values.
    const verified = configured && !rejected && !state.loading && !state.error
      && (state.totalQuantity != null || (state.rows?.length ?? 0) > 0 || state.complete === false);
    if (rejected) expanded = true;
    const open = verifying || (expanded ?? !configured);
    $('cart-settings-state-icon').className = verified ? 'bi bi-check-circle-fill' : rejected ? 'bi bi-exclamation-circle' : '';
    $('cart-settings-state-label').textContent = t(!configured ? 'cart_not_configured'
      : rejected ? 'cart_credentials_need_update' : verified ? 'cart_configured' : 'cart_unverified_badge');
    $('cart-settings-state').classList.toggle('ok', verified);
    $('cart-settings-state').classList.toggle('warn', rejected);
    $('cart-settings-body').hidden = !open;
    $('cart-settings').classList.toggle('collapsed', !open);
    $('cart-settings-toggle').hidden = verifying;
    $('cart-settings-toggle').setAttribute('aria-expanded', String(open));
    // Icon-only: down expands, up collapses; the words stay as its accessible name.
    $('cart-settings-toggle-icon').className = `bi ${open ? 'bi-chevron-up' : 'bi-chevron-down'}`;
    $('cart-settings-toggle').setAttribute('aria-label', t(open ? 'cart_settings_collapse' : 'cart_settings_edit'));
    $('cart-settings-toggle').title = t(open ? 'cart_settings_collapse' : 'cart_settings_edit');
    // Only offered once something is saved; nothing to clear otherwise.
    $('cart-clear').hidden = !configured;
    $('cart-clear').disabled = verifying;
    $('cart-save').disabled = verifying;
    $('cart-curl-save').disabled = verifying;
    $('cart-settings-status').textContent = statusKey ? t(statusKey, parameters) : '';
    $('cart-settings-status').classList.toggle('error', Boolean(cart.state.error) && statusKey === cart.state.error);
  }
  // Both routes end here: store this tab's credentials, then read the cart once to verify them.
  function saveAndVerify(credentials) {
    try {
      client.saveCredentials(credentials);
    } catch (error) {
      statusKey = error.message === 'credential_storage_unavailable' ? 'cart_storage_unavailable' : 'cart_failed';
      render(); return;
    }
    $('cart-cookie').value = ''; $('cart-csrf').value = ''; $('cart-curl').value = '';
    verifying = true; statusKey = 'cart_verifying';
    // refresh() marks the cart as loading first, so this render shows the pending check.
    cart.refresh();
    render();
  }
  $('cart-curl-save').addEventListener('click', () => {
    let credentials;
    try {
      // Parsing only: the pasted command itself is never run.
      credentials = parseCurlCredentials($('cart-curl').value);
    } catch {
      statusKey = 'cart_curl_invalid'; render(); return;
    }
    saveAndVerify(credentials);
  });
  $('cart-settings-toggle').addEventListener('click', () => {
    expanded = $('cart-settings-body').hidden;
    render();
  });
  $('cart-settings-form').addEventListener('submit', event => {
    event.preventDefault();
    const cookie = $('cart-cookie').value.trim(), csrf = $('cart-csrf').value.trim();
    if (!cookie || !csrf) { statusKey = 'cart_invalid_input'; render(); return; }
    saveAndVerify({cookie, csrf_token: csrf});
  });
  $('cart-clear').addEventListener('click', () => {
    try { client.clearCredentials(); } catch { /* signed out: nothing stored */ }
    reset(); statusKey = 'cart_cleared';
    cart.refresh();
  });
  // Signing out or switching account must not leave the previous status behind.
  function reset() {
    statusKey = ''; verifying = false; expanded = null;
    $('cart-cookie').value = ''; $('cart-csrf').value = ''; $('cart-curl').value = '';
  }
  return {render, reset};
}
