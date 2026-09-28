import { useQuery } from '@tanstack/react-query'
import { api } from '@/api'
import { EQUIPMENT } from '@/data'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardBody } from '@/components/ui/Card'
import { StatTile } from '@/components/ui/StatTile'
import { KIND } from '@/lib/labels'
import { fmtDate } from '@/lib/utils'
import { VehicleIcon } from '@/components/VehicleIcon'

/** Сводка за 7 дней. Считает сервер: GET /reports/weekly */
export function InspectorReports() {
  const { data: report, isPending, isError } = useQuery({ queryKey: ['weekly-report'], queryFn: api.weeklyReport, refetchInterval: 60_000 })
  if (isPending) return <p className="text-muted-foreground">Считаем отчёт…</p>
  if (isError || !report) return <p role="alert" className="text-danger">Не удалось загрузить отчёт. Обновите страницу.</p>
  const dayMax = Math.max(...report.byDay.map((d) => d.count), 1)
  const siteMax = Math.max(...report.bySite.map((s) => s.count), 1)

  return (
    <div>
      <PageHeader title="Отчёт за неделю" subtitle={`${fmtDate(report.dateFrom)} — ${fmtDate(report.dateTo)} · по всем объектам`} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        <StatTile label="Всего нарушений" value={report.total} hint="за 7 дней" />
        <StatTile label="Открытых нарушений" value={report.open} hint="сбои камер сюда не входят" tone="danger" />
        <StatTile label="Устранено" value={report.resolved} hint="закрыто" tone="ok" />
        <StatTile label="Ошибок системы" value={report.falsePositive} hint="ложных срабатываний" />
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Card><CardBody>
          <h2 className="font-semibold text-[17px] mb-3">Нарушения по дням</h2>
          <div className="flex items-end gap-2 h-40" role="img" aria-label={`Нарушения по дням: ${report.byDay.map((d) => `${d.label} — ${d.count}`).join(', ')}`}>
            {report.byDay.map((d) => (
              <div key={d.date} className="flex-1 flex flex-col items-center gap-1 h-full justify-end">
                <span className="text-[14px] font-semibold tabular">{d.count}</span>
                <div className="w-full rounded-t-sm bg-primary" style={{ height: `${(d.count / dayMax) * 100}%`, minHeight: d.count ? 6 : 2, opacity: d.count ? 1 : 0.25 }} />
                <span className="text-[13px] text-muted-foreground">{d.label}</span>
              </div>
            ))}
          </div>
        </CardBody></Card>

        <Card><CardBody>
          <h2 className="font-semibold text-[17px] mb-3">По объектам</h2>
          <ul className="space-y-3">
            {report.bySite.map((s) => (
              <li key={s.siteId}>
                <div className="flex justify-between text-[15px] mb-1">
                  <span className="font-medium truncate pr-3">{s.name}</span>
                  <span className="shrink-0 tabular">{s.count} <span className="text-muted-foreground">(срочных {s.high})</span></span>
                </div>
                <div className="h-2.5 rounded-sm bg-muted overflow-hidden"><div className="h-full bg-primary rounded-sm" style={{ width: `${(s.count / siteMax) * 100}%` }} /></div>
              </li>
            ))}
          </ul>
        </CardBody></Card>

        <Card><CardBody>
          <h2 className="font-semibold text-[17px] mb-3">По типам нарушений</h2>
          <ul className="divide-y divide-border">
            {report.byKind.map((k) => <li key={k.key} className="flex justify-between py-2"><span>{KIND[k.key]}</span><b className="tabular">{k.count}</b></li>)}
          </ul>
        </CardBody></Card>

        <Card><CardBody>
          <h2 className="font-semibold text-[17px] mb-3">С какой техникой чаще проблемы</h2>
          <ul className="divide-y divide-border">
            {report.byEquipment.map((e) => (
              <li key={e.key} className="flex items-center gap-3 py-2">
                <VehicleIcon type={e.key} className="w-12 h-8" fill={EQUIPMENT[e.key].color} />
                <span className="flex-1">{EQUIPMENT[e.key].name}</span><b className="tabular">{e.count}</b>
              </li>
            ))}
          </ul>
        </CardBody></Card>
      </div>
    </div>
  )
}
