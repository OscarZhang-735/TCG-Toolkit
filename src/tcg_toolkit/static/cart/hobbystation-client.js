import {createStorefrontClient} from './storefront-client.js';

export const HOBBYSTATION_CREDENTIALS_KEY = 'tcg.hobbystation.credentials';
const VALUE = /^[A-Za-z0-9%+/_=.,-]{1,512}$/;
// Accepts the bare value or a pasted "name=value" pair, as copied from DevTools.
export function cookieValue(input, name) {
  const value = String(input ?? '').trim().replace(new RegExp(`^${name}=`), '').replace(/;$/, '');
  return VALUE.test(value) ? value : null;
}

export function createHobbystationClient(options) {
  return createStorefrontClient({shop: 'hobbystation', storageKey: HOBBYSTATION_CREDENTIALS_KEY,
    rowField: 'product_class_id', credentialFields: {eccube: VALUE, eccube_bot_check: VALUE}}, options);
}

export const hobbystationClient = createHobbystationClient();
