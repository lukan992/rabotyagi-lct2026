import { useState } from 'react'
import { FileWarning } from 'lucide-react'
import { EQUIPMENT, type Alert } from '@/data'
import { useApp } from '@/store/context'
import { cn, daysBetween, fmtDate, plural, todayISO } from '@/lib/utils'
import { Modal } from './ui/Modal'
import { Button } from './ui/Button'
import { Field, inputCls } from './ui/Field'

/** Срок по умолчанию — через 3 дня: столько обычно дают на то, чтобы подогнать технику */
const DEFAULT_DAYS = 3

/**
 * Предписание — юридически значимый документ, поэтому не выдаётся одним нажатием:
 * инспектор видит, кому оно уходит, формулирует требование и назначает срок устранения.
 */
export function PrescribeDialog({ alert, onClose, onDone }: { alert: Alert | null; onClose: () => void; onDone: () => void }) {
  return (
    <Modal open={!!alert} onClose={onClose} title="Выдать предписание">
      {alert && <PrescribeForm alert={alert} onClose={onClose} onDone={onDone} />}
    </Modal>
  )
}

function PrescribeForm({ alert, onClose, onDone }: { alert: Alert; onClose: () => void; onDone: () => void }) {
  const { bySite, updateAlert, notify } = useApp()
  const site = bySite(alert.siteId)
  const today = todayISO()
  const [demand, setDemand] = useState(() => defaultDemand(alert))
  const [due, setDue] = useState(() => addDays(today, DEFAULT_DAYS))
  const [tried, setTried] = useState(false)
  const [saving, setSaving] = useState(false)

  const demandError = !demand.trim() ? 'Напишите, что подрядчик должен сделать' : undefined
  const dueError = !due ? 'Укажите срок устранения' : due < today ? 'Срок не может быть в прошлом' : undefined
  const days = due && !dueError ? daysBetween(today, due) : null

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setTried(true)
    if (demandError || dueError) return
    setSaving(true)
    const ok = await updateAlert(alert.id, 'prescribed', demand.trim(), due)
    setSaving(false)
    if (ok) {
      notify(`Предписание по № ${alert.code} выдано, срок — до ${fmtDate(due)}`)
      onDone()
    }
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-5">
      <dl className="grid sm:grid-cols-2 gap-x-6 gap-y-3 text-[15px]">
        <div><dt className="text-muted-foreground">Кому</dt><dd className="font-semibold">{site?.contractor || 'Подрядчик объекта'}</dd></div>
        <div><dt className="text-muted-foreground">По отклонению</dt><dd className="font-semibold">№ {alert.code}</dd></div>
        <div className="sm:col-span-2"><dt className="text-muted-foreground">Объект</dt><dd>{site?.name ?? '—'}</dd></div>
      </dl>

      <Field label="Требование" error={tried ? demandError : undefined}>
        {(id, describedBy) => (
          <textarea
            id={id} value={demand} onChange={(e) => setDemand(e.target.value)} maxLength={900} required
            aria-invalid={tried && !!demandError} aria-describedby={describedBy}
            className={`${inputCls} min-h-[96px] py-2.5 leading-relaxed`}
          />
        )}
      </Field>

      <Field label="Срок устранения" error={tried ? dueError : undefined}>
        {(id, describedBy) => (
          <div className="flex flex-wrap items-center gap-3">
            <input
              id={id} type="date" value={due} min={today} onChange={(e) => setDue(e.target.value)} required
              aria-invalid={tried && !!dueError} aria-describedby={describedBy} className={cn(inputCls, 'w-auto')}
            />
            {days !== null && <span className="text-muted-foreground">{days === 0 ? 'сегодня' : `через ${plural(days, 'день', 'дня', 'дней')}`}</span>}
          </div>
        )}
      </Field>

      <p className="text-[15px] text-muted-foreground">
        Предписание получит номер и появится в журнале нарушений. Закрыть его сможете вы или администратор, когда подрядчик устранит нарушение.
      </p>

      <div className="flex flex-wrap gap-3">
        <Button type="submit" variant="danger" size="lg" disabled={saving}><FileWarning className="w-5 h-5" /> {saving ? 'Выдаём…' : 'Выдать предписание'}</Button>
        <Button variant="outline" size="lg" onClick={onClose} disabled={saving}>Отмена</Button>
      </div>
    </form>
  )
}

/** Черновик требования — из того, что нарушено; инспектор правит его под себя */
function defaultDemand(a: Alert) {
  const eq = a.equipment ? EQUIPMENT[a.equipment] : undefined
  if (!eq) return 'Устранить нарушение и обеспечить соответствие работ календарному плану.'
  switch (a.kind) {
    case 'missing':
    case 'count_below':
      return `Обеспечить в рабочей зоне ${eq.genitivePlural} не меньше ${a.expected ?? 1}, как требует этап календарного плана.`
    case 'unexpected':
      return `Убрать ${eq.name.toLowerCase()} из рабочей зоны или согласовать изменение календарного плана.`
    case 'idle':
      return `Устранить простой техники (${eq.name.toLowerCase()}) или объяснить его причину.`
    default:
      return 'Устранить нарушение и обеспечить соответствие работ календарному плану.'
  }
}

function addDays(iso: string, n: number) {
  const d = new Date(`${iso}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}
