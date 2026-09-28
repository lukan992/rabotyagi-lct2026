import { ChevronLeft, ChevronRight, X } from 'lucide-react'
import type { Snapshot } from '@/data'
import { useApp } from '@/store/context'
import { cn, fmtWhen } from '@/lib/utils'
import { ViewerPanel } from './ui/ViewerPanel'
import { CameraFrame } from './CameraFrame'

interface Props {
  snapshots: Snapshot[]
  /** Какой кадр открыт; null — просмотр закрыт */
  index: number | null
  onIndex: (index: number) => void
  onClose: () => void
  /** Подсветить рамки этих типов техники */
  highlight?: string[]
  offline?: boolean
}

/** Кадры-доказательства на всю вкладку: стрелки ← → листают кадры, Escape закрывает */
export function FrameViewer({ snapshots, index, onIndex, onClose, highlight, offline }: Props) {
  const { cameraOf, bySite, byZone } = useApp()
  const snapshot = index !== null ? snapshots[index] : undefined
  const camera = snapshot ? cameraOf(snapshot) : undefined
  const many = snapshots.length > 1
  const step = (d: number) => index !== null && many && onIndex((index + d + snapshots.length) % snapshots.length)
  const control = 'w-11 h-11 rounded-lg flex items-center justify-center cursor-pointer transition-colors hover:bg-white/15'
  return (
    <ViewerPanel
      open={!!snapshot} label={camera ? `Кадр с камеры «${camera.name}» — просмотр на всю вкладку` : ''} onClose={onClose}
      onKey={(e) => {
        if (e.key === 'ArrowRight') step(1)
        if (e.key === 'ArrowLeft') step(-1)
      }}
    >
      {snapshot && camera && (
        <>
          <header className="h-16 shrink-0 px-3 sm:px-5 flex items-center gap-3 bg-black/85 border-b border-white/10">
            <div className="min-w-0 flex-1">
              <div className="font-semibold truncate text-[17px]">{camera.name}</div>
              <div className="text-[13px] text-white/70 truncate">
                {[bySite(camera.siteId)?.name, byZone(camera.zoneId)?.name, `кадр ${fmtWhen(snapshot.takenAt)}`].filter(Boolean).join(' · ')}
                {many && ` · ${(index ?? 0) + 1} из ${snapshots.length}`}
              </div>
            </div>
            <button type="button" onClick={onClose} aria-label="Закрыть (Esc)" className={control}><X className="w-6 h-6" /></button>
          </header>

          <div className="relative flex-1 min-h-0 flex items-center justify-center [container-type:size]">
            <div className="w-[min(100cqw,calc(100cqh*16/9))]">
              <CameraFrame camera={camera} snapshot={snapshot} highlight={highlight} offline={offline} className="rounded-none" />
            </div>
            {many && (
              <>
                <button type="button" onClick={() => step(-1)} aria-label="Предыдущий кадр (←)" className={cn(control, 'absolute left-2 sm:left-4 top-1/2 -translate-y-1/2 w-12 h-12 bg-black/50')}>
                  <ChevronLeft className="w-7 h-7" />
                </button>
                <button type="button" onClick={() => step(1)} aria-label="Следующий кадр (→)" className={cn(control, 'absolute right-2 sm:right-4 top-1/2 -translate-y-1/2 w-12 h-12 bg-black/50')}>
                  <ChevronRight className="w-7 h-7" />
                </button>
              </>
            )}
          </div>
        </>
      )}
    </ViewerPanel>
  )
}
