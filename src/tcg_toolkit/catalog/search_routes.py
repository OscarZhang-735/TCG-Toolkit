"""Authenticated HTTP access to the shared card search registry."""
from typing import Annotated
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..auth.dependencies import current_user
from .errors import SearchParseError, SearchRequestError
from .hobby_images import HOBBY_IMAGE_ROOT, HOBBY_ORIGIN, IMAGE_TYPES, public_hobby_image_url, valid_image
from .models import Game, SearchSource
from .search import create_searcher

router = APIRouter()


@router.get("/api/images/hobbystation/{game_dir}/{filename}")
def hobby_card_image(game_dir: str, filename: str):
    if not valid_image(game_dir, filename):
        raise HTTPException(404)
    url = f"{HOBBY_ORIGIN}{HOBBY_IMAGE_ROOT}{game_dir}/{quote(filename, safe='')}"
    try:
        with httpx.Client(trust_env=False, timeout=15) as client, client.stream(
            "GET", url, headers={"Referer": HOBBY_ORIGIN + "/"},
        ) as upstream:
            if upstream.status_code == 404:
                raise HTTPException(404)
            upstream.raise_for_status()
            media_type = upstream.headers.get("content-type", "").split(";")[0].lower()
            if media_type not in IMAGE_TYPES:
                raise HTTPException(502, "Unexpected image format")
            data = bytearray()
            for chunk in upstream.iter_bytes():
                data.extend(chunk)
                if len(data) > 10 * 1024 * 1024:
                    raise HTTPException(502, "Image too large")
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Image source timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Image source unavailable") from exc
    return Response(bytes(data), media_type=media_type, headers={
        "Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff",
    })


@router.get("/api/search", dependencies=[Depends(current_user)])
def search(request: Request, game: Game, keyword: Annotated[str, Query(min_length=1)],
           page: Annotated[int, Query(ge=1)] = 1, source: SearchSource = SearchSource.YUYUTEI,
           stock: bool = False):
    if not keyword.strip():
        raise HTTPException(422, detail={"type": "validation_error", "message": "Keyword must not be empty."})
    try:
        searcher = create_searcher(source, game, stock=stock)
    except ValueError as exc:
        raise HTTPException(422, detail={"type": "validation_error", "message": str(exc)}) from exc
    try:
        with searcher:
            result = searcher.search_page(keyword, page=page)
        data = result.to_dict()
        if source == SearchSource.HOBBYSTATION:
            for item in data["items"]:
                item["image_url"] = public_hobby_image_url(request, item["image_url"])
        return {**data, "game": game.value}
    except SearchRequestError as exc:
        timeout = isinstance(exc.__cause__, httpx.TimeoutException)
        raise HTTPException(504 if timeout else 502, detail={
            "type": type(exc).__name__,
            "message": "The source timed out." if timeout else "Could not retrieve results from the source.",
        }) from exc
    except SearchParseError as exc:
        raise HTTPException(502, detail={"type": type(exc).__name__,
                                         "message": "Could not read the source results."}) from exc


