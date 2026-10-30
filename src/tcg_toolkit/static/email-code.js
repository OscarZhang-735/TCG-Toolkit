// Email challenges stay in memory; resetting invalidates responses from older forms/accounts.
export function authError(detail, status) {
  return ({username_taken: 'auth_taken', email_taken: 'email_taken',
    email_already_bound: 'email_already_bound', invalid_password: 'email_invalid_password',
    invalid_code: 'email_invalid_code', code_expired: 'email_code_expired',
    code_attempts_exhausted: 'email_code_exhausted', email_unavailable: 'email_unavailable',
    email_send_failed: 'email_send_failed'})[detail]
    || ({401: 'auth_invalid', 409: 'auth_taken', 422: 'auth_validation',
      429: 'auth_limited', 403: 'auth_origin'})[status] || 'auth_unavailable';
}

export function createEmailChallenge({request, onChange = () => {}, now = Date.now}) {
  let generation = 0;
  let state;
  function reset(email = '') {
    generation++;
    state = {email, challengeId: '', expiresAt: 0, retryAt: 0, busy: false, errorKey: ''};
    onChange();
  }
  reset();
  return {
    reset,
    setEmail(email) { if (email !== state.email) reset(email); },
    snapshot() {
      return {...state, remaining: Math.max(0, Math.ceil((state.retryAt - now()) / 1000)),
        expired: Boolean(state.challengeId) && state.expiresAt <= now()};
    },
    payload(code) {
      if (!state.challengeId || state.expiresAt <= now()) return null;
      return {challenge_id: state.challengeId, code};
    },
    async send(path, language, extra = {}) {
      if (state.busy || state.retryAt > now()) return;
      const started = generation;
      state.busy = true; state.errorKey = ''; onChange();
      try {
        const response = await request(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({email: state.email, language, ...extra})});
        const result = await response.json();
        if (started !== generation) return;
        if (!response.ok) {
          state.errorKey = authError(result.detail, response.status);
          const retry = Number(response.headers.get('Retry-After'));
          if (retry > 0) state.retryAt = now() + retry * 1000;
          return;
        }
        state.challengeId = result.challenge_id;
        state.expiresAt = now() + result.expires_in * 1000;
        state.retryAt = now() + result.retry_after * 1000;
      } catch (error) {
        if (started === generation && error.name !== 'AbortError') state.errorKey = 'auth_unavailable';
      } finally {
        if (started === generation) { state.busy = false; onChange(); }
      }
    }
  };
}
