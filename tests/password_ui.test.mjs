import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
const base = new URL('../src/tcg_toolkit/static/', import.meta.url);
const asModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const code = asModule(await readFile(new URL('email-code.js',base),'utf8'));
const i18n = asModule(`export const t = key => key; export const getLanguage = () => 'en';`);
const source = (await readFile(new URL('password-ui.js',base),'utf8'))
  .replace("'./email-code.js'",JSON.stringify(code)).replace("'./i18n.js'",JSON.stringify(i18n));
const {createPasswordUI} = await import(asModule(source));
const reply = (status, body) => ({status, ok:status < 300, headers:{get:()=>null},
  json:async()=>{assert.notEqual(status,204); return body;}});

function setup(user = {email:'alice@example.com',email_verified:true}) {
  const fields = new Map(), calls = [];
  let changed = 0;
  const $ = id => {
    if (!fields.has(id)) fields.set(id, {value:'', hidden:false,
      disabled:false, required:false, textContent:'', listeners:{}, attributes:{}, classes:new Set(),
      classList:{toggle(name,on){on?fields.get(id).classes.add(name):fields.get(id).classes.delete(name);}},
      setAttribute(name,value){this.attributes[name]=value;}, getAttribute(name){return this.attributes[name];},
      addEventListener(name,handler){this.listeners[name]=handler;}, reportValidity(){return true;},
      reset(){
        const prefix = id.includes('change') ? 'change' : 'reset';
        for (const [key,field] of fields) if (key.startsWith(`password-${prefix}-`)) field.value='';
        if (prefix==='change') { $('password-email-option').checked=false; $('password-current').value=''; }
      }});
    return fields.get(id);
  };
  let handler = async path => path.endsWith('email-code')
    ? reply(200,{challenge_id:'challenge',expires_in:600,retry_after:60}) : reply(204);
  const request = auth => async (path, options) => {
    calls.push({path,body:JSON.parse(options.body),auth});
    return handler(path,options);
  };
  const ui = createPasswordUI({root:{getElementById:$}, request:request(false),
    authenticatedRequest:request(true),getUser:()=>user,onChanged:()=>{changed++;}});
  const emit = async(id,event='click')=>$(id).listeners[event]({preventDefault(){}});
  const passwords = prefix => { $(`password-${prefix}-new`).value='newpassword123'; $(`password-${prefix}-confirm`).value='newpassword123'; };
  ui.refresh();
  return {$,ui,calls,emit,passwords,setHandler:value=>{handler=value;},get changed(){return changed;}};
}

test('old password change sends only password proof and clears sensitive fields on 204',async()=>{
  const s=setup(); s.passwords('change'); s.$('password-current').value='oldpassword123';
  await s.emit('password-change-form','submit');
  assert.deepEqual(s.calls[0],{path:'/api/auth/password/change',auth:true,
    body:{method:'password',current_password:'oldpassword123',new_password:'newpassword123',confirm_password:'newpassword123'}});
  assert.equal(s.changed,1); assert.equal(s.$('password-current').value,'');
  assert.equal(s.$('password-change-new').value,'');
});

test('email change uses the verified account email and does not submit old password',async()=>{
  const s=setup(); s.$('password-email-option').checked=true;
  await s.emit('password-change-method','change');
  assert.equal(s.$('password-current').disabled,true);
  assert.equal(s.$('password-change-code').required,true);
  await s.emit('password-change-send');
  assert.equal(s.calls[0].body.email,'alice@example.com');
  assert.equal(s.$('password-change-send').disabled,true);
  s.passwords('change'); s.$('password-change-code').value='123456';
  await s.emit('password-change-form','submit');
  assert.equal(s.calls[1].body.method,'email');
  assert.equal(s.calls[1].body.challenge_id,'challenge');
  assert.ok(!Object.hasOwn(s.calls[1].body,'current_password'));
  assert.equal(s.changed,1);
});

test('user-centre form starts collapsed, toggles open, and collapses again on reset',async()=>{
  const s=setup();
  assert.equal(s.$('password-change-form').hidden,true);
  assert.equal(s.$('password-settings-toggle').attributes['aria-expanded'],'false');
  await s.emit('password-settings-toggle');
  assert.equal(s.$('password-change-form').hidden,false);
  assert.equal(s.$('password-settings').classes.has('collapsed'),false);
  assert.equal(s.$('password-settings-toggle').attributes['aria-label'],'panel_collapse');
  s.ui.reset(); s.ui.refresh();
  assert.equal(s.$('password-change-form').hidden,true);
  assert.equal(s.$('password-settings').classes.has('collapsed'),true);
});

test('unbound accounts retain old-password option and see binding guidance',()=>{
  const s=setup({email:null,email_verified:false});
  assert.equal(s.$('password-email-option').disabled,true);
  assert.equal(s.$('password-email-required').hidden,false);
  assert.equal(s.$('password-current').required,true);
});

test('forgot-password path is anonymous and preserves generic send status',async()=>{
  const s=setup(null); s.$('password-reset-email').value='alice@example.com';
  await s.emit('password-reset-send');
  assert.equal(s.$('password-reset-status').textContent,'password_reset_sent');
  s.passwords('reset'); s.$('password-reset-code').value='123456';
  await s.emit('password-reset-form','submit');
  assert.equal(s.calls[1].path,'/api/auth/password/reset'); assert.equal(s.calls[1].auth,false);
  assert.equal(s.calls[1].body.email,'alice@example.com'); assert.equal(s.changed,1);
});

test('mismatch prevents submission; invalid proof shows error without signing out',async()=>{
  const s=setup(); s.passwords('change'); s.$('password-change-confirm').value='different';
  await s.emit('password-change-form','submit');
  assert.equal(s.calls.length,0); assert.equal(s.$('password-change-error').textContent,'auth_password_mismatch');
  s.passwords('change'); s.setHandler(async()=>reply(400,{detail:'invalid_password'}));
  await s.emit('password-change-form','submit');
  assert.equal(s.$('password-change-error').textContent,'email_invalid_password');
  assert.equal(s.changed,0); assert.equal(s.$('password-change-submit').disabled,false);
});

test('editing reset email discards proof; back/sign-out prevents stale success from changing account',async()=>{
  const s=setup(null); s.$('password-reset-email').value='alice@example.com';
  await s.emit('password-reset-send');
  s.$('password-reset-email').value='bob@example.com'; await s.emit('password-reset-email','input');
  s.passwords('reset'); await s.emit('password-reset-form','submit');
  assert.equal(s.calls.length,1); assert.equal(s.$('password-reset-error').textContent,'email_request_code');
  await s.emit('password-reset-send'); s.$('password-reset-code').value='123456';
  let finish; s.setHandler(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=s.emit('password-reset-form','submit');
  s.ui.reset(); finish(reply(204)); await pending;
  assert.equal(s.changed,0); assert.equal(s.$('password-reset-new').value,'');
});
