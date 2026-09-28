import { useMemo, useState } from 'react'
import { Download, FileWarning, Search } from 'lucide-react'
import { useApp } from '@/store/context'
import { isOpen } from '@/store/selectors'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { AlertDetail } from '@/components/AlertDetail'
import { Chip } from '@/components/ui/Chip'
import { inputCls } from '@/components/ui/Field'
import { fmtDateShort, fmtTime, todayISO, plural } from '@/lib/utils'
import { KIND, SEVERITY, STATUS } from '@/lib/labels'
import { cn } from '@/lib/utils'
import { useOpenAlert, useSearchParam } from '@/lib/useUrlState'

const FILTERS = ['open', 'prescribed', 'closed', 'all'] as const
type Filter = typeof FILTERS[number]
const PERIODS = ['all', '7', '30', '90'] as const
type Period = typeof PERIODS[number]
const PERIOD_LABEL: Record<Period, string> = { all: 'За всё время', 7: 'За 7 дней', 30: 'За 30 дней', 90: 'За 90 дней' }

/** Журнал нарушений: таблица с фильтрами, экспорт, выдача предписаний */
export function InspectorViolations() {
  const { alerts, notify, sites, bySite, byZone } = useApp()
  const [filter, setFilter] = useSearchParam<Filter>('show', 'open', FILTERS)
  const [site, setSite] = useSearchParam<string>('site', 'all')
  const [contractor, setContractor] = useSearchParam<string>('contractor', 'all')
  const [period, setPeriod] = useSearchParam<Period>('period', 'all', PERIODS)
  const [text, setText] = useSearchParam<string>('q', '')
  const { alert: sel, open: setSel, close } = useOpenAlert()
  // инспектор проверяет подрядчиков — отбор по подрядчику сразу по всем его объектам
  const contractors = useMemo(() => [...new Set(sites.map((s) => s.contractor).filter(Boolean))].sort(), [sites])

  const [openedAt] = useState(() => Date.now())  // от этого момента считаем «за 7 дней»: список не прыгает при обновлении данных
  const list = useMemo(() => {
    const since = period === 'all' ? 0 : openedAt - Number(period) * 86_400_000
    const words = text.trim().toLowerCase().split(/\s+/).filter(Boolean)
    return alerts
      .filter((a) => a.kind !== 'camera_offline')
      .filter((a) => site === 'all' || a.siteId === site)
      .filter((a) => contractor === 'all' || bySite(a.siteId)?.contractor === contractor)
      .filter((a) => !since || Date.parse(a.startedAt) >= since)
      .filter((a) => filter === 'all' ? true : filter === 'open' ? isOpen(a.status) && a.status !== 'prescribed' : filter === 'prescribed' ? a.status === 'prescribed' : !isOpen(a.status))
      .filter((a) => {
        if (!words.length) return true
        const hay = [a.code, a.prescriptionNo, a.title, bySite(a.siteId)?.name, bySite(a.siteId)?.contractor, byZone(a.zoneId)?.name].join(' ').toLowerCase()
        return words.every((w) => hay.includes(w))
      })
      .sort((a, b) => b.startedAt.localeCompare(a.startedAt))
  }, [alerts, filter, site, contractor, period, text, bySite, byZone, openedAt])
  const narrowed = site !== 'all' || contractor !== 'all' || period !== 'all' || !!text

  const exportCsv = () => {
    const last = (a: typeof list[number]) => a.history[a.history.length - 1]
    const rows = [['№', 'Дата', 'Время', 'Объект', 'Подрядчик', 'Зона', 'Тип', 'Нарушение', 'Важность', 'Статус', 'Предписание', 'Срок устранения', 'Последний ответ'],
      ...list.map((a) => [
        a.code, fmtDateShort(a.startedAt), fmtTime(a.startedAt), bySite(a.siteId)?.name ?? '', bySite(a.siteId)?.contractor ?? '',
        byZone(a.zoneId)?.name ?? '', KIND[a.kind], a.title, SEVERITY[a.severity].label, STATUS[a.status].label,
        a.prescriptionNo ?? '', a.prescriptionDue ? fmtDateShort(a.prescriptionDue) : '', last(a) ? `${last(a).who}: ${last(a).text}` : '',
      ])]
    const csv = '\ufeff' + rows.map((r) => r.map(csvCell).join(';')).join('\r\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a'); a.href = url; a.download = `нарушения-${todayISO()}.csv`; a.click()
    // сразу отзывать ссылку нельзя: Safari и Firefox начинают скачивание уже после click()
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
    notify(`Выгружено ${plural(list.length, 'нарушение', 'нарушения', 'нарушений')} в «нарушения-${todayISO()}.csv»`)
  }

  return (
    <div>
      <PageHeader
        title="Журнал нарушений"
        info="Отклонения от графика, которые система нашла по видео с камер. Откройте нарушение, чтобы посмотреть кадры-доказательства, выдать предписание или закрыть его."
        action={<Button variant="outline" onClick={exportCsv}><Download className="w-5 h-5" /> Выгрузить в Excel</Button>}
      />
      <div className="flex flex-wrap gap-2 mb-3">
        {([['open', 'Новые и в работе'], ['prescribed', 'С предписанием'], ['closed', 'Закрытые'], ['all', 'Все']] as [Filter, string][]).map(([f, l]) => (
          <Chip key={f} active={filter === f} onClick={() => setFilter(f)}>{l}</Chip>
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(0,1.4fr)_repeat(3,minmax(0,1fr))] mb-5">
        <label className="relative block">
          <span className="sr-only">Поиск по журналу</span>
          <Search className="w-5 h-5 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <input type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder="№, объект, нарушение, подрядчик…" className={cn(inputCls, 'pl-10')} />
        </label>
        <label>
          <span className="sr-only">Объект</span>
          <select value={site} onChange={(e) => setSite(e.target.value)} className={inputCls}>
            <option value="all">Все объекты</option>
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <label>
          <span className="sr-only">Подрядчик</span>
          <select value={contractor} onChange={(e) => setContractor(e.target.value)} className={inputCls}>
            <option value="all">Все подрядчики</option>
            {contractors.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label>
          <span className="sr-only">Период</span>
          <select value={period} onChange={(e) => setPeriod(e.target.value as Period)} className={inputCls}>
            {PERIODS.map((p) => <option key={p} value={p}>{PERIOD_LABEL[p]}</option>)}
          </select>
        </label>
      </div>

      <div className="bg-card rounded-xl border border-border overflow-x-auto">
        <table className="w-full text-[15px] min-w-[860px]">
          <thead className="bg-muted/60 text-left text-[13px] text-muted-foreground">
            <tr>
              <th className="px-4 py-3">№</th>
              <th className="px-4 py-3">Дата</th>
              <th className="px-4 py-3">Объект</th>
              <th className="px-4 py-3">Нарушение</th>
              <th className="px-4 py-3">Важность</th>
              <th className="px-4 py-3">Статус</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {list.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-10 text-center text-muted-foreground">
                Ничего не найдено.
                {narrowed && <> <button type="button" className="text-primary font-semibold hover:underline cursor-pointer" onClick={() => { setSite('all'); setContractor('all'); setPeriod('all'); setText('') }}>Сбросить отбор</button></>}
              </td></tr>
            )}
            {list.map((a) => (
              <tr key={a.id} className="hover:bg-muted/40 cursor-pointer" onClick={() => setSel(a)}>
                <td className="px-4 py-3 whitespace-nowrap font-mono text-[13px] text-muted-foreground">{a.code}</td>
                <td className="px-4 py-3 whitespace-nowrap tabular">{fmtDateShort(a.startedAt)}<div className="text-muted-foreground text-[13px]">{fmtTime(a.startedAt)}</div></td>
                <td className="px-4 py-3"><div className="font-semibold">{bySite(a.siteId)?.name}</div><div className="text-muted-foreground text-[13px]">{byZone(a.zoneId)?.name}</div></td>
                <td className="px-4 py-3"><div className="font-semibold">{a.title}</div><div className="text-muted-foreground text-[13px]">{KIND[a.kind]}</div></td>
                <td className="px-4 py-3"><Badge tone={SEVERITY[a.severity].tone}>{SEVERITY[a.severity].label}</Badge></td>
                <td className="px-4 py-3">
                  <Badge tone={STATUS[a.status].tone}>{STATUS[a.status].label}</Badge>
                  {a.status === 'prescribed' && a.prescriptionDue && (
                    <div className={cn('text-[13px] mt-1 whitespace-nowrap', a.prescriptionDue < todayISO() ? 'text-danger font-semibold' : 'text-muted-foreground')}>
                      {a.prescriptionDue < todayISO() ? 'просрочено, ' : ''}до {fmtDateShort(a.prescriptionDue)}
                    </div>
                  )}
                </td>
                <td className="px-4 py-3 text-right">
                  {/* настоящая кнопка: строку таблицы с клавиатуры не открыть */}
                  <button type="button" onClick={(e) => { e.stopPropagation(); setSel(a) }} aria-label={`Открыть нарушение № ${a.code}`}
                    className={cn('inline-flex items-center gap-1 min-h-[44px] px-2 rounded-md font-semibold text-primary hover:underline cursor-pointer whitespace-nowrap')}>
                    {a.status === 'prescribed' ? <><FileWarning className="w-4 h-4" /> № {a.prescriptionNo}</> : 'Открыть'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <AlertDetail alert={sel} onClose={close} />
    </div>
  )
}

/** Ячейка CSV: кавычки удваиваем; текст, начинающийся с = + - @, Excel принял бы за формулу — ставим перед ним апостроф */
function csvCell(value: string) {
  const safe = /^[=+\-@]/.test(value) ? `'${value}` : value
  return `"${safe.replace(/"/g, '""')}"`
}
