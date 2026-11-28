"""Store private COS images and sign temporary links for their owners."""
import asyncio
import logging
import os
import re
import time
from uuid import uuid4

import httpx
from fastapi import HTTPException
from qcloud_cos import CosConfig, CosS3Client
from qcloud_cos.cos_exception import CosException, CosServiceError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..persistence.models import AvatarCleanup

AVATAR_PREFIX = '/tcgtoolkit/avatars/'
GRADED_PREFIX = '/tcgtoolkit/graded/'
CLEANUP_INTERVAL = 60
IMAGE_URL_TTL = 3600
logger = logging.getLogger(__name__)


def image_bucket():
    try:
        bucket = httpx.URL(os.getenv('TCG_AVATAR_COS_URL', '').strip())
    except httpx.InvalidURL:
        return None
    if (bucket.scheme != 'https' or not bucket.host or bucket.userinfo
            or bucket.raw_path != b'/' or bucket.fragment):
        return None
    return bucket.copy_with(path='/')


def _cos_client(bucket, timeout=15):
    secret_id = os.getenv('TCG_COS_SECRET_ID', '').strip()
    secret_key = os.getenv('TCG_COS_SECRET_KEY', '').strip()
    if bucket is None or not secret_id or not secret_key:
        return None
    # Domain already identifies the bucket. Keep the configured origin for both
    # new objects and existing URLs, including COS custom origin domains.
    config = CosConfig(Domain=bucket.netloc.decode('ascii'), Scheme='https', SecretId=secret_id,
                       SecretKey=secret_key, Timeout=timeout, Proxies={'no_proxy': '*'})
    return CosS3Client(config, retry=0)


def store_webp(data, prefix, failure):
    """Upload a WebP and return the stable, unsigned URL for storage."""
    bucket = image_bucket()
    client = _cos_client(bucket)
    if client is None:
        raise HTTPException(503, detail=failure)
    url = bucket.copy_with(path=f'{prefix}{uuid4().hex}.webp')
    try:
        client.put_object(Bucket=None, Key=url.path.lstrip('/'), Body=data, ContentType='image/webp')
    except CosException:
        raise HTTPException(502, detail=failure) from None
    return str(url)


def is_stored_image(url, bucket):
    try:
        target = httpx.URL(url)
    except httpx.InvalidURL:
        return False
    return (target.copy_with(path='/') == bucket and not target.query and not target.fragment
            and re.fullmatch(rb'/tcgtoolkit/(?:avatars|graded)/[0-9a-f]{32}\.webp', target.raw_path) is not None)


def signed_image_url(url):
    if not url:
        return url
    bucket = image_bucket()
    if not is_stored_image(url, bucket):
        return url
    client = _cos_client(bucket)
    if client is None:
        return None
    return client.get_presigned_url(Method='GET', Bucket=None, Key=httpx.URL(url).path.lstrip('/'),
                                    Expired=IMAGE_URL_TTL)


def _delete_stored_image(client, bucket, url):
    if not is_stored_image(url, bucket):
        raise ValueError('Image bucket changed')
    try:
        client.delete_object(Bucket=None, Key=httpx.URL(url).path.lstrip('/'))
    except CosServiceError as exc:
        if exc.get_status_code() != 404:
            raise


def cleanup_image(store, url=None):
    bucket = image_bucket()
    client = _cos_client(bucket, timeout=5)
    if client is None:
        return False
    query = select(AvatarCleanup).where(AvatarCleanup.next_attempt_at <= int(time.time()))
    if url is not None:
        query = query.where(AvatarCleanup.url == url)
    query = query.order_by(AvatarCleanup.next_attempt_at, AvatarCleanup.url).limit(1).with_for_update(skip_locked=True)
    try:
        with store.session() as db:
            pending = db.scalar(query)
            if pending is None:
                return False
            try:
                _delete_stored_image(client, bucket, pending.url)
            except (CosException, ValueError):
                pending.next_attempt_at = int(time.time()) + CLEANUP_INTERVAL
                logger.warning('COS image deletion deferred')
            else:
                db.delete(pending)
    except SQLAlchemyError:
        logger.warning('Could not process the image cleanup queue')
        return False
    return True


async def retry_image_cleanup(store, stop):
    while not stop.is_set():
        if await asyncio.to_thread(cleanup_image, store):
            continue
        try:
            await asyncio.wait_for(stop.wait(), CLEANUP_INTERVAL)
        except TimeoutError:
            pass
