"""Typed ORM entities matching the deployed PostgreSQL schema.

API/search models live in models.py. These entities are internal persistence
objects; response serializers explicitly select public fields.
"""
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Double, ForeignKey, Identity, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {str: Text, int: BigInteger}


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column()
    password_hash: Mapped[str]
    created_at: Mapped[int]
    email: Mapped[str | None] = mapped_column(unique=True)
    email_verified_at: Mapped[int | None]
    __table_args__ = (Index("users_username", func.lower(username), unique=True),)


class LoginSession(Base):
    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[int]
    __table_args__ = (Index("sessions_expiry", "expires_at"), Index("sessions_user", "user_id"))


class Favorite(Base):
    __tablename__ = "favorites"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    game: Mapped[str]
    product_url: Mapped[str]
    snapshot: Mapped[str]
    created_at: Mapped[int]
    refreshed_at: Mapped[int | None]
    sort_id: Mapped[int] = mapped_column(Identity(always=True))
    __table_args__ = (UniqueConstraint("user_id", "game", "product_url"),)


class OwnedCard(Base):
    __tablename__ = "owned_cards"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    game: Mapped[str]
    card_number: Mapped[str]
    rarity: Mapped[str]
    name: Mapped[str]
    quantity: Mapped[int] = mapped_column()
    notes: Mapped[str]
    game_key: Mapped[str]
    number_key: Mapped[str]
    rarity_key: Mapped[str]
    created_at: Mapped[int]
    updated_at: Mapped[int]
    image_url: Mapped[str] = mapped_column(default="", server_default="")
    sort_id: Mapped[int] = mapped_column(Identity(always=True))
    __table_args__ = (
        CheckConstraint(quantity >= 0),
        UniqueConstraint("user_id", "game_key", "number_key", "rarity_key"),
    )


class CartOperation(Base):
    __tablename__ = "cart_operations"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    operation_key: Mapped[str] = mapped_column(primary_key=True)
    request_hash: Mapped[str]
    state: Mapped[str] = mapped_column()
    status_code: Mapped[int | None] = mapped_column(Integer)
    response_json: Mapped[str | None]
    created_at: Mapped[int]
    updated_at: Mapped[int]
    __table_args__ = (CheckConstraint(state.in_(("processing", "complete"))),)


class EmailChallenge(Base):
    __tablename__ = "email_challenges"

    id: Mapped[str] = mapped_column(primary_key=True)
    email: Mapped[str]
    purpose: Mapped[str]
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    code_hash: Mapped[str]
    created_at: Mapped[int]
    expires_at: Mapped[int]
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    state: Mapped[str] = mapped_column()
    __table_args__ = (
        CheckConstraint(state.in_(("pending", "active", "failed", "superseded", "used"))),
        Index("email_challenges_quota", "email", "created_at"),
        Index("email_challenges_user", "user_id"),
    )


class CurrencyRate(Base):
    __tablename__ = "rates"

    currency: Mapped[str] = mapped_column(primary_key=True)
    rate: Mapped[float | None] = mapped_column(Double)
    fetched_at: Mapped[float | None] = mapped_column(Double)
    attempted_at: Mapped[float | None] = mapped_column(Double)


class SchemaMigration(Base):
    """Compatibility marker used by existing release metadata and rollback checks."""
    __tablename__ = "schema_migrations"

    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
