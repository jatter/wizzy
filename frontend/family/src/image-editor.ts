import { operationId } from './api.ts';
import { MAX_IMAGE_BYTES, validateImages } from './homework.ts';
import type { HomeworkImage } from './homework.ts';

export interface Crop { x: number; y: number; width: number; height: number }
export function imageType(bytes: ArrayBuffer) {
  const b = new Uint8Array(bytes);
  if (b[0] === 255 && b[1] === 216 && b[2] === 255) return 'image/jpeg';
  if (b[0] === 137 && b[1] === 80 && b[2] === 78 && b[3] === 71) return 'image/png';
  if (String.fromCharCode(...b.slice(0, 4)) === 'RIFF' && String.fromCharCode(...b.slice(8, 12)) === 'WEBP') return 'image/webp';
  throw new Error('图片格式无法识别，请选择 JPG、PNG 或 WebP。');
}
export function browserImage(file: Blob, name: string): HomeworkImage {
  const image = { id: operationId(), name, size: file.size, type: file.type,
    preview: '', read: () => file.arrayBuffer(), release: () => {} };
  validateImages([image], 0);
  const url = URL.createObjectURL(file);
  return { ...image, preview: url, release: () => URL.revokeObjectURL(url) };
}
async function rotatedCanvas(image: HomeworkImage, turns: number): Promise<HTMLCanvasElement> {
  const source = new Image();
  source.src = image.preview;
  await source.decode();
  const width = source.naturalWidth, height = source.naturalHeight;
  if (!width || !height || width * height > 20_000_000) throw new Error('图片需不超过 2000 万像素，请缩小后重试。');
  const quarter = ((turns % 4) + 4) % 4;
  const rotated = document.createElement('canvas');
  rotated.width = quarter % 2 ? height : width; rotated.height = quarter % 2 ? width : height;
  const context = rotated.getContext('2d')!;
  context.translate(rotated.width / 2, rotated.height / 2); context.rotate(quarter * Math.PI / 2);
  context.drawImage(source, -width / 2, -height / 2);
  return rotated;
}
export async function previewBrowserImage(image: HomeworkImage, turns: number) {
  const rotated = await rotatedCanvas(image, turns);
  const preview = document.createElement('canvas'), scale = Math.min(1, 720 / Math.max(rotated.width, rotated.height));
  preview.width = Math.max(1, Math.round(rotated.width * scale));
  preview.height = Math.max(1, Math.round(rotated.height * scale));
  preview.getContext('2d')!.drawImage(rotated, 0, 0, preview.width, preview.height);
  return { url: preview.toDataURL('image/jpeg', .9), width: rotated.width, height: rotated.height };
}
// Crop coordinates refer to the rotated image. Preview and export share that coordinate space.
export async function editBrowserImage(image: HomeworkImage, turns: number, crop: Crop): Promise<HomeworkImage> {
  const values = [crop.x, crop.y, crop.width, crop.height];
  if (values.some(value => !Number.isFinite(value)) || crop.x < 0 || crop.y < 0 || crop.width <= 0 || crop.height <= 0 ||
    crop.x + crop.width > 100 || crop.y + crop.height > 100) throw new Error('裁剪范围需在图片内，宽高需大于 0。');
  const rotated = await rotatedCanvas(image, turns);
  const output = document.createElement('canvas');
  output.width = Math.max(1, Math.round(rotated.width * crop.width / 100));
  output.height = Math.max(1, Math.round(rotated.height * crop.height / 100));
  output.getContext('2d')!.drawImage(rotated, rotated.width * crop.x / 100, rotated.height * crop.y / 100,
    rotated.width * crop.width / 100, rotated.height * crop.height / 100, 0, 0, output.width, output.height);
  const blob = await new Promise<Blob>((resolve, reject) => output.toBlob(value => value ? resolve(value) : reject(new Error('图片处理失败，请重试。')), 'image/jpeg', .94));
  if (blob.size > MAX_IMAGE_BYTES) throw new Error('处理后的图片超过 10 MB，请缩小后重试。');
  return browserImage(blob, image.name.replace(/\.[^.]+$/, '') + '-已调整.jpg');
}
