import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { m } from 'framer-motion'
import { CheckCircle2, Circle, Loader2, PencilLine } from 'lucide-react'
import { api } from '@/api'
import { EQUIPMENT, PROGRESS_REPORTERS, type Stage } from '@/data'
import { useApp } from '@/store/context'
import { ago, cn, daysBetween, fmtDate, plural, todayISO } from '@/lib/utils'
import { lagDays, pace, planUnits, type Tone } from '@/lib/schedule'
import { Button } from './ui/Button'
import { InfoTip } from './ui/InfoTip'
import { VehicleIcon } from './VehicleIcon'

type Kind = 'done' | 'current' | 'future'
/** lag — на сколько дней отстали от графика, меньше нуля — опередили */
interface Phase { stage: Stage; works: Stage[]; plan: number; fact: number; lag: number; kind: Kind }

const FILL: Record<Tone, string> = { ok: 'bg-ok', warn: 'bg-warn', danger: 'bg-danger' }
const TEXT: Record<Tone, string> = { ok: 'text-ok', warn: 'text-warn', danger: 'text-danger' }

const length = (s: Stage) => daysBetween(s.start, s.end) + 1
const range = (s: Stage) => `${fmtDate(s.start)} — ${fmtDate(s.end)}`
/** Завершён — сделан по факту; текущий — по графику уже начался или работы уже идут; будущий — остальное */
const kindOf = (plan: number, fact: number): Kind => (fact >= 100 ? 'done' : plan > 0 || fact > 0 ? 'current' : 'future')

/** Выполнение укрупнённого этапа: по его работам с весом по длительности, у этапа без работ — его собственное */
function measure(stage: Stage, works: Stage[]): Omit<Phase, 'stage' | 'works' | 'kind'> {
  const lag = lagDays(works.length ? works : [stage]) ?? 0
  if (!works.length) return { plan: stage.planProgress, fact: stage.factProgress, lag }
  const total = works.reduce((n, w) => n + length(w), 0)
  const avg = (key: 'planProgress' | 'factProgress') => Math.round(works.reduce((n, w) => n + w[key] * length(w), 0) / total)
  return { plan: avg('planProgress'), fact: avg('factProgress'), lag }
}

/**
 * План работ объекта: завершённые этапы, текущие и будущие. У текущих полоса показывает, сколько сделано по факту,
 * а черта на ней — где работы должны быть по графику на сегодня. Сколько сделано, отмечают прораб, руководитель и администратор.
 */
export function StageTimeline({ siteId }: { siteId: string }) {
  const { stagesOf, role, bySite } = useApp()
  const stages = stagesOf(siteId)
  const site = bySite(siteId)
  const canReport = !!role && PROGRESS_REPORTERS.includes(role.id)
  const phases: Phase[] = stages
    .filter((s) => s.level === 1)
    .sort((a, b) => a.start.localeCompare(b.start))
    .map((stage) => {
      const works = stages.filter((w) => w.parentId === stage.id).sort((a, b) => a.start.localeCompare(b.start))
      const measured = measure(stage, works)
      return { stage, works, ...measured, kind: kindOf(measured.plan, measured.fact) }
    })
  if (!phases.length) return <p className="text-muted-foreground">План работ пока пуст.</p>
  const of = (kind: Kind) => phases.filter((p) => p.kind === kind)

  // отставание объекта — в днях, как у этапов: 2% всего объекта — это почти неделя
  const lag = lagDays(planUnits(stages))
  const whole = site && site.planProgress !== null && site.factProgress !== null && lag !== null
    ? { plan: site.planProgress, fact: site.factProgress, ...pace(lag) }
    : null

  return (
    <div className="space-y-8">
      {/* те же цифры, что в плитке «Готовность объекта» на главном экране, — чтобы проценты этапов не путали с общими */}
      {whole && (
        <div className="bg-card rounded-xl border border-border px-4 py-3.5">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 mb-2">
            <span className="font-semibold">Объект в целом</span>
            <span className="text-[15px]">
              По факту <b className="tabular">{whole.fact}%</b> <span className="text-muted-foreground">по графику {whole.plan}%</span>
              {' · '}<span className={cn('font-medium', site?.currentStageId ? TEXT[whole.tone] : 'text-muted-foreground')}>{site?.currentStageId ? whole.text : 'сейчас нет этапа по плану'}</span>
            </span>
          </div>
          <Track plan={whole.plan} fact={whole.fact} tone={whole.tone} />
        </div>
      )}

      {of('done').length > 0 && (
        <Section title="Завершённые" count={of('done').length}>
          <ul className="bg-card rounded-xl border border-border divide-y divide-border">
            {of('done').map((p) => (
              <li key={p.stage.id} className="px-4 py-3 flex flex-wrap items-center gap-x-3 gap-y-1">
                <CheckCircle2 className="w-5 h-5 text-ok shrink-0" aria-hidden />
                <span className="font-semibold">{p.stage.name}</span>
                <span className="text-[14px] text-muted-foreground">{range(p.stage)}</span>
                {p.plan < 100 && <span className="text-[14px] text-ok font-semibold">досрочно</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {of('current').length > 0 && (
        <Section
          title="Текущие" count={of('current').length}
          info={<>Заливка полосы — сколько сделано <b>по факту</b>, тёмная черта — где работы должны быть <b>по графику</b> на сегодня. Заливка левее черты — отставание.</>}
        >
          <div className="space-y-4">
            {of('current').map((p) => <CurrentPhase key={p.stage.id} phase={p} canReport={canReport} />)}
          </div>
        </Section>
      )}

      {of('future').length > 0 && (
        <Section title="Будущие" count={of('future').length}>
          <ul className="bg-card rounded-xl border border-border divide-y divide-border">
            {of('future').map((p) => (
              <li key={p.stage.id} className="px-4 py-3">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <Circle className="w-5 h-5 text-muted-foreground shrink-0" aria-hidden />
                  <span className="font-semibold">{p.stage.name}</span>
                  <span className="text-[14px] text-muted-foreground">{range(p.stage)} · {startsIn(p.stage)}</span>
                </div>
                {p.works.length > 0 && (
                  <p className="mt-1 pl-8 text-[14px] text-muted-foreground">{p.works.map((w) => w.name).join(' → ')}</p>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  )
}

/** Отметку выполнения не обновляли больше трёх дней, а работа уже идёт — цифрам «по факту» верить рано */
const STALE_MS = 3 * 86_400_000
function stale(work: Stage) {
  return work.factUpdatedAt ? Date.now() - Date.parse(work.factUpdatedAt) > STALE_MS : work.planProgress >= 10 && work.factProgress === 0
}

function startsIn(stage: Stage) {
  const n = daysBetween(todayISO(), stage.start)
  return n <= 0 ? 'начинается сегодня' : n === 1 ? 'начнётся завтра' : `начнётся через ${plural(n, 'день', 'дня', 'дней')}`
}

function Section({ title, count, info, children }: { title: string; count: number; info?: ReactNode; children: ReactNode }) {
  return (
    <section>
      <div className="flex items-center gap-2 mb-3">
        <h2 className="text-[18px] font-semibold">{title} <span className="text-muted-foreground font-normal">· {count}</span></h2>
        {info && <InfoTip label={`Как читать: ${title.toLowerCase()} этапы`}>{info}</InfoTip>}
      </div>
      {children}
    </section>
  )
}

/** Полоса выполнения: заливка — сделано по факту, черта — где должно быть по графику */
function Track({ plan, fact, tone, big }: { plan: number; fact: number; tone: Tone; big?: boolean }) {
  return (
    <div className={cn('relative rounded-full bg-muted', big ? 'h-3.5' : 'h-2.5')} role="img" aria-label={`Сделано по факту ${fact}%, по графику должно быть ${plan}%`}>
      <m.div
        className={cn('absolute inset-y-0 left-0 rounded-full', FILL[tone])}
        initial={{ width: 0 }} animate={{ width: `${fact}%` }} transition={{ duration: 0.5, ease: 'easeOut' }}
      />
      <span className="absolute -top-1.5 -bottom-1.5 w-[3px] -ml-[1.5px] rounded-full bg-foreground" style={{ left: `${plan}%` }} aria-hidden />
    </div>
  )
}

function Numbers({ plan, fact, lag, className }: { plan: number; fact: number; lag: number; className?: string }) {
  const p = pace(lag)
  return (
    <div className={cn('flex flex-wrap items-baseline gap-x-4 gap-y-1', className)}>
      <span>По факту <b>{fact}%</b></span>
      <span className="text-muted-foreground">по графику {plan}%</span>
      <span className={cn('font-semibold', TEXT[p.tone])}>{p.text}</span>
    </div>
  )
}

function CurrentPhase({ phase, canReport }: { phase: Phase; canReport: boolean }) {
  const { stage, works, plan, fact, lag } = phase
  const today = todayISO()
  const overdue = daysBetween(stage.end, today)
  // «на каком этапе должно быть и на каком по факту» — словами, по работам этапа
  const bySchedule = works.filter((w) => w.start <= today && today <= w.end)
  const inWork = works.filter((w) => w.factProgress > 0 && w.factProgress < 100)
  return (
    <article className="bg-card rounded-xl border border-border shadow-[var(--shadow-card)] p-4 sm:p-5">
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="text-[18px] font-semibold leading-snug">{stage.name}</h3>
        <span className="text-[14px] text-muted-foreground">{range(stage)}</span>
      </header>
      <div className="mt-4"><Track big plan={plan} fact={fact} tone={pace(lag).tone} /></div>
      <Numbers plan={plan} fact={fact} lag={lag} className="mt-2.5 text-[15px]" />
      {overdue > 0 && <p className="mt-1 text-[15px] font-semibold text-danger">Срок этапа вышел {plural(overdue, 'день', 'дня', 'дней')} назад</p>}

      {works.length > 0 && (
        <>
          <dl className="mt-3 space-y-0.5 text-[15px]">
            <div><dt className="inline text-muted-foreground">По графику сейчас: </dt><dd className="inline font-semibold">{bySchedule.length ? bySchedule.map((w) => w.name).join(', ') : 'перерыв между работами'}</dd></div>
            <div><dt className="inline text-muted-foreground">По факту идёт: </dt><dd className="inline font-semibold">{inWork.length ? inWork.map((w) => `${w.name} (${w.factProgress}%)`).join(', ') : 'работы ещё не начаты'}</dd></div>
          </dl>
          <ul className="mt-4 border-t border-border divide-y divide-border">
            {works.map((w) => <WorkRow key={w.id} work={w} canReport={canReport} />)}
          </ul>
        </>
      )}
      {works.length === 0 && canReport && <FactControl stage={stage} />}
    </article>
  )
}

function WorkRow({ work, canReport }: { work: Stage; canReport: boolean }) {
  const { rules } = useApp()
  const kind = kindOf(work.planProgress, work.factProgress)
  const rule = work.ruleKey ? rules[work.ruleKey] : undefined
  const lag = lagDays([work]) ?? 0
  if (kind !== 'current') {
    return (
      <li className="py-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[15px]">
        {kind === 'done'
          ? <CheckCircle2 className="w-5 h-5 text-ok shrink-0" aria-hidden />
          : <Circle className="w-5 h-5 text-muted-foreground shrink-0" aria-hidden />}
        <span className={cn('font-semibold', kind === 'done' && 'text-muted-foreground')}>{work.name}</span>
        <span className="text-[14px] text-muted-foreground">{range(work)}{kind === 'future' && ` · ${startsIn(work)}`}</span>
      </li>
    )
  }
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="font-semibold text-[16px]">{work.name}</span>
        <span className="text-[14px] text-muted-foreground">{range(work)}</span>
      </div>
      <div className="mt-2.5"><Track plan={work.planProgress} fact={work.factProgress} tone={pace(lag).tone} /></div>
      <Numbers plan={work.planProgress} fact={work.factProgress} lag={lag} className="mt-2 text-[14px]" />
      {stale(work) && <p className="text-[14px] text-warn font-semibold mt-0.5">Выполнение давно не отмечали: {work.factUpdatedAt ? ago(work.factUpdatedAt) : 'ни разу'}</p>}
      {rule && rule.required.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[14px]">
          <span className="text-muted-foreground">Нужна техника:</span>
          {rule.required.map((r) => (
            <span key={r.type} className="inline-flex items-center gap-1.5 bg-muted rounded-sm pl-1.5 pr-3 py-0.5 font-semibold">
              <VehicleIcon type={r.type} className="w-7 h-4" fill={EQUIPMENT[r.type].color} />
              {EQUIPMENT[r.type].name} ×{r.min}
            </span>
          ))}
        </div>
      )}
      {canReport && <FactControl stage={work} />}
    </li>
  )
}

/** «Отметить выполнение»: сколько сделано по факту — ползунком с шагом 5% */
function FactControl({ stage }: { stage: Stage }) {
  const { run } = useApp()
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState(stage.factProgress)
  const [busy, setBusy] = useState(false)
  const id = useId()
  if (!open) {
    return (
      <Button variant="outline" className="mt-3" onClick={() => { setValue(stage.factProgress); setOpen(true) }}>
        <PencilLine className="w-4 h-4" /> Отметить выполнение
      </Button>
    )
  }
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    const ok = await run(() => api.setStageProgress(stage.id, value), `«${stage.name}»: отмечено ${value}%`)
    setBusy(false)
    if (ok) setOpen(false)
  }
  return (
    <form onSubmit={submit} className="mt-3 rounded-lg bg-muted/60 p-3 space-y-2">
      <label htmlFor={id} className="flex items-baseline justify-between gap-3 font-semibold text-[15px]">
        Сделано по факту <span className="tabular-nums text-[20px]">{value}%</span>
      </label>
      <input
        id={id} type="range" min={0} max={100} step={5} value={value} onChange={(e) => setValue(Number(e.target.value))}
        className="w-full h-11 cursor-pointer accent-[var(--color-primary)]"
      />
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy || value === stage.factProgress}>{busy && <Loader2 className="w-4 h-4 animate-spin" />} Сохранить</Button>
        <Button type="button" variant="outline" onClick={() => setOpen(false)}>Отмена</Button>
      </div>
    </form>
  )
}
