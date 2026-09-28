import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/** Кнопка-фильтр: «Открытые», «Все объекты»… Нажатая — залита акцентом */
export function Chip({ active, onClick, children, small }: { active: boolean; onClick: () => void; children: ReactNode; small?: boolean }) {
  return (
    <button
      type="button" onClick={onClick} aria-pressed={active}
      className={cn(
        'rounded-sm font-semibold border cursor-pointer transition-colors',
        small ? 'min-h-[44px] px-3 text-[14px]' : 'min-h-[44px] px-4',
        active ? 'bg-primary text-on-primary border-primary' : 'bg-card border-border hover:border-primary/60',
      )}
    >
      {children}
    </button>
  )
}
