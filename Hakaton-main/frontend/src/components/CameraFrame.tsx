import { EQUIPMENT } from '@/data'
import type { Camera, Snapshot } from '@/data'
import { mediaUrl } from '@/api'
import { fmtTimeSec, fmtDateShort, fmtWhen } from '@/lib/utils'
import { cn, inkOn } from '@/lib/utils'

interface Props {
  camera: Camera
  snapshot?: Snapshot
  /** Подсветить рамки этих типов техники */
  highlight?: string[]
  showLabels?: boolean
  /** Показывать ли рамки распознавания (на странице проверки — только после анализа) */
  showBoxes?: boolean
  offline?: boolean
  /** Миниатюра: без подписей камеры и времени */
  thumb?: boolean
  className?: string
}

/**
 * Кадр с камеры (доказательство в отклонении) с рамками распознанной техники.
 * Когда кадр недоступен, вместо изображения показывается нейтральная заглушка.
 */
export function CameraFrame({ camera, snapshot, highlight, showLabels = true, showBoxes = true, offline, thumb, className }: Props) {
  const dets = snapshot?.detections ?? []
  const hasFrame = Boolean(snapshot?.imageUrl)
  const camNo = `CAM-${camera.id.slice(-4).toUpperCase()}`
  // только span: кадр стоит внутри кнопок (строка отклонения, «развернуть кадр»), а в <button> div недопустим
  return (
    <span className={cn('relative block w-full aspect-video rounded-lg overflow-hidden bg-slate-900 select-none', className)}>
      {snapshot?.imageUrl ? (
        <img src={mediaUrl(snapshot.imageUrl)} alt="" loading="lazy" className="absolute inset-0 w-full h-full object-cover" />
      ) : (
        <span role="img" aria-label="Кадр с камеры недоступен" className="absolute inset-0 flex items-center justify-center bg-slate-800 text-slate-300">
          <span className={thumb ? 'text-xs' : 'text-sm'}>Нет кадра</span>
        </span>
      )}

      {offline && (
        <span className="absolute inset-0 bg-slate-950/85 flex flex-col items-center justify-center text-white">
          <span className={thumb ? 'text-sm font-semibold' : 'text-2xl font-semibold'}>Нет сигнала</span>
          {!thumb && <span className="text-sm opacity-80">последний кадр {snapshot ? fmtWhen(snapshot.takenAt) : '—'}</span>}
        </span>
      )}

      {/* Кадр получен, но разобрать его не удалось */}
      {!offline && hasFrame && !thumb && snapshot && !snapshot.analyzed && (
        <span className="absolute inset-x-0 bottom-0 bg-slate-950/75 text-white text-[13px] px-3 py-2 pr-40">
          Кадр получен, техника не распознана: {snapshot.note ?? 'сервис анализа недоступен'}
        </span>
      )}

      {/* Рамки распознавания */}
      {!offline && hasFrame && showBoxes && dets.map((d) => {
        const info = EQUIPMENT[d.type]
        const hl = !highlight || highlight.includes(d.type)
        return (
          <span
            key={d.id}
            className="absolute"
            style={{
              left: `${d.box.x}%`, top: `${d.box.y}%`, width: `${d.box.w}%`, height: `${d.box.h}%`,
              border: `2px solid ${info.color}`, opacity: hl ? 1 : 0.4,
              boxShadow: hl ? '0 0 0 1px rgba(0,0,0,.45), inset 0 0 0 1px rgba(0,0,0,.25)' : 'none',
            }}
          >
            {showLabels && !thumb && (
              <span
                className={cn('absolute -left-[2px] text-[11px] leading-none font-semibold px-1.5 py-[3px] whitespace-nowrap font-mono', d.box.y >= 25 ? '-top-[19px]' : d.box.x < 35 ? 'bottom-0' : 'top-0')}
                style={{ background: info.color, color: inkOn(info.color) }}
              >
                {info.name} {d.confidence.toFixed(2)}
              </span>
            )}
          </span>
        )
      })}

      {/* Экранные надписи камеры */}
      {!thumb && (
        <>
          <span className="absolute left-2 top-2 text-white text-[11px] sm:text-[12px] font-mono leading-tight [text-shadow:0_1px_2px_rgba(0,0,0,.9)]">
            <span className="block font-semibold">{camNo}</span>
            <span className="block opacity-90">{camera.name.split('—')[1]?.trim() ?? camera.name}</span>
          </span>
          {!offline && hasFrame && (
            <span className="absolute right-2 top-2 flex items-center gap-1.5 text-white text-[11px] sm:text-[12px] font-mono [text-shadow:0_1px_2px_rgba(0,0,0,.9)]">
              <span className="w-2 h-2 rounded-full bg-red-500" /> REC
            </span>
          )}
          {hasFrame && snapshot && (
            <span className="absolute right-2 bottom-2 text-white text-[11px] sm:text-[12px] font-mono [text-shadow:0_1px_2px_rgba(0,0,0,.9)]">
              {fmtDateShort(snapshot.takenAt)} {fmtTimeSec(snapshot.takenAt)}
            </span>
          )}
        </>
      )}
    </span>
  )
}

