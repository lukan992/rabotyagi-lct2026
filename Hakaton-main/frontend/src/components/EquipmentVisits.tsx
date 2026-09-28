import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Loader2 } from 'lucide-react'
import { api } from '@/api'
import { EQUIPMENT, type EquipmentType, type EquipmentVisitStatus } from '@/data'
import { useApp } from '@/store/context'
import { fmtWhen } from '@/lib/utils'
import { Badge } from './ui/Badge'
import { Button } from './ui/Button'

const PAGE_SIZE = 25
const STATUS: Record<EquipmentVisitStatus, { label: string; tone: 'ok' | 'info' | 'warn' }> = {
  active: { label: 'Наблюдается', tone: 'ok' },
  completed: { label: 'Наблюдение завершено', tone: 'info' },
  lost: { label: 'Наблюдение потеряно', tone: 'warn' },
}

/** Постоянный журнал сглаженных наблюдаемых интервалов, не оценка парка или производительности. */
export function EquipmentVisits({ siteId }: { siteId: string }) {
  const { cameras } = useApp()
  const [cameraId, setCameraId] = useState('')
  const [equipmentClass, setEquipmentClass] = useState('')
  const [status, setStatus] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [offset, setOffset] = useState(0)
  const filters = {
    cameraId: cameraId || undefined,
    equipmentClass: equipmentClass || undefined,
    status: status || undefined,
    from: from ? new Date(from).toISOString() : undefined,
    to: to ? new Date(to).toISOString() : undefined,
    limit: PAGE_SIZE,
    offset,
  }
  const { data, isPending, isError, isFetching } = useQuery({
    queryKey: ['equipment-visits', siteId, filters],
    queryFn: () => api.equipmentVisits(siteId, filters),
    refetchInterval: 10_000,
  })
  const update = (change: () => void) => {
    change()
    setOffset(0)
  }
  const siteCameras = cameras.filter((camera) => camera.siteId === siteId)
  const lastPage = data ? Math.max(0, Math.ceil(data.total / PAGE_SIZE) - 1) : 0
  const page = Math.floor(offset / PAGE_SIZE)

  return (
    <section className="mt-5 bg-card rounded-xl border border-border shadow-[var(--shadow-card)] px-4 sm:px-5 py-4" aria-busy={isFetching}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="text-[18px] font-semibold">Журнал наблюдений техники</h2>
          <p className="mt-0.5 text-[14px] text-muted-foreground">Камеры не объединяются в уникальный парк: ReID отсутствует. Активное наблюдение не означает фактическую производительность.</p>
        </div>
        {isFetching && !isPending && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" aria-label="Обновляем журнал" />}
      </div>
      <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
        <label className="text-[14px]">Камера
          <select value={cameraId} onChange={(event) => update(() => setCameraId(event.target.value))} className="mt-1 block min-h-10 w-full rounded-lg border border-border bg-card px-2">
            <option value="">Все камеры</option>
            {siteCameras.map((camera) => <option key={camera.id} value={camera.id}>{camera.name}</option>)}
          </select>
        </label>
        <label className="text-[14px]">Класс техники
          <select value={equipmentClass} onChange={(event) => update(() => setEquipmentClass(event.target.value))} className="mt-1 block min-h-10 w-full rounded-lg border border-border bg-card px-2">
            <option value="">Все классы</option>
            {Object.entries(EQUIPMENT).map(([id, item]) => <option key={id} value={id}>{item.name}</option>)}
          </select>
        </label>
        <label className="text-[14px]">Статус
          <select value={status} onChange={(event) => update(() => setStatus(event.target.value))} className="mt-1 block min-h-10 w-full rounded-lg border border-border bg-card px-2">
            <option value="">Все статусы</option>
            {(Object.entries(STATUS) as [EquipmentVisitStatus, typeof STATUS.active][]).map(([id, item]) => <option key={id} value={id}>{item.label}</option>)}
          </select>
        </label>
        <label className="text-[14px]">С
          <input type="datetime-local" value={from} onChange={(event) => update(() => setFrom(event.target.value))} className="mt-1 block min-h-10 w-full rounded-lg border border-border bg-card px-2" />
        </label>
        <label className="text-[14px]">По
          <input type="datetime-local" value={to} min={from || undefined} onChange={(event) => update(() => setTo(event.target.value))} className="mt-1 block min-h-10 w-full rounded-lg border border-border bg-card px-2" />
        </label>
      </div>
      {isPending ? <div className="mt-4 h-32 animate-pulse rounded-lg bg-muted" /> : isError || !data ? <p role="alert" className="mt-4 rounded-lg bg-danger-bg text-danger-fg px-3 py-2">Не удалось загрузить журнал наблюдений.</p> : <>
        {data.items.length === 0 ? <p className="mt-4 text-muted-foreground">За выбранный период наблюдений нет.</p> : <div className="mt-4 overflow-x-auto"><table className="min-w-full text-left text-[14px]"><thead className="border-b border-border text-muted-foreground"><tr><th className="px-2 py-2 font-medium">Техника</th><th className="px-2 py-2 font-medium">Камера</th><th className="px-2 py-2 font-medium">Начало</th><th className="px-2 py-2 font-medium">Последнее</th><th className="px-2 py-2 font-medium">Длительность</th><th className="px-2 py-2 font-medium">Статус</th></tr></thead><tbody>{data.items.map((visit) => {
          const state = STATUS[visit.status]
          const camera = siteCameras.find((item) => item.id === visit.cameraId)
          return <tr key={visit.id} className="border-b border-border last:border-0"><td className="px-2 py-3 font-medium">{EQUIPMENT[visit.equipmentClass as EquipmentType]?.name ?? visit.equipmentClass}</td><td className="px-2 py-3">{camera?.name ?? 'Камера удалена'}</td><td className="px-2 py-3 whitespace-nowrap">{fmtWhen(visit.firstSeenAt)}</td><td className="px-2 py-3 whitespace-nowrap">{fmtWhen(visit.lastSeenAt)}</td><td className="px-2 py-3 whitespace-nowrap">{duration(visit.durationSeconds)}</td><td className="px-2 py-3"><Badge tone={state.tone}>{state.label}</Badge>{visit.status === 'lost' && visit.closeReason && <p className="mt-1 max-w-48 text-[13px] text-muted-foreground">Причина: {visit.closeReason}</p>}</td></tr>
        })}</tbody></table></div>}
        <div className="mt-4 flex items-center justify-between gap-3"><span className="text-[14px] text-muted-foreground">{data.total} {data.total === 1 ? 'наблюдение' : 'наблюдений'}</span><div className="flex items-center gap-2"><Button size="sm" variant="outline" disabled={page === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}><ChevronLeft className="w-4 h-4" /> Назад</Button><span className="text-[14px]">Страница {page + 1} из {lastPage + 1}</span><Button size="sm" variant="outline" disabled={page >= lastPage} onClick={() => setOffset(offset + PAGE_SIZE)}>Вперёд <ChevronRight className="w-4 h-4" /></Button></div></div>
      </>}
    </section>
  )
}

function duration(seconds: number) {
  const total = Math.max(0, Math.round(seconds))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const rest = total % 60
  if (hours) return `${hours} ч ${minutes} мин`
  if (minutes) return `${minutes} мин ${rest} с`
  return `${rest} с`
}
