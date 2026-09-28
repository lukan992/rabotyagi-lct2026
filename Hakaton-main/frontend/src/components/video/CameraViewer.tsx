import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, ScanSearch, X } from 'lucide-react'
import type { Camera, LiveCamera } from '@/data'
import { useApp } from '@/store/context'
import { streamSource, tileStatus, trackerDelay, usePageVisible, useVideo } from '@/lib/video'
import { ago, cn } from '@/lib/utils'
import { ViewerPanel } from '@/components/ui/ViewerPanel'
import { LiveBoxes } from './TrackBoxes'
import { LiveBadge, SeenNow } from './VideoTile'

interface Props {
  cameras: Camera[]
  /** Какая камера открыта; null — просмотр закрыт */
  index: number | null
  onIndex: (index: number) => void
  onClose: () => void
  live: Map<string, LiveCamera>
  showBoxes: boolean
  onShowBoxes: (on: boolean) => void
}

/** Камера на всю вкладку: стрелки ← → листают камеры, Escape закрывает */
export function CameraViewer({ cameras, index, onIndex, onClose, live, showBoxes, onShowBoxes }: Props) {
  const camera = index !== null ? cameras[index] : undefined
  const step = (d: number) => index !== null && cameras.length > 1 && onIndex((index + d + cameras.length) % cameras.length)
  return (
    <ViewerPanel
      open={!!camera} label={camera ? `${camera.name} — просмотр на всю вкладку` : ''} onClose={onClose}
      onKey={(e) => {
        if (e.key === 'ArrowRight') step(1)
        if (e.key === 'ArrowLeft') step(-1)
      }}
    >
      {camera && (
        <Viewer
          key={camera.id} camera={camera} live={live.get(camera.id)} showBoxes={showBoxes} onShowBoxes={onShowBoxes}
          position={cameras.length > 1 ? `${(index ?? 0) + 1} из ${cameras.length}` : null}
          onPrev={cameras.length > 1 ? () => step(-1) : undefined} onNext={cameras.length > 1 ? () => step(1) : undefined}
          onClose={onClose}
        />
      )}
    </ViewerPanel>
  )
}

function Viewer({ camera, live, showBoxes, onShowBoxes, position, onPrev, onNext, onClose }: {
  camera: Camera; live?: LiveCamera; showBoxes: boolean; onShowBoxes: (on: boolean) => void; position: string | null
  onPrev?: () => void; onNext?: () => void; onClose: () => void
}) {
  const { meta, bySite, byZone } = useApp()
  const pageVisible = usePageVisible()
  const { videoRef, state } = useVideo(camera.enabled && pageVisible ? streamSource(meta, camera.streamPath) : null, showBoxes ? trackerDelay(meta) : 0)
  const status = tileStatus(camera, live, state)
  const [, tick] = useState(0) // «разобрано 2 с назад» обновляем раз в секунду
  useEffect(() => {
    const timer = window.setInterval(() => tick((n) => n + 1), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const control = 'w-11 h-11 rounded-lg flex items-center justify-center cursor-pointer transition-colors hover:bg-white/15'
  return (
    <>
      <header className="h-16 shrink-0 px-3 sm:px-5 flex items-center gap-3 bg-black/85 border-b border-white/10">
        <div className="min-w-0 flex-1">
          <div className="font-semibold truncate text-[17px]">{camera.name}</div>
          <div className="text-[13px] text-white/70 truncate">{bySite(camera.siteId)?.name} · {byZone(camera.zoneId)?.name}{position && ` · ${position}`}</div>
        </div>
        <LiveBadge status={status} />
        <button
          type="button" onClick={() => onShowBoxes(!showBoxes)} aria-pressed={showBoxes} title="Рамки найденной техники"
          className={cn(control, 'w-auto px-3 gap-2 text-[14px] font-medium', showBoxes && 'bg-white/15')}
        >
          <ScanSearch className="w-5 h-5" /> <span className="hidden sm:inline">Рамки</span>
        </button>
        <button type="button" onClick={onClose} aria-label="Закрыть (Esc)" className={control}><X className="w-6 h-6" /></button>
      </header>

      <div className="relative flex-1 min-h-0 flex items-center justify-center [container-type:size]">
        <div className="relative aspect-video w-[min(100cqw,calc(100cqh*16/9))]">
          <video ref={videoRef} autoPlay muted playsInline className={cn('absolute inset-0 w-full h-full object-contain', status !== 'live' && 'opacity-0')} />
          {status === 'live' && showBoxes && <LiveBoxes cameraId={camera.id} live={live} />}
          {status !== 'live' && (
            <div className="absolute inset-0 flex items-center justify-center text-white/80 text-[16px] px-6 text-center">
              {status === 'connecting' ? 'Подключаемся к видео…' : status === 'disabled' ? 'Камера выключена' : live?.error ?? camera.lastError ?? 'Нет сигнала'}
            </div>
          )}
        </div>
        {onPrev && (
          <button type="button" onClick={onPrev} aria-label="Предыдущая камера (←)" className={cn(control, 'absolute left-2 sm:left-4 top-1/2 -translate-y-1/2 w-12 h-12 bg-black/50')}>
            <ChevronLeft className="w-7 h-7" />
          </button>
        )}
        {onNext && (
          <button type="button" onClick={onNext} aria-label="Следующая камера (→)" className={cn(control, 'absolute right-2 sm:right-4 top-1/2 -translate-y-1/2 w-12 h-12 bg-black/50')}>
            <ChevronRight className="w-7 h-7" />
          </button>
        )}
      </div>

      <footer className="shrink-0 px-4 sm:px-5 py-3 bg-black/85 border-t border-white/10 flex flex-wrap items-center gap-x-4 gap-y-2 text-[14px]">
        <span className="text-white/70">Анализ видео:</span>
        <SeenNow live={live} status={status} dark />
        {live?.analyzedAt && <span className="text-white/60 sm:ml-auto">кадр разобран {ago(live.analyzedAt)}</span>}
      </footer>
    </>
  )
}
