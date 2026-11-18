import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PlatformApi } from '../src/api.ts';
import { validEvaluationRun, evaluationRun, validPredictions, validRunReport, runReport } from '../src/evaluationRuns.ts';
import { identity } from './evaluation-fixtures.mjs';
import { run, runId, completed, predictions, report } from './evaluation-runs-fixtures.mjs';
const response = data => new Response(JSON.stringify({data}),{status:200});
async function client(){
  const calls=[], replies=[response({access_token:'run-test-token',audience:'platform'}),response(identity)];
  const api=new PlatformApi(async(url,options)=>{calls.push({url,options});return replies.shift();});
  await api.login({email:identity.email,password:'test-only',captcha_id:'11111111-1111-1111-1111-111111111111',captcha:'K7M2'});
  return {api,calls,replies};
}
test('run fixes versions, locked corpus and offline execution with distinct pending/completed states',()=>{
  assert.equal(validEvaluationRun(run()),true);
  assert.equal(validEvaluationRun(completed()),true);
  assert.equal(validEvaluationRun(completed({usable:false})),true);
  for(const change of [{revision:2},{state:'completed'},{prediction_count:1},{production_eligible:true},{execution_mode:'provider'},
    {dataset:{...run().dataset,split:'train'}},{required_strata:[]},{required_strata:[...run().required_strata,...run().required_strata]},
    {model:{...run().model,revision:0}},{prompt_hash:'bad'},{created_at:'bad'},{usable:1}])
    assert.equal(validEvaluationRun(run(change)),false,JSON.stringify(change));
  assert.equal(validEvaluationRun(run(),'other-id'),false);
  assert.equal(validEvaluationRun(completed({source_reference:null})),false);
});
test('prediction import rejects duplicate UUIDs, extra data, string booleans and released abstentions',()=>{
  assert.equal(validPredictions(predictions()),true);
  for(const value of [null,[],[...predictions(),...predictions()],predictions().map(p=>({...p,accuracy:1})),
    predictions().map(p=>({...p,automatic_release:'true'})),predictions().map(p=>({...p,question_id:'invalid'})),
    predictions().map(p=>({...p,verdict:'pending_review'})),predictions().map(p=>({...p,verdict:'unsupported'})),
    Array(50001).fill(predictions()[0]),[{...predictions()[0],question_id:'abcdefab-abcd-4abc-8abc-abcdefabcdef'},
      {...predictions()[0],question_id:'ABCDEFAB-ABCD-4ABC-8ABC-ABCDEFABCDEF'}]])assert.equal(validPredictions(value),false);
  assert.equal(validPredictions(predictions().map(p=>({...p,verdict:'pending_review',automatic_release:false}))),true);
});
test('real Python statistics fixture preserves null denominators and insufficient-cluster intervals',()=>{
  const value=report();
  assert.equal(validRunReport(value,runId,run().manifest_digest),true);
  assert.equal(value.report.metrics.false_correct_rate.estimate,null);
  assert.equal(value.report.metrics.coverage.clusters.student.status,'insufficient_clusters');
  assert.equal(value.report.metrics.coverage.clusters.student.lower,0);
});
test('report fails closed for mismatched identity, invalid metrics, clusters and algorithm',()=>{
  const mutations=[v=>v.run_id='other',v=>v.manifest_digest='a'.repeat(64),v=>v.report.production_eligible=true,
    v=>v.report.method.seed=0,v=>v.report.method.bootstrap_resamples=100,v=>v.report.evidence_issues.push('unknown'),
    v=>v.report.metrics.coverage.estimate=.3,v=>v.report.metrics.coverage.numerator=2,
    v=>v.report.metrics.false_correct_rate.estimate=0,v=>v.report.metrics.coverage.one_sided_95.lower=NaN,
    v=>v.report.metrics.coverage.clusters.page.status='computed',v=>v.report.metrics.coverage.clusters.page.upper=.9,
    v=>v.report.prediction_counts.incorrect=1,v=>v.report.automatic_questions=2,
    v=>v.report.by_grade[0].grade=7,v=>v.report.by_grade_question_type[0].required='true'];
  for(const mutate of mutations){const v=report();mutate(v);assert.equal(validRunReport(v,runId,run().manifest_digest),false);}
});
test('run and report whitelist nested fields and own all arrays/intervals',()=>{
  const input=run({private:'secret'});input.model.secret_ref='secret';input.dataset.student='secret';input.required_strata[0].secret='secret';
  const result=evaluationRun(input);assert.equal(JSON.stringify(result).includes('secret'),false);
  result.required_strata[0].grade=6;assert.equal(input.required_strata[0].grade,3);
  const source=report();source.secret='secret';source.report.metrics.coverage.secret='secret';source.report.by_grade[0].student='secret';source.report.method.secret='secret';
  const output=runReport(source);assert.equal(JSON.stringify(output).includes('secret'),false);
  output.report.metrics.coverage.clusters.page.lower=.2;output.report.evidence_issues.push('changed');
  assert.equal(source.report.metrics.coverage.clusters.page.lower,0);assert.equal(source.report.evidence_issues.includes('changed'),false);
});
test('API uses exact run endpoints, pagination, immutable commands and no cache or ambient credentials',async()=>{
  const {api,replies,calls}=await client();
  replies.push(response({items:[run()],next_offset:100}));await api.evaluationRuns(50);
  assert.equal(calls.at(-1).url,'/api/v1/admin/evaluation-runs?limit=50&offset=50');
  replies.push(response(run()));await api.evaluationRun(runId);assert.equal(calls.at(-1).url,`/api/v1/admin/evaluation-runs/${runId}`);
  const create={operation_id:crypto.randomUUID(),expected_revision:0,dataset_id:run().dataset.id,dataset_revision:5,model_id:run().model.id,model_revision:1,prompt_hash:'a'.repeat(64),schema_version:'grading-v1',rule_version:'math-v1',image_pipeline_version:'image-v1',required_strata:run().required_strata};
  const payload={operation_id:crypto.randomUUID(),expected_revision:1,manifest_digest:run().manifest_digest,source_reference:'offline-run-001',predictions:predictions()};
  for(const [fn,args,value,path] of [['createEvaluationRun',[create],run(),''],['importEvaluationPredictions',[runId,payload],completed(),`/${runId}/predictions`]]){
    replies.push(response(value));await api[fn](...args);const {url,options}=calls.at(-1);
    assert.equal(url,`/api/v1/admin/evaluation-runs${path}`);assert.equal(options.method,'POST');
    assert.deepEqual(JSON.parse(options.body),args.at(-1));assert.equal(options.credentials,'omit');assert.equal(options.cache,'no-store');
  }
  replies.push(response(report()));await api.evaluationRunReport(runId,run().manifest_digest);
  assert.equal(calls.at(-1).url,`/api/v1/admin/evaluation-runs/${runId}/report`);
});
test('API rejects malformed lists, wrong run identity and reports bound to another digest',async()=>{
  const {api,replies}=await client();
  for(const list of [null,{items:[run(),run()],next_offset:null},{items:[run()],next_offset:0},{items:[run({production_eligible:true})],next_offset:null}]){
    replies.push(response(list));await assert.rejects(api.evaluationRuns(),e=>e.status===502);
  }
  replies.push(response(run({id:'55555555-5555-4555-8555-555555555555'})));
  await assert.rejects(api.evaluationRun(runId),e=>e.status===502);
  replies.push(response(report()));await assert.rejects(api.evaluationRunReport(runId,'a'.repeat(64)),e=>e.status===502);
});
