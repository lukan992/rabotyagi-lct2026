import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { api, ApiError, getToken, setToken, UNAUTHORIZED_EVENT } from '@/api'
import { ROLES, type Alert, type AlertStatus, type Camera, type NewCamera, type Rule, type SiteStatus, type Snapshot } from '@/data'
import { Button } from '@/components/ui/Button'
import { Ctx, ToastCtx, type AppState, type Toast } from './context'
import { initKeycloak, keycloakLogin, keycloakLogout } from './keycloak'
import { isOpen } from './selectors'

const POLL_MS = 20_000 // как часто подтягиваем свежие кадры и предупреждения
const CORE_KEYS = new Set(['sites', 'zones', 'stages', 'rules', 'cameras', 'alerts'])
const message = (error: unknown) => (error instanceof ApiError ? error.message : 'Что-то пошло не так. Попробуйте ещё раз.')

export function AppProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [token, setTokenState] = useState(getToken)
  const [toasts, setToasts] = useState<Toast[]>([])

  const notify = useCallback((text: string, tone: Toast['tone'] = 'ok') => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, text, tone }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === 'error' ? 6000 : 3200)
  }, [])

  // публичный запрос: узнаём режим авторизации (локальный или Keycloak)
  const metaQ = useQuery({ queryKey: ['meta'], queryFn: api.meta, staleTime: Infinity, retry: 1 })
  const authMode = metaQ.data?.authMode ?? 'local'

  // ---------- вход ----------
  // В режиме Keycloak «кто я» спрашиваем только после проверки сессии: иначе при обновлении страницы запрос уходил
  // со старым токеном, получал 401, приложение выходило из системы и на миг показывало форму входа.
  const keycloakCfg = metaQ.data?.keycloak
  const [kcReady, setKcReady] = useState(false)
  const authKnown = metaQ.isError || (metaQ.isSuccess && (authMode !== 'keycloak' || !keycloakCfg || kcReady))
  // placeholderData: когда Keycloak после проверки сессии выдаёт свежий токен, не гасим весь экран заставкой загрузки
  const me = useQuery({ queryKey: ['me', token], queryFn: api.me, enabled: !!token && authKnown, retry: false, staleTime: Infinity, placeholderData: (prev) => prev })
  const user = token ? me.data ?? null : null
  const role = user ? ROLES.find((r) => r.id === user.role) ?? null : null

  const forget = useCallback(() => {
    setToken(null)
    setTokenState(null)
    queryClient.clear()
  }, [queryClient])

  // выход по кнопке; в режиме Keycloak завершаем и его сессию
  const logout = useCallback(() => {
    forget()
    if (authMode === 'keycloak') keycloakLogout()
  }, [forget, authMode])

  // Сервер ответил 401 — токен истёк или отозван: забываем его и показываем вход.
  // Сессию Keycloak не трогаем: пока она жива, повторный вход пройдёт без пароля.
  useEffect(() => {
    window.addEventListener(UNAUTHORIZED_EVENT, forget)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, forget)
  }, [forget])

  // Keycloak: один раз проверяем сессию и, если пользователь уже вошёл, подхватываем токен.
  // При возврате со страницы входа Keycloak (#…code=…) до конца обмена кода на токен показываем загрузку, а не форму входа.
  const [kcCallback, setKcCallback] = useState(() => /[#&]code=/.test(window.location.hash))
  useEffect(() => {
    if (authMode !== 'keycloak' || !keycloakCfg) return
    initKeycloak(keycloakCfg).then(() => {
      setTokenState((prev) => {
        const next = getToken()
        if (prev && next !== prev) void queryClient.invalidateQueries()  // вход сменился — данные перечитаем в фоне
        return next
      })
      setKcCallback(false)
      setKcReady(true)
    })
  }, [authMode, keycloakCfg, queryClient])
  const kcBusy = kcCallback && (metaQ.isPending || authMode === 'keycloak')

  const open = useCallback((session: { token: string }) => {
    queryClient.clear()
    setToken(session.token)
    setTokenState(session.token)
  }, [queryClient])
  const login = useCallback(async (name: string, password: string) => open(await api.login(name, password)), [open])

  // ---------- данные ----------
  const enabled = !!user
  const live = { enabled, refetchInterval: POLL_MS }
  const sitesQ = useQuery({ queryKey: ['sites'], queryFn: api.sites, ...live })
  const zonesQ = useQuery({ queryKey: ['zones'], queryFn: api.zones, enabled })
  const stagesQ = useQuery({ queryKey: ['stages'], queryFn: api.stages, enabled })
  const rulesQ = useQuery({ queryKey: ['rules'], queryFn: api.rules, enabled })
  const camerasQ = useQuery({ queryKey: ['cameras'], queryFn: api.cameras, ...live })
  const snapshotsQ = useQuery({ queryKey: ['snapshots'], queryFn: api.snapshots, ...live })
  const alertsQ = useQuery({ queryKey: ['alerts'], queryFn: api.alerts, ...live })
  // без этих данных экраны показали бы неправду («Всё по плану» при недошедших отклонениях) — ждём их;
  // свежие кадры нужны только для миниатюр и индикатора свежести — без них приложение работает
  const queries = [sitesQ, zonesQ, stagesQ, rulesQ, camerasQ, alertsQ]

  // После действия ждём только данные, которые видны на экранах сразу (списки объектов, камер, отклонений):
  // остальное — отчёт, журнал, сверка техники — перечитываем в фоне, кнопка не крутится до самого медленного запроса.
  const refresh = useCallback(async () => {
    const isCore = (key: unknown) => typeof key === 'string' && CORE_KEYS.has(key)
    void queryClient.invalidateQueries({ predicate: (q) => !isCore(q.queryKey[0]) && q.queryKey[0] !== 'meta' })
    await queryClient.invalidateQueries({ predicate: (q) => isCore(q.queryKey[0]) })
  }, [queryClient])

  const data = useMemo(() => {
    const sites = sitesQ.data ?? [], zones = zonesQ.data ?? [], stages = stagesQ.data ?? []
    const cameras = camerasQ.data ?? [], alerts = alertsQ.data ?? []
    // снимки-доказательства приходят вместе с предупреждениями: старый кадр может уже не попасть в ленту свежих
    const snapshotMap = new Map<string, Snapshot>()
    for (const alert of alerts) for (const s of alert.evidenceSnapshots) snapshotMap.set(s.id, s)
    for (const s of snapshotsQ.data ?? []) snapshotMap.set(s.id, s)
    const snapshots = [...snapshotMap.values()].sort((a, b) => b.takenAt.localeCompare(a.takenAt))

    const index = <T extends { id: string }>(rows: T[]) => new Map(rows.map((r) => [r.id, r]))
    const siteMap = index(sites), zoneMap = index(zones), stageMap = index(stages), cameraMap = index(cameras)
    return {
      sites, zones, stages, cameras, alerts, snapshots,
      rules: Object.fromEntries((rulesQ.data ?? []).map((r) => [r.key, r])) as Record<string, Rule>,
      lastDataAt: snapshots[0]?.takenAt ?? null,
      bySite: (id: string) => siteMap.get(id),
      byZone: (id: string) => zoneMap.get(id),
      byStage: (id: string | null) => (id ? stageMap.get(id) : undefined),
      byCamera: (id: string | null) => (id ? cameraMap.get(id) : undefined),
      // камеру могли удалить, а её кадры остаются доказательствами — показываем их с пометкой, а не прячем
      cameraOf: (snapshot: Snapshot) => cameraMap.get(snapshot.cameraId) ?? removedCamera(snapshot.cameraId),
      bySnapshot: (id: string) => snapshotMap.get(id),
      stagesOf: (siteId: string) => stages.filter((s) => s.siteId === siteId),
      snapshotsOf: (cameraId: string) => snapshots.filter((s) => s.cameraId === cameraId),
      alertsForSite: (siteId: string) => alerts.filter((a: Alert) => a.siteId === siteId),
      siteStatus: (siteId: string): SiteStatus => {
        const openAlerts = alerts.filter((a) => a.siteId === siteId && isOpen(a.status))
        if (openAlerts.some((a) => a.severity === 'high')) return 'critical'
        return openAlerts.length > 0 ? 'warning' : 'ok'
      },
    }
  }, [sitesQ.data, zonesQ.data, stagesQ.data, rulesQ.data, camerasQ.data, snapshotsQ.data, alertsQ.data])

  // ---------- действия ----------
  /** Выполнить запрос, показать результат, обновить данные. true — получилось. */
  const run = useCallback(async (action: () => Promise<unknown>, done?: string): Promise<boolean> => {
    try {
      await action()
      if (done) notify(done)
      return true
    } catch (error) {
      notify(message(error), 'error')
      return false
    } finally {
      await refresh()
    }
  }, [notify, refresh])

  const updateAlert = useCallback((id: string, status: AlertStatus, comment: string, dueDate?: string) =>
    run(() => api.alertAction(id, status, comment, dueDate)), [run])
  const saveRule = useCallback((rule: Rule) => run(() => api.saveRule(rule), `Правило «${rule.stageName}» сохранено`), [run])
  const deleteCamera = useCallback((camera: Camera) => run(() => api.deleteCamera(camera.id), `${camera.name} удалена`), [run])
  const addCamera = useCallback(async (camera: NewCamera) => {
    try {
      return await api.addCamera(camera)
    } finally {
      await refresh()
    }
  }, [refresh])

  // один объект на все экраны: пересоздаём, только когда что-то поменялось, — иначе перерисовывались бы все, кто вызывает useApp()
  const meta = metaQ.data
  const value = useMemo<AppState>(() => ({
    user, role, ownSiteId: user?.role === 'foreman' ? user.siteIds[0] ?? null : null, base: role ? `/${role.id}` : '', authMode, meta,
    login, keycloakLogin, logout,
    ...data, refresh, updateAlert, saveRule, addCamera, deleteCamera, run, notify,
  }), [user, role, authMode, meta, login, logout, data, refresh, updateAlert, saveRule, addCamera, deleteCamera, run, notify])

  // ---------- состояния загрузки ----------
  const failed = queries.find((q) => q.isError && !q.data)
  const loading = kcBusy || (!!token && me.isPending) || (enabled && queries.some((q) => q.isPending))
  let screen = children
  // !me.data: разовый сбой фонового обновления (перезапуск сервера) не должен закрывать уже загруженное приложение
  if (token && me.isError && !me.data && !(me.error instanceof ApiError && me.error.status === 401)) {
    screen = <Splash error={message(me.error)} onRetry={() => me.refetch()} onExit={logout} />
  } else if (failed) {
    screen = <Splash error={message(failed.error)} onRetry={refresh} onExit={logout} />
  } else if (loading) {
    screen = <Splash />
  }
  return <Ctx.Provider value={value}><ToastCtx.Provider value={toasts}>{screen}</ToastCtx.Provider></Ctx.Provider>
}

/** Подпись и фон для кадра удалённой камеры */
function removedCamera(id: string): Camera {
  return {
    id, siteId: '', zoneId: '', name: 'Камера удалена', online: false, enabled: false, spiderEnabled: false, status: 'unknown', sourceType: 'rtsp',
    address: null, hasCredentials: false, demo: false, streamPath: '', lastError: null, lastSnapshotAt: null, scene: 'yard',
  }
}

/** Экран на весь вид: идёт загрузка или сервер недоступен */
function Splash({ error, onRetry, onExit }: { error?: string; onRetry?: () => void; onExit?: () => void }) {
  return (
    <div className="min-h-dvh flex flex-col items-center justify-center gap-4 px-6 text-center" role={error ? 'alert' : 'status'}>
      {error ? (
        <>
          <AlertTriangle className="w-10 h-10 text-warn" />
          <p className="text-lg font-semibold max-w-md">{error}</p>
          <div className="flex gap-3">
            <Button size="lg" onClick={onRetry}>Повторить</Button>
            <Button size="lg" variant="outline" onClick={onExit}>Выйти</Button>
          </div>
        </>
      ) : (
        <>
          <Loader2 className="w-9 h-9 text-primary animate-spin" />
          <p className="text-muted-foreground">Загружаем данные…</p>
        </>
      )}
    </div>
  )
}
