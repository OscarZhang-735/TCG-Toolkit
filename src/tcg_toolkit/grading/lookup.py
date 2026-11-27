"""Provider registry, certificate presentation and lookup lifecycle."""
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from fastapi import HTTPException

from ..persistence.models import User
from .adapters import fetch_ars, fetch_cgc, fetch_pcgcn, fetch_psa
from .concurrency import LookupRegistry, LookupSlots, raise_if_cancelled


@dataclass(frozen=True)
class GradingProvider:
    """One grading company. ``scan_patterns`` recognise the URL in its slab QR codes.

    Patterns are matched case-insensitively against the whole scanned text and group 1
    is the certificate number. The browser compiles the same strings with ``RegExp``,
    so they may only use syntax both engines share (no named groups or inline flags).
    """
    id: str
    name: str
    fetch: Callable[[str], dict]
    timeout_seconds: int
    scan_patterns: tuple[str, ...] = ()
    # Saved cards show this field as their name when present (see ``present``).
    title_field: str = ''
    # Fields that only translate the others; the web UI folds them away.
    english_field: Callable[[str], bool] | None = None


# Insertion order is the order the web UI lists providers in.
PROVIDERS = {provider.id: provider for provider in (
    # Labels encode https://www.psacard.com/cert/<n>/; the site also adds /psa and locale paths.
    GradingProvider('psa', 'PSA', fetch_psa, 225, (
        r'^https?://(?:www\.)?psacard\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?cert/([0-9]{1,32})(?:/[^?#]*)?(?:[?#].*)?$',)),
    # Labels encode https://www.cgccards.com/certlookup/<n>/<grade>/; older cards point at cgccomics.
    GradingProvider('cgc', 'CGC', fetch_cgc, 225, (
        r'^https?://(?:www\.)?cgc(?:cards|comics)\.com/certlookup/([0-9]{1,32})(?:/[^?#]*)?(?:[?#].*)?$',)),
    # Labels encode https://ars-grading.com/grading/searchNumber?id=<n>.
    # The Japanese card name is the original; ARS repeats every field under an English label.
    GradingProvider('ars', 'ARS', fetch_ars, 40, (
        r'^https?://(?:www\.)?ars-grading\.com/grading/searchNumber\?(?:[^#]*&)?id=([0-9]{1,32})(?:[&#].*)?$',),
        title_field='カード名称', english_field=str.isascii),
    # PCG-CN's name is a Chinese translation; c2j keeps the card's original name.
    GradingProvider('pcgcn', 'PCG-CN', fetch_pcgcn, 40, (
        r'^https?://card\.pcgcard\.cn/(?:card/?|h5/index\.html\#/pages/f/r)\?(?:[^#]*&)?query=([0-9]{1,32})(?:&[^#]*)?$',),
        title_field='Original Name'),
)}


def present(provider_id, certificate):
    """(title, fields, english_fields) for showing a saved certificate.

    Applied when reading, so cards saved before a provider's rule changed show the same
    way. Lookup responses themselves keep the parser's title and every field.
    """
    provider = PROVIDERS.get(provider_id)
    fields = dict(certificate.get('fields') or {})
    title = certificate.get('title') or ''
    if provider is None:
        return title, fields, {}
    if provider.title_field:
        title = fields.get(provider.title_field) or title
    english = {key: value for key, value in fields.items() if provider.english_field and provider.english_field(key)}
    return title, {key: value for key, value in fields.items() if key not in english}, english


LOOKUPS = LookupRegistry()
LOOKUP_SLOTS = LookupSlots()
DAILY_PAID_LOOKUP_LIMIT = 20


def normalize_request(provider_id, cert_number):
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        raise HTTPException(422, detail='unsupported_grading_provider')
    cert_number = cert_number.strip()
    if not re.fullmatch(r'[0-9]{1,32}', cert_number):
        raise HTTPException(422, detail='invalid_cert_number')
    return provider, cert_number


def reserve_paid_lookup(database, user_id):
    """Commit the attempt before contacting the provider, then release the account lock."""
    with database.session() as db:
        user = db.get_one(User, user_id, with_for_update=True)
        today = datetime.now(UTC).date()
        if user.grading_lookup_date != today:
            user.grading_lookup_date = today
            user.grading_lookup_count = 0
        if user.grading_lookup_count >= DAILY_PAID_LOOKUP_LIMIT:
            raise HTTPException(429, detail='grading_daily_limit')
        user.grading_lookup_count += 1


def lookup_certificate(provider_id, cert_number, user_id, database):
    """Validate, take a lookup slot and fetch. Blocking: call it from a worker thread."""
    provider, cert_number = normalize_request(provider_id, cert_number)
    raise_if_cancelled()
    with LOOKUP_SLOTS.hold(user_id):
        if provider.id in ('psa', 'cgc'):
            reserve_paid_lookup(database, user_id)
        try:
            result = provider.fetch(cert_number)
        except Exception:
            # A closed client surfaces as an upstream error; report the cancel instead.
            raise_if_cancelled()
            raise
    raise_if_cancelled()
    return result
