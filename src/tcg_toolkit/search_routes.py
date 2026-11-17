"""Authenticated HTTP access to the shared card search registry."""
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from .auth.dependencies import current_user
from .errors import SearchParseError, SearchRequestError
from .models import Game
from .search import SEARCHERS

router = APIRouter()


@router.get("/api/search", dependencies=[Depends(current_user)])
def search(game: Game, keyword: Annotated[str, Query(min_length=1)],
           page: Annotated[int, Query(ge=1)] = 1):
    if not keyword.strip():
        raise HTTPException(422, detail={"type": "validation_error", "message": "Keyword must not be empty."})
    try:
        with SEARCHERS[game]() as searcher:
            result = searcher.search_page(keyword, page=page)
        return {**result.to_dict(), "game": game.value}
    except SearchRequestError as exc:
        timeout = isinstance(exc.__cause__, httpx.TimeoutException)
        raise HTTPException(504 if timeout else 502, detail={
            "type": type(exc).__name__,
            "message": "The source timed out." if timeout else "Could not retrieve results from the source.",
        }) from exc
    except SearchParseError as exc:
        raise HTTPException(502, detail={"type": type(exc).__name__,
                                         "message": "Could not read the source results."}) from exc


