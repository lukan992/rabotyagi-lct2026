import { useCallback, useRef, useState } from 'react'
import { AnimatePresence, m } from 'framer-motion'
import { Moon, MonitorSmartphone, Sun } from 'lucide-react'
import { useTheme, type ThemeMode } from '@/store/themeContext'
import { useDismiss } from '@/lib/useDismiss'
import { useMenuKeys } from '@/lib/useMenuKeys'
import { cn } from '@/lib/utils'

const OPTIONS: { id: ThemeMode; label: string; Icon: typeof Sun }[] = [
  { id: 'light', label: 'Светлая', Icon: Sun },
  { id: 'dark', label: 'Тёмная', Icon: Moon },
  { id: 'system', label: 'Как на устройстве', Icon: MonitorSmartphone },
]

/** Три кнопки в ряд: светлая / тёмная / как на устройстве */
export function ThemePicker({ className }: { className?: string }) {
  const { mode, setMode } = useTheme()
  return (
    <div role="radiogroup" aria-label="Оформление" className={cn('inline-flex rounded-lg border border-border-strong bg-card p-0.5', className)}>
      {OPTIONS.map((o) => (
        <button
          key={o.id} type="button" role="radio" aria-checked={mode === o.id} onClick={() => setMode(o.id)}
          className={cn(
            'inline-flex items-center gap-1.5 min-h-[44px] px-3 rounded-md text-[14px] font-medium cursor-pointer transition-colors',
            mode === o.id ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          <o.Icon className="w-4 h-4" /> {o.label}
        </button>
      ))}
    </div>
  )
}

/** Кнопка со значком текущей темы; открывает те же три варианта списком */
export function ThemeMenuButton({ className }: { className?: string }) {
  const { mode, theme, setMode } = useTheme()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const close = useCallback(() => setOpen(false), [])
  useDismiss(ref, open, close)
  useMenuKeys(menu, trigger, open, close)
  const Current = theme === 'dark' ? Moon : Sun
  const label = OPTIONS.find((o) => o.id === mode)?.label ?? ''

  return (
    <div className={cn('relative', className)} ref={ref}>
      <button
        ref={trigger} type="button" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open}
        aria-label={`Оформление: ${label.toLowerCase()}`} title="Оформление"
        className={cn('w-11 h-11 rounded-lg flex items-center justify-center cursor-pointer transition-colors', open ? 'bg-muted text-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground')}
      >
        <Current className="w-5 h-5" />
      </button>
      <AnimatePresence>
        {open && (
          <m.div
            ref={menu} role="menu" aria-label="Оформление"
            initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.12 }}
            className="absolute right-0 mt-2 w-56 bg-card border border-border rounded-xl shadow-[var(--shadow-pop)] p-1.5 z-40"
          >
            <div className="px-2.5 pt-1.5 pb-1 text-[13px] text-muted-foreground">Оформление</div>
            {OPTIONS.map((o) => (
              <button
                key={o.id} type="button" role="menuitemradio" aria-checked={mode === o.id}
                onClick={() => { setMode(o.id); setOpen(false) }}
                className={cn('w-full flex items-center gap-3 min-h-[44px] px-2.5 rounded-lg text-left cursor-pointer transition-colors', mode === o.id ? 'bg-muted font-semibold' : 'hover:bg-muted')}
              >
                <o.Icon className="w-5 h-5 text-muted-foreground" /> {o.label}
              </button>
            ))}
          </m.div>
        )}
      </AnimatePresence>
    </div>
  )
}
