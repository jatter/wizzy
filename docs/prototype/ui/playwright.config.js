import { defineConfig } from '@playwright/test';
const built = process.env.PROTOTYPE_BUILT === '1';
export default defineConfig({
  testDir: './tests', fullyParallel: true, workers: 3,
  use: { baseURL: 'http://127.0.0.1:4174', launchOptions: process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {}, screenshot:'only-on-failure' },
  webServer: {command:built ? 'python3 -m http.server 4174 --bind 127.0.0.1 --directory ..' : 'npm run dev -- --port 4174',url:'http://127.0.0.1:4174',reuseExistingServer:!built && !process.env.CI},
});
