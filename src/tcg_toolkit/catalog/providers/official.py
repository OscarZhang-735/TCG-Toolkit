"""Card metadata parsers for the official game catalogs."""
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

LYCEE_SEARCH = 'https://lycee-tcg.com/card/'


def parse_lycee(html, base=LYCEE_SEARCH, *, separate_rows=False):
    soup = BeautifulSoup(html, 'html.parser')
    items = []
    seen = set()
    for link in soup.select('a[href*="card_detail.pl?cardno="]'):
        number = parse_qs(urlsplit(link['href']).query).get('cardno', [''])[0]
        row = link.find_parent('tr')
        cells = row.find_all('td', recursive=False) if row else []
        name = link.get_text(' ', strip=True)
        rarity = cells[-1].get_text(strip=True) if cells else ''
        image = row.select_one('img[src]') if row else None
        image_url = urljoin(base, image['src']) if image else None
        if image_url and urlsplit(image_url).scheme not in ('http', 'https'):
            image_url = None
        if not number or not name or not rarity:
            raise ValueError('Invalid official card row')
        identity = id(row) if separate_rows else number
        if identity not in seen:
            items.append(dict(game='Lycee', card_number=number, name=name, rarity=rarity, image_url=image_url))
            seen.add(identity)
    if not items and not soup.select('input[name="word"]'):
        raise ValueError('Unrecognized official search page')
    return items


WS_SEARCH = 'https://ws-tcg.com/manage/CardListUser/searchJson'
WS_IMAGES = 'https://ws-tcg.com/wordpress/wp-content/images/cardlist/'


def parse_ws(data):
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise ValueError('Unrecognized official WS response')
    items = []
    for row in data['items']:
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k].strip()
                                           for k in ('card_number', 'card_name', 'rare')):
            raise ValueError('Invalid official WS card')
        picture = row.get('picture')
        image = urljoin(WS_IMAGES, picture) if isinstance(picture, str) and picture else None
        if image and not image.startswith(WS_IMAGES):
            image = None
        items.append(dict(game='WS', card_number=row['card_number'], name=row['card_name'],
                          rarity=row['rare'], image_url=image))
    return items


WSR_SEARCH = 'https://ws-rose.com/cardlist/searchresults/'
WSR_DETAIL = 'https://ws-rose.com/cardlist/'


def parse_wsr(html, *, fragment=False):
    soup = BeautifulSoup(html, 'html.parser')
    hits = soup.select_one('.search-Hits span')
    if not fragment and hits is None:
        raise ValueError('Unrecognized WSR search page')
    total = None if fragment else int(hits.get_text(strip=True).replace(',', ''))
    items = []
    selector = '.card-Item a[href]' if fragment else '.cardlist-Result_Item .card-Item a[href]'
    for link in soup.select(selector):
        number = parse_qs(urlsplit(link['href']).query).get('cardno', [''])[0]
        image = link.select_one('img[src]')
        if not number or image is None or not image.get('alt'):
            raise ValueError('Invalid WSR card row')
        image_url = urljoin(WSR_SEARCH, image['src'])
        if not image_url.startswith('https://ws-rose.com/'):
            image_url = None
        items.append(dict(game='WSR', card_number=number, name=image['alt'], image_url=image_url))
    if total and not items:
        raise ValueError('Missing WSR results')
    return items, total


def wsrose_rarity(html, number):
    soup = BeautifulSoup(html, 'html.parser')
    fields = {}
    for term in soup.select('dt'):
        value = term.find_next_sibling('dd')
        if value:
            fields[term.get_text(strip=True)] = value.get_text(strip=True)
    if fields.get('カード番号') != number or not fields.get('レアリティ'):
        raise ValueError('Invalid WSR detail')
    return fields['レアリティ']


PM_SEARCH = 'http://www.p-memories.com/card_product_list_page'


def parse_pm(html):
    soup = BeautifulSoup(html, 'html.parser')
    if not soup.select_one('input[name="keyword_card"]'):
        raise ValueError('Unrecognized PM search page')
    items = []
    for row in soup.select('table tbody tr'):
        link = row.select_one('a.cardPreview[href]')
        if link is None:
            continue
        cells = row.find_all('td', recursive=False)
        if len(cells) < 6:
            raise ValueError('Invalid PM card row')
        rarity, number, name = (cells[i].get_text(' ', strip=True) for i in (0, 1, 5))
        if not number or not name:
            raise ValueError('Missing PM card metadata')
        image = urljoin(PM_SEARCH, link['src']) if link.get('src') else None
        if image and urlsplit(image).scheme not in ('http', 'https'):
            image = None
        items.append(dict(game='PM', card_number=number, name=name, rarity=rarity,
                          image_url=image, source_url=urljoin(PM_SEARCH, link['href'])))
    return items


SEV_DETAIL = 'https://shadowverse-evolve.com/cardlist/'
SEV_EN_DETAIL = 'https://en.shadowverse-evolve.com/cards/'
SEV_EN_RARITIES = {'Bronze': 'B', 'Bronze Rare': 'B', 'Silver': 'S', 'Silver Rare': 'S',
                   'Gold': 'G', 'Gold Rare': 'G', 'Legendary': 'L', 'Ultimate Rare': 'U',
                   'Super Legendary': 'SL', 'Special': 'SP', 'Super Special': 'SSP',
                   'Premium': 'P'}


def _parse_sev_detail(html, number, *, english):
    game = 'SEV EN' if english else 'SEV'
    base = SEV_EN_DETAIL if english else SEV_DETAIL
    soup = BeautifulSoup(html, 'html.parser')
    detail = soup.select_one('.cardlist-Detail_Box_Inner')
    if detail is None:
        if soup.select_one('form#searchForm'):
            return []
        raise ValueError('Unrecognized SEV detail page')
    printed = detail.select_one('.illustrator .heading')
    if printed is None:
        raise ValueError('Missing SEV card number')
    if printed.get_text(strip=True) != number:
        return []
    name = detail.select_one('h1.ttl')
    image = detail.select_one('.img img[src]')
    fields = {dt.get_text(strip=True): dt.find_next_sibling('dd').get_text(strip=True)
              for dt in detail.select('dt') if dt.find_next_sibling('dd')}
    rarity = fields.get('Rarity' if english else 'レアリティ')
    if name is None or not name.get_text(strip=True) or not rarity or image is None:
        raise ValueError('Missing SEV metadata')
    image_url = urljoin(base, image['src'])
    if urlsplit(image_url).scheme not in ('http', 'https'):
        image_url = None
    return [dict(game=game, card_number=number, name=name.get_text(' ', strip=True),
                 rarity=SEV_EN_RARITIES.get(rarity, rarity) if english else rarity, image_url=image_url)]


def parse_sev_en(html, number):
    return _parse_sev_detail(html, number, english=True)


def parse_sev(html, number):
    return _parse_sev_detail(html, number, english=False)


WS_EN_SEARCH = 'https://en.ws-tcg.com/cardlist/searchresults/'


def parse_ws_en(html, word='', *, limit=5, fragment=False):
    soup = BeautifulSoup(html, 'html.parser')
    hits = soup.select_one('.c-search__results-item span')
    if not fragment and hits is None:
        raise ValueError('Unrecognized WS EN results')
    total = None if fragment else int(hits.get_text(strip=True).replace(',', ''))
    items = []
    links = [(parse_qs(urlsplit(link['href']).query).get('cardno', [''])[0], link)
             for link in soup.select('a[href*="?cardno="]')]
    if word:
        links.sort(key=lambda entry: entry[0].casefold() != word.casefold())
    for number, link in links[:limit]:
        name = link.select_one('.ttl')
        image = link.select_one('.image img[src]')
        fields = {dt.get_text(strip=True): dt.find_next_sibling('dd').get_text(strip=True)
                  for dt in link.select('dt') if dt.find_next_sibling('dd')}
        rarity = fields.get('Rarity')
        if not number or name is None or not name.get_text(strip=True) or image is None or not rarity:
            raise ValueError('Invalid WS EN card')
        image_url = urljoin(WS_EN_SEARCH, image['src'])
        if urlsplit(image_url).scheme not in ('http', 'https'):
            image_url = None
        items.append(dict(game='WS EN', card_number=number, name=name.get_text(' ', strip=True),
                          rarity=rarity, image_url=image_url))
    if total and not items:
        raise ValueError('Missing WS EN cards')
    return {'items': items, 'total': total}
