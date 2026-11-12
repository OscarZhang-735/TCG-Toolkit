import {apiRequest, getToken, setToken, TOKEN_KEY} from './api.js';
import {t, applyLanguage, setLanguage, getLanguage} from './i18n.js';
import {createAuthenticatedRequest} from './auth-client.js';
import {yuyuteiClient} from './yuyutei-client.js';
import {bindNavMenus, syncNavMenus} from './nav-menu.js';
import {createEmailChallenge, authError} from './email-code.js';
import {createPasswordUI} from './password-ui.js';
import {createSubscriptionSettingsUI} from './subscription-settings-ui.js';
import {createNotificationEmailUI} from './notification-email-ui.js';
import {createAvatarCropper, loadImage} from './avatar-crop-ui.js';
const $ = id => document.getElementById(id);
let user = null, searchApp = null, registering = false, errorKey = '';
let bindError = '', authGeneration = 0, forgotten = false;
let avatarBusy = false, avatarStatus = '', avatarCropError = '', avatarPicking = false;
let nicknameBusy = false, nicknameError = '';
const api = createAuthenticatedRequest(() => signedOut('auth_expired'), apiRequest);
const registrationEmail = createEmailChallenge({request: apiRequest});
const bindingEmail = createEmailChallenge({request: api.fetch});
const passwordUI = createPasswordUI({request: apiRequest, authenticatedRequest: api.fetch,
  getUser: () => user, onChanged: () => signedOut('password_changed')});
const subscriptionSettingsUI = createSubscriptionSettingsUI({request: api.fetch});
const notificationUI = createNotificationEmailUI({request: api.fetch, getUser: () => user, onChange: email => searchApp?.setNotificationEmail(email)});
const avatarCropper = createAvatarCropper({stage: $('avatar-crop-stage'), canvas: $('avatar-crop-canvas'), zoom: $('avatar-crop-zoom')});
const AVATAR_TYPES = ['image/jpeg', 'image/png', 'image/webp'], AVATAR_LIMIT = 5 * 1024 * 1024;
function renderEmail(prefix, challenge) {
  const state = challenge.snapshot();
  const button = $(`${prefix}-send-code`);
  button.disabled = state.busy || state.remaining > 0;
  button.textContent = t(state.busy ? 'email_sending' : state.remaining ? 'email_resend_in' : 'email_send', {seconds: state.remaining});
  $(`${prefix}-email-status`).textContent = state.errorKey ? t(state.errorKey)
    : state.expired ? t('email_code_expired') : state.challengeId ? t('email_sent') : '';
}
function refreshEmails() {
  passwordUI.refresh(); notificationUI.refresh(); subscriptionSettingsUI.refresh();
  renderEmail('auth', registrationEmail); renderEmail('bind', bindingEmail);
  const verified = Boolean(user?.email_verified);
  $('account-email').textContent = user?.email || t('email_bind_intro');
  $('account-email').classList.toggle('bound', Boolean(user?.email));
  $('account-email-state').classList.toggle('ok', verified);
  $('account-email-state-icon').className = `bi ${verified ? 'bi-check-circle-fill' : 'bi-dash-circle'}`;
  $('account-email-state-label').textContent = t(verified ? 'email_verified' : 'email_unbound');
  $('bind-email-form').hidden = !user || Boolean(user.email);
  $('bind-email-error').textContent = bindError ? t(bindError) : '';
}
function resetEmails() {
  passwordUI.reset(); notificationUI.reset();
  registrationEmail.reset(); bindingEmail.reset(); bindError = '';
  for (const id of ['auth-email', 'auth-code', 'bind-email', 'bind-code', 'bind-password']) $(id).value = '';
}
function showPage() {
  const cards = Boolean(user) && location.hash === '#cards';
  $('cards-panel').hidden = !cards;
  const favorites = Boolean(user) && location.hash === '#favorites';
  const cart = Boolean(user) && location.hash === '#cart';
  $('cart-panel').hidden = !cart;
  $('favorites-panel').hidden = !favorites;
  const account = Boolean(user) && location.hash === '#account';
  // Subscriptions may have changed elsewhere; refresh the next-check estimate.
  if (account && $('account-panel').hidden) subscriptionSettingsUI.reload();
  document.body.classList.toggle('signed-out', !user);
  $('auth-panel').hidden = Boolean(user);
  $('search').hidden = !user || account || favorites || cards || cart;
  $('account-panel').hidden = !account;
  $('user-bar').hidden = !user;
  $('app-nav').hidden = !user;
  $('app-settings').hidden = !user;
  document.querySelectorAll('#app-nav a, #account-link').forEach(a => {
    if (a.hash === (cards ? '#cards' : cart ? '#cart' : account ? '#account' : favorites ? '#favorites' : '#search')) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  syncNavMenus();
}
function refresh() {
  applyLanguage(document);
  renderNicknameEditor();
  renderAvatarUpload();
  syncNavMenus();  // page names in the dropdown toggles follow the language
  $('auth-language').value = getLanguage();
  $('auth-heading').textContent = t(forgotten ? 'password_reset_title' : registering ? 'auth_register' : 'auth_login');
  $('auth-form').hidden = forgotten;
  $('password-reset-form').hidden = !forgotten;
  $('auth-forgot').hidden = registering;
  $('auth-submit').textContent = t(registering ? 'auth_register' : 'auth_login');
  $('auth-switch').textContent = t(registering ? 'auth_have_account' : 'auth_need_account');
  $('auth-confirm-field').hidden = !registering;
  $('auth-confirm-password').required = registering;
  $('auth-confirm-password').type = $('auth-password').type;
  $('auth-identifier-label').textContent = t(registering ? 'auth_username' : 'auth_identifier');
  $('auth-username').maxLength = registering ? 32 : 320;
  if (registering) $('auth-username').setAttribute('pattern', '[A-Za-z0-9_\\-]+');
  else $('auth-username').removeAttribute('pattern');
  $('auth-email-fields').hidden = !registering;
  $('auth-email').required = registering; $('auth-code').required = registering;
  refreshEmails();
  updateConfirmState();
  $('auth-password').autocomplete = registering ? 'new-password' : 'current-password';
  $('auth-form').classList.toggle('registering', registering);
  for (const id of ['auth-required-note', 'auth-username-hint', 'auth-password-hint']) $(id).hidden = !registering;
  const visible = $('auth-password').type === 'text';
  $('password-toggle').setAttribute('aria-pressed', String(visible));
  $('password-toggle').setAttribute('aria-label', t(visible ? 'auth_hide_password' : 'auth_show_password'));
  $('password-toggle').title = $('password-toggle').getAttribute('aria-label');
  $('password-toggle').firstElementChild.className = `bi ${visible ? 'bi-eye-slash' : 'bi-eye'}`;
  $('auth-error').textContent = errorKey ? t(errorKey) : '';
  if (user) $('account-created').textContent = new Date(user.created_at * 1000).toLocaleDateString(getLanguage(), {dateStyle: 'long'});
}
function updateConfirmState() {
  const value = $('auth-confirm-password').value;
  const match = value === $('auth-password').value;
  $('confirm-state').className = `confirm-state bi ${!value ? '' : match ? 'bi-check-circle-fill match' : 'bi-x-circle mismatch'}`;
  if (value && !match) $('auth-confirm-password').setAttribute('aria-invalid', 'true');
  else $('auth-confirm-password').removeAttribute('aria-invalid');
}
function setAvatar(name, url) {
  const [first = ''] = name;
  const initial = first.toUpperCase();
  for (const id of ['user-avatar', 'account-avatar']) {
    const avatar = $(id);
    avatar.textContent = initial;
    if (!url) continue;
    const image = document.createElement('img');
    image.alt = '';
    image.referrerPolicy = 'no-referrer';
    image.addEventListener('load', () => {
      if (user?.avatar_url === url) avatar.replaceChildren(image);
    }, {once: true});
    image.src = url;
  }
}
function renderDisplayName() {
  const name = user?.nickname ?? user?.username ?? '';
  $('current-username').textContent = name;
  $('account-username').textContent = name;
  $('account-login-name').hidden = !user?.nickname;
  $('account-login-username').textContent = user?.username ?? '';
  setAvatar(name, user?.avatar_url);
}
function renderNicknameEditor() {
  $('nickname-input').disabled = nicknameBusy;
  $('nickname-cancel').disabled = nicknameBusy;
  $('nickname-close').disabled = nicknameBusy;
  $('nickname-save').disabled = !user || nicknameBusy;
  $('nickname-save').textContent = t(nicknameBusy ? 'nickname_saving' : 'nickname_save');
  $('nickname-error').textContent = nicknameError ? t(nicknameError) : '';
}
function resetNicknameEditor() {
  nicknameBusy = false; nicknameError = '';
  $('nickname-input').value = '';
  $('nickname-dialog').close();
}
function renderAvatarUpload() {
  $('avatar-upload').disabled = !user || avatarBusy || avatarPicking;
  $('avatar-upload').setAttribute('aria-busy', String(avatarBusy));
  $('avatar-upload-icon').className = avatarBusy ? 'bi bi-arrow-repeat spin' : 'bi bi-camera';
  $('avatar-status').textContent = avatarStatus ? t(avatarStatus) : '';
  for (const id of ['avatar-crop-confirm', 'avatar-crop-cancel', 'avatar-crop-close', 'avatar-crop-zoom']) $(id).disabled = avatarBusy;
  $('avatar-crop-confirm-label').textContent = t(avatarBusy ? 'avatar_uploading' : 'avatar_crop_confirm');
  $('avatar-crop-error').textContent = avatarCropError ? t(avatarCropError) : '';
}
function signedOut(message = '', clearToken = true) {
  forgotten = false;
  subscriptionSettingsUI.reset();
  authGeneration++; resetEmails();
  yuyuteiClient.deactivate({clear: clearToken});
  if (clearToken) setToken(null);
  api.invalidate(); searchApp?.clearUser(); user = null;
  avatarBusy = false; avatarStatus = ''; avatarCropError = ''; $('avatar-crop-dialog').close();
  registering = false; $('account-status').textContent = '';
  resetNicknameEditor(); renderDisplayName(); $('account-created').textContent = '';
  $('auth-password').value = ''; $('auth-confirm-password').value = ''; $('auth-password').type = 'password'; errorKey = message;
  location.hash = '#login'; showPage(); refresh();
}
async function signedIn(value) {
  const expectedToken = getToken();
  api.invalidate();
  searchApp ??= await import('./app.js');
  if (getToken() !== expectedToken) return;
  // The cart client must know the account before the search app reads its cart.
  user = value; yuyuteiClient.activateUser(user.id, api.fetch);
  avatarBusy = false; avatarStatus = '';
  authGeneration++; resetEmails();
  searchApp.activateUser(user.id, api.fetch, user.email_verified ? user.email : null);
  notificationUI.load(); subscriptionSettingsUI.activate();
  $('account-status').textContent = '';
  resetNicknameEditor(); renderDisplayName();
  $('auth-password').value = ''; $('auth-confirm-password').value = ''; $('auth-password').type = 'password'; errorKey = '';
  if (!['#account', '#favorites', '#cards', '#cart'].includes(location.hash)) location.hash = '#search';
  showPage(); refresh();
}
$('auth-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (registering && $('auth-password').value !== $('auth-confirm-password').value) { errorKey = 'auth_password_mismatch'; refresh(); return; }
  const challenge = registrationEmail.payload($('auth-code').value);
  if (registering && !challenge) { errorKey = 'email_request_code'; refresh(); return; }
  const started = authGeneration;
  $('auth-submit').disabled = true; $('auth-switch').disabled = true; $('auth-forgot').disabled = true;
  errorKey = ''; refresh();
  try {
    const response = await apiRequest(`/api/auth/${registering ? 'register' : 'login'}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: $('auth-username').value, password: $('auth-password').value, ...(registering ? {confirm_password: $('auth-confirm-password').value, email: $('auth-email').value, ...challenge} : {})})
    });
    const result = await response.json();
    if (started !== authGeneration) return;
    if (!response.ok) {errorKey = authError(result.detail, response.status); refresh(); return;}
    setToken(result.token); await signedIn(result.user);
  } catch { if (started === authGeneration) { errorKey = 'auth_unavailable'; refresh(); } }
  finally { $('auth-submit').disabled = false; $('auth-switch').disabled = false; $('auth-forgot').disabled = false; }
});
$('password-toggle').addEventListener('click', () => {
  $('auth-password').type = $('auth-password').type === 'password' ? 'text' : 'password'; refresh();
});
for (const id of ['auth-password', 'auth-confirm-password']) $(id).addEventListener('input', updateConfirmState);
$('auth-switch').addEventListener('click', () => {
  authGeneration++; resetEmails();
  registering = !registering; errorKey = ''; $('auth-password').value = ''; $('auth-confirm-password').value = ''; refresh();
});
$('auth-forgot').addEventListener('click', () => {
  authGeneration++; resetEmails(); forgotten = true; registering = false; errorKey = '';
  $('auth-password').value = ''; $('auth-confirm-password').value = ''; refresh();
  $('password-reset-email').focus();
});
$('password-reset-back').addEventListener('click', () => {
  authGeneration++; resetEmails(); forgotten = false; errorKey = ''; refresh();
});
for (const [prefix, challenge] of [['auth', registrationEmail], ['bind', bindingEmail]]) {
  $(`${prefix}-email`).addEventListener('input', () => {
    challenge.setEmail($(`${prefix}-email`).value); $(`${prefix}-code`).value = '';
    errorKey = ''; bindError = ''; refresh();
  });
  $(`${prefix}-send-code`).addEventListener('click', async () => {
    const email = $(`${prefix}-email`);
    if (!email.reportValidity() || !email.value) return;
    if (prefix === 'bind' && !$('bind-password').reportValidity()) return;
    challenge.setEmail(email.value);
    const promise = challenge.send(prefix === 'auth' ? '/api/auth/register/email-code' : '/api/auth/email/bind-code',
      getLanguage(), prefix === 'bind' ? {current_password: $('bind-password').value} : {});
    refreshEmails(); await promise; refreshEmails();
  });
}
$('bind-email-form').addEventListener('submit', async event => {
  event.preventDefault();
  const challenge = bindingEmail.payload($('bind-code').value);
  if (!challenge) {bindError = 'email_request_code'; refreshEmails(); return;}
  const started = authGeneration;
  $('bind-email-submit').disabled = true; bindError = ''; refreshEmails();
  try {
    const response = await api.fetch('/api/auth/email/bind', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(challenge)});
    const result = await response.json();
    if (started !== authGeneration) return;
    if (!response.ok) bindError = authError(result.detail, response.status);
    else {user = {...user, email: result.email, email_verified: result.email_verified}; resetEmails(); notificationUI.load();}
  } catch (error) {
    if (started === authGeneration && error.name !== 'AbortError') bindError = 'auth_unavailable';
  } finally { $('bind-email-submit').disabled = false; refreshEmails(); }
});
setInterval(refreshEmails, 1000);
$('nickname-edit').addEventListener('click', () => {
  if (!user || nicknameBusy) return;
  $('nickname-input').value = user.nickname ?? '';
  nicknameError = ''; renderNicknameEditor(); $('nickname-dialog').showModal();
});
for (const id of ['nickname-cancel', 'nickname-close']) $(id).addEventListener('click', () => { if (!nicknameBusy) $('nickname-dialog').close(); });
$('nickname-dialog').addEventListener('cancel', event => { if (nicknameBusy) event.preventDefault(); });
$('nickname-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!user || nicknameBusy) return;
  const nickname = $('nickname-input').value.trim();
  const started = authGeneration;
  nicknameBusy = true; nicknameError = ''; renderNicknameEditor();
  try {
    const response = await api.fetch('/api/auth/nickname', {method: 'PUT',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({nickname: nickname || null})});
    const result = await response.json();
    if (started !== authGeneration) return;
    if (response.ok) {
      user.nickname = result.nickname;
      renderDisplayName(); $('nickname-dialog').close();
    } else nicknameError = response.status === 422 ? 'nickname_invalid' : 'nickname_failed';
  } catch {
    if (started === authGeneration) nicknameError = 'nickname_failed';
  } finally {
    if (started === authGeneration) { nicknameBusy = false; renderNicknameEditor(); }
  }
});
$('avatar-upload').addEventListener('click', () => $('avatar-file').click());
$('avatar-file').addEventListener('change', async event => {
  const input = event.target, file = input.files[0];
  input.value = '';
  if (!user || !file || avatarBusy || avatarPicking) return;
  avatarStatus = !AVATAR_TYPES.includes(file.type) ? 'avatar_invalid' : file.size > AVATAR_LIMIT ? 'avatar_too_large' : '';
  if (avatarStatus) { renderAvatarUpload(); return; }
  const started = authGeneration;
  avatarPicking = true; renderAvatarUpload();
  try {
    const image = await loadImage(file);
    if (started !== authGeneration) return;
    avatarCropError = '';
    $('avatar-crop-dialog').showModal();
    avatarCropper.open(image);
    $('avatar-crop-stage').focus();
  } catch {
    if (started === authGeneration) avatarStatus = 'avatar_invalid';
  } finally { avatarPicking = false; renderAvatarUpload(); }
});
function closeAvatarCrop() { if (!avatarBusy) $('avatar-crop-dialog').close(); }
for (const id of ['avatar-crop-cancel', 'avatar-crop-close']) $(id).addEventListener('click', closeAvatarCrop);
$('avatar-crop-dialog').addEventListener('cancel', event => { if (avatarBusy) event.preventDefault(); });
$('avatar-crop-dialog').addEventListener('close', () => { avatarCropper.clear(); avatarCropError = ''; renderAvatarUpload(); $('avatar-upload').focus(); });
$('avatar-crop-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!user || avatarBusy) return;
  const started = authGeneration;
  avatarBusy = true; avatarCropError = ''; avatarStatus = ''; renderAvatarUpload();
  try {
    const blob = await avatarCropper.export();
    if (!blob || !AVATAR_TYPES.includes(blob.type)) throw new Error('export failed');
    const response = await api.fetch('/api/auth/avatar', {method: 'PUT', headers: {'Content-Type': blob.type}, body: blob});
    const result = await response.json();
    if (started !== authGeneration) return;
    if (response.ok) {
      user.avatar_url = result.avatar_url;
      renderDisplayName();
      avatarBusy = false; $('avatar-crop-dialog').close();
    } else {
      const errors = ['avatar_invalid', 'avatar_too_large', 'avatar_dimensions', 'avatar_storage_failed'];
      avatarCropError = response.status === 429 ? 'avatar_rate_limited' : errors.includes(result.detail) ? result.detail : 'avatar_failed';
    }
  } catch {
    if (started === authGeneration) avatarCropError = 'avatar_failed';
  } finally {
    if (started === authGeneration) { avatarBusy = false; renderAvatarUpload(); }
  }
});
$('logout').addEventListener('click', async () => {
  yuyuteiClient.clearCredentials();
  $('logout').disabled = true;
  api.invalidate(); searchApp?.clearUser();
  try {
    const response = await apiRequest('/api/auth/logout', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: '{}'});
    if (!response.ok) throw new Error('logout');
    signedOut();
  } catch { $('account-status').textContent = t('auth_logout_failed'); }
  finally { $('logout').disabled = false; }
});
$('auth-language').addEventListener('change', () => {setLanguage($('auth-language').value); refresh();});
$('language').addEventListener('change', () => {setLanguage($('language').value); refresh();});
window.addEventListener('hashchange', showPage);
bindNavMenus();
refresh(); showPage();
let restoreGeneration = 0;
async function restoreSession() {
  const generation = ++restoreGeneration;
  const token = getToken();
  const destination = location.hash;
  signedOut('', false);
  if (!token) { yuyuteiClient.deactivate(); $('auth-submit').disabled = false; return; }
  $('auth-submit').disabled = true;
  try {
    const response = await apiRequest('/api/auth/me', {cache: 'no-store'});
    const value = response.ok ? await response.json() : null;
    if (generation !== restoreGeneration || token !== getToken()) return;
    if (response.ok) { location.hash = destination; await signedIn(value); }
    else if (response.status === 401) signedOut();
    else {errorKey = 'auth_unavailable'; refresh();}
  } catch {if (generation === restoreGeneration) {errorKey = 'auth_unavailable'; refresh();}}
  finally {if (generation === restoreGeneration) $('auth-submit').disabled = false;}
}
window.addEventListener('storage', event => {
  if (event.key === TOKEN_KEY || event.key === null) {
    yuyuteiClient.deactivate();
    restoreSession();
  }
});
await restoreSession();
