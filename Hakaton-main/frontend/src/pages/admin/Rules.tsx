import { useId, useRef, useState, type FormEvent } from 'react'
import { useReducedMotion } from 'framer-motion'
import { Plus, Minus, Trash2, ChevronDown, Save, Loader2 } from 'lucide-react'
import { api } from '@/api'
import { EQUIPMENT, EQUIPMENT_LIST, type EquipmentType, type Rule } from '@/data'
import { useApp } from '@/store/context'
import { PageHeader } from '@/components/ui/PageHeader'
import { InfoTip } from '@/components/ui/InfoTip'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { Field, inputCls } from '@/components/ui/Field'
import { VehicleIcon } from '@/components/VehicleIcon'
import { cn, pluralWord } from '@/lib/utils'

/**
 * Редактор методики «этап → техника»: правила добавляют, переименовывают и удаляют здесь же.
 * Технику — на кнопках «+ / −», без ввода кода и формул.
 */
export function AdminRules() {
  const { rules, saveRule } = useApp()
  const list = Object.values(rules) as Rule[]
  const [openKey, setOpenKey] = useState<string | null>('excavation')
  const [saved, setSaved] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [removing, setRemoving] = useState<Rule | null>(null)
  // новое правило появляется в конце списка — прокручиваем к нему, как только его карточка появится на странице
  const scrollTo = useRef<string | null>(null)
  const reduce = useReducedMotion()
  const created = (key: string) => {
    scrollTo.current = key
    setOpenKey(key)
  }

  // после сохранения сервер сразу пересверяет объекты, где идёт этап с этим правилом
  const save = async (key: string, rule: Rule) => {
    if (await saveRule(rule)) {
      setSaved(key)
      setTimeout(() => setSaved(null), 1800)
    }
  }

  return (
    <div>
      <PageHeader
        title="Правила: этап → техника"
        info="По этим правилам система решает, есть ли отклонение: какая техника нужна на этапе, сколько её и какая лишняя. Правило выбирают у работы в плане объекта. Меняйте цифры кнопками и нажимайте «Сохранить»."
        action={<Button size="lg" onClick={() => setCreating(true)}><Plus className="w-5 h-5" /> Добавить правило</Button>}
      />
      {list.length === 0 && (
        <div className="bg-card rounded-xl border border-border p-5">
          <p className="font-semibold text-[17px]">Правил пока нет</p>
          <p className="text-muted-foreground">Без правил работы плана не с чем сверять: добавьте правило для каждого этапа, на котором важна техника.</p>
        </div>
      )}
      <div className="space-y-3">
        {list.map((r) => (
          <div
            key={r.key} className="bg-card rounded-xl border border-border overflow-hidden scroll-mt-4"
            ref={(el) => {
              if (!el || scrollTo.current !== r.key) return
              scrollTo.current = null
              el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
            }}
          >
            <button
              type="button" onClick={() => setOpenKey(openKey === r.key ? null : r.key)}
              className="w-full text-left px-4 py-4 flex items-center gap-3 cursor-pointer hover:bg-muted/40 transition-colors"
              aria-expanded={openKey === r.key}
            >
              <div className="flex-1 min-w-0">
                <div className="font-semibold text-lg">{r.stageName}</div>
                {r.description && <div className="text-muted-foreground text-[14px]">{r.description}</div>}
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {r.required.map((q) => (
                    <span key={q.type} className="inline-flex items-center gap-1 bg-muted rounded-sm pl-1 pr-2.5 py-0.5 text-[13px] font-semibold">
                      <VehicleIcon type={q.type} className="w-6 h-4" fill={EQUIPMENT[q.type].color} /> {EQUIPMENT[q.type].name} ≥{q.min}
                    </span>
                  ))}
                  {r.required.length === 0 && <span className="text-muted-foreground text-[13px]">Нужная техника не указана</span>}
                </div>
              </div>
              <ChevronDown className={cn('w-6 h-6 text-muted-foreground transition-transform', openKey === r.key && 'rotate-180')} />
            </button>
              {openKey === r.key && (
                <div className="overflow-hidden">
                  <RuleEditor rule={r} others={list.filter((o) => o.key !== r.key)} onSave={(nr) => save(r.key, nr)} onDelete={() => setRemoving(r)} saved={saved === r.key} />
                </div>
              )}
          </div>
        ))}
      </div>
      <Modal open={creating} onClose={() => setCreating(false)} title="Новое правило">
        {creating && <NewRuleForm rules={list} onClose={() => setCreating(false)} onCreated={created} />}
      </Modal>
      <DeleteRuleDialog rule={removing} onClose={() => setRemoving(null)} />
    </div>
  )
}

/** Название этапа так, как его сохранит сервер: без лишних пробелов. Совпадение с другим правилом — без учёта регистра */
const cleanName = (name: string) => name.trim().replace(/\s+/g, ' ')
function nameError(name: string, others: Rule[]): string | undefined {
  const clean = cleanName(name)
  if (clean.length < 2) return 'Введите название этапа'
  const same = others.find((o) => o.stageName.toLocaleLowerCase('ru') === clean.toLocaleLowerCase('ru'))
  return same ? `Правило «${same.stageName}» уже есть` : undefined
}

function NewRuleForm({ rules, onClose, onCreated }: { rules: Rule[]; onClose: () => void; onCreated: (key: string) => void }) {
  const { run } = useApp()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const error = touched ? nameError(name, rules) : undefined

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (nameError(name, rules)) return
    setBusy(true)
    let created: Rule | undefined
    const ok = await run(
      async () => { created = await api.createRule({ stageName: cleanName(name), description: description.trim() }) },
      `Правило «${cleanName(name)}» добавлено — укажите, какая техника нужна`,
    )
    setBusy(false)
    if (!ok || !created) return
    onCreated(created.key)  // сразу открываем его редактор: технику задают там
    onClose()
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <p className="text-muted-foreground">Технику — какая нужна на этапе и какая лишняя — укажете сразу после, в редакторе правила.</p>
      <Field label="Название этапа" error={error}>
        {(id, d) => <input id={id} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} onBlur={() => name && setTouched(true)} aria-invalid={!!error} aria-describedby={d} className={inputCls} placeholder="Монтаж наружных сетей" />}
      </Field>
      <Field label="Что входит в этап" hint="Необязательно. Коротко — чтобы было понятно, для каких работ плана это правило">
        {(id, d) => <textarea id={id} value={description} maxLength={1000} rows={3} onChange={(e) => setDescription(e.target.value)} aria-describedby={d} className={cn(inputCls, 'py-2.5')} placeholder="Траншеи, укладка труб, обратная засыпка" />}
      </Field>
      <div className="flex flex-wrap gap-3 pt-2">
        <Button type="submit" size="lg" disabled={busy}>{busy && <Loader2 className="w-5 h-5 animate-spin" />} Добавить правило</Button>
        <Button type="button" variant="outline" size="lg" onClick={onClose}>Отмена</Button>
      </div>
    </form>
  )
}

/** Удалить правило можно, только если его не выбрали ни у одной работы плана: иначе работа перестала бы сверяться */
function DeleteRuleDialog({ rule, onClose }: { rule: Rule | null; onClose: () => void }) {
  const { stages, bySite, run } = useApp()
  const [busy, setBusy] = useState(false)
  const works = rule ? stages.filter((s) => s.ruleKey === rule.key) : []
  return (
    <Modal open={!!rule} onClose={onClose} title={works.length ? 'Правило используется' : 'Удалить правило?'}>
      {rule && (works.length ? (
        <div className="space-y-5">
          <p>Правило «{rule.stageName}» выбрано у {works.length} {pluralWord(works.length, 'работы', 'работ', 'работ')} в планах объектов:</p>
          <ul className="list-disc pl-6 space-y-1">
            {works.slice(0, 6).map((w) => <li key={w.id}>{w.name} — {bySite(w.siteId)?.name}</li>)}
            {works.length > 6 && <li>и ещё {works.length - 6}</li>}
          </ul>
          <p className="text-muted-foreground text-[15px]">Без правила работа перестанет сверяться с техникой. Сначала выберите у этих работ другое правило в плане объекта — потом это можно будет удалить.</p>
          <Button variant="outline" size="lg" onClick={onClose}>Понятно</Button>
        </div>
      ) : (
        <div className="space-y-5">
          <p>Правило «{rule.stageName}» удалится. Ни у одной работы в планах объектов оно не выбрано — на сверку это не повлияет.</p>
          <p className="text-muted-foreground text-[15px]">Отклонения, найденные по нему раньше, останутся в истории.</p>
          <div className="flex flex-wrap gap-3">
            <Button
              variant="danger" size="lg" disabled={busy}
              onClick={async () => {
                setBusy(true)
                const ok = await run(() => api.deleteRule(rule.key), `Правило «${rule.stageName}» удалено`)
                setBusy(false)
                if (ok) onClose()
              }}
            >
              {busy && <Loader2 className="w-5 h-5 animate-spin" />} Удалить правило
            </Button>
            <Button variant="outline" size="lg" onClick={onClose}>Отмена</Button>
          </div>
        </div>
      ))}
    </Modal>
  )
}

function RuleEditor({ rule, others, onSave, onDelete, saved }: {
  rule: Rule; others: Rule[]; onSave: (r: Rule) => Promise<void>; onDelete: () => void; saved: boolean
}) {
  const { meta } = useApp()
  // техника, которую модель на сервере не распознаёт: её «отсутствие» не станет отклонением — проверяют на месте
  const unseen = (t: EquipmentType) => !!meta && !meta.detectableEquipment.includes(t)
  const [draft, setDraft] = useState<Rule>(rule)
  const [saving, setSaving] = useState(false)  // двойное нажатие не шлёт правило дважды
  const used = new Set<EquipmentType>([...draft.required.map((r) => r.type), ...draft.unexpected.map((u) => u.type)])
  const free = EQUIPMENT_LIST.filter((e) => !used.has(e.type))

  // пределы те же, что проверяет сервер: иначе «Сохранить» падало бы с невнятной ошибкой
  const setMin = (t: EquipmentType, d: number) => setDraft({ ...draft, required: draft.required.map((r) => r.type === t ? { ...r, min: clamp(r.min + d, 1, MAX_MIN) } : r) })
  const removeReq = (t: EquipmentType) => setDraft({ ...draft, required: draft.required.filter((r) => r.type !== t) })
  const addReq = (t: EquipmentType) => setDraft({ ...draft, required: [...draft.required, { type: t, min: 1, why: 'Добавлено администратором', risk: '' }] })
  const removeUnexp = (t: EquipmentType) => setDraft({ ...draft, unexpected: draft.unexpected.filter((u) => u.type !== t) })
  const addUnexp = (t: EquipmentType) => setDraft({ ...draft, unexpected: [...draft.unexpected, { type: t, why: 'Не предусмотрено этапом', risk: '' }] })

  const nameProblem = nameError(draft.stageName, others)

  return (
    <div className="border-t border-border p-4 sm:p-5 grid grid-cols-1 lg:grid-cols-2 gap-5">
      <Field label="Название этапа" error={nameProblem}>
        {(id, d) => <input id={id} value={draft.stageName} maxLength={200} onChange={(e) => setDraft({ ...draft, stageName: e.target.value })} aria-invalid={!!nameProblem} aria-describedby={d} className={inputCls} />}
      </Field>
      <Field label="Что входит в этап">
        {(id) => <input id={id} value={draft.description} maxLength={1000} onChange={(e) => setDraft({ ...draft, description: e.target.value })} className={inputCls} />}
      </Field>

      <section>
        <div className="flex items-center gap-2 mb-3">
          <h3 className="font-semibold">Нужная техника</h3>
          <InfoTip label="Что значит «нужная техника»">Если её нет на площадке или меньше, чем указано, — система сообщит.</InfoTip>
        </div>
        <ul className="space-y-2">
          {draft.required.map((r) => (
            <li key={r.type} className="flex items-center gap-3 bg-muted/50 rounded-lg p-2 pr-3">
              <VehicleIcon type={r.type} className="w-12 h-8 shrink-0" fill={EQUIPMENT[r.type].color} />
              <div className="flex-1 min-w-0">
                <div className="font-semibold">{EQUIPMENT[r.type].name}</div>
                {unseen(r.type)
                  ? <div className="text-warn text-[13px]">Модель пока не распознаёт — отклонений по ней не будет, проверяют на месте</div>
                  : <div className="text-muted-foreground text-[13px] truncate">{r.why}</div>}
              </div>
              <div className="flex items-center gap-1">
                <Btn onClick={() => setMin(r.type, -1)} disabled={r.min <= 1} label={`Уменьшить ${EQUIPMENT[r.type].name}`}><Minus className="w-5 h-5" /></Btn>
                <span className="w-10 text-center text-[18px] font-semibold">{r.min}</span>
                <Btn onClick={() => setMin(r.type, 1)} disabled={r.min >= MAX_MIN} label={`Увеличить ${EQUIPMENT[r.type].name}`}><Plus className="w-5 h-5" /></Btn>
              </div>
              <Btn onClick={() => removeReq(r.type)} label="Убрать" danger><Trash2 className="w-5 h-5" /></Btn>
            </li>
          ))}
        </ul>
        <AddPicker free={free.map((e) => (unseen(e.type) ? { ...e, name: `${e.name} — модель не распознаёт` } : e))} onPick={addReq} label="Добавить нужную технику" />
      </section>

      <section>
        <div className="flex items-center gap-2 mb-3">
          <h3 className="font-semibold">Лишняя техника</h3>
          <InfoTip label="Что значит «лишняя техника»">Если такая техника появится на площадке на этом этапе, — система предупредит.</InfoTip>
        </div>
        <ul className="space-y-2">
          {draft.unexpected.map((u) => (
            <li key={u.type} className="flex items-center gap-3 bg-warn-bg/40 rounded-lg p-2 pr-3">
              <VehicleIcon type={u.type} className="w-12 h-8 shrink-0" fill={EQUIPMENT[u.type].color} />
              <div className="flex-1 min-w-0"><div className="font-semibold">{EQUIPMENT[u.type].name}</div><div className="text-muted-foreground text-[13px] truncate">{u.why}</div></div>
              <Btn onClick={() => removeUnexp(u.type)} label="Убрать" danger><Trash2 className="w-5 h-5" /></Btn>
            </li>
          ))}
        </ul>
        <AddPicker free={free} onPick={addUnexp} label="Добавить лишнюю технику" />
      </section>

      <div className="lg:col-span-2 flex flex-wrap items-center gap-3 pt-2 border-t border-border">
        {/* не <label>: иначе нажатие на подпись срабатывало как «−» и незаметно ослабляло правило */}
        <div role="group" aria-label="Сообщать после скольких проверок подряд" className="flex items-center gap-3">
          <span className="font-semibold">Сообщать после</span>
          <Btn onClick={() => setDraft({ ...draft, confirmAfterSnapshots: clamp(draft.confirmAfterSnapshots - 1, 1, MAX_CONFIRM) })} disabled={draft.confirmAfterSnapshots <= 1} label="Меньше проверок"><Minus className="w-5 h-5" /></Btn>
          <span className="w-8 text-center text-[18px] font-semibold">{draft.confirmAfterSnapshots}</span>
          <Btn onClick={() => setDraft({ ...draft, confirmAfterSnapshots: clamp(draft.confirmAfterSnapshots + 1, 1, MAX_CONFIRM) })} disabled={draft.confirmAfterSnapshots >= MAX_CONFIRM} label="Больше проверок"><Plus className="w-5 h-5" /></Btn>
          <span className="text-muted-foreground">проверок подряд</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-3">
          {saved && <span className="text-ok font-semibold">Сохранено</span>}
          <Button variant="ghost" size="lg" className="text-danger hover:text-danger hover:bg-danger-bg" onClick={onDelete}>
            <Trash2 className="w-5 h-5" /> Удалить правило
          </Button>
          <Button
            size="lg" disabled={saving || !!nameProblem}
            onClick={async () => {
              setSaving(true)
              await onSave({ ...draft, stageName: cleanName(draft.stageName), description: draft.description.trim() })
              setSaving(false)
            }}
          >
            {saving ? <Loader2 className="w-5 h-5 animate-spin" /> : <Save className="w-5 h-5" />} Сохранить
          </Button>
        </div>
      </div>
    </div>
  )
}

const MAX_MIN = 50 // не больше 50 единиц одного типа техники
const MAX_CONFIRM = 24 // не больше 24 проверок подряд
const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n))

function Btn({ onClick, label, danger, disabled, children }: { onClick: () => void; label: string; danger?: boolean; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} aria-label={label} className={cn('w-11 h-11 rounded-lg border flex items-center justify-center cursor-pointer transition-colors disabled:opacity-50 disabled:cursor-not-allowed', danger ? 'border-transparent text-danger hover:bg-danger-bg' : 'border-border bg-card enabled:hover:border-primary enabled:hover:text-primary')}>
      {children}
    </button>
  )
}

function AddPicker({ free, onPick, label }: { free: { type: EquipmentType; name: string }[]; onPick: (t: EquipmentType) => void; label: string }) {
  const id = useId()
  if (!free.length) return null
  return (
    <div className="mt-3">
      <label htmlFor={id} className="block text-[14px] font-semibold mb-1">{label}</label>
      <select
        id={id} className="w-full min-h-[48px] rounded-lg border border-border bg-card px-3 text-[16px]"
        value="" onChange={(e) => e.target.value && onPick(e.target.value as EquipmentType)}
      >
        <option value="">Выберите технику…</option>
        {free.map((e) => <option key={e.type} value={e.type}>{e.name}</option>)}
      </select>
    </div>
  )
}
