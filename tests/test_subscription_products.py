import httpx
import pytest

from tcg_toolkit.catalog.errors import ProductSourceError
from tcg_toolkit.subscriptions.products import fetch_product_snapshot


HS = 'https://www.hobbystation-single.jp/lo/product/detail/305273'
CM = 'https://www.cardmax.jp/shopdetail/000000160917'


def fetch(game, url, html):
    def serve(request):
        assert str(request.url) == url and request.method == 'GET'
        assert 'cookie' not in request.headers
        if url == CM:
            return httpx.Response(200, content=html.encode('euc_jis_2004'),
                                  headers={'Content-Type': 'text/html; charset=EUC-JP'})
        return httpx.Response(200, text=html)
    return fetch_product_snapshot(game, url + '/', lambda: httpx.Client(transport=httpx.MockTransport(serve)))


@pytest.mark.parametrize('stock,status,quantity', [
    ('<dd class="stock">在庫数：◎</dd>', 'in_stock', None),
    ('<dd class="stock">在庫数：3</dd>', 'in_stock', 3),
    ('<dd><img alt="SOLD OUT"></dd>', 'out_of_stock', 0),
])
def test_hobby_subscription_offer(stock, status, quantity):
    html = f'''<div class="detailWrap"><div class="sliderBox"><img src="/lo.png"></div><div class="detail">
        <div>LO-LO-4899-K</div><div class="itemName"><h2>式部 茉優</h2></div>
        <dl class="price"><dd><span>2,580円</span></dd>{stock}</dl></div></div>
        <dl class="price"><dd><span>1円</span></dd><img alt="SOLD OUT"></dl>'''
    item = fetch('lycee', HS, html)
    assert (item['card_number'], item['price'], item['stock_status'], item['stock_quantity']) == (
        'LO-4899-K', 2580, status, quantity)
    assert item['source'] == 'hobbystation' and item['product_url'] == HS


@pytest.mark.parametrize('stock,status,quantity', [('smallstock">残りあと5個', 'in_stock', 5),
    ('stock">在庫あり', 'in_stock', None), ('soldout">売り切れ', 'out_of_stock', 0)])
def test_cardmax_subscription_offer(stock, status, quantity):
    html = f'''<link rel="canonical" href="{CM}/"><meta property="og:image" content="/img.jpg">
        <div class="product-info"><h2 class="M_title">商品詳細</h2><h2>(GCR) 椎名立希(PR)(BD/W125-P05S)◇PR【WS】</h2></div>
        <div id="M_itemInfo"><div class="detail-info"><dl><dt>レアリティ</dt><dd>PR</dd></dl></div>
        <div class="price"><span>販売価格：</span><span>￥1,220<input name="price1" value="1109">（税込）</span></div>
        <span class="M_item-stock-{stock}</span></div>'''
    item = fetch('ws', CM, html)
    assert (item['name'], item['card_number'], item['rarity'], item['price']) == ('椎名立希', 'BD/W125-P05S', 'PR', 1220)
    assert (item['stock_status'], item['stock_quantity'], item['source']) == (status, quantity, 'cardmax')


@pytest.mark.parametrize('game,url', [('ws', CM), ('lycee', HS)])
def test_missing_offer_not_reported_as_zero(game, url):
    with pytest.raises(ProductSourceError, match='product_unreadable'):
        fetch(game, url, '<main>unavailable</main>')
