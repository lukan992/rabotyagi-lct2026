import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ChevronRight, MapPin, Plus } from 'lucide-react'
import { SITE_MANAGERS } from '@/data'
import { useApp } from '@/store/context'
import { isOpen } from '@/store/selectors'
import { PageHeader } from '@/components/ui/PageHeader'
import { StatusPill } from '@/components/ui/StatusPill'
import { StatTile } from '@/components/ui/StatTile'
import { Button } from '@/components/ui/Button'
import { SiteDialog } from '@/components/site'
import { fmtWhen, plural, pluralWord, todayLabel } from '@/lib/utils'
import { cn } from '@/lib/utils'
import { lagDays, pace, planUnits } from '@/lib/schedule'
import { useSearchParam } from '@/lib/useUrlState'

const SHOW = ['all', 'critical', 'warning', 'ok'] as const
type Show = typeof SHOW[number]

/** Все объекты одним взглядом: светофор, этап, отставание, открытые замечания. Руководитель и администратор здесь же добавляют объекты. */
export function ManagerOverview() {
  const { sites: visibleSites, siteStatus, alertsForSite, byStage, stagesOf, lastDataAt, base, role } = useApp()
  const navigate = useNavigate()
  const [creating, setCreating] = useState(false)
  const [show, setShow] = useSearchParam<Show>('status', 'all', SHOW)
  const toggle = (s: Show) => setShow(show === s ? 'all' : s)
  const isAdmin = role?.id === 'admin'
  const canAddSite = !!role && SITE_MANAGERS.includes(role.id)
  const counts = { ok: 0, warning: 0, critical: 0 }
  visibleSites.forEach((s) => { counts[siteStatus(s.id)]++ })
  const totalOpen = visibleSites.reduce((n, s) => n + alertsForSite(s.id).filter((a) => isOpen(a.status)).length, 0)

  return (
    <div>
      <PageHeader
        title={isAdmin ? 'Все объекты' : 'Мои объекты'} subtitle={`${todayLabel()}${lastDataAt ? ` · данные на ${fmtWhen(lastDataAt)}` : ''}`}
        action={canAddSite && <Button size="lg" onClick={() => setCreating(true)}><Plus className="w-5 h-5" /> Добавить объект</Button>}
      />
      {visibleSites.length === 0 && <FirstSite canAdd={canAddSite} />}
      {visibleSites.length > 0 && <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        <StatTile label="Нужно вмешаться" value={counts.critical} hint={pluralWord(counts.critical, 'объект', 'объекта', 'объектов')} tone="danger" onClick={() => toggle('critical')} active={show === 'critical'} />
        <StatTile label="Есть замечания" value={counts.warning} hint={pluralWord(counts.warning, 'объект', 'объекта', 'объектов')} tone="warn" onClick={() => toggle('warning')} active={show === 'warning'} />
        <StatTile label="Нет открытых замечаний" value={counts.ok} hint={pluralWord(counts.ok, 'объект', 'объекта', 'объектов')} tone="ok" onClick={() => toggle('ok')} active={show === 'ok'} />
        <StatTile label="Открытых замечаний" value={totalOpen} hint="по всем объектам →" onClick={() => navigate(`${base}/alerts`)} />
      </div>}
      {show !== 'all' && (
        <p className="-mt-3 mb-4 text-[15px] text-muted-foreground">
          Показаны только объекты «{{ critical: 'Нужно вмешаться', warning: 'Есть замечания', ok: 'Нет открытых замечаний' }[show]}».{' '}
          <button type="button" onClick={() => setShow('all')} className="text-primary font-semibold hover:underline cursor-pointer min-h-[44px]">Показать все</button>
        </p>
      )}

      <div className="grid md:grid-cols-2 gap-4">
        {visibleSites.filter((s) => show === 'all' || siteStatus(s.id) === show).sort((a, b) => rank(siteStatus(b.id)) - rank(siteStatus(a.id))).map((s) => {
          const status = siteStatus(s.id)
          const open = alertsForSite(s.id).filter((a) => isOpen(a.status))
          const lag = s.planProgress === null ? null : lagDays(planUnits(stagesOf(s.id)))
          const progress = lag === null ? null : pace(lag)
          const stage = byStage(s.currentStageId)
          return (
            <div key={s.id}>
              <Link
                to={`${base}/site/${s.id}`}
                className={cn(
                  'block bg-card rounded-xl border p-5 shadow-[var(--shadow-card)] hover:shadow-md transition-all',
                  'border-border hover:border-primary/60',
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-[18px] font-semibold leading-tight">{s.name}</div>
                    <div className="text-muted-foreground text-[14px] inline-flex items-center gap-1 mt-1"><MapPin className="w-4 h-4" />{s.address}</div>
                  </div>
                  <ChevronRight className="w-7 h-7 text-muted-foreground shrink-0" />
                </div>
                <div className="mt-3"><StatusPill status={status} /></div>
                <dl className="grid grid-cols-3 gap-2 mt-4 text-[14px]">
                  <div><dt className="text-muted-foreground">Этап</dt><dd className="font-semibold leading-tight">{stage?.name ?? '—'}</dd></div>
                  <div><dt className="text-muted-foreground">Выполнено</dt><dd className="font-semibold">
                    {s.factProgress === null ? <span className="text-muted-foreground font-normal">нет плана</span> : <>{s.factProgress}% <span className="text-muted-foreground font-normal">/ план {s.planProgress}%</span></>}
                    {/* на карточке — только отставание: что идёт по графику, и так видно по цифрам */}
                    {progress && progress.tone !== 'ok' && <span className={cn('block', progress.tone === 'danger' ? 'text-danger' : 'text-warn')}>{progress.short}</span>}
                  </dd></div>
                  <div><dt className="text-muted-foreground">Замечания</dt><dd className="font-semibold">{open.length ? plural(open.length, 'открытое', 'открытых', 'открытых') : 'нет'}</dd></div>
                </dl>
                {open[0] && (
                  <div className={cn('mt-3 rounded-lg px-3 py-2 text-[15px] font-semibold', open.some((a) => a.severity === 'high') ? 'bg-danger-bg text-danger-fg' : 'bg-warn-bg text-warn-fg')}>
                    {open.sort((a, b) => (a.severity === 'high' ? -1 : 1) - (b.severity === 'high' ? -1 : 1))[0].title}
                    {open.length > 1 && <span className="font-normal opacity-80"> и ещё {open.length - 1}</span>}
                  </div>
                )}
              </Link>
            </div>
          )
        })}
      </div>
      {canAddSite && <SiteDialog site={creating ? 'new' : null} onClose={() => setCreating(false)} onCreated={(site) => navigate(`${base}/site/${site.id}`)} />}
    </div>
  )
}

/** Новая установка, объектов ещё нет: что сделать, чтобы сверка заработала */
function FirstSite({ canAdd }: { canAdd: boolean }) {
  return (
    <section className="bg-card rounded-xl border border-border shadow-[var(--shadow-card)] p-5 sm:p-6 max-w-2xl">
      <h2 className="text-[18px] font-semibold">Объектов пока нет</h2>
      {canAdd ? (
        <>
          <p className="text-muted-foreground mt-1">Система сверяет технику на площадке с календарным планом объекта. Чтобы начать:</p>
          <ul className="mt-3 space-y-2 list-disc pl-5">
            <li><b>Добавьте объект</b> кнопкой выше — название, адрес, подрядчик и рабочее время.</li>
            <li><b>Заведите план и зоны</b> в карточке объекта — план можно загрузить из Excel по шаблону. У каждой работы плана — правило «этап → техника».</li>
            <li><b>Подключите камеры</b> к зонам объекта — дальше сверка идёт сама, раз в минуту.</li>
          </ul>
        </>
      ) : (
        <p className="text-muted-foreground mt-1">Объекты заводят руководитель проекта и администратор. Когда вас добавят к объекту, он появится здесь.</p>
      )}
    </section>
  )
}

function rank(s: 'ok' | 'warning' | 'critical') { return { critical: 2, warning: 1, ok: 0 }[s] }
