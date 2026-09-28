import type { ReactNode } from 'react'
import { Loader2, Maximize2, VideoOff, WifiOff } from 'lucide-react'
import { EQUIPMENT, type Camera, type EquipmentType, type LiveCamera } from '@/data'
import { useApp } from '@/store/context'
import { streamSource, tileStatus, trackerDelay, useInView, usePageVisible, useVideo, type TileStatus } from '@/lib/video'
import { cn, inkOn } from '@/lib/utils'
import { LiveBoxes } from './TrackBoxes'

interface Props {
  camera: Camera
  live?: LiveCamera
  showBoxes: boolean
  /** Видео не подключать (открыт просмотр на всю вкладку — не держим два потока одной камеры) */
  paused?: boolean
  onOpen: () => void
  /** Кнопки под видео (у администратора — управление камерой) */
  actions?: ReactNode
}

/** Плитка видеостены: живое видео камеры. Нажатие на видео — просмотр на всю вкладку. Видео идёт, только пока плитка на экране. */
export function VideoTile({ camera, live, showBoxes, paused, onOpen, actions }: Props) {
  const { meta, byZone } = useApp()
  const { ref, inView } = useInView<HTMLElement>()
  const pageVisible = usePageVisible()
  const active = camera.enabled && inView && pageVisible && !paused
  // рамки идут в реальном времени — придерживаем видео на время разбора кадра, чтобы рамка попадала в машину
  const { videoRef, state } = useVideo(active ? streamSource(meta, camera.streamPath) : null, showBoxes ? trackerDelay(meta) : 0)
  const status = tileStatus(camera, live, state)
  const zone = byZone(camera.zoneId)

  return (
    <article ref={ref} className="bg-card rounded-xl border border-border shadow-[var(--shadow-card)] overflow-hidden transition-[box-shadow,border-color] duration-150 hover:border-border-strong hover:shadow-[var(--shadow-hover)] flex flex-col">
      <button
        type="button" onClick={onOpen} aria-label={`${camera.name}: ${STATUS_LABEL[status]}. Развернуть на всю вкладку`}
        className="group relative block w-full aspect-video bg-slate-950 overflow-hidden cursor-pointer"
      >
        <video ref={videoRef} autoPlay muted playsInline className={cn('absolute inset-0 w-full h-full object-contain', state !== 'playing' && 'opacity-0')} />
        {state === 'playing' && showBoxes && <LiveBoxes cameraId={camera.id} live={live} labels={false} />}
        <Placeholder status={status} error={live?.error ?? camera.lastError} />
        <span className="absolute left-2 top-2"><LiveBadge status={status} /></span>
        <span className="absolute right-2 top-2 w-9 h-9 rounded-lg bg-black/55 text-white flex items-center justify-center opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100 transition-opacity" aria-hidden>
          <Maximize2 className="w-4 h-4" />
        </span>
      </button>
      <div className="p-3.5 flex-1 flex flex-col">
        <div className="font-semibold leading-snug">{camera.name}</div>
        <div className="text-[14px] text-muted-foreground">{zone?.name}</div>
        <SeenNow live={live} status={status} />
        {actions && <div className="mt-auto pt-3 flex flex-wrap gap-2">{actions}</div>}
      </div>
    </article>
  )
}

const STATUS_LABEL: Record<TileStatus, string> = { live: 'в эфире', connecting: 'подключаемся', offline: 'нет сигнала', disabled: 'камера выключена' }

export function LiveBadge({ status }: { status: TileStatus }) {
  const tone = {
    live: 'bg-black/60 text-white',
    connecting: 'bg-black/60 text-white/85',
    offline: 'bg-danger-solid text-white',
    disabled: 'bg-black/60 text-white/85',
  }[status]
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] font-semibold leading-none', tone)}>
      {status === 'live' && <span className="w-2 h-2 rounded-full bg-red-500" aria-hidden />}
      {status === 'live' ? 'В эфире' : status === 'connecting' ? 'Подключаемся' : status === 'offline' ? 'Нет сигнала' : 'Выключена'}
    </span>
  )
}

function Placeholder({ status, error }: { status: TileStatus; error: string | null | undefined }) {
  if (status === 'live') return null
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-white/80 text-[14px] px-4 text-center">
      {status === 'connecting' && <Loader2 className="w-7 h-7 animate-spin" aria-hidden />}
      {status === 'offline' && <WifiOff className="w-7 h-7" aria-hidden />}
      {status === 'disabled' && <VideoOff className="w-7 h-7" aria-hidden />}
      {status === 'offline' && error && <span className="line-clamp-2 max-w-xs">{error}</span>}
    </div>
  )
}

/** Что видит анализ прямо сейчас: «Экскаватор ×1» */
export function SeenNow({ live, status, dark }: { live?: LiveCamera; status: TileStatus; dark?: boolean }) {
  if (status === 'disabled' || status === 'offline') return null
  const muted = dark ? 'text-white/70' : 'text-muted-foreground'
  if (!live?.analyzedAt) return <div className={cn('mt-2 text-[13px]', muted)}>анализ ещё не начался</div>
  if (live.analyzed === false) return <div className={cn('mt-2 text-[13px]', muted)}>техника не распознана</div>
  const counts = Object.entries(live.counts) as [EquipmentType, number][]
  if (!counts.length) return <div className={cn('mt-2 text-[13px]', muted)}>техники не видно</div>
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {counts.map(([type, n]) => (
        <span key={type} className="text-[13px] font-medium rounded-sm px-2 py-0.5" style={{ background: EQUIPMENT[type].color, color: inkOn(EQUIPMENT[type].color) }}>
          {EQUIPMENT[type].name} ×{n}
        </span>
      ))}
    </div>
  )
}
