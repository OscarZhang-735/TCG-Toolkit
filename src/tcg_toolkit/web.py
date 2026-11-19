"""Local browser API; the existing searchers own upstream parsing."""
from contextlib import asynccontextmanager
import asyncio
from pathlib import Path
import mimetypes
import os
from starlette.middleware.cors import CORSMiddleware

from fastapi import FastAPI, HTTPException, Depends, Request
from sqlalchemy.exc import SQLAlchemyError
from .persistence.schema import require_schema
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse

from .external.search_proxy import direct_search

# Slim images may lack a system MIME table entry for the game logos.
mimetypes.add_type('image/webp', '.webp')

from .auth.routes import router as auth_router
from .auth.dependencies import current_user
from .persistence.runtime import open_database
from .auth.runtime import initialize_auth
from .media.objects import image_bucket, retry_image_cleanup
from .collection.favorites import router as favorites_router
from .collection.cards import router as cards_router
from .catalog.prices import router as prices_router
from .catalog.official import router as official_router
from .cart.yuyutei import router as yuyutei_router
from .cart.hobbystation import router as hobbystation_cart_router
from .cart.cardmax import router as cardmax_cart_router
from .catalog.product_details import router as product_router
from .grading.routes import router as grading_router
from .collection.graded_cards import router as graded_cards_router
from .subscriptions.routes import router as subscriptions_router
from .subscriptions.recipient_routes import router as notification_email_router
from .subscriptions.worker import SubscriptionWorker
from .catalog.search_routes import router as search_router
from .currency.routes import router as currency_router


@asynccontextmanager
async def lifespan(app):
    store = open_database()
    app.state.database = store
    worker = None
    stop_image_cleanup = asyncio.Event()
    image_worker = None
    try:
        initialize_auth(app, store)
        if image_bucket():
            image_worker = asyncio.create_task(retry_image_cleanup(store, stop_image_cleanup))
        if os.getenv('TCG_SUBSCRIPTIONS_ENABLED', '').lower() == 'true':
            worker = SubscriptionWorker(store)
            worker.start()
        yield
    finally:
        if image_worker is not None:
            stop_image_cleanup.set()
            await image_worker
        if worker is not None:
            await asyncio.to_thread(worker.stop)
        store.close()


class BrowserCORS:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        origins = getattr(scope["app"].state, "auth_origins", set())
        await CORSMiddleware(self.app, allow_origins=list(origins),
                             allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                             allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
                             expose_headers=["Idempotency-Replayed", "Retry-After"])(scope, receive, send)


app = FastAPI(title="TCG Toolkit", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(BrowserCORS)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    if request.url.path.startswith('/api/cardmax/cart'):
        return JSONResponse({'detail': 'invalid_cardmax_request'}, status_code=422)
    if request.url.path.startswith('/api/hobbystation/cart'):
        return JSONResponse({'detail': 'invalid_hobbystation_request'}, status_code=422)
    if request.url.path.startswith(('/api/auth/', '/api/subscriptions/notification-email')):
        return JSONResponse({'detail': 'validation_error'}, status_code=422)
    return await request_validation_exception_handler(request, exc)


app.include_router(auth_router)
app.include_router(favorites_router)
app.include_router(cards_router)
app.include_router(prices_router)
app.include_router(official_router)
app.include_router(yuyutei_router)
app.include_router(hobbystation_cart_router)
app.include_router(cardmax_cart_router)
app.include_router(product_router)
app.include_router(grading_router)
app.include_router(graded_cards_router)
app.include_router(notification_email_router)
app.include_router(subscriptions_router)
app.include_router(search_router)
app.include_router(currency_router)


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
           for path in ('/api/auth', '/api/favorites', '/api/cards', '/api/yuyutei', '/api/hobbystation/cart', '/api/cardmax/cart',
                        '/api/subscriptions', '/api/prices',
                        '/api/grading', '/api/graded-cards')):
        response.headers['Cache-Control'] = 'no-store'
    elif not request.url.path.startswith('/api/'):
        # Static filenames are stable across releases; validate cached modules.
        response.headers['Cache-Control'] = 'no-cache'
    return response


@app.get("/api/version")
def version():
    return {"sha": os.getenv("TCG_BUILD_SHA", "local")}


@app.get("/api/health")
def health(request: Request):
    try:
        with request.app.state.database.connect() as db:
            require_schema(db)
    except (SQLAlchemyError, RuntimeError):
        raise HTTPException(503, detail="database_unavailable") from None
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="frontend")
