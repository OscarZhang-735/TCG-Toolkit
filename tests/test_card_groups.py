from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tcg_toolkit.auth.store import UserStore
from tcg_toolkit.cards import EXPORT_COLUMNS, XLSX_MEDIA_TYPE
from tcg_toolkit.db_models import CardGroup, OwnedCard
from tcg_toolkit.web import app
from test_cards import CARD, client, exported, login, spreadsheet_rows


def group(client, name='Custom'):
    response = client.post('/api/cards/groups', json={'name': name})
    assert response.status_code == 201
    return response.json()


def add(client, **fields):
    response = client.post('/api/cards', json={**CARD, **fields})
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize('created_at', [(100, 100, 100), (100, 101, 102)])
def test_group_lifecycle_and_name_rules(client, monkeypatch, created_at):
    timestamps, ids = iter(created_at), iter([3, 2, 1])
    monkeypatch.setattr('tcg_toolkit.cards.time', SimpleNamespace(time=lambda: next(timestamps)))
    monkeypatch.setattr('tcg_toolkit.cards.uuid', SimpleNamespace(uuid4=lambda: UUID(int=next(ids))))
    assert client.get('/api/cards/groups').json() == {'items': []}
    for name in ['', ' \t\n', 'x' * 101, None, 42]:
        assert client.post('/api/cards/groups', json={'name': name}).status_code == 422
    first = group(client, '  Custom  ')
    assert first['name'] == 'Custom' and set(first) == {'id', 'name', 'created_at'}
    second, third = group(client, 'custom'), group(client, ' ' + '长' * 100 + ' ')
    for name in ['Custom', ' Custom ']:
        response = client.post('/api/cards/groups', json={'name': name})
        assert response.status_code == 409
        assert response.json()['detail'] == 'card_group_name_taken'
    path = f"/api/cards/groups/{first['id']}"
    assert client.put(path, json={'name': 'custom'}).status_code == 409
    assert client.put(path, json={'name': '  '}).status_code == 422
    assert client.put(path, json={'name': 'Custom'}).json() == first
    renamed = client.put(path, json={'name': ' Renamed '}).json()
    assert renamed == {**first, 'name': 'Renamed'}
    response = client.get('/api/cards/groups')
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['items'] == sorted([renamed, second, third], key=lambda row: (row['created_at'], row['id']))
    assert client.request('DELETE', f"/api/cards/groups/{second['id']}", json={}).status_code == 204
    assert client.get('/api/cards', params={'group_id': second['id']}).status_code == 404


def test_membership_filter_move_delete_and_edit_preserve_cards(client):
    custom = group(client)
    saved = add(client)
    assert saved['group_id'] is None
    other = add(client, card_number='ABC-002', group_id=custom['id'])
    assert other['group_id'] == custom['id']
    assert client.get('/api/cards').json()['items'] == [other, saved]
    assert client.get('/api/cards', params={'group_id': custom['id']}).json()['items'] == [other]
    path = f"/api/cards/{saved['id']}/group"
    for body in [{}, {'group_id': ''}]:
        assert client.put(path, json=body).status_code == 422
    assert client.post('/api/cards', json={**CARD, 'group_id': ''}).status_code == 422
    moved = client.put(path, json={'group_id': custom['id']}).json()
    assert moved == {**saved, 'group_id': custom['id']}
    assert client.put(path, json={'group_id': custom['id']}).json() == moved
    assert client.put(path, json={'group_id': None}).json() == saved
    client.put(path, json={'group_id': custom['id']})
    # Edits that omit group_id (older clients) retain membership.
    edited = client.put(f"/api/cards/{saved['id']}", json={**CARD, 'quantity': 7, 'notes': 'Edited'}).json()
    assert edited['group_id'] == custom['id'] and edited['quantity'] == 7
    # The editor may send group_id with the card fields: null ungroups, an id moves, '' is invalid.
    edit = f"/api/cards/{saved['id']}"
    assert client.put(edit, json={**CARD, 'group_id': ''}).status_code == 422
    assert client.put(edit, json={**CARD, 'quantity': 8, 'group_id': None}).json()['group_id'] is None
    edited = client.put(edit, json={**CARD, 'quantity': 7, 'notes': 'Edited', 'group_id': custom['id']}).json()
    assert edited['group_id'] == custom['id'] and edited['quantity'] == 7
    # A rejected edit (duplicate) leaves the group unchanged too.
    response = client.put(edit, json={**CARD, 'card_number': 'ABC-002', 'group_id': None})
    assert response.status_code == 409
    assert client.get('/api/cards', params={'group_id': custom['id']}).json()['items'][-1]['id'] == saved['id']
    with UserStore(app.state.auth.store.conninfo) as store, store.session() as db:
        assert db.get(OwnedCard, saved['id']).group_id == custom['id']
        assert db.get(CardGroup, custom['id']).name == custom['name']
    assert client.request('DELETE', f"/api/cards/groups/{custom['id']}", json={}).status_code == 204
    assert client.get('/api/cards').json()['items'] == [
        {**other, 'group_id': None}, {**edited, 'group_id': None}]
    assert client.request('DELETE', f"/api/cards/groups/{custom['id']}", json={}).status_code == 404


def test_duplicates_remain_account_wide_and_favorite_groups_are_separate(client):
    custom = group(client)
    favorite_group = client.post('/api/favorites/groups', json={'name': custom['name']}).json()
    saved = add(client, group_id=custom['id'])
    for group_id in [None, group(client, 'Other')['id']]:
        response = client.post('/api/cards', json={**CARD, 'group_id': group_id})
        assert response.status_code == 409
        assert response.json()['detail'] == {'type': 'card_duplicate', 'id': saved['id']}
    assert client.post('/api/cards', json={**CARD, 'group_id': favorite_group['id']}).status_code == 404
    client.request('DELETE', f"/api/cards/groups/{custom['id']}", json={})
    assert client.get('/api/favorites/groups').json()['items'] == [favorite_group]


def test_group_ownership_and_database_constraints(client):
    alice_group, saved = group(client), add(client)
    login(client, 'bob')
    assert client.get('/api/cards/groups').json()['items'] == []
    bob_group, bob_saved = group(client), add(client)
    for group_id in [alice_group['id'], 'missing']:
        for method, path, kwargs in [
            ('GET', '/api/cards', {'params': {'group_id': group_id}}),
            ('GET', '/api/cards/export', {'params': {'format': 'json', 'group_id': group_id}}),
            ('POST', '/api/cards', {'json': {**CARD, 'group_id': group_id}}),
            ('PUT', f'/api/cards/groups/{group_id}', {'json': {'name': 'Other'}}),
            ('DELETE', f'/api/cards/groups/{group_id}', {'json': {}}),
            ('PUT', f"/api/cards/{bob_saved['id']}/group", {'json': {'group_id': group_id}}),
            ('PUT', f"/api/cards/{bob_saved['id']}", {'json': {**CARD, 'group_id': group_id}}),
        ]:
            response = client.request(method, path, **kwargs)
            assert response.status_code == 404
            assert response.json()['detail'] == 'card_group_not_found'
    for card_id in [saved['id'], 'missing']:
        response = client.put(f'/api/cards/{card_id}/group', json={'group_id': bob_group['id']})
        assert response.status_code == 404 and response.json()['detail'] == 'card_not_found'
    with pytest.raises(IntegrityError):
        with app.state.auth.store.session() as db:
            db.execute(update(OwnedCard).where(OwnedCard.id == saved['id']).values(group_id=bob_group['id']))
    with pytest.raises(IntegrityError):
        with app.state.auth.store.session() as db:
            db.add(CardGroup(id='duplicate', user_id=db.get(CardGroup, bob_group['id']).user_id,
                             name=bob_group['name'], created_at=1))


@pytest.mark.parametrize('method,path,body', [
    ('GET', '/api/cards/groups', None),
    ('POST', '/api/cards/groups', {'name': 'Custom'}),
    ('PUT', '/api/cards/groups/x', {'name': 'Custom'}),
    ('DELETE', '/api/cards/groups/x', {}),
    ('PUT', '/api/cards/x/group', {'group_id': 'x'}),
])
def test_group_access_guards(client, method, path, body):
    if method != 'GET':
        assert client.request(method, path, json=body, headers={'Origin': 'https://evil.test'}).status_code == 403
        assert client.request(method, path, content='{}', headers={'Content-Type': 'text/plain'}).status_code == 415
    client.headers.pop('Authorization')
    response = client.request(method, path, json=body)
    assert response.status_code == 401
    assert response.headers['cache-control'] == 'no-store'


def test_group_delete_rolls_back_membership_on_failure(client, monkeypatch):
    custom = group(client)
    saved = add(client, group_id=custom['id'])
    original = Session.delete

    def fail_delete(self, instance):
        if isinstance(instance, CardGroup):
            raise RuntimeError('simulated group deletion failure')
        return original(self, instance)

    with monkeypatch.context() as patch:
        patch.setattr(Session, 'delete', fail_delete)
        with pytest.raises(RuntimeError, match='simulated group deletion failure'):
            client.request('DELETE', f"/api/cards/groups/{custom['id']}", json={})
    assert client.get('/api/cards', params={'group_id': custom['id']}).json()['items'] == [saved]


@pytest.mark.parametrize('action', ['add', 'move'])
def test_concurrent_group_delete_and_membership_write(client, action):
    custom = group(client)
    saved = add(client) if action == 'move' else None
    barrier = Barrier(2)

    def remove():
        barrier.wait(timeout=10)
        return client.request('DELETE', f"/api/cards/groups/{custom['id']}", json={})

    def write():
        barrier.wait(timeout=10)
        if action == 'add':
            return client.post('/api/cards', json={**CARD, 'group_id': custom['id']})
        return client.put(f"/api/cards/{saved['id']}/group", json={'group_id': custom['id']})

    with ThreadPoolExecutor(max_workers=2) as pool:
        deletion, writing = pool.submit(remove), pool.submit(write)
        assert deletion.result(timeout=15).status_code == 204
        result = writing.result(timeout=15)
    assert result.status_code in (201 if action == 'add' else 200, 404)
    rows = client.get('/api/cards').json()['items']
    assert len(rows) == (1 if action == 'move' or result.status_code == 201 else 0)
    assert all(row['group_id'] is None for row in rows)
    if saved:
        assert rows == [saved]


def test_concurrent_duplicate_group_creation(client):
    barrier = Barrier(2)

    def create():
        barrier.wait(timeout=10)
        return client.post('/api/cards/groups', json={'name': 'Same'})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert sorted(response.status_code for response in results) == [201, 409]


def group_names(client):
    return [row['name'] for row in client.get('/api/cards/groups').json()['items']]


@pytest.mark.parametrize('format,media_type', [('json', 'application/json'), ('xlsx', XLSX_MEDIA_TYPE)])
def test_export_records_group_names_and_import_restores_them(client, format, media_type):
    custom, binder = group(client), group(client, 'Binder')
    saved = add(client, group_id=custom['id'])
    traded = add(client, card_number='ABC-002', group_id=binder['id'])
    loose = add(client, card_number='ABC-003')
    response = client.get('/api/cards/export', params={'format': format})
    expected = [exported(loose), exported(traded, 'Binder'), exported(saved, 'Custom')]
    if format == 'json':
        assert response.json() == {'items': expected}
    else:
        rows = spreadsheet_rows(response.content)
        assert rows[0] == list(EXPORT_COLUMNS)
        assert [dict(zip(rows[0], row)) for row in rows[1:]] == expected
    only = client.get('/api/cards/export', params={'format': 'json', 'group_id': custom['id'], 'fields': ['name', 'group']})
    assert only.json() == {'items': [{'name': 'Card', 'group': 'Custom'}]}

    # Re-importing skips every card; existing cards keep their groups and no group is added.
    path, headers = f'/api/cards/import?format={format}', {'Content-Type': media_type}
    assert client.post(path, content=response.content, headers=headers).json() == {'imported': 0, 'skipped': 3}
    assert {row['id']: row['group_id'] for row in client.get('/api/cards').json()['items']} == {
        saved['id']: custom['id'], traded['id']: binder['id'], loose['id']: None}
    assert sorted(group_names(client)) == ['Binder', 'Custom']

    # Another account: an existing same-named group is reused; a group used only by a
    # skipped duplicate is not created.
    login(client, 'bob')
    mine = group(client)
    duplicate = add(client, card_number='ABC-002')
    assert client.post(path, content=response.content, headers=headers).json() == {'imported': 2, 'skipped': 1}
    cards = {row['card_number']: row for row in client.get('/api/cards').json()['items']}
    assert cards['ABC-001']['group_id'] == mine['id']
    assert cards['ABC-002'] == duplicate
    assert cards['ABC-003']['group_id'] is None
    assert group_names(client) == ['Custom']


def test_import_creates_groups_in_file_order_and_validates_names(client):
    path = '/api/cards/import?format=json'
    rows = [{**CARD, 'card_number': f'N-{index}', 'group': name}
            for index, name in enumerate(['Zeta', '  Alpha  ', 'Zeta', '   ', None])]
    assert client.post(path, json={'items': rows}).json() == {'imported': 5, 'skipped': 0}
    assert group_names(client) == ['Zeta', 'Alpha']
    names = {row['id']: row['name'] for row in client.get('/api/cards/groups').json()['items']}
    assert {row['card_number']: names.get(row['group_id']) for row in client.get('/api/cards').json()['items']} == {
        'N-0': 'Zeta', 'N-1': 'Alpha', 'N-2': 'Zeta', 'N-3': None, 'N-4': None}
    # Group names follow the same rules as the group API; a bad name rejects the whole file.
    for bad in [42, 'x' * 101, ['Zeta'], 'a\0b']:
        response = client.post(path, json={'items': [{**CARD, 'card_number': 'NEW'}, {**CARD, 'group': bad}]})
        assert response.status_code == 422
        assert response.json()['detail'] == {'code': 'cards_import_row', 'row': 2}
    assert len(client.get('/api/cards').json()['items']) == 5
    assert group_names(client) == ['Zeta', 'Alpha']


def test_xlsx_import_reads_group_column_in_any_heading_language(client):
    from io import BytesIO
    from xlsxwriter import Workbook

    output = BytesIO()
    with Workbook(output, {'in_memory': True}) as book:
        sheet = book.add_worksheet()
        sheet.write_row(0, 0, ['游戏', 'Number', 'rarity', '名称', '分组'])
        sheet.write_row(1, 0, ['WS', 'ABC-001', 'SR', 'Card', 2024])
        sheet.write_row(2, 0, ['WS', 'ABC-002', 'SR', 'Card'])
    response = client.post('/api/cards/import?format=xlsx', content=output.getvalue(),
                           headers={'Content-Type': XLSX_MEDIA_TYPE})
    assert response.json() == {'imported': 2, 'skipped': 0}
    assert group_names(client) == ['2024']
    [year] = client.get('/api/cards/groups').json()['items']
    assert {row['card_number']: row['group_id'] for row in client.get('/api/cards').json()['items']} == {
        'ABC-001': year['id'], 'ABC-002': None}