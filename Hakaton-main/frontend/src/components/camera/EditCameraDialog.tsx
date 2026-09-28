import { useState, type FormEvent } from 'react'
import { Loader2 } from 'lucide-react'
import { api } from '@/api'
import type { Camera } from '@/data'
import { useApp } from '@/store/context'
import { Modal } from '../ui/Modal'
import { Button } from '../ui/Button'
import { Field, inputCls } from '../ui/Field'
import { addressErrors, addressOf, toConnection, type Address } from './address'
import { AddressFields } from './AddressFields'

export function EditCameraDialog({ camera, onClose }: { camera: Camera | null; onClose: () => void }) {
  return (
    <Modal open={!!camera} onClose={onClose} title={camera ? `Изменить: ${camera.name}` : ''} wide>
      {camera && <EditBody key={camera.id} camera={camera} onClose={onClose} />}
    </Modal>
  )
}

function EditBody({ camera, onClose }: { camera: Camera; onClose: () => void }) {
  const { zones, run } = useApp()
  const [name, setName] = useState(camera.name)
  const [zoneId, setZoneId] = useState(camera.zoneId)
  const initial = addressOf(camera)
  const [address, setAddress] = useState<Address>(initial)
  const [spiderEnabled, setSpiderEnabled] = useState(camera.spiderEnabled)
  const [touched, setTouched] = useState<Partial<Record<keyof Address, boolean>>>({})
  const [busy, setBusy] = useState(false)
  const [nameTouched, setNameTouched] = useState(false)  // ошибку показываем после ухода из поля или отправки, как в других формах
  const nameError = name.trim().length < 2 ? 'Введите название' : undefined
  const errors = addressErrors(address)
  const addressChanged = (['host', 'port', 'path', 'username', 'password'] as const).some((k) => address[k] !== initial[k])
  const spiderChanged = spiderEnabled !== camera.spiderEnabled

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setTouched({ host: true, port: true, path: true })
    setNameTouched(true)
    if (busy || nameError || Object.keys(errors).length) return
    setBusy(true)
    const ok = await run(
      () => api.patchCamera(camera.id, {
        name: name.trim(), zoneId, ...(spiderChanged ? { spiderEnabled } : {}), ...(addressChanged ? { connection: toConnection(address) } : {}),
      }),
      `${name.trim()}: изменения сохранены`,
    )
    setBusy(false)
    if (ok) onClose()
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-5">
      <div className="grid sm:grid-cols-2 gap-4">
        <Field label="Название камеры" error={nameTouched ? nameError : undefined}>
          {(id, describedBy) => (
            <input
              id={id} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} onBlur={() => setNameTouched(true)}
              aria-invalid={nameTouched && !!nameError} aria-describedby={describedBy} className={inputCls}
            />
          )}
        </Field>
        <Field label="Зона">
          {(id) => (
            <select id={id} value={zoneId} onChange={(e) => setZoneId(e.target.value)} className={inputCls}>
              {zones.filter((z) => z.siteId === camera.siteId).map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
            </select>
          )}
        </Field>
      </div>
      <section className="border-t border-border pt-5 space-y-4">
        <h3 className="font-semibold text-[17px]">Видеопоток камеры</h3>
        <AddressFields
          value={address} onChange={(patch) => setAddress((a) => ({ ...a, ...patch }))} touched={touched}
          onTouch={(key) => setTouched((t) => ({ ...t, [key]: true }))}
          passwordHint={camera.hasCredentials ? 'Оставьте пустым, чтобы не менять сохранённый пароль' : 'Хранится на сервере в зашифрованном виде'}
        />
      </section>
      <section className="border-t border-border pt-5">
        <label className="flex items-start gap-3 cursor-pointer select-none">
          <input type="checkbox" checked={spiderEnabled} onChange={(e) => setSpiderEnabled(e.target.checked)} className="w-5 h-5 mt-0.5 accent-[var(--color-primary)]" />
          <span>
            <strong className="block">Учитывать план и ресурсы Spider при анализе видео</strong>
            <span className="block mt-0.5 text-[14px] text-muted-foreground">Spider дополняет локальный план для этой камеры и не является наблюдением с видео.</span>
          </span>
        </label>
      </section>
      <div className="flex flex-wrap gap-3 border-t border-border pt-5">
        <Button type="submit" size="lg" disabled={busy}>{busy && <Loader2 className="w-5 h-5 animate-spin" />} Сохранить</Button>
        <Button type="button" variant="outline" size="lg" onClick={onClose}>Отмена</Button>
      </div>
    </form>
  )
}
