import { useRef, type ReactNode } from 'react'
import { AnimatePresence, m, useReducedMotion } from 'framer-motion'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useDialog } from './useDialog'

interface ModalProps {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  wide?: boolean
}

/**
 * Диалог: на телефоне выезжает снизу, на десктопе — по центру.
 * Фокус переходит в окно, по Tab не уходит из него и возвращается туда, откуда окно открыли.
 */
export function Modal({ open, onClose, title, children, wide }: ModalProps) {
  const reduce = useReducedMotion()
  const panel = useRef<HTMLDivElement>(null)
  // закрываем по подложке, только если и нажали, и отпустили на ней: иначе выделение текста в поле,
  // закончившееся за краем окна, закрывало диалог вместе с введённым
  const downOnBackdrop = useRef(false)
  useDialog(open, panel, onClose)

  return (
    <AnimatePresence>
      {open && (
        <m.div
          className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-slate-900/45 p-0 sm:p-6"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}
          onPointerDown={(e) => { downOnBackdrop.current = e.target === e.currentTarget }}
          onClick={(e) => { if (downOnBackdrop.current && e.target === e.currentTarget) onClose(); downOnBackdrop.current = false }}
        >
          <m.div
            ref={panel} tabIndex={-1}
            role="dialog" aria-modal="true" aria-label={title}
            className={cn('bg-card w-full max-h-[94dvh] overflow-y-auto rounded-t-2xl sm:rounded-2xl shadow-[var(--shadow-pop)] focus-visible:outline-none', wide ? 'sm:max-w-4xl' : 'sm:max-w-2xl')}
            initial={reduce ? false : { y: 16, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={reduce ? undefined : { y: 8, opacity: 0 }}
            transition={{ duration: 0.18, ease: 'easeOut' }}
            onClick={(e) => e.stopPropagation()}  // клики в окне не должны доходить до карточек и строк, внутри которых оно открыто
          >
            <div className="sticky top-0 bg-card border-b border-border pl-5 pr-2 sm:pl-6 h-14 flex items-center justify-between gap-3 z-10 rounded-t-2xl">
              <h2 className="text-[18px] font-semibold leading-tight truncate">{title}</h2>
              <button onClick={onClose} aria-label="Закрыть" className="shrink-0 w-11 h-11 rounded-lg flex items-center justify-center text-muted-foreground hover:bg-muted hover:text-foreground cursor-pointer transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-5 sm:p-6">{children}</div>
          </m.div>
        </m.div>
      )}
    </AnimatePresence>
  )
}
