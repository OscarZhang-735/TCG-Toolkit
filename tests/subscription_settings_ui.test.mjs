import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';

const asModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const i18n = `export const getLanguage = () => 'en';
export const t = (key, values = {}) => Object.keys(values).length ? key + JSON.stringify(values) : key;`;
const source = (await readFile(new URL('../src/tcg_toolkit/static/subscription-settings-ui.js', import.meta.url), 'utf8'))
  .replace("'./i18n.js'", JSON.stringify(asModule(i18n)));
const {createSubscriptionSettingsUI, formatInterval, formatNextCheck, intervalSummary, utcOffset, INTERVAL_PRESETS} = await import(asModule(source));
const reply = (status, body) => ({status, ok: status < 300, json: async () => body});
const NOW = new Date(2026, 10, 11, 21, 39).getTime();
const at = (...parts) => new Date(2026, ...parts).getTime() / 1000;

function setup(handler) {
  const fields = new Map(), calls = [];
  const $ = id => {
    if (!fields.has(id)) {
      const classes = new Set(), attributes = new Map();
      fields.set(id, {value: '', disabled: false, hidden: false, checked: false, textContent: '', title: '', listeners: {},
        classList: {toggle(name, on) { on ? classes.add(name) : classes.delete(name); }, contains: name => classes.has(name)},
        setAttribute(name, value) { attributes.set(name, value); }, getAttribute: name => attributes.get(name),
        addEventListener(name, listener) { this.listeners[name] = listener; }});
    }
    return fields.get(id);
  };
  const ui = createSubscriptionSettingsUI({root: {getElementById: $}, now: () => NOW,
    request: async (path, options = {}) => {
      calls.push([path, options.method || 'GET', options.body ? JSON.parse(options.body) : null]);
      return handler(path, options);
    }});
  const fire = (id, event = 'click') => $(id).listeners[event]({preventDefault() {}});
  const type = value => { $('subscription-interval').value = value; fire('subscription-interval', 'input'); };
  return {ui, $, calls, fire, type};
}

test('intervals and next-check times read naturally', () => {
  assert.equal(formatInterval(1), 'interval_hour_one{"count":"1"}');
  assert.equal(formatInterval(6), 'interval_hours{"count":"6"}');
  assert.equal(formatInterval(24), 'interval_day_one{"count":"1"}');
  assert.equal(formatInterval(72), 'interval_days{"count":"3"}');
  assert.equal(formatInterval(25), 'interval_hours{"count":"25"}');
  assert.equal(intervalSummary(1), 'interval_summary_hourly');
  assert.equal(intervalSummary(24), 'interval_summary_daily');
  assert.equal(intervalSummary(48), 'interval_summary{"interval":"interval_days{\\"count\\":\\"2\\"}"}');
  assert.equal(formatNextCheck(at(10, 11, 22), NOW), `2026/11/11 22:00 (${utcOffset(new Date(2026, 10, 11, 22))})`);
  assert.match(formatNextCheck(at(10, 12, 1), NOW), /^2026\/11\/12 01:00 \(UTC[+-]\d+(:\d\d)?\)$/);
  assert.match(formatNextCheck(new Date(2027, 0, 2, 9, 5) / 1000, NOW), /^2027\/01\/02 09:05 /);
  assert.equal(utcOffset({getTimezoneOffset: () => 300}), 'UTC-5');
  assert.equal(utcOffset({getTimezoneOffset: () => 240}), 'UTC-4');
  assert.equal(utcOffset({getTimezoneOffset: () => -480}), 'UTC+8');
  assert.equal(utcOffset({getTimezoneOffset: () => -330}), 'UTC+5:30');
  assert.equal(utcOffset({getTimezoneOffset: () => 210}), 'UTC-3:30');
  assert.equal(utcOffset({getTimezoneOffset: () => 0}), 'UTC+0');
  assert.equal(formatNextCheck(at(10, 11, 21), NOW), 'interval_next_soon');
});

test('loads the interval with its estimate, starts collapsed and saves through the settings endpoint', async () => {
  const {ui, $, calls, fire, type} = setup(async (_, {method}) => reply(200, method === 'PUT'
    ? {interval_hours: 6, next_check_at: at(10, 12, 1)} : {interval_hours: 24, next_check_at: at(10, 12, 19)}));
  await ui.activate();
  assert.equal($('subscription-interval').value, '24');
  assert.equal($('subscription-interval-summary').textContent, 'interval_summary_daily');
  assert.match($('subscription-next-check-label').textContent, /^interval_next\{"time":"2026\/11\/12\s19:00\s\(UTC[+-]\d/);
  assert.equal($('subscription-next-check').hidden, false);
  assert.equal($('subscription-settings-form').hidden, true);
  assert.equal($('subscription-settings').classList.contains('collapsed'), true);
  assert.equal($('subscription-interval-preset-24').checked, true);
  assert.equal($('subscription-settings-save').disabled, true, 'nothing to save yet');
  fire('subscription-settings-toggle');
  assert.equal($('subscription-settings-form').hidden, false);
  assert.equal($('subscription-settings-toggle').getAttribute('aria-expanded'), 'true');
  fire('subscription-interval-preset-6', 'change');
  assert.equal($('subscription-interval').value, '6');
  assert.equal($('subscription-interval-preset-6').checked, true);
  assert.equal($('subscription-interval-preset-24').checked, false);
  assert.equal($('subscription-settings-save').disabled, false);
  await fire('subscription-settings-form', 'submit');
  assert.deepEqual(calls, [['/api/subscriptions/settings', 'GET', null],
    ['/api/subscriptions/settings', 'PUT', {interval_hours: 6}]]);
  assert.equal($('subscription-settings-status').textContent, 'interval_saved');
  assert.equal($('subscription-interval-summary').textContent, 'interval_summary{"interval":"interval_hours{\\"count\\":\\"6\\"}"}');
  assert.match($('subscription-next-check-label').textContent, /2026\/11\/12\s01:00\s\(UTC/);
  assert.equal($('subscription-settings-save').disabled, true);
  type('7');
  assert.equal($('subscription-settings-status').textContent, '');
  assert.equal(INTERVAL_PRESETS.some(hours => $(`subscription-interval-preset-${hours}`).checked), false);
});

test('accounts without subscriptions get a hint instead of a time', async () => {
  const {ui, $} = setup(async () => reply(200, {interval_hours: 1, next_check_at: null}));
  await ui.activate();
  assert.equal($('subscription-interval-summary').textContent, 'interval_summary_hourly');
  assert.equal($('subscription-next-check-label').textContent, 'interval_next_none');
});

test('invalid hours are rejected before requesting; failures keep the draft', async () => {
  const {ui, $, calls, fire, type} = setup(async (_, {method}) => method === 'PUT'
    ? reply(500, {}) : reply(200, {interval_hours: 24, next_check_at: null}));
  await ui.activate();
  for (const [value, key] of [['', 'interval_invalid'], ['0', 'interval_invalid'], ['-1', 'interval_invalid'],
    ['1.5', 'interval_invalid'], ['1e3', 'interval_invalid'], ['2147483648', 'interval_too_long']]) {
    type(value);
    await fire('subscription-settings-form', 'submit');
    assert.equal($('subscription-settings-error').textContent, key, value);
  }
  assert.equal(calls.length, 1);
  type('6');
  assert.equal($('subscription-settings-error').textContent, '', 'typing clears the error');
  await fire('subscription-settings-form', 'submit');
  assert.equal($('subscription-settings-error').textContent, 'interval_save_failed');
  assert.equal($('subscription-interval').value, '6');
  assert.equal($('subscription-settings-save').disabled, false);
  assert.equal($('subscription-interval-summary').textContent, 'interval_summary_daily', 'summary keeps the saved value');
});

test('failed loads disable editing and can be retried', async () => {
  let failed = true;
  const {ui, $, fire} = setup(async () => failed ? reply(500, {}) : reply(200, {interval_hours: 3, next_check_at: null}));
  await ui.activate();
  assert.equal($('subscription-settings-save').disabled, true);
  assert.equal($('subscription-settings-toggle').disabled, true);
  assert.equal($('subscription-settings-retry').hidden, false);
  assert.equal($('subscription-interval-summary').textContent, 'interval_load_failed');
  assert.equal($('subscription-next-check').hidden, true);
  failed = false;
  await fire('subscription-settings-retry');
  assert.equal($('subscription-interval').value, '3');
  assert.equal($('subscription-settings-retry').hidden, true);
  assert.equal($('subscription-settings-toggle').disabled, false);
});

test('returning to the page refreshes the estimate without touching an edited draft', async () => {
  let next = at(10, 12, 19);
  const {ui, $, calls, type} = setup(async () => reply(200, {interval_hours: 24, next_check_at: next}));
  await ui.activate();
  next = null;
  await ui.reload();
  assert.equal($('subscription-next-check-label').textContent, 'interval_next_none');
  type('6');
  await ui.reload();
  assert.equal(calls.length, 2, 'a dirty draft skips the refresh');
  assert.equal($('subscription-interval').value, '6');
});

test('account switches discard stale load and save responses and block duplicate submissions', async () => {
  let resolve;
  const {ui, $, calls, fire, type} = setup(() => new Promise(r => { resolve = r; }));
  const load = ui.activate();
  ui.reset(); resolve(reply(200, {interval_hours: 6, next_check_at: null})); await load;
  assert.equal($('subscription-interval').value, '');
  const next = ui.activate(); resolve(reply(200, {interval_hours: 24, next_check_at: null})); await next;
  type('1');
  const save = fire('subscription-settings-form', 'submit');
  assert.equal($('subscription-interval').disabled, true);
  assert.equal($('subscription-interval-preset-1').disabled, true);
  await fire('subscription-settings-form', 'submit');
  assert.equal(calls.length, 3);
  ui.reset(); resolve(reply(200, {interval_hours: 1, next_check_at: null})); await save;
  assert.equal($('subscription-interval').value, '');
  assert.equal($('subscription-settings-status').textContent, '');
  assert.equal($('subscription-interval-summary').textContent, 'interval_loading');
});
