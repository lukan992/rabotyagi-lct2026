import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { ScanSearch } from 'lucide-react'
import { type Severity } from '@/data'
import { useApp } from '@/store/context'
import { isOpen, bySeverity } from '@/store/selectors'
import { PageHeader } from '@/components/ui/PageHeader'
import { Chip } from '@/components/ui/Chip'
import { AlertCard } from '@/components/AlertCard'
import { AlertDetail } from '@/components/AlertDetail'
import { EmptyState } from '@/components/ui/EmptyState'
import { useOpenAlert, useSearchParam } from '@/lib/useUrlState'

const FILTERS = ['open', 'high', 'closed', 'all'] as const
type Filter = typeof FILTERS[number]

/** Лента отклонений по всем объектам с простыми фильтрами-кнопками */
export function ManagerAlerts() {
  const { alerts, sites: visibleSites, base } = useApp()
  const [filter, setFilter] = useSearchParam<Filter>('show', 'open', FILTERS)
  const [site, setSite] = useSearchParam<string>('site', 'all')
  const { alert: sel, open: setSel, close } = useOpenAlert()

  const list = useMemo(() => alerts
    .filter((a) => site === 'all' || a.siteId === site)
    .filter((a) => filter === 'all' ? true : filter === 'open' ? isOpen(a.status) : filter === 'closed' ? !isOpen(a.status) : a.severity === ('high' as Severity) && isOpen(a.status))
    .sort(bySeverity), [alerts, filter, site])

  const count = (f: Filter) => alerts.filter((a) => site === 'all' || a.siteId === site).filter((a) => f === 'all' ? true : f === 'open' ? isOpen(a.status) : f === 'closed' ? !isOpen(a.status) : a.severity === 'high' && isOpen(a.status)).length

  return (
    <div>
      <PageHeader
        title="Отклонения" info="Все замечания системы по вашим объектам. Откройте замечание, чтобы увидеть кадры-доказательства и ответить."
        action={
          <Link to={`${base}/check`} className="inline-flex items-center gap-2 min-h-[44px] px-4 rounded-lg border border-border-strong bg-card font-medium hover:bg-muted transition-colors">
            <ScanSearch className="w-5 h-5" /> Проверить своё фото
          </Link>
        }
      />
      <div className="flex flex-wrap gap-2 mb-3">
        {([['open', 'Открытые'], ['high', 'Срочные'], ['closed', 'Закрытые'], ['all', 'Все']] as [Filter, string][]).map(([f, l]) => (
          <Chip key={f} active={filter === f} onClick={() => setFilter(f)}>{l} <span className="opacity-70">{count(f)}</span></Chip>
        ))}
      </div>
      <div className="flex flex-wrap gap-2 mb-5">
        <Chip active={site === 'all'} onClick={() => setSite('all')} small>Все объекты</Chip>
        {visibleSites.map((s) => <Chip key={s.id} active={site === s.id} onClick={() => setSite(s.id)} small>{s.name}</Chip>)}
      </div>
      {list.length === 0 ? (
        <div className="bg-card rounded-xl border border-border"><EmptyState title="Ничего нет" text="По выбранным условиям отклонений не найдено." /></div>
      ) : (
        <div className="space-y-3">{list.map((a) => <AlertCard key={a.id} alert={a} onOpen={setSel} showSite />)}</div>
      )}
      <AlertDetail alert={sel} onClose={close} />
    </div>
  )
}
