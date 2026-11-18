import { ApiError, operationId } from './api.ts';
import type { FamilyApi, SubmissionBody, SubmissionReceipt } from './api.ts';

export interface HomeworkImage {
  id: string; name: string; size: number; type: string; preview: string;
  read: () => Promise<ArrayBuffer>; release: () => void;
}
export interface UploadPage extends HomeworkImage { mediaId?: string; phase: 'waiting' | 'reading' | 'uploading' | 'checking' | 'ready' }
export const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
export const MAX_HOMEWORK_PAGES = 10;
export function pickerCount(existing: number) {
  // WeChat chooseImage rejects a count above 9. A later pick can fill the batch.
  return Math.min(9, MAX_HOMEWORK_PAGES - existing);
}
export function validateImages(images: HomeworkImage[], existing: number) {
  if (!images.length || images.length + existing > MAX_HOMEWORK_PAGES) throw new Error('每次作业请选择 1–10 张图片。');
  if (images.some(image => !['image/jpeg', 'image/png', 'image/webp'].includes(image.type))) throw new Error('请选择 JPG、PNG 或 WebP 图片。');
  if (images.some(image => !image.size || image.size > MAX_IMAGE_BYTES)) throw new Error('每张图片需大于 0 且不超过 10 MB。');
}

// Once submission may have reached the server, retain the exact key and body for every retry.
export class HomeworkBatch {
  pages: UploadPage[] = [];
  frozen: SubmissionBody | null = null;
  key = operationId();
  running = false;
  active = true;
  private api: FamilyApi; private student: string; private valid: () => boolean;
  constructor(api: FamilyApi, student: string, valid: () => boolean = () => true) { this.api = api; this.student = student; this.valid = valid; }
  check() { if (!this.active || !this.valid()) throw new ApiError(0, 'stale'); }
  add(images: HomeworkImage[]) {
    this.check();
    if (this.running || this.frozen) throw new Error('请先完成当前作业的提交重试。');
    validateImages(images, this.pages.length);
    this.pages.push(...images.map(image => ({ ...image, phase: 'waiting' as const })));
  }
  move(index: number, offset: number) {
    if (this.running || this.frozen) return;
    const target = index + offset;
    if (target < 0 || target >= this.pages.length) return;
    [this.pages[index], this.pages[target]] = [this.pages[target]!, this.pages[index]!];
  }
  remove(index: number) { if (!this.running && !this.frozen) this.pages.splice(index, 1).forEach(page => page.release()); }
  replace(index: number, image: HomeworkImage) {
    this.check();
    if (this.running || this.frozen) throw new Error('请先完成当前作业的提交重试。');
    validateImages([image], this.pages.length - 1);
    const old = this.pages[index];
    if (!old) throw new Error('图片已移除，请重新选择。');
    this.pages[index] = { ...image, phase: 'waiting' };
    old.release();
  }
  dispose() { this.active = false; this.pages.forEach(page => page.release()); this.pages = []; }
  async send(completed: boolean, changed: () => void = () => {}, subject: SubmissionBody['subject'] = 'auto', activityType: NonNullable<SubmissionBody['activity_type']> = 'auto', referenceText = ''): Promise<SubmissionReceipt> {
    this.check();
    if (this.running) throw new Error('作业正在提交，请稍候。');
    if (!completed || !this.pages.length) throw new Error('请先选图，并确认已认真尝试完成作业。');
    const generation = this.api.generation;
    const check = () => { this.check(); if (generation !== this.api.generation) throw new ApiError(0, 'stale'); };
    this.running = true; changed();
    try {
      if (!this.frozen) {
        for (const page of this.pages) {
          check();
          if (page.mediaId) continue;
          page.phase = 'reading'; changed();
          const bytes = await page.read(); check();
          if (bytes.byteLength !== page.size) throw new Error('图片已变化，请重新选择。');
          const intent = await this.api.uploadIntent(this.student, page.type, bytes.byteLength); check();
          page.phase = 'uploading'; changed();
          await this.api.upload(intent.media_id, bytes, page.type); check();
          page.phase = 'checking'; changed();
          await this.api.completeMedia(intent.media_id); check();
          page.mediaId = intent.media_id; page.phase = 'ready'; changed();
        }
        this.frozen = { student_id: this.student, subject, activity_type: activityType, reference_text: referenceText, completed_attempt: true,
          media_ids: this.pages.map(page => page.mediaId!), client_request_id: operationId() };
      }
      check(); changed();
      const receipt = await this.api.submit(this.frozen, this.key); check();
      return receipt;
    } finally { this.running = false; changed(); }
  }
}
