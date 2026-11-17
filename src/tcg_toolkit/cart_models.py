"""Shared input schema for a YuYu-Tei cart offer."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CartTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    gid: Literal[7, 8, 11, 34, 47, 56]
    ver: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-][A-Za-z0-9_.-]*$')
    cid: str = Field(min_length=1, max_length=32, pattern=r'^[0-9]+$')
    kizu: int = Field(default=0, strict=True, ge=0, le=1)
