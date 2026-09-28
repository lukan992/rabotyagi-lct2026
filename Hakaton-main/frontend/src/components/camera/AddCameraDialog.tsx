import { useState, type FormEvent } from 'react'
import { Loader2, PlugZap } from 'lucide-react'
import { api, ApiError } from '@/api'
import type { ZoneKind } from '@/data'
import { useApp } from '@/store/context'
import { ZONE_KINDS } from '@/lib/zones'
import { Modal } from '../ui/Modal'
import { Button } from '../ui/Button'
import { Field, FormError, inputCls } from '../ui/Field'
import { addressErrors, RTSP_PORT, toConnection, type Address } from './address'
import { AddressFields } from './AddressFields'
import { ProbeResultPanel } from './ProbeResultPanel'
import { useProbe } from './useProbe'

const NEW_ZONE = '__new__'

interface AddForm extends Address { siteId: string; zoneId: string; newZoneName: string; newZoneKind: ZoneKind; name: string; allowOffline: boolean; spiderEnabled: boolean }

/** Диалог «Добавить камеру по IP-адресу»: адрес видеопотока → проверка с живым видео → сохранение */
export function AddCameraDialog({ open, onClose, defaultSiteId }: { open: boolean; onClose: () => void; defaultSiteId?: string }) {
  return (
    <Modal open={open} onClose={onClose} title="Добавить камеру" wide>
      {open && <AddBody onClose={onClose} defaultSiteId={defaultSiteId} />}
    </Modal>
  )
}

function AddBody({ onClose, defaultSiteId }: { onClose: () => void; defaultSiteId?: string }) {
  const { sites, zones, addCamera, notify } = useApp()
  const firstZone = (siteId: string) => zones.find((z) => z.siteId === siteId)?.id ?? NEW_ZONE
  const [form, setForm] = useState<AddForm>(() => {
    const siteId = defaultSiteId ?? sites[0]?.id ?? ''
    return {
      siteId, zoneId: firstZone(siteId), newZoneName: '', newZoneKind: 'work', name: '', allowOffline: false, spiderEnabled: false,
      preset: '', host: '', port: String(RTSP_PORT), path: '/', username: '', password: '',
    }
  })
  const [touched, setTouched] = useState<Partial<Record<keyof AddForm, boolean>>>({})
  const [probe, setProbe] = useProbe()
  const [busy, setBusy] = useState<'probe' | 'save' | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const siteZones = zones.filter((z) => z.siteId === form.siteId)

  const set = (patch: Partial<AddForm>) => {
    setForm((f) => ({ ...f, ...patch }))
    if (['host', 'port', 'path', 'username', 'password'].some((k) => k in patch)) setProbe(null)
    setSaveError(null)
  }
  const touch = (key: keyof AddForm) => setTouched((t) => ({ ...t, [key]: true }))

  const errors: Partial<Record<keyof AddForm, string>> = { ...addressErrors(form) }
  if (form.name.trim().length < 2) errors.name = 'Введите название, например «Камера 2 — въезд»'
  if (form.zoneId === NEW_ZONE && form.newZoneName.trim().length < 2) errors.newZoneName = 'Введите название зоны'
  const shown = (key: keyof AddForm) => (touched[key] ? errors[key] : undefined)

  const runProbe = async () => {
    setTouched((t) => ({ ...t, host: true, port: true, path: true }))
    if (errors.host || errors.port || errors.path) return
    setBusy('probe')
    try {
      setProbe(await api.probeCamera(toConnection(form)))
    } catch (e) {
      setProbe({ ok: false, code: 'error', message: e instanceof ApiError ? e.message : 'Не удалось проверить подключение', elapsedMs: 0, previewPath: null })
    } finally {
      setBusy(null)
    }
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setTouched({ name: true, host: true, port: true, path: true, newZoneName: true })
    if (Object.keys(errors).length) return
    setBusy('save')
    setSaveError(null)
    try {
      const created = await addCamera({
        siteId: form.siteId, name: form.name.trim(), connection: toConnection(form), allowOffline: form.allowOffline, spiderEnabled: form.spiderEnabled,
        zoneId: form.zoneId === NEW_ZONE ? null : form.zoneId,
        newZoneName: form.zoneId === NEW_ZONE ? form.newZoneName.trim() : null, newZoneKind: form.newZoneKind,
      })
      notify(created.status === 'offline' ? `${created.name} добавлена, но пока не отвечает` : `${created.name} добавлена — видео появится через несколько секунд`)
      onClose()
    } catch (e) {
      setSaveError(e instanceof ApiError ? e.message : 'Не удалось добавить камеру')
      setBusy(null)
    }
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-6">
      <section className="grid sm:grid-cols-2 gap-4">
        <Field label="Объект">
          {(id) => (
            <select id={id} value={form.siteId} onChange={(e) => set({ siteId: e.target.value, zoneId: firstZone(e.target.value) })} className={inputCls}>
              {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          )}
        </Field>
        <Field label="Зона, за которой следит камера">
          {(id) => (
            <select id={id} value={form.zoneId} onChange={(e) => set({ zoneId: e.target.value })} className={inputCls}>
              {siteZones.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
              <option value={NEW_ZONE}>+ Новая зона…</option>
            </select>
          )}
        </Field>
        {form.zoneId === NEW_ZONE && (
          <>
            <Field label="Название новой зоны" error={shown('newZoneName')}>
              {(id, describedBy) => <input id={id} value={form.newZoneName} maxLength={200} onChange={(e) => set({ newZoneName: e.target.value })} onBlur={() => touch('newZoneName')}
                aria-invalid={!!shown('newZoneName')} aria-describedby={describedBy} className={inputCls} placeholder="Например: Въезд № 2" />}
            </Field>
            <Field label="Вид зоны">
              {(id) => (
                <select id={id} value={form.newZoneKind} onChange={(e) => set({ newZoneKind: e.target.value as ZoneKind })} className={inputCls}>
                  {ZONE_KINDS.map((k) => <option key={k.id} value={k.id}>{k.hint ? `${k.label} — ${k.hint}` : k.label}</option>)}
                </select>
              )}
            </Field>
          </>
        )}
        <Field label="Название камеры" error={shown('name')} className="sm:col-span-2">
          {(id, describedBy) => <input id={id} value={form.name} maxLength={200} onChange={(e) => set({ name: e.target.value })} onBlur={() => touch('name')}
            aria-invalid={!!shown('name')} aria-describedby={describedBy} className={inputCls} placeholder="Камера 2 — въезд" />}
        </Field>
      </section>

      <section className="border-t border-border pt-5 space-y-4">
        <div>
          <h3 className="font-semibold text-[17px]">Видеопоток камеры</h3>
          <p className="text-[14px] text-muted-foreground">Камера подключается по RTSP — так работают почти все IP-камеры. Видео сразу появится у всех, кто видит этот объект.</p>
        </div>
        <AddressFields value={form} onChange={set} touched={touched} onTouch={touch} passwordHint="Хранится на сервере в зашифрованном виде" />
        <ProbeResultPanel probe={probe} />
      </section>

      <div className="border-t border-border pt-5 space-y-4">
        <label className="flex items-start gap-3 cursor-pointer select-none">
          <input type="checkbox" checked={form.spiderEnabled} onChange={(e) => set({ spiderEnabled: e.target.checked })} className="w-5 h-5 mt-0.5 accent-[var(--color-primary)]" />
          <span>
            <strong className="block">Учитывать план и ресурсы Spider при анализе видео</strong>
            <span className="block mt-0.5 text-[14px] text-muted-foreground">Опция применяется только к видеокадрам этой камеры. Данные Spider — план, а не наблюдения с камеры.</span>
          </span>
        </label>
        <label className="flex items-start gap-3 cursor-pointer select-none">
          <input type="checkbox" checked={form.allowOffline} onChange={(e) => set({ allowOffline: e.target.checked })} className="w-5 h-5 mt-0.5 accent-[var(--color-primary)]" />
          <span>Добавить, даже если камера сейчас не отвечает</span>
        </label>
        <FormError message={saveError} />
        <div className="flex flex-wrap gap-3">
          <Button type="button" variant="outline" size="lg" onClick={runProbe} disabled={busy !== null}>
            {busy === 'probe' ? <Loader2 className="w-5 h-5 animate-spin" /> : <PlugZap className="w-5 h-5" />} Проверить и показать видео
          </Button>
          <Button type="submit" size="lg" disabled={busy !== null}>
            {busy === 'save' && <Loader2 className="w-5 h-5 animate-spin" />} Добавить камеру
          </Button>
        </div>
      </div>
    </form>
  )
}
