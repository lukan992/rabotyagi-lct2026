/**
 * Типы данных интерфейса. Совпадают с JSON, который отдаёт бэкенд (backend/app/schemas.py).
 */

export type EquipmentType =
  | 'excavator'     // экскаватор
  | 'dump_truck'    // самосвал
  | 'roller'        // каток
  | 'manipulator'   // кран-манипулятор
  | 'mixer'         // автобетоносмеситель
  | 'bulldozer'     // бульдозер
  | 'truck'         // грузовик
  | 'crane'         // автокран

export interface EquipmentInfo {
  type: EquipmentType
  name: string           // «Экскаватор»
  namePlural: string     // «экскаваторы»
  genitivePlural: string // «экскаваторов»
  color: string          // цвет рамки на снимке
}

export type RoleId = 'foreman' | 'manager' | 'inspector' | 'admin'

export interface Role {
  id: RoleId
  title: string
  subtitle: string
  description: string
}

export interface User {
  id: string
  login: string
  name: string
  role: RoleId
  phone: string
  siteIds: string[]
  isActive: boolean
}

export type SiteStatus = 'ok' | 'warning' | 'critical'

/** Вид объекта — как в «Справочнике видов работ»: у дороги и у школы разные работы и техника. Уходит сервисам аналитики;
 *  public (соцобъект без уточнения), industrial и other в справочнике нет — для сервисов это «вид неизвестен» */
export type SiteKind =
  | 'housing' | 'education' | 'preschool' | 'healthcare' | 'sports' | 'culture' | 'administrative' | 'office' | 'roads'
  | 'public' | 'industrial' | 'other'

export interface Site {
  id: string
  name: string
  address: string
  contractor: string
  foreman: string
  kind: SiteKind
  /** Рабочее время (часы местные, workTo не входит; 0–24 — круглосуточно) и рабочие дни пн…вс: «1111110».
   *  Вне его технику не сверяем и кадры сервисам аналитики не отправляем */
  workFrom: number
  workTo: number
  workDays: string
  currentStageId: string | null
  /** По всему плану объекта: сколько должно быть сделано по графику и сколько по факту, %. null — плана нет */
  planProgress: number | null
  factProgress: number | null
}

export type ZoneKind = 'work' | 'gate' | 'storage'

export interface Zone {
  id: string
  siteId: string
  name: string
  kind: ZoneKind
}

/** Камера всегда принимает RTSP; публичный HTTPS отдаёт её по HLS, локальная установка — по WebRTC/WHEP. */
export interface Camera {
  id: string
  siteId: string
  zoneId: string
  name: string
  /** Включена и на связи */
  online: boolean
  enabled: boolean
  /** Передавать контекст Spider в анализ этой видеокамеры. */
  spiderEnabled: boolean
  status: 'online' | 'offline' | 'unknown'
  sourceType: 'rtsp'
  /** Адрес видеопотока без логина и пароля */
  address: string | null
  hasCredentials: boolean
  /** Смотрит на демо-ролик шлюза */
  demo: boolean
  streamPath: string
  lastError: string | null
  lastSnapshotAt: string | null
  scene: 'pit' | 'foundation' | 'road' | 'entrance' | 'yard'
}

/** Что видит анализ на камере прямо сейчас (кадр из видео разбирается каждые 2 секунды) */
export interface LiveCamera {
  cameraId: string
  online: boolean
  error: string | null
  receivedAt: string | null
  analyzedAt: string | null
  analyzed: boolean | null
  note: string | null
  detections: Detection[]
  counts: Partial<Record<EquipmentType, number>>
}

export interface Stage {
  id: string
  siteId: string
  parentId: string | null
  level: 1 | 2
  name: string
  start: string
  end: string
  status: 'done' | 'in_progress' | 'planned'
  ruleKey: string | null
  /** Сколько должно быть сделано к сегодняшнему дню по графику, % */
  planProgress: number
  /** Сколько сделано по факту, % (у этапа с работами считается по работам) */
  factProgress: number
  factUpdatedAt: string | null
  /** Вид работ по справочнику сервисов аналитики и версия справочника, по которой его выбрали (только у работ) */
  catalogStageId: number | null
  catalogVersion: string | null
}

/** risk — чем грозит нехватка; сервер всегда отдаёт строку (пустую, если не задано) */
export interface RuleRequirement { type: EquipmentType; min: number; why: string; risk: string }
export interface RuleForbidden { type: EquipmentType; why: string; risk: string }

/** Правило методики: «этап работ → необходимая техника» */
export interface Rule {
  key: string
  stageName: string
  description: string
  required: RuleRequirement[]
  allowed: EquipmentType[]
  unexpected: RuleForbidden[]
  /** Сколько проверок подряд подтверждают отклонение */
  confirmAfterSnapshots: number
}

/** Новое правило: название этапа и описание, технику добавляют потом в редакторе */
export interface RuleInput { stageName: string; description: string }

/** Работа из файла плана: ошибки не дают загрузить план, пояснения — к сведению */
export interface PlanImportWork {
  /** Номер строки в файле — чтобы найти её в Excel */
  line: number
  name: string
  start: string | null
  end: string | null
  ruleKey: string | null
  catalogStageId: number | null
  errors: string[]
  notes: string[]
}

export interface PlanImportPhase {
  line: number
  name: string
  start: string | null
  end: string | null
  errors: string[]
  works: PlanImportWork[]
}

/** Что получится из файла плана (или что загрузилось — applied) */
export interface PlanImport {
  fileName: string
  phases: PlanImportPhase[]
  works: number
  errors: number
  /** Этапов и работ уже в плане объекта — при замене они удалятся */
  existing: number
  applied: boolean
}

export interface Detection {
  id: string
  type: EquipmentType
  confidence: number
  /** Рамка в процентах от кадра 16:9 */
  box: { x: number; y: number; w: number; h: number }
  moving?: boolean | null
}

export interface Snapshot {
  id: string
  cameraId: string
  takenAt: string
  imageUrl: string
  detections: Detection[]
  /** false — кадр получен, но разобрать его не удалось */
  analyzed: boolean
  provider: string | null
  note: string | null
}

export type AlertKind = 'missing' | 'count_below' | 'unexpected' | 'idle' | 'camera_offline'
export type Severity = 'high' | 'medium' | 'low'
export type AlertStatus = 'new' | 'acknowledged' | 'confirmed' | 'prescribed' | 'resolved' | 'false_positive'

export interface AlertEvent { at: string; who: string; text: string }

export interface Alert {
  id: string
  code: string
  siteId: string
  zoneId: string
  stageId: string | null
  cameraId: string | null
  kind: AlertKind
  severity: Severity
  status: AlertStatus
  isOpen: boolean
  title: string
  summary: string
  consequence: string
  advice: string
  equipment: EquipmentType | null
  expected: number | null
  observed: number | null
  prescriptionNo: string | null
  /** Срок устранения по предписанию, «2026-09-30» */
  prescriptionDue: string | null
  startedAt: string
  updatedAt: string
  evidence: string[]
  evidenceSnapshots: Snapshot[]
  history: AlertEvent[]
}

// ---------- ответы отдельных методов ----------
/** not_detected — модель такую технику не распознаёт: проверяют на месте */
export interface CheckRow { type: EquipmentType; need: number; have: number; state: 'ok' | 'low' | 'missing' | 'not_detected'; why: string }
export interface ExtraRow { type: EquipmentType; have: number; why: string }

export interface EquipmentCheckResult {
  siteId: string
  stageId: string | null
  stageName: string | null
  coverage: boolean
  /** Идёт ли рабочее время объекта: вне его технику не сверяем */
  working: boolean
  /** Рабочее время словами: «8:00–20:00, пн–сб» */
  workHours: string
  checkedAt: string | null
  rows: CheckRow[]
  extra: ExtraRow[]
  arriving: Partial<Record<EquipmentType, number>>
}

// ---------- работы по камерам: ответы сервисов аналитики (контракт frame-analysis-v1) ----------
/** deterministic — «по технике» (матрица техники, история, сроки), vlm_llm — «по снимку» (нейросеть смотрит кадр) */
export type AnalyticsService = 'deterministic' | 'vlm_llm'

/** Работа плана в ответе сервиса; удалённая из плана — с названием вида работ из справочника */
export interface WorkRef { stepKey: string; stageId: number | null; name: string; inPlan: boolean }

export interface WorkEvidence { source: string; role: string; explanation: string }

/** Одна операция на кадре: specific — одна работа, ambiguous — одна из нескольких */
export interface WorkGroup {
  match: string
  works: WorkRef[]
  /** operation_indicated — видно, что работа идёт; presence_or_result_only — только техника или результат; not_evaluated */
  visualState: string
  explanation: string
  evidence: WorkEvidence[]
  area: number[] | null
}

/** Следующая работа по технике: possible_start — похоже, началась */
export interface Transition {
  status: string
  current: WorkRef | null
  next: WorkRef | null
  firstAt: string | null
  lastAt: string | null
  points: number
}

export interface ScheduleItem { work: WorkRef; status: string; reason: string; overdueS: number | null; evidenceAt: string | null }
export interface Schedule { status: string; items: ScheduleItem[] }


export type ResourceReasonCode =
  | 'no_resource_plan'
  | 'no_resource_target'
  | 'scope_unknown'
  | 'resource_scope_unknown'
  | 'plan_class_unmapped'
  | 'duplicate_planned_class'
  | 'planned_quantity_unknown'
  | 'observation_unavailable'
  | 'partial_coverage'
  | 'unknown_coverage'
  | 'duplicate_observed_class'
  | 'observation_class_missing'
  | 'observation_class_unmapped'
  | 'non_production_source'
  | 'timestamp_unverified'
  | 'observation_requires_validation'
  | 'demonstration_mode'
  | 'no_independent_measurements'
  | 'no_planned_equipment'
  | 'vlm_resources_not_evaluated'

export interface ResourceMeasure {
  value: number
  unit: string
}

export interface ResourceEquipment {
  itemId: string
  classCode: string | null
  plannedQuantity: number | null
  visibleCount: number | null
  visibleCountDelta: number | null
  status: 'visible_below_plan' | 'visible_equal_plan' | 'visible_above_plan' | 'unknown'
  reasonCodes: ResourceReasonCode[]
  evidenceRefs: string[]
}

/** Проверенный сервисом результат сопоставления ресурсов для одного снимка. */
export interface ResourceAssessment {
  status: 'compared' | 'demonstration' | 'insufficient_evidence' | 'not_evaluated'
  reasonCodes: ResourceReasonCode[]
  stageCode: string | null
  selectionBasis: 'planned_at_frame_time' | 'confirmed_current' | 'explicit' | null
  basis: 'cv_detections' | 'manual_visual_estimate' | 'unavailable'
  coverage: 'full_scope' | 'partial_scope' | 'unknown'
  equipmentItems: ResourceEquipment[]
  plannedVolume: ResourceMeasure | null
  plannedWorkShifts: number | null
  plannedProductivity: ResourceMeasure | null
  actualVolume: null
  actualProductivity: null
  evidenceRefs: string[]
  limitations: string[]
}

/** Безопасное описание доказательства из metadata того же запроса анализа. */
export interface ResourceEvidence {
  pointer: string
  description: string
  available: boolean
}

/** Последний ответ одного сервиса по кадру камеры (или почему его нет) */
export interface ServiceAnswer {
  service: AnalyticsService
  state: 'pending' | 'done' | 'error' | 'unknown'
  at: string | null
  observedAt: string | null
  /** assessed | insufficient_evidence | outside_plan | no_plan | scope_unknown */
  outcome: string | null
  groups: WorkGroup[]
  transition: Transition | null
  schedule: Schedule | null
  limitations: string[]
  model: string | null
  errorCode: string | null
  error: string | null
  /** По более свежему кадру уже спросили — ждём ответ */
  newerPending: boolean
  /** Ресурсное сопоставление ровно для этого снимка; null для v1 или без результата v2. */
  resourceAssessment: ResourceAssessment | null
  /** Режим v2-анализа ровно для этого снимка. */
  analysisMode: 'demonstration' | 'operational' | null
  /** Безопасные описания доказательств resourceAssessment, без сырого metadata. */
  resourceEvidence: ResourceEvidence[]
}

export interface CameraWork {
  cameraId: string
  cameraName: string
  zoneName: string
  sentAt: string | null
  imageUrl: string | null
  answers: ServiceAnswer[]
  /** Хоть один кандидат — работа, которая идёт сегодня по графику; null — не с чем сравнить */
  matchesPlan: boolean | null
}

/** Какая работа идёт на кадрах камер рабочих зон (GET /sites/{id}/work-analysis) */
export interface SiteWork {
  /** Подключён хоть один сервис аналитики */
  enabled: boolean
  services: AnalyticsService[]
  canRun: boolean
  running: boolean
  /** Сколько запросов к нейросети ждут свободный общий слот */
  llmWaiting: number
  planned: string[]
  /** Почему план не уходит сервисам */
  planIssue: string | null
  /** Почему последний кадр не ушёл */
  problem: string | null
  cameras: CameraWork[]
  catalogVersion: string | null
  nextAt: string | null
  /** Есть успешный импорт источника для площадки. */
  sourceConfigured: boolean
}

export interface SpiderPlannedValue { value: number | null; unit: string | null }
export interface SpiderEquipmentResource {
  sourceName: string
  plannedQuantity: number | null
  unitProductivity: number | null
  limitations: string[]
}
export interface SpiderStageResource {
  code: string
  name: string
  start: string
  finish: string
  plannedWorkShifts: number | null
  plannedVolume: SpiderPlannedValue
  plannedProductivity: SpiderPlannedValue
  equipment: SpiderEquipmentResource[]
}
export interface SpiderResources {
  stages: SpiderStageResource[]
  limitations: string[]
}
export interface SpiderImport {
  id: string
  status: 'pending' | 'succeeded' | 'failed'
  snapshotId: string | null
  startedAt: string
  finishedAt: string | null
  errorCode: string | null
  errorMessage: string | null
}
export interface SpiderSnapshot {
  id: string
  resourceRevisionId: string
  sourceUrl: string
  apiVersion: string
  dataSource: string | null
  dataType: string | null
  warning: string | null
  createdAt: string
  resources: SpiderResources
  sourceObservations: Record<string, unknown>[]
  manualAnnotationType: string | null
  manualRequiresValidation: boolean | null
  manualAnnotations: Record<string, unknown>[]
  sourceComparisons: Record<string, unknown>[]
}
export interface SpiderSource {
  snapshot: SpiderSnapshot | null
  lastImport: SpiderImport | null
  lastSuccessAt: string | null
  stale: boolean
  limitations: string[]
}

/** Подключение конкретного объекта к Spider. Секрет токена сервер никогда не возвращает. */
export interface SpiderConnection {
  url: string | null
  hasToken: boolean
  configured: boolean
  custom: boolean
}

/** Самостоятельно загруженная фотография объекта и результаты сервисов аналитики. */
export interface PhotoAnalysis {
  id: string
  imageUrl: string
  at: string
  answers: ServiceAnswer[]
  /** Сколько запросов к нейросети ждут свободный общий слот */
  llmWaiting: number
}
export type SpiderObservationTarget = Record<string, unknown>
export interface SpiderObservationAsset {
  id: string
  snapshotId: string
  observationId: string
  imageSha256: string
  mediaType: string
  width: number
  height: number
  observedAt: string
  timestampQuality: string
  fetchedAt: string
  imageUrl: string
  target: SpiderObservationTarget | null
  targetError: string | null
  dataType: string | null
  warning: string | null
}

export type EquipmentVisitStatus = 'active' | 'completed' | 'lost'
export interface EquipmentVisit {
  id: string
  cameraId: string
  trackerSessionId: string
  trackId: string
  equipmentClass: EquipmentType
  firstSeenAt: string
  lastSeenAt: string
  durationSeconds: number
  status: EquipmentVisitStatus
  confidence: number | null
  closedAt: string | null
  closeReason: string | null
  hasObservationGap: boolean
}
export interface EquipmentEvent {
  eventId: string
  cameraId: string
  trackerSessionId: string
  visitId: string
  trackId: string
  equipmentClass: EquipmentType
  eventType: 'appeared' | 'disappeared'
  timestamp: string
  confidence: number | null
  receivedAt: string
}
export interface EquipmentVisitPage {
  items: EquipmentVisit[]
  total: number
  limit: number
  offset: number
}
export interface EquipmentEventPage {
  items: EquipmentEvent[]
  total: number
  limit: number
  offset: number
}

/** Вид работ справочника. no_class — работа без техники (геодезия, отселение): по кадрам её не видно, сервисы следят
 *  только за сроками и отметками выполнения */
export interface CatalogWork { stageId: number; name: string; path: string[]; kind: 'concrete' | 'no_class' }

/** Виды работ справочника сервисов аналитики — для поля «Вид работ по справочнику» */
export interface AnalyticsCatalog { enabled: boolean; version: string | null; objectType: string | null; works: CatalogWork[]; error: string | null }

export interface Deviation { kind: 'missing' | 'count_below' | 'unexpected'; type: EquipmentType; need: number | null; have: number; title: string; why: string }

export interface AnalyzeResult {
  provider: string
  model: string | null
  supported: boolean
  note: string | null
  elapsedMs: number
  imageUrl: string | null
  detections: Detection[]
  ruleKey: string
  stageName: string
  rows: CheckRow[]
  deviations: Deviation[]
}


export interface Connection {
  protocol: 'rtsp'
  host: string
  port: number | null
  path: string
  username: string | null
  password: string | null
}

/** previewPath — временный поток в шлюзе: форма показывает по нему видео камеры до сохранения */
export interface ProbeResult { ok: boolean; code: string; message: string; elapsedMs: number; previewPath: string | null }

export interface CameraPatch { name?: string; enabled?: boolean; spiderEnabled?: boolean; zoneId?: string; connection?: Connection }

export interface NewCamera {
  siteId: string
  name: string
  zoneId: string | null
  newZoneName: string | null
  newZoneKind: ZoneKind
  connection: Connection
  allowOffline: boolean
  spiderEnabled: boolean
}

/** Демо-ролик шлюза как обычный RTSP-адрес — для быстрой настройки в форме камеры */
export interface DemoFeed { clip: string; title: string; host: string; port: number; path: string }

export interface Meta {
  version: string
  demoMode: boolean
  analysisProvider: string
  /** Какую технику анализ кадров умеет находить (своя модель — не всю) */
  detectableEquipment: EquipmentType[]
  timezone: string
  /** Время сервера в ISO — по нему видно, не разошлись ли часы устройства и сервера */
  serverTime: string
  database: 'sqlite' | 'postgresql'
  authMode: 'local' | 'keycloak'
  keycloak: { url: string; realm: string; clientId: string } | null
  video: { enabled: boolean; webrtcUrl: string; frameIntervalS: number; checkIntervalS: number }
  /** Рамки в реальном времени (WebSocket /api/tracks): модель на сервере разбирает видео или их шлёт внешний сервис разметки */
  tracker: {
    enabled: boolean
    connected: boolean
    videoDelayMs: number
    /** Когда пришло последнее сообщение с рамками */
    lastMessageAt: string | null
  }
  demoFeeds: DemoFeed[]
}

// ---------- администрирование ----------
export interface SiteInput {
  name: string
  address: string
  contractor: string
  kind?: SiteKind
  workFrom?: number
  workTo?: number
  workDays?: string
  /** id прораба; null — снять прораба с объекта; поля нет — прораба не менять */
  foremanId?: string | null
}

export interface ZoneInput { name: string; kind: ZoneKind }

export interface StageInput {
  name: string; level: 1 | 2; parentId: string | null; start: string; end: string; ruleKey: string | null; factProgress?: number | null
  /** Вид работ по справочнику сервисов аналитики; null — снять; поля нет — не менять */
  catalogStageId?: number | null
}

export interface UserInput { login: string; name: string; role: RoleId; phone: string; siteIds: string[]; password: string }

export interface UserPatch { name?: string; role?: RoleId; phone?: string; siteIds?: string[]; isActive?: boolean }

/** Запись журнала действий: кто, когда, что сделал */
export interface AuditEvent {
  id: number
  at: string
  actorLogin: string
  actorName: string
  actorRole: RoleId | ''
  action: string
  entityType: string
  entityId: string | null
  entityName: string
  summary: string
  details: Record<string, unknown>
  ip: string
}

export interface WeeklyReport {
  dateFrom: string
  dateTo: string
  total: number
  open: number
  resolved: number
  falsePositive: number
  byDay: { date: string; label: string; count: number }[]
  bySite: { siteId: string; name: string; count: number; high: number }[]
  byKind: { key: AlertKind; count: number }[]
  byEquipment: { key: EquipmentType; count: number }[]
}
