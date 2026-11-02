from datetime import date, datetime

import pytest

from tcg_toolkit.email_templates import (BETTER, VERIFICATION_SUBJECT, WORSE, digest_html, favorites_url,
                                         verification_html, verification_text)


def card(**values):
    return dict(dict(name='<Card & name>', card_number='OS01/R01-053SEC', rarity='SEC', price=6980,
                     stock_quantity=0, stock_status='out_of_stock',
                     product_url='https://yuyu-tei.jp/sell/wsr/card/OS01/10240?a="b"'), **values)


def chinese_before_english(text, chinese, english):
    return chinese in text and english in text and text.index(chinese) < text.index(english)


def test_verification_is_bilingual_chinese_first():
    html = verification_html('012345', 'password_reset')
    assert '012345' in html
    assert chinese_before_english(html, '你的验证码', 'Your verification code')
    assert chinese_before_english(html, '用于重设密码，10 分钟内有效。', 'Use it to reset your password. It expires in 10 minutes.')
    assert chinese_before_english(html, '此邮件由 TCG Toolkit 自动发送', 'Sent automatically by TCG Toolkit')
    text = verification_text('012345', 'notification_email')
    assert chinese_before_english(text, '你正在设置订阅通知邮箱。', 'You requested to set your subscription notification email.')
    assert text.count('012345') == 2
    assert chinese_before_english(VERIFICATION_SUBJECT, '邮箱验证码', 'Email verification code')


def test_digest_marks_direction_and_simplifies_card_metadata():
    before, after = card(), card(price=6480, stock_quantity=3, stock_status='in_stock')
    rising = card(card_number='PR-001', rarity='PR+', stock_quantity=2, stock_status='in_stock')
    html = digest_html(date(2026, 11, 1), [
        dict(before=before, after=after, checked_at=1_800_000_000),
        dict(before=rising, after={**rising, 'price': 7480, 'stock_quantity': 0, 'stock_status': 'out_of_stock'},
             checked_at=1_800_000_000)])
    assert chinese_before_english(html, '2 张订阅卡牌有变化', '2 subscribed cards changed')
    assert f"检查时间 Checked at：{datetime.fromtimestamp(1_800_000_000).astimezone():%Y-%m-%d %H:%M}</p>" in html
    assert '价格 Price' in html and '库存 Stock' in html and '在游游亭查看 View on YuYu-Tei' in html
    assert chinese_before_english(html, '每天中午检查订阅的卡牌', 'Subscribed cards are checked daily at noon')
    assert '&lt;Card &amp; name&gt;' in html and '<Card' not in html
    assert 'href="https://yuyu-tei.jp/sell/wsr/card/OS01/10240?a=&quot;b&quot;"' in html
    # Every card shows its rarity as a badge before the card number.
    assert '>SEC</span>OS01/R01-053SEC</div>' in html and '>PR+</span>PR-001</div>' in html
    assert f'color:{BETTER}">6,480 JPY' in html and f'color:{BETTER}">3</strong>' in html
    assert f'color:{WORSE}">7,480 JPY' in html and f'color:{WORSE}">0</strong>' in html


def test_digest_shows_unchanged_value_without_arrow():
    item = card(stock_quantity=5, stock_status='in_stock')
    html = digest_html(date(2026, 11, 1), [dict(before=item, after={**item, 'price': 100}, checked_at=1_800_000_000)])
    assert '>5</span></td>' in html and '>5</span><span' not in html


def test_digest_omits_badge_without_rarity():
    item = card(rarity=None, stock_quantity=5, stock_status='in_stock')
    html = digest_html(date(2026, 11, 1), [dict(before=item, after={**item, 'price': 100}, checked_at=1_800_000_000)])
    assert 'border-radius:4px' not in html


@pytest.mark.parametrize('value,expected', [
    ('', 'https://oscarzhang-735.github.io/TCG-Toolkit/#favorites'),
    ('http://localhost:18080', 'http://localhost:18080/#favorites'),
    ('https://example.com/app/#search', 'https://example.com/app/#favorites'),
    ('javascript:alert(1)', 'https://oscarzhang-735.github.io/TCG-Toolkit/#favorites'),
])
def test_digest_footer_links_to_site_favorites(monkeypatch, value, expected):
    monkeypatch.setenv('TCG_SITE_URL', value)
    assert favorites_url() == expected
    item = card(stock_quantity=5, stock_status='in_stock')
    html = digest_html(date(2026, 11, 1), [dict(before=item, after={**item, 'price': 100}, checked_at=1_800_000_000)])
    assert f'href="{expected}"' in html and '>前往 TCG Toolkit 查看 · Open TCG Toolkit</a>' in html
