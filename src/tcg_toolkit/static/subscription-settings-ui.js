import {t, getLanguage} from './i18n.js';

const PATH = '/api/subscriptions/settings';
const MAX_HOURS = 2147483647;
export const INTERVAL_PRESETS = [1, 3, 6, 12, 24, 72];

// "6 hours", "3 days": whole days read better once the interval spans them.
export function formatInterval(hours) {
  const days = hours % 24 === 0 ? hours / 24 : 0;
  const count = (days || hours).toLocaleString(getLanguage());
  if (days) return t(days === 1 ? 'interval_day_one' : 'interval_days', {count});
  return t(hours === 1 ? 'interval_hour_one' : 'interval_hours', {count});
}

export function intervalSummary(hours) {
  if (hours === 1) return t('interval_summary_hourly');
  if (hours === 24) return t('interval_summary_daily');
  return t('interval_summary', {interval: formatInterval(hours)});
}

// "UTC-5", "UTC+5:30": this machine's offset at that moment, so DST is reflected.
export function utcOffset(date) {
  const minutes = -date.getTimezoneOffset(), abs = Math.abs(minutes);
  const rest = abs % 60 ? `:${String(abs % 60).padStart(2, '0')}` : '';
  return `UTC${minutes < 0 ? '-' : '+'}${Math.floor(abs / 60)}${rest}`;
}

// "2026/11/11 22:00 (UTC-5)" in the browser's local time; seconds in.
export function formatNextCheck(seconds, now = Date.now()) {
  const date = new Date(seconds * 1000);
  if (date.getTime() <= now) return t('interval_next_soon');
  const pad = value => String(value).padStart(2, '0');
  return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())} (${utcOffset(date)})`;
}

export function createSubscriptionSettingsUI({request, root = document, now = () => Date.now()}) {
  const $ = id => root.getElementById(id);
  let generation = 0, ready = false, busy = false, open = false, loadFailed = false, error = '', status = '';
  let saved = null;  // {interval_hours, next_check_at} as last confirmed by the server.
  const draft = () => $('subscription-interval').value.trim();
  const dirty = () => Boolean(saved) && draft() !== String(saved.interval_hours);
  function render() {
    $('subscription-settings').classList.toggle('collapsed', !open);
    $('subscription-settings-form').hidden = !open;
    const toggle = $('subscription-settings-toggle');
    toggle.disabled = !ready;
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', t(open ? 'panel_collapse' : 'panel_expand'));
    toggle.title = toggle.getAttribute('aria-label');
    $('subscription-settings-toggle-icon').className = `bi ${open ? 'bi-chevron-up' : 'bi-chevron-down'}`;
    $('subscription-settings-retry').hidden = !loadFailed || busy;
    const summary = $('subscription-interval-summary');
    summary.textContent = saved ? intervalSummary(saved.interval_hours)
      : t(loadFailed ? 'interval_load_failed' : 'interval_loading');
    summary.classList.toggle('bound', Boolean(saved));
    const next = $('subscription-next-check'), due = saved?.next_check_at;
    next.hidden = !saved;
    next.classList.toggle('scheduled', due != null);
    $('subscription-next-check-label').textContent = !saved ? ''
      // Non-breaking spaces keep "2026/11/11 22:00 (UTC-5)" on one line.
      : due == null ? t('interval_next_none')
      : t('interval_next', {time: formatNextCheck(due, now()).replaceAll(' ', '\u00a0')});
    $('subscription-interval').disabled = busy || !ready;
    for (const hours of INTERVAL_PRESETS) {
      const preset = $(`subscription-interval-preset-${hours}`);
      preset.disabled = busy || !ready;
      preset.checked = draft() === String(hours);
      $(`subscription-interval-preset-${hours}-label`).textContent = formatInterval(hours);
    }
    $('subscription-settings-save').disabled = busy || !ready || !dirty();
    $('subscription-settings-save-label').textContent = t(busy && ready ? 'interval_saving' : 'interval_save');
    $('subscription-settings-error').textContent = error ? t(error) : '';
    $('subscription-settings-status').textContent = status ? t(status) : '';
  }
  function reset() {
    generation++; ready = false; busy = false; open = false; loadFailed = false;
    error = ''; status = ''; saved = null;
    $('subscription-interval').value = '';
    render();
  }
  // quiet: refresh the estimate on returning to the page, leaving an edited draft alone.
  async function load({quiet = false} = {}) {
    if (quiet && (!ready || busy || dirty())) return;
    const started = ++generation;
    if (!quiet) { ready = false; busy = true; loadFailed = false; error = ''; status = ''; render(); }
    try {
      const response = await request(PATH, {cache: 'no-store'});
      if (!response.ok) throw new Error('settings load failed');
      const result = await response.json();
      if (started !== generation || (quiet && dirty())) return;
      saved = result;
      $('subscription-interval').value = String(result.interval_hours);
      ready = true;
    } catch (failure) {
      if (!quiet && started === generation && failure.name !== 'AbortError') loadFailed = true;
    } finally {
      if (started === generation) { busy = false; render(); }
    }
  }
  function validate(value) {
    if (!/^\d+$/.test(value) || Number(value) < 1) return 'interval_invalid';
    return Number(value) > MAX_HOURS ? 'interval_too_long' : '';
  }
  $('subscription-settings-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!ready || busy) return;
    const value = draft();
    error = validate(value); status = '';
    if (error) { render(); return; }
    const started = ++generation;
    busy = true; render();
    try {
      const response = await request(PATH, {method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({interval_hours: Number(value)})});
      const result = await response.json();
      if (started !== generation) return;
      if (!response.ok) { error = response.status === 422 ? 'interval_invalid' : 'interval_save_failed'; return; }
      saved = result;
      $('subscription-interval').value = String(result.interval_hours);
      status = 'interval_saved';
    } catch (failure) {
      if (started === generation && failure.name !== 'AbortError') error = 'interval_save_failed';
    } finally {
      if (started === generation) { busy = false; render(); }
    }
  });
  $('subscription-settings-toggle').addEventListener('click', () => { open = !open; render(); });
  $('subscription-settings-retry').addEventListener('click', () => load());
  $('subscription-interval').addEventListener('input', () => { error = ''; status = ''; render(); });
  for (const hours of INTERVAL_PRESETS) {
    $(`subscription-interval-preset-${hours}`).addEventListener('change', () => {
      $('subscription-interval').value = String(hours); error = ''; status = ''; render();
    });
  }
  return {refresh: render, reset, load, reload: () => load({quiet: true}), activate() { reset(); return load(); }};
}
