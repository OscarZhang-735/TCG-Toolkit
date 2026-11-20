import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import test from 'node:test';
const source = await readFile(new URL('../src/tcg_toolkit/static/catalog/history.js', import.meta.url), 'utf8');
const {readHistory, appendHistory, saveHistory} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
test('keeps last ten unique searches and moves repeats to newest', () => {
  let history = [];
  for (let i=0;i<12;i++) history=appendHistory(history, ` q${i} `);
  assert.deepEqual(history, Array.from({length:10},(_,i)=>`q${i+2}`));
  history=appendHistory(history,' q4 ');
  assert.equal(history.length,10);
  assert.equal(history.at(-1),'q4');
  assert.equal(history.filter(item=>item==='q4').length,1);
  assert.equal(appendHistory(history,' '),history);
});
test('storage roundtrip and malformed or unavailable storage', () => {
  const data=new Map(); const storage={getItem:k=>data.get(k),setItem:(k,v)=>data.set(k,v)};
  saveHistory(storage,['OS01','LO']); assert.deepEqual(readHistory(storage),['OS01','LO']);
  saveHistory(storage,['OS01',' LO ','OS01']); assert.deepEqual(readHistory(storage),['LO','OS01']);
  saveHistory(storage,[]); assert.deepEqual(readHistory(storage),[]);
  assert.deepEqual(readHistory({getItem:()=>'{bad'}),[]);
  assert.deepEqual(readHistory({getItem:()=>'{"x":1}'}),[]);
  assert.deepEqual(readHistory({getItem:()=>JSON.stringify([null,5,'','YZ'])}),['YZ']);
  assert.doesNotThrow(()=>saveHistory({setItem:()=>{throw Error();}},['LO']));
});
