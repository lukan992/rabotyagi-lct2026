import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

type Tone = 'ok' | 'warn' | 'danger' | 'neutral'
const hintCls: Record<Tone, string> = { ok: 'text-ok', warn: 'text-warn', danger: 'text-danger', neutral: 'text-muted-foreground' }

/**
 * Показатель: подпись, крупное значение, пояснение. Цветом выделяется только пояснение.
 * С onClick становится фильтром: нажал «Нужно вмешаться: 2» — видишь эти два объекта.
 */
export function StatTile({ label, value, hint, tone = 'neutral', onClick, active }: {
  label: string; value: ReactNode; hint?: string; tone?: Tone; onClick?: () => void; active?: boolean
}) {
  const body = (
    <>
      <div className="text-[14px] text-muted-foreground">{label}</div>
      <div className="text-[26px] font-semibold leading-tight mt-1 tabular tracking-tight">{value}</div>
      {hint && <div className={cn('text-[14px] mt-1', hintCls[tone])}>{hint}</div>}
    </>
  )
  const cls = 'bg-card border rounded-xl shadow-[var(--shadow-card)] px-5 py-4'
  if (!onClick) return <div className={cn(cls, 'border-border')}>{body}</div>
  return (
    <button
      type="button" onClick={onClick} aria-pressed={active}
      className={cn(cls, 'text-left w-full cursor-pointer transition-colors', active ? 'border-primary ring-2 ring-primary/25' : 'border-border hover:border-primary/60')}
    >
      {body}
    </button>
  )
}
