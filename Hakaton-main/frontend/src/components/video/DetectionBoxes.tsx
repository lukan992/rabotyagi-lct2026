import { m } from 'framer-motion'
import { EQUIPMENT, type Detection } from '@/data'
import { cn, inkOn } from '@/lib/utils'

/**
 * Рамки найденной техники поверх видео. Координаты — проценты от кадра 16:9: сервер перед анализом дополняет кадр
 * полями до 16:9, а видео в плеере вписывается так же — поэтому рамки ложатся точно.
 * Рамки отстают от видео не больше чем на 2 секунды — так часто кадр уходит на анализ; между разборами
 * рамка плавно переезжает (при «уменьшить движение» — сразу встаёт на место).
 */
export function DetectionBoxes({ detections, labels = true }: { detections: Detection[]; labels?: boolean }) {
  return (
    <div className="absolute inset-0 pointer-events-none" aria-hidden>
      {detections.map((d, i) => {
        const info = EQUIPMENT[d.type]
        return (
          <m.div
            key={`${d.type}-${i}`} className="absolute"
            initial={false}
            animate={{ left: `${d.box.x}%`, top: `${d.box.y}%`, width: `${d.box.w}%`, height: `${d.box.h}%` }}
            transition={{ duration: 0.5, ease: 'easeOut' }}
            style={{ border: `2px solid ${info.color}`, boxShadow: '0 0 0 1px rgba(0,0,0,.45)' }}
          >
            {labels && (
              <span
                className={cn('absolute -left-[2px] text-[11px] leading-none font-semibold px-1.5 py-[3px] whitespace-nowrap font-mono', d.box.y >= 8 ? '-top-[19px]' : 'top-0')}
                style={{ background: info.color, color: inkOn(info.color) }}
              >
                {info.name} {Math.round(d.confidence * 100)}%
              </span>
            )}
          </m.div>
        )
      })}
    </div>
  )
}
