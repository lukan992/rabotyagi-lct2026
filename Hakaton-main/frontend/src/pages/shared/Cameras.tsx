import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Plus } from 'lucide-react'
import { CAMERA_ADDERS } from '@/data'
import { useApp } from '@/store/context'
import { PageHeader } from '@/components/ui/PageHeader'
import { Chip } from '@/components/ui/Chip'
import { Button } from '@/components/ui/Button'
import { VideoWall } from '@/components/video/VideoWall'
import { AddCameraDialog, CameraAdminActions } from '@/components/camera'

/**
 * Камеры всех объектов: видеостена с разбивкой по объектам. Здесь же ими и управляют: руководитель и администратор
 * подключают новые камеры, администратор меняет, проверяет и удаляет — у самой камеры.
 */
export function CamerasPage() {
  const { cameras, sites, role, base } = useApp()
  const [site, setSite] = useState('all')
  const [adding, setAdding] = useState<string | true | null>(null)  // true — объект выбирается в форме
  const canAdd = !!role && CAMERA_ADDERS.includes(role.id)
  const isAdmin = role?.id === 'admin'
  const shown = site === 'all' ? sites : sites.filter((s) => s.id === site)
  const sections = shown.map((s) => ({
    id: s.id, title: s.name, cameras: cameras.filter((c) => c.siteId === s.id),
    extra: canAdd && <Button variant="ghost" onClick={() => setAdding(s.id)}><Plus className="w-4 h-4" /> Камера на этот объект</Button>,
  }))

  return (
    <div>
      <PageHeader
        title="Камеры"
        info={<>
          <p>Видео со всех объектов в реальном времени. Нажмите на видео — оно развернётся на всю вкладку. Рамки — что система видит на последнем кадре.</p>
          {canAdd && <p className="mt-2"><b>Как ставить камеру:</b> сверху под углом 30–45°, чтобы рабочая зона была видна целиком, и не против солнца. На объект — хотя бы две камеры: рабочая зона и въезд.</p>}
        </>}
        action={canAdd && sites.length > 0 && <Button size="lg" onClick={() => setAdding(site === 'all' ? true : site)}><Plus className="w-5 h-5" /> Добавить камеру</Button>}
      />
      {sites.length === 0 && (
        <section className="bg-card rounded-xl border border-border shadow-[var(--shadow-card)] p-5 sm:p-6 max-w-2xl">
          <h2 className="text-[18px] font-semibold">Камер пока нет</h2>
          <p className="text-muted-foreground mt-1">
            Камеру подключают к зоне объекта: котлован, въезд, склад. {canAdd ? 'Сначала добавьте объект и его зоны.' : 'Объекты и камеры заводят руководитель проекта и администратор.'}
          </p>
          {canAdd && <Link to={base} className="inline-flex items-center gap-1.5 mt-4 min-h-[44px] font-semibold text-primary hover:underline">К объектам <ArrowRight className="w-4 h-4" /></Link>}
        </section>
      )}
      {sites.length > 1 && (
        <div className="flex flex-wrap gap-2 mb-5" role="group" aria-label="Объект">
          <Chip active={site === 'all'} onClick={() => setSite('all')} small>Все объекты</Chip>
          {sites.map((s) => <Chip key={s.id} active={site === s.id} onClick={() => setSite(s.id)} small>{s.name}</Chip>)}
        </div>
      )}
      {sites.length > 0 && <VideoWall
        cameras={sections.flatMap((s) => s.cameras)} sections={sections}
        actions={isAdmin ? (c) => <CameraAdminActions camera={c} /> : undefined}
      />}
      {canAdd && <AddCameraDialog open={adding !== null} defaultSiteId={typeof adding === 'string' ? adding : undefined} onClose={() => setAdding(null)} />}
    </div>
  )
}
