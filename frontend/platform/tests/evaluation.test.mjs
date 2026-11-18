import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PlatformApi } from '../src/api.ts';
import { validEvaluationLabel, validEvaluationDataset, evaluationDataset } from '../src/evaluation.ts';
import { datasetId, sampleId, identity, label, sample, dataset, entry } from './evaluation-fixtures.mjs';
const response = data => new Response(JSON.stringify({data}),{status:200});
async function client() {
  const calls = [], replies = [response({access_token:'evaluation-test-only',audience:'platform'}),response(identity)];
  const api = new PlatformApi(async (url,options) => { calls.push({url,options}); return replies.shift(); });
  await api.login({email:identity.email,password:'test-only',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  return {api,calls,replies};
}
test('blind draft and authorized locked gold have distinct response contracts', () => {
  assert.equal(validEvaluationDataset(dataset()),true);
  assert.equal(validEvaluationDataset(dataset({samples:[sample({gold:label()})]})),false);
  const locked = dataset({state:'locked',locked_at:'2026-10-26T01:00:00Z',locked_digest:'c'.repeat(64),
    samples:[sample({state:'agreed',label_count:2,gold:label()})],agreement:{double_labeled:1,agreed:1,disputed:0,adjudicated:0,rate:1}});
  assert.equal(validEvaluationDataset(locked),true);
  locked.samples[0].gold=null;
  assert.equal(validEvaluationDataset(locked),false);
});
test('inactive authorization cannot expose source text, own label, disagreements or gold', () => {
  const inactive = sample({state:'authorization_inactive',redacted_stem:null,redacted_answer:null});
  assert.equal(validEvaluationDataset(dataset({usable:false,samples:[inactive]})),true);
  for (const [key,value] of Object.entries({redacted_stem:'private',redacted_answer:'private',own_label:label(),gold:label(),disagreement_labels:[label(),label()]}))
    assert.equal(validEvaluationDataset(dataset({samples:[{...inactive,[key]:value}]})),false,key);
});
test('only disputed samples without own annotation may contain two disagreement labels', () => {
  const disputed = sample({state:'disputed',label_count:2,disagreement_labels:[label(),{...label(),correct_answer:'6'}]});
  const agreement = {double_labeled:1,agreed:0,disputed:1,adjudicated:0,rate:0};
  assert.equal(validEvaluationDataset(dataset({samples:[disputed],agreement})),true);
  for (const changes of [{state:'pending'},{own_label:label()},{disagreement_labels:[label()]}])
    assert.equal(validEvaluationDataset(dataset({samples:[{...disputed,...changes}],agreement})),false);
});
test('label rejects invalid rectangles, unsupported verdicts without abstention and inconsistent answers', () => {
  assert.equal(validEvaluationLabel(label()),true);
  for (const changes of [{box:[5,0,5,10]},{box:[0,0,10001,10000]},{box:[0,0,1.5,10]},
    {verdict:'unsupported'},{verdict:'pending_review'},{verdict:'unanswered'},{correct_answer:' '},{stem:''}])
    assert.equal(validEvaluationLabel({...label(),...changes}),false);
  assert.equal(validEvaluationLabel({...label(),verdict:'unsupported',requires_abstention:true,correct_answer:''}),true);
  assert.equal(validEvaluationLabel({...label(),verdict:'unanswered',student_answer:'',correct_answer:''}),true);
});
test('projection strips extra fields at every level and owns nested label coordinates', () => {
  const input = dataset({secret:'hidden',samples:[sample({own_label:{...label(),secret:'hidden'},secret:'hidden'})]});
  input.agreement.secret='hidden';
  const output = evaluationDataset(input);
  assert.equal(JSON.stringify(output).includes('hidden'),false);
  output.samples[0].own_label.box[0]=1;
  assert.equal(input.samples[0].own_label.box[0],0);
});
test('invalid ids, duplicate samples, malformed statistics and production eligibility fail closed', () => {
  for (const changes of [{id:'invalid'},{production_eligible:true},{samples:[sample(),sample()],sample_count:2},
    {next_offset:0},{sample_count:0},{agreement:{double_labeled:1,agreed:1,disputed:1,adjudicated:0,rate:1}}])
    assert.equal(validEvaluationDataset(dataset(changes)),false);
  assert.equal(validEvaluationDataset(dataset(),'33333333-3333-4333-8333-333333333333'),false);
});
test('API lists and reads samples at the same 50-item page length', async () => {
  const {api,replies,calls} = await client();
  replies.push(response({items:[{...entry(dataset()),secret:'hidden'}],next_offset:100}),response(dataset({offset:50})));
  assert.equal((await api.evaluationDatasets(50)).items[0].secret,undefined);
  await api.evaluationDataset(datasetId,50);
  assert.equal(calls.at(-2).url,'/api/v1/admin/evaluation-datasets?limit=50&offset=50');
  assert.equal(calls.at(-1).url,`/api/v1/admin/evaluation-datasets/${datasetId}?limit=50&offset=50`);
});
test('API sends exact mutation payloads to create, intake, annotation, adjudication and lock', async () => {
  const {api,replies,calls} = await client();
  const command = {operation_id:crypto.randomUUID(),expected_revision:2};
  const mutations = [
    ['createEvaluationDataset',[{...command,expected_revision:0,name:'基准',split:'test',handbook_version:'v1'}],''],
    ['intakeEvaluationSample',[datasetId,{...command,question_id:sampleId,source_revision:1,authorization_id:sampleId,
      near_duplicate_group:'math/one',redacted_stem:'题目',redacted_answer:'5',redaction_note:'已人工脱敏'}],`/${datasetId}/samples`],
    ['annotateEvaluationSample',[datasetId,sampleId,{...command,label:label()}],`/${datasetId}/samples/${sampleId}/annotations`],
    ['adjudicateEvaluationSample',[datasetId,sampleId,{...command,label:label(),reason:'独立核对计算'}],`/${datasetId}/samples/${sampleId}/adjudications`],
    ['lockEvaluationDataset',[datasetId,command],`/${datasetId}/lock`],
  ];
  for (const [method,args,path] of mutations) {
    replies.push(response(dataset())); await api[method](...args);
    const call=calls.at(-1);
    assert.equal(call.url,`/api/v1/admin/evaluation-datasets${path}`);
    assert.equal(call.options.method,'POST');
    assert.deepEqual(JSON.parse(call.options.body),args.at(-1));
    assert.equal(call.options.credentials,'omit'); assert.equal(call.options.cache,'no-store');
  }
});
test('API rejects malformed dataset, wrong dataset id and leaked inactive content', async () => {
  const {api,replies} = await client();
  for (const data of [null,dataset({id:sampleId}),dataset({samples:[sample({state:'authorization_inactive'})]})]) {
    replies.push(response(data));
    await assert.rejects(api.evaluationDataset(datasetId),error => error.status===502);
  }
});
