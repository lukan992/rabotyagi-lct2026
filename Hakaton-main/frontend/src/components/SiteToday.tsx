import { Link } from 'react-router-dom'
import { MapPin, HardHat, Building } from 'lucide-react'
import { useApp } from '@/store/context'
import { isOpen, bySeverity } from '@/store/selectors'
import { cn, fmtDate, plural } from '@/lib/utils'
import { SITE_KIND } from '@/lib/labels'
import { lagDays, pace, planUnits } from '@/lib/schedule'
import { StatusPill } from './ui/StatusPill'
import { StatTile } from './ui/StatTile'
import { EmptyState } from './ui/EmptyState'
import { AlertCard } from './AlertCard'
import { AlertDetail } from './AlertDetail'
import { EquipmentCheck } from './EquipmentCheck'
import { WorkByCameras } from './WorkByCameras'
import { useOpenAlert } from '@/lib/useUrlState'

/** Главный экран объекта: светофор, этап, что не так, техника по плану и по факту */
export function SiteToday({ siteId, camerasLink, onShowCameras, showName = true }: {
  siteId: string; camerasLink?: string; onShowCameras?: () => void
  /** false — название объекта уже стоит над вкладками страницы объекта */
  showName?: boolean
}) {
  const { siteStatus, alertsForSite, bySite, byStage, stagesOf, role } = useApp()
  const { alert: sel, open: setSel, close } = useOpenAlert()
  const site = bySite(siteId)
  if (!site) return <p className="text-muted-foreground">Объект не найден или у вас нет к нему доступа.</p>
  const stage = byStage(site.currentStageId)
  const status = siteStatus(siteId)
  const open = alertsForSite(siteId).filter((a) => isOpen(a.status)).sort(bySeverity)
  const closed = alertsForSite(siteId).filter((a) => !isOpen(a.status))
  const { planProgress: plan, factProgress: fact } = site  // по всему плану объекта; null — плана нет
  const hasPlan = plan !== null && fact !== null
  const lag = hasPlan ? lagDays(planUnits(stagesOf(siteId))) : null  // в днях, как у этапов
  const progress = lag === null ? null : pace(lag)
  const urgent = open.filter((a) => a.severity === 'high').length

  // На телефоне первым идёт «что не так»: показатели уезжают ниже, иначе они занимают весь первый экран
  return (
    <div className="flex flex-col gap-6">
      <div>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            {showName && <h1 className="text-[22px] sm:text-2xl font-semibold leading-tight">{site.name}</h1>}
            <div className={cn('flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground text-[15px]', showName && 'mt-2')}>
              <span className="inline-flex items-center gap-1.5"><MapPin className="w-4 h-4" />{site.address}</span>
              <span className="inline-flex items-center gap-1.5"><Building className="w-4 h-4" />{site.kind !== 'other' && <>{SITE_KIND[site.kind].split(' (')[0]} · </>}{site.contractor}</span>
              {role?.id !== 'foreman' && <span className="inline-flex items-center gap-1.5"><HardHat className="w-4 h-4" />Прораб: {site.foreman || 'не назначен'}</span>}
            </div>
          </div>
          <StatusPill status={status} big />
        </div>
      </div>

      <div className="grid sm:grid-cols-3 gap-3 order-2 sm:order-none">{/* показатели */}
        <StatTile
          label="Этап сейчас" value={<span className="text-xl">{stage?.name ?? 'Нет этапа по плану'}</span>}
          hint={stage ? `сделано ${stage.factProgress}% · по графику до ${fmtDate(stage.end)}` : undefined}
        />
        {/* весь объект, а не текущий этап: на странице плана те же цифры стоят в строке «Объект в целом» */}
        <StatTile
          label="Готовность объекта" value={hasPlan ? `${fact}%` : '—'}
          hint={!hasPlan ? 'план работ не задан' : !stage ? `по графику ${plan}% · сейчас нет этапа по плану` : progress ? `по графику ${plan}% — ${progress.text}` : `по графику ${plan}%`}
          tone={stage ? progress?.tone : undefined}
        />
        <StatTile
          label="Открытых замечаний" value={open.length}
          hint={!open.length ? 'в журнале нет открытых записей' : urgent ? `из них ${plural(urgent, 'срочное', 'срочных', 'срочных')}` : 'срочных нет'}
          tone={urgent ? 'danger' : open.length ? 'warn' : 'ok'}
        />
      </div>

      {/* рядом с этапом по графику — какие работы видят на кадрах сервисы аналитики (если подключены) */}
      <WorkByCameras siteId={siteId} className="order-2 sm:order-none" />

      <section className="order-1 sm:order-none">
        <div className="flex items-baseline justify-between gap-3 mb-3">
          <h2 className="text-[18px] font-semibold">Что не так прямо сейчас</h2>
          {camerasLink && <Link to={camerasLink} className="text-primary font-semibold hover:underline min-h-[44px] inline-flex items-center">Смотреть камеры</Link>}
          {onShowCameras && (
            <button type="button" onClick={onShowCameras} className="text-primary font-semibold hover:underline min-h-[44px] inline-flex items-center cursor-pointer">
              Смотреть камеры
            </button>
          )}
        </div>
        {open.length === 0 ? (
          <div className="bg-card rounded-xl border border-border"><EmptyState title="Нет открытых замечаний" text="В журнале замечаний нет открытых записей. Это не подтверждает соблюдение графика." /></div>
        ) : (
          <div className="space-y-3">
            {open.map((a) => (
              <div key={a.id}>
                <AlertCard alert={a} onOpen={setSel} />
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="order-3 sm:order-none">
        <h2 className="text-[18px] font-semibold mb-2">План и факт по технике</h2>
        <EquipmentCheck siteId={siteId} />
      </section>

      {closed.length > 0 && (
        <section className="order-4 sm:order-none">
          <h2 className="text-[18px] font-semibold mb-2">Уже решено</h2>
          <div className="space-y-3">
            {closed.map((a) => <AlertCard key={a.id} alert={a} onOpen={setSel} compact />)}
          </div>
        </section>
      )}

      <AlertDetail alert={sel} onClose={close} />
    </div>
  )
}
