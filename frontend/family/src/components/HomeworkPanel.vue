<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue';
import { ApiError } from '../api';
import type { FamilyApi, GradingResult, HomeworkActivity, Job, ModelOutput, Quota, Submission, SubmissionDetail, SubmissionFilters } from '../api';
import { HomeworkBatch, MAX_HOMEWORK_PAGES, pickerCount } from '../homework';
import type { HomeworkImage } from '../homework';
import { browserImage, editBrowserImage, previewBrowserImage } from '../image-editor';
import { chooseLocalImages, editLocalImage, privatePreview } from '../image-picker';
import FamilyButton from './FamilyButton.vue';
import FamilyInput from './FamilyInput.vue';
import HomeworkPageResult from './HomeworkPageResult.vue';
import { beijingDate, homeworkDays } from '../homework-history';
import LearningIcon from './LearningIcon.vue';

const props = defineProps<{ api: FamilyApi; student: string; guardian: boolean; visible: boolean; section: 'capture' | 'history' }>();
const emit = defineEmits<{ navigate: [section: 'capture' | 'history'] }>();
let active = true, selection = 0, historyVersion = 0, timer: ReturnType<typeof setInterval>;
const generation = props.api.generation, student = props.student;
const valid = () => active && props.student === student && props.api.generation === generation;
const batch = shallowRef(new HomeworkBatch(props.api, student, valid)), tick = ref(0);
const pages = computed(() => { tick.value; return [...batch.value.pages]; });
const locked = computed(() => { tick.value; return batch.value.running || !!batch.value.frozen; });
const working = ref(false), message = ref(''), quota = ref<Quota | null>(null);
const activityType = ref<HomeworkActivity>('auto'), referenceText = ref('');
const activityOptions: { value: HomeworkActivity; label: string }[] = [{ value: 'auto', label: '自动识别' }, { value: 'exercise', label: '普通作业' }, { value: 'dictation', label: '单词听写' }];
const messageKind = ref<'success' | 'error'>('success');
const history = ref<Submission[]>([]), cursor = ref<string | null>(null), detail = ref<SubmissionDetail | null>(null);
const job = ref<Job | null>(null), result = ref<GradingResult | null>(null), confirmingCancel = ref(false);
const outputs = ref<ModelOutput[]>([]), selectedPage = ref(1), showRaw = ref(false);
const selectedDate = ref(beijingDate()), availableDays = ref<{ date: string; count: number }[]>([]);
const days = computed(() => homeworkDays(availableDays.value));
const pageResult = computed(() => [...outputs.value].reverse().find(output => output.status === 'succeeded' && output.page_results?.length)?.page_results?.find(page => page.position === selectedPage.value));
const subjectLabels: Record<string, string> = { math: '数学作业', english: '英语作业', chinese: '语文作业', auto: '作业' };
const originals = ref<{ position: number; url: string; release: () => void }[]>([]);
const editing = ref<HomeworkImage | null>(null), turns = ref(0), cropX = ref('0'), cropY = ref('0'), cropW = ref('100'), cropH = ref('100');
const nativeInput = 'input';
async function selectDay(date: string) {
  if (date === selectedDate.value) return;
  selectedDate.value = date;
  await work(() => loadHistory());
}
async function loadDays() {
  const value = await props.api.submissionDays(student);
  if (valid()) availableDays.value = value.items;
}
async function refreshHistory() { await work(async () => { await Promise.all([loadDays(), loadHistory()]); }); }
const phases = { waiting: '等待上传', reading: '读取图片', uploading: '上传原图', checking: '检查清晰度与格式', ready: '已上传' };
const labels: Record<string, string> = { queued: '等待处理', processing: '正在处理', running: '正在处理', retry_wait: '等待重试', completed: '处理完成', partial: '部分页面完成', failed: '处理失败', cancelled: '已取消', succeeded: '处理完成' };
const cancellable = computed(() => !!job.value && ['queued', 'running', 'retry_wait'].includes(job.value.status));
const canRegrade = computed(() => !!job.value?.error_code && ['failed', 'partial'].includes(detail.value?.status ?? ''));
const cropStyle = computed(() => ({ left: cropX.value + '%', top: cropY.value + '%', width: cropW.value + '%', height: cropH.value + '%' }));
const editPreview = ref<{ url: string; width: number; height: number } | null>(null);
const previewStyle = computed(() => editPreview.value ? { aspectRatio: `${editPreview.value.width}/${editPreview.value.height}`, maxWidth: `${360 * editPreview.value.width / editPreview.value.height}px` } : {});
function changed() { tick.value++; }
function releaseOriginals() { originals.value.forEach(image => image.release()); originals.value = []; }
function report(error: unknown) {
  if (!valid() || error instanceof ApiError && error.code === 'stale') return;
  messageKind.value = 'error';
  message.value = error instanceof Error ? error.message : '图片选择未完成，可重新选择或改用相册。';
}
async function work(action: () => Promise<void>) {
  if (working.value || !valid()) return;
  working.value = true; message.value = ''; messageKind.value = 'success';
  try { await action(); } catch (error) { report(error); } finally { if (active) working.value = false; }
}
function add(images: HomeworkImage[]) {
  try { if (!valid()) throw new ApiError(0, 'stale'); batch.value.add(images); message.value = ''; changed(); }
  catch (error) { images.forEach(image => image.release()); throw error; }
}
async function files(event: Event) {
  const input = event.target as HTMLInputElement;
  const selected = Array.from(input.files ?? []); input.value = '';
  if (!selected.length) return;
  const images: HomeworkImage[] = [];
  try { for (const file of selected) images.push(browserImage(file, file.name)); }
  catch (error) { images.forEach(image => image.release()); report(error); return; }
  try { add(images); } catch (error) { report(error); }
}
async function pick(source: 'camera' | 'album') {
  await work(async () => { try { add(await chooseLocalImages(source, pickerCount(pages.value.length))); }
    catch { throw new Error(source === 'camera' ? '拍照未完成，请检查相机权限，或使用相册选图。' : '相册选图未完成，请重新选择。'); } });
}
function move(index: number, offset: number) { batch.value.move(index, offset); changed(); }
function remove(index: number) { batch.value.remove(index); changed(); }
async function edit(image: HomeworkImage) {
  // #ifdef H5
  await work(async () => {
    const preview = await previewBrowserImage(image, 0);
    if (!valid()) return;
    editPreview.value = preview; editing.value = image; turns.value = 0;
    cropX.value = '0'; cropY.value = '0'; cropW.value = '100'; cropH.value = '100';
  });
  // #endif
  // #ifdef MP-WEIXIN
  await work(async () => {
    const edited = await editLocalImage(image);
    const index = pages.value.findIndex(page => page.id === image.id);
    try { batch.value.replace(index, edited); changed(); } catch (error) { edited.release(); throw error; }
  });
  // #endif
}
async function rotate() {
  const image = editing.value; if (!image) return;
  await work(async () => {
    const next = (turns.value + 1) % 4, preview = await previewBrowserImage(image, next);
    if (!valid() || editing.value !== image) return;
    editPreview.value = preview; turns.value = next;
  });
}
async function saveEdit() {
  const source = editing.value; if (!source) return;
  await work(async () => {
    const edited = await editBrowserImage(source, turns.value, { x: Number(cropX.value), y: Number(cropY.value), width: Number(cropW.value), height: Number(cropH.value) });
    try { batch.value.replace(pages.value.findIndex(page => page.id === source.id), edited); changed(); editing.value = null; editPreview.value = null; }
    catch (error) { edited.release(); throw error; }
  });
}
async function loadHistory(more = false) {
  const version = ++historyVersion, date = selectedDate.value;
  const filters: SubmissionFilters = { date_from: date, date_to: date };
  const page = await props.api.submissions(student, more ? cursor.value ?? undefined : undefined, filters);
  if (!valid() || version !== historyVersion || date !== selectedDate.value) return;
  history.value = more ? [...history.value, ...page.items] : page.items; cursor.value = page.next_cursor;
}
async function loadQuota() { if (props.guardian) { const value = await props.api.quota(); if (valid()) quota.value = value; } }
async function refreshDetail(id: string, version: number) {
  const record = await props.api.submission(id);
  if (!valid() || version !== selection) return;
  const state = await props.api.job(record.job_id);
  if (!valid() || version !== selection) return;
  const savedOutputs = await props.api.modelOutputs(id);
  if (!valid() || version !== selection) return;
  let projection: GradingResult | null = null;
  if (!savedOutputs.items.length && ['completed', 'partial', 'failed'].includes(record.status)) {
    try { projection = await props.api.result(id); } catch (error) { if (!(error instanceof ApiError && error.status === 409)) throw error; }
  }
  if (!valid() || version !== selection) return;
  detail.value = record; job.value = state; result.value = projection; outputs.value = savedOutputs.items;
  history.value = history.value.map(item => item.id === record.id ? { ...item, ...record } : item);
}
async function open(id: string) {
  const version = ++selection; releaseOriginals(); detail.value = null; job.value = null; result.value = null; outputs.value = []; confirmingCancel.value = false;
  selectedPage.value = 1; showRaw.value = false;
  await refreshDetail(id, version);
  await preview();
}
async function send() {
  await work(async () => {
    const receipt = await batch.value.send(true, changed, 'auto', activityType.value, referenceText.value);
    if (!valid()) return;
    batch.value.dispose(); batch.value = new HomeworkBatch(props.api, student, valid); changed();
    activityType.value = 'auto'; referenceText.value = '';
    message.value = '作业已提交，正在自动识别学科并批改。';
    selectedDate.value = beijingDate();
    emit('navigate', 'history');
    await Promise.all([loadDays(), loadHistory(), loadQuota(), open(receipt.submission_id)]);
  });
}
async function preview() {
  const record = detail.value, version = selection; if (!record) return;
  releaseOriginals();
  const loaded: typeof originals.value = [];
  try {
    for (const page of record.pages) {
      const bytes = await props.api.mediaContent(page.media_id);
      if (!valid() || version !== selection) return;
      loaded.push({ position: page.position, ...privatePreview(bytes) });
    }
    originals.value = loaded.splice(0);
  } finally { loaded.forEach(image => image.release()); }
}
async function regrade() {
  const current = detail.value; if (!current) return;
  await work(async () => {
    await props.api.regrade(current.id);
    await refreshDetail(current.id, selection);
    message.value = '已重新开始批改，完成后会显示在这里。';
  });
}
async function cancel() {
  const record = detail.value, version = selection; if (!record) return;
  await work(async () => {
    await props.api.cancelJob(record.job_id);
    if (!valid() || version !== selection) return;
    confirmingCancel.value = false;
    await Promise.all([refreshDetail(record.id, version), loadQuota()]);
    message.value = '作业已取消，未使用的预占页数已释放。';
  });
}
watch(() => [props.visible, props.section] as const, ([visible, section]) => {
  if (!visible) return;
  if (section === 'history') {
    if (!working.value) { detail.value = null; selection++; releaseOriginals(); }
    void refreshHistory();
  }
  else void work(loadQuota);
});
onMounted(() => {
  if (props.visible) void work(async () => { await Promise.all([loadDays(), loadHistory(), loadQuota()]); });
  timer = setInterval(() => {
    if (!props.visible || working.value || !cancellable.value || !detail.value) return;
    void work(async () => { await refreshDetail(detail.value!.id, selection); await loadQuota(); });
  }, 5000);
});
onUnmounted(() => { active = false; selection++; clearInterval(timer); batch.value.dispose(); releaseOriginals(); editing.value = null; });
</script>

<template>
  <view class="homework-space">
    <template v-if="section === 'capture'">
    <view class="section-head"><view><text class="section-title">拍作业</text><text class="muted">作业、单词和短语听写可以一起上传，自动识别。</text></view><FamilyButton class="text-button compact" @click="emit('navigate', 'history')">查看作业记录</FamilyButton></view>
    <view class="panel upload-panel">
      <view class="upload-intro"><text class="small muted">每次 1–10 张，每张不超过 10 MB。</text><text v-if="quota" class="quota-note">剩余 {{ quota.remaining_pages }} 页</text></view>
      <view class="activity-options" role="group" aria-label="批改类型"><FamilyButton v-for="option in activityOptions" :key="option.value" class="secondary compact" :class="{ selected: activityType === option.value }" :aria-pressed="activityType === option.value" :disabled="working || locked" @click="activityType = option.value">{{ option.label }}</FamilyButton></view>
      <view class="reference-field">
        <text class="field-label">参考词表（可选）</text>
        <FamilyInput v-model="referenceText" multiline class="field reference-input" aria-label="参考词表（可选）" :maxlength="8000" :disabled="working || locked" placeholder="例如：第1张左列 1. apple；2. take care of。多页请标注页码和对应位置。" />
        <text class="small muted">未提供参考时仅检查拼写，不能核对听写目标、漏写或顺序。</text>
      </view>
      <view class="pick-row">
        <!-- #ifdef H5 -->
        <view class="file-pick" :class="{ 'file-pick-disabled': working || locked || !!editing || pages.length >= MAX_HOMEWORK_PAGES }"><LearningIcon name="book" /><text>相册选图</text><component :is="nativeInput" class="native-file-input" type="file" accept="image/jpeg,image/png,image/webp" multiple aria-label="选择作业图片" :disabled="working || locked || !!editing || pages.length >= MAX_HOMEWORK_PAGES" @change="files" /></view>
        <view class="file-pick camera-pick" :class="{ 'file-pick-disabled': working || locked || !!editing || pages.length >= MAX_HOMEWORK_PAGES }"><LearningIcon name="camera" /><text>拍照</text><component :is="nativeInput" class="native-file-input" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" aria-label="拍摄作业图片" :disabled="working || locked || !!editing || pages.length >= MAX_HOMEWORK_PAGES" @change="files" /></view>
        <!-- #endif -->
        <!-- #ifdef MP-WEIXIN -->
        <FamilyButton class="secondary" :disabled="working || locked || pages.length >= MAX_HOMEWORK_PAGES" @click="pick('album')">相册选图</FamilyButton><FamilyButton class="secondary" :disabled="working || locked || pages.length >= MAX_HOMEWORK_PAGES" @click="pick('camera')">拍照</FamilyButton>
        <!-- #endif -->
        <text class="small muted">{{ pages.length }}/{{ MAX_HOMEWORK_PAGES }} 页 · 题目缺边或模糊时，请移除后重拍</text>
      </view>
      <view v-if="!pages.length" class="upload-empty"><view class="capture-symbol"><LearningIcon name="camera" /></view><view><text class="student-name">拍清完整题目和作答</text><text class="muted small">可以一次选择多张，不用分学科。</text></view></view>
      <view class="page-grid"><view v-for="(page, index) in pages" :key="page.id" class="upload-page"><image :src="page.preview" mode="aspectFit" :alt="'作业第 ' + (index + 1) + ' 页'" class="page-image" /><view class="page-caption"><text class="page-order">第 {{ index + 1 }} 页</text><text class="small">{{ phases[page.phase] }}</text></view><text class="page-filename small muted">{{ page.name }}</text><view class="page-tools"><FamilyButton class="text-button compact" :aria-label="'第' + (index + 1) + '页前移'" :disabled="working || locked || !!editing || index === 0" @click="move(index, -1)">前移</FamilyButton><FamilyButton class="text-button compact" :aria-label="'第' + (index + 1) + '页后移'" :disabled="working || locked || !!editing || index === pages.length - 1" @click="move(index, 1)">后移</FamilyButton><FamilyButton class="text-button compact" :aria-label="'编辑第' + (index + 1) + '页'" :disabled="working || locked || !!editing" @click="edit(page)">编辑</FamilyButton><FamilyButton class="text-button compact danger" :aria-label="'移除第' + (index + 1) + '页'" :disabled="working || locked || !!editing" @click="remove(index)">移除</FamilyButton></view></view></view>
      <!-- #ifdef H5 -->
      <view v-if="editing" class="image-editor"><text class="section-title">旋转与裁剪</text><text class="small muted">先旋转，再按百分比调整裁剪框；保留完整题目与作答。</text><view class="editor-preview" :style="previewStyle"><image :src="editPreview?.url" mode="scaleToFill" class="edit-image" /><view class="crop-outline" :style="cropStyle" /></view><FamilyButton class="secondary compact" :disabled="working" @click="rotate">顺时针旋转 90°</FamilyButton><view class="crop-fields"><view v-for="field in ['left', 'top', 'width', 'height']" :key="field"><text class="small">{{ {left:'左边距 %',top:'上边距 %',width:'宽度 %',height:'高度 %'}[field] }}</text><FamilyInput v-if="field === 'left'" v-model="cropX" class="field" aria-label="裁剪左边距百分比" type="number" :disabled="working" /><FamilyInput v-else-if="field === 'top'" v-model="cropY" class="field" aria-label="裁剪上边距百分比" type="number" :disabled="working" /><FamilyInput v-else-if="field === 'width'" v-model="cropW" class="field" aria-label="裁剪宽度百分比" type="number" :disabled="working" /><FamilyInput v-else v-model="cropH" class="field" aria-label="裁剪高度百分比" type="number" :disabled="working" /></view></view><view class="button-row"><FamilyButton class="primary" :disabled="working" @click="saveEdit">应用图片调整</FamilyButton><FamilyButton class="secondary" :disabled="working" @click="editing = null">取消图片调整</FamilyButton></view></view>
      <!-- #endif -->
      <text v-if="batch.frozen" class="notice">提交回执尚待确认。请使用下方按钮重试，保留同一份作业和页序。</text>
      <FamilyButton class="primary submit-homework" :disabled="working || !pages.length || !!editing" @click="send">{{ working && batch.running ? '正在提交作业…' : batch.frozen ? '重试确认作业提交' : '提交已完成的作业' }}</FamilyButton>
      <view v-if="message" class="notice upload-message" :class="{ error: messageKind === 'error' }" :role="messageKind === 'error' ? 'alert' : 'status'"><LearningIcon v-if="messageKind === 'success'" name="check" /><text>{{ message }}</text></view>
    </view>
    </template>
    <template v-else>
      <template v-if="!detail">
        <view class="section-head"><text class="section-title">作业记录</text><FamilyButton class="secondary compact" :disabled="working" @click="refreshHistory">刷新记录</FamilyButton></view>
        <view class="date-tabs" role="tablist" aria-label="作业日期"><FamilyButton v-for="day in days" :key="day.date" class="date-tab" :class="{ selected: selectedDate === day.date }" role="tab" :aria-selected="selectedDate === day.date" :disabled="working" @click="selectDay(day.date)"><text>{{ day.label }}</text><text class="small">{{ day.count }} 份</text></FamilyButton></view>
        <view v-if="!history.length" class="panel empty-state"><text class="section-title">这一天还没有作业</text><FamilyButton class="primary" @click="emit('navigate', 'capture')">去拍作业</FamilyButton></view>
        <view v-for="record in history" :key="record.id" class="panel submission-row"><view><text class="section-title">{{ subjectLabels[record.subject] ?? '作业' }}<text v-if="record.page_count"> · {{ record.page_count }} 张</text></text><text class="small muted">{{ new Date(record.created_at).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit' }) }} 提交</text></view><text class="badge">{{ labels[record.status] ?? '状态待刷新' }}</text><FamilyButton class="secondary compact" :disabled="working" @click="work(() => open(record.id))">查看详情</FamilyButton></view>
        <FamilyButton v-if="cursor" class="secondary load-more" :disabled="working" @click="work(() => loadHistory(true))">加载更早的作业</FamilyButton>
      </template>
      <view v-else class="panel homework-detail">
        <view class="section-head"><FamilyButton class="text-button compact" :disabled="working" @click="detail = null; selection++; releaseOriginals(); work(() => loadHistory())">返回作业记录</FamilyButton><text class="badge">{{ labels[detail.status] ?? '状态待刷新' }}</text></view>
        <view class="detail-heading"><text class="section-title">{{ new Date(detail.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) }} · {{ detail.pages.length }} 张作业</text><text class="small muted">原图与本张批改结果逐一对照。</text></view>
        <view class="image-tabs" role="tablist" aria-label="作业图片"><FamilyButton v-for="page in detail.pages" :key="page.position" class="secondary compact" :class="{ selected: selectedPage === page.position }" role="tab" :aria-selected="selectedPage === page.position" @click="selectedPage = page.position">第 {{ page.position }} 张</FamilyButton></view>
        <HomeworkPageResult :position="selectedPage" :original="originals.find(item => item.position === selectedPage)?.url" :result="pageResult" :pending="cancellable" />
        <view class="button-row detail-actions"><FamilyButton class="secondary compact" :disabled="working" @click="work(async () => { await refreshDetail(detail!.id, selection); if (!originals.length) await preview(); })">刷新处理状态</FamilyButton><FamilyButton v-if="canRegrade" class="secondary compact" :disabled="working" @click="regrade">重新批改</FamilyButton><FamilyButton v-if="cancellable" class="text-button compact danger" :disabled="working" @click="confirmingCancel = !confirmingCancel">取消本次处理</FamilyButton><FamilyButton v-if="outputs.length" class="text-button compact" @click="showRaw = !showRaw">{{ showRaw ? '收起批改记录' : '查看批改记录' }}</FamilyButton></view>
        <view v-if="confirmingCancel && cancellable" class="notice"><text>取消后释放未使用的页数。</text><FamilyButton class="secondary danger" :disabled="working" @click="cancel">确认取消作业</FamilyButton></view>
        <text v-if="job?.error_code" class="notice error">本次批改未全部完成，已返回的结果会保留。请核对图片或稍后重新提交。</text>
        <text v-for="output in outputs.filter(item => item.error_message)" :key="output.id" class="notice error">{{ output.error_message }}</text>
        <view v-if="showRaw" class="model-results"><text class="small muted">两次批改分别保存，逐图展示采用最后一次成功的完整结果，模型判断请结合原图核对。</text><view v-for="output in outputs" :key="output.id" class="model-output"><text class="section-title">{{ output.sequence }}. {{ output.prompt_name }}</text><text class="small muted">{{ output.model_name }} · 模型优先级 {{ output.model_priority }} · 提示词优先级 {{ output.prompt_priority }}</text><text class="model-answer">{{ output.content || (output.status === 'running' ? '正在批改…' : output.error_message) }}</text></view></view>
        <view v-for="page in result?.pages.filter(item => item.position === selectedPage) ?? []" :key="page.position" class="result-page"><text class="field-label">历史识别内容</text><view v-for="question in page.questions ?? []" :key="question.ordinal" class="recognized-question"><text>{{ question.stem }}</text><text>{{ question.student_answer_raw || '未识别到作答' }}</text></view></view>
      </view>
      <view v-if="message" class="notice upload-message" :class="{ error: messageKind === 'error' }" :role="messageKind === 'error' ? 'alert' : 'status'"><text>{{ message }}</text></view>
    </template>
  </view>
</template>

<style scoped>
.activity-options{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}.activity-options .selected{background:var(--blue-soft);border-color:var(--primary);color:var(--primary)}.reference-field{display:flex;flex-direction:column;gap:8px}.reference-input{width:100%;box-sizing:border-box;min-height:90px;resize:vertical}
.model-results{margin-top:24px}.model-output{padding:20px 0;border-top:1px solid var(--line)}.model-output>.small{display:block;margin-top:8px}.model-answer{display:block;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;margin-top:16px}.date-tabs,.image-tabs{display:flex;gap:10px;overflow-x:auto;padding:8px 0 16px;margin-top:16px}.date-tab{display:flex;flex-direction:column;gap:4px;flex-shrink:0;min-width:88px;padding:12px 16px;background:#fff;border:1px solid var(--line);border-radius:10px}.date-tab.selected,.image-tabs .selected{background:var(--blue-soft);border-color:var(--primary);color:var(--primary)}.detail-heading{margin-top:24px}.detail-heading>.small{display:block;margin-top:6px}.detail-actions{margin-top:24px}
.homework-space{margin-top:32px}.upload-panel{border-top:0}.upload-intro,.pick-row,.page-caption,.page-tools,.submission-row{display:flex;align-items:center;gap:12px}.upload-intro{justify-content:space-between;align-items:flex-start}.quota-note{font-size:12px;color:var(--primary);max-width:180px;text-align:right;line-height:1.7}.pick-row{flex-wrap:wrap;margin:24px 0}.file-pick{position:relative;display:flex;align-items:center;gap:9px;min-height:48px;padding:11px 20px;border:1px solid #c7d4f2;border-radius:10px;color:var(--primary);background:var(--blue-soft);font-size:15px;font-weight:600;overflow:hidden}.file-pick-disabled{opacity:.5;cursor:default}.native-file-input{position:absolute;inset:0;opacity:0;width:100%;height:100%;cursor:pointer}.file-pick:focus-within{outline:3px solid #8c9fe5;outline-offset:3px}.upload-empty{display:flex;align-items:center;gap:20px;padding:25px;border:1px dashed #c7d4e8;border-radius:12px;background:var(--paper);margin-bottom:20px}.capture-symbol{display:flex;align-items:center;justify-content:center;width:52px;height:60px;border:1px solid #c7d4f2;background:#fff;border-radius:9px;flex-shrink:0}.page-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin:16px 0}.upload-page{border:1px solid #dae5e9;border-radius:12px;overflow:hidden;padding-bottom:10px;min-width:0}.page-image{display:block;width:100%;height:190px;background:#edf3f5}.page-caption{justify-content:space-between;padding:12px 12px 0}.page-order{font-weight:700;color:var(--primary)}.page-filename{display:block;margin:6px 12px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.page-tools{gap:2px;flex-wrap:wrap;padding:0 8px}.image-editor{padding:20px;border:1px solid #a4cbca;border-radius:12px;background:#f7fbfa;margin:20px 0}.editor-preview{position:relative;width:100%;background:#e2eded;overflow:hidden;margin:15px auto}.edit-image{position:absolute;inset:0;width:100%;height:100%}.crop-outline{position:absolute;border:2px dashed var(--primary);box-sizing:border-box;pointer-events:none;background:#3659ce10}.crop-fields{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:15px 0}.crop-fields .field{width:100%;box-sizing:border-box}.submit-homework{margin-top:18px}.upload-message{display:flex;align-items:flex-start;gap:10px;margin-top:16px}.submission-row{justify-content:space-between;padding:20px;margin:12px 0}.submission-row>view:first-child{min-width:0;flex:1}.record-id{display:block}.homework-detail{margin-top:0}.original-image{display:block;width:100%;max-width:760px;margin-top:12px}.original-page,.result-page{padding-top:20px;margin-top:16px;border-top:1px solid #dae5e9}.recognized-question{display:flex;flex-direction:column;gap:8px;padding:14px 0}.record-id{font-family:monospace}@media(max-width:600px){.filter-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.upload-intro{flex-direction:column}.quota-note{text-align:left;max-width:none}.page-grid{grid-template-columns:1fr}.submission-row{flex-wrap:wrap}.submission-row>view:first-child{flex-basis:100%}.crop-fields{grid-template-columns:repeat(2,minmax(0,1fr))}.upload-empty{padding:18px}.section-head{flex-wrap:wrap}.quota-note{max-width:100%}}
</style>
