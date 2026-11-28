"""Inventory JSON/XLSX transfer and transactional group assignment."""
import json
import time
import uuid
from io import BytesIO
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException
from fastapi import HTTPException
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import ValidationError
from sqlalchemy import select, update as sql_update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from xlsxwriter import Workbook

from .schemas import CardInput, card_record, card_values
from ..persistence.models import CardGroup, OwnedCard
from .groups import lock_user


# 'group' holds the group name (not its ID), so files carry groups between accounts.
EXPORT_COLUMNS = ('game', 'card_number', 'rarity', 'name', 'quantity', 'price', 'currency', 'notes', 'group',
                  'image_url', 'id', 'created_at', 'updated_at')
XLSX_MEDIA_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
IMPORT_LIMIT = 10 * 1024 * 1024
XLSX_IMPORT_MAX_ROWS = 10_000  # Includes the header and blank rows.
REQUIRED_COLUMNS = {key for key, field in CardInput.model_fields.items() if field.is_required()}
IMPORT_COLUMNS = {*CardInput.model_fields, 'group'}
EXPORT_WIDTHS = {'game': 20, 'card_number': 20, 'rarity': 20, 'name': 36, 'quantity': 18, 'price': 18, 'currency': 12,
                 'notes': 40, 'group': 24, 'image_url': 40, 'id': 40, 'created_at': 20, 'updated_at': 20}
# XLSX headings are for people; JSON keys stay stable so files remain machine-readable.
EXPORT_HEADINGS = {
    'en': {'sheet': 'Cards', 'game': 'Game', 'card_number': 'Number', 'rarity': 'Rarity', 'name': 'Name',
           'quantity': 'Quantity', 'price': 'Price', 'currency': 'Currency', 'notes': 'Notes', 'group': 'Group', 'image_url': 'Image URL', 'id': 'ID',
           'created_at': 'Created at', 'updated_at': 'Updated at'},
    'zh-CN': {'sheet': '卡牌', 'game': '游戏', 'card_number': '编号', 'rarity': '稀有度', 'name': '名称',
              'quantity': '拥有量', 'price': '价格', 'currency': '币种', 'notes': '备注', 'group': '分组', 'image_url': '图片地址', 'id': 'ID',
              'created_at': '创建时间', 'updated_at': '更新时间'},
}

# Import reads raw keys and every exported heading language, so any export file round-trips.
HEADING_KEYS = {label.casefold(): key for labels in [{key: key for key in EXPORT_COLUMNS}, *EXPORT_HEADINGS.values()]
                for key, label in labels.items() if key != 'sheet'}


def export_card_rows(store, user_id, format, *, language=None, fields=None, games=None, group_id=None):
    """Export one account's inventory without depending on HTTP routes."""
    with store.session() as db:
        query = select(OwnedCard).where(OwnedCard.user_id == user_id)
        if group_id is not None:
            if db.scalar(select(CardGroup.id).where(CardGroup.user_id == user_id, CardGroup.id == group_id)) is None:
                raise HTTPException(404, detail='card_group_not_found')
            query = query.where(OwnedCard.group_id == group_id)
        items = [card_record(row) for row in db.scalars(query.order_by(
            OwnedCard.created_at.desc(), OwnedCard.sort_id.desc()))]
        names = dict(db.execute(select(CardGroup.id, CardGroup.name).where(CardGroup.user_id == user_id)).all())
    if games is not None:
        wanted = {game.strip().casefold() for game in games}
        items = [item for item in items if item['game'].casefold() in wanted]
    # Canonical column order, regardless of the order fields were requested in.
    columns = [key for key in EXPORT_COLUMNS if fields is None or key in fields]
    # Ungrouped cards export a null group (an empty XLSX cell).
    items = [{key: names.get(item['group_id']) if key == 'group' else item[key] for key in columns} for item in items]
    if format == 'json':
        return json.dumps({'items': items}, ensure_ascii=False, indent=2), 'application/json'
    # Without a language, headings are the raw keys (stable for scripts and re-import).
    labels = EXPORT_HEADINGS.get(language) or {'sheet': 'Cards', **{key: key for key in EXPORT_COLUMNS}}
    output = BytesIO()
    with Workbook(output, {'in_memory': True}) as workbook:
        sheet = workbook.add_worksheet(labels['sheet'])
        bold = workbook.add_format({'bold': True, 'bg_color': '#E8EEF7'})
        sheet.write_row(0, 0, [labels[key] for key in columns], bold)
        sheet.freeze_panes(1, 0)
        for column_index, key in enumerate(columns):
            sheet.set_column(column_index, column_index, EXPORT_WIDTHS[key])
        for row_index, item in enumerate(items, 1):
            for column_index, key in enumerate(columns):
                value = item[key]
                if value is None:
                    continue
                # Excel preserves only 15 numeric digits; retain larger quantities as text.
                if isinstance(value, (int, float)) and abs(value) < 10**15:
                    sheet.write_number(row_index, column_index, value)
                else:
                    # Explicit strings preserve card numbers and never execute formulas.
                    result = sheet.write_string(row_index, column_index, str(value))
                    if result == -2:
                        raise HTTPException(422, detail='export_xlsx_text_too_long')
        sheet.autofilter(0, 0, len(items), len(columns) - 1)
    return output.getvalue(), XLSX_MEDIA_TYPE


def xlsx_import_rows(content):
    with ZipFile(BytesIO(content)) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > 64 * 1024 * 1024:
            raise HTTPException(413, detail='cards_import_too_large')
    workbook = load_workbook(BytesIO(content), read_only=True, keep_links=False)
    try:
        sheet = workbook.worksheets[0]
        sheet.reset_dimensions()
        rows = sheet.iter_rows(max_col=len(EXPORT_COLUMNS) + 1)
        headings = [cell.value for cell in next(rows, ())]
        while headings and headings[-1] is None:
            headings.pop()
        headers = [HEADING_KEYS.get(value.strip().casefold()) if isinstance(value, str) else None
                   for value in headings]
        if (any(not isinstance(key, str) or key not in EXPORT_COLUMNS for key in headers)
                or len(set(headers)) != len(headers) or not REQUIRED_COLUMNS.issubset(headers)):
            raise HTTPException(422, detail='cards_import_headers')
        for line, cells in enumerate(rows, 2):
            if line > XLSX_IMPORT_MAX_ROWS:
                raise HTTPException(413, detail='cards_import_too_large')
            if not any(cell.value is not None for cell in cells):
                continue
            if any(cell.value is not None for cell in cells[len(headers):]):
                raise HTTPException(422, detail={'code': 'cards_import_row', 'row': line})
            item = {}
            for key, cell in zip(headers, cells):
                if key not in IMPORT_COLUMNS or cell.value is None:
                    continue
                if cell.data_type in ('f', 'e'):
                    raise HTTPException(422, detail={'code': 'cards_import_row', 'row': line})
                value = cell.value
                if key == 'quantity' and isinstance(value, str) and value.isascii() and value.isdecimal():
                    value = int(value) if len(value) <= 16 else value
                if key in ('card_number', 'group') and type(value) is int:
                    value = str(value)
                item[key] = value
            yield line, item
    finally:
        workbook.close()


def import_group_name(value):
    """Group name from an import row; blank or missing means ungrouped."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('Group must be text')
    value = value.strip()
    if len(value) > 100 or '\0' in value:
        raise ValueError('Invalid group name')
    value.encode('utf-8')
    return value or None


def assign_import_groups(db: Session, user_id, imported, wanted, now):
    """Put newly imported cards into groups by name, creating missing groups in file order."""
    members = {}
    for card_id, name in wanted:
        if name is not None and card_id in imported:
            members.setdefault(name, []).append(card_id)
    if not members:
        return
    existing = dict(db.execute(select(CardGroup.name, CardGroup.id).where(
        CardGroup.user_id == user_id, CardGroup.name.in_(list(members)))).all())
    missing = [name for name in members if name not in existing]
    # New groups share one timestamp; sorted IDs keep their (created_at, id) order matching the file.
    for name, group_id in zip(missing, sorted(str(uuid.uuid4()) for _ in missing)):
        db.add(CardGroup(id=group_id, user_id=user_id, name=name, created_at=now))
        existing[name] = group_id
    db.flush()
    for name, card_ids in members.items():
        db.execute(sql_update(OwnedCard).where(OwnedCard.user_id == user_id, OwnedCard.id.in_(card_ids))
                   .values(group_id=existing[name]))


def import_card_rows(content, format, store, user_id):
    now = int(time.time())
    try:
        if format == 'json':
            data = json.loads(content.decode('utf-8-sig'))
            items = data.get('items') if isinstance(data, dict) else data
            if not isinstance(items, list):
                raise ValueError('Expected a list of cards')
            rows = enumerate(items, 1)
        else:
            rows = xlsx_import_rows(content)
        cards, wanted = [], []
        for line, item in rows:
            try:
                body = CardInput.model_validate(item)
                group = import_group_name(item.get('group'))
                fields = card_values(body)
                for value in fields.values():
                    if isinstance(value, str):
                        if '\0' in value:
                            raise ValueError('NUL character')
                        value.encode('utf-8')
            except (ValidationError, ValueError):
                raise HTTPException(422, detail={'code': 'cards_import_row', 'row': line}) from None
            card_id = str(uuid.uuid4())
            cards.append(dict(id=card_id, user_id=user_id, created_at=now, updated_at=now, **fields))
            wanted.append((card_id, group))
    except (BadZipFile, InvalidFileException, ParseError, DefusedXmlException, KeyError, IndexError,
            ValueError, TypeError, RecursionError, OSError):
        raise HTTPException(422, detail='cards_import_invalid') from None
    if not cards:
        return {'imported': 0, 'skipped': 0}
    statement = insert(OwnedCard).on_conflict_do_nothing(
        index_elements=['user_id', 'game_key', 'number_key', 'rarity_key']).returning(OwnedCard.id)
    # Skipped duplicates keep their existing group; only new cards join the file's groups.
    with store.session() as db:
        lock_user(db, user_id)
        imported = set(db.scalars(statement, cards).all())
        assign_import_groups(db, user_id, imported, wanted, now)
    return {'imported': len(imported), 'skipped': len(cards) - len(imported)}


