"""In-memory transport for the COS SDK used by the private image tests."""
from time import time
from urllib.parse import parse_qs

import pytest
import requests

BUCKET = 'https://cards-1250000000.cos.ap-guangzhou.myqcloud.com'
SECRET_ID = 'test-secret-id'


def signed_object_url(url):
    """Check a read link and return its stable object URL."""
    canonical, _, query = url.partition('?')
    assert canonical.startswith(BUCKET + '/tcgtoolkit/')
    fields = parse_qs(query)
    assert fields['q-ak'] == [SECRET_ID] and fields['q-signature'][0]
    starts, expires = map(int, fields['q-sign-time'][0].split(';'))
    assert starts <= time() < expires <= time() + 3600
    return canonical


class Bucket:
    def __init__(self):
        self.objects = {}
        self.deleted = []
        self.deny_delete = False

    def send(self, request):
        assert request.url.startswith(BUCKET + '/tcgtoolkit/')
        authorization = parse_qs(request.headers['Authorization'])
        assert authorization['q-ak'] == [SECRET_ID] and authorization['q-signature'][0]
        response = requests.Response()
        response.request, response.url = request, request.url
        response._content = b''
        if request.method == 'PUT':
            assert request.headers['Content-Type'] in ('image/webp', b'image/webp')
            self.objects[request.url] = request.body
            response.status_code = 200
        else:
            assert request.method == 'DELETE'
            self.deleted.append(request.url)
            if self.deny_delete:
                response.status_code = 403
                response._content = b'<Error><Code>AccessDenied</Code></Error>'
            elif self.objects.pop(request.url, None) is None:
                response.status_code = 404
                response._content = b'<Error><Code>NoSuchKey</Code></Error>'
            else:
                response.status_code = 204
        return response


@pytest.fixture
def cos(monkeypatch):
    bucket = Bucket()
    monkeypatch.setenv('TCG_AVATAR_COS_URL', BUCKET)
    monkeypatch.setenv('TCG_COS_SECRET_ID', SECRET_ID)
    monkeypatch.setenv('TCG_COS_SECRET_KEY', 'test-secret-key')
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', lambda adapter, request, **kwargs: bucket.send(request))
    return bucket
