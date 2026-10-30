"""Local browser API; the existing searchers own upstream parsing."""
from contextlib import asynccontextmanager
from pathlib import Path
import mimetypes
import os
from starlette.middleware.cors import CORSMiddleware
from typing import Annotated

import httpx
from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse

from . import (ChaosTCGSearcher, Game, LyceeSearcher, SearchParseError,
               SearchRequestError, WSRSearcher, WSSearcher, PMSearcher, SEVSearcher)
from .external.search_proxy import direct_search
from .external import CurrencyClient, CurrencyConfigError, CurrencyRequestError, TargetCurrency

# Slim images may lack a system MIME table entry for the game logos.
mimetypes.add_type('image/webp', '.webp')

from .auth.routes import router as auth_router, current_user, initialize_auth
from .favorites import router as favorites_router
from .cards import router as cards_router
from .official import router as official_router
from .yuyutei_cart import router as yuyutei_router
from .product_details import router as product_router


@asynccontextmanager
async def lifespan(app):
    initialize_auth(app)
    yield


class BrowserCORS:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        origins = getattr(scope["app"].state, "auth_origins", set())
        await CORSMiddleware(self.app, allow_origins=list(origins),
                             allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                             allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
                             expose_headers=["Idempotency-Replayed", "Retry-After"])(scope, receive, send)


app = FastAPI(title="TCG Toolkit", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(BrowserCORS)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    if request.url.path.startswith('/api/auth/'):
        return JSONResponse({'detail': 'validation_error'}, status_code=422)
    return await request_validation_exception_handler(request, exc)


app.include_router(auth_router)
app.include_router(favorites_router)
app.include_router(cards_router)
app.include_router(official_router)
app.include_router(yuyutei_router)
app.include_router(product_router)
SEARCHERS = {Game.WSR: WSRSearcher, Game.LYCEE: LyceeSearcher, Game.CHAOS: ChaosTCGSearcher, Game.WS: WSSearcher, Game.PM: PMSearcher, Game.SEV: SEVSearcher}


def proxy_switch_allowed():
    return os.getenv('TCG_ALLOW_PROXY_SWITCH', '').lower() == 'true'


@app.get('/api/yuyutei/proxy-config', dependencies=[Depends(current_user)])
def proxy_config():
    return {'available': proxy_switch_allowed()}


@app.middleware('http')
async def request_proxy_mode(request, call_next):
    direct = request.headers.get('x-tcg-direct') == '1'
    if direct and not proxy_switch_allowed():
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': 'proxy_switch_disabled'}, status_code=403,
                            headers={'Cache-Control': 'no-store'})
    token = direct_search.set(direct)
    try:
        return await call_next(request)
    finally:
        direct_search.reset(token)


@app.middleware('http')
async def browser_cache_policy(request, call_next):
    response = await call_next(request)
    if any(request.url.path == path or request.url.path.startswith(path + '/')
           for path in ('/api/auth', '/api/favorites', '/api/cards', '/api/yuyutei')):
        response.headers['Cache-Control'] = 'no-store'
    elif not request.url.path.startswith('/api/'):
        # Static filenames are stable across releases; validate cached modules.
        response.headers['Cache-Control'] = 'no-cache'
    return response


@app.get("/api/version")
def version():
    return {"sha": os.getenv("TCG_BUILD_SHA", "local")}


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/search", dependencies=[Depends(current_user)])
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


@app.get("/api/currency/convert", dependencies=[Depends(current_user)])
def convert_currency(to: TargetCurrency, amount: Annotated[float, Query(ge=0, allow_inf_nan=False)] = 1):
    try:
        with CurrencyClient() as currency:
            return currency.convert_jpy(amount, to)
    except CurrencyConfigError as exc:
        raise HTTPException(503, detail={"type": type(exc).__name__, "message": str(exc)}) from exc
    except CurrencyRequestError as exc:
        timeout = isinstance(exc.__cause__, httpx.TimeoutException)
        raise HTTPException(504 if timeout else 502, detail={
            "type": type(exc).__name__,
            "message": "The currency provider timed out." if timeout else "Could not retrieve the currency conversion.",
        }) from exc


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="frontend")
