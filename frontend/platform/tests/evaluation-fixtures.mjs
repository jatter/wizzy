export const datasetId = '11111111-1111-4111-8111-111111111111';
export const sampleId = '22222222-2222-4222-8222-222222222222';
export const identity = {id:'evaluation-admin',email:'evaluation@example.test',role:'security_admin',audience:'platform',
  permissions:['platform.evaluation.read','platform.evaluation.curate','platform.evaluation.annotate','platform.evaluation.adjudicate'],
  session_id:'evaluation-session',expires_at:'2099-01-01T00:00:00Z'};
export const label = () => ({stem:'计算 2 + 3',student_answer:'5',correct_answer:'5',verdict:'correct',
  question_type:'short_answer',difficulty:'easy',requires_abstention:false,redaction_safe:true,box:[0,0,10000,10000]});
export const sample = (overrides = {}) => ({id:sampleId,grade:3,state:'pending',label_count:0,
  student_key:'a'.repeat(32),page_key:'b'.repeat(32),redacted_stem:'计算 2 + 3',redacted_answer:'5',
  own_label:null,gold:null,disagreement_labels:null,...overrides});
export const dataset = (overrides = {}) => ({id:datasetId,name:'三年级数学基准',split:'test',revision:2,state:'draft',
  handbook_version:'math-v1',created_at:'2026-10-26T00:00:00Z',production_eligible:false,
  locked_at:null,locked_digest:null,usable:true,sample_count:1,samples:[sample()],offset:0,next_offset:null,
  agreement:{double_labeled:0,agreed:0,disputed:0,adjudicated:0,rate:null},...overrides});
export const entry = item => {
  const {id,name,split,revision,state,handbook_version,created_at,production_eligible} = item;
  return {id,name,split,revision,state,handbook_version,created_at,production_eligible};
};
