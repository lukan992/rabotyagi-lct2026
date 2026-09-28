import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ThemeCtx, applyTheme, readMode, resolveTheme, saveMode, watchSystemTheme, type ResolvedTheme, type ThemeMode } from './themeContext'

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(readMode)
  const [theme, setTheme] = useState<ResolvedTheme>(() => resolveTheme(readMode()))

  useEffect(() => { applyTheme(theme) }, [theme])

  // в режиме «как на устройстве» следим за переключением темы в системе
  useEffect(() => (mode === 'system' ? watchSystemTheme(setTheme) : undefined), [mode])

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next)
    setTheme(resolveTheme(next))
    saveMode(next)
  }, [])

  const value = useMemo(() => ({ mode, theme, setMode }), [mode, theme, setMode])
  return <ThemeCtx.Provider value={value}>{children}</ThemeCtx.Provider>
}
