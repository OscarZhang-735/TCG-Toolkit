import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import test from 'node:test';
const source = await readFile(new URL('../src/tcg_toolkit/static/logic.js', import.meta.url), 'utf8');
const {selectItems, loadResults, priceSteps, sliderIndex, sliderBound, largeImageUrl} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const base = {games: ['wsr','chaos'], stock: '', min: null, max: null, text: '', sort: 'original'};
const items = [
 {game:'wsr',name:'Card 10',card_number:'A10',price:100,stock_status:'in_stock',stock_quantity:null},
 {game:'chaos',name:'Card 2',card_number:'A2',price:50,stock_status:'out_of_stock',stock_quantity:0},
 {game:'wsr',name:'Other',card_number:'B1',price:100,stock_status:'in_stock',stock_quantity:3}];
test('combined filters, inclusive boundaries and case-insensitive text', () => {
 assert.deepEqual(selectItems(items,{...base,games:['wsr'],stock:'in_stock',min:100,max:100,text:'a10'}),[items[0]]);
 assert.equal(selectItems(items,{...base,games:[]}).length,0);
});
test('numeric sorting, stable ties and unknown quantities last', () => {
 assert.deepEqual(selectItems(items,{...base,sort:'price:asc'}),[items[1],items[0],items[2]]);
 assert.deepEqual(selectItems(items,{...base,sort:'card_number:asc'}),[items[1],items[0],items[2]]);
 for (const direction of ['asc','desc']) assert.equal(selectItems(items,{...base,sort:`stock_quantity:${direction}`}).at(-1),items[0]);
 assert.deepEqual(selectItems(items,base),items);
});
test('round robin, next links, failures and continued results', async () => {
 const calls=[],received=[],errors=[]; let waits=0;
 const failed=await loadResults({games:['wsr','lycee','chaos'],keyword:'x',signal:new AbortController().signal,
  fetchPage:async(g,k,p)=>{calls.push([g,p]);if(g==='lycee')throw Error('failed');return {game:g,next_page:p===1?2:null};},
  onPage:r=>received.push(r),onError:g=>errors.push(g),wait:async()=>{waits++;}});
 assert.equal(failed,true);assert.deepEqual(errors,['lycee']);assert.equal(received.length,4);
 assert.deepEqual(calls,[['wsr',1],['lycee',1],['chaos',1],['wsr',2],['chaos',2]]);assert.equal(waits,4);
});
test('stop ignores in-flight response and prevents further requests',async()=>{
 const controller=new AbortController();let received=0,calls=0;
 await loadResults({games:['wsr','chaos'],keyword:'x',signal:controller.signal,
 fetchPage:async()=>{calls++;controller.abort();return {next_page:2};},
 onPage:()=>received++,onError:()=>assert.fail(),wait:async()=>assert.fail()});
 assert.equal(calls,1);assert.equal(received,0);
});

test('rarity filtering combines with existing filters and handles unknown',()=>{
 const cards=items.map((item,i)=>({...item,rarity:i===0?'SP':i===1?'R':null}));
 assert.deepEqual(selectItems(cards,{...base,rarity:'SP'}),[cards[0]]);
 assert.deepEqual(selectItems(cards,{...base,rarity:'R',stock:'in_stock'}),[]);
 assert.deepEqual(selectItems(cards,{...base,rarity:'__unknown__'}),[cards[2]]);
 assert.equal(selectItems(cards,{...base,rarity:''}).length,3);
});

test('price slider maps distinct prices and treats ends as unbounded', () => {
  const steps = priceSteps([{price: 500}, {price: 100}, {price: 500}, {price: 9000}]);
  assert.deepEqual(steps, [100, 500, 9000]);
  assert.equal(sliderIndex(steps, null, 'min'), 0);
  assert.equal(sliderIndex(steps, null, 'max'), 2);
  assert.equal(sliderIndex(steps, 300, 'min'), 1);
  assert.equal(sliderIndex(steps, 300, 'max'), 0);
  assert.equal(sliderBound(steps, 0, 'min'), null);
  assert.equal(sliderBound(steps, 1, 'min'), 500);
  assert.equal(sliderBound(steps, 1, 'max'), 500);
  assert.equal(sliderBound(steps, 2, 'max'), null);
  assert.equal(sliderBound([100], 0, 'max'), null);
});

test('large image URL swaps the thumbnail size and skips the placeholder', () => {
  assert.equal(largeImageUrl('https://card.yuyu-tei.jp/wsr/100_140/OS01/10239.jpg'), 'https://card.yuyu-tei.jp/wsr/front/OS01/10239.jpg');
  assert.equal(largeImageUrl('https://card.yuyu-tei.jp/noimage_100_140.jpg'), null);
  assert.equal(largeImageUrl(null), null);
});

test('subscription filter uses the supplied predicate and combines with other filters', () => {
 const isSubscribed = item => item.card_number !== 'A2';
 assert.deepEqual(selectItems(items, {...base, subscription: 'subscribed', isSubscribed}), [items[0], items[2]]);
 assert.deepEqual(selectItems(items, {...base, subscription: 'unsubscribed', isSubscribed}), [items[1]]);
 assert.deepEqual(selectItems(items, {...base, subscription: 'subscribed', isSubscribed, text: 'other'}), [items[2]]);
 assert.deepEqual(selectItems(items, {...base, subscription: ''}), items);
});
