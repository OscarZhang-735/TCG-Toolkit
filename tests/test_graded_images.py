"""Card outline detection and the upload / crop / restore flow for graded card photos."""
from io import BytesIO

import pytest
from PIL import Image

from card_photos import corner_error, photo
from test_graded_cards import PATH, add, client  # noqa: F401  (fixture)

BUCKET = 'https://cards.example.com'


@pytest.fixture
def cos(monkeypatch):
    """In-memory COS bucket: uploads by URL and deletions."""
    monkeypatch.setenv('TCG_AVATAR_COS_URL', BUCKET)
    objects, deleted = {}, []

    class Reply:
        def __init__(self, status=200):
            self.status_code = status

        def raise_for_status(self):
            pass

    def put(url, content, **kwargs):
        assert kwargs['headers']['Content-Type'] == 'image/webp'
        objects[url] = content
        return Reply()

    def delete(url, **kwargs):
        deleted.append(url)
        objects.pop(url, None)
        return Reply(204)

    monkeypatch.setattr('tcg_toolkit.media.objects.httpx.put', put)
    monkeypatch.setattr('tcg_toolkit.media.objects.httpx.delete', delete)
    return objects, deleted


def upload(client, card_id, side, data, corners, content_type='image/jpeg'):  # noqa: F811
    text = ','.join(str(value) for point in corners for value in point)
    return client.put(f'{PATH}/{card_id}/images/{side}', params={'corners': text}, content=data,
                      headers={'Content-Type': content_type})


def test_detect_endpoint_suggests_corners(client):  # noqa: F811
    data, truth = photo('slab', 'busy', 10, .05, (1600, 1200), 11)
    response = client.post(f'{PATH}/detect-card', content=data, headers={'Content-Type': 'image/jpeg'})
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    body = response.json()
    assert (body['width'], body['height']) == (1200, 1600)
    assert corner_error(body['corners'], truth) < 0.04


def test_upload_replaces_one_side_survives_refresh_and_restores(client, cos):  # noqa: F811
    objects, deleted = cos
    card = add(client).json()
    data, truth = photo('slab', 'dark', -6, .05, (1600, 1200), 12)
    response = upload(client, card['id'], 'back', data, truth)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    images = {image['side']: image for image in response.json()['images']}
    first = images['back']['url']
    assert first.startswith(BUCKET + '/tcgtoolkit/graded/') and images['back']['uploaded']
    assert images['front'] == {'url': 'https://example.test/front.jpg', 'alt': 'Front', 'side': 'front',
                               'thumbnail_url': None, 'uploaded': False}
    with Image.open(BytesIO(objects[first])) as stored:
        assert stored.format == 'WEBP' and abs(stored.width / stored.height - 760 / 1260) < 0.04
    # A lookup refresh never replaces the user's photo.
    assert client.post(f"{PATH}/{card['id']}/refresh", json={}).json()['images'] == response.json()['images']
    second = upload(client, card['id'], 'back', data, truth).json()
    assert second['images'][1]['url'] != first and deleted == [first]
    restored = client.request('DELETE', f"{PATH}/{card['id']}/images/back", json={}).json()
    assert [image['side'] for image in restored['images']] == ['front']
    assert deleted[-1] == second['images'][1]['url'] and not objects
    upload(client, card['id'], 'front', data, truth)
    assert client.request('DELETE', f"{PATH}/{card['id']}", json={}).status_code == 204
    assert not objects, 'deleting the card removes its uploaded photos'
