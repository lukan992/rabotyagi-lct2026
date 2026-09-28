import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2, Pencil, Plus, Trash2 } from 'lucide-react'
import { api } from '@/api'
import type { AnalyticsCatalog, CatalogWork, Site, Stage, StageInput } from '@/data'
import { useApp } from '@/store/context'
import { fmtDate } from '@/lib/utils'
import { STAGE_STATUS } from '@/lib/labels'
import { Button } from '../ui/Button'
import { Modal } from '../ui/Modal'
import { Field, inputCls } from '../ui/Field'
import { Badge } from '../ui/Badge'

/** Календарный план объекта: этапы и работы в них, даты, правило «этап → техника», выполнение по факту и вид работ
 *  по справочнику сервисов аналитики (если они подключены) */
export function PlanDialog({ site, onClose }: { site: Site | null; onClose: () => void }) {
  return (
    <Modal open={!!site} onClose={onClose} title={site ? `План работ: ${site.name}` : ''} wide>
      {site && <Plan site={site} />}
    </Modal>
  )
}

function Plan({ site }: { site: Site }) {
  const { stagesOf, rules, run } = useApp()
  const stages = stagesOf(site.id)
  // справочник сервисов аналитики: сервисы не подключены — поля «вид работ по справочнику» нет
  const { data: catalog } = useQuery({
    queryKey: ['analytics-catalog', site.id], queryFn: () => api.analyticsCatalog(site.id), staleTime: 5 * 60_000,
  })
  const phases = stages.filter((s) => s.level === 1)
  const [editing, setEditing] = useState<string | null>(null) // id этапа или 'new-phase' / 'new-work:<id этапа>'
  const [removing, setRemoving] = useState<Stage | null>(null)
  const [deleting, setDeleting] = useState(false)  // двойное нажатие не шлёт второй DELETE
  const save = async (stage: Stage | null, input: StageInput) => {
    const ok = await run(() => (stage ? api.updateStage(stage.id, input) : api.createStage(site.id, input)), stage ? 'Этап сохранён' : 'Этап добавлен в план')
    if (ok) setEditing(null)
    return ok
  }

  return (
    <div className="space-y-4">
      {phases.length === 0 && <p className="text-muted-foreground">План пуст — добавьте первый этап.</p>}
      {phases.map((phase) => {
        const works = stages.filter((s) => s.parentId === phase.id)
        return (
          <section key={phase.id} className="rounded-xl border border-border overflow-hidden">
            <div className="bg-muted/50 px-4 py-3">
              {editing === phase.id ? (
                <StageForm stage={phase} level={1} onSave={(input) => save(phase, input)} onCancel={() => setEditing(null)} />
              ) : (
                <div className="flex flex-wrap items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="font-semibold text-[17px]">{phase.name}</div>
                    <div className="text-[14px] text-muted-foreground">{fmtDate(phase.start)} — {fmtDate(phase.end)}</div>
                  </div>
                  <Badge tone={STAGE_STATUS[phase.status].tone}>{STAGE_STATUS[phase.status].label}</Badge>
                  <Button variant="outline" size="sm" onClick={() => setEditing(phase.id)}><Pencil className="w-4 h-4" /> Изменить</Button>
                  <Button variant="ghost" size="sm" aria-label={`Удалить этап ${phase.name}`} onClick={() => setRemoving(phase)}><Trash2 className="w-4 h-4" /></Button>
                </div>
              )}
            </div>
            <ul className="divide-y divide-border">
              {works.map((work) => (
                <li key={work.id} className="px-4 py-3">
                  {editing === work.id ? (
                    <StageForm stage={work} level={2} parentId={phase.id} catalog={catalog} onSave={(input) => save(work, input)} onCancel={() => setEditing(null)} />
                  ) : (
                    <div className="flex flex-wrap items-center gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="font-semibold">{work.name}</div>
                        <div className="text-[14px] text-muted-foreground">
                          {fmtDate(work.start)} — {fmtDate(work.end)} · сделано {work.factProgress}% · правило: {work.ruleKey ? rules[work.ruleKey]?.stageName ?? work.ruleKey : 'не задано'}
                        </div>
                        {catalog?.enabled && <CatalogNote work={work} catalog={catalog} />}
                      </div>
                      <Badge tone={STAGE_STATUS[work.status].tone}>{STAGE_STATUS[work.status].label}</Badge>
                      <Button variant="outline" size="sm" onClick={() => setEditing(work.id)}><Pencil className="w-4 h-4" /> Изменить</Button>
                      <Button variant="ghost" size="sm" aria-label={`Удалить работу ${work.name}`} onClick={() => setRemoving(work)}><Trash2 className="w-4 h-4" /></Button>
                    </div>
                  )}
                </li>
              ))}
              <li className="px-4 py-3">
                {editing === `new-work:${phase.id}` ? (
                  <StageForm level={2} parentId={phase.id} catalog={catalog} defaults={{ start: phase.start, end: phase.end }} onSave={(input) => save(null, input)} onCancel={() => setEditing(null)} />
                ) : (
                  <Button variant="ghost" size="sm" onClick={() => setEditing(`new-work:${phase.id}`)}><Plus className="w-4 h-4" /> Добавить работы в этот этап</Button>
                )}
              </li>
            </ul>
          </section>
        )
      })}
      {editing === 'new-phase' ? (
        <div className="rounded-xl border border-border p-4">
          <StageForm level={1} onSave={(input) => save(null, input)} onCancel={() => setEditing(null)} />
        </div>
      ) : (
        <Button variant="outline" size="lg" onClick={() => setEditing('new-phase')}><Plus className="w-5 h-5" /> Добавить этап</Button>
      )}

      <Modal open={!!removing} onClose={() => setRemoving(null)} title="Удалить из плана?">
        {removing && (
          <div className="space-y-5">
            <p>
              {removing.level === 1
                ? `Этап «${removing.name}» удалится вместе со всеми его работами.`
                : `Работа «${removing.name}» удалится из плана.`}{' '}
              Отклонения, найденные раньше, останутся в истории.
            </p>
            <div className="flex flex-wrap gap-3">
              <Button
                variant="danger" size="lg" disabled={deleting}
                onClick={async () => {
                  setDeleting(true)
                  const ok = await run(() => api.deleteStage(removing.id), 'Удалено из плана')
                  setDeleting(false)
                  if (ok) setRemoving(null)
                }}
              >
                {deleting && <Loader2 className="w-5 h-5 animate-spin" />} Удалить
              </Button>
              <Button variant="outline" size="lg" onClick={() => setRemoving(null)}>Отмена</Button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}

/** Какой вид работ по справочнику выбран у работы — строкой под её датами */
function CatalogNote({ work, catalog }: { work: Stage; catalog: AnalyticsCatalog }) {
  if (work.catalogStageId === null) {
    return <div className="text-[14px] text-warn">Вид работ по справочнику не выбран — план не уходит сервисам аналитики</div>
  }
  const chosen = catalog.works.find((w) => w.stageId === work.catalogStageId)
  if (catalog.version && work.catalogVersion !== catalog.version) {
    return <div className="text-[14px] text-warn">Вид работ выбран по прежней версии справочника — проверьте и сохраните работу</div>
  }
  if (!chosen) return <div className="text-[14px] text-warn">По справочнику: вид работ {work.catalogStageId} — не подходит этому объекту</div>
  return (
    <div className="text-[14px] text-muted-foreground">
      По справочнику: «{chosen.name}»{chosen.kind === 'no_class' && ' — без техники: по кадрам не видна, следим только за сроками'}
    </div>
  )
}

/** Виды работ по разделам справочника — для списка выбора */
function bySection(works: CatalogWork[]): [string, CatalogWork[]][] {
  const sections = new Map<string, CatalogWork[]>()
  for (const work of works) {
    const title = work.path.join(' › ') || 'Без раздела'
    sections.set(title, [...(sections.get(title) ?? []), work])
  }
  return [...sections]
}

function StageForm({ stage, level, parentId, defaults, catalog, onSave, onCancel }: {
  stage?: Stage; level: 1 | 2; parentId?: string; defaults?: { start: string; end: string }; catalog?: AnalyticsCatalog
  onSave: (input: StageInput) => Promise<boolean>; onCancel: () => void
}) {
  const { rules } = useApp()
  const [name, setName] = useState(stage?.name ?? '')
  const [start, setStart] = useState(stage?.start ?? defaults?.start ?? '')
  const [end, setEnd] = useState(stage?.end ?? defaults?.end ?? '')
  const [ruleKey, setRuleKey] = useState(stage?.ruleKey ?? '')
  // процент — строкой, пока его набирают: иначе поле нельзя было очистить, чтобы ввести новое число
  const [fact, setFact] = useState(String(stage?.factProgress ?? 0))
  const [catalogId, setCatalogId] = useState(stage?.catalogStageId != null ? String(stage.catalogStageId) : '')
  const withCatalog = level === 2 && !!catalog?.enabled
  const [tried, setTried] = useState(false)
  const [busy, setBusy] = useState(false)
  const error = name.trim().length < 2 ? 'Введите название' : !start || !end ? 'Укажите даты' : end < start ? 'Окончание раньше начала' : undefined

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setTried(true)
    if (error || busy) return
    setBusy(true)
    await onSave({
      name: name.trim(), level, parentId: parentId ?? null, start, end, ruleKey: level === 2 ? ruleKey || null : null, factProgress: clampPercent(fact),
      // без подключённых сервисов поле не отправляем — выбранный раньше вид работ сохраняется
      ...(withCatalog ? { catalogStageId: catalogId ? Number(catalogId) : null } : {}),
    })
    setBusy(false)
  }

  return (
    <form onSubmit={submit} noValidate className="grid sm:grid-cols-2 gap-3">
      <Field label={level === 1 ? 'Название этапа' : 'Название работ'} className="sm:col-span-2" error={tried ? error : undefined}>
        {(id, d) => <input id={id} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} aria-describedby={d} className={inputCls} placeholder={level === 1 ? 'Земляные работы' : 'Разработка котлована'} />}
      </Field>
      <Field label="Начало">{(id) => <input id={id} type="date" value={start} onChange={(e) => setStart(e.target.value)} className={inputCls} />}</Field>
      <Field label="Окончание">{(id) => <input id={id} type="date" value={end} onChange={(e) => setEnd(e.target.value)} className={inputCls} />}</Field>
      <Field label="Сделано по факту, %" className="sm:col-span-2" hint={level === 1 ? 'Если у этапа есть работы, выполнение считается по ним' : 'Прораб отмечает это и сам — на странице плана'}>
        {(id, d) => <input id={id} type="number" min={0} max={100} step={5} value={fact} onChange={(e) => setFact(e.target.value)} onBlur={() => setFact(String(clampPercent(fact)))} aria-describedby={d} className={inputCls} />}
      </Field>
      {level === 2 && (
        <Field label="Правило «этап → техника»" className="sm:col-span-2" hint="По нему система решает, какая техника должна быть на площадке">
          {(id, d) => (
            <select id={id} value={ruleKey} onChange={(e) => setRuleKey(e.target.value)} aria-describedby={d} className={inputCls}>
              <option value="">— без сверки техники —</option>
              {Object.values(rules).map((r) => <option key={r.key} value={r.key}>{r.stageName}</option>)}
            </select>
          )}
        </Field>
      )}
      {withCatalog && catalog && (
        <Field
          label="Вид работ по справочнику" className="sm:col-span-2"
          hint={catalog.error ?? 'По нему сервисы аналитики сверяют кадры с планом. Выберите, что делается на самом деле: по похожему названию система сама не подставляет. Работы без техники (геодезия, отселение) по кадрам не видны — по ним сервисы следят только за сроками'}
        >
          {(id, d) => (
            <select id={id} value={catalogId} onChange={(e) => setCatalogId(e.target.value)} aria-describedby={d} className={inputCls} disabled={!catalog.works.length}>
              <option value="">— не выбран: план не уйдёт сервисам —</option>
              {catalogId && !catalog.works.some((w) => String(w.stageId) === catalogId) && (
                <option value={catalogId}>Вид работ {catalogId} — нет в справочнике для этого объекта</option>
              )}
              {bySection(catalog.works).map(([title, works]) => (
                <optgroup key={title} label={title}>
                  {works.map((w) => <option key={w.stageId} value={w.stageId}>{w.name}{w.kind === 'no_class' && ' — без техники, только сроки'}</option>)}
                </optgroup>
              ))}
            </select>
          )}
        </Field>
      )}
      <div className="sm:col-span-2 flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={busy}>{busy && <Loader2 className="w-4 h-4 animate-spin" />} Сохранить</Button>
        <Button type="button" variant="outline" size="sm" onClick={onCancel}>Отмена</Button>
      </div>
    </form>
  )
}

const clampPercent = (value: string) => Math.min(100, Math.max(0, Math.round(Number(value) || 0)))
