"""Account-scoped group membership and mutations shared across collections."""
import time
import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..persistence.models import User


def lock_user(db: Session, user_id):
    """Serialize group deletion with membership writes before locking members."""
    db.execute(select(User.id).where(User.id == user_id).with_for_update()).one()


def group_record(row):
    return {'id': row.id, 'name': row.name, 'created_at': row.created_at}


@dataclass(frozen=True)
class CollectionGroups:
    group: type
    member: type
    error_prefix: str

    def owned(self, db: Session, user_id, group_id):
        row = db.scalar(select(self.group).where(self.group.user_id == user_id, self.group.id == group_id))
        if row is None:
            raise HTTPException(404, detail=f'{self.error_prefix}_not_found')
        return row

    def require_available_name(self, db: Session, user_id, name, group_id=None):
        query = select(self.group.id).where(self.group.user_id == user_id, self.group.name == name)
        if group_id is not None:
            query = query.where(self.group.id != group_id)
        if db.scalar(query) is not None:
            raise HTTPException(409, detail=f'{self.error_prefix}_name_taken')

    def list_records(self, db: Session, user_id):
        rows = db.scalars(select(self.group).where(self.group.user_id == user_id).order_by(
            self.group.created_at, self.group.id))
        return [group_record(row) for row in rows]

    def create(self, db: Session, user_id, name):
        lock_user(db, user_id)
        self.require_available_name(db, user_id, name)
        row = self.group(id=str(uuid.uuid4()), user_id=user_id, name=name, created_at=int(time.time()))
        db.add(row)
        return group_record(row)

    def rename(self, db: Session, user_id, group_id, name):
        lock_user(db, user_id)
        row = self.owned(db, user_id, group_id)
        self.require_available_name(db, user_id, name, group_id)
        row.name = name
        return group_record(row)

    def remove(self, db: Session, user_id, group_id):
        lock_user(db, user_id)
        row = self.owned(db, user_id, group_id)
        db.execute(update(self.member).where(self.member.user_id == user_id, self.member.group_id == row.id)
                   .values(group_id=None))
        db.delete(row)
