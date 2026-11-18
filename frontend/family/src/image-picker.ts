import { operationId } from './api';
import type { HomeworkImage } from './homework';
import { imageType } from './image-editor';

// Reuse the bundled WeChat signatures without exposing a second global API.
declare const wx: Pick<Uni, 'getFileSystemManager' | 'editImage'>;

function readLocal(path: string): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    // #ifdef MP-WEIXIN
    wx.getFileSystemManager().readFile({ filePath: path,
      success: result => result.data instanceof ArrayBuffer ? resolve(result.data) : reject(new Error('图片读取失败，请重新选择。')), fail: reject });
    // #endif
  });
}
export async function localImage(path: string): Promise<HomeworkImage> {
  const bytes = await readLocal(path);
  return { id: operationId(), name: '作业图片', size: bytes.byteLength, type: imageType(bytes), preview: path,
    read: () => readLocal(path), release() {} };
}
export async function chooseLocalImages(source: 'camera' | 'album', count: number): Promise<HomeworkImage[]> {
  const paths = await new Promise<string[]>((resolve, reject) => uni.chooseImage({ count, sourceType: [source],
    success: result => resolve(result.tempFilePaths as string[]), fail: reject }));
  return Promise.all(paths.map(localImage));
}
export async function editLocalImage(image: HomeworkImage): Promise<HomeworkImage> {
  const path = await new Promise<string>((resolve, reject) => {
    // #ifdef MP-WEIXIN
    wx.editImage({ src: image.preview, success: result => resolve(result.tempFilePath), fail: reject });
    // #endif
  });
  return localImage(path);
}
export function privatePreview(bytes: ArrayBuffer): { url: string; release: () => void } {
  // #ifdef H5
  const url = URL.createObjectURL(new Blob([bytes], { type: 'image/jpeg' }));
  return { url, release: () => URL.revokeObjectURL(url) };
  // #endif
  // #ifndef H5
  return { url: 'data:image/jpeg;base64,' + uni.arrayBufferToBase64(bytes), release() {} };
  // #endif
}
