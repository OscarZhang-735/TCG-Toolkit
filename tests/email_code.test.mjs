import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
const source = await readFile(new URL('../src/tcg_toolkit/static/email-code.js', import.meta.url), 'utf8');
const {createEmailChallenge, authError} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const response = (body, status = 200, retry = null) => ({ok: status === 200, status,
  json: async () => body, headers: {get: () => retry}});
const sent = {challenge_id: 'challenge', expires_in: 600, retry_after: 60};

test('sending, cooldown, expiry and submission use the same challenge', async () => {
  let now = 1000, calls = 0, finish;
  const challenge = createEmailChallenge({now: () => now, request: async (path, options) => {
    calls++;
    assert.equal(path, '/api/auth/register/email-code');
    assert.deepEqual(JSON.parse(options.body), {email: 'a@example.com', language: 'zh-CN'});
    return new Promise(resolve => {finish = resolve;});
  }});
  challenge.setEmail('a@example.com');
  const pending = challenge.send('/api/auth/register/email-code', 'zh-CN');
  assert.equal(challenge.snapshot().busy, true);
  await challenge.send('/api/auth/register/email-code', 'en');
  assert.equal(calls, 1);
  finish(response(sent)); await pending;
  assert.equal(challenge.snapshot().remaining, 60);
  assert.deepEqual(challenge.payload('012345'), {challenge_id: 'challenge', code: '012345'});
  await challenge.send('/api/auth/register/email-code', 'en');
  assert.equal(calls, 1);
  now += 60000;
  assert.equal(challenge.snapshot().remaining, 0);
  now += 540000;
  assert.equal(challenge.snapshot().expired, true);
  assert.equal(challenge.payload('012345'), null);
});

test('email edit or sign-out drops pending responses and credentials', async () => {
  for (const action of ['edit', 'logout']) {
    let finish;
    const challenge = createEmailChallenge({request: () => new Promise(resolve => {finish = resolve;})});
    challenge.setEmail('old@example.com');
    const pending = challenge.send('/send', 'en', {current_password: 'private-password'});
    if (action === 'edit') challenge.setEmail('new@example.com');
    else challenge.reset();
    finish(response(sent)); await pending;
    assert.equal(challenge.payload('012345'), null);
    assert.equal(challenge.snapshot().busy, false);
    assert.equal(challenge.snapshot().email, action === 'edit' ? 'new@example.com' : '');
    assert.ok(!JSON.stringify(challenge.snapshot()).includes('private-password'));
  }
});

test('failed resend preserves the old usable code; rate limit exposes countdown', async () => {
  let now = 0, next = response(sent);
  const challenge = createEmailChallenge({now: () => now, request: async () => next});
  challenge.setEmail('a@example.com'); await challenge.send('/send', 'en');
  now += 60000;
  next = response({detail: 'email_send_failed'}, 503);
  await challenge.send('/send', 'en');
  assert.equal(challenge.snapshot().errorKey, 'email_send_failed');
  assert.equal(challenge.payload('012345').challenge_id, 'challenge');
  next = response({detail: 'rate_limited'}, 429, '3300');
  await challenge.send('/send', 'en');
  assert.equal(challenge.snapshot().remaining, 3300);
});

test('resend updates challenge; network failures are retryable', async () => {
  let now = 0, fail = true, id = 'first';
  const challenge = createEmailChallenge({now: () => now, request: async () => {
    if (fail) throw new TypeError('network');
    return response({...sent, challenge_id: id});
  }});
  await challenge.send('/send', 'en');
  assert.equal(challenge.snapshot().errorKey, 'auth_unavailable');
  assert.equal(challenge.snapshot().busy, false);
  fail = false; await challenge.send('/send', 'en');
  now += 60000; id = 'second'; await challenge.send('/send', 'zh-CN');
  assert.equal(challenge.payload('012345').challenge_id, 'second');
});

test('error keys remain translatable after a language switch', async () => {
  const locales = await readFile(new URL('../src/tcg_toolkit/static/locales.js', import.meta.url), 'utf8');
  const {messages} = await import(`data:text/javascript;base64,${Buffer.from(locales).toString('base64')}`);
  for (const detail of ['email_taken', 'email_already_bound', 'invalid_password', 'invalid_code',
    'code_expired', 'code_attempts_exhausted', 'email_unavailable', 'email_send_failed']) {
    const key = authError(detail, 400);
    assert.ok(messages.en[key]); assert.ok(messages['zh-CN'][key]);
    assert.notEqual(messages.en[key], messages['zh-CN'][key]);
  }
  for (const key of ['email_sending', 'email_resend_in', 'email_sent', 'email_request_code']) {
    assert.ok(messages.en[key]); assert.ok(messages['zh-CN'][key]);
  }
});
