import {createEmailChallenge, authError} from './email-code.js';
import {t, getLanguage} from './i18n.js';

export function createPasswordUI({request, authenticatedRequest, getUser, onChanged, root = document}) {
  const $ = id => root.getElementById(id);
  const challenges = {
    change: createEmailChallenge({request: authenticatedRequest}),
    reset: createEmailChallenge({request})
  };
  let generation = 0, open = false;  // the user-centre form starts collapsed
  const errors = {change: '', reset: ''}, busy = {change: false, reset: false};
  function refresh() {
    const user = getUser();
    $('password-change-form').hidden = !open;
    $('password-settings').classList.toggle('collapsed', !open);
    // Icon-only, like the cart card: down expands, up collapses; the words stay as its accessible name.
    const toggle = $('password-settings-toggle');
    toggle.disabled = busy.change;
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', t(open ? 'panel_collapse' : 'panel_expand'));
    toggle.title = toggle.getAttribute('aria-label');
    $('password-settings-toggle-icon').className = `bi ${open ? 'bi-chevron-up' : 'bi-chevron-down'}`;
    $('password-email-option').disabled = !user?.email_verified;
    if (!user?.email_verified) $('password-method-password').checked = true;
    const byEmail = $('password-email-option').checked;
    $('password-old-field').hidden = byEmail;
    $('password-current').disabled = byEmail;
    $('password-current').required = !byEmail;
    $('password-change-email-fields').hidden = !byEmail;
    $('password-change-code').required = byEmail;
    $('password-change-code').disabled = !byEmail;
    $('password-change-address').textContent = user?.email ? t('password_code_to', {email: user.email}) : '';
    $('password-email-required').hidden = Boolean(user?.email_verified);
    $('password-change-method').disabled = busy.change;
    $('password-reset-back').disabled = busy.reset;
    for (const prefix of ['change', 'reset']) {
      const state = challenges[prefix].snapshot();
      const button = $(`password-${prefix}-send`);
      button.disabled = busy[prefix] || state.busy || state.remaining > 0;
      button.textContent = t(state.busy ? 'email_sending' : state.remaining ? 'email_resend_in' : 'email_send', {seconds: state.remaining});
      $(`password-${prefix}-status`).textContent = state.errorKey ? t(state.errorKey)
        : state.expired ? t('email_code_expired') : state.challengeId ? t(prefix === 'reset' ? 'password_reset_sent' : 'email_sent') : '';
      $(`password-${prefix}-error`).textContent = errors[prefix] ? t(errors[prefix]) : '';
      $(`password-${prefix}-submit`).disabled = busy[prefix] || state.busy;
    }
  }
  function reset() {
    generation++; open = false;
    for (const prefix of ['change', 'reset']) {
      challenges[prefix].reset(); errors[prefix] = ''; busy[prefix] = false;
      $(`password-${prefix}-form`).reset();
    }
  }
  $('password-settings-toggle').addEventListener('click', () => {
    open = !open; refresh();
  });
  $('password-change-method').addEventListener('change', () => {
    $('password-current').value = ''; $('password-change-code').value = '';
    errors.change = ''; refresh();
  });
  $('password-reset-email').addEventListener('input', () => {
    challenges.reset.setEmail($('password-reset-email').value);
    $('password-reset-code').value = ''; errors.reset = ''; refresh();
  });
  for (const prefix of ['change', 'reset']) {
    $(`password-${prefix}-send`).addEventListener('click', async () => {
      if (prefix === 'reset' && !$('password-reset-email').reportValidity()) return;
      const email = prefix === 'reset' ? $('password-reset-email').value : getUser()?.email;
      if (!email) return;
      challenges[prefix].setEmail(email);
      errors[prefix] = '';
      const pending = challenges[prefix].send(`/api/auth/password/${prefix}/email-code`, getLanguage());
      refresh(); await pending; refresh();
    });
    $(`password-${prefix}-form`).addEventListener('submit', async event => {
      event.preventDefault();
      if (busy[prefix]) return;
      const newPassword = $(`password-${prefix}-new`).value;
      const confirmPassword = $(`password-${prefix}-confirm`).value;
      if (newPassword !== confirmPassword) {errors[prefix] = 'auth_password_mismatch'; refresh(); return;}
      const method = prefix === 'reset' || $('password-email-option').checked ? 'email' : 'password';
      const proof = challenges[prefix].payload($(`password-${prefix}-code`).value);
      if (method === 'email' && !proof) {errors[prefix] = 'email_request_code'; refresh(); return;}
      const body = {new_password: newPassword, confirm_password: confirmPassword,
        ...(prefix === 'reset' ? {email: $('password-reset-email').value, ...proof}
          : {method, ...(method === 'email' ? proof : {current_password: $('password-current').value})})};
      const started = generation;
      busy[prefix] = true; errors[prefix] = ''; refresh();
      try {
        const send = prefix === 'reset' ? request : authenticatedRequest;
        const response = await send(`/api/auth/password/${prefix}`, {method: 'POST',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        const result = response.ok ? null : await response.json();
        if (started !== generation) return;
        if (response.ok) { reset(); onChanged(); }
        else errors[prefix] = authError(result.detail, response.status);
      } catch (error) {
        if (started === generation && error.name !== 'AbortError') errors[prefix] = 'auth_unavailable';
      } finally {
        if (started === generation) busy[prefix] = false;
        refresh();
      }
    });
  }
  return {reset, refresh};
}
