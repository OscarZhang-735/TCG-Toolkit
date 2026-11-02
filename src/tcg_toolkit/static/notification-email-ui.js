import {createEmailChallenge, authError} from './email-code.js';
import {t, getLanguage} from './i18n.js';

const PATH = '/api/subscriptions/notification-email';

// User-centre card for the subscription notification address. It only affects
// subscription emails; login and recovery keep using the account email.
export function createNotificationEmailUI({request, getUser = () => null, onChange = () => {}, root = document}) {
  const $ = id => root.getElementById(id);
  const challenge = createEmailChallenge({request});
  let generation = 0, open = false, settings = null, loadError = '', error = '', busy = false;
  function refresh() {
    $('notification-form').hidden = !open;
    $('notification-settings').classList.toggle('collapsed', !open);
    const toggle = $('notification-toggle');
    toggle.disabled = busy || !settings;
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', t(open ? 'panel_collapse' : 'panel_expand'));
    toggle.title = toggle.getAttribute('aria-label');
    $('notification-toggle-icon').className = `bi ${open ? 'bi-chevron-up' : 'bi-chevron-down'}`;
    const custom = Boolean(settings && !settings.using_registration_email);
    $('notification-email').textContent = loadError ? t(loadError) : !settings ? t('notify_loading')
      : settings.email ? t(custom ? 'notify_to_custom' : 'notify_to_registration', {email: settings.email}) : t('notify_none');
    $('notification-email').classList.toggle('bound', Boolean(settings?.email));
    $('notification-retry').hidden = !loadError;
    $('notification-state').hidden = !settings;
    $('notification-state').classList.toggle('ok', Boolean(settings?.email));
    $('notification-state-icon').className = `bi ${settings?.email ? 'bi-check-circle-fill' : 'bi-dash-circle'}`;
    $('notification-state-label').textContent = t(!settings?.email ? 'notify_state_none' : custom ? 'notify_state_custom' : 'notify_state_registration');
    const state = challenge.snapshot();
    const send = $('notification-send-code');
    send.disabled = busy || state.busy || state.remaining > 0;
    send.textContent = t(state.busy ? 'email_sending' : state.remaining ? 'email_resend_in' : 'email_send', {seconds: state.remaining});
    $('notification-status').textContent = state.errorKey ? t(state.errorKey)
      : state.expired ? t('email_code_expired') : state.challengeId ? t('email_sent') : '';
    $('notification-error').textContent = error ? t(error) : '';
    $('notification-submit').disabled = busy || state.busy;
    $('notification-reset').hidden = !custom;
    // Without a verified account email, resetting leaves no recipient at all.
    $('notification-reset-label').textContent = t(getUser()?.email_verified ? 'notify_use_registration' : 'notify_remove_custom');
    $('notification-reset').disabled = busy;
  }
  function accept(value) {
    settings = value; loadError = '';
    onChange(settings.email);
  }
  async function load() {
    const started = generation;
    loadError = ''; refresh();
    try {
      const response = await request(PATH, {cache: 'no-store'});
      if (!response.ok) throw new Error('notification settings');
      const value = await response.json();
      if (started === generation) accept(value);
    } catch (failure) {
      if (started === generation && failure.name !== 'AbortError') loadError = 'notify_load_failed';
    }
    if (started === generation) refresh();
  }
  function clearForm() {
    challenge.reset(); error = '';
    $('notification-form').reset();
  }
  function reset() {
    generation++; open = false; settings = null; loadError = ''; busy = false;
    clearForm(); refresh();
  }
  // Settings changes return the effective address; a stale reply is ignored.
  async function write(method, body) {
    const started = generation;
    busy = true; error = ''; refresh();
    try {
      const response = await request(PATH, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
      const result = await response.json();
      if (started !== generation) return;
      if (!response.ok) { error = authError(result.detail, response.status); return; }
      accept(result); clearForm(); open = false;
    } catch (failure) {
      if (started === generation && failure.name !== 'AbortError') error = 'auth_unavailable';
    } finally {
      if (started === generation) { busy = false; refresh(); }
    }
  }
  $('notification-toggle').addEventListener('click', () => { open = !open; refresh(); });
  $('notification-retry').addEventListener('click', load);
  $('notification-new-email').addEventListener('input', () => {
    challenge.setEmail($('notification-new-email').value);
    $('notification-code').value = ''; error = ''; refresh();
  });
  $('notification-send-code').addEventListener('click', async () => {
    const email = $('notification-new-email');
    if (!email.reportValidity() || !email.value) return;
    challenge.setEmail(email.value); error = '';
    const pending = challenge.send(`${PATH}/code`, getLanguage());
    refresh(); await pending; refresh();
  });
  $('notification-form').addEventListener('submit', event => {
    event.preventDefault();
    if (busy) return;
    const proof = challenge.payload($('notification-code').value);
    if (!proof) { error = 'email_request_code'; refresh(); return; }
    write('PUT', proof);
  });
  $('notification-reset').addEventListener('click', () => { if (!busy) write('DELETE', {}); });
  return {refresh, reset, load, activate() { reset(); return load(); }};
}
