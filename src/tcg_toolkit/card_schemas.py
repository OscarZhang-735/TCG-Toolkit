"""Inventory card input, normalization and response serialization."""
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


GAMES = {'ws': 'WS', 'ws_en': 'WS EN', 'ws en': 'WS EN', 'wsr': 'WSR', 'pm': 'PM', 'sev': 'SEV', 'sev_en': 'SEV EN', 'sev en': 'SEV EN', 'lycee': 'Lycee',
         'chaos': 'ChaosTCG', 'chaostcg': 'ChaosTCG'}


# Currencies a unit price may be recorded in; the WebUI converts between them for display.
CURRENCIES = ('JPY', 'CNY', 'CAD')


class CardPriceInput(BaseModel):
    price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    currency: Literal[CURRENCIES] = 'JPY'

    @field_validator('currency', mode='before')
    @classmethod
    def currency_code(cls, value):
        # Missing (null) means the default; codes are case-insensitive, e.g. "cny".
        if value is None:
            return 'JPY'
        return value.strip().upper() if isinstance(value, str) else value


def price_record(row):
    return {'price': float(row.price) if row.price is not None else None, 'currency': row.currency}


class CardInput(CardPriceInput):
    game: str = Field(min_length=1)
    card_number: str = Field(min_length=1)
    rarity: str = Field(min_length=1)
    name: str = Field(min_length=1)
    quantity: int = Field(default=1, strict=True, ge=0, le=9007199254740991)
    notes: str = ''
    image_url: str = ''

    @field_validator('image_url', mode='before')
    @classmethod
    def image_location(cls, value):
        if value is None:
            return ''
        if not isinstance(value, str):
            raise ValueError('Invalid image URL')
        value = value.strip()
        if value:
            url = urlsplit(value)
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
                raise ValueError('Image URL must be HTTP or HTTPS')
        return value

    @field_validator('game', 'card_number', 'rarity', 'name', 'notes')
    @classmethod
    def clean(cls, value, info):
        value = value.strip()
        if info.field_name != 'notes' and not value:
            raise ValueError('Must not be blank')
        return GAMES.get(value.casefold(), value) if info.field_name == 'game' else value


class CardCreate(CardInput):
    group_id: str | None = Field(default=None, min_length=1)


class GroupName(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator('name', mode='before')
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class GroupTarget(BaseModel):
    group_id: str | None = Field(min_length=1)


def card_record(row):
    return {**{key: getattr(row, key) for key in ('id', 'group_id', 'game', 'card_number', 'rarity', 'name',
                                                'quantity', 'notes', 'image_url', 'created_at', 'updated_at')},
            **price_record(row)}


def card_values(body):
    return dict(game=body.game, card_number=body.card_number, rarity=body.rarity, name=body.name,
                quantity=body.quantity, price=body.price, currency=body.currency, notes=body.notes, image_url=body.image_url,
                game_key=body.game.casefold(), number_key=body.card_number.casefold(), rarity_key=body.rarity.casefold())


