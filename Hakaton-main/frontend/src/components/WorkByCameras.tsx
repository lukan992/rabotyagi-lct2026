import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, m } from 'framer-motion'
import { ChevronDown, Loader2, RefreshCw } from 'lucide-react'
import { api, ApiError, mediaUrl } from '@/api'
import type { AnalyticsService, CameraWork, ServiceAnswer, SiteWork, WorkGroup } from '@/data'
import { useApp } from '@/store/context'
import { cn, fmtDate, fmtTime, fmtWhen, plural } from '@/lib/utils'
import { Badge } from './ui/Badge'
import { Button } from './ui/Button'
import { InfoTip } from './ui/InfoTip'
import { ResourceAssessment } from './ResourceAssessment'

// «по снимку» отвечает нейросеть — так и подписано: людям важно знать, где оценка модели
const SERVICE: Record<AnalyticsService, string> = { deterministic: 'По технике', vlm_llm: 'По снимку (ИИ)' }

/** Ответ есть, но работу он не называет — почему */
const OUTCOME: Record<string, string> = {
  insufficient_evidence: 'Признаков мало — по этому кадру работу не определить',
  outside_plan: 'На кадре работа, которой нет в плане',
  no_plan: 'План сервису не отправлен — сверить было не с чем',
  scope_unknown: 'Камера не привязана к участку плана',
}

const VISUAL: Record<string, string> = {
  operation_indicated: 'видно, что работа идёт',
  presence_or_result_only: 'видна техника или результат, но не сама работа',
}

/** Что сервис «по технике» говорит о следующей работе (кроме «похоже, началась» — оно выделено отдельно) */
const TRANSITION: Record<string, (next: string) => string> = {
  no_signal: (next) => `Техники следующей работы${next} на кадрах не видно.`,
  single_or_short_signal: (next) => `Техника следующей работы${next} мелькнула один раз — ждём подтверждения.`,
  not_distinguishable_by_equipment: (next) => `Начало следующей работы${next} по технике не отличить: техника та же.`,
  needs_progress_anchor: () => 'О следующей работе судить не по чему: в плане нет отметок выполнения.',
  history_incomplete: () => 'Наблюдений пока мало, чтобы судить о начале следующей работы.',
  cv_unavailable: () => 'Распознавание техники недоступно — о следующей работе судить нельзя.',
  no_next_stage: () => 'Идёт последняя работа плана.',
}

/** Почему нет ответа — по коду отказа сервиса (раздел 11 контракта); подробность от сервиса видят руководитель и админ */
const REFUSAL: Record<string, string> = {
  not_ready: 'Сервис не готов к работе',
  dependency_unavailable: 'Сервису недоступна модель',
  model_failure: 'Ошибка модели сервиса',
  model_invalid_response: 'Модель сервиса ответила не по формату',
  analysis_timeout: 'Сервис не успел ответить',
  busy: 'Сервис занят',
  unreachable: 'Нет связи с сервисом',
  unauthorized: 'Сервис не принял токен доступа',
  forbidden: 'Сервис не принял токен доступа',
  catalog_version_mismatch: 'Справочник сервиса обновился — проверьте виды работ в плане',
  invalid_result: 'Ответ сервиса не относится к этому кадру',
  execution_uncertain: 'Неизвестно, выполнен ли анализ',
}
const refusal = (code: string | null, fallback: string) =>
  code && REFUSAL[code] ? REFUSAL[code] : code && /^(invalid|unknown|image|idempotency|observation|plan|ambiguous)/.test(code) ? 'Сервис отклонил запрос' : fallback

const days = (seconds: number) => plural(Math.max(1, Math.round(seconds / 86_400)), 'день', 'дня', 'дней')

const works = (group: WorkGroup) =>
  group.works.map((w) => `«${w.name}»`).join(group.match === 'ambiguous' ? ' или ' : ', ')

/**
 * «Работы по камерам» — что идёт на кадрах по двум сервисам аналитики коллеги: «по технике» (техника на кадре
 * и план) и «по снимку» (нейросеть смотрит кадр). Их ответы стоят рядом — победителя не выбираем. Сервер отправляет
 * кадры сам раз в 20 минут; руководитель и администратор могут отправить сейчас.
 */
export function WorkByCameras({ siteId, className }: { siteId: string; className?: string }) {
  const queryClient = useQueryClient()
  const { notify } = useApp()
  const key = ['work', siteId]
  const { data, isError, isPending, error, refetch } = useQuery({
    queryKey: key,
    queryFn: () => api.siteWork(siteId),
    // пока ждём ответы — чаще: «по технике» отвечает за секунды, «по снимку» — до нескольких минут
    refetchInterval: (query) => (waiting(query.state.data) ? 5_000 : 60_000),
  })
  const run = useMutation({
    mutationFn: () => api.runSiteWork(siteId),
    onSuccess: (fresh: SiteWork) => {
      queryClient.setQueryData(key, fresh)
      notify('Кадры отправлены сервисам — ответы появятся здесь')
    },
    onError: (error) => notify(error instanceof ApiError ? error.message : 'Не удалось отправить кадры', 'error'),
  })
  if (isPending && !data) {
    return (
      <section className={cn('bg-card rounded-xl border border-border shadow-[var(--shadow-card)] px-4 sm:px-5 py-4', className)} aria-busy>
        <h2 className="text-[18px] font-semibold">Работы по камерам</h2>
        <p role="status" className="mt-3 text-[15px] text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />Загружаем результаты анализа…</p>
      </section>
    )
  }
  if (!data) {
    return (
      <section className={cn('bg-card rounded-xl border border-border shadow-[var(--shadow-card)] px-4 sm:px-5 py-4', className)}>
        <h2 className="text-[18px] font-semibold">Работы по камерам</h2>
        <p role="alert" className="mt-3 rounded-lg bg-danger-bg text-danger-fg px-3 py-2 text-[14px]">{error instanceof ApiError ? error.message : 'Не удалось загрузить результаты анализа.'}</p>
        <Button className="mt-3" variant="outline" size="sm" onClick={() => { void refetch() }}><RefreshCw className="w-4 h-4" />Повторить загрузку</Button>
      </section>
    )
  }

  const analyticsEnabled = data.enabled
  const busy = analyticsEnabled && (data.running || run.isPending)
  const outlook = data.cameras
    .flatMap((c) => c.answers)
    .filter((a) => a.service === 'deterministic' && a.state === 'done')
    .sort((a, b) => (b.at ?? '').localeCompare(a.at ?? ''))[0]
  return (
    <section className={cn('bg-card rounded-xl border border-border shadow-[var(--shadow-card)] px-4 sm:px-5 py-4', className)} aria-busy={busy}>
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
        <div className="min-w-0">
          <h2 className="flex items-center gap-1.5 text-[18px] font-semibold">
            Работы по камерам
            <InfoTip label="Как определяются работы по камерам">
              Раз в 20 минут свежий кадр каждой камеры рабочей зоны уходит в два сервиса аналитики. «По технике»
              сверяет технику на кадре с планом и отметками выполнения, «по снимку» — нейросеть смотрит сам кадр. Это
              оценка, а не отметка выполнения: если она расходится с графиком — посмотрите камеры.
            </InfoTip>
          </h2>
          <p className="text-[14px] text-muted-foreground mt-0.5">
            {analyticsEnabled
              ? data.planned.length ? `По графику сегодня: ${data.planned.map((n) => `«${n}»`).join(', ')}` : 'По графику сегодня работ нет'
              : 'Сервисы аналитики по кадрам не подключены.'}
          </p>
        </div>
        {analyticsEnabled && data.canRun && (
          <Button variant="outline" size="sm" onClick={() => run.mutate()} disabled={busy}>
            {busy ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : <RefreshCw className="w-4 h-4" aria-hidden />}
            {busy ? 'Ждём ответы…' : 'Определить сейчас'}
          </Button>
        )}
      </div>

      <AnimatePresence initial={false}>
        {analyticsEnabled && data.running && (
          <m.p
            key="running" role="status" className="mt-3 text-[14px] text-muted-foreground"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}
          >
            Кадры отправлены. «По технике» отвечает за секунды, «по снимку» — до нескольких минут.
          </m.p>
        )}
      </AnimatePresence>

      {isError && (
        <p role="alert" className="mt-3 rounded-lg bg-warn-bg text-warn-fg px-3 py-2 text-[14px]">
          Не удалось обновить результаты. Показан сохранённый ответ, он может быть неактуален.
          <Button className="ml-2 align-baseline" variant="ghost" size="sm" onClick={() => { void refetch() }}>Повторить загрузку</Button>
        </p>
      )}

      {analyticsEnabled && <>
        {data.planIssue && (
          <p className="mt-3 rounded-lg bg-warn-bg text-warn-fg px-3 py-2 text-[14px]">
            План не уходит сервисам — они не сверяют кадры с графиком.
            {data.canRun && <> Причина: {data.planIssue}. Откройте план объекта и выберите вид работ по справочнику.</>}
          </p>
        )}
        {data.problem && data.canRun && (
          <p className="mt-3 rounded-lg bg-warn-bg text-warn-fg px-3 py-2 text-[14px]">Последний кадр не отправлен: {data.problem}</p>
        )}

        {data.cameras.length === 0 ? (
          <p className="mt-3 text-[15px] text-muted-foreground">На объекте нет камер рабочих зон — сервисам нечего отправлять.</p>
        ) : (
          <div className="mt-3 divide-y divide-border">
            {data.cameras.map((camera) => <CameraBlock key={camera.cameraId} camera={camera} canSeeErrors={data.canRun} />)}
          </div>
        )}

        {outlook && <Outlook answer={outlook} />}

        <p className="mt-3 text-[13px] text-muted-foreground">
          Оценка сервисов аналитики по кадрам, а не отметка выполнения
          {data.nextAt && <>. Следующая отправка — {fmtWhen(data.nextAt)}</>}
        </p>
      </>}

    </section>
  )
}

function waiting(data: SiteWork | undefined) {
  return !!data && (data.running || data.cameras.some((c) => c.answers.some((a) => a.state === 'pending' || a.newerPending)))
}

function CameraBlock({ camera, canSeeErrors }: { camera: CameraWork; canSeeErrors: boolean }) {
  const observed = camera.answers.find((a) => a.observedAt)?.observedAt
  return (
    <div className="py-3 first:pt-0 last:pb-0 flex gap-3 sm:gap-4">
      {camera.imageUrl && (
        <img
          src={mediaUrl(camera.imageUrl)} alt={`Кадр камеры «${camera.cameraName}»${observed ? ` от ${fmtTime(observed)}` : ''}`}
          className="hidden sm:block self-start w-36 aspect-video shrink-0 rounded-lg object-cover bg-slate-900" loading="lazy"
        />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-semibold">{camera.cameraName}</span>
          <span className="text-[14px] text-muted-foreground">{camera.zoneName}{observed && ` · кадр ${fmtWhen(observed)}`}</span>
          {camera.matchesPlan === true && <Badge tone="ok">Работа сопоставлена с планом</Badge>}
          {camera.matchesPlan === false && <Badge tone="warn">Работа не сопоставлена с планом</Badge>}
        </div>
        {camera.answers.length === 0 ? (
          <p className="mt-1.5 text-[15px] text-muted-foreground">Кадр этой камеры ещё не отправлялся.</p>
        ) : (
          <dl className="mt-1.5 space-y-2">
            {camera.answers.map((answer) => <Answer key={answer.service} answer={answer} canSeeErrors={canSeeErrors} />)}
          </dl>
        )}
      </div>
    </div>
  )
}

function Answer({ answer, canSeeErrors }: { answer: ServiceAnswer; canSeeErrors: boolean }) {
  const [open, setOpen] = useState(false)
  const details = answer.groups.flatMap((g) => g.evidence.map((e) => e.explanation)).filter(Boolean)
  const more = details.length > 0
  return (
    <div className="grid sm:grid-cols-[112px_1fr] gap-x-3">
      <dt className="text-[14px] text-muted-foreground pt-px">{SERVICE[answer.service]}</dt>
      <dd className="text-[15px] min-w-0">
        {answer.state === 'pending' && (
          <span className="inline-flex items-center gap-1.5 text-muted-foreground"><Loader2 className="w-4 h-4 animate-spin" aria-hidden />Ждём ответ…</span>
        )}
        {answer.state === 'error' && (
          <span className="text-danger">{refusal(answer.errorCode, 'Сервис не ответил')}{canSeeErrors && answer.error ? `: ${answer.error}` : ''}</span>
        )}
        {answer.state === 'unknown' && (
          <span className="text-warn">{refusal(answer.errorCode, 'Неизвестно, выполнен ли анализ')}{canSeeErrors && answer.error ? `: ${answer.error}` : ''}</span>
        )}
        {answer.state === 'done' && answer.outcome !== 'assessed' && (
          <span className={answer.outcome === 'outside_plan' ? 'text-warn' : 'text-muted-foreground'}>
            {OUTCOME[answer.outcome ?? ''] ?? 'Работу не назвал'}
          </span>
        )}
        {answer.groups.map((group, n) => (
          <p key={n}>
            {group.match === 'ambiguous' && <span className="text-muted-foreground">Одна из работ: </span>}
            <span className="font-medium">{works(group)}</span>
            {VISUAL[group.visualState] && <span className="text-muted-foreground"> — {VISUAL[group.visualState]}</span>}
          </p>
        ))}
        {answer.groups.length > 0 && answer.groups[0].explanation && (
          <p className="text-[14px] text-muted-foreground mt-0.5">{answer.groups[0].explanation}</p>
        )}
        {answer.newerPending && answer.state !== 'pending' && (
          <p className="text-[13px] text-muted-foreground mt-0.5">По новому кадру ждём ответ</p>
        )}
        {answer.resourceAssessment ? (
          <ResourceAssessment
            assessment={answer.resourceAssessment}
            analysisMode={answer.analysisMode}
            limitations={answer.limitations}
            evidence={answer.resourceEvidence}
          />
        ) : answer.limitations.length > 0 ? (
          <p className="mt-2 rounded-md bg-warn-bg px-2.5 py-2 text-[14px] text-warn-fg">Ограничения: {answer.limitations.join('; ')}</p>
        ) : null}
        {more && answer.state === 'done' && (
          <>
            <button
              type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)}
              className="mt-1 -ml-1 px-1 min-h-[32px] inline-flex items-center gap-1 text-[14px] text-primary font-medium cursor-pointer rounded hover:underline"
            >
              {open ? 'Скрыть' : 'Почему так'}
              <ChevronDown className={cn('w-4 h-4 transition-transform', open && 'rotate-180')} aria-hidden />
            </button>
            <AnimatePresence initial={false}>
              {open && (
                <m.div
                  className="overflow-hidden"
                  initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.18 }}
                >
                  <ul className="mt-1 space-y-0.5 text-[14px] text-muted-foreground list-disc pl-5">
                    {details.map((text, n) => <li key={`e${n}`}>{text}</li>)}
                  </ul>
                </m.div>
              )}
            </AnimatePresence>
          </>
        )}
      </dd>
    </div>
  )
}

/** Следующая работа и сроки — по последнему ответу «по технике»: он смотрит историю всего участка, а не один кадр */
function Outlook({ answer }: { answer: ServiceAnswer }) {
  const { transition, schedule } = answer
  const delays = schedule?.items.filter((i) => i.status === 'possible_delay') ?? []
  const start = transition?.status === 'possible_start' ? transition : null
  const next = transition?.next ? ` («${transition.next.name}»)` : ''
  const note = transition && !start ? TRANSITION[transition.status]?.(next) : undefined
  if (!start && !delays.length && !note && !schedule) return null
  return (
    <div className="mt-4 pt-3 border-t border-border space-y-2 text-[15px]">
      {start && (
        <p className="rounded-lg bg-info-bg text-info-fg px-3 py-2">
          Похоже, началась следующая работа «{start.next?.name}»: её техника видна{' '}
          {plural(start.points, 'раз', 'раза', 'раз')}
          {start.firstAt && start.lastAt && <> с {fmtWhen(start.firstAt)} по {fmtWhen(start.lastAt)}</>}. Если это так — отметьте начало в плане.
        </p>
      )}
      {delays.map((item) => (
        <p key={item.work.stepKey} className="rounded-lg bg-warn-bg text-warn-fg px-3 py-2">
          Возможное отставание: «{item.work.name}»{' '}
          {item.reason === 'actual_completion_after_deadline'
            ? <>закончена позже срока на {days(item.overdueS ?? 0)}.</>
            : <>— срок по плану прошёл, а по отметке{item.evidenceAt && <> от {fmtDate(item.evidenceAt)}</>} работа ещё не закончена
              {item.overdueS ? <> (позже срока не меньше чем на {days(item.overdueS)})</> : null}.</>}
        </p>
      ))}
      {note && <p className="text-muted-foreground">{note}</p>}
      {!delays.length && schedule && (
        <p className="text-muted-foreground">
          {schedule.status === 'no_delay_indicated'
            ? 'Сроки: по отметкам выполнения отставания не видно.'
            : 'Сроки: по отметкам выполнения вывод не сделать — нужны отметки о завершении работ.'}
        </p>
      )}
    </div>
  )
}
