<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue';
import { ApiError, operationId } from '../api';
import type { FamilyApi } from '../api';
import type { DailyItem, HomeworkDraft, HomeworkFields as Fields, HomeworkImport, HomeworkImportSummary, ImportCandidate } from '../daily-homework-types';
import { pickerCount } from '../homework';
import type { HomeworkImage } from '../homework';
import { HomeworkImportBatch, importStates, subjects, mergeDraft, itemFields } from '../daily-homework';
import { browserImage, editBrowserImage, previewBrowserImage } from '../image-editor';
import { chooseLocalImages, editLocalImage, privatePreview } from '../image-picker';
import FamilyButton from './FamilyButton.vue';
import FamilyInput from './FamilyInput.vue';
import ConsentCheck from './ConsentCheck.vue';
import HomeworkFields from './HomeworkFields.vue';
const props = defineProps<{ api: FamilyApi; student: string; visible: boolean }>();
const emit = defineEmits<{ published: [] }>();
const student = props.student, generation = props.api.generation;
let active = true, selection = 0, polling = false, timer: ReturnType<typeof setInterval>;
const valid = () => active && props.student === student && props.api.generation === generation;
const batch = shallowRef(new HomeworkImportBatch(props.api, student, valid)), tick = ref(0);
const pages = computed(() => { tick.value; return [...batch.value.pages]; });
const uploadLocked = computed(() => { tick.value; return working.value || batch.value.running || !!batch.value.frozen; });
const changed = () => { tick.value++; };
const source = ref('六年级10班家长群'), targetDate = ref(''), working = ref(false), message = ref('');
const history = ref<HomeworkImportSummary[]>([]), cursor = ref<string | null>(null), detail = ref<HomeworkImport | null>(null);
const draft = ref<HomeworkDraft>({ items: [], no_homework: [], notes: [] }), dirty = ref(false), reviewMerge = ref(false);
const previews = ref<{ id: string; url: string; release: () => void }[]>([]), selectedImage = ref(''), selectedBox = ref<number[] | null>(null);
const dayItems = ref<Record<string, DailyItem[]>>({}), unknownCost = ref(false), discardConfirm = ref(false);
const pending = ref<{ label: string; run: () => Promise<HomeworkImport>; applied?: boolean } | null>(null);
const processing = computed(() => !!detail.value && ['queued','processing'].includes(detail.value.status));
const closed = computed(() => !!detail.value && ['discarded','cancelled','applied'].includes(detail.value.status));
const locked = computed(() => working.value || !!pending.value || processing.value || closed.value || reviewMerge.value);
const editing = ref<HomeworkImage | null>(null), turns = ref(0), cropX = ref('0'), cropY = ref('0'), cropW = ref('100'), cropH = ref('100');
const editPreview = ref<{ url: string; width: number; height: number } | null>(null);
const nativeInput = 'input';
const boxStyle = computed(() => selectedBox.value ? { left: selectedBox.value[0]! * 100 + '%', top: selectedBox.value[1]! * 100 + '%', width: selectedBox.value[2]! * 100 + '%', height: selectedBox.value[3]! * 100 + '%' } : {});
function release() { previews.value.forEach(image => image.release()); previews.value = []; }
function report(error: unknown) { if (!valid() || error instanceof ApiError && error.code === 'stale') return; message.value = error instanceof Error ? error.message : '操作未完成。'; if (error instanceof ApiError && error.status === 409) message.value = '导入或日期清单已变化。当前修改仍保留，请刷新导入并核对后继续。'; }
async function work(action: () => Promise<void>) { if (working.value || !valid()) return; working.value = true; message.value = ''; try { await action(); } catch (error) { report(error); } finally { if (active) working.value = false; } }
async function list(more = false) { const value = await props.api.homeworkImports(student, more ? cursor.value ?? undefined : undefined); if (!valid()) return; history.value = more ? [...history.value, ...value.items] : value.items; cursor.value = value.next_cursor; }
function receive(value: HomeworkImport, keep = false) {
  if (!valid()) return;
  if (keep && dirty.value && detail.value?.id === value.id && detail.value.revision !== value.revision) { draft.value = mergeDraft(draft.value, value.draft, value.applied_ids); reviewMerge.value = true; }
  else if (!keep || !dirty.value) { draft.value = JSON.parse(JSON.stringify(value.draft)); dirty.value = false; reviewMerge.value = false; }
  detail.value = value;
  history.value = history.value.map(row => row.id === value.id ? value : row);
}
async function open(identifier: string) {
  if (dirty.value || pending.value) { message.value = '请先保存当前草稿或重试未确认的操作，再打开其他导入。'; return; }
  const version = ++selection;
  await work(async () => {
    const value = await props.api.homeworkImport(identifier); if (!valid() || version !== selection) return;
    receive(value); release(); selectedImage.value = value.images[0]?.id ?? ''; selectedBox.value = null; unknownCost.value = false; discardConfirm.value = false;
    for (const image of value.images) { const bytes = await props.api.mediaContent(image.media_id); if (!valid() || version !== selection) return; previews.value.push({ id: image.id, ...privatePreview(bytes) }); }
  });
}
async function refreshDetail() { if (!detail.value) return; const id = detail.value.id, version = selection; const fresh = await props.api.homeworkImport(id); if (valid() && version === selection) receive(fresh, true); }
async function poll() { if (!props.visible || working.value || polling || pending.value || !valid()) return; polling = true; try { if (processing.value) await refreshDetail(); await list(); } catch (error) { report(error); } finally { polling = false; } }
async function attempt() {
  if (!pending.value) return;
  await work(async () => {
    const operation = pending.value!;
    let value: HomeworkImport;
    try { value = await operation.run(); } catch (error) { if (!(error instanceof ApiError && error.status === 0)) pending.value = null; throw error; }
    if (!valid()) return;
    pending.value = null; receive(value); if (operation.applied) emit('published'); await list();
  });
}
async function mutate(label: string, run: () => Promise<HomeworkImport>, applied = false) { if (locked.value) return; pending.value = { label, run, applied }; await attempt(); }
function updateCandidate(candidate: ImportCandidate, fields: Fields) { if (candidate.subject !== fields.subject) candidate.merge_item_id = null; Object.assign(candidate, itemFields(fields)); candidate.conflict_confirmed = false; dirty.value = true; }
function dateCandidate(candidate: ImportCandidate, date: string) { candidate.assigned_date = date || null; candidate.date_confirmed = false; candidate.conflict_confirmed = false; candidate.merge_item_id = null; dirty.value = true; }
function setBox(candidate: ImportCandidate, index: number, value: string) {
  const box = [...(candidate.bbox ?? [0, 0, 1, 1])]; box[index] = Number(value) / 100;
  if (box.some(value => !Number.isFinite(value) || value < 0 || value > 1) || box[2]! <= 0 || box[3]! <= 0 || box[0]! + box[2]! > 1 || box[1]! + box[3]! > 1) { message.value = '位置框需在图片内，宽度和高度需大于0。'; return; }
  candidate.bbox = box; selectedBox.value = box; selectedImage.value = candidate.image_id; dirty.value = true;
}
async function save() {
  if (!detail.value) return;
  if (draft.value.items.some(item => !item.title.trim())) { message.value = '请填写每项作业内容。'; return; }
  if (draft.value.items.some(item => item.due_at && (!/(Z|[+-]\d{2}:\d{2})$/.test(item.due_at) || !Number.isFinite(Date.parse(item.due_at))))) { message.value = '请完整填写截止日期和时刻，或清空截止日期。'; return; }
  const id = detail.value.id, payload = { expected_revision: detail.value.revision, client_request_id: operationId(), draft: JSON.parse(JSON.stringify(draft.value)) };
  await mutate('保存识别草稿', () => props.api.saveHomeworkDraft(id, payload));
}
async function publish() {
  if (locked.value || !detail.value) return;
  if (dirty.value) { await save(); if (dirty.value || pending.value) return; }
  const record = detail.value, dates = new Set<string>();
  for (const candidate of draft.value.items) if (!record.applied_ids[candidate.id] && ['include','merge'].includes(candidate.decision)) {
    if (!candidate.assigned_date || !candidate.date_confirmed) { message.value = '收入清单的每一项都需要填写并明确确认布置日期。'; return; }
    if (candidate.due_at && (!/(Z|[+-]\d{2}:\d{2})$/.test(candidate.due_at) || !Number.isFinite(Date.parse(candidate.due_at)))) { message.value = '请完整填写截止日期和时刻，或清空截止日期。'; return; }
    dates.add(candidate.assigned_date);
  }
  for (const claim of draft.value.no_homework) if (!record.applied_ids[claim.id] && claim.confirmed && !claim.excluded) { if (!claim.assigned_date) { message.value = '无作业声明也需要完整日期。'; return; } dates.add(claim.assigned_date); }
  await work(async () => {
    const dayRevisions: Record<string, number> = {};
    for (const date of [...dates].sort()) { const day = await props.api.homeworkDay(student, date); if (!valid()) return; dayRevisions[date] = day.revision; }
    const payload = { expected_revision: record.revision, client_request_id: operationId(), day_revisions: dayRevisions };
    pending.value = { label: '发布已核对条目', run: () => props.api.applyHomeworkImport(record.id, payload), applied: true };
  });
  if (pending.value) await attempt();
}
async function mergeChoices(candidate: ImportCandidate) { if (!candidate.assigned_date) { message.value = '请先填写布置日期。'; return; } const date = candidate.assigned_date; await work(async () => { const day = await props.api.homeworkDay(student, date); if (valid()) dayItems.value = { ...dayItems.value, [date]: day.items }; }); }
async function retry(imageId: string) { if (!detail.value) return; if (dirty.value) { message.value = '请先保存识别草稿，再重试失败图片。'; return; } const id = detail.value.id, payload = { expected_revision: detail.value.revision, client_request_id: operationId(), image_ids: [imageId], confirm_unknown_cost: unknownCost.value }; await mutate('重试失败图片', () => props.api.retryHomeworkImport(id, payload)); }
async function discard() { if (!detail.value || !discardConfirm.value) return; const id = detail.value.id, payload = { expected_revision: detail.value.revision, client_request_id: operationId() }; await mutate('结束导入', () => props.api.discardHomeworkImport(id, payload)); }
function add(images: HomeworkImage[]) { try { batch.value.add(images); changed(); } catch (error) { images.forEach(image => image.release()); throw error; } }
async function files(event: Event) { const input = event.target as HTMLInputElement, values = Array.from(input.files ?? []); input.value = ''; if (!values.length) return; const images: HomeworkImage[] = []; try { for (const file of values) images.push(browserImage(file, file.name)); add(images); } catch (error) { report(error); } }
async function pick(source: 'camera' | 'album') { await work(async () => add(await chooseLocalImages(source, pickerCount(pages.value.length)))); }
async function edit(image: HomeworkImage) {
  // #ifdef H5
  await work(async () => { const preview = await previewBrowserImage(image, 0); if (!valid()) return; editPreview.value = preview; editing.value = image; turns.value = 0; cropX.value = '0'; cropY.value = '0'; cropW.value = '100'; cropH.value = '100'; });
  // #endif
  // #ifdef MP-WEIXIN
  await work(async () => { const updated = await editLocalImage(image); try { batch.value.replace(pages.value.findIndex(page => page.id === image.id), updated); changed(); } catch (error) { updated.release(); throw error; } });
  // #endif
}
async function rotate() { if (!editing.value) return; const image = editing.value; await work(async () => { const next = (turns.value + 1) % 4, preview = await previewBrowserImage(image, next); if (valid() && editing.value === image) { editPreview.value = preview; turns.value = next; } }); }
async function crop() { if (!editing.value) return; const image = editing.value; await work(async () => { const updated = await editBrowserImage(image, turns.value, { x: Number(cropX.value), y: Number(cropY.value), width: Number(cropW.value), height: Number(cropH.value) }); try { batch.value.replace(pages.value.findIndex(page => page.id === image.id), updated); changed(); editing.value = null; editPreview.value = null; } catch (error) { updated.release(); throw error; } }); }
async function send() {
  if (dirty.value || pending.value) { message.value = '请先保存当前识别草稿。'; return; }
  let identifier: string | null = null;
  await work(async () => { const value = await batch.value.send(source.value, targetDate.value || null, changed); if (!valid()) return; identifier = value.id; batch.value.dispose(); batch.value = new HomeworkImportBatch(props.api, student, valid); changed(); await list(); });
  if (identifier) await open(identifier);
}
watch(() => props.visible, value => { if (value) void work(async () => { await list(); if (detail.value) await refreshDetail(); }); });
onMounted(() => { if (props.visible) void work(() => list()); timer = setInterval(() => { void poll(); }, 3000); });
onUnmounted(() => { active = false; selection++; clearInterval(timer); batch.value.dispose(); release(); });
</script>
<template>
  <view class="homework-import panel">
    <text class="section-title">从群作业截图整理清单</text><text class="muted">先裁剪作业正文，再识别、核对日期与要求。识别内容会作为草稿保存。</text>
    <view v-if="message" role="alert" class="notice error">{{ message }}</view>
    <view v-if="pending" class="notice warning"><text>操作结果尚未确认，重试会沿用原编号与内容。</text><FamilyButton class="secondary" :disabled="working" @click="attempt">重试{{ pending.label }}</FamilyButton></view>
    <view class="import-upload"><view class="locator-grid"><view><text class="field-label">来源说明</text><FamilyInput v-model="source" aria-label="来源说明" maxlength="150" :disabled="uploadLocked" /></view><view><text class="field-label">目标日期（可选）</text><FamilyInput v-model="targetDate" type="date" aria-label="目标日期（可选）" :disabled="uploadLocked" /></view></view>
      <!-- #ifdef H5 -->
      <component :is="nativeInput" type="file" multiple accept="image/jpeg,image/png,image/webp" aria-label="选择作业清单图片" :disabled="uploadLocked" @change="files" />
      <!-- #endif -->
      <!-- #ifndef H5 -->
      <view class="button-row"><FamilyButton class="secondary" :disabled="uploadLocked" @click="pick('camera')">拍摄作业清单</FamilyButton><FamilyButton class="secondary" :disabled="uploadLocked" @click="pick('album')">选择清单相册图片</FamilyButton></view>
      <!-- #endif -->
      <text class="small muted">每批1–10张，每张不超过10MB。目标日期不会替代逐项日期确认。</text>
      <view class="import-pages"><view v-for="(page, index) in pages" :key="page.id" class="import-upload-page"><image :src="page.preview" class="upload-thumb" mode="aspectFit" /><text class="page-filename">{{ page.name }}</text><view class="button-row"><FamilyButton class="secondary compact" :aria-label="'编辑清单第' + (index+1) + '页'" :disabled="uploadLocked" @click="edit(page)">裁剪/旋转</FamilyButton><FamilyButton class="text-button compact" :aria-label="'清单第' + (index+1) + '页前移'" :disabled="uploadLocked || index === 0" @click="batch.move(index, -1); changed()">前移</FamilyButton><FamilyButton class="text-button compact" :aria-label="'移除清单第' + (index+1) + '页'" :disabled="uploadLocked" @click="batch.remove(index); changed()">移除</FamilyButton></view></view></view>
      <view v-if="editing && editPreview" class="import-crop"><image :src="editPreview.url" class="crop-preview" mode="widthFix" /><view class="locator-grid"><FamilyInput v-model="cropX" aria-label="清单裁剪左边距百分比" type="number" /><FamilyInput v-model="cropY" aria-label="清单裁剪上边距百分比" type="number" /><FamilyInput v-model="cropW" aria-label="清单裁剪宽度百分比" type="number" /><FamilyInput v-model="cropH" aria-label="清单裁剪高度百分比" type="number" /></view><view class="button-row"><FamilyButton class="secondary" :disabled="working" @click="rotate">清单顺时针旋转90°</FamilyButton><FamilyButton class="primary" :disabled="working" @click="crop">应用清单图片调整</FamilyButton><FamilyButton class="text-button" :disabled="working" @click="editing = null; editPreview = null">取消图片调整</FamilyButton></view></view>
      <FamilyButton class="primary" :disabled="working || !pages.length" @click="send">{{ batch.frozen ? '重试确认识别提交' : '开始识别清单' }}</FamilyButton>
    </view>
    <view class="section-head"><text class="section-title">导入记录</text><FamilyButton class="secondary compact" :disabled="working || !!pending" @click="work(() => list())">刷新导入记录</FamilyButton></view><text v-if="!history.length" class="muted">还没有导入记录。也可以直接手工维护清单。</text><FamilyButton v-for="record in history" :key="record.id" class="secondary import-history-row" data-testid="import-index" :disabled="working || !!pending" @click="open(record.id)">{{ record.source_label }} · {{ record.target_date || '日期待确认' }} · {{ importStates[record.status] }}</FamilyButton><FamilyButton v-if="cursor" class="secondary" :disabled="working" @click="work(() => list(true))">加载更多导入</FamilyButton>
    <view v-if="detail" class="import-detail" data-testid="import-detail">
      <view class="section-head"><text class="section-title">{{ detail.source_label }} · {{ importStates[detail.status] }}</text><FamilyButton class="secondary compact" :disabled="working || !!pending" @click="work(refreshDetail)">刷新当前导入</FamilyButton></view>
      <text v-if="processing" role="status" class="notice">正在逐图识别，可以离开后从导入记录继续。请等待本批识别结束再编辑。</text>
      <view v-if="reviewMerge" class="notice warning"><text>识别记录已更新，已保留本地修改并加入新条目。请重新核对。</text><FamilyButton class="secondary" :disabled="working" @click="reviewMerge = false">确认保留本地修改及新增条目</FamilyButton></view>
      <text v-if="detail.duplicate_images.length" class="notice warning">存在与该孩子历史导入相同的图片。请核对日期及重复条目；同图也可用于不同日期。</text>
      <view v-for="image in detail.images" :key="image.id" class="import-image-row"><FamilyButton class="secondary compact" :disabled="working" @click="selectedImage = image.id; selectedBox = null">查看来源图{{ image.position }}</FamilyButton><text>{{ image.status === 'succeeded' ? '已识别' : image.status === 'failed' ? '识别失败' : image.status === 'processing' ? '识别中' : image.status === 'queued' ? '等待识别' : '已结束' }}</text><text v-if="image.error_message" class="small muted">{{ image.error_message }}</text><FamilyButton v-if="image.status === 'failed'" class="secondary compact" :aria-label="'重试来源图' + image.position" :disabled="locked || (!!image.error_message?.includes('call_outcome_unknown') && !unknownCost)" @click="retry(image.id)">仅重试此图</FamilyButton></view>
      <ConsentCheck v-if="detail.images.some(image => image.error_message?.includes('call_outcome_unknown'))" v-model="unknownCost" label="我了解上次调用结果未知，可能已计费，仍要重试" :disabled="locked" />
      <view class="import-review-grid"><view class="import-image-column"><view v-if="previews.find(image => image.id === selectedImage)" class="source-image-frame"><image :src="previews.find(image => image.id === selectedImage)!.url" class="source-image" mode="widthFix" /><view v-if="selectedBox" class="source-bbox" :style="boxStyle" /></view><text class="small muted">原图位置框仅作辅助，请对照原文核对。</text></view>
        <view class="import-candidates"><text v-for="note in draft.notes" :key="note" class="notice warning">{{ note }}</text>
          <view v-for="(candidate,index) in draft.items" :key="candidate.id" class="import-candidate" data-testid="import-candidate">
            <view class="section-head"><text class="section-title">第{{ index+1 }}项</text><FamilyButton class="text-button compact" :disabled="working" @click="selectedImage = candidate.image_id; selectedBox = candidate.bbox">查看第{{ index+1 }}项原图</FamilyButton></view>
            <text v-if="detail.applied_ids[candidate.id]" class="badge">{{ detail.applied_ids[candidate.id] === 'excluded' ? '已排除' : '已入清单，请在每日清单修改' }}</text>
            <HomeworkFields :model-value="candidate" :prefix="'第' + (index+1) + '项'" :disabled="locked || !!detail.applied_ids[candidate.id]" @update:model-value="updateCandidate(candidate,$event)" />
            <text class="small muted">图片日期原文：{{ candidate.date_raw || '未提供' }}{{ candidate.date_candidates.length ? ' · 日期候选：' + candidate.date_candidates.join(' / ') : '' }}</text>
            <text v-for="warning in candidate.needs_review" :key="warning" class="small review-warning">{{ warning }}</text><text v-if="candidate.source_mark" class="small muted">来源标记 {{ candidate.source_mark }}，不表示孩子完成。</text>
            <view class="locator-grid"><view v-for="(label, position) in ['左边距','上边距','宽度','高度']" :key="label"><text class="field-label">位置框{{ label }}（%）</text><FamilyInput :model-value="String(Math.round((candidate.bbox ?? [0,0,1,1])[position]! * 100))" type="number" :aria-label="'第' + (index+1) + '项位置框' + label + '百分比'" :disabled="locked || !!detail.applied_ids[candidate.id]" @update:model-value="setBox(candidate, position, $event)" /></view></view><FamilyButton class="text-button compact" :disabled="locked || !!detail.applied_ids[candidate.id]" @click="candidate.bbox = null; selectedBox = null; dirty = true">清除辅助位置框</FamilyButton>
            <FamilyInput :model-value="candidate.assigned_date ?? ''" type="date" :aria-label="'第' + (index+1) + '项布置日期'" :disabled="locked || !!detail.applied_ids[candidate.id]" @update:model-value="dateCandidate(candidate,$event)" />
            <ConsentCheck v-model="candidate.date_confirmed" :label="'确认第' + (index+1) + '项布置日期'" :disabled="locked || !!detail.applied_ids[candidate.id] || !candidate.assigned_date" @update:model-value="dirty = true" />
            <view class="button-row"><FamilyButton v-for="choice in [{value:'pending',label:'暂不处理'},{value:'include',label:'收入清单'},{value:'exclude',label:'排除'},{value:'merge',label:'合并已有作业'}]" :key="choice.value" class="secondary compact" :aria-label="'第' + (index+1) + '项' + choice.label" :aria-pressed="candidate.decision === choice.value" :disabled="locked || !!detail.applied_ids[candidate.id]" @click="candidate.decision = choice.value as ImportCandidate['decision']; dirty = true">{{ choice.label }}</FamilyButton></view>
            <template v-if="candidate.decision === 'merge'"><FamilyButton class="secondary compact" :disabled="locked" @click="mergeChoices(candidate)">载入同日已有作业</FamilyButton><FamilyButton v-for="item in (dayItems[candidate.assigned_date ?? ''] ?? []).filter(item => item.subject === candidate.subject)" :key="item.id" class="secondary compact" :aria-pressed="candidate.merge_item_id === item.id" :disabled="locked || !!detail.applied_ids[candidate.id]" @click="candidate.merge_item_id = item.id; dirty = true">合并到：{{ item.title }}</FamilyButton><text class="small muted">只追加来源，不覆盖已有作业要求和完成状态。</text></template>
            <text v-for="suggestion in detail.suggestions.filter(row => row.candidate_id === candidate.id)" :key="suggestion.item_id" class="notice warning">{{ suggestion.kind === 'exact' ? '可能重复' : '范围或内容可能冲突' }}：{{ suggestion.assigned_date }} · {{ suggestion.title }}</text>
            <ConsentCheck v-model="candidate.conflict_confirmed" :label="'确认第' + (index+1) + '项即使重复或与无作业声明冲突仍需新增'" :disabled="locked || !!detail.applied_ids[candidate.id]" @update:model-value="dirty = true" />
          </view>
          <view v-for="(claim,index) in draft.no_homework" :key="claim.id" class="import-candidate import-none"><text class="section-title">{{ subjects[claim.subject] }}：来源声明无作业</text><text class="small muted">原图日期 {{ claim.date_raw || '未提供' }}</text><FamilyInput :model-value="claim.assigned_date ?? ''" type="date" :aria-label="'第' + (index+1) + '条无作业布置日期'" :disabled="locked || !!detail.applied_ids[claim.id]" @update:model-value="claim.assigned_date = $event || null; claim.confirmed = false; dirty = true" /><ConsentCheck v-model="claim.confirmed" :label="'确认第' + (index+1) + '条无作业日期及科目'" :disabled="locked || !!detail.applied_ids[claim.id] || !claim.assigned_date || claim.excluded" @update:model-value="dirty = true" /><ConsentCheck v-model="claim.excluded" :label="'排除第' + (index+1) + '条无作业声明'" :disabled="locked || !!detail.applied_ids[claim.id]" @update:model-value="dirty = true" /></view>
        </view>
      </view>
      <view class="button-row"><FamilyButton class="secondary" :disabled="locked || !dirty" @click="save">保存识别草稿</FamilyButton><FamilyButton class="primary" :disabled="locked || (!draft.items.length && !draft.no_homework.length)" @click="publish">发布已核对条目</FamilyButton></view><text class="small muted">未处理条目继续保留，部分发布不会把当天清单标为完整。</text>
      <ConsentCheck v-if="!closed" v-model="discardConfirm" label="确认结束本次导入，未发布条目不再计入待处理来源" :disabled="locked" /><FamilyButton v-if="!closed" class="text-button" :disabled="locked || !discardConfirm" @click="discard">结束导入</FamilyButton>
      <view class="import-runs"><text class="small muted">识别调用记录（费用未知不等于免费）</text><text v-for="run in detail.runs" :key="run.id" class="small muted">第{{ run.attempt }}次 · {{ run.state === 'succeeded' ? '识别成功' : run.state === 'failed' ? '识别失败' : '处理中' }} · {{ typeof run.usage?.total_tokens === 'number' ? '用量 ' + run.usage.total_tokens : '未返回总用量' }} · {{ run.cost_state === 'unknown' ? '费用未知' : '费用已记录' }}</text></view>
    </view>
  </view>
</template>
<style scoped>
.homework-import,.import-upload,.import-detail,.import-candidates,.import-candidate,.import-crop,.import-runs{display:flex;flex-direction:column;gap:16px;min-width:0}.homework-import{background:#f8fbf7}.locator-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.import-pages{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}.import-upload-page{min-width:0;padding:12px;background:white;border:1px solid #dde7df;border-radius:16px}.upload-thumb{width:100%;height:140px}.page-filename{display:block;overflow-wrap:anywhere}.crop-preview{width:100%;max-width:340px}.import-history-row{text-align:left;white-space:normal}.import-image-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.import-review-grid{display:grid;grid-template-columns:minmax(240px,.8fr) minmax(300px,1.2fr);gap:20px;align-items:start}.import-image-column{position:sticky;top:20px;min-width:0}.source-image-frame{position:relative;width:100%;line-height:0}.source-image{width:100%;display:block}.source-bbox{position:absolute;border:3px solid #e4a642;pointer-events:none;box-sizing:border-box}.import-candidate{border:1px solid #dce7df;border-radius:16px;padding:18px;background:white;overflow-wrap:anywhere}.review-warning{color:#996017}.import-runs{text-wrap:wrap;overflow-wrap:anywhere;border-top:1px solid #dce7df;padding-top:16px}@media(max-width:900px){.import-review-grid{grid-template-columns:minmax(0,1fr)}.import-image-column{position:static}.source-image-frame{max-width:540px}}@media(max-width:480px){.locator-grid{grid-template-columns:minmax(0,1fr)}.import-candidate{padding:12px}}
</style>
