import { CheckCircle2, AlertTriangle, OctagonAlert } from 'lucide-react'
import type { SiteStatus } from '@/data'
import { cn } from '@/lib/utils'

const map: Record<SiteStatus, { label: string; cls: string; Icon: typeof CheckCircle2 }> = {
  ok: { label: 'Нет открытых замечаний', cls: 'bg-ok-bg text-ok-fg', Icon: CheckCircle2 },
  warning: { label: 'Есть замечания', cls: 'bg-warn-bg text-warn-fg', Icon: AlertTriangle },
  critical: { label: 'Нужно вмешаться', cls: 'bg-danger-bg text-danger-fg', Icon: OctagonAlert },
}

/** «Светофор» состояния объекта: значок + слова, понятно без объяснений */
export function StatusPill({ status, big }: { status: SiteStatus; big?: boolean }) {
  const { label, cls, Icon } = map[status]
  return (
    <span className={cn('inline-flex items-center gap-2 font-semibold', cls, big ? 'rounded-lg px-4 py-2.5 text-[17px]' : 'rounded-md px-2.5 py-1 text-[14px]')}>
      <Icon className={big ? 'w-5 h-5' : 'w-4 h-4'} />
      {label}
    </span>
  )
}
