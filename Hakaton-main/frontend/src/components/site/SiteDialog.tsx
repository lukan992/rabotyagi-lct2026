import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { api } from '@/api'
import type { Site, SiteInput, SiteKind } from '@/data'
import { SITE_KIND } from '@/lib/labels'
import { useApp } from '@/store/context'
import { Button } from '../ui/Button'
import { Chip } from '../ui/Chip'
import { Modal } from '../ui/Modal'
import { Field, inputCls } from '../ui/Field'

const DAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
const DAY_NAMES = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']

/** Создать объект или изменить его карточку: название, вид, адрес, подрядчик, рабочее время, прораб */
export function SiteDialog({ site, onClose, onCreated }: { site: Site | 'new' | null; onClose: () => void; onCreated?: (site: Site) => void }) {
  return (
    <Modal open={site !== null} onClose={onClose} title={site === 'new' ? 'Новый объект' : site ? `Изменить: ${site.name}` : ''}>
      {site !== null && <SiteForm key={site === 'new' ? 'new' : site.id} site={site === 'new' ? null : site} onClose={onClose} onCreated={onCreated} />}
    </Modal>
  )
}

function SiteForm({ site, onClose, onCreated }: { site: Site | null; onClose: () => void; onCreated?: (site: Site) => void }) {
  const { run } = useApp()
  const users = useQuery({ queryKey: ['users'], queryFn: api.users })
  const foremen = (users.data ?? []).filter((u) => u.role === 'foreman' && u.isActive)
  const currentForeman = site ? foremen.find((u) => u.siteIds.includes(site.id))?.id ?? null : null
  // foremanId: undefined — прораба не трогали (на сервер не отправляем: список сотрудников мог ещё не загрузиться),
  // null — выбрали «не назначен» и прораба нужно снять
  const [form, setForm] = useState<SiteInput>({
    name: site?.name ?? '', address: site?.address ?? '', contractor: site?.contractor ?? '', kind: site?.kind ?? 'other',  // как на сервере: неверный вид — неверная подсказка при распознавании этапа
    // у нового объекта — обычная смена; у существующего — как было (у объектов до этой настройки — круглосуточно)
    workFrom: site?.workFrom ?? 8, workTo: site?.workTo ?? 20, workDays: site?.workDays ?? '1111110',
  })
  const foremanId = form.foremanId === undefined ? currentForeman : form.foremanId
  const [tried, setTried] = useState(false)
  const [busy, setBusy] = useState(false)
  const nameError = form.name.trim().length < 2 ? 'Введите название объекта' : undefined
  const aroundTheClock = form.workFrom === 0 && form.workTo === 24
  const hoursError = !form.workDays?.includes('1') ? 'Отметьте хотя бы один рабочий день'
    : !aroundTheClock && form.workFrom === (form.workTo ?? 24) % 24 ? 'Начало и конец смены совпадают' : undefined
  const toggleDay = (n: number) => set({ workDays: (form.workDays ?? '1111111').split('').map((d, i) => (i === n ? (d === '1' ? '0' : '1') : d)).join('') })
  const set = (patch: Partial<SiteInput>) => setForm((f) => ({ ...f, ...patch }))

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setTried(true)
    if (nameError || hoursError) return
    setBusy(true)
    const body: SiteInput = { ...form, name: form.name.trim() }
    const result: { created?: Site } = {}
    const ok = await run(
      async () => { if (site) await api.updateSite(site.id, body); else result.created = await api.createSite(body) },
      site ? 'Объект сохранён' : `Объект «${body.name}» создан`,
    )
    setBusy(false)
    if (!ok) return
    onClose()
    if (result.created) onCreated?.(result.created)
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <Field label="Название" error={tried ? nameError : undefined}>
        {(id, d) => <input id={id} value={form.name} maxLength={200} onChange={(e) => set({ name: e.target.value })} aria-invalid={tried && !!nameError} aria-describedby={d} className={inputCls} placeholder="Название объекта" />}
      </Field>
      <Field label="Вид объекта" hint="От вида зависят этапы и техника: на дороге — асфальт и катки, у дома — котлован, каркас. Это подсказка для распознавания этапа по кадрам">
        {(id, d) => (
          <select id={id} value={form.kind} onChange={(e) => set({ kind: e.target.value as SiteKind })} aria-describedby={d} className={inputCls}>
            {(Object.keys(SITE_KIND) as SiteKind[]).map((k) => <option key={k} value={k}>{SITE_KIND[k]}</option>)}
          </select>
        )}
      </Field>
      <Field label="Адрес">
        {(id) => <input id={id} value={form.address} maxLength={200} onChange={(e) => set({ address: e.target.value })} className={inputCls} />}
      </Field>
      <Field label="Подрядчик">
        {(id) => <input id={id} value={form.contractor} maxLength={200} onChange={(e) => set({ contractor: e.target.value })} className={inputCls} />}
      </Field>
      <fieldset className="space-y-2">
        <legend className="font-semibold text-[15px]">Рабочее время</legend>
        <p className="text-[14px] text-muted-foreground">
          Вне него технику не сверяем и кадры в аналитику не отправляем: ночью и в выходные техники на площадке нет — это не отклонение.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Chip active={aroundTheClock} onClick={() => set(aroundTheClock ? { workFrom: 8, workTo: 20 } : { workFrom: 0, workTo: 24 })}>Круглосуточно</Chip>
          {!aroundTheClock && (
            <>
              <label className="inline-flex items-center gap-2">
                <span className="text-[15px]">с</span>
                <select value={form.workFrom} onChange={(e) => set({ workFrom: Number(e.target.value) })} className={`${inputCls} w-24`} aria-label="Начало смены, час">
                  {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{h}:00</option>)}
                </select>
              </label>
              <label className="inline-flex items-center gap-2">
                <span className="text-[15px]">до</span>
                <select value={form.workTo} onChange={(e) => set({ workTo: Number(e.target.value) })} className={`${inputCls} w-24`} aria-label="Конец смены, час">
                  {Array.from({ length: 24 }, (_, i) => i + 1).map((h) => <option key={h} value={h}>{h % 24}:00</option>)}
                </select>
              </label>
            </>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Рабочие дни">
          {DAYS.map((day, n) => (
            <span key={day} title={DAY_NAMES[n]}>
              <Chip small active={form.workDays?.[n] === '1'} onClick={() => toggleDay(n)}>{day}</Chip>
            </span>
          ))}
        </div>
        {tried && hoursError && <p role="alert" className="text-[14px] text-danger">{hoursError}</p>}
      </fieldset>
      <Field label="Прораб" hint="Получит доступ к объекту. Нужного нет в списке — его заводит администратор в «Управлении → Сотрудники»">
        {(id, d) => (
          <select id={id} value={foremanId ?? ''} onChange={(e) => set({ foremanId: e.target.value || null })} aria-describedby={d} className={inputCls}>
            <option value="">— не назначен —</option>
            {foremen.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
        )}
      </Field>
      <div className="flex flex-wrap gap-3 pt-2">
        <Button type="submit" size="lg" disabled={busy}>{busy && <Loader2 className="w-5 h-5 animate-spin" />} {site ? 'Сохранить' : 'Создать объект'}</Button>
        <Button type="button" variant="outline" size="lg" onClick={onClose}>Отмена</Button>
      </div>
    </form>
  )
}
