import { useState, type FormEvent } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { api } from '@/api'
import type { Site, Zone, ZoneKind } from '@/data'
import { useApp } from '@/store/context'
import { ZONE_KINDS } from '@/lib/zones'
import { Button } from '../ui/Button'
import { Modal } from '../ui/Modal'
import { Field, inputCls } from '../ui/Field'

/** Зоны объекта: рабочие, въезд, склад — от вида зоны зависит, как считается техника на её камерах */
export function ZonesDialog({ site, onClose }: { site: Site | null; onClose: () => void }) {
  return (
    <Modal open={!!site} onClose={onClose} title={site ? `Зоны: ${site.name}` : ''}>
      {site && <Zones site={site} />}
    </Modal>
  )
}

function Zones({ site }: { site: Site }) {
  const { zones, cameras, run } = useApp()
  const siteZones = zones.filter((z) => z.siteId === site.id)
  const [name, setName] = useState('')
  const [kind, setKind] = useState<ZoneKind>('work')
  const [busy, setBusy] = useState(false)  // повторное нажатие «Добавить» не создаёт вторую такую же зону
  const add = async (e: FormEvent) => {
    e.preventDefault()
    if (busy || name.trim().length < 2) return
    setBusy(true)
    if (await run(() => api.createZone(site.id, { name: name.trim(), kind }), `Зона «${name.trim()}» добавлена`)) setName('')
    setBusy(false)
  }
  return (
    <div className="space-y-5">
      <ul className="space-y-2">
        {siteZones.map((z) => <ZoneRow key={z.id} zone={z} used={cameras.some((c) => c.zoneId === z.id)} />)}
      </ul>
      <form onSubmit={add} noValidate className="grid sm:grid-cols-[1fr_180px_auto] gap-3 items-end border-t border-border pt-4">
        <Field label="Новая зона">{(id) => <input id={id} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} className={inputCls} placeholder="Въезд № 2" />}</Field>
        <Field label="Вид" hint="Рабочая зона — там, где техника считается работающей. На въезде и складе техника считается подъезжающей: она видна, но нехватку в рабочей зоне не закрывает.">
          {(id, d) => <select id={id} aria-describedby={d} value={kind} onChange={(e) => setKind(e.target.value as ZoneKind)} className={inputCls}>{ZONE_KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}</select>}
        </Field>
        <Button type="submit" size="lg" disabled={busy}><Plus className="w-5 h-5" /> Добавить</Button>
      </form>
    </div>
  )
}

function ZoneRow({ zone, used }: { zone: Zone; used: boolean }) {
  const { run } = useApp()
  const [name, setName] = useState(zone.name)
  const [kind, setKind] = useState(zone.kind)
  const [busy, setBusy] = useState(false)
  const changed = name.trim() !== zone.name || kind !== zone.kind
  const once = async (action: () => Promise<unknown>, done: string) => {
    if (busy) return
    setBusy(true)
    await run(action, done)
    setBusy(false)
  }
  return (
    <li className="grid sm:grid-cols-[1fr_180px_auto] gap-2 items-center">
      <input aria-label="Название зоны" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} className={inputCls} />
      <select aria-label="Вид зоны" value={kind} onChange={(e) => setKind(e.target.value as ZoneKind)} className={inputCls}>
        {ZONE_KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
      </select>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" disabled={busy || !changed || name.trim().length < 2} onClick={() => void once(() => api.updateZone(zone.id, { name: name.trim(), kind }), 'Зона сохранена')}>Сохранить</Button>
        <Button
          size="sm" variant="ghost" aria-label={`Удалить зону ${zone.name}`} disabled={used || busy}
          title={used ? 'В зоне стоит камера — сначала перенесите её' : undefined}
          onClick={() => void once(() => api.deleteZone(zone.id), `Зона «${zone.name}» удалена`)}
        >
          <Trash2 className="w-4 h-4" />
        </Button>
      </div>
    </li>
  )
}
