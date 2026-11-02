"""Generate local previews with sample data; never send email."""
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from tcg_toolkit.email_templates import verification_html, digest_html


def main():
    output = Path(__file__).resolve().parents[1] / 'data' / 'email-preview'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'verification.html').write_text(verification_html('482916', '设置订阅通知邮箱', 'zh-CN'), encoding='utf-8')
    before = dict(name='大人のいい女 美羽（サイン入り）', card_number='OS01/R01-053SEC',
                  rarity='SEC', price=6980, stock_quantity=0, stock_status='out_of_stock',
                  product_url='https://yuyu-tei.jp/sell/wsr/card/OS01/10240')
    after = {**before, 'price': 6480, 'stock_quantity': 3, 'stock_status': 'in_stock'}
    # Cover a drop, a rise with unchanged stock, and a sell-out.
    rising = dict(name='バニーガール 詩織', card_number='OS03/R03-021R', rarity='R', price=380, stock_quantity=12,
                  stock_status='in_stock', product_url='https://yuyu-tei.jp/sell/wsr/card/os03/10180')
    selling = dict(name='真面目な幼馴染 黒木', card_number='OS03/R03-031SP', rarity='SP', price=2980, stock_quantity=1,
                   stock_status='in_stock', product_url='https://yuyu-tei.jp/sell/wsr/card/os03/10193')
    now = int(datetime.now().timestamp())
    changes = [dict(before=before, after=after, checked_at=now),
               dict(before=rising, after={**rising, 'price': 480}, checked_at=now),
               dict(before=selling, after={**selling, 'stock_quantity': 0, 'stock_status': 'out_of_stock'}, checked_at=now)]
    (output / 'subscription.html').write_text(digest_html(datetime.now().date(), changes), encoding='utf-8')
    (output / 'index.html').write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TCG Toolkit 邮件预览</title>
<style>body{margin:0;background:#e2e8f0;font-family:Arial,sans-serif;color:#172033}header{padding:24px 32px}h1{margin:0 0 8px;font-size:24px}p{color:#475569}main{display:grid;grid-template-columns:1fr 1fr;gap:20px;padding:0 24px 24px}iframe{width:100%;height:850px;border:0;border-radius:14px}h2{font-size:16px}@media(max-width:850px){main{grid-template-columns:1fr}}</style>
<header><h1>TCG Toolkit · 邮件预览</h1><p>示例数据 · 不发送邮件。验证码适用于五种验证操作，订阅通知按用户汇总。</p></header>
<main><section><h2>01 / 验证码</h2><iframe title="验证码邮件" src="verification.html"></iframe></section><section><h2>02 / 订阅通知</h2><iframe title="订阅通知邮件" src="subscription.html"></iframe></section></main></html>''', encoding='utf-8')
    print(output / 'index.html')


if __name__ == '__main__':
    main()
