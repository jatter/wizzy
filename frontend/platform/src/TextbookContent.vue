<script setup lang="ts">
import { computed, onUnmounted, ref, shallowRef, watch } from 'vue';
import { ElButton, ElTag, ElProgress } from 'element-plus';
import { BookOpen, FileCheck2 } from 'lucide-vue-next';
import TextbookPdfPreview from './TextbookPdfPreview.vue';
import { ApiError, type PlatformApi, type Identity, type TextbookEntry, type TextbookProcessingJob,
  type TextbookContent, type TextbookContentBody, type ContentAction, type ContentWrite, type TextbookScope } from './api';

const props = defineProps<{api: PlatformApi; identity: Identity; edition: TextbookEntry; job: TextbookProcessingJob}>();
const emit = defineEmits<{failure: [error: unknown]}>();
const opened = ref(false), busy = ref(false), denied = ref(false), uncertain = ref(false), searched = ref(false);
const item = shallowRef<TextbookContent | null>(null), body = ref<TextbookContentBody | null>(null);
const history = shallowRef<TextbookContent[]>([]), before = ref<number | null>(null), historyLoaded = ref(false);
const cursor = ref<string | null>(null), page = ref(1), viewedRevision = ref(0), reason = ref(''), issue = ref(''), notice = ref('');
const readBlocked = ref(false);
const samples = ref<number[]>([]), dirty = ref(false), txt = ref('');
let alive = true, generation = 0;
let pending: {action: ContentAction; payload: ContentWrite; contentId?: string} | null = null;
const labels = {draft:'待校核',pending_review:'待独立审核',published:'已发布',withdrawn:'已撤回'};
const actions = {create:'建立草稿',revise:'修订正文',submit:'提交审核',approve:'审核发布',reject:'驳回',withdraw:'撤回'};
const write = computed(() => !denied.value && props.identity.permissions.includes('platform.textbooks.write'));
const review = computed(() => !denied.value && props.identity.permissions.includes('platform.textbooks.review'));
const authorized = computed(() => !denied.value && !readBlocked.value && props.job.edition_revision === props.edition.revision &&
  props.edition.availability === 'rights_approved' && ['storage','ocr','preview'].every(s => props.edition.rights?.scopes.includes(s as TextbookScope)));
const independent = computed(() => !!item.value && !item.value.contributors.includes(props.identity.id));
const current = computed(() => !!item.value && viewedRevision.value === item.value.revision);
const editable = computed(() => authorized.value && write.value && current.value && !!item.value && ['draft','withdrawn'].includes(item.value.state) && !busy.value && !uncertain.value);
const selectedPage = computed(() => body.value?.pages[page.value-1]);
const verified = computed(() => body.value?.pages.filter(p => p.verified).length ?? 0);
const progress = computed(() => body.value ? Math.round(100*verified.value/body.value.pages.length) : 0);
const complete = computed(() => !!body.value && body.value.pages.every(p => p.verified) && body.value.pages.some(p => p.text.trim()) &&
  body.value.chapters.length > 0 && body.value.pages.every(p => body.value!.chapters.some(c => c.first_page <= p.number && c.last_page >= p.number)));
function clearBody() { body.value = null; txt.value = ''; dirty.value = false; samples.value = []; viewedRevision.value = 0; }
function reset() {
  generation++; readBlocked.value = false; opened.value = false; busy.value = false; denied.value = false; uncertain.value = false; searched.value = false;
  item.value = null; history.value = []; before.value = null; historyLoaded.value = false; cursor.value = null;
  reason.value = ''; issue.value = ''; notice.value = ''; pending = null; page.value = 1; clearBody();
}
watch(() => [props.job.id,props.edition.id,props.edition.revision,props.edition.availability,props.edition.rights?.scopes.join(','),
  props.edition.rights?.valid_from,props.edition.rights?.valid_until,props.identity.id,props.identity.session_id,props.identity.permissions.join(',')],reset);
onUnmounted(() => { alive = false; generation++; clearBody(); pending = null; });
const valid = (v: number) => alive && v === generation;
function failed(error: unknown) {
  issue.value = error instanceof ApiError ? error.status === 401 ? '会话已失效，请重新登录。' : error.status === 403 ? '当前账号没有权限，已清除正文。' :
    error.status === 409 ? `操作未完成：${error.message} 请刷新当前版本。` : error.status === 422 ? '请检查正文、章节与操作说明，数据未通过校验。' :
    error.status === 502 ? '服务返回的数据不符合约定，请刷新后重试。' : '请求失败，请重试。' : '连接中断，请重试。';
  if (error instanceof ApiError && ([401,403].includes(error.status) || ['content_rights_unavailable','content_source_unavailable','draft_rights_unavailable','preview_source_mismatch'].includes(error.code ?? ''))) {
    generation++; clearBody(); history.value = []; historyLoaded.value = false; pending = null; uncertain.value = false; busy.value = false;
    if ([401,403].includes(error.status)) { denied.value = true; emit('failure',error); }
    else readBlocked.value = true;
  }
}
async function find(more = false) {
  if (busy.value || denied.value || uncertain.value || dirty.value) return;
  opened.value = true; busy.value = true; issue.value = ''; notice.value = ''; clearBody();
  const version = ++generation;
  try {
    const result = await props.api.textbookContents(props.edition.id,more ? cursor.value ?? undefined : undefined);
    if (!valid(version)) return;
    item.value = result.items.find(c => c.job_id === props.job.id) ?? null; cursor.value = result.next_cursor; searched.value = true;
    history.value = []; historyLoaded.value = false;
  } catch (error) { if (valid(version)) failed(error); }
  finally { if (valid(version)) busy.value = false; }
}
async function read(rev?: number) {
  if (busy.value || denied.value || uncertain.value || dirty.value || !item.value || !authorized.value) return;
  busy.value = true; issue.value = ''; clearBody(); const version = ++generation;
  try {
    const head = await props.api.textbookContent(props.edition.id,item.value.id);
    if (!valid(version)) return;
    item.value = head;
    const n = rev ?? head.revision;
    const result = await props.api.textbookContentBody(props.edition.id,head.id,n);
    if (!valid(version)) return;
    if (result.pages.length !== head.page_count) throw new ApiError(502);
    body.value = result; viewedRevision.value = n; page.value = 1;
  } catch (error) { if (valid(version)) failed(error); }
  finally { if (valid(version)) busy.value = false; }
}
function changed() { dirty.value = true; txt.value = ''; }
function editText(event: Event) { if (editable.value && selectedPage.value) { selectedPage.value.text = (event.target as HTMLTextAreaElement).value; selectedPage.value.verified = false; changed(); } }
function addChapter() {
  if (!editable.value || !body.value || body.value.chapters.length >= 500) return;
  let n = body.value.chapters.length+1; while (body.value.chapters.some(c => c.code === `CH${n}`)) n++;
  body.value.chapters.push({code:`CH${n}`,title:'',parent_code:null,first_page:page.value,last_page:page.value}); changed();
}
function cleanReason() { return reason.value === reason.value.trim() && reason.value.length > 0 && [...reason.value].length <= 500 && !/[\x00-\x1f\x7f]/.test(reason.value); }
async function send(action?: ContentAction) {
  if (busy.value || denied.value) return;
  if (!pending) {
    if (!action || !cleanReason()) { issue.value = '请填写操作说明（1–500 字，首尾无空格）。'; return; }
    const state = item.value?.state;
    const allowed = action === 'create' ? write.value && authorized.value && searched.value && !item.value && !cursor.value :
      action === 'revise' ? editable.value && dirty.value && !!body.value :
      action === 'submit' ? write.value && authorized.value && current.value && !dirty.value && complete.value && item.value?.ready && state === 'draft' :
      action === 'withdraw' ? write.value && ['pending_review','published'].includes(state ?? '') :
      review.value && independent.value && authorized.value && state === 'pending_review' &&
        (action === 'reject' || (current.value && !!body.value && props.edition.rights?.scopes.includes('retrieval') && item.value!.required_sample_pages.every(n => samples.value.includes(n))));
    if (!allowed) { issue.value = '当前状态或权限不允许此操作，请先保存并刷新后重试。'; return; }
    const payload: ContentWrite = {operation_id:crypto.randomUUID(),expected_revision:action === 'create' ? props.edition.revision : item.value!.revision,reason:reason.value};
    if (action === 'revise') payload.body = JSON.parse(JSON.stringify(body.value));
    if (action === 'approve') payload.sample_pages = [...samples.value];
    pending = {action,payload,contentId:item.value?.id};
  }
  const request = pending, version = ++generation;
  busy.value = true; issue.value = ''; notice.value = '';
  try {
    const result = request.action === 'create' ? await props.api.createTextbookContent(props.edition.id,props.job.id,request.payload) :
      await props.api.writeTextbookContent(props.edition.id,request.contentId!,request.action,request.payload);
    if (!valid(version)) return;
    // An idempotent replay can return an old revision; always obtain the current head.
    clearBody(); item.value = result; history.value = []; historyLoaded.value = false;
    const head = await props.api.textbookContent(props.edition.id,result.id);
    if (!valid(version)) return;
    item.value = head; pending = null; uncertain.value = false; reason.value = '';
    notice.value = `${actions[request.action]}已确认，当前版本 v${head.revision}。`;
  } catch (error) {
    if (!valid(version)) return;
    uncertain.value = !(error instanceof ApiError) || ![401,403,404,409,422].includes(error.status);
    if (!uncertain.value) pending = null;
    failed(error);
  } finally { if (valid(version)) busy.value = false; }
}
async function loadHistory(more = false) {
  if (busy.value || denied.value || uncertain.value || !item.value) return;
  busy.value = true; issue.value = ''; const version = ++generation;
  try {
    const result = await props.api.textbookContentHistory(props.edition.id,item.value.id,more ? before.value ?? undefined : undefined);
    if (!valid(version)) return;
    history.value = more ? [...history.value,...result.items] : result.items; before.value = result.next_before; historyLoaded.value = true;
  } catch (error) { if (valid(version)) failed(error); }
  finally { if (valid(version)) busy.value = false; }
}
async function exportTxt() {
  if (busy.value || denied.value || uncertain.value || !item.value?.content_available) return;
  busy.value = true; txt.value = ''; issue.value = ''; const version = ++generation;
  try {
    const result = await props.api.textbookContentTxt(props.edition.id,item.value.id,item.value.revision);
    if (!valid(version)) return;
    txt.value = result.text;
    const url = URL.createObjectURL(new Blob([result.text],{type:'text/plain;charset=utf-8'}));
    const a = document.createElement('a'); a.href = url; a.download = `${props.edition.code}-v${result.revision}.txt`; a.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
    notice.value = '已生成 TXT 下载，同时显示本次在线读取的正文。';
  } catch (error) { if (valid(version)) failed(error); }
  finally { if (valid(version)) busy.value = false; }
}
</script>

<template>
  <section class="content-workspace" aria-label="教材正文校核工作区">
    <div class="content-heading"><div><span class="content-eyebrow">教材内容 · 人工校核</span><h5><BookOpen :size="18" />正文与章节工作区</h5></div>
      <ElButton :disabled="busy || denied || uncertain || dirty" @click="find()">{{ opened ? '刷新当前版本' : '打开校核工作区' }}</ElButton></div>
    <template v-if="opened">
      <p class="content-note">对照左侧原件逐页校核，页码使用 PDF 物理页；窄屏时原件显示在正文上方。勾选为人工核对声明，不代表系统自动判定一致。本流程未执行 OCR，发布后尚未接入 AI 检索。</p>
      <p v-if="issue" class="error-box" role="alert">{{ issue }}</p><p v-if="notice" role="status">{{ notice }}</p>
      <p v-if="busy" role="status">正在处理，请稍候…</p>
      <div v-if="uncertain" class="content-warning" role="alert">操作结果未确认。原请求已保留，请重试以确认结果。<ElButton :disabled="busy" @click="send()">重试原操作</ElButton></div>
      <template v-if="searched && !item">
        <p>{{ cursor ? '当前页未找到该来源的正文，请继续查找。' : '此来源尚未建立校核正文。' }}</p>
        <ElButton v-if="cursor" :disabled="busy || uncertain" @click="find(true)">继续查找更早正文</ElButton>
        <template v-else-if="write && authorized"><label>操作说明<input v-model="reason" aria-label="正文操作说明" maxlength="500" :disabled="busy || uncertain" placeholder="例如：建立人工校核稿" /></label><ElButton type="primary" :disabled="busy || uncertain" @click="send('create')">建立校核草稿</ElButton></template>
      </template>
      <template v-if="item">
        <div class="content-summary"><ElTag :type="item.state === 'published' ? 'success' : 'info'">{{ labels[item.state] }}</ElTag><strong>内容 v{{ item.revision }}</strong><span>{{ item.page_count }} 页 · {{ item.chapter_count }} 章节 · {{ item.verified_pages }} 页已校核</span></div>
        <p class="content-note">来源授权版本 v{{ item.edition_revision }} · {{ item.id }}</p>
        <p v-if="!authorized" class="content-warning">来源版本或授权已变化，正文读取与审核已停用；可按权限查看历史记录或撤回内容。</p>
        <div class="content-toolbar"><ElButton :disabled="busy || uncertain || dirty || !authorized" @click="read()">{{ body ? '重新读取当前正文' : '读取当前正文' }}</ElButton><ElButton :disabled="busy || uncertain || denied" @click="loadHistory()">查看内容历史</ElButton><ElButton v-if="item.content_available" :disabled="busy || uncertain || !authorized" @click="exportTxt()">下载已发布 TXT</ElButton></div>
        <template v-if="body">
          <div class="content-progress"><FileCheck2 :size="18" /><span>正在查看 v{{ viewedRevision }}{{ current ? '' : '（历史只读）' }} · {{ verified }}/{{ body.pages.length }} 页已核对</span><ElProgress :percentage="progress" :stroke-width="6" /></div>
          <div class="content-editor">
            <aside class="content-pages"><label>校核页码<select v-model="page" aria-label="校核页码" :disabled="busy || uncertain"><option v-for="p in body.pages" :key="p.number" :value="p.number">第 {{ p.number }} 页 · {{ p.verified ? '已核对' : '待核对' }}</option></select></label><p class="content-note">修改正文会取消该页的核对标记。保存后才会产生新版本。</p></aside>
            <div v-if="selectedPage" class="content-comparison"><TextbookPdfPreview v-if="authorized" :api="api" :edition="edition.id" :document="job.document_id" :job="job.id" :page="page" @failure="failed" /><div class="content-paper"><div class="content-heading"><strong>PDF 第 {{ page }} 页</strong><label class="content-check"><input v-model="selectedPage.verified" aria-label="本页已与原件核对" type="checkbox" :disabled="!editable" @change="changed" />本页已与原件核对</label></div><textarea :value="selectedPage.text" aria-label="本页校核正文" :readonly="!editable" maxlength="200000" rows="12" placeholder="此页尚无文本；请参照原件补充或确认空白" @input="editText" /></div></div>
          </div>
          <div class="content-heading chapter-heading"><div><h5>章节与源页映射</h5><p class="content-note">父章节排在子章节之前，子章节页范围须包含在父章节内；提交前覆盖全部 PDF 页。</p></div><ElButton v-if="editable" :disabled="body.chapters.length >= 500" @click="addChapter">新增章节</ElButton></div>
          <p v-if="!body.chapters.length" class="content-note">尚无章节映射。</p>
          <fieldset v-for="(chapter,index) in body.chapters" :key="index" class="content-chapter" :disabled="!editable" @input="changed" @change="changed"><legend>章节 {{ index+1 }}</legend>
            <label>章节编号<input v-model="chapter.code" :aria-label="`章节${index+1}编号`" maxlength="40" /></label><label>章节名称<input v-model="chapter.title" :aria-label="`章节${index+1}名称`" maxlength="200" /></label>
            <label>父章节<select v-model="chapter.parent_code" :aria-label="`章节${index+1}父章节`"><option :value="null">顶级章节</option><option v-for="parent in body.chapters.slice(0,index)" :key="parent.code" :value="parent.code">{{ parent.code }} · {{ parent.title }}</option></select></label>
            <label>起始页<input v-model.number="chapter.first_page" :aria-label="`章节${index+1}起始页`" type="number" min="1" :max="body.pages.length" /></label><label>结束页<input v-model.number="chapter.last_page" :aria-label="`章节${index+1}结束页`" type="number" min="1" :max="body.pages.length" /></label><ElButton v-if="editable" type="danger" plain @click="body.chapters.splice(index,1); changed()">移除章节 {{ index+1 }}</ElButton>
          </fieldset>
        </template>
        <p v-if="dirty" class="content-warning">有未保存的正文或章节变更。请先保存，再读取、刷新或提交。<ElButton :disabled="busy || uncertain" @click="dirty = false; read()">放弃未保存更改</ElButton></p>
        <template v-if="write || review"><label>操作说明<input v-model="reason" aria-label="正文操作说明" maxlength="500" :disabled="busy || uncertain || denied" placeholder="记录本次校核依据、审核结论或撤回原因" /></label>
          <div class="content-toolbar"><ElButton v-if="write && authorized && body && current && ['draft','withdrawn'].includes(item.state)" type="primary" :disabled="busy || uncertain || !dirty" @click="send('revise')">保存正文与章节</ElButton>
            <ElButton v-if="write && authorized && item.state === 'draft'" :disabled="busy || uncertain || dirty || !current || !complete || !item.ready" @click="send('submit')">提交独立审核</ElButton>
            <ElButton v-if="write && ['pending_review','published'].includes(item.state)" type="danger" plain :disabled="busy || uncertain || denied" @click="send('withdraw')">撤回内容</ElButton></div>
        </template>
        <div v-if="item.state === 'pending_review'" class="content-review"><h5>独立审核与发布</h5><p class="content-note">首末页、源提取空白页及发生修订的页均为必检页。审核人不得参与本内容的创建、修订或提交。</p>
          <p v-if="!review">当前账号没有教材审核权限。</p><p v-else-if="!independent">您参与过本内容的编辑，请由独立审核人处理。</p>
          <template v-else-if="authorized"><div class="content-samples"><label v-for="n in item.required_sample_pages" :key="n" class="content-check"><input v-model="samples" type="checkbox" :value="n" :aria-label="`抽检第${n}页已核对`" :disabled="busy || uncertain || !current || !body" />第 {{ n }} 页已核对</label></div><div class="content-toolbar"><ElButton type="primary" :disabled="busy || uncertain || !body || !current || !item.required_sample_pages.every(n => samples.includes(n)) || !edition.rights?.scopes.includes('retrieval')" @click="send('approve')">审核通过并发布</ElButton><ElButton type="danger" plain :disabled="busy || uncertain" @click="send('reject')">驳回至校核</ElButton></div><p v-if="!edition.rights?.scopes.includes('retrieval')" class="content-warning">发布需要教材具有有效的检索授权。</p></template>
        </div>
        <div v-if="historyLoaded" class="content-history" aria-label="内容版本历史"><h5>版本历史</h5><ol><li v-for="row in history" :key="row.revision"><div class="content-heading"><strong>v{{ row.revision }} · {{ actions[row.action] }}</strong><ElButton :disabled="busy || uncertain || dirty || !authorized" @click="read(row.revision)">查看 v{{ row.revision }} 正文</ElButton></div><p>{{ row.reason }}</p><small>{{ new Date(row.created_at).toLocaleString('zh-CN') }} · {{ row.actor_id }}</small></li></ol><ElButton v-if="before" :disabled="busy || uncertain" @click="loadHistory(true)">加载更早内容版本</ElButton></div>
        <details v-if="txt"><summary>本次下载的 TXT 正文</summary><pre class="content-txt">{{ txt }}</pre></details>
      </template>
    </template>
  </section>
</template>

<style scoped>
.content-workspace{margin-top:20px;border:1px solid var(--el-border-color);border-top:3px solid var(--el-color-primary);border-radius:8px;background:var(--el-bg-color);padding:20px;min-width:0}.content-heading,.content-summary,.content-toolbar,.content-progress,.content-check,.content-samples{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.content-heading{justify-content:space-between}.content-heading h5,.content-review h5,.content-history h5{display:flex;gap:8px;align-items:center;font-size:15px;margin:4px 0}.content-eyebrow{font-size:11px;letter-spacing:.12em;color:var(--el-color-primary);font-weight:600}.content-note{font-size:13px;color:var(--el-text-color-secondary);line-height:1.8;overflow-wrap:anywhere}.content-summary{padding:14px 0;border-bottom:1px solid var(--el-border-color-light);font-size:13px}.content-toolbar{margin:14px 0}.content-toolbar>.el-button{margin-left:0}.content-warning{padding:12px;background:var(--el-color-warning-light-9);color:var(--el-color-warning-dark-2);border-radius:6px;font-size:13px;line-height:1.8;overflow-wrap:anywhere}.content-workspace label{display:grid;gap:6px;font-size:13px;min-width:0}.content-workspace input:not([type=checkbox]),.content-workspace select,.content-workspace textarea{box-sizing:border-box;width:100%;min-width:0;max-width:100%;border:1px solid var(--el-border-color);border-radius:6px;padding:9px 10px;color:inherit;background:var(--el-bg-color);font:inherit}.content-workspace :is(input,select,textarea):focus-visible{outline:2px solid var(--el-color-primary);outline-offset:2px}.content-workspace textarea{line-height:1.9;resize:vertical;min-height:240px}.content-workspace .content-check{display:flex;gap:8px;font-size:13px}.content-progress{margin:20px 0;font-size:13px;color:var(--el-text-color-regular)}.content-progress .el-progress{flex:1;min-width:120px}.content-editor{display:grid;grid-template-columns:170px minmax(0,1fr);border:1px solid var(--el-border-color-light);border-radius:8px;overflow:hidden}.content-pages{padding:16px;background:var(--el-fill-color-light)}.content-comparison{min-width:0;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.content-paper{min-width:0;padding:16px}.content-paper textarea{min-height:360px}.content-paper>.content-heading{margin-bottom:12px;font-size:13px}.chapter-heading{margin-top:24px}.content-chapter{min-width:0;margin:12px 0;border:1px solid var(--el-border-color-light);border-radius:8px;padding:12px;display:grid;grid-template-columns:1fr 2fr 1.5fr .8fr .8fr;gap:12px;align-items:end}.content-chapter legend{font-size:12px;color:var(--el-text-color-secondary)}.content-chapter>.el-button{margin:0;width:max-content}.content-review{margin-top:20px;border-top:1px solid var(--el-border-color-light);padding-top:16px}.content-samples{padding:12px 0}.content-history ol{padding:0;list-style:none}.content-history li{padding:12px 0;border-bottom:1px solid var(--el-border-color-light);font-size:13px;overflow-wrap:anywhere}.content-history small{color:var(--el-text-color-secondary)}.content-txt{white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto;font-size:13px;line-height:1.8}.content-workspace summary{cursor:pointer;font-size:13px}.content-workspace :disabled{cursor:not-allowed}
@media(max-width:1100px){.content-comparison{grid-template-columns:minmax(0,1fr)}}
@media(max-width:800px){.content-chapter{grid-template-columns:repeat(2,minmax(0,1fr))}.content-editor{grid-template-columns:1fr}.content-workspace{padding:12px}.content-workspace .el-button{white-space:normal;height:auto;min-height:32px;max-width:100%}}
</style>
