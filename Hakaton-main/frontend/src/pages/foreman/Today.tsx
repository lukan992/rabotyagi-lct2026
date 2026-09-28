import { useApp } from '@/store/context'
import { SiteToday } from '@/components/SiteToday'

export function ForemanToday() {
  const { ownSiteId } = useApp()
  if (!ownSiteId) return <p className="text-muted-foreground">За вами не закреплён ни один объект. Обратитесь к администратору.</p>
  return <SiteToday siteId={ownSiteId} camerasLink="/foreman/cameras" />
}
