import { useCallback, useRef, useState } from 'react'
import { AnimatePresence, m } from 'framer-motion'
import { MoreHorizontal, type LucideIcon } from 'lucide-react'
import { useDismiss } from '@/lib/useDismiss'
import { useMenuKeys } from '@/lib/useMenuKeys'
import { cn } from '@/lib/utils'

export interface MenuAction {
  label: string
  Icon: LucideIcon
  onSelect: () => void
  danger?: boolean
  disabled?: boolean
  /** Почему пункт недоступен — показываем подсказкой */
  hint?: string
}

/** Кнопка «⋯» со списком редких действий: в строке остаётся одна главная кнопка, а не четыре */
export function ActionMenu({ label, actions }: { label: string; actions: MenuAction[] }) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const close = useCallback(() => setOpen(false), [])
  useDismiss(box, open, close)
  useMenuKeys(menu, trigger, open, close)

  return (
    <div className="relative" ref={box}>
      <button
        ref={trigger} type="button" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open} aria-label={label} title={label}
        className={cn('w-11 h-11 rounded-lg flex items-center justify-center cursor-pointer transition-colors border', open ? 'bg-muted border-border-strong' : 'border-transparent text-muted-foreground hover:bg-muted hover:text-foreground')}
      >
        <MoreHorizontal className="w-5 h-5" />
      </button>
      <AnimatePresence>
        {open && (
          <m.div
            ref={menu} role="menu" aria-label={label}
            initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.12 }}
            className="absolute right-0 mt-2 w-60 bg-card border border-border rounded-xl shadow-[var(--shadow-pop)] p-1.5 z-40"
          >
            {actions.map((a) => (
              <button
                key={a.label} type="button" role="menuitem" disabled={a.disabled} title={a.disabled ? a.hint : undefined}
                onClick={() => { setOpen(false); a.onSelect() }}
                className={cn(
                  'w-full flex items-center gap-3 min-h-[44px] px-2.5 rounded-lg text-left cursor-pointer transition-colors',
                  a.danger ? 'text-danger hover:bg-danger-bg focus:bg-danger-bg' : 'hover:bg-muted focus:bg-muted',
                  'disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent',
                )}
              >
                <a.Icon className="w-5 h-5 shrink-0" /> {a.label}
                {a.disabled && a.hint && <span className="sr-only"> — {a.hint}</span>}
              </button>
            ))}
          </m.div>
        )}
      </AnimatePresence>
    </div>
  )
}
