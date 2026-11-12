"""Two email families sharing a table layout and inline, email-safe styles.

Every email is bilingual: Chinese first, then English. Colours follow the web
UI's neutral palette (static/theme.css); no external fonts, images or scripts,
so clients that block remote content render fully.
"""
from datetime import datetime
from html import escape
import os
from urllib.parse import urlsplit

FONT = "-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',Arial,sans-serif"
TEXT, MUTED, BORDER, SURFACE = '#2b2b2b', '#757575', '#e6e6e6', '#fafafa'
BETTER, WORSE = '#15803d', '#b45309'
DEFAULT_SITE_URL = 'https://oscarzhang-735.github.io/TCG-Toolkit/'
# Verification purpose -> (Chinese, English) action.
ACTIONS = {'register': ('注册账户', 'create an account'), 'bind': ('绑定邮箱', 'link your email'),
           'password_change': ('修改密码', 'change your password'), 'password_reset': ('重设密码', 'reset your password'),
           'notification_email': ('设置订阅通知邮箱', 'set your subscription notification email')}
VERIFICATION_SUBJECT = 'TCG Toolkit 邮箱验证码 · Email verification code'


def site_url():
    """Web UI address for email links; self-hosted deployments set TCG_SITE_URL."""
    value = os.getenv('TCG_SITE_URL', '').strip()
    url = urlsplit(value)
    return value if url.scheme in ('http', 'https') and url.netloc else DEFAULT_SITE_URL


def favorites_url():
    return site_url().split('#')[0].rstrip('/') + '/#favorites'


def _en(text, size=13):
    """English line under its Chinese counterpart."""
    return f'<div style="margin-top:2px;font-size:{size}px;line-height:1.6;color:{MUTED}">{text}</div>'


def frame(title, preheader, content, footer):
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
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


def verification_text(code, purpose):
    zh, en = ACTIONS[purpose]
    return (f'你正在{zh}。\n验证码：{code}\n验证码在 10 分钟内有效，请勿向他人透露。如果不是你本人操作，请忽略此邮件。\n\n'
            f'You requested to {en}.\nYour code: {code}\n'
            'This code expires in 10 minutes. Do not share it. If you did not request this, ignore this email.')


def verification_html(code, purpose):
    zh, en = ACTIONS[purpose]
    content = f'''<h1 style="margin:0;font-size:20px;line-height:1.4">你的验证码</h1>{_en('Your verification code', 14)}
<div style="margin:12px 0 20px;font-size:14px;line-height:1.7;color:{MUTED}">用于{escape(zh)}，10 分钟内有效。{_en(escape(f'Use it to {en}. It expires in 10 minutes.'))}</div>
<div style="padding:20px 12px;background:{SURFACE};border:1px solid {BORDER};border-radius:10px;text-align:center;font-family:Consolas,'SF Mono',Menlo,monospace;font-size:32px;font-weight:bold;letter-spacing:8px">{escape(code)}</div>
<p style="margin:20px 0 0;font-size:13px;line-height:1.7;color:{MUTED}">请勿将验证码告诉他人。如非本人操作，请忽略此邮件。<br>Do not share this code. If you did not request it, ignore this email.</p>'''
    footer = '此邮件由 TCG Toolkit 自动发送，请勿回复。<br>Sent automatically by TCG Toolkit. Please do not reply.'
    return frame('你的验证码 · Your verification code', f'用于{zh}，10 分钟内有效 · Expires in 10 minutes', content, footer)


def _change_row(label, old, new, improved):
    if old == new:
        value = f'<span style="color:{TEXT}">{escape(new)}</span>'
    else:
        color = BETTER if improved else WORSE
        value = (f'<span style="color:{MUTED};text-decoration:line-through">{escape(old)}</span>'
                 f'<span style="color:{MUTED}">&nbsp;→&nbsp;</span><strong style="color:{color}">{escape(new)}</strong>')
    return (f'<tr><td style="padding:4px 12px 4px 0;font-size:13px;color:{MUTED};white-space:nowrap">{label}</td>'
            f'<td style="padding:4px 0;font-size:14px">{value}</td></tr>')


def digest_titles(count):
    return f'{count} 张订阅卡牌有变化', f"{count} subscribed card{'' if count == 1 else 's'} changed"


def digest_subject(run_date):
    return f'TCG Toolkit 卡牌更新 · Card updates · {run_date}'


def digest_html(run_date, changes):
    zh_title, en_title = digest_titles(len(changes))
    checked = datetime.fromtimestamp(max(change['checked_at'] for change in changes)).astimezone().strftime('%Y-%m-%d %H:%M')
    cards = []
    for index, change in enumerate(changes):
        before, after = change['before'], change['after']
        rarity = after.get('rarity')
        badge = (f'<span style="display:inline-block;margin-right:6px;padding:0 6px;border:1px solid {BORDER};border-radius:4px;'
                 f'background:{SURFACE};font-size:11px;font-weight:bold;line-height:18px;color:{TEXT}">{escape(rarity)}</span>') if rarity else ''
        # Stock is a bare count so the comparison reads the same in both languages.
        old_stock, new_stock = before['stock_quantity'] or 0, after['stock_quantity'] or 0
        rows = (_change_row('价格 Price', f"{before['price']:,} JPY", f"{after['price']:,} JPY", after['price'] < before['price'])
                + _change_row('库存 Stock', f'{old_stock:,}', f'{new_stock:,}', new_stock > old_stock))
        divider = f'border-top:1px solid {BORDER};' if index else ''
        cards.append(f'''<tr><td style="{divider}padding:16px 0">
<a href="{escape(after['product_url'], quote=True)}" style="font-size:15px;font-weight:bold;line-height:1.5;color:{TEXT};text-decoration:none;overflow-wrap:anywhere">{escape(after['name'])}</a>
<div style="margin:4px 0 8px;font-size:12px;line-height:20px;color:{MUTED}">{badge}{escape(after['card_number'])}</div>
<table role="presentation" cellspacing="0" cellpadding="0">{rows}</table>
<a href="{escape(after['product_url'], quote=True)}" style="display:inline-block;margin-top:8px;font-size:13px;color:{TEXT};text-decoration:underline">在游游亭查看 View on YuYu-Tei</a>
</td></tr>''')
    content = f'''<h1 style="margin:0;font-size:20px;line-height:1.4">{zh_title}</h1>{_en(en_title, 14)}
<p style="margin:12px 0 8px;font-size:14px;line-height:1.7;color:{MUTED}">检查时间 Checked at：{escape(checked)}</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0">{''.join(cards)}</table>'''
    footer = ('按你设置的查询间隔检查订阅卡牌，仅在价格或库存变化时发送。<br>'
              'Subscribed cards are checked at your chosen interval; emails are sent only when price or stock changes.<br>'
              f'<a href="{escape(favorites_url(), quote=True)}" style="color:{TEXT};font-weight:bold;text-decoration:underline">'
              '前往 TCG Toolkit 查看 · Open TCG Toolkit</a>')
    return frame(f'{zh_title} · {en_title}', f'{run_date} · {zh_title} · {en_title}', content, footer)
