/**
 * Живое видео камер идёт через MediaMTX. Локальная установка использует WebRTC/WHEP,
 * а HTTPS-развёртывание за ingress — HLS через same-origin nginx-прокси.
 *
 * В обоих случаях шлюз спрашивает бэкенд, можно ли пользователю читать конкретную камеру.
 */
import Hls from 'hls.js'
import { useEffect, useRef, useState } from 'react'
import { getFreshToken } from '@/api'
import type { Camera, LiveCamera, Meta } from '@/data'

export type StreamState = 'idle' | 'connecting' | 'playing' | 'error'

const RETRY_MS = 10_000 // видео прервалось — пробуем снова: камера могла вернуться
const RETRY_MAX_MS = 60_000 // камера молчит долго — пробуем реже, но не реже раза в минуту
// «disconnected» у WebRTC обычно временный (моргнула сеть, телефон сменил вышку) и сам проходит за пару секунд
const DISCONNECT_GRACE_MS = 5_000

/**
 * Адрес шлюза из настроек сервера. Если сервер назвал его «localhost», а приложение открыто по адресу в сети
 * (например, с телефона), подставляем этот адрес — иначе телефон искал бы шлюз у себя.
 */
export function gatewayUrl(meta: Meta | undefined): string | null {
  if (!meta?.video.enabled) return null
  // относительный адрес (/webrtc за тем же nginx) считаем от адреса приложения; без второго аргумента new URL упал бы
  const url = new URL(meta.video.webrtcUrl, window.location.origin)
  if (['localhost', '127.0.0.1'].includes(url.hostname) && !['localhost', '127.0.0.1'].includes(window.location.hostname)) {
    url.hostname = window.location.hostname
  }
  return url.origin + url.pathname.replace(/\/$/, '')
}

/** На сколько придержать видео, чтобы рамки в реальном времени совпадали с картинкой (0 — таких рамок нет) */
export function trackerDelay(meta: Meta | undefined): number {
  // браузер принимает 0–4000 мс; за пределами присваивание бросает ошибку — страница с видео не должна из-за этого падать
  return meta?.tracker?.enabled ? Math.min(Math.max(meta.tracker.videoDelayMs, 0), 4000) : 0
}

export function whepUrl(meta: Meta | undefined, streamPath: string): string | null {
  const base = gatewayUrl(meta)
  return base ? `${base}/${streamPath}/whep` : null
}

export interface StreamSource {
  transport: 'hls' | 'whep'
  url: string
}

/**
 * Публичный ingress открывает только HTTPS. Если сервер также объявил свой WebRTC-адрес
 * тем же HTTPS-origin, HLS доступен через /hls/ в nginx. Не используем этот путь для
 * другого origin: заголовок Authorization не должен уйти на чужой хост.
 */
export function hlsUrl(meta: Meta | undefined, streamPath: string): string | null {
  if (!meta?.video.enabled || !streamPath) return null
  const gateway = gatewayUrl(meta)
  if (!gateway) return null
  const origin = new URL(gateway).origin
  if (window.location.protocol !== 'https:' || origin !== window.location.origin) return null
  return `/hls/${encodeURIComponent(streamPath)}/index.m3u8`
}

/** HLS на публичном HTTPS, WHEP во всех остальных (в частности локальных) установках. */
export function streamSource(meta: Meta | undefined, streamPath: string): StreamSource | null {
  const hls = hlsUrl(meta, streamPath)
  if (hls) return { transport: 'hls', url: hls }
  const whep = whepUrl(meta, streamPath)
  return whep ? { transport: 'whep', url: whep } : null
}

/** Подключиться к потоку (url = null — отключиться). Возвращает ссылку для <video> и состояние. */
export function useWhep(url: string | null, delayMs = 0) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const pcRef = useRef<RTCPeerConnection | null>(null)
  // состояние помнит, к какому адресу и какой попытке относится: сменился адрес или пошла новая попытка —
  // пока это «подключаемся» (раньше при повторе всё время переподключения висело «нет сигнала»)
  const [status, setStatus] = useState<{ url: string; attempt: number; state: StreamState } | null>(null)
  const [attempt, setAttempt] = useState(0)
  const failures = useRef(0)  // неудачи подряд: от них растёт пауза перед повтором; видео пошло — счёт с нуля

  useEffect(() => {
    if (!url) return
    let stopped = false
    let resourceUrl = ''
    let pc: RTCPeerConnection | null = null
    let retry: number | undefined
    let grace: number | undefined
    const setState = (state: StreamState) => setStatus({ url, attempt, state })
    const dropSession = () => {
      if (!resourceUrl) return
      const sessionUrl = resourceUrl
      resourceUrl = ''
      void getFreshToken().then((token) => fetch(sessionUrl, {
        method: 'DELETE',
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      }).catch(() => {}))
    }
    const fail = () => {
      if (stopped) return
      setState('error')
      dropSession()
      pc?.close()
      window.clearTimeout(retry)
      window.clearTimeout(grace)
      const pause = Math.min(RETRY_MS * 2 ** Math.min(failures.current, 3), RETRY_MAX_MS)
      failures.current += 1
      retry = window.setTimeout(() => setAttempt((n) => n + 1), pause)
    }

    async function connect(target: string) {
      const conn = new RTCPeerConnection()
      pc = conn
      pcRef.current = conn
      conn.addTransceiver('video', { direction: 'recvonly' })
      conn.ontrack = (e) => {
        if (videoRef.current && e.streams[0]) videoRef.current.srcObject = e.streams[0]
      }
      conn.onconnectionstatechange = () => {
        if (stopped) return
        window.clearTimeout(grace)
        if (conn.connectionState === 'connected') { failures.current = 0; setState('playing') }
        else if (conn.connectionState === 'failed') fail()
        else if (conn.connectionState === 'disconnected') grace = window.setTimeout(fail, DISCONNECT_GRACE_MS)
      }
      await conn.setLocalDescription(await conn.createOffer())
      // WHEP без trickle-ICE: ждём сбора кандидатов (не дольше 1,5 с), затем отправляем предложение целиком
      await new Promise<void>((resolve) => {
        if (conn.iceGatheringState === 'complete') return resolve()
        const finish = () => { window.clearTimeout(timer); conn.removeEventListener('icegatheringstatechange', check); resolve() }
        const check = () => { if (conn.iceGatheringState === 'complete') finish() }
        const timer = window.setTimeout(finish, 1500)
        conn.addEventListener('icegatheringstatechange', check)
      })
      if (stopped) return

      const headers: Record<string, string> = { 'Content-Type': 'application/sdp' }
      const token = await getFreshToken()  // в режиме Keycloak токен мог истечь, пока открыта страница
      if (stopped) return
      if (token) headers.Authorization = `Bearer ${token}`
      const response = await fetch(target, { method: 'POST', headers, body: conn.localDescription!.sdp })
      if (!response.ok) throw new Error(`шлюз ответил ${response.status}`)
      const location = response.headers.get('Location')
      if (location) resourceUrl = new URL(location, target).href
      if (stopped) return dropSession() // закрыли, пока шлюз отвечал
      const answer = await response.text()
      if (stopped) return
      await conn.setRemoteDescription({ type: 'answer', sdp: answer })
    }

    connect(url).catch(fail)
    return () => {
      stopped = true
      window.clearTimeout(retry)
      window.clearTimeout(grace)
      dropSession()
      pc?.close()
    }
  }, [url, attempt])

  const state: StreamState = !url ? 'idle' : status?.url === url && status.attempt === attempt ? status.state : 'connecting'

  // Придержать видео: браузер показывает кадр на столько позже — как раз пока сервер разбирает тот же кадр.
  // jitterBufferTarget есть в Chrome, Edge и Firefox; где его нет — видео идёт без задержки, рамки чуть отстают.
  useEffect(() => {
    if (state !== 'playing') return
    for (const receiver of pcRef.current?.getReceivers() ?? []) {
      const r = receiver as RTCRtpReceiver & { jitterBufferTarget?: number | null }
      try {
        if ('jitterBufferTarget' in r) r.jitterBufferTarget = delayMs || null
      } catch { /* старый браузер или недопустимое значение — видео просто без задержки */ }
    }
  }, [state, delayMs])

  return { videoRef, state }
}

/** HLS не получает заголовок Authorization от нативного <video>; hls.js ставит его на каждый запрос. */
export function useHls(url: string | null) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [status, setStatus] = useState<{ url: string; attempt: number; state: StreamState } | null>(null)
  const [attempt, setAttempt] = useState(0)
  const failures = useRef(0)

  useEffect(() => {
    if (!url) return
    const target = url
    let stopped = false
    let retry: number | undefined
    let failed = false
    let video: HTMLVideoElement | null = null
    let player: { destroy: () => void } | null = null
    const setState = (state: StreamState) => setStatus({ url, attempt, state })
    const fail = () => {
      if (stopped || failed) return
      failed = true
      setState('error')
      player?.destroy()
      const pause = Math.min(RETRY_MS * 2 ** Math.min(failures.current, 3), RETRY_MAX_MS)
      failures.current += 1
      retry = window.setTimeout(() => setAttempt((n) => n + 1), pause)
    }

    function connect() {
      if (stopped) return
      if (!Hls.isSupported()) {
        setState('error')
        return
      }
      video = videoRef.current
      if (!video) return
      const hls = new Hls({
        // xhrSetup runs for manifests, segments and low-latency parts. Opening before awaiting
        // is required by XMLHttpRequest before a request header can be attached.
        xhrSetup: async (xhr, requestUrl) => {
          xhr.open('GET', requestUrl, true)
          const token = await getFreshToken()
          if (!token) throw new Error('Для просмотра видео нужен действующий вход.')
          xhr.setRequestHeader('Authorization', `Bearer ${token}`)
        },
      })
      player = hls
      hls.on(Hls.Events.MEDIA_ATTACHED, () => hls.loadSource(target))
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        if (stopped) return
        video?.play().catch(fail)
      })
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) fail()
      })
      const onPlaying = () => {
        failures.current = 0
        setState('playing')
      }
      video.addEventListener('playing', onPlaying)
      hls.attachMedia(video)
      return () => video?.removeEventListener('playing', onPlaying)
    }

    const removePlayingListener = connect()
    return () => {
      stopped = true
      window.clearTimeout(retry)
      removePlayingListener?.()
      player?.destroy()
      if (video) {
        video.removeAttribute('src')
        video.load()
      }
    }
  }, [url, attempt])

  const state: StreamState = !url ? 'idle' : status?.url === url && status.attempt === attempt ? status.state : 'connecting'
  return { videoRef, state }
}

/** Выбирает transport в одном месте, чтобы плитки, полный просмотр и probe не расходились. */
export function useVideo(source: StreamSource | null, delayMs = 0) {
  const whep = useWhep(source?.transport === 'whep' ? source.url : null, delayMs)
  const hls = useHls(source?.transport === 'hls' ? source.url : null)
  return source?.transport === 'hls' ? hls : whep
}

/** Что показать на плитке камеры: в эфире, подключаемся, нет сигнала, выключена */
export type TileStatus = 'live' | 'connecting' | 'offline' | 'disabled'

export function tileStatus(camera: Camera, live: LiveCamera | undefined, state: StreamState): TileStatus {
  if (!camera.enabled) return 'disabled'
  if (state === 'playing') return 'live'
  if (state === 'error' || (live && !live.online && state !== 'connecting')) return 'offline'
  return 'connecting'
}

/** Элемент на экране (с запасом) — только тогда держим видео: десятки невидимых потоков зря грузят сеть */
export function useInView<T extends Element>(margin = '150px') {
  const ref = useRef<T>(null)
  const [inView, setInView] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { rootMargin: margin })
    observer.observe(el)
    return () => observer.disconnect()
  }, [margin])
  return { ref, inView }
}

/** Вкладка на виду? Свернули или ушли на другую — видео отключаем */
export function usePageVisible() {
  const [visible, setVisible] = useState(() => document.visibilityState === 'visible')
  useEffect(() => {
    const onChange = () => setVisible(document.visibilityState === 'visible')
    document.addEventListener('visibilitychange', onChange)
    return () => document.removeEventListener('visibilitychange', onChange)
  }, [])
  return visible
}
