"""Store and clean up public COS objects shared by avatars and graded cards."""
import asyncio
import logging
import os
import re
import time
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..persistence.models import AvatarCleanup

AVATAR_PREFIX = '/tcgtoolkit/avatars/'
GRADED_PREFIX = '/tcgtoolkit/graded/'
CLEANUP_INTERVAL = 60
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


def store_webp(data, prefix, failure):
    """Upload a WebP under a random name and return its public URL."""
    bucket = image_bucket()
    if bucket is None:
        raise HTTPException(503, detail=failure)
    url = str(bucket.copy_with(path=f'{prefix}{uuid4().hex}.webp'))
    try:
        response = httpx.put(url, content=data, timeout=15,
                             headers={'Content-Type': 'image/webp'}, trust_env=False)
        response.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(502, detail=failure) from None
    return url


def is_stored_image(url, bucket):
    try:
        target = httpx.URL(url)
    except httpx.InvalidURL:
        return False
    return (target.copy_with(path='/') == bucket and not target.query and not target.fragment
            and re.fullmatch(rb'/tcgtoolkit/(?:avatars|graded)/[0-9a-f]{32}\.webp', target.raw_path) is not None)


def cleanup_image(store, url=None):
    bucket = image_bucket()
    if bucket is None:
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
                if not is_stored_image(pending.url, bucket):
                    raise ValueError('Image bucket changed')
                response = httpx.delete(pending.url, timeout=5, trust_env=False)
                if response.status_code != 404:
                    response.raise_for_status()
            except (httpx.HTTPError, ValueError):
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
