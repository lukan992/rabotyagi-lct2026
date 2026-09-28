import { Suspense, useEffect, useRef } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { History, ListChecks, Users } from 'lucide-react'
import { PageLoading } from '@/components/ui/PageLoading'
import { cn } from '@/lib/utils'

const SECTIONS = [
  { to: 'users', label: 'Сотрудники', Icon: Users },
  { to: 'rules', label: 'Правила', Icon: ListChecks },
  { to: 'audit', label: 'Журнал действий', Icon: History },
]

/** «Управление» — то, у чего нет своего списка на других экранах: сотрудники, правила, журнал действий.
 * Объекты и камеры добавляют и настраивают там, где их список: «Обзор», страница объекта, «Камеры». */
export function AdminManage() {
  const nav = useRef<HTMLElement>(null)
  const { pathname } = useLocation()
  // на телефоне вкладки не помещаются — открытую подвигаем в поле зрения
  useEffect(() => {
    nav.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [pathname])

  return (
    <div>
      <nav ref={nav} aria-label="Разделы управления" className="flex gap-2 mb-6 overflow-x-auto -mx-4 px-4 lg:mx-0 lg:px-0 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {SECTIONS.map(({ to, label, Icon }) => (
          <NavLink
            key={to} to={to}
            className={({ isActive }) => cn(
              'inline-flex items-center gap-2 min-h-[44px] px-4 rounded-lg border font-semibold whitespace-nowrap transition-colors',
              isActive ? 'bg-primary text-on-primary border-primary' : 'bg-card border-border hover:border-primary/60',
            )}
          >
            <Icon className="w-5 h-5" aria-hidden /> {label}
          </NavLink>
        ))}
      </nav>
      {/* вкладки остаются на месте, пока грузится код раздела */}
      <Suspense fallback={<PageLoading />}>
        <Outlet />
      </Suspense>
    </div>
  )
}
