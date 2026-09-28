import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { api } from '@/api'
import type { Site } from '@/data'
import { useApp } from '@/store/context'
import { pluralWord } from '@/lib/utils'
import { Button } from '../ui/Button'
import { Modal } from '../ui/Modal'

/** Удалить объект целиком — только администратор, с перечнем того, что удалится, и подтверждением */
export function DeleteSiteDialog({ site, onClose, onDeleted }: { site: Site | null; onClose: () => void; onDeleted?: () => void }) {
  const { cameras, stagesOf, alertsForSite, run } = useApp()
  const [sure, setSure] = useState(false)
  const [busy, setBusy] = useState(false)
  const close = () => { setSure(false); onClose() }
  if (!site) return <Modal open={false} onClose={close} title="">{null}</Modal>
  const siteCameras = cameras.filter((c) => c.siteId === site.id).length
  const alerts = alertsForSite(site.id).length
  return (
    <Modal open onClose={close} title="Удалить объект?">
      <div className="space-y-5">
        <p>Объект «{site.name}» удалится целиком — вместе с ним:</p>
        <ul className="list-disc pl-6 space-y-1">
          <li>{siteCameras} {pluralWord(siteCameras, 'камера', 'камеры', 'камер')} и их видео;</li>
          <li>календарный план ({stagesOf(site.id).length} {pluralWord(stagesOf(site.id).length, 'этап', 'этапа', 'этапов')}) и зоны;</li>
          <li>{alerts} {pluralWord(alerts, 'отклонение', 'отклонения', 'отклонений')} с историей и кадрами-доказательствами.</li>
        </ul>
        <label className="flex items-start gap-3 cursor-pointer select-none">
          <input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} className="w-5 h-5 mt-0.5 accent-[var(--color-danger-solid)]" />
          <span>Понимаю, что это нельзя отменить</span>
        </label>
        <div className="flex flex-wrap gap-3">
          <Button variant="danger" size="lg" disabled={!sure || busy} onClick={async () => {
            setBusy(true)
            const ok = await run(() => api.deleteSite(site.id), `Объект «${site.name}» удалён`)
            setBusy(false)
            if (!ok) return
            close()
            onDeleted?.()
          }}>
            {busy && <Loader2 className="w-5 h-5 animate-spin" />} Удалить объект
          </Button>
          <Button variant="outline" size="lg" onClick={close}>Отмена</Button>
        </div>
      </div>
    </Modal>
  )
}
