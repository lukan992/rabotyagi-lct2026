import { Suspense, useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { AnimatePresence, m } from 'framer-motion'
import { AlertTriangle, Bell, CircleHelp, LogOut, CheckCircle2 } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useApp, useToasts } from '@/store/context'
import { ThemeMenuButton } from '@/components/ThemePicker'
import { PageLoading } from '@/components/ui/PageLoading'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { Modal } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'
import { useDismiss } from '@/lib/useDismiss'
import { bySeverity, initials } from '@/store/selectors'
import { SEVERITY } from '@/lib/labels'
import { ago, fmtWhen, shortName, todayLabel } from '@/lib/utils'
import { cn } from '@/lib/utils'

/** Кадр для журнала сохраняется раз в минуту — данные старше 3 минут уже запаздывают, старше 10 — «слепая» зона */
const STALE_MIN = 3
const BLIND_MIN = 10

/** Насколько свежие данные с камер: зелёный — всё идёт, жёлтый — запаздывают, красный — кадров давно нет */
function DataFreshness({ at }: { at: string | null }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000)
    return () => window.clearInterval(timer)
  }, [])
  const ageMin = at ? (now - Date.parse(at)) / 60_000 : Infinity
  const tone = !at ? 'bg-border-strong' : ageMin <= STALE_MIN ? 'bg-ok' : ageMin <= BLIND_MIN ? 'bg-warn' : 'bg-danger'
  const text = !at ? 'Кадров пока нет' : ageMin <= BLIND_MIN ? `Данные на ${fmtWhen(at)}` : `Нет новых кадров с ${fmtWhen(at)}`
  return (
    <span
      className={cn('hidden sm:inline-flex items-center gap-2 text-[14px] mr-2', at && ageMin > BLIND_MIN ? 'text-danger font-medium' : 'text-muted-foreground')}
      title={at ? `Последний кадр с камер — ${ago(at, new Date(now))}. Кадры разбираются каждые 2 секунды, объекты сверяются с планом раз в минуту` : undefined}
    >
      <span className={cn('w-2 h-2 rounded-full', tone)} aria-hidden />
      {text}
    </span>
  )
}

export interface NavItem { to: string; label: string; Icon: LucideIcon; end?: boolean }

/** Логотип: знак + название */
export function Logo({ className, nameClassName }: { className?: string; nameClassName?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-2.5 font-semibold tracking-tight', className)}>
      <img src="/favicon.svg" alt="" className="w-7 h-7" />
      <span className={nameClassName}>СтройКонтроль</span>
    </span>
  )
}

const navCls = ({ isActive }: { isActive: boolean }) => cn(
  'group flex items-center gap-3 min-h-[44px] px-3 rounded-lg text-[15px] transition-colors duration-150',
  isActive ? 'bg-muted text-foreground font-semibold' : 'text-muted-foreground hover:bg-muted/70 hover:text-foreground',
)

/**
 * Каркас. Десктоп: светлое боковое меню + верхняя панель. Телефон: верхняя панель + нижняя навигация.
 */
export function AppShell({ nav, alertsPath }: { nav: NavItem[]; alertsPath: string }) {
  const { role, user, logout, ownSiteId, bySite, lastDataAt } = useApp()
  const toasts = useToasts()
  const [confirmExit, setConfirmExit] = useState(false)
  const ownSite = ownSiteId ? bySite(ownSiteId) : undefined
  const { pathname } = useLocation()

  return (
    <div className="min-h-dvh lg:pl-[260px]">
      <aside className="hidden lg:flex fixed inset-y-0 left-0 w-[260px] flex-col bg-card border-r border-border z-40">
        <NavLink to="/" className="h-16 px-5 flex items-center shrink-0 text-[17px]"><Logo /></NavLink>

        {ownSite && (
          <div className="mx-4 mb-2 rounded-lg bg-muted px-3 py-2.5">
            <div className="text-[12px] text-muted-foreground">Ваш объект</div>
            <div className="font-semibold leading-snug text-[15px]">{ownSite.name}</div>
          </div>
        )}

        <nav className="flex-1 px-3 py-2 space-y-0.5 overflow-y-auto" aria-label="Разделы">
          {nav.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={navCls}>
              {({ isActive }) => <><n.Icon className={cn('w-5 h-5 shrink-0', isActive && 'text-primary')} /> {n.label}</>}
            </NavLink>
          ))}
        </nav>

        <div className="px-3 pb-2">
          <NavLink to="/help" className={navCls}>
            {({ isActive }) => <><CircleHelp className={cn('w-5 h-5 shrink-0', isActive && 'text-primary')} /> Справка</>}
          </NavLink>
        </div>

        <div className="border-t border-border p-3 flex items-center gap-3">
          <span className="w-9 h-9 rounded-full bg-muted text-[13px] font-semibold flex items-center justify-center shrink-0">{user ? initials(user.name) : ''}</span>
          <div className="min-w-0 flex-1 leading-tight">
            <div className="font-semibold text-[15px] truncate" title={user?.name}>{user ? shortName(user.name) : ''}</div>
            <div className="text-[13px] text-muted-foreground truncate">{role?.title}</div>
          </div>
          <button onClick={logout} aria-label="Выйти" title="Выйти" className="w-11 h-11 rounded-lg flex items-center justify-center text-muted-foreground hover:bg-muted hover:text-foreground cursor-pointer transition-colors shrink-0">
            <LogOut className="w-5 h-5" />
          </button>
        </div>
      </aside>

      <header className="sticky top-0 z-30 bg-card/90 backdrop-blur-sm border-b border-border">
        <div className="h-16 px-4 lg:px-8 flex items-center gap-3">
          {/* на узком телефоне (375–390 px) название с четырьмя кнопками не помещается — остаётся знак, название читает диктор */}
          <NavLink to="/" className="lg:hidden text-[17px] shrink-0"><Logo nameClassName="max-[409px]:sr-only" /></NavLink>
          <div className="hidden lg:block text-[15px]">
            <span className="font-semibold">{todayLabel()}</span>
          </div>
          <div className="ml-auto flex items-center gap-1 sm:gap-2">
            <DataFreshness at={lastDataAt} />
            <ThemeMenuButton />
            <NotificationsBell alertsPath={alertsPath} />
            {/* на телефоне справка — здесь: нижняя панель занята разделами роли */}
            <NavLink to="/help" aria-label="Справка" className="lg:hidden w-11 h-11 rounded-lg flex items-center justify-center text-muted-foreground hover:bg-muted"><CircleHelp className="w-5 h-5" /></NavLink>
            {/* на телефоне кнопка выхода — рядом со справкой и колокольчиком: промахнуться легко, поэтому переспрашиваем */}
            <button onClick={() => setConfirmExit(true)} aria-label="Выйти" className="lg:hidden w-11 h-11 rounded-lg flex items-center justify-center text-muted-foreground hover:bg-muted cursor-pointer"><LogOut className="w-5 h-5" /></button>
          </div>
        </div>
      </header>

      <main className="w-full max-w-[1120px] mx-auto px-4 lg:px-8 py-6 lg:py-8 pb-28 lg:pb-12">
        {/* ключ — адрес: ошибка в одном разделе не мешает перейти в другой */}
        <ErrorBoundary key={pathname}>
          <Suspense fallback={<PageLoading />}>
            <Outlet />
          </Suspense>
        </ErrorBoundary>
      </main>

      <nav className="lg:hidden fixed bottom-0 inset-x-0 bg-card border-t border-border z-30 pb-[env(safe-area-inset-bottom)]" aria-label="Разделы">
        <div className="grid max-w-xl mx-auto" style={{ gridTemplateColumns: `repeat(${nav.length}, 1fr)` }}>
          {nav.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => cn('flex flex-col items-center justify-center gap-1 min-h-[60px] px-1 text-[12px] text-center transition-colors', isActive ? 'text-primary font-semibold' : 'text-muted-foreground')}>
              <n.Icon className="w-6 h-6" />
              {n.label}
            </NavLink>
          ))}
        </div>
      </nav>

      <Modal open={confirmExit} onClose={() => setConfirmExit(false)} title="Выйти из системы?">
        <p>Чтобы снова открыть приложение, нужно будет войти ещё раз.</p>
        <div className="flex flex-wrap gap-3 mt-5">
          <Button size="lg" onClick={() => { setConfirmExit(false); logout() }}><LogOut className="w-5 h-5" /> Выйти</Button>
          <Button size="lg" variant="outline" onClick={() => setConfirmExit(false)}>Остаться</Button>
        </div>
      </Modal>

      <div className="fixed z-[60] bottom-24 lg:bottom-6 inset-x-4 flex flex-col items-center gap-2 pointer-events-none" role="status" aria-live="polite">
        <AnimatePresence>
          {toasts.map((t) => (
            <m.div
              key={t.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.16 }}
              className="pointer-events-auto bg-card text-foreground border border-border rounded-xl px-4 py-3 max-w-md text-[15px] flex items-center gap-2.5 shadow-[var(--shadow-pop)]"
            >
              {t.tone === 'error' ? <AlertTriangle className="w-5 h-5 text-warn shrink-0" /> : <CheckCircle2 className="w-5 h-5 text-ok shrink-0" />} {t.text}
            </m.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}

/** Уведомления: новые отклонения, доступные роли */
function NotificationsBell({ alertsPath }: { alertsPath: string }) {
  const { alerts, role, bySite, ownSiteId } = useApp()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const nav = useNavigate()
  // считаем только то, что человек найдёт, перейдя по колокольчику: прораб — свой объект, инспектор — журнал без «камера не отвечает»
  const fresh = alerts
    .filter((a) => a.status === 'new'
      && (role?.id !== 'foreman' || a.siteId === ownSiteId)
      && (role?.id !== 'inspector' || a.kind !== 'camera_offline'))
    .sort(bySeverity)

  useDismiss(ref, open, () => setOpen(false))

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)} aria-label={`Уведомления: ${fresh.length} новых`} aria-expanded={open}
        className={cn('relative w-11 h-11 rounded-lg flex items-center justify-center cursor-pointer transition-colors', open ? 'bg-muted text-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground')}
      >
        <Bell className="w-5 h-5" />
        {fresh.length > 0 && <span className="absolute top-1.5 right-1.5 min-w-[18px] h-[18px] px-1 rounded-full bg-danger-solid text-white text-[11px] font-semibold flex items-center justify-center tabular ring-2 ring-card">{fresh.length}</span>}
      </button>
      <AnimatePresence>
        {open && (
          <m.div
            initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.12 }}
            className="absolute right-0 mt-2 w-[min(92vw,380px)] bg-card border border-border rounded-xl shadow-[var(--shadow-pop)] overflow-hidden"
          >
            <div className="px-4 pt-3.5 pb-2 font-semibold">Новые отклонения</div>
            {fresh.length === 0 ? (
              <div className="px-4 pb-4 text-muted-foreground">Новых отклонений нет</div>
            ) : (
              <ul className="max-h-[60vh] overflow-y-auto pb-1.5">
                {fresh.map((a) => (
                  <li key={a.id} className="px-1.5">
                    <button onClick={() => { setOpen(false); nav(`${alertsPath}?alert=${a.id}`) }} className="w-full text-left px-2.5 py-2.5 rounded-lg hover:bg-muted cursor-pointer transition-colors flex gap-3">
                      <span className={cn('w-2 h-2 rounded-full mt-[7px] shrink-0', SEVERITY[a.severity].bar)} />
                      <span className="min-w-0">
                        <span className="block font-medium leading-snug text-[15px]">{a.title}</span>
                        <span className="block text-[13px] text-muted-foreground truncate">{bySite(a.siteId)?.name} · {ago(a.startedAt)}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </m.div>
        )}
      </AnimatePresence>
    </div>
  )
}
