"""Account-scoped subscriptions to merchant product offers."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..auth.dependencies import current_user, write_guard
from ..catalog.errors import ProductSourceError
from ..catalog.identity import favorite_source, normalize_favorite_url
from ..catalog.models import Game
from . import service

router = APIRouter(prefix='/api/subscriptions')


class IntervalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval_hours: int = Field(strict=True, ge=1, le=2147483647)


class SubscribeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    game: Game
    product_url: str = Field(max_length=512)

    @model_validator(mode='after')
    def product_location(self):
        self.product_url = normalize_favorite_url(favorite_source(self.product_url), self.game, self.product_url)
        return self


@router.get('/settings')
def get_settings(request: Request, user=Depends(current_user)):
    return service.get_settings(request.app.state.database, user['id'])


@router.put('/settings', dependencies=[Depends(write_guard)])
def update_settings(body: IntervalInput, request: Request, user=Depends(current_user)):
    return service.update_interval(request.app.state.database, user['id'], body.interval_hours)


@router.get('')
def listing(request: Request, user=Depends(current_user)):
    return {'items': service.list_subscriptions(request.app.state.database, user['id'])}


@router.post('', dependencies=[Depends(write_guard)])
def add(body: SubscribeInput, request: Request, user=Depends(current_user)):
    try:
        return service.subscribe(request.app.state.database, user['id'], body.game, body.product_url)
    except service.VerifiedEmailRequired:
        raise HTTPException(403, detail='verified_email_required') from None
    except ProductSourceError as exc:
        raise HTTPException(exc.status_code, detail=exc.code) from None


@router.delete('/{subscription_id}', status_code=204, dependencies=[Depends(write_guard)])
def remove(subscription_id: str, request: Request, user=Depends(current_user)):
    if not service.unsubscribe(request.app.state.database, user['id'], subscription_id):
        raise HTTPException(404, detail='subscription_not_found')
