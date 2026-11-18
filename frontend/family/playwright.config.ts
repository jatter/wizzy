import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests', testMatch: '**/*.spec.ts', fullyParallel: true, workers: 3,
  use: {
    baseURL: 'http://127.0.0.1:4177',
    launchOptions: process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {},
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'node node_modules/@dcloudio/vite-plugin-uni/bin/uni.js --host 127.0.0.1 --port 4177 --strictPort',
    url: 'http://127.0.0.1:4177', reuseExistingServer: false,
  },
});
