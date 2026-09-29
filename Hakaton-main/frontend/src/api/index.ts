import { downloadFile, request } from './client'
import type {
  Alert, AlertStatus, AnalyticsCatalog, AuditEvent, Camera, CameraPatch, Connection, EquipmentCheckResult,
  EquipmentEventPage, EquipmentVisitPage, LiveCamera, Meta, NewCamera, PhotoAnalysis, PlanImport, ProbeResult, Rule, RuleInput,
  Site, SiteInput, SiteWork, Snapshot, SpiderConnection, SpiderImport, SpiderObservationAsset, SpiderSource, SpiderStageLinks, Stage, StageInput, User,
  UserInput, UserPatch, WeeklyReport, Zone, ZoneInput,
} from '@/data'

export { ApiError, getFreshToken, getToken, mediaUrl, setToken, setTokenRefresher, UNAUTHORIZED_EVENT, wsUrl } from './client'

interface Session { token: string; user: User }

function photoAnalysisForm(image: File, useSpider: boolean): FormData {
  const form = new FormData()
  form.append('image', image)
  form.append('useSpider', String(useSpider))
  return form
}

const query = (params: Record<string, string | number | null | undefined>) => {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== '').map(([k, v]) => [k, String(v)])).toString()
  return q ? `?${q}` : ''
}

export const api = {
  meta: () => request<Meta>('GET', '/meta'),

  login: (login: string, password: string) => request<Session>('POST', '/auth/login', { login, password }),
  me: () => request<User>('GET', '/auth/me'),

  sites: () => request<Site[]>('GET', '/sites'),
  zones: () => request<Zone[]>('GET', '/zones'),
  stages: () => request<Stage[]>('GET', '/stages'),
  rules: () => request<Rule[]>('GET', '/rules'),
  saveRule: (rule: Rule) => request<Rule>('PUT', `/rules/${rule.key}`, rule),
  createRule: (rule: RuleInput) => request<Rule>('POST', '/rules', rule),
  deleteRule: (key: string) => request<void>('DELETE', `/rules/${key}`),

  cameras: () => request<Camera[]>('GET', '/cameras'),
  probeCamera: (connection: Connection) => request<ProbeResult>('POST', '/cameras/probe', connection),
  closeProbe: (path: string) => request<void>('DELETE', `/cameras/probe/${path}`),
  addCamera: (camera: NewCamera) => request<Camera>('POST', '/cameras', camera),
  patchCamera: (id: string, patch: CameraPatch) => request<Camera>('PATCH', `/cameras/${id}`, patch),
  deleteCamera: (id: string) => request<void>('DELETE', `/cameras/${id}`),
  testCamera: (id: string) => request<ProbeResult>('POST', `/cameras/${id}/test`),
  /** Что видит анализ на камерах прямо сейчас */
  live: (siteId?: string) => request<LiveCamera[]>('GET', `/live${query({ siteId })}`),

  snapshots: () => request<Snapshot[]>('GET', '/snapshots?perCamera=8'),
  alerts: () => request<Alert[]>('GET', '/alerts'),
  alertAction: (id: string, status: AlertStatus, comment: string, dueDate?: string) =>
    request<Alert>('POST', `/alerts/${id}/actions`, { status, comment, dueDate }),

  equipmentCheck: (siteId: string) => request<EquipmentCheckResult>('GET', `/sites/${siteId}/equipment-check`),
  /** Работы по камерам: последние ответы сервисов аналитики по кадрам камер рабочих зон */
  siteWork: (siteId: string) => request<SiteWork>('GET', `/sites/${siteId}/work-analysis`),
  /** Отправить свежие кадры сервисам сейчас — ответы придут в фоне (по снимку — до нескольких минут) */
  runSiteWork: (siteId: string) => request<SiteWork>('POST', `/sites/${siteId}/work-analysis`),

  /** Сохранённый защищённый снимок источника Camera Stage Monitor. */
  spider: (siteId: string) => request<SpiderSource>('GET', `/sites/${siteId}/spider`),
  spiderStageLinks: (siteId: string) => request<SpiderStageLinks>('GET', `/sites/${siteId}/spider/stage-links`),
  saveSpiderStageLink: (siteId: string, body: { snapshotId: string; stageCode: string; stepKey: string | null }) =>
    request<SpiderStageLinks>('PUT', `/sites/${siteId}/spider/stage-links`, body),
  /** Импорт всегда получает ровно пять документов источника; подтверждается в UI. */
  importSpider: (siteId: string) => request<SpiderImport>('POST', `/sites/${siteId}/spider/import`),
  prepareSpiderObservation: (siteId: string, observationId: string, snapshotId: string) =>
    request<SpiderObservationAsset>('POST', `/sites/${siteId}/spider/observations/${observationId}/prepare`, { snapshotId }),
  /** Картинка источника доступна только через bearer-запрос, а не обычный img src. */
  spiderImage: (imageUrl: string) => downloadFile(imageUrl.startsWith('/api/') ? imageUrl.slice('/api'.length) : imageUrl),

  /** Настройка Spider отдельна для каждого объекта; токен читается только при сохранении и никогда не возвращается. */
  spiderConnection: (siteId: string) => request<SpiderConnection>('GET', `/sites/${siteId}/spider/connection`),
  saveSpiderConnection: (siteId: string, connection: { url: string; token?: string | null }) =>
    request<SpiderConnection>('PUT', `/sites/${siteId}/spider/connection`, connection),

  /** Независимый анализ загруженной фотографии объекта: без камеры и без старого Rule API. */
  createPhotoAnalysis: (siteId: string, image: File, useSpider: boolean) =>
    request<PhotoAnalysis>('POST', `/sites/${siteId}/photo-analyses`, photoAnalysisForm(image, useSpider)),
  photoAnalyses: (siteId: string) => request<PhotoAnalysis[]>('GET', `/sites/${siteId}/photo-analyses`),
  photoAnalysis: (siteId: string, id: string) => request<PhotoAnalysis>('GET', `/sites/${siteId}/photo-analyses/${id}`),
  photoAnalysisImage: (siteId: string, id: string) => downloadFile(`/sites/${siteId}/photo-analyses/${id}/image`),
  equipmentVisits: (siteId: string, filters: {
    cameraId?: string
    equipmentClass?: string
    from?: string
    to?: string
    status?: string
    limit?: number
    offset?: number
  }) => request<EquipmentVisitPage>('GET', `/sites/${siteId}/equipment-visits${query(filters)}`),
  equipmentEvents: (siteId: string, filters: {
    cameraId?: string
    equipmentClass?: string
    from?: string
    to?: string
    limit?: number
    offset?: number
  }) => request<EquipmentEventPage>('GET', `/sites/${siteId}/equipment-events${query(filters)}`),
  /** Виды работ справочника сервисов аналитики — для плана объекта */
  analyticsCatalog: (siteId: string) => request<AnalyticsCatalog>('GET', `/analytics/catalog${query({ siteId })}`),
  weeklyReport: () => request<WeeklyReport>('GET', '/reports/weekly'),

  // ---------- администрирование ----------
  createSite: (site: SiteInput) => request<Site>('POST', '/sites', site),
  updateSite: (id: string, site: SiteInput) => request<Site>('PATCH', `/sites/${id}`, site),
  deleteSite: (id: string) => request<void>('DELETE', `/sites/${id}`),
  createZone: (siteId: string, zone: ZoneInput) => request<Zone>('POST', `/sites/${siteId}/zones`, zone),
  updateZone: (id: string, zone: ZoneInput) => request<Zone>('PATCH', `/zones/${id}`, zone),
  deleteZone: (id: string) => request<void>('DELETE', `/zones/${id}`),
  createStage: (siteId: string, stage: StageInput) => request<Stage>('POST', `/sites/${siteId}/stages`, stage),
  updateStage: (id: string, stage: StageInput) => request<Stage>('PATCH', `/stages/${id}`, stage),
  deleteStage: (id: string) => request<void>('DELETE', `/stages/${id}`),
  setStageProgress: (id: string, factProgress: number) => request<Stage>('PATCH', `/stages/${id}/progress`, { factProgress }),
  /** Шаблон плана работ (Excel) со списками правил и видов работ этого объекта */
  planTemplate: (siteId: string) => downloadFile(`/sites/${siteId}/plan/template`),
  /** План из Excel или CSV: без apply — только предпросмотр, план не меняется */
  importPlan: (siteId: string, file: File, options: { apply?: boolean; replace?: boolean } = {}) => {
    const form = new FormData()
    form.append('file', file)
    const params = query({ apply: options.apply ? 'true' : null, replace: options.replace ? 'true' : null })
    return request<PlanImport>('POST', `/sites/${siteId}/plan/import${params}`, form)
  },

  users: () => request<User[]>('GET', '/users'),
  createUser: (user: UserInput) => request<User>('POST', '/users', user),
  updateUser: (id: string, patch: UserPatch) => request<User>('PATCH', `/users/${id}`, patch),
  setPassword: (id: string, password: string) => request<void>('POST', `/users/${id}/password`, { password }),
  deleteUser: (id: string) => request<void>('DELETE', `/users/${id}`),

  audit: (filters: { action?: string; actor?: string; q?: string; beforeId?: number; limit?: number }) =>
    request<AuditEvent[]>('GET', `/audit${query(filters)}`),
}
