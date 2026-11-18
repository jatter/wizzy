import { defineConfig } from 'vite';
import { createRequire } from 'node:module';

// The uni plugin publishes a CommonJS object with its factory on `default`.
const { default: uni } = createRequire(import.meta.url)('@dcloudio/vite-plugin-uni');

const familyBase = process.env.VITE_FAMILY_BASE || '/';

export default defineConfig({
  base: familyBase,
  plugins: [uni()],
  server: { host: '127.0.0.1', port: 4176, strictPort: true,
    proxy: { '/api/v1': { target: 'http://127.0.0.1:8000', changeOrigin: false } } },
});
