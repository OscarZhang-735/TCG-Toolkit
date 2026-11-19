"""Group management endpoints mounted under each collection."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator

from ..auth.dependencies import current_user, write_guard
from .groups import CollectionGroups


class GroupName(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator('name', mode='before')
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class GroupTarget(BaseModel):
    group_id: str | None = Field(min_length=1)


def group_router(collection: CollectionGroups):
    router = APIRouter(prefix='/groups')

    @router.get('')
    def groups(request: Request, user=Depends(current_user)):
        with request.app.state.database.session() as db:
            return {'items': collection.list_records(db, user['id'])}

    @router.post('', status_code=201, dependencies=[Depends(write_guard)])
    def create_group(body: GroupName, request: Request, user=Depends(current_user)):
        with request.app.state.database.session() as db:
            return collection.create(db, user['id'], body.name)

    @router.put('/{group_id}', dependencies=[Depends(write_guard)])
    def rename_group(group_id: str, body: GroupName, request: Request, user=Depends(current_user)):
        with request.app.state.database.session() as db:
            return collection.rename(db, user['id'], group_id, body.name)

    @router.delete('/{group_id}', status_code=204, dependencies=[Depends(write_guard)])
    def remove_group(group_id: str, request: Request, user=Depends(current_user)):
        with request.app.state.database.session() as db:
            collection.remove(db, user['id'], group_id)

    return router
