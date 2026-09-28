import { createContext, useContext } from 'react'
import type {
  Alert, AlertStatus, Camera, Meta, NewCamera, Role, Rule, Site, SiteStatus, Snapshot, Stage, User, Zone,
} from '@/data'

export interface Toast { id: number; text: string; tone: 'ok' | 'error' }

export interface AppState {
  // ---- кто вошёл ----
  user: User | null
  role: Role | null
  /** Объект прораба (у остальных ролей — null) */
  ownSiteId: string | null
  /** Начало адресов роли: «/manager», «/admin»… — у администратора те же экраны, что у руководителя */
  base: string
  authMode: 'local' | 'keycloak'
  /** Настройки сервера: адрес шлюза и режим авторизации */
  meta: Meta | undefined
  login: (login: string, password: string) => Promise<void>
  keycloakLogin: () => Promise<void>
  logout: () => void

  // ---- данные с сервера ----
  sites: Site[]
  zones: Zone[]
  cameras: Camera[]
  stages: Stage[]
  rules: Record<string, Rule>
  snapshots: Snapshot[]
  alerts: Alert[]
  /** Время самого свежего кадра — «данные на 12:30» */
  lastDataAt: string | null
  refresh: () => Promise<void>

  // ---- поиск по справочникам ----
  bySite: (id: string) => Site | undefined
  byZone: (id: string) => Zone | undefined
  byStage: (id: string | null) => Stage | undefined
  byCamera: (id: string | null) => Camera | undefined
  /** Камера снимка; для удалённой — заглушка «Камера удалена» (кадры-доказательства остаются) */
  cameraOf: (snapshot: Snapshot) => Camera
  bySnapshot: (id: string) => Snapshot | undefined
  stagesOf: (siteId: string) => Stage[]
  /** Снимки камеры, от новых к старым */
  snapshotsOf: (cameraId: string) => Snapshot[]
  alertsForSite: (siteId: string) => Alert[]
  siteStatus: (siteId: string) => SiteStatus

  // ---- действия: возвращают true при успехе, об ошибке сообщают сами ----
  /** dueDate — срок устранения, только для предписания */
  updateAlert: (id: string, status: AlertStatus, comment: string, dueDate?: string) => Promise<boolean>
  saveRule: (rule: Rule) => Promise<boolean>
  addCamera: (camera: NewCamera) => Promise<Camera>
  deleteCamera: (camera: Camera) => Promise<boolean>
  /** Выполнить действие администратора: показать итог, обновить данные. true — получилось */
  run: (action: () => Promise<unknown>, done?: string) => Promise<boolean>

  notify: (text: string, tone?: Toast['tone']) => void
}

export const Ctx = createContext<AppState | null>(null)
/** Всплывающие сообщения — отдельно: иначе каждый тост перерисовывал бы всё приложение */
export const ToastCtx = createContext<Toast[]>([])

export function useToasts() {
  return useContext(ToastCtx)
}

export function useApp() {
  const v = useContext(Ctx)
  if (!v) throw new Error('useApp вне AppProvider')
  return v
}
