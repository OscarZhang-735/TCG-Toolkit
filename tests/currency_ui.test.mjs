import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import test from 'node:test';
const source = await readFile(new URL('../src/tcg_toolkit/static/shared/currency.js', import.meta.url), 'utf8');
const {fetchRate, formatPrice} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
test('JPY makes no request; foreign currency requests one unit', async () => {
  assert.equal(await fetchRate('JPY',()=>assert.fail()),1);
  assert.equal(await fetchRate('CNY', async url=>{
    assert.equal(url,'/api/currency/convert?to=CNY&amount=1');
    return {ok:true,json:async()=>({from:'JPY',to:'CNY',amount:1,result:0.05})};
  }),0.05);
});
test('invalid or unsuccessful conversion is rejected', async () => {
  for (const result of [null,0,-1,'0.05',Infinity]) {
    await assert.rejects(fetchRate('CAD',async()=>({ok:true,json:async()=>({from:'JPY',to:'CAD',amount:1,result})})));
  }
  await assert.rejects(fetchRate('CAD',async()=>({ok:false,json:async()=>({detail:{}})})));
  await assert.rejects(fetchRate('CAD',async()=>{throw Error('offline');}));
});
test('format JPY as integers and converted amounts with two decimal places', () => {
  assert.match(formatPrice(750,'CNY',0.05,'en'),/37\.50/);
  assert.match(formatPrice(750,'CAD',0.01,'zh-CN'),/7\.50/);
  assert.match(formatPrice(750,'JPY',1,'en'),/750$/);
});
