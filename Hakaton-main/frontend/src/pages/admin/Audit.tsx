import { useEffect, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { ChevronDown, Loader2, RefreshCw, Search } from 'lucide-react'
import { api } from '@/api'
import { ROLES, type AuditEvent } from '@/data'
import { PageHeader } from '@/components/ui/PageHeader'
import { Chip } from '@/components/ui/Chip'
import { Button } from '@/components/ui/Button'
import { inputCls } from '@/components/ui/Field'
import { fmtDateShort, fmtTime, cn } from '@/lib/utils'

const PAGE = 50
const KINDS: [string, string][] = [
  ['', 'Все действия'], ['camera', 'Камеры'], ['site', 'Объекты'], ['stage', 'План'], ['zone', 'Зоны'],
  ['user', 'Сотрудники'], ['rule', 'Правила'], ['alert', 'Отклонения'], ['login', 'Входы'],
]
const FIELD_NAMES: Record<string, string> = {
  name: 'название', address: 'адрес', contractor: 'подрядчик', foreman: 'прораб', planProgress: 'план, %', factProgress: 'факт, %',
  enabled: 'включена', zone: 'зона', role: 'роль', phone: 'телефон', sites: 'объекты', active: 'доступ', start: 'начало', end: 'окончание',
  rule: 'правило', kind: 'вид', comment: 'комментарий', required: 'нужная техника', unexpected: 'лишняя техника',
  confirmAfterSnapshots: 'проверок подряд', allowed: 'разрешённая техника',
}

/** Журнал действий: кто, когда и что сделал — например, кто удалил камеру или сменил пароль */
export function AdminAudit() {
  const [kind, setKind] = useState('')
  const [actor, setActor] = useState('')
  const [text, setText] = useState('')
  const [q, setQ] = useState('')
  useEffect(() => { // ищем, когда человек перестал печатать
    const timer = window.setTimeout(() => setQ(text.trim()), 350)
    return () => window.clearTimeout(timer)
  }, [text])
  const users = useQuery({ queryKey: ['users'], queryFn: api.users })

  const log = useInfiniteQuery({
    queryKey: ['audit', kind, actor, q],
    queryFn: ({ pageParam }) => api.audit({ action: kind || undefined, actor: actor || undefined, q: q || undefined, beforeId: pageParam, limit: PAGE }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1].id : undefined),
  })
  const events = log.data?.pages.flat() ?? []

  return (
    <div>
      <PageHeader
        title="Журнал действий" info="Кто, когда и что сделал в системе: камеры, объекты, план, сотрудники, пароли, ответы на отклонения, входы. Сами пароли в журнал не попадают."
        action={<Button variant="outline" onClick={() => void log.refetch()}><RefreshCw className="w-5 h-5" /> Обновить</Button>}
      />
      <div className="flex flex-wrap gap-2 mb-3" role="group" aria-label="Что показывать">
        {KINDS.map(([key, label]) => <Chip key={key || 'all'} active={kind === key} onClick={() => setKind(key)} small>{label}</Chip>)}
      </div>
      <div className="grid sm:grid-cols-[260px_1fr] gap-3 mb-5">
        <label className="block">
          <span className="sr-only">Кто действовал</span>
          <select value={actor} onChange={(e) => setActor(e.target.value)} className={inputCls}>
            <option value="">Все сотрудники</option>
            {users.data?.map((u) => <option key={u.id} value={u.login}>{u.name}</option>)}
          </select>
        </label>
        <label className="relative block">
          <span className="sr-only">Поиск по журналу</span>
          <Search className="w-5 h-5 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <input type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder="Поиск: название камеры, объекта, фамилия…" className={cn(inputCls, 'pl-10')} />
        </label>
      </div>

      {log.isPending && <p className="text-muted-foreground">Загружаем журнал…</p>}
      {log.isError && <p role="alert" className="text-danger">Не удалось загрузить журнал. Обновите страницу.</p>}
      {log.isSuccess && events.length === 0 && <p className="text-muted-foreground">Записей по этим условиям нет.</p>}

      <ol className="bg-card rounded-xl border border-border divide-y divide-border overflow-hidden">
        {events.map((e) => <EventRow key={e.id} event={e} />)}
      </ol>
      {log.hasNextPage && (
        <div className="mt-4">
          <Button variant="outline" size="lg" onClick={() => void log.fetchNextPage()} disabled={log.isFetchingNextPage}>
            {log.isFetchingNextPage && <Loader2 className="w-5 h-5 animate-spin" />} Показать ещё
          </Button>
        </div>
      )}
    </div>
  )
}

function EventRow({ event: e }: { event: AuditEvent }) {
  const [open, setOpen] = useState(false)
  const changes = Object.entries(e.details ?? {})
  const role = ROLES.find((r) => r.id === e.actorRole)?.title
  const failed = e.action === 'login.failed'
  return (
    <li className="px-4 py-3 flex flex-col sm:flex-row gap-x-5 gap-y-1">
      <div className="sm:w-[120px] shrink-0 tabular text-[14px] text-muted-foreground">
        <span className="text-foreground font-medium">{fmtDateShort(e.at)}</span> {fmtTime(e.at)}
      </div>
      <div className="min-w-0 flex-1">
        <div className={cn('text-[15px]', failed && 'text-danger')}>
          <b>{e.actorName || e.actorLogin || 'Неизвестный'}</b>
          {role && <span className="text-muted-foreground"> · {role}</span>}
          <span> — {e.summary}</span>
        </div>
        {changes.length > 0 && (
          <>
            <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="mt-1 inline-flex items-center gap-1 text-[14px] text-primary font-medium min-h-[44px] cursor-pointer">
              <ChevronDown className={cn('w-4 h-4 transition-transform', open && 'rotate-180')} /> {open ? 'Скрыть подробности' : 'Что изменилось'}
            </button>
            {open && (
              <dl className="mt-1 grid sm:grid-cols-[180px_1fr] gap-x-4 gap-y-1 text-[14px]">
                {changes.map(([key, value]) => (
                  <div key={key} className="contents">
                    <dt className="text-muted-foreground">{FIELD_NAMES[key] ?? key}</dt>
                    <dd className="break-words">{describe(value)}</dd>
                  </div>
                ))}
              </dl>
            )}
          </>
        )}
      </div>
      {e.ip && <div className="text-[13px] text-muted-foreground font-mono shrink-0" title="Адрес, с которого действовали">{e.ip}</div>}
    </li>
  )
}

/** Изменение хранится как [было, стало] — показываем «было → стало» */
function describe(value: unknown): string {
  const show = (v: unknown) => (v === null || v === undefined || v === '' ? '—' : typeof v === 'boolean' ? (v ? 'да' : 'нет') : typeof v === 'object' ? JSON.stringify(v) : String(v))
  return Array.isArray(value) && value.length === 2 ? `${show(value[0])} → ${show(value[1])}` : show(value)
}
