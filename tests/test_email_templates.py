from datetime import date

from tcg_toolkit.email_templates import BETTER, WORSE, digest_html, verification_html


def card(**values):
    return dict(dict(name='<Card & name>', card_number='OS01/R01-053SEC', rarity='SEC', price=6980,
                     stock_quantity=0, stock_status='out_of_stock',
                     product_url='https://yuyu-tei.jp/sell/wsr/card/OS01/10240?a="b"'), **values)


def test_verification_states_purpose_and_code_in_both_languages():
    zh = verification_html('012345', '设置订阅通知邮箱', 'zh-CN')
    assert '你的验证码' in zh and '用于设置订阅通知邮箱，10 分钟内有效。' in zh and '012345' in zh
    en = verification_html('012345', 'reset your password', 'en')
    assert 'Use it to reset your password. It expires in 10 minutes.' in en and 'lang="en"' in en
    assert '验证码' not in en


def test_digest_marks_direction_and_simplifies_card_metadata():
    before, after = card(), card(price=6480, stock_quantity=3, stock_status='in_stock')
    rising = card(card_number='PR-001', rarity='PR+', stock_quantity=2, stock_status='in_stock')
    html = digest_html(date(2026, 11, 1), [
        dict(before=before, after=after, checked_at=1_800_000_000),
        dict(before=rising, after={**rising, 'price': 7480, 'stock_quantity': 0, 'stock_status': 'out_of_stock'},
             checked_at=1_800_000_000)])
    assert '2 张订阅卡牌有变化' in html and '2026-11-01 每日检查 · 普通品相' in html
    assert '&lt;Card &amp; name&gt;' in html and '<Card' not in html
    assert 'href="https://yuyu-tei.jp/sell/wsr/card/OS01/10240?a=&quot;b&quot;"' in html
    # A rarity already in the card number is not repeated; a different one is shown.
    assert 'OS01/R01-053SEC · SEC' not in html and 'PR-001 · PR+' in html
    assert f'color:{BETTER}">6,480 JPY' in html and f'color:{BETTER}">3 张' in html
    assert f'color:{WORSE}">7,480 JPY' in html and f'color:{WORSE}">缺货' in html


def test_digest_shows_unchanged_value_without_arrow():
    item = card(stock_quantity=5, stock_status='in_stock')
    html = digest_html(date(2026, 11, 1), [dict(before=item, after={**item, 'price': 100}, checked_at=1_800_000_000)])
    assert '5 张</span></td>' in html and '5 张</span><span' not in html
