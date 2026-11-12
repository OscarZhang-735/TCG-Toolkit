from tcg_toolkit.db_models import OwnedCard
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from auth_helpers import register_user

import pytest
from fastapi.testclient import TestClient

from tcg_toolkit.auth.store import UserStore
from tcg_toolkit.cards import EXPORT_COLUMNS, XLSX_MEDIA_TYPE
from tcg_toolkit.web import app

ORIGIN = 'https://testserver'
CARD = dict(game='ws', card_number='ABC-001', rarity='SR', name='Card', quantity=2, notes='Mine')


def exported(row, group=None):
    """Export record of an API card; group is its group name (None when ungrouped)."""
    return {key: group if key == 'group' else row[key] for key in EXPORT_COLUMNS}


def spreadsheet_rows(content):
    """Sheet values by row; cells the file leaves out (empty values) read as None."""
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(BytesIO(content)) as archive:
        strings = ET.fromstring(archive.read('xl/sharedStrings.xml'))
        texts = [''.join(item.itertext()) for item in strings]
        sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
    assert sheet.findall('.//s:f', ns) == []

    def column(reference):
        index = 0
        for letter in reference.rstrip('0123456789'):
            index = index * 26 + ord(letter) - 64
        return index - 1
    rows = [{column(cell.get('r')): texts[int(cell.find('s:v', ns).text)] if cell.get('t') == 's'
             else float(cell.find('s:v', ns).text) for cell in row}
            for row in sheet.findall('s:sheetData/s:row', ns)]
    width = max((max(row, default=-1) + 1 for row in rows), default=0)
    return [[row.get(index) for index in range(width)] for row in rows]


@pytest.mark.parametrize('format', ['json', 'xlsx'])
def test_export_empty_and_private(client, format):
    response = client.get('/api/cards/export', params={'format': format})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['content-disposition'] == f'attachment; filename="cards.{format}"'
    if format == 'json':
        assert response.json() == {'items': []}
    else:
        assert len(spreadsheet_rows(response.content)) == 1
    client.post('/api/cards', json=CARD)
    login(client, 'bob')
    response = client.get('/api/cards/export', params={'format': format})
    assert (response.json()['items'] if format == 'json' else spreadsheet_rows(response.content)[1:]) == []
    client.headers.pop('Authorization')
    assert client.get('/api/cards/export', params={'format': format}).status_code == 401


def test_export_round_trip(client):
    saved = client.post('/api/cards', json={**CARD, 'card_number': '00123',
        'name': '中文 カード', 'notes': '=1+1\n备注 & <text>',
        'quantity': 9007199254740991, 'image_url': 'https://example.com/card.png'}).json()
    zero = client.post('/api/cards', json={**CARD, 'quantity': 0}).json()
    saved, zero = (exported(row) for row in (saved, zero))
    assert client.get('/api/cards/export?format=json').json() == {'items': [zero, saved]}
    response = client.get('/api/cards/export?format=xlsx')
    assert response.status_code == 200
    assert 'spreadsheetml' in response.headers['content-type']
    rows = spreadsheet_rows(response.content)
    result = [dict(zip(rows[0], row)) for row in rows[1:]]
    assert result == [zero, {**saved, 'quantity': str(saved['quantity'])}]
    assert client.get('/api/cards/export?format=csv').status_code == 422


def test_export_long_text_offers_lossless_json(client):
    saved = client.post('/api/cards', json={**CARD, 'notes': '长' * 32768}).json()
    response = client.get('/api/cards/export?format=xlsx')
    assert response.status_code == 422
    assert response.json()['detail'] == 'export_xlsx_text_too_long'
    assert client.get('/api/cards/export?format=json').json()['items'] == [
        exported(saved)]


def test_export_language_fields_and_games(client):
    ws = client.post('/api/cards', json=CARD).json()
    client.post('/api/cards', json={**CARD, 'game': 'pm'})
    sev = client.post('/api/cards', json={**CARD, 'game': 'sev en'}).json()
    params = {'format': 'json', 'games': ['ws', 'SEV EN'], 'fields': ['quantity', 'name']}
    assert client.get('/api/cards/export', params=params).json() == {'items': [
        {'name': sev['name'], 'quantity': sev['quantity']}, {'name': ws['name'], 'quantity': ws['quantity']}]}
    response = client.get('/api/cards/export', params={**params, 'format': 'xlsx', 'language': 'zh-CN'})
    assert spreadsheet_rows(response.content) == [['名称', '拥有量'], ['Card', 2], ['Card', 2]]
    with ZipFile(BytesIO(response.content)) as archive:
        assert '卡牌' in archive.read('xl/workbook.xml').decode()
    english = client.get('/api/cards/export', params={'format': 'xlsx', 'language': 'en', 'fields': 'card_number'})
    assert spreadsheet_rows(english.content) == [['Number'], ['ABC-001'], ['ABC-001'], ['ABC-001']]
    assert client.get('/api/cards/export', params={'format': 'json', 'games': 'other'}).json() == {'items': []}
    for bad in [{'fields': 'password'}, {'language': 'fr'}]:
        assert client.get('/api/cards/export', params={'format': 'xlsx', **bad}).status_code == 422


@pytest.mark.parametrize('format,media_type', [('json', 'application/json'), ('xlsx', XLSX_MEDIA_TYPE)])
def test_import_exported_cards_into_current_account(client, format, media_type):
    saved = client.post('/api/cards', json={**CARD, 'card_number': '00123',
        'quantity': 9007199254740991, 'notes': '=1+1\n备注',
        'image_url': 'https://example.com/card.png'}).json()
    exported = client.get('/api/cards/export', params={'format': format}).content
    login(client, 'bob')
    path = f'/api/cards/import?format={format}'
    headers = {'Content-Type': media_type}
    response = client.post(path, content=exported, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {'imported': 1, 'skipped': 0}
    [imported] = client.get('/api/cards').json()['items']
    assert imported['id'] != saved['id']
    assert {key: imported[key] for key in CARD | {'image_url': ''}} == {
        key: saved[key] for key in CARD | {'image_url': ''}}
    client.put('/api/cards/' + imported['id'], json={**imported, 'quantity': 0})
    assert client.post(path, content=exported, headers=headers).json() == {'imported': 0, 'skipped': 1}
    assert client.get('/api/cards').json()['items'][0]['quantity'] == 0


def test_import_rejects_invalid_rows_without_writing_any_cards(client):
    path = '/api/cards/import?format=json'
    response = client.post(path, json={'items': [CARD, {**CARD, 'name': ''}]})
    assert response.status_code == 422
    assert response.json()['detail'] == {'code': 'cards_import_row', 'row': 2}
    assert client.get('/api/cards').json() == {'items': []}
    assert client.post(path, json={'items': [CARD]}, headers={'Origin': 'https://evil.test'}).status_code == 403
    client.headers.pop('Authorization')
    assert client.post(path, json={'items': [CARD]}).status_code == 401


@pytest.mark.parametrize('language', ['zh-CN', 'en'])
def test_xlsx_import_reads_localized_headings(client, language):
    client.post('/api/cards', json=CARD)
    exported = client.get('/api/cards/export', params={'format': 'xlsx', 'language': language}).content
    login(client, 'bob')
    response = client.post('/api/cards/import?format=xlsx', content=exported, headers={'Content-Type': XLSX_MEDIA_TYPE})
    assert response.json() == {'imported': 1, 'skipped': 0}
    [row] = client.get('/api/cards').json()['items']
    assert {key: row[key] for key in CARD} == {**CARD, 'game': 'WS'}


def test_xlsx_import_rejects_unknown_headings_and_formulas(client):
    from xlsxwriter import Workbook

    path = '/api/cards/import?format=xlsx'
    headers = {'Content-Type': XLSX_MEDIA_TYPE}
    for heading in [['游戏', 'Number', 'rarity', '价格'], ['game', 'card_number', 'rarity', '名称', 'Name']]:
        output = BytesIO()
        with Workbook(output, {'in_memory': True}) as book:
            book.add_worksheet().write_row(0, 0, heading)
        response = client.post(path, content=output.getvalue(), headers=headers)
        assert response.status_code == 422
        assert response.json()['detail'] == 'cards_import_headers'
    output = BytesIO()
    with Workbook(output, {'in_memory': True}) as book:
        sheet = book.add_worksheet()
        sheet.write_row(0, 0, ['name', 'rarity', 'card_number', 'game'])
        sheet.write_row(1, 0, ['=1+1', 'SR', 'ABC-001', 'WS'])
    response = client.post(path, content=output.getvalue(), headers=headers)
    assert response.status_code == 422
    assert response.json()['detail'] == {'code': 'cards_import_row', 'row': 2}
    assert client.get('/api/cards').json() == {'items': []}


def login(client, name):
    data = register_user(client, name).json()
    client.headers['Authorization'] = 'Bearer ' + data['token']
    return data['token']


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', ORIGIN)
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
        login(client, 'alice')
        yield client


def test_crud_duplicates_persistence(client):
    response = client.post('/api/cards', json={**CARD, 'game': ' Ws ', 'name': ' Card '})
    assert response.status_code == 201
    row = response.json()
    assert row['game'] == 'WS' and row['name'] == 'Card'
    duplicate = client.post('/api/cards', json={**CARD, 'rarity': ' sr ', 'card_number': ' abc-001 '})
    assert duplicate.status_code == 409 and duplicate.json()['detail']['id'] == row['id']
    other = client.post('/api/cards', json={**CARD, 'game': 'Custom game'}).json()
    assert client.put('/api/cards/' + other['id'], json=CARD).status_code == 409
    edited = client.put('/api/cards/' + row['id'], json={**CARD, 'quantity': 0, 'notes': ''}).json()
    assert edited['quantity'] == 0 and edited['notes'] == ''
    assert [r['id'] for r in client.get('/api/cards').json()['items']] == [other['id'], row['id']]
    with UserStore(app.state.auth.store.conninfo).session() as db:
        assert db.get(OwnedCard, row['id']).quantity == 0
    assert client.request('DELETE', '/api/cards/' + row['id'], json={}).status_code == 204
    assert client.request('DELETE', '/api/cards/' + row['id'], json={}).status_code == 404


def test_user_isolation_and_guards(client):
    alice_token = client.headers['Authorization']
    saved = client.post('/api/cards', json=CARD).json()
    login(client, 'bob')
    assert client.get('/api/cards').json() == {'items': []}
    for method in ['PUT', 'DELETE']:
        assert client.request(method, '/api/cards/' + saved['id'], json=CARD).status_code == 404
    assert client.post('/api/cards', json=CARD).status_code == 201
    client.headers['Authorization'] = alice_token
    assert client.get('/api/cards').json()['items'] == [saved]
    for method, path in [('POST', ''), ('PUT', '/' + saved['id']), ('DELETE', '/' + saved['id'])]:
        assert client.request(method, '/api/cards' + path, json=CARD, headers={'Origin': 'https://evil.test'}).status_code == 403
        assert client.request(method, '/api/cards' + path, content='{}', headers={'Content-Type': 'text/plain'}).status_code == 415
    client.headers.pop('Authorization')
    for method, path in [('GET', ''), ('POST', ''), ('PUT', '/x'), ('DELETE', '/x')]:
        response = client.request(method, '/api/cards' + path, json=CARD)
        assert response.status_code == 401 and response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('field,value', [('game', ' '), ('name', ''), ('rarity', '\t'), ('card_number', ' '),
    ('quantity', -1), ('quantity', 1.5), ('quantity', True), ('quantity', '2'), ('quantity', 9007199254740992)])
def test_validation(client, field, value):
    assert client.post('/api/cards', json={**CARD, field: value}).status_code == 422


def test_sev_brands_are_independent_and_aliases_share_identity(client):
    # Same number and shared rarity can coexist across brands.
    for game, expected in [('sev', 'SEV'), ('sev_en', 'SEV EN')]:
        response = client.post('/api/cards', json={**CARD, 'game': game, 'rarity': 'SP'})
        assert response.status_code == 201
        assert response.json()['game'] == expected
    assert client.post('/api/cards', json={**CARD, 'game': ' SEV EN ', 'rarity': 'SP'}).status_code == 409
    rows = client.get('/api/cards').json()['items']
    assert {row['game'] for row in rows} == {'SEV', 'SEV EN'}


def test_unicode_keys_defaults_and_cors(client):
    body = {**CARD, 'game': 'Straße', 'rarity': 'Rare'}
    body.pop('quantity'); body.pop('notes')
    saved = client.post('/api/cards', json=body).json()
    assert saved['quantity'] == 1 and saved['notes'] == ''
    assert client.post('/api/cards', json={**body, 'game': 'STRASSE'}).status_code == 409
    response = client.options('/api/cards/x', headers={'Access-Control-Request-Method': 'PUT', 'Access-Control-Request-Headers': 'authorization,content-type'})
    assert response.status_code == 200
    assert 'PUT' in response.headers['access-control-allow-methods']




def test_image_persistence_update_and_clear(client):
    image = 'https://example.com/card.png'
    saved = client.post('/api/cards', json={**CARD, 'image_url': image}).json()
    assert saved['image_url'] == image
    assert client.get('/api/cards').json()['items'][0]['image_url'] == image
    with UserStore(app.state.auth.store.conninfo).session() as db:
        assert db.get(OwnedCard, saved['id']).image_url == image
    response = client.put('/api/cards/' + saved['id'], json={**CARD, 'image_url': None})
    assert response.status_code == 200 and response.json()['image_url'] == ''
    for invalid in ['javascript:alert(1)', 'data:image/png;base64,test', '/relative.png']:
        assert client.put('/api/cards/' + saved['id'], json={**CARD, 'image_url': invalid}).status_code == 422




def test_ws_en_is_separate_inventory_brand(client):
    for game, expected in [('WS', 'WS'), ('ws_en', 'WS EN')]:
        response = client.post('/api/cards', json={**CARD, 'game': game})
        assert response.status_code == 201
        assert response.json()['game'] == expected
    assert client.post('/api/cards', json={**CARD, 'game': 'WS EN'}).status_code == 409
