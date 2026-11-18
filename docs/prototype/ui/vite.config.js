import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { resolve } from 'node:path';
export default defineConfig({
  base: './', plugins: [vue()],
  build: {
    outDir: 'dist', emptyOutDir: true, assetsDir: 'assets/ui',
    rollupOptions: { input: Object.fromEntries(['index','child','parent','platform'].map(n => [n,resolve(import.meta.dirname,`${n}.html`)])) }
  }
});
