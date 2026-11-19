import {createStorefrontClient} from './storefront-client.js';

const value = /^[A-Za-z0-9%+/_=.,@-]{1,512}$/;
const memberKey = /^[0-9]{1,32}_key=[A-Za-z0-9%+/_=.,@-]{1,512}$/;
export function cardmaxCookie(input, name) {
  let text = String(input ?? '').trim().replace(/;$/, '');
  if (name === 'member_name') return /^[0-9]{1,32}_key$/.test(text) ? text : null;
  if (name !== 'member_value' && text.startsWith(name + '=')) text = text.slice(name.length + 1);
  return value.test(text) ? text : null;
}

export function createCardmaxClient(options) {
  return createStorefrontClient({shop: 'cardmax', storageKey: 'tcg.cardmax.credentials', rowField: 'brandcode',
    credentialFields: {tempid: value, login_id: value, member_key: memberKey}}, options);
}

export const cardmaxClient = createCardmaxClient();
