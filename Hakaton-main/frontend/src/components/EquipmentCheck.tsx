import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, XCircle, AlertTriangle, Eye, Truck } from 'lucide-react'
import { api } from '@/api'
import { EQUIPMENT, type EquipmentType } from '@/data'
import { fmtTime } from '@/lib/utils'
import { VehicleIcon } from './VehicleIcon'
import { cn } from '@/lib/utils'
import { InfoTip } from './ui/InfoTip'

/**
 * «Что должно быть по плану» и «что видим на камерах» — сердце методики.
 * Считает сервер (GET /sites/{id}/equipment-check): нужная техника учитывается только в рабочих зонах,
 * техника на въезде показывается отдельно как подъезжающая.
 */
export function EquipmentCheck({ siteId }: { siteId: string }) {
  const { data, isPending, isError } = useQuery({
    queryKey: ['equipment-check', siteId], queryFn: () => api.equipmentCheck(siteId), refetchInterval: 20_000,
  })
  if (isPending) return <div className="bg-card rounded-xl border border-border h-40 animate-pulse" aria-busy />
  if (isError || !data) return <p className="text-muted-foreground">Не удалось загрузить сверку техники.</p>
  const estimatedTotal = data.estimatedSiteObserved
    ? Object.values(data.estimatedSiteObserved).reduce((total, count) => total + count, 0)
    : null
  if (!data.stageName) return <div className="text-muted-foreground">
    На сегодня в календарном плане нет этапа работ.
    {estimatedTotal !== null && <div className="text-warn-fg">Техники на объекте с учётом пересечений: {estimatedTotal} (неточно)</div>}
  </div>
  // лишняя техника уже показана своей строкой — в «подъезжает» её не дублируем, иначе автокран был бы и «лишним», и «подъезжающим»
  const extraTypes = new Set(data.extra.map((e) => e.type))
  const arriving = (Object.entries(data.arriving) as [EquipmentType, number][]).filter(([type]) => !extraTypes.has(type))

  return (
    <div className="bg-card rounded-xl border border-border overflow-hidden shadow-[var(--shadow-card)]">
      <div className="px-4 sm:px-5 py-3.5 border-b border-border">
        <div className="flex items-center gap-2">
          <div className="font-semibold text-[17px]">Техника на этапе «{data.stageName}»</div>
          <InfoTip label="Как считается техника">
            Сколько техники нужно по правилу этапа и сколько её видят камеры рабочей зоны. Техника на въезде и складе
            считается подъезжающей: её видно, но в норму рабочей зоны она пока не засчитывается. Оценка по пересечениям
            сопоставляет рамки на разных камерах приблизительно и не влияет на автоматические предупреждения.
          </InfoTip>
        </div>
        {data.checkedAt && <div className="text-muted-foreground text-[14px]">данные на {fmtTime(data.checkedAt)}</div>}
        {estimatedTotal !== null && <div className="text-warn-fg text-[15px] mt-1">Техники на объекте с учётом пересечений: {estimatedTotal} (неточно)</div>}
      </div>
      {!data.working ? (
        <p className="px-4 sm:px-5 py-3 bg-muted text-foreground text-[15px]">
          Сейчас нерабочее время ({data.workHours}) — технику не сверяем: ночью и в выходные её на площадке и не должно быть.
        </p>
      ) : !data.coverage && (
        <p className="px-4 sm:px-5 py-3 bg-warn-bg text-warn-fg text-[15px]">
          Нет свежих кадров с камер рабочей зоны — сверить технику сейчас нельзя. Проверьте камеры.
        </p>
      )}
      <ul className="divide-y divide-border">
        {data.rows.map((r) => (
          <li key={r.type} className="px-4 sm:px-5 py-3 flex items-center gap-4">
            <VehicleIcon type={r.type} className="w-14 h-9 shrink-0" fill={EQUIPMENT[r.type].color} />
            <div className="flex-1 min-w-0">
              <div className="font-semibold">{EQUIPMENT[r.type].name}</div>
              <div className="text-muted-foreground text-[14px]">
                {r.state === 'not_detected' ? `нужно ${r.need} — модель такую технику пока не распознаёт, проверьте на месте` : `нужно ${r.need}, видим ${r.have}`}
              </div>
              {r.state !== 'not_detected' && data.estimatedObserved && (
                <div className="text-warn-fg text-[14px]">
                  С учётом пересечений: {data.estimatedObserved[r.type] ?? 0} (неточно)
                </div>
              )}
            </div>
            <StateChip state={r.state === 'not_detected' ? 'not_detected' : data.coverage && data.working ? r.state : 'unknown'} />
          </li>
        ))}
        {data.extra.map((e) => (
          <li key={e.type} className="px-4 sm:px-5 py-3 flex items-center gap-4 bg-warn-bg/40">
            <VehicleIcon type={e.type} className="w-14 h-9 shrink-0" fill={EQUIPMENT[e.type].color} />
            <div className="flex-1 min-w-0">
              <div className="font-semibold">{EQUIPMENT[e.type].name} — не по плану</div>
              <div className="text-muted-foreground text-[14px]">
                {(data.arriving[e.type] ?? 0) >= e.have ? 'на въезде или складе' : 'в рабочей зоне'}
                {e.why && <> · {e.why}</>}
              </div>
            </div>
            <StateChip state="extra" />
          </li>
        ))}
      </ul>
      {arriving.length > 0 && (
        <div className="px-4 sm:px-5 py-3 border-t border-border text-[15px] flex flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground">
          <Truck className="w-5 h-5 shrink-0" /> На въезде и складе:
          {arriving.map(([type, n]) => <span key={type} className="text-foreground font-medium">{EQUIPMENT[type].name} ×{n}</span>)}
        </div>
      )}
    </div>
  )
}

function StateChip({ state }: { state: 'ok' | 'missing' | 'low' | 'extra' | 'unknown' | 'not_detected' }) {
  const m = {
    ok: { label: 'Есть', cls: 'bg-ok-bg text-ok-fg', Icon: CheckCircle2 },
    missing: { label: 'Нет', cls: 'bg-danger-bg text-danger-fg', Icon: XCircle },
    low: { label: 'Мало', cls: 'bg-warn-bg text-warn-fg', Icon: AlertTriangle },
    extra: { label: 'Лишняя', cls: 'bg-warn-bg text-warn-fg', Icon: AlertTriangle },
    unknown: { label: 'Не видно', cls: 'bg-muted text-muted-foreground', Icon: AlertTriangle },
    not_detected: { label: 'На месте', cls: 'bg-muted text-muted-foreground', Icon: Eye },
  }[state]
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-sm px-3 py-1.5 font-semibold text-[15px] shrink-0', m.cls)}>
      <m.Icon className="w-5 h-5" /> {m.label}
    </span>
  )
}
