/**
 * Клиент API. Адреса относительные: в разработке Vite проксирует /api и /media на бэкенд,
 * в Docker то же самое делает nginx. Чтобы ходить на другой сервер, задайте VITE_API_URL.
 */

const API_URL: string = import.meta.env.VITE_API_URL ?? '/api'
const MEDIA_ORIGIN = /^https?:\/\//.test(API_URL) ? new URL(API_URL).origin : ''
const TOKEN_KEY = 'sk-token'

/** Адрес WebSocket того же сервера, что и API: wsUrl('/tracks') → ws(s)://сервер/api/tracks */
export function wsUrl(path: string): string {
  const base = new URL(API_URL, window.location.href)  // относительный /api — от адреса страницы, VITE_API_URL — как есть
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:'
  base.pathname = base.pathname.replace(/\/$/, '') + path
  return base.toString()
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

let token: string | null = null
try { token = localStorage.getItem(TOKEN_KEY) } catch { /* приватный режим */ }

export const getToken = () => token

/** Токен, продлённый при необходимости, — для запросов мимо request() (видеошлюз) */
export async function getFreshToken() {
  if (ensureFreshToken && token) await ensureFreshToken().catch(() => {})
  return token
}

export function setToken(value: string | null) {
  token = value
  try {
    if (value) localStorage.setItem(TOKEN_KEY, value)
    else localStorage.removeItem(TOKEN_KEY)
  } catch { /* приватный режим */ }
}

/** Продление токена перед запросом — задаёт вход через Keycloak (store/keycloak.ts); у своих токенов его нет */
let ensureFreshToken: (() => Promise<void>) | null = null
export function setTokenRefresher(fn: (() => Promise<void>) | null) {
  ensureFreshToken = fn
}

/** Сервер сообщил, что вход больше не действует — хранилище выходит из системы */
export const UNAUTHORIZED_EVENT = 'sk:unauthorized'

function messageOf(payload: unknown, status: number): string {
  const detail = (payload as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length) return 'Проверьте заполнение полей формы'
  if (status === 413) return 'Файл слишком большой. Выберите фото поменьше.'  // nginx отвечает на это HTML-страницей
  return status >= 500 ? 'Ошибка на сервере. Попробуйте ещё раз.' : `Запрос не выполнен (${status})`
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  // не удалось продлить — отправляем как есть: сервер ответит 401, и приложение покажет вход
  if (ensureFreshToken && token) await ensureFreshToken().catch(() => {})
  const sent = token
  const form = body instanceof FormData
  const headers: Record<string, string> = {}
  if (sent) headers.Authorization = `Bearer ${sent}`
  if (body !== undefined && !form) headers['Content-Type'] = 'application/json'

  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, {
      method, headers, body: body === undefined ? undefined : form ? body : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'Нет связи с сервером. Проверьте, что он запущен, и попробуйте ещё раз.')
  }
  if (response.status === 204) return undefined as T
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    // выходим, только если отказали текущему токену: поздний ответ на запрос со старым токеном не должен выбить новый вход
    if (response.status === 401 && sent && sent === token && path !== '/auth/login') {
      setToken(null)
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    }
    throw new ApiError(response.status, messageOf(payload, response.status))
  }
  return payload as T
}

/** Файл с сервера (шаблон Excel) — с тем же входом, что и остальные запросы: простая ссылка токен не передаст */
export async function downloadFile(path: string): Promise<Blob> {
  if (ensureFreshToken && token) await ensureFreshToken().catch(() => {})
  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
  } catch {
    throw new ApiError(0, 'Нет связи с сервером. Проверьте, что он запущен, и попробуйте ещё раз.')
  }
  if (!response.ok) throw new ApiError(response.status, messageOf(await response.json().catch(() => null), response.status))
  return response.blob()
}

/** Адрес картинки: кадры лежат на бэкенде (/media/…), предпросмотр приходит как data: */
export function mediaUrl(path: string): string {
  return /^(data:|blob:|https?:)/.test(path) ? path : `${MEDIA_ORIGIN}${path}`
}
