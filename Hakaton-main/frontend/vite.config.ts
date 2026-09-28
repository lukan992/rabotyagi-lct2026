/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: Number(process.env.PORT) || 5180,
    // бэкенд: /api — данные (и WebSocket /api/tracks — рамки техники в реальном времени), /media — кадры с камер
    proxy: Object.fromEntries(['/api', '/media'].map((path) => [path, { target: process.env.VITE_BACKEND_URL || 'http://localhost:8100', changeOrigin: true, ws: path === '/api' }])),
  },
  // модульные тесты (vitest) — только в src; e2e/ — смоук-тесты Playwright, у них свой запуск: npm run test:e2e
  test: { include: ['src/**/*.test.ts'] },
  resolve: {
    // fileURLToPath, а не .pathname: иначе путь с пробелом или кириллицей (~/Проекты/…) приходит в %-кодировке и импорты ломаются
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
})
