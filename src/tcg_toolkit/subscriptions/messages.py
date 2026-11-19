"""Compose the price and stock change digest."""
from datetime import datetime
from email.message import EmailMessage

from ..mail.templates import digest_html, digest_subject, digest_titles, favorites_url


def digest_message(recipient, email_id, run_date, changes):
    message = EmailMessage()
    message['To'] = recipient
    message['Subject'] = digest_subject(run_date)
    message['Message-ID'] = f'<{email_id}@tcg-toolkit.local>'
    # Bilingual like the HTML part: Chinese first, then English, line by line.
    zh_title, en_title = digest_titles(len(changes))
    text = [f'{run_date} {zh_title}（普通品相）', f'{run_date} {en_title} (normal condition)', '']
    for change in changes:
        before, after = change['before'], change['after']
        title = ' · '.join(filter(None, [after['name'], after.get('rarity'), after['card_number']]))
        price = f"价格 Price：{before['price']:,} → {after['price']:,} JPY"
        quantity = f"库存 Stock：{before['stock_quantity'] or 0:,} → {after['stock_quantity'] or 0:,}"
        checked = '检查时间 Checked at：' + datetime.fromtimestamp(change['checked_at']).astimezone().isoformat(timespec='seconds')
        url = after['product_url']
        text.extend([title, price, quantity, checked, url, ''])
    text.append(f'前往 TCG Toolkit 查看 · Open TCG Toolkit：{favorites_url()}')
    message.set_content('\n'.join(text))
    message.add_alternative(digest_html(run_date, changes), subtype='html')
    return message
