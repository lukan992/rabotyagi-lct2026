import { lazy } from 'react'
import type { ComponentType } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { LazyMotion, MotionConfig } from 'framer-motion'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Home, Video, CalendarDays, Building2, Bell, ClipboardList, BarChart3, Settings2 } from 'lucide-react'
import { AppProvider } from '@/store/AppContext'
import { ThemeProvider } from '@/store/theme'
import { useApp } from '@/store/context'
import { AppShell } from '@/components/layout/AppShell'
import type { NavItem } from '@/components/layout/AppShell'
import { Login } from '@/pages/Login'
import { ErrorBoundary } from '@/components/ErrorBoundary'

/** Экраны грузятся, когда понадобятся: прорабу на телефоне не нужен код админки */
function page<K extends string>(load: () => Promise<Record<K, ComponentType>>, name: K) {
  return lazy(() => load().then((module) => ({ default: module[name] })))
}

const Help = page(() => import('@/pages/Help'), 'Help')
const ForemanToday = page(() => import('@/pages/foreman/Today'), 'ForemanToday')
const ForemanCameras = page(() => import('@/pages/foreman/Cameras'), 'ForemanCameras')
const ForemanPlan = page(() => import('@/pages/foreman/Plan'), 'ForemanPlan')
const ManagerOverview = page(() => import('@/pages/manager/Overview'), 'ManagerOverview')
const ManagerSite = page(() => import('@/pages/manager/SiteDetail'), 'ManagerSite')
const ManagerAlerts = page(() => import('@/pages/manager/Alerts'), 'ManagerAlerts')
const CheckSnapshot = page(() => import('@/pages/manager/CheckSnapshot'), 'CheckSnapshot')
const InspectorViolations = page(() => import('@/pages/inspector/Violations'), 'InspectorViolations')
const InspectorReports = page(() => import('@/pages/inspector/Reports'), 'InspectorReports')
const CamerasPage = page(() => import('@/pages/shared/Cameras'), 'CamerasPage')
const AdminManage = page(() => import('@/pages/admin/Manage'), 'AdminManage')
const AdminRules = page(() => import('@/pages/admin/Rules'), 'AdminRules')
const AdminUsers = page(() => import('@/pages/admin/Users'), 'AdminUsers')
const AdminAudit = page(() => import('@/pages/admin/Audit'), 'AdminAudit')

/** Меню ролей: у каждой роли — не больше пяти разделов (помещаются в нижнюю панель телефона) */
const NAV: Record<string, NavItem[]> = {
  foreman: [
    { to: '/foreman', label: 'Сегодня', Icon: Home, end: true },
    { to: '/foreman/cameras', label: 'Камеры', Icon: Video },
    { to: '/foreman/plan', label: 'План', Icon: CalendarDays },
  ],
  manager: [
    { to: '/manager', label: 'Объекты', Icon: Building2, end: true },
    { to: '/manager/cameras', label: 'Камеры', Icon: Video },
    { to: '/manager/alerts', label: 'Отклонения', Icon: Bell },
    { to: '/manager/reports', label: 'Отчёты', Icon: BarChart3 },
  ],
  inspector: [
    { to: '/inspector', label: 'Нарушения', Icon: ClipboardList, end: true },
    { to: '/inspector/cameras', label: 'Камеры', Icon: Video },
    { to: '/inspector/reports', label: 'Отчёты', Icon: BarChart3 },
  ],
  // администратор видит всё, что остальные; объекты и камеры настраивает прямо в их списках, остальное — в «Управлении»
  admin: [
    { to: '/admin', label: 'Объекты', Icon: Building2, end: true },
    { to: '/admin/cameras', label: 'Камеры', Icon: Video },
    { to: '/admin/alerts', label: 'Отклонения', Icon: Bell },
    { to: '/admin/reports', label: 'Отчёты', Icon: BarChart3 },
    { to: '/admin/manage', label: 'Управление', Icon: Settings2 },
  ],
}

/** Куда ведёт колокольчик уведомлений для каждой роли */
const ALERTS_PATH: Record<string, string> = { foreman: '/foreman', manager: '/manager/alerts', inspector: '/inspector', admin: '/admin/alerts' }

function Router() {
  const { role } = useApp()
  const home = role ? `/${role.id}` : '/'
  return (
    <Routes>
      {!role && <Route path="*" element={<Login />} />}
      {role && (
      <Route element={<AppShell nav={NAV[role.id]} alertsPath={ALERTS_PATH[role.id]} />}>
        <Route path="/help" element={<Help />} />
        {role.id === 'foreman' && (
          <>
            <Route path="/foreman" element={<ForemanToday />} />
            <Route path="/foreman/cameras" element={<ForemanCameras />} />
            <Route path="/foreman/plan" element={<ForemanPlan />} />
          </>
        )}
        {role.id === 'manager' && (
          <>
            <Route path="/manager" element={<ManagerOverview />} />
            <Route path="/manager/site/:id" element={<ManagerSite />} />
            <Route path="/manager/cameras" element={<CamerasPage />} />
            <Route path="/manager/alerts" element={<ManagerAlerts />} />
            <Route path="/manager/reports" element={<InspectorReports />} />
            <Route path="/manager/check" element={<CheckSnapshot />} />
          </>
        )}
        {role.id === 'inspector' && (
          <>
            <Route path="/inspector" element={<InspectorViolations />} />
            <Route path="/inspector/cameras" element={<CamerasPage />} />
            <Route path="/inspector/reports" element={<InspectorReports />} />
          </>
        )}
        {role.id === 'admin' && (
          <>
            <Route path="/admin" element={<ManagerOverview />} />
            <Route path="/admin/site/:id" element={<ManagerSite />} />
            <Route path="/admin/cameras" element={<CamerasPage />} />
            <Route path="/admin/alerts" element={<ManagerAlerts />} />
            <Route path="/admin/reports" element={<InspectorReports />} />
            <Route path="/admin/check" element={<CheckSnapshot />} />
            <Route path="/admin/manage" element={<AdminManage />}>
              <Route index element={<Navigate to="users" replace />} />
              {/* объекты и камеры теперь там, где их списки */}
              <Route path="sites" element={<Navigate to="/admin" replace />} />
              <Route path="cameras" element={<Navigate to="/admin/cameras" replace />} />
              <Route path="users" element={<AdminUsers />} />
              <Route path="rules" element={<AdminRules />} />
              <Route path="audit" element={<AdminAudit />} />
            </Route>
          </>
        )}
        <Route path="*" element={<Navigate to={home} replace />} />
      </Route>
      )}
    </Routes>
  )
}

// анимации подгружаются отдельно (см. lib/motionFeatures); strict — если где-то остался тяжёлый motion.*, сразу будет видно
const motionFeatures = () => import('@/lib/motionFeatures').then((mod) => mod.default)

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: true } },
})

export default function App() {
  return (
    <LazyMotion features={motionFeatures} strict>
    <MotionConfig reducedMotion="user">
      <ErrorBoundary full>
      <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route path="*" element={<AppProvider><Router /></AppProvider>} />
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
      </ThemeProvider>
      </ErrorBoundary>
    </MotionConfig>
    </LazyMotion>
  )
}
