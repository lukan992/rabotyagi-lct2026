/**
 * Рамки техники в реальном времени (своя модель на сервере или внешний сервис разметки → браузер по WebSocket /api/tracks).
 *
 * Одно подключение на вкладку, сколько бы плиток ни было открыто: каждая плитка подписывается на свою камеру,
 * а серверу уходит список камер «на экране» — лишние рамки по сети не гоняем.
 * Токен уходит первым сообщением, а не в адресе: адреса попадают в журналы сервера и nginx.
 * Трек, пропавший на одно-два сообщения, ещё немного держим — иначе рамка мигала бы.
 * Если по камере давно ничего нет, считаем, что данных нет (null), — тогда плитка покажет рамки из анализа кадров.
 * «Давно» зависит от того, как часто идут сообщения: модель на сервере — 5–8 раз в секунду (на видеостене — реже,
 * камер много), внешний сервис — 10–15, а пока видео камеры не разбирается — раз в 2 секунды.
 */
import { useCallback, useSyncExternalStore } from 'react'
import { getFreshToken, getToken, wsUrl } from '@/api'
import type { EquipmentType } from '@/data'

export interface TrackObject {
  trackId: string
  type: EquipmentType
  confidence: number
  box: { x: number; y: number; w: number; h: number }
}

interface TrackMessage { cameraId: string; ts: string | null; objects: TrackObject[] }

/** Кто вошёл — по токену (свой и Keycloak — оба JWT). Токен Keycloak обновляется каждые несколько минут, а человек тот же */
function subjectOf(token: string | null): string | null {
  if (!token) return null
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))) as { sub?: unknown }
    return typeof payload.sub === 'string' ? payload.sub : token
  } catch {
    return token
  }
}

const HOLD_MS = 400  // трек не пришёл — держим рамку ещё столько (при редких сообщениях — полтора промежутка)
const STALE_MS = 1500  // по камере ничего нет дольше — данных нет (при редких сообщениях — три промежутка)
const STALE_MAX_MS = 8000
const PACE_MS = 125  // промежуток между сообщениями, пока он не измерен
const RETRY_MIN_MS = 1000, RETRY_MAX_MS = 30_000
const LINGER_MS = 5000  // все плитки закрылись — подключение держим ещё немного: вдруг сразу откроют другую

class TrackHub {
  private ws: WebSocket | null = null
  private subject: string | null = null  // кто вошёл, когда открывали подключение: сменился пользователь — переподключаемся
  private wanted = new Map<string, number>()  // камера → сколько компонентов на неё подписано
  private listeners = new Map<string, Set<() => void>>()
  /** Камера → рамки. Нет записи — данных нет; пустой список — сервис сообщил, что техники в кадре нет */
  private frames = new Map<string, TrackObject[]>()
  private seen = new Map<string, Map<string, { obj: TrackObject; at: number }>>()
  private pace = new Map<string, { at: number; ms: number }>()  // камера → прошлое сообщение и средний промежуток
  private staleTimers = new Map<string, number>()
  private retry = RETRY_MIN_MS
  private retryTimer: number | undefined
  private lingerTimer: number | undefined
  private disabled = false  // сервер ответил «рамок в реальном времени нет» — не стучимся зря
  // до 15 сообщений в секунду на камеру: перерисовываем плитку не чаще раза за кадр экрана, а не на каждое
  private dirty = new Set<string>()
  private frame: number | undefined

  subscribe(cameraId: string, listener: () => void) {
    this.wanted.set(cameraId, (this.wanted.get(cameraId) ?? 0) + 1)
    let set = this.listeners.get(cameraId)
    if (!set) this.listeners.set(cameraId, (set = new Set()))
    set.add(listener)
    window.clearTimeout(this.lingerTimer)
    if (this.ws && subjectOf(getToken()) !== this.subject) this.close()  // вышли и вошли другим, пока сокет ещё жил
    this.ensureOpen()
    this.sendWanted()
    return () => {
      set.delete(listener)
      const n = (this.wanted.get(cameraId) ?? 1) - 1
      if (n > 0) this.wanted.set(cameraId, n)
      else {
        this.wanted.delete(cameraId)
        this.drop(cameraId)
      }
      this.sendWanted()
      if (!this.wanted.size) this.lingerTimer = window.setTimeout(() => this.close(), LINGER_MS)
    }
  }

  get(cameraId: string): TrackObject[] | null {
    return this.frames.get(cameraId) ?? null
  }

  /** Средний промежуток между сообщениями камеры, мс */
  paceOf(cameraId: string): number {
    return this.pace.get(cameraId)?.ms ?? PACE_MS
  }

  private ensureOpen() {
    if (this.ws || this.disabled || this.retryTimer !== undefined) return
    void this.open()
  }

  private async open() {
    const token = await getFreshToken()
    if (!token || !this.wanted.size || this.ws) return
    const ws = new WebSocket(wsUrl('/tracks'))
    this.ws = ws
    this.subject = subjectOf(token)
    ws.onopen = () => ws.send(JSON.stringify({ token, subscribe: [...this.wanted.keys()] }))
    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data as string) as TrackMessage | { type: 'ready' }
        // паузу между повторами сбрасываем только после входа: иначе при отказе (4401) стучались бы каждую секунду
        if ('type' in data) this.retry = RETRY_MIN_MS
        else this.receive(data)
      } catch { /* кривое сообщение пропускаем */ }
    }
    ws.onclose = (e) => {
      if (this.ws !== ws) return
      this.ws = null
      if (e.code === 4404) { this.disabled = true; return }  // рамок в реальном времени на сервере нет
      if (!this.wanted.size) return
      // 4401 — вход не действует (токен истёк, сотрудника отключили): при повторе возьмём свежий токен;
      // 4503 — сервер входа не отвечает: подождём подольше, пауза растёт до 30 с
      this.retryTimer = window.setTimeout(() => { this.retryTimer = undefined; this.ensureOpen() }, this.retry)
      this.retry = Math.min(this.retry * 2, RETRY_MAX_MS)
    }
  }

  private close() {
    window.clearTimeout(this.retryTimer)
    this.retryTimer = undefined
    const ws = this.ws
    this.ws = null
    this.subject = null
    ws?.close()
  }

  private sendWanted() {
    // до onopen список уйдёт вместе с токеном; после — отдельным сообщением
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ subscribe: [...this.wanted.keys()] }))
  }

  private receive(msg: TrackMessage) {
    if (!this.wanted.has(msg.cameraId)) return
    const now = performance.now()
    // сообщения стали реже — сразу верим новому промежутку (иначе рамки мигали бы, пока среднее догонит), чаще —
    // привыкаем постепенно; перерыв (камера молчала, вкладку не смотрели) — не промежуток между сообщениями
    const prev = this.pace.get(msg.cameraId)
    let ms = prev?.ms ?? PACE_MS
    const gap = prev ? now - prev.at : 0
    if (prev && gap <= STALE_MAX_MS) ms = gap > ms ? gap : ms * 0.8 + gap * 0.2
    this.pace.set(msg.cameraId, { at: now, ms })
    const tracks = this.seen.get(msg.cameraId) ?? new Map()
    for (const obj of msg.objects) tracks.set(obj.trackId, { obj, at: now })
    for (const [id, t] of tracks) if (now - t.at > Math.max(HOLD_MS, ms * 1.5)) tracks.delete(id)
    this.seen.set(msg.cameraId, tracks)
    this.frames.set(msg.cameraId, [...tracks.values()].map((t) => t.obj))
    this.notify(msg.cameraId)
    window.clearTimeout(this.staleTimers.get(msg.cameraId))
    const stale = Math.min(Math.max(STALE_MS, ms * 3), STALE_MAX_MS)
    this.staleTimers.set(msg.cameraId, window.setTimeout(() => this.drop(msg.cameraId), stale))
  }

  private drop(cameraId: string) {
    window.clearTimeout(this.staleTimers.get(cameraId))
    this.staleTimers.delete(cameraId)
    this.seen.delete(cameraId)  // средний промежуток помним: рамки вернутся — будут идти так же часто
    if (this.frames.delete(cameraId)) this.notify(cameraId)
  }

  private notify(cameraId: string) {
    this.dirty.add(cameraId)
    this.frame ??= requestAnimationFrame(() => {
      this.frame = undefined
      const cameras = [...this.dirty]
      this.dirty.clear()
      for (const id of cameras) this.listeners.get(id)?.forEach((fn) => fn())
    })
  }
}

const hub = new TrackHub()

/** Как часто сейчас приходят рамки камеры (средний промежуток, мс): за это время рамка доезжает до нового места */
export function trackPace(cameraId: string): number {
  return hub.paceOf(cameraId)
}

/**
 * Рамки камеры прямо сейчас: список (может быть пустым — техники нет) или null — рамок в реальном времени по ней нет.
 * cameraId = null — не подписываться.
 */
export function useTracks(cameraId: string | null): TrackObject[] | null {
  // подписка — та же функция, пока камера та же: иначе каждый рендер переподписывал бы плитку
  const subscribe = useCallback((fn: () => void) => (cameraId ? hub.subscribe(cameraId, fn) : () => {}), [cameraId])
  return useSyncExternalStore(subscribe, () => (cameraId ? hub.get(cameraId) : null))
}
