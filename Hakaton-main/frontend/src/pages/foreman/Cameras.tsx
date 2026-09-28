import { useApp } from '@/store/context'
import { PageHeader } from '@/components/ui/PageHeader'
import { VideoWall } from '@/components/video/VideoWall'

export function ForemanCameras() {
  const { ownSiteId, cameras } = useApp()
  if (!ownSiteId) return <p className="text-muted-foreground">За вами не закреплён ни один объект. Обратитесь к администратору.</p>
  return (
    <div>
      <PageHeader title="Камеры на объекте" info="Видео в реальном времени. Каждые 2 секунды система берёт кадр и ищет на нём технику — рамки показывают, что она видит. Нажмите на камеру — она развернётся на всю вкладку." />
      <VideoWall cameras={cameras.filter((c) => c.siteId === ownSiteId)} empty="На объекте пока нет камер." />
    </div>
  )
}
