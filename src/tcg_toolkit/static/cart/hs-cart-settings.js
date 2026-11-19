import {bindStorefrontSettings} from './storefront-settings.js';
import {cookieValue} from './hobbystation-client.js';

// Server error type → message key; anything else is a passing failure worth retrying.
export function hobbystationErrorKey(error) {
  if (error?.message === 'hobbystation_credentials_missing') return 'hs_cart_need_credentials';
  if (error?.detail === 'invalid_hobbystation_request') return 'hs_cart_invalid_input';
  return ({hobbystation_credentials_rejected: 'hs_cart_credentials_rejected',
    hobbystation_timeout: 'hs_cart_timeout'})[error?.detail?.type] ?? 'hs_cart_failed';
}

export function bindHobbystationSettings(client, options) {
  return bindStorefrontSettings(client, {prefix: 'hs', fields: [
    ['eccube', 'eccube', cookieValue], ['bot-check', 'eccube_bot_check', cookieValue]],
    keys: {verified: 'hs_cart_verified', verifying: 'hs_cart_verifying', rejected: 'hs_cart_credentials_rejected',
      invalid: 'hs_cart_invalid_input'}, errorKey: hobbystationErrorKey}, options);
}
