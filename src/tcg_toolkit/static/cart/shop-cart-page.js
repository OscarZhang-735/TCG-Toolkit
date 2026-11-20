// The shared cart page selects its store from the current route.
export const SHOP_CART_PAGES = {
  '#cart': {source: 'yuyutei', label: 'source_yuyutei', icon: './yuyutei-logo.webp',
    cartUrl: 'https://yuyu-tei.jp/cart/sell', needCredentials: 'cart_need_credentials'},
  '#cart-hobbystation': {source: 'hobbystation', label: 'source_hobbystation', icon: './source-hobbystation.png',
    cartUrl: 'https://www.hobbystation-single.jp/cart', needCredentials: 'hs_cart_need_credentials'},
  '#cart-cardmax': {source: 'cardmax', label: 'source_cardmax', icon: './source-cardmax.png',
    cartUrl: 'https://www.cardmax.jp/shop/basket.html', needCredentials: 'cm_cart_need_credentials'},
};
