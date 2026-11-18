import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

const proxy = { '/api/v1/admin': { target: 'http://127.0.0.1:8000' } };
export default defineConfig({
  base: process.env.VITE_PLATFORM_BASE || '/',
  plugins: [vue()],
  server: { host: '127.0.0.1', port: 4175, strictPort: true, proxy },
  preview: { host: '127.0.0.1', port: 4175, strictPort: true, proxy },
});
