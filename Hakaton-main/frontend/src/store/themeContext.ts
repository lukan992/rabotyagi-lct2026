import { createContext, useContext } from 'react'

/** light / dark — выбор пользователя; system — как настроено на устройстве (по умолчанию) */
export type ThemeMode = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

const KEY = 'sk-theme'
const query = () => window.matchMedia('(prefers-color-scheme: dark)')

export function readMode(): ThemeMode {
  try {
    const value = localStorage.getItem(KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

export function saveMode(mode: ThemeMode) {
  try {
    if (mode === 'system') localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, mode)
  } catch { /* приватный режим — тема сохранится до перезагрузки */ }
}

export function resolveTheme(mode: ThemeMode): ResolvedTheme {
  return mode === 'system' ? (query().matches ? 'dark' : 'light') : mode
}

export function watchSystemTheme(onChange: (theme: ResolvedTheme) => void): () => void {
  const mq = query()
  const handler = () => onChange(mq.matches ? 'dark' : 'light')
  mq.addEventListener('change', handler)
  return () => mq.removeEventListener('change', handler)
}

/** Применить тему к документу: атрибут для CSS-токенов и цвет строки браузера на телефоне */
export function applyTheme(theme: ResolvedTheme) {
  document.documentElement.dataset.theme = theme
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#171D26' : '#FFFFFF')
}

interface ThemeState {
  mode: ThemeMode
  theme: ResolvedTheme
  setMode: (mode: ThemeMode) => void
}

export const ThemeCtx = createContext<ThemeState | null>(null)


export function useTheme() {
  const v = useContext(ThemeCtx)
  if (!v) throw new Error('useTheme вне ThemeProvider')
  return v
}
