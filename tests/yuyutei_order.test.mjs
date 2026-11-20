import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const source = await readFile(new URL('../src/tcg_toolkit/static/collection/yuyutei-order.js', import.meta.url));
const {parseYuyuteiOrder} = await import('data:text/javascript;base64,' + source.toString('base64'));

test('parses the mixed WSR and WS purchase, preserving each printing, quantity and unit price', () => {
  const lines = [
    '--------------------------------', '■ご注文商品', '--------------------------------',
    'OS01/R01-047S SR 興味本位の身体検査 180円 数量:6 小計1,080円',
    'OS01/R01-050S SR 紅に染まる時間 180円 数量:3 小計540円',
    'OS01/R01-026 RR 強がるオトナのSecret Labo 茉優 180円 数量:6 小計1,080円',
    'OS01/R01-046S SR 研究員のお姉さん 茉優 220円 数量:10 小計2,200円',
    'OS01/R01-045S SR 幸せな２人の朝 茉優 180円 数量:2 小計360円',
    'OS01/R01-037S SR お姉さんの振る舞い 茉優 120円 数量:2 小計240円',
    'OS01/R01-036S SR 無茶な接触 茉優 120円 数量:2 小計240円',
    'OS01/R01-031S SR 先で待つ 茉優 180円 数量:5 小計900円',
    'OS01/R01-026OFR OFR 強がるオトナのSecret Labo 茉優 680円 数量:4 小計2,720円',
    'BD/WE49-56SP SP “お待たせいたしました”椎名立希(サイン入り) 5,980円 数量:1 小計5,980円',
    'BD/W125-075SP SP 一生の誓い 椎名立希(サイン入り) 2,480円 数量:1 小計2,480円',
    '─────────────────────────────',
  ];
  const {items, errors} = parseYuyuteiOrder(lines.map(line => line + '\\').join('\r\n'));
  assert.deepEqual(errors, []);
  assert.deepEqual(items.map(item => [item.card_number, item.quantity, item.price]), [
    ['OS01/R01-047S', 6, 180], ['OS01/R01-050S', 3, 180], ['OS01/R01-026', 6, 180],
    ['OS01/R01-046S', 10, 220], ['OS01/R01-045S', 2, 180], ['OS01/R01-037S', 2, 120],
    ['OS01/R01-036S', 2, 120], ['OS01/R01-031S', 5, 180], ['OS01/R01-026OFR', 4, 680],
    ['BD/WE49-56SP', 1, 5980], ['BD/W125-075SP', 1, 2480],
  ]);
  assert.deepEqual(items[2], {line: 6, card_number: 'OS01/R01-026', rarity: 'RR', name: '強がるオトナのSecret Labo 茉優',
    quantity: 6, price: 180, currency: 'JPY'});
  assert.equal(items[4].name, '幸せな２人の朝 茉優');
});

test('parses pasted product lines from multiple games without a heading, keeping distinct purchases', () => {
  const rows = ['LO-1606-S', 'YZ-001', '01-011a', 'BP01-U01', '01-011a', '0007', 'PR001', 'PR', '特典・01'];
  const receipt = rows.map(number => number + '　R　カード　１，０００円　数量：２　小計：２，０００円').join('\n');
  const {items, errors} = parseYuyuteiOrder(receipt);
  assert.deepEqual(errors, []);
  assert.deepEqual(items.map(item => item.card_number), rows);
  assert.ok(items.every(item => item.price === 1000 && item.quantity === 2));
});

test('reports malformed product lines, retains valid neighbours and ignores other mail sections', () => {
  const lines = [
    '■ご注文情報', '1234-5678', '■ご注文商品', '───',
    'OS01/R01-026 RR 茉優 180円 数量:0 小計0円',
    'BD/WE49-56SP SP カード 5,98円 数量:1 小計5,980円',
    'BD/W125-075SP SP カード 120円',
    '01-011a R カード 120円 数量:3',
    '??? R カード 120円 数量:1 小計120円',
    '───', '■配送先', '1234-5678',
  ];
  const {items, errors} = parseYuyuteiOrder(lines.join('\n'));
  assert.deepEqual(items.map(item => item.line), [8]);
  assert.deepEqual(errors, [5, 6, 7, 9].map(line => ({line, text: lines[line - 1]})));
});
