import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PlatformApi, ApiError } from '../src/api.ts';

const me = { id:'account', email:'security@example.test', role:'security_admin', audience:'platform', permissions:['platform.accounts.read'], session_id:'session', expires_at:'2099-01-01T00:00:00Z' };
const response = (data, status=200) => new Response(JSON.stringify(status === 200 ? {data} : {error:{message:'never expose upstream secrets'},request_id:'req_'+'a'.repeat(32)}), {status});
function fixture() {
  const calls = [];
  const replies = [response({access_token:'platform-only-token',audience:'platform'}), response(me)];
  const api = new PlatformApi(async (url, options) => {calls.push({url,options}); return replies.shift();});
  return {api,calls,replies};
}

test('login verifies platform audience, reads me, never uses cookies, and keeps token out of URLs', async () => {
  const {api,calls,replies} = fixture();
  assert.deepEqual(await api.login({email:me.email,password:'test-password',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'}), me);
  replies.push(response({items:[],next_cursor:null}));
  await api.accounts('cursor with+/=');
  assert.equal(calls[0].url, '/api/v1/admin/auth/login');
  assert.equal(calls[0].options.headers.Authorization, undefined);
  assert.deepEqual(JSON.parse(calls[0].options.body), {email:me.email,password:'test-password',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  assert.equal(calls[1].options.headers.Authorization, 'Bearer platform-only-token');
  assert.equal(calls[2].url, '/api/v1/admin/accounts?limit=50&cursor=cursor+with%2B%2F%3D');
  for (const {url,options} of calls) {
    assert.equal(options.credentials,'omit'); assert.equal(options.cache,'no-store');
    assert.equal(options.redirect,'error'); assert.ok(options.signal);
    assert.ok(!url.includes('platform-only-token'));
  }
});

test('rejects family audience and never sends its token to me', async () => {
  const {api,calls,replies} = fixture();
  replies.splice(0,2,response({access_token:'family-token',audience:'guardian'}));
  await assert.rejects(api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'}), ApiError);
  assert.equal(calls.length,1);
  await assert.rejects(api.roles(), error => error.status===401);
  assert.equal(calls.length,1);
});

test('failed me drops the newly issued token', async () => {
  const {api,calls,replies} = fixture(); replies[1]=response(null,503);
  await assert.rejects(api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'}), ApiError);
  await assert.rejects(api.roles(), error => error.status===401);
  assert.equal(calls.length,2);
});

test('401 clears session; 403 preserves it and returns only safe error and request id', async () => {
  const {api,calls,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  replies.push(response(null,403), response({items:[]}), response(null,401));
  await assert.rejects(api.accounts(), error => error.status===403 && !error.message.includes('secrets') && error.requestId==='req_'+'a'.repeat(32));
  await api.roles();
  await assert.rejects(api.audit(), error => error.status===401);
  await assert.rejects(api.roles(), error => error.status===401);
  assert.equal(calls.length,5);
});

test('network errors and non-JSON proxy errors do not echo raw response', async () => {
  const api = new PlatformApi(async () => {throw new Error('password=secret');});
  await assert.rejects(api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'}), error => error.status===0 && !error.message.includes('secret'));
  const bad = new PlatformApi(async () => new Response('<html>upstream secret</html>',{status:502}));
  await assert.rejects(bad.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'}), error => error.status===502 && !error.message.includes('secret'));
});

test('local clear invalidates in-flight login and does not resurrect session', async () => {
  let resolve;
  const api = new PlatformApi(() => new Promise(r => {resolve=r;}));
  const pending = api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  api.clear(); resolve(response({access_token:'late-token',audience:'platform'}));
  await assert.rejects(pending,error => error.code==='stale');
  await assert.rejects(api.roles(),error => error.status===401);
});

test('old in-flight 401 cannot clear a newer login', async () => {
  const {api,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  let resolve;
  replies.push(new Promise(r => {resolve=r;}));
  const pending = api.accounts();
  replies.push(response({access_token:'new-token',audience:'platform'}),response(me));
  await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  resolve(response(null,401));
  await assert.rejects(pending,error => error.code==='stale');
  replies.push(response({items:[]})); await api.roles();
});

test('successful response arriving after clear is discarded', async () => {
  const {api,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  let resolve; replies.push(new Promise(r => {resolve=r;}));
  const pending = api.accounts(); api.clear(); resolve(response({items:[{email:'old@example.test'}]}));
  await assert.rejects(pending,error => error.code==='stale');
});

test('both logout variants clear local session even on transport failure', async () => {
  for (const all of [false,true]) {
    const {api,calls,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
    replies.push(Promise.reject(new Error('offline')));
    await assert.rejects(api.logout(all),ApiError);
    assert.equal(calls.at(-1).url, '/api/v1/admin/auth/'+(all?'logout-all':'logout'));
    await assert.rejects(api.roles(), error => error.status===401);
  }
});

test('malformed list payloads fail safely instead of reaching rendering', async () => {
  for (const [method,payload] of [
    ['roles',null], ['roles',{items:[{name:'operator',permissions:'not-an-array'}]}],
    ['accounts',{items:[],next_cursor:42}], ['accounts',{items:[{email:'secret'}],next_cursor:null}],
    ['audit',{items:[{id:'event',actor_id:42,action:'login',request_id:'req',created_at:'today'}],next_cursor:null}],
  ]) {
    const {api,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
    replies.push(response(payload));
    await assert.rejects(api[method](), error => error instanceof ApiError && error.status===502);
    replies.push(response({items:[]})); await api.roles();
  }
});

test('logout completion from old session cannot clear newer login', async () => {
  const {api,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  let resolve; replies.push(new Promise(r => {resolve=r;}));
  const pending = api.logout(true);
  replies.push(response({access_token:'new-token',audience:'platform'}),response(me));
  await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  resolve(response({revoked:true}));
  await assert.rejects(pending,error => error.code==='stale');
  replies.push(response({items:[]})); await api.roles();
});

const model = {id:'model-1',provider:'example',model_key:'math',fixed_version:'2026-10-01',region:'cn-east',
  display_name:'视觉模型',purposes:['structure'],revision:1,revision_id:'revision-1',reason:'登记',
  created_at:'2026-10-26T00:00:00Z',registered_at:'2026-10-26T00:00:00Z',credential_configured:true,
  contract_status:'approved',contract_reference:'C-1',contract_expires_on:'2099-01-01',
  input_price_micros:0,output_price_micros:null,enabled:false,catalog_status:'disabled',accuracy:null,evaluation_status:'not_evaluated',production_eligible:false};

test('model catalog query encodes filters and history cursor independently', async () => {
  const {api,calls,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  replies.push(response({items:[model],next_cursor:'next'}),response(model),response({items:[model],next_cursor:null}));
  assert.deepEqual((await api.models({q:'数学 &',enabled:false,cursor:'a+/='})).items,[model]);
  assert.deepEqual(await api.model('model/1'),model);
  assert.equal((await api.modelHistory('model/1','old+')).items[0].revision,1);
  assert.equal(calls[2].url,'/api/v1/admin/models?limit=50&cursor=a%2B%2F%3D&q=%E6%95%B0%E5%AD%A6+%26&enabled=false');
  assert.equal(calls[3].url,'/api/v1/admin/models/model%2F1');
  assert.equal(calls[4].url,'/api/v1/admin/models/model%2F1/revisions?limit=50&cursor=old%2B');
});

test('model writes keep omitted credentials distinct from explicit clearing and surface safe conflicts', async () => {
  const {api,calls,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  replies.push(response(model),response(model),response(model),response(null,409));
  await api.createModel({operation_id:'op-create',secret_ref:'secret://providers/example/primary'});
  await api.reviseModel('model-1',{operation_id:'op-edit',expected_revision:1});
  await api.reviseModel('model-1',{operation_id:'op-clear',expected_revision:1,secret_ref:null});
  assert.ok(!Object.hasOwn(JSON.parse(calls[3].options.body),'secret_ref'));
  assert.equal(JSON.parse(calls[4].options.body).secret_ref,null);
  await assert.rejects(api.reviseModel('model-1',{}), error => error.status===409 && error.message.includes('刷新') && !error.message.includes('secrets'));
});

test('rejects invalid model envelopes, prices, revisions, dates and fabricated evaluation data', async () => {
  const {api,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  for (const change of [{purposes:['unknown']},{input_price_micros:1.5},{revision:0},{enabled:'yes'},
    {created_at:'bad'},{accuracy:0.99},{production_eligible:true},{secret_ref:'secret://must-not-read'},
    {contract_expires_on:'nonsense'},{credential_configured:null},{catalog_status:undefined},{catalog_status:'production_ready'}]) {
    replies.push(response({items:[{...model,...change}],next_cursor:null}));
    await assert.rejects(api.models(), error => error.status===502);
  }
  replies.push(response({items:[],next_cursor:5}));
  await assert.rejects(api.modelHistory('model-1'), error => error.status===502);
});


test('model availability is server supplied and never grants production eligibility', async () => {
  const {api,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  for (const status of ['available','contract_unapproved','contract_expired','credential_missing']) {
    const value = {...model,enabled:true,catalog_status:status};
    replies.push(response(value));
    assert.deepEqual(await api.model('model-1'),value);
  }
});


const accountDetail = {id:'10000000-0000-4000-8000-000000000001',email:'operator@example.test',
  role:'operator',status:'active',revision:0,created_at:'2026-10-26T00:00:00Z'};
const accountChange = {id:'20000000-0000-4000-8000-000000000001',account_id:accountDetail.id,
  actor_id:'30000000-0000-4000-8000-000000000001',expected_revision:0,
  previous_role:'operator',previous_status:'active',role:'security_admin',status:'active',reason:'权限申请',
  requires_approval:true,created_at:'2026-10-26T00:00:00Z',state:'pending',
  decided_by:null,decision_reason:null,decided_at:null,resulting_revision:null,stale:false};

test('account detail and history encode paths and cursors and accept revision zero', async () => {
  const {api,calls,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  replies.push(response(accountDetail),response({items:[accountChange],next_cursor:'older'}));
  assert.deepEqual(await api.account('account/1'),accountDetail);
  assert.deepEqual(await api.accountHistory('account/1','a+/='),{items:[accountChange],next_cursor:'older'});
  assert.equal(calls[2].url,'/api/v1/admin/accounts/account%2F1');
  assert.equal(calls[3].url,'/api/v1/admin/accounts/account%2F1/changes?limit=50&cursor=a%2B%2F%3D');
});

test('account writes accept 201 and preserve operation ids and independent decision reasons', async () => {
  const {api,calls,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  const body = {operation_id:crypto.randomUUID(),expected_revision:0,role:'security_admin',status:'active',reason:'权限申请'};
  const decision = {operation_id:crypto.randomUUID(),reason:'独立核对完成'};
  const approved = {...accountChange,state:'approved',decided_by:accountDetail.id,decision_reason:decision.reason,
    decided_at:'2026-10-26T01:00:00Z',resulting_revision:1};
  replies.push(new Response(JSON.stringify({data:accountChange}),{status:201}),response(approved),
    response({...approved,state:'rejected',resulting_revision:null}));
  assert.deepEqual(await api.changeAccount('account/1',body),accountChange);
  assert.deepEqual(await api.decideAccount('change/1','approve',decision),approved);
  await api.decideAccount('change/1','reject',decision);
  assert.deepEqual(JSON.parse(calls[2].options.body),body);
  assert.deepEqual(JSON.parse(calls[3].options.body),decision);
  assert.equal(calls[2].url,'/api/v1/admin/accounts/account%2F1/changes');
  assert.equal(calls[3].url,'/api/v1/admin/account-changes/change%2F1/approve');
  assert.equal(calls[4].url,'/api/v1/admin/account-changes/change%2F1/reject');
});

test('account projections reject malformed identity, revisions, decisions and pagination', async () => {
  const {api,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  for (const patch of [{id:'invalid'},{role:'root'},{status:'deleted'},{revision:-1},{revision:0.5},{created_at:'bad'}]) {
    replies.push(response({...accountDetail,...patch}));
    await assert.rejects(api.account(accountDetail.id), error => error.status===502);
  }
  for (const patch of [{actor_id:'bad'},{expected_revision:-1},{stale:'yes'},{state:'unknown'},
    {requires_approval:false},{decided_by:accountDetail.id},{resulting_revision:1},
    {state:'approved'},{state:'applied',decided_by:accountDetail.id,decision_reason:'ok',decided_at:'bad',resulting_revision:1}]) {
    replies.push(response({items:[{...accountChange,...patch}],next_cursor:null}));
    await assert.rejects(api.accountHistory(accountDetail.id), error => error.status===502);
  }
  replies.push(response({items:[],next_cursor:42}));
  await assert.rejects(api.accountHistory(accountDetail.id), error => error.status===502);
});

test('account conflicts expose only approved messages and never upstream details', async () => {
  const {api,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  for (const [code,message] of Object.entries({operation_conflict:'操作编号',revision_conflict:'账号已被修改',
    already_decided:'已有审批结果',separate_approver_required:'不能审批自己的申请',
    proposer_ineligible:'申请人已失去权限',last_security_admin:'至少一个启用的安全管理员',
    unknown_code:'记录版本或操作键冲突'})) {
    replies.push(new Response(JSON.stringify({error:{code,message:'secret upstream detail'},request_id:'req_'+'a'.repeat(32)}),{status:409}));
    await assert.rejects(api.changeAccount(accountDetail.id,{}), error => error.status===409 &&
      error.message.includes(message) && !error.message.includes('secret') &&
      error.code===(code==='unknown_code' ? '' : code));
  }
});


const school = {id:'40000000-0000-4000-8000-000000000001',code:'SH_PUDONG_001',name:'浦东实验学校',
  region:'上海市 / 浦东新区',academic_year:'2026-2027',status:'active',revision:1,
  revision_id:'50000000-0000-4000-8000-000000000001',actor_id:'60000000-0000-4000-8000-000000000001',
  reason:'学校登记',created_at:'2026-10-26T00:00:00Z',registered_at:'2026-10-26T00:00:00Z'};

test('school queries encode filters, ids and cursors and project documented fields only', async () => {
  const {api,calls,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  const withExtra = {...school,internal_only:'discard'};
  replies.push(response({items:[withExtra],next_cursor:'older'}),response(withExtra),
    response({items:[withExtra],next_cursor:null}));
  assert.deepEqual(await api.schools({q:'上海 + A',status:'active',cursor:'a+/='}),{items:[school],next_cursor:'older'});
  assert.deepEqual(await api.school('school/1'),school);
  assert.deepEqual(await api.schoolHistory('school/1','a+/='),{items:[school],next_cursor:null});
  const query = new URL(calls[2].url,'http://localhost').searchParams;
  assert.equal(query.get('limit'),'50'); assert.equal(query.get('q'),'上海 + A');
  assert.equal(query.get('status'),'active'); assert.equal(query.get('cursor'),'a+/=');
  assert.equal(calls[3].url,'/api/v1/admin/schools/school%2F1');
  assert.equal(calls[4].url,'/api/v1/admin/schools/school%2F1/revisions?limit=50&cursor=a%2B%2F%3D');
});

test('school registration and revision preserve idempotency and expected revision', async () => {
  const {api,calls,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  const body = {operation_id:crypto.randomUUID(),code:school.code,name:school.name,region:school.region,
    academic_year:school.academic_year,status:school.status,reason:school.reason};
  const {code,...fields} = body;
  const revision = {...fields,operation_id:crypto.randomUUID(),expected_revision:1,status:'disabled',reason:'学校停用'};
  replies.push(new Response(JSON.stringify({data:school}),{status:201}),response({...school,revision:2,status:'disabled'}));
  assert.deepEqual(await api.createSchool(body),school);
  assert.equal((await api.reviseSchool('school/1',revision)).revision,2);
  assert.equal(calls[2].url,'/api/v1/admin/schools');
  assert.equal(calls[3].url,'/api/v1/admin/schools/school%2F1/revisions');
  assert.deepEqual(JSON.parse(calls[2].options.body),body);
  assert.deepEqual(JSON.parse(calls[3].options.body),revision);
});

test('school responses reject invalid metadata, identities and pagination', async () => {
  const {api,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  for (const patch of [{id:'bad'},{revision_id:'bad'},{actor_id:'bad'},{code:'lowercase'},
    {name:''},{name:'a'.repeat(121)},{region:'a'.repeat(121)},{reason:'a'.repeat(501)},
    {region:'bad\u0000region'},{academic_year:'2026-2028'},{academic_year:'1999-2000'},
    {status:'deleted'},{revision:0},{revision:1.5},{revision:1000000000},
    {created_at:'bad'},{registered_at:'bad'}]) {
    replies.push(response({...school,...patch}));
    await assert.rejects(api.school(school.id),error => error.status===502);
  }
  for (const page of [{items:[null],next_cursor:null},{items:[],next_cursor:12},null]) {
    replies.push(response(page));
    await assert.rejects(api.schools(),error => error.status===502);
  }
});

test('school conflict messages use approved codes without exposing upstream messages', async () => {
  const {api,replies} = fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  for (const [code,message] of Object.entries({school_code_conflict:'学校代码已存在',
    school_revision_conflict:'学校已被修改',operation_conflict:'操作编号',other:'记录版本或操作键冲突'})) {
    replies.push(new Response(JSON.stringify({error:{code,message:'private internal detail'}}),{status:409}));
    await assert.rejects(api.createSchool({}),error => error.status===409 && error.message.includes(message) &&
      !error.message.includes('private') && error.code===(code==='other' ? '' : code));
  }
});

const {region:structureRegion,...structureBase}=school;
const structure={...structureBase,id:'70000000-0000-4000-8000-000000000001',school_id:school.id,kind:'grade',parent_id:null,code:'GRADE_01',name:'一年级'};
const tree=items => ({items,academic_year:'2026-2027',school_status:'active',school_revision:1});
test('structure tree, detail and history bind school/node/year and remove extra fields',async () => {
  const {api,calls,replies}=fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  replies.push(response({...tree([{...structure,private_field:'discard'}]),private_field:'discard'}),response(structure),response({items:[structure],next_cursor:'older'}));
  assert.deepEqual(await api.schoolStructure(school.id,'2026-2027'),tree([structure]));
  assert.deepEqual(await api.structureNode(school.id,structure.id),structure);
  assert.deepEqual(await api.structureHistory(school.id,structure.id,'a+/='),{items:[structure],next_cursor:'older'});
  assert.equal(calls[2].url,`/api/v1/admin/schools/${school.id}/structure?academic_year=2026-2027`);
  assert.ok(calls[4].url.endsWith('/revisions?limit=50&cursor=a%2B%2F%3D'));
  for (const invalid of [{...structure,school_id:structure.id},{...structure,id:school.id},{...structure,kind:'school'},{...structure,parent_id:'bad'}]) {
    replies.push(response(invalid)); await assert.rejects(api.structureNode(school.id,structure.id),error=>error.status===502);
  }
  replies.push(response({items:[{...structure,id:school.id}],next_cursor:null}));
  await assert.rejects(api.structureHistory(school.id,structure.id),error=>error.status===502);
});
test('structure tree rejects incomplete, cyclic, too deep and mixed-scope graphs',async () => {
  const {api,replies}=fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  const deep=Array.from({length:9},(_,i)=>({...structure,id:`70000000-0000-4000-8000-${String(i+1).padStart(12,'0')}`,kind:'department',code:`DEPT_${i}`,parent_id:i ? `70000000-0000-4000-8000-${String(i).padStart(12,'0')}` : null}));
  const invalidTrees=[tree([structure,structure]),tree([{...structure,parent_id:structure.id}]),tree([{...structure,parent_id:school.id}]),tree([{...structure,kind:'class'}]),tree([{...structure,academic_year:'2025-2026'}]),tree([{...structure,school_id:structure.id}]),tree(deep),{...tree([]),academic_year:'2025-2026'},{...tree([]),school_revision:0},{...tree([]),school_status:'deleted'}];
  for (const invalid of invalidTrees) { replies.push(response(invalid)); await assert.rejects(api.schoolStructure(school.id,'2026-2027'),error=>error.status===502); }
  replies.push(response(tree([structure,{...structure,id:school.id,code:'CLASS_01',kind:'class',parent_id:structure.id}])));
  assert.equal((await api.schoolStructure(school.id,'2026-2027')).items.length,2);
});
test('structure writes retain UUID, parent and CAS and encode path segments',async () => {
  const {api,calls,replies}=fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  const body={operation_id:crypto.randomUUID(),code:structure.code,kind:structure.kind,academic_year:structure.academic_year,name:structure.name,parent_id:null,status:'active',reason:'组织登记'};
  const change={operation_id:crypto.randomUUID(),expected_revision:1,name:'二年级',parent_id:null,status:'disabled',reason:'组织调整'};
  replies.push(response(structure),response({...structure,revision:2}));
  await api.createStructure(school.id,body); await api.reviseStructure(school.id,structure.id,change);
  assert.deepEqual(JSON.parse(calls[2].options.body),body); assert.deepEqual(JSON.parse(calls[3].options.body),change);
  replies.push(response(structure)); await assert.rejects(api.structureNode('a/b','c/d'),error=>error.status===502);
  assert.equal(calls[4].url,'/api/v1/admin/schools/a%2Fb/structure/c%2Fd');
});

const {name:rosterName,region:rosterRegion,...rosterBase}=school;
const enrollment={...rosterBase,id:'80000000-0000-4000-8000-000000000001',school_id:school.id,student_id:'90000000-0000-4000-8000-000000000001',code:'STU_01',display_name:'测试学生',class_id:structure.id,status:'enrolled'};
test('roster filters and cursors are encoded, scoped by school/year and discard unknown fields',async()=>{
  const {api,calls,replies}=fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  replies.push(response({items:[{...enrollment,private_field:'discard'}],next_cursor:'older'}),response(enrollment),response({items:[enrollment],next_cursor:null}));
  assert.deepEqual(await api.schoolRoster(school.id,{academic_year:'2026-2027',q:'%_张 &+',status:'enrolled',cursor:'a+/='}),{items:[enrollment],next_cursor:'older'});
  const params=new URL(calls[2].url,'http://localhost').searchParams;
  assert.equal(params.get('q'),'%_张 &+'); assert.equal(params.get('cursor'),'a+/='); assert.equal(params.get('status'),'enrolled'); assert.equal(params.get('academic_year'),'2026-2027');
  assert.deepEqual(await api.rosterEnrollment(school.id,enrollment.id),enrollment);
  assert.deepEqual(await api.rosterHistory(school.id,enrollment.id,'a+/='),{items:[enrollment],next_cursor:null});
  assert.ok(calls[4].url.endsWith('/revisions?limit=50&cursor=a%2B%2F%3D'));
  for(const invalid of [{...enrollment,school_id:structure.id},{...enrollment,academic_year:'2025-2026'}]){
    replies.push(response({items:[invalid],next_cursor:null})); await assert.rejects(api.schoolRoster(school.id,{academic_year:'2026-2027'}),error=>error.status===502);
  }
});
test('roster detail and history reject invalid or mixed-scope responses',async()=>{
  const {api,replies}=fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  for(const patch of [{id:school.id},{student_id:'bad'},{school_id:structure.id},{status:'deleted'},{class_id:'bad'},{display_name:''},{display_name:'a'.repeat(81)},{revision:0},{academic_year:'2026-2028'}]){
    replies.push(response({...enrollment,...patch})); await assert.rejects(api.rosterEnrollment(school.id,enrollment.id),error=>error.status===502);
  }
  for(const page of [{items:[{...enrollment,id:school.id}],next_cursor:null},{items:[null],next_cursor:null},{items:[],next_cursor:123},null]){
    replies.push(response(page)); await assert.rejects(api.rosterHistory(school.id,enrollment.id),error=>error.status===502);
  }
});
test('roster writes retain exact operation UUID, identity/year and CAS',async()=>{
  const {api,calls,replies}=fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  const body={operation_id:crypto.randomUUID(),code:'STU_01',academic_year:'2026-2027',display_name:'测试学生',class_id:structure.id,status:'enrolled',reason:'登记'};
  const change={operation_id:crypto.randomUUID(),expected_revision:1,display_name:'测试学生',class_id:null,status:'paused',reason:'休学'};
  replies.push(response(enrollment),response({...enrollment,...change,revision:2}));
  await api.createEnrollment(school.id,body); await api.reviseEnrollment(school.id,enrollment.id,change);
  assert.deepEqual(JSON.parse(calls[2].options.body),body); assert.deepEqual(JSON.parse(calls[3].options.body),change);
  replies.push(response(enrollment)); await assert.rejects(api.rosterEnrollment('a/b','c/d'),error=>error.status===502);
  assert.equal(calls[4].url,'/api/v1/admin/schools/a%2Fb/roster/c%2Fd');
});

const textbook = {id:'70000000-0000-4000-8000-000000000001',code:'MATH_G3_2026_A',title:'数学',stage:'primary',grade:3,
  subject:'数学',publisher:'示例出版社',edition_label:'2026修订版',academic_year:'2026-2027',volume:'first',
  rights:{basis:'publisher_contract',holder:'示例出版社',reference:'CONTRACT-001',scopes:['storage','ocr'],regions:['CN'],valid_from:'2020-01-01',valid_until:'2099-12-31'},
  revision:1,revision_id:'70000000-0000-4000-8000-000000000002',state:'draft',action:'create',
  creator_id:'70000000-0000-4000-8000-000000000003',editor_id:'70000000-0000-4000-8000-000000000003',submitted_by:null,
  actor_id:'70000000-0000-4000-8000-000000000003',reason:'登记教材权利',created_at:'2026-10-26T00:00:00Z',registered_at:'2026-10-26T00:00:00Z',
  availability:'metadata_only',content_available:false};

test('textbook queries encode filters and project only documented metadata and rights',async () => {
  const {api,calls,replies}=fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  const extra={...textbook,internal_note:'private',rights:{...textbook.rights,internal_note:'private'}};
  replies.push(response({items:[extra],next_cursor:'older'}),response(extra),response({items:[extra],next_cursor:null}));
  assert.deepEqual(await api.textbooks({q:'数学 + A',state:'draft',cursor:'a+/='}),{items:[textbook],next_cursor:'older'});
  assert.deepEqual(await api.textbook(textbook.id),textbook);
  assert.deepEqual(await api.textbookHistory(textbook.id,'a+/='),{items:[textbook],next_cursor:null});
  const query=new URL(calls[2].url,'http://localhost').searchParams;
  assert.equal(query.get('limit'),'50'); assert.equal(query.get('q'),'数学 + A');
  assert.equal(query.get('state'),'draft'); assert.equal(query.get('cursor'),'a+/=');
  assert.equal(calls.at(-1).url,`/api/v1/admin/textbook-editions/${textbook.id}/revisions?limit=50&cursor=a%2B%2F%3D`);
});

test('textbook writes preserve operation id and CAS on each transition',async () => {
  const {api,calls,replies}=fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  const decision={operation_id:crypto.randomUUID(),expected_revision:1,reason:'核验授权'};
  for (const action of ['submit','approve','reject','withdraw']) {
    replies.push(response(textbook)); await api.decideTextbook(textbook.id,action,decision);
    assert.equal(calls.at(-1).url,`/api/v1/admin/textbook-editions/${textbook.id}/${action}`);
    assert.deepEqual(JSON.parse(calls.at(-1).options.body),decision);
  }
  replies.push(response(textbook),response(textbook));
  await api.createTextbook({...decision,...textbook}); await api.reviseTextbook(textbook.id,{...decision,...textbook});
  assert.equal(calls.at(-1).url,`/api/v1/admin/textbook-editions/${textbook.id}/revisions`);
  assert.equal(JSON.parse(calls.at(-1).options.body).operation_id,decision.operation_id);
});

test('textbook malformed rights, states, dates and foreign history fail safely',async () => {
  const {api,replies}=fixture(); await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  for (const change of [{rights:undefined},{grade:true},{grade:7},{academic_year:'2026-2028'},{content_available:true},
    {state:'approved',submitted_by:null},{title:' bad '},{reason:'bad\u0000'},
    ...[{scopes:['ocr']},{scopes:['storage','storage']},{regions:['CN','CN']},{regions:Array.from({length:21},(_,i)=>`CN-${i}`)},
      {valid_from:'2026-02-30'},{valid_from:'2099-01-01',valid_until:'2020-01-01'}].map(rights=>({rights:{...textbook.rights,...rights}}))]) {
    replies.push(response({...textbook,...change})); await assert.rejects(api.textbook(textbook.id),error=>error.status===502);
  }
  replies.push(response({...textbook,id:crypto.randomUUID()}));
  await assert.rejects(api.textbook(textbook.id),error=>error.status===502);
  replies.push(response({items:[{...textbook,id:crypto.randomUUID()}],next_cursor:null}));
  await assert.rejects(api.textbookHistory(textbook.id),error=>error.status===502);
  replies.push(response({...textbook,rights:null})); assert.equal((await api.textbook(textbook.id)).rights,null);
});

const document = {id:'80000000-0000-4000-8000-000000000001',edition_id:textbook.id,edition_revision:1,
  filename:'math.pdf',size_bytes:9,sha256:'a'.repeat(64),status:'awaiting_upload',scan_status:'not_scanned',content_available:false,
  created_by:textbook.creator_id,operation_id:'80000000-0000-4000-8000-000000000002',reason:'授权原件接收',
  created_at:'2026-10-26T00:00:00Z',expires_at:'2026-10-26T00:15:00Z',received_at:null};
const intent = {operation_id:document.operation_id,expected_revision:1,filename:document.filename,
  size_bytes:document.size_bytes,sha256:document.sha256,reason:document.reason};

test('PDF intent binds exact metadata and binary PUT keeps bearer, bytes and quarantine state',async()=>{
  const {api,calls,replies}=fixture();await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  const received={...document,status:'quarantined',received_at:'2026-10-26T00:01:00Z'};
  replies.push(response({...document,private_path:'/private'}),response(received),response({items:[received],next_cursor:null}));
  assert.deepEqual(await api.requestTextbookUpload(textbook.id,intent),document);
  assert.deepEqual(JSON.parse(calls[2].options.body),intent);
  const pdf=new Blob(['%PDF-test'],{type:'application/pdf'});
  assert.deepEqual(await api.uploadTextbook(textbook.id,document.id,pdf),received);
  assert.equal(calls[3].options.method,'PUT');assert.equal(calls[3].options.body,pdf);
  assert.equal(calls[3].options.headers['Content-Type'],'application/pdf');
  assert.equal(calls[3].options.headers.Authorization,'Bearer platform-only-token');
  assert.equal(calls[3].options.credentials,'omit');assert.equal(calls[3].options.redirect,'error');
  assert.equal(calls[3].url,`/api/v1/admin/textbook-editions/${textbook.id}/documents/${document.id}/content`);
  assert.deepEqual(await api.textbookDocuments(textbook.id,'older+/='),{items:[received],next_cursor:null});
  assert.ok(calls[4].url.endsWith('limit=50&cursor=older%2B%2F%3D'));
});

test('PDF contract rejects wrong bindings, invented scan success and exposed content',async()=>{
  const {api,replies}=fixture();await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  for(const change of [{edition_id:document.id},{id:'bad'},{edition_revision:0},{filename:'../math.pdf'},
    {size_bytes:52428801},{sha256:'A'.repeat(64)},{scan_status:'clean'},{content_available:true},
    {status:'published'},{reason:''},{expires_at:document.created_at},{received_at:document.created_at}]) {
    replies.push(response({items:[{...document,...change}],next_cursor:null}));
    await assert.rejects(api.textbookDocuments(textbook.id),error=>error.status===502);
  }
  for(const change of [{operation_id:document.id},{edition_revision:2},{filename:'other.pdf'},
    {size_bytes:8},{sha256:'b'.repeat(64)},{reason:'other'}]) {
    replies.push(response({...document,...change}));
    await assert.rejects(api.requestTextbookUpload(textbook.id,intent),error=>error.status===502);
  }
  for(const value of [document,{...document,id:document.operation_id,status:'quarantined',received_at:document.created_at}]) {
    replies.push(response(value));
    await assert.rejects(api.uploadTextbook(textbook.id,document.id,new Blob(['%PDF-test'])),error=>error.status===502);
  }
});

const processingIds = {edition:'90000000-0000-4000-8000-000000000001',document:'90000000-0000-4000-8000-000000000002',job:'90000000-0000-4000-8000-000000000003',actor:'90000000-0000-4000-8000-000000000004'};
const processingJob = {id:processingIds.job,edition_id:processingIds.edition,document_id:processingIds.document,edition_revision:3,
  created_by:processingIds.actor,reason:'提取教材草稿',state:'queued',attempts:0,created_at:'2026-10-26T00:00:00Z',finished_at:null,code:null,
  scan:null,page_count:null,character_count:null,pages_without_text:null,requires_review:true,ocr_performed:false,content_available:false};
const processingDraft = {...processingJob,state:'draft_ready',attempts:1,finished_at:'2026-10-26T00:00:01Z',page_count:2,character_count:10,pages_without_text:[2],
  scan:{engine:'test-engine',database_revision:1,database_date:'test-date',checked_at:'2026-10-26T00:00:00Z'}};

test('processing API binds edition/document/job, projects safe fields, and preserves cursor and request body',async()=>{
  const {api,calls,replies}=fixture();await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  const {edition,document,job}=processingIds;
  replies.push(response({items:[{...processingDraft,private_text:'hidden',scan:{...processingDraft.scan,private_key:'hidden'}}],next_cursor:'cursor+/='}));
  const list=await api.textbookProcessingJobs(edition,document,'older+/=');
  assert.deepEqual(list,{items:[processingDraft],next_cursor:'cursor+/='});
  assert.match(calls.at(-1).url,/cursor=older%2B%2F%3D/);
  const payload={operation_id:'90000000-0000-4000-8000-000000000005',expected_revision:3,reason:processingJob.reason};
  replies.push(new Response(JSON.stringify({data:processingJob}),{status:201}));
  assert.deepEqual(await api.requestTextbookProcessing(edition,document,payload),processingJob);
  assert.deepEqual(JSON.parse(calls.at(-1).options.body),payload);
  replies.push(response(processingDraft));assert.deepEqual(await api.textbookProcessingJob(edition,document,job),processingDraft);
  const page={job_id:job,page_count:2,page:{number:1,text:'数学😀\n <script>不执行</script>'},requires_review:true,ocr_performed:false,content_available:false};
  replies.push(response({...page,private_text:'hidden',page:{...page.page,secret:'hidden'}}));
  assert.deepEqual(await api.textbookProcessingPage(edition,document,job,1),page);
  assert.match(calls.at(-1).url,/\/pages\/1$/);
});

for (const [name,change] of Object.entries({wrongEdition:{edition_id:processingIds.actor},wrongDocument:{document_id:processingIds.actor},
  wrongJob:{id:processingIds.actor},unknownState:{state:'published'},forgedContent:{content_available:true},ocr:{ocr_performed:true},
  noReview:{requires_review:false},invalidAttempts:{attempts:4},wrongRevision:{edition_revision:0},wrongScan:{scan:null},
  privateScan:{scan:{...processingDraft.scan,database_revision:0}},missingFinished:{finished_at:null},unsortedPages:{pages_without_text:[2,1]},
  outOfBounds:{pages_without_text:[3]},badCode:{code:'secret-error'},wrongStats:{character_count:1000001},falseEmpty:{state:'no_text'},
  queuedScan:{state:'queued',finished_at:null,scan:processingDraft.scan,page_count:null,character_count:null,pages_without_text:null},
  failureWithText:{state:'unavailable',code:'extraction_failed'}})) {
  test(`processing detail rejects ${name}`,async()=>{
    const {api,replies}=fixture();await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
    replies.push(response({...processingDraft,...change}));
    await assert.rejects(api.textbookProcessingJob(processingIds.edition,processingIds.document,processingIds.job),error=>error.status===502);
  });
}

for (const [name,change] of Object.entries({wrongJob:{job_id:processingIds.actor},wrongPage:{page:{number:2,text:'test'}},
  nul:{page:{number:1,text:'a\x00b'}},surrogate:{page:{number:1,text:'a\ud800b'}},large:{page:{number:1,text:'a'.repeat(100001)}},
  wrongCount:{page_count:501},available:{content_available:true},ocr:{ocr_performed:true},noReview:{requires_review:false}})) {
  test(`processing page rejects ${name}`,async()=>{
    const {api,replies}=fixture();await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
    replies.push(response({job_id:processingIds.job,page_count:2,page:{number:1,text:'test'},requires_review:true,ocr_performed:false,content_available:false,...change}));
    await assert.rejects(api.textbookProcessingPage(processingIds.edition,processingIds.document,processingIds.job,1),error=>error.status===502);
  });
}

test('processing list and request reject malformed pages and mismatched request acknowledgments',async()=>{
  const {api,replies,calls}=fixture();await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  replies.push(response({items:[processingJob],next_cursor:1}));
  await assert.rejects(api.textbookProcessingJobs(processingIds.edition,processingIds.document),error=>error.status===502);
  for (const payload of [{expected_revision:2,reason:processingJob.reason},{expected_revision:3,reason:'other'}]) {
    replies.push(response(processingJob));
    await assert.rejects(api.requestTextbookProcessing(processingIds.edition,processingIds.document,{operation_id:processingIds.actor,...payload}),error=>error.status===502);
  }
  const count=calls.length;
  for (const number of [0,501,1.5,NaN]) await assert.rejects(api.textbookProcessingPage(processingIds.edition,processingIds.document,processingIds.job,number),error=>error.status===422);
  assert.equal(calls.length,count);
});

const content = {id:'a0000000-0000-4000-8000-000000000001',job_id:processingIds.job,edition_id:processingIds.edition,
  document_id:processingIds.document,edition_revision:3,revision:1,current_revision:1,revision_id:processingIds.actor,
  state:'draft',action:'create',created_by:processingIds.actor,actor_id:processingIds.actor,submitted_by:null,
  contributors:[processingIds.actor],reason:'人工校核',created_at:'2026-10-26T02:00:00Z',source_created_at:'2026-10-26T01:00:00Z',
  page_count:2,chapter_count:0,verified_pages:0,sample_pages:[],required_sample_pages:[1,2],ready:false,ocr_performed:false,content_available:false};
const contentBody = {pages:[{number:1,text:'数学 😀',verified:true},{number:2,text:'',verified:true}],
  chapters:[{code:'CH1',title:'第一章',parent_code:null,first_page:1,last_page:2}]};

test('content create, edits and decisions bind routes, action and expected revision',async()=>{
  const {api,calls,replies}=fixture();await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  const decision={operation_id:processingIds.actor,expected_revision:3,reason:content.reason};
  replies.push(response(content));assert.deepEqual(await api.createTextbookContent(content.edition_id,content.job_id,decision),content);
  assert.ok(calls.at(-1).url.endsWith(`/processing-jobs/${content.job_id}/content`));
  assert.deepEqual(JSON.parse(calls.at(-1).options.body),decision);
  for(const [action,state] of Object.entries({revise:'draft',submit:'pending_review',approve:'published',reject:'draft',withdraw:'withdrawn'})) {
    const result={...content,revision:2,current_revision:2,action,state,submitted_by:processingIds.actor};
    const payload={...decision,expected_revision:1,...(action==='revise'?{body:contentBody}:action==='approve'?{sample_pages:[1,2]}:{})};
    replies.push(response(result));assert.deepEqual(await api.writeTextbookContent(content.edition_id,content.id,action,payload),result);
    assert.ok(calls.at(-1).url.endsWith(`/contents/${content.id}/${action==='revise'?'revisions':action}`));
    assert.deepEqual(JSON.parse(calls.at(-1).options.body),payload);
  }
  for(const change of [{job_id:content.id},{edition_revision:4},{action:'revise'}]) {
    replies.push(response({...content,...change}));
    await assert.rejects(api.createTextbookContent(content.edition_id,content.job_id,decision),e=>e.status===502);
  }
  for(const change of [{revision:1,current_revision:2},{action:'create'}]) {
    replies.push(response({...content,revision:2,current_revision:2,action:'revise',...change}));
    await assert.rejects(api.writeTextbookContent(content.edition_id,content.id,'revise',{...decision,expected_revision:1}),e=>e.status===502);
  }
});

test('content metadata rejects foreign bindings, forged availability and incomplete sample evidence',async()=>{
  const {api,replies}=fixture();await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  for(const change of [{id:processingIds.actor},{edition_id:content.id},{current_revision:0},{page_count:501},
    {required_sample_pages:[1]},{required_sample_pages:[1,1,2]},{sample_pages:[3]},{verified_pages:3},
    {contributors:[]},{contributors:[content.id]},{ready:true},{content_available:true},{ocr_performed:true},
    {state:'pending_review',action:'submit',submitted_by:null},{action:'approve'}]) {
    replies.push(response({...content,...change}));
    await assert.rejects(api.textbookContent(content.edition_id,content.id),e=>e.status===502);
  }
  replies.push(response({items:[content],next_cursor:'next'}));
  assert.deepEqual(await api.textbookContents(content.edition_id),{items:[content],next_cursor:'next'});
});

test('content body rejects invalid chapters, page numbering, Unicode and cross-version responses',async()=>{
  const {api,replies}=fixture();await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  const wrapped={content_id:content.id,revision:1,body:contentBody};
  replies.push(response(wrapped));assert.deepEqual(await api.textbookContentBody(content.edition_id,content.id,1),contentBody);
  const invalid=[{pages:[]},{pages:[{number:2,text:'text',verified:true}]},{pages:[{number:1,text:'text',verified:1}]},
    {pages:[{number:1,text:'\ud800',verified:true}]},{pages:[{number:1,text:'\x00',verified:true}]},
    ...[{code:123},{parent_code:'MISSING'},{last_page:3},{title:' bad '}].map(c=>({chapters:[{...contentBody.chapters[0],...c}]})),
    {chapters:[contentBody.chapters[0],contentBody.chapters[0]]}];
  for(const change of invalid) {
    replies.push(response({...wrapped,body:{...contentBody,...change}}));
    await assert.rejects(api.textbookContentBody(content.edition_id,content.id,1),e=>e.status===502);
  }
  for(const change of [{content_id:processingIds.actor},{revision:2}]) {
    replies.push(response({...wrapped,...change}));
    await assert.rejects(api.textbookContentBody(content.edition_id,content.id,1),e=>e.status===502);
  }
});

test('content history enforces descending revisions and cursor boundaries; TXT binds publication version',async()=>{
  const {api,replies,calls}=fixture();await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  const recent={...content,revision:2,current_revision:2,action:'revise'},old={...content,current_revision:2};
  replies.push(response({items:[recent,old],next_before:1}));
  assert.deepEqual((await api.textbookContentHistory(content.edition_id,content.id,3)).items,[recent,old]);
  assert.ok(calls.at(-1).url.endsWith('/history?limit=50&before=3'));
  for(const data of [{items:[old,recent],next_before:null},{items:[recent,recent],next_before:null},
    {items:[recent],next_before:1},{items:[recent],next_before:null},{items:[{...old,id:processingIds.actor}],next_before:null}]) {
    replies.push(response(data));await assert.rejects(api.textbookContentHistory(content.edition_id,content.id,2),e=>e.status===502);
  }
  const txt={content_id:content.id,revision:2,text:'[PDF page 1]\n数学 😀',chapters:contentBody.chapters};
  replies.push(response(txt));assert.deepEqual(await api.textbookContentTxt(content.edition_id,content.id,2),txt);
  for(const change of [{content_id:processingIds.actor},{revision:1},{text:'\udfff'},{chapters:[{...contentBody.chapters[0],code:123}]}]) {
    replies.push(response({...txt,...change}));await assert.rejects(api.textbookContentTxt(content.edition_id,content.id,2),e=>e.status===502);
  }
});

const previewPng = Uint8Array.from(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j1ZkAAAAASUVORK5CYII=', 'base64'));
const pngResponse = () => new Response(previewPng,{headers:{'Content-Type':'image/png'}});
async function previewFixture() {
  const value=fixture(); await value.api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'}); return value;
}
test('PDF page image is a private bounded PNG request, with no token in its URL', async()=>{
  const {api,calls,replies}=await previewFixture(); replies.push(pngResponse());
  const blob=await api.textbookProcessingPageImage('edition','document','job',2);
  assert.equal(blob.type,'image/png'); assert.deepEqual(new Uint8Array(await blob.arrayBuffer()),previewPng);
  const {url,options}=calls.at(-1);
  assert.equal(url,'/api/v1/admin/textbook-editions/edition/documents/document/processing-jobs/job/pages/2/image');
  assert.equal(options.headers.Accept,'image/png'); assert.equal(options.headers.Authorization,'Bearer platform-only-token');
  assert.equal(options.credentials,'omit'); assert.equal(options.cache,'no-store'); assert.equal(options.redirect,'error');
});
test('PDF image validates page and requires an active platform session before fetching',async()=>{
  const {api,calls}=fixture();
  for(const page of [0,501,1.5,NaN]) await assert.rejects(api.textbookProcessingPageImage('e','d','j',page), e=>e.status===422);
  await assert.rejects(api.textbookProcessingPageImage('e','d','j',1),e=>e.status===401); assert.equal(calls.length,0);
});
test('PDF image rejects wrong MIME, fake PNG and excessive dimensions',async()=>{
  const {api,replies}=await previewFixture();
  const huge=previewPng.slice();new DataView(huge.buffer).setUint32(16,1601);
  for(const res of [new Response('<svg/>',{headers:{'Content-Type':'image/svg+xml'}}),new Response('<svg/>',{headers:{'Content-Type':'image/png'}}),new Response(huge,{headers:{'Content-Type':'image/png'}})]) {
    replies.push(res);await assert.rejects(api.textbookProcessingPageImage('e','d','j',1), e=>e.status===502);
  }
});
test('PDF image limits streamed bytes without trusting Content-Length and cancels oversized body',async()=>{
  const {api,replies}=await previewFixture();let cancelled=false;
  replies.push(new Response(new ReadableStream({pull(c){c.enqueue(new Uint8Array(1024*1024));},cancel(){cancelled=true;}}),{headers:{'Content-Type':'image/png','Content-Length':'1'}}));
  await assert.rejects(api.textbookProcessingPageImage('e','d','j',1), e=>e.status===502);assert.equal(cancelled,true);
});
test('PDF image authorization errors whitelist messages, clear 401 session and never expose raw server text',async()=>{
  const {api,replies,calls}=await previewFixture();
  for(const code of ['draft_rights_unavailable','preview_source_mismatch','unknown_secrets']) {
    replies.push(new Response(JSON.stringify({error:{code,message:'upstream secrets'},request_id:'req_'+'a'.repeat(32)}),{status:409}));
    await assert.rejects(api.textbookProcessingPageImage('e','d','j',1),e=>e.status===409 && e.code===(code==='unknown_secrets'?'':code) && !e.message.includes('secrets'));
  }
  replies.push(response(null,403),response(null,401));
  await assert.rejects(api.textbookProcessingPageImage('e','d','j',1),e=>e.status===403);
  await assert.rejects(api.textbookProcessingPageImage('e','d','j',1),e=>e.status===401);
  const count=calls.length;await assert.rejects(api.textbookProcessingPageImage('e','d','j',1),e=>e.status===401);assert.equal(calls.length,count);
});
test('PDF image from a logged-out session cannot finish after stream consumption',async()=>{
  const {api,replies}=await previewFixture(); let send;
  replies.push(new Response(new ReadableStream({start(c){send=c;}}),{headers:{'Content-Type':'image/png'}}));
  const pending=api.textbookProcessingPageImage('e','d','j',1);
  await new Promise(resolve=>setImmediate(resolve));api.clear();send.enqueue(previewPng);send.close();
  await assert.rejects(pending,e=>e.code==='stale');
});
test('PDF image ignores a late 401 from an old session instead of clearing the new login',async()=>{
  const {api,calls,replies}=await previewFixture();let release;
  replies.push(new Promise(resolve=>{release=resolve;}));
  const old=api.textbookProcessingPageImage('e','d','j',1);
  replies.push(response({access_token:'new-platform-token',audience:'platform'}),response(me));
  await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});release(response(null,401));
  await assert.rejects(old,e=>e.code==='stale');replies.push(pngResponse());
  await api.textbookProcessingPageImage('e','d','j',1);assert.equal(calls.at(-1).options.headers.Authorization,'Bearer new-platform-token');
});

test('session revocation validates account, operation, reason and cursor and preserves exact retry body', async () => {
  const {api,calls,replies} = fixture();
  await api.login({email:me.email,password:'test',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  const id='20000000-0000-4000-8000-000000000001';
  const body={operation_id:crypto.randomUUID(),reason:'异常登录核查'};
  const receipt={...body,id:crypto.randomUUID(),account_id:id,actor_id:crypto.randomUUID(),revoked_count:2,created_at:'2026-10-26T00:00:00.12345Z'};
  replies.push(response({items:[receipt],next_cursor:'next+/='}));
  assert.deepEqual(await api.accountSessionRevocations(id,'cursor+/='),{items:[receipt],next_cursor:'next+/='});
  assert.ok(calls.at(-1).url.endsWith('session-revocations?limit=50&cursor=cursor%2B%2F%3D'));
  for (let i=0;i<2;i++) { replies.push(response(receipt)); assert.deepEqual(await api.revokeAccountSessions(id,body),receipt); }
  assert.equal(calls.at(-1).options.body,calls.at(-2).options.body);
  for (const patch of [{account_id:crypto.randomUUID()},{revoked_count:-1},{revoked_count:0.5},{created_at:'invalid'},{reason:''},{operation_id:'bad'}]) {
    replies.push(response({items:[{...receipt,...patch}],next_cursor:null}));
    await assert.rejects(api.accountSessionRevocations(id),e=>e.status===502);
  }
  for (const patch of [{operation_id:crypto.randomUUID()},{reason:'other'}]) {
    replies.push(response({...receipt,...patch}));
    await assert.rejects(api.revokeAccountSessions(id,body),e=>e.status===502);
  }
});
