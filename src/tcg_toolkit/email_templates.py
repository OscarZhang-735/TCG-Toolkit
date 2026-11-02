"""Two email families sharing a table layout and inline, email-safe styles.

Colours follow the web UI's neutral palette (static/theme.css); no external
fonts, images or scripts, so clients that block remote content render fully.
"""
from datetime import datetime
from html import escape

FONT = "-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',Arial,sans-serif"
TEXT, MUTED, BORDER, SURFACE = '#2b2b2b', '#757575', '#e6e6e6', '#fafafa'
BETTER, WORSE = '#15803d', '#b45309'


def frame(title, preheader, content, footer, language='zh-CN'):
    return f'''<!doctype html><html lang="{language}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light"><title>{escape(title)}</title></head>
<body style="margin:0;padding:0;background:#f2f2f2;color:{TEXT};font-family:{FONT}">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all">{escape(preheader)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f2f2f2"><tr><td align="center" style="padding:32px 12px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border:1px solid {BORDER};border-radius:12px">
<tr><td style="padding:24px 28px 0;font-size:14px;font-weight:bold;letter-spacing:.2px">TCG Toolkit</td></tr>
<tr><td style="padding:20px 28px 28px">{content}</td></tr>
</table>
<p style="max-width:560px;margin:16px auto 0;font-size:12px;line-height:1.7;color:{MUTED}">{footer}</p>
</td></tr></table></body></html>'''


def verification_html(code, action, language):
    zh = language == 'zh-CN'
    title = '你的验证码' if zh else 'Your verification code'
    purpose = f'用于{action}，10 分钟内有效。' if zh else f'Use it to {action}. It expires in 10 minutes.'
    safety = '请勿将验证码告诉他人。如非本人操作，请忽略此邮件。' if zh else "Don't share this code. If you didn't request it, ignore this email."
    footer = '此邮件由 TCG Toolkit 自动发送，请勿回复。' if zh else 'Sent automatically by TCG Toolkit. Please do not reply.'
    content = f'''<h1 style="margin:0 0 8px;font-size:20px;line-height:1.4">{title}</h1>
<p style="margin:0 0 20px;font-size:14px;line-height:1.7;color:{MUTED}">{escape(purpose)}</p>
<div style="padding:20px 12px;background:{SURFACE};border:1px solid {BORDER};border-radius:10px;text-align:center;font-family:Consolas,'SF Mono',Menlo,monospace;font-size:32px;font-weight:bold;letter-spacing:8px">{escape(code)}</div>
<p style="margin:20px 0 0;font-size:13px;line-height:1.7;color:{MUTED}">{safety}</p>'''
    return frame(title, purpose, content, footer, language)


def _stock(item):
    return '缺货' if item['stock_status'] != 'in_stock' or not item['stock_quantity'] else f"{item['stock_quantity']:,} 张"


def _change_row(label, old, new, improved):
    if old == new:
        value = f'<span style="color:{TEXT}">{escape(new)}</span>'
    else:
        color = BETTER if improved else WORSE
        value = (f'<span style="color:{MUTED};text-decoration:line-through">{escape(old)}</span>'
                 f'<span style="color:{MUTED}">&nbsp;→&nbsp;</span><strong style="color:{color}">{escape(new)}</strong>')
    return (f'<tr><td style="padding:4px 0;width:48px;font-size:13px;color:{MUTED}">{label}</td>'
            f'<td style="padding:4px 0;font-size:14px">{value}</td></tr>')


def digest_html(run_date, changes):
    title = f'{len(changes)} 张订阅卡牌有变化'
    cards = []
    for index, change in enumerate(changes):
        before, after = change['before'], change['after']
        # Rarity is usually already the card number suffix (e.g. …053SEC).
        rarity = after.get('rarity') or ''
        meta = [after['card_number']] + ([rarity] if rarity and not after['card_number'].endswith(rarity) else [])
        meta.append(datetime.fromtimestamp(change['checked_at']).astimezone().strftime('%H:%M 检查'))
        rows = (_change_row('价格', f"{before['price']:,} JPY", f"{after['price']:,} JPY", after['price'] < before['price'])
                + _change_row('库存', _stock(before), _stock(after), (after['stock_quantity'] or 0) > (before['stock_quantity'] or 0)))
        divider = f'border-top:1px solid {BORDER};' if index else ''
        cards.append(f'''<tr><td style="{divider}padding:16px 0">
<a href="{escape(after['product_url'], quote=True)}" style="font-size:15px;font-weight:bold;line-height:1.5;color:{TEXT};text-decoration:none;overflow-wrap:anywhere">{escape(after['name'])}</a>
<div style="margin:2px 0 8px;font-size:12px;color:{MUTED}">{escape(' · '.join(meta))}</div>
<table role="presentation" cellspacing="0" cellpadding="0">{rows}</table>
<a href="{escape(after['product_url'], quote=True)}" style="display:inline-block;margin-top:8px;font-size:13px;color:{TEXT};text-decoration:underline">在游游亭查看</a>
</td></tr>''')
    content = f'''<h1 style="margin:0 0 8px;font-size:20px;line-height:1.4">{title}</h1>
<p style="margin:0 0 8px;font-size:14px;line-height:1.7;color:{MUTED}">{escape(str(run_date))} 每日检查 · 普通品相</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0">{''.join(cards)}</table>'''
    footer = '每天中午检查订阅的卡牌，仅在价格或库存变化时发送。可在收藏夹中取消订阅。'
    return frame(title, f'{run_date} · {title}', content, footer)
