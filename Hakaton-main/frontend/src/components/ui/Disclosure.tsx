import { useId, useState, type ReactNode } from 'react'
import { AnimatePresence, m } from 'framer-motion'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'

/** Раскрывающийся раздел: заголовок-кнопка, содержимое спрятано до нажатия — подробности не шумят на экране */
export function Disclosure({ title, children, className }: { title: ReactNode; children: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  return (
    <section className={cn('rounded-xl border border-border', className)}>
      <h3>
        <button
          type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}
          className="w-full min-h-[48px] px-4 flex items-center justify-between gap-3 font-semibold text-left rounded-xl cursor-pointer transition-colors hover:bg-muted/50"
        >
          <span>{title}</span>
          <ChevronDown className={cn('w-5 h-5 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')} aria-hidden />
        </button>
      </h3>
      <AnimatePresence initial={false}>
        {open && (
          <m.div
            id={id} className="overflow-hidden"
            initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.2 }}
          >
            <div className="px-4 pb-4">{children}</div>
          </m.div>
        )}
      </AnimatePresence>
    </section>
  )
}
