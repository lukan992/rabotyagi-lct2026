import { ChevronRight } from 'lucide-react'
import type { Alert } from '@/data'
import { useApp } from '@/store/context'
import { ago } from '@/lib/utils'
import { SEVERITY, STATUS } from '@/lib/labels'
import { Badge } from './ui/Badge'
import { CameraFrame } from './CameraFrame'

interface Props {
  alert: Alert
  onOpen: (a: Alert) => void
  showSite?: boolean
  compact?: boolean
}

/** Строка отклонения. Вся строка — одна большая кнопка. */
export function AlertCard({ alert, onOpen, showSite, compact }: Props) {
  const sev = SEVERITY[alert.severity]
  const st = STATUS[alert.status]
  const { bySite, byZone, cameraOf } = useApp()
  const snap = alert.evidenceSnapshots[alert.evidenceSnapshots.length - 1]
  const cam = snap ? cameraOf(snap) : undefined
  const offline = alert.kind === 'camera_offline'
  const highlight = alert.equipment && (alert.kind === 'unexpected' || alert.kind === 'idle') ? [alert.equipment] : undefined
  return (
    <button
      type="button"
      onClick={() => onOpen(alert)}
      className="w-full text-left bg-card rounded-xl border border-border shadow-[var(--shadow-card)] overflow-hidden cursor-pointer transition-[box-shadow,border-color] duration-150 hover:border-border-strong hover:shadow-[var(--shadow-hover)] flex flex-col sm:flex-row sm:items-center"
    >
      {!compact && cam && (
        <>
          {/* телефон: фото полосой сверху; десктоп: миниатюра слева */}
          <span className="block sm:hidden w-full">
            <CameraFrame camera={cam} snapshot={snap} thumb showBoxes={false} offline={offline} className="aspect-[5/2] rounded-none" />
          </span>
          <span className="hidden sm:block w-44 shrink-0 p-3 pr-0">
            <CameraFrame camera={cam} snapshot={snap} showLabels={false} thumb highlight={highlight} offline={offline} className="rounded-md" />
          </span>
        </>
      )}
      <span className="flex-1 min-w-0 px-4 sm:px-5 py-4 flex items-center gap-3">
        <span className="flex-1 min-w-0">
          <span className="flex flex-wrap items-center gap-2 mb-1">
            <Badge tone={sev.tone}>{sev.label}</Badge>
            <Badge tone={st.tone}>{st.label}</Badge>
            <span className="text-[13px] text-muted-foreground ml-auto hidden sm:inline tabular">№ {alert.code}</span>
          </span>
          <span className="block text-[17px] font-semibold leading-snug mt-1.5">{alert.title}</span>
          {!compact && <span className="block text-muted-foreground text-[15px] mt-0.5 line-clamp-2">{alert.summary}</span>}
          <span className="block mt-1 text-[14px] text-muted-foreground">
            {showSite && <><span className="text-foreground font-semibold">{bySite(alert.siteId)?.name}</span> · </>}
            {byZone(alert.zoneId)?.name} · {ago(alert.startedAt)}
          </span>
        </span>
        <ChevronRight className="w-5 h-5 text-muted-foreground shrink-0" />
      </span>
    </button>
  )
}
