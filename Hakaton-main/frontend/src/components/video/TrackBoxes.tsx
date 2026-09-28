import { AnimatePresence, m } from 'framer-motion'
import { EQUIPMENT, type LiveCamera } from '@/data'
import { useApp } from '@/store/context'
import { trackPace, useTracks, type TrackObject } from '@/lib/useTracks'
import { cn, inkOn } from '@/lib/utils'
import { DetectionBoxes } from './DetectionBoxes'

/**
 * Рамки поверх живого видео. В обычном режиме это рамки в реальном времени от
 * сервера или снимок анализа кадров. В push-режиме с внешним трекером поверх
 * видео допустимы только свежие сообщения WebSocket: история снимков остаётся
 * для анализа, но не подменяет живые координаты.
 */
export function LiveBoxes({ cameraId, live, labels = true }: { cameraId: string; live?: LiveCamera; labels?: boolean }) {
  const { meta } = useApp()
  const tracks = useTracks(meta?.tracker?.enabled ? cameraId : null)
  const requiresLiveTracks = meta?.analysisProvider === 'push' && meta.tracker.enabled
  // Внешний push-трекер — единственный источник оверлея: отсутствие/устаревание WS скрывает рамки.
  if (requiresLiveTracks) return tracks ? <TrackBoxes tracks={tracks} labels={labels} pace={trackPace(cameraId)} /> : null
  // В не-push режиме сохранён снимок анализа, когда live-треков пока нет.
  if (tracks) return <TrackBoxes tracks={tracks} labels={labels} pace={trackPace(cameraId)} />
  return live ? <DetectionBoxes detections={live.detections} labels={labels} /> : null
}

/**
 * Каждая рамка привязана к своему track_id: между сообщениями она линейно доезжает до нового положения
 * за время, равное промежутку между сообщениями (pace, мс), — поэтому едет ровно, а не прыгает.
 * При «уменьшить движение» (MotionConfig в App) рамка сразу встаёт на место.
 */
function TrackBoxes({ tracks, labels, pace }: { tracks: TrackObject[]; labels: boolean; pace: number }) {
  const glide = Math.min(Math.max(pace, 80), 500) / 1000
  return (
    <div className="absolute inset-0 pointer-events-none" aria-hidden>
      <AnimatePresence initial={false}>
        {tracks.map((t) => {
          const info = EQUIPMENT[t.type]
          return (
            <m.div
              key={t.trackId} className="absolute"
              initial={{ opacity: 0, left: `${t.box.x}%`, top: `${t.box.y}%`, width: `${t.box.w}%`, height: `${t.box.h}%` }}
              animate={{ opacity: 1, left: `${t.box.x}%`, top: `${t.box.y}%`, width: `${t.box.w}%`, height: `${t.box.h}%` }}
              exit={{ opacity: 0, transition: { duration: 0.2 } }}
              transition={{ duration: glide, ease: 'linear', opacity: { duration: 0.15 } }}
              style={{ border: `2px solid ${info.color}`, boxShadow: '0 0 0 1px rgba(0,0,0,.45)' }}
            >
              {labels && (
                <span
                  className={cn('absolute -left-[2px] text-[11px] leading-none font-semibold px-1.5 py-[3px] whitespace-nowrap font-mono', t.box.y >= 8 ? '-top-[19px]' : 'top-0')}
                  style={{ background: info.color, color: inkOn(info.color) }}
                >
                  {info.name} {Math.round(t.confidence * 100)}%
                </span>
              )}
            </m.div>
          )
        })}
      </AnimatePresence>
    </div>
  )
}
