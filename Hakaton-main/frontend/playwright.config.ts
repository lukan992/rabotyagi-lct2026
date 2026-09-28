import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { defineConfig, devices } from '@playwright/test'

/**
 * Смоук-тесты по ролям (e2e/): настоящий бэкенд на временной базе с демо-данными — без видео, Keycloak и своей модели
 * (демо-анализатор даёт одинаковые ответы), имитация сервисов аналитики и интерфейс в dev-режиме. Свои порты 8102,
 * 8302 и 5191: запущенные для разработки 8100, 8300 и 5180 не мешают и не задеваются.
 *
 *   npx playwright install chromium   # один раз: браузер для тестов
 *   npm run test:e2e                  # PW_CHANNEL=chrome — в установленном Google Chrome, если браузер не скачан
 */
const API_PORT = 8102
const ANALYTICS_PORT = 8302
const WEB_PORT = 5191
const data = mkdtempSync(join(tmpdir(), 'stroykontrol-e2e-'))

export default defineConfig({
  testDir: './e2e',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  workers: 1,  // одна база на все тесты: по очереди
  reporter: [['list']],
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
    trace: 'retain-on-failure',
    channel: process.env.PW_CHANNEL || undefined,
  },
  projects: [
    { name: 'компьютер', use: { ...devices['Desktop Chrome'], channel: process.env.PW_CHANNEL || undefined } },
    // прораб в основном с телефона — его экраны проверяем и на телефоне
    { name: 'телефон', grep: /@телефон/, use: { ...devices['Pixel 7'], channel: process.env.PW_CHANNEL || undefined } },
  ],
  webServer: [
    {
      command: `uv run --directory ../backend uvicorn app.mock_analytics:app --port ${ANALYTICS_PORT}`,
      url: `http://127.0.0.1:${ANALYTICS_PORT}/health`,
      env: { MOCK_ANALYTICS_TOKEN: '', MOCK_VLM_DELAY_S: '0' },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `uv run --directory ../backend uvicorn app.main:app --port ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/api/meta`,
      env: {
        SK_DATA_DIR: data,
        SK_DATABASE_URL: `sqlite+aiosqlite:///${join(data, 'e2e.db')}`,
        SK_VIDEO_ENABLED: 'false',
        SK_CHECK_INTERVAL_S: '0',
        SK_ANALYSIS_PROVIDER: 'mock',
        SK_DEMO_MODE: 'true',
        SK_SEED_ON_START: 'true',
        SK_DEMO_PASSWORD: 'e2e-password',
        SK_KEYCLOAK_ISSUER: '',
        DETERMINISTIC_SERVICE_URL: `http://127.0.0.1:${ANALYTICS_PORT}/deterministic`,
        VLM_LLM_SERVICE_URL: `http://127.0.0.1:${ANALYTICS_PORT}/vlm_llm`,
        ANALYTICS_SERVICE_TOKEN: '',
        SK_TRACKER_URL: '',
        SK_CORS_ORIGINS: `http://localhost:${WEB_PORT}`,
      },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npm run dev -- --port ${WEB_PORT} --strictPort`,
      url: `http://localhost:${WEB_PORT}`,
      env: { VITE_BACKEND_URL: `http://localhost:${API_PORT}` },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
