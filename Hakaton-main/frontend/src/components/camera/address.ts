import type { Camera, Connection } from '@/data'

/** Адрес видеопотока камеры по полям формы: проверка, разбор вставленной ссылки, запрос к серверу */

export const RTSP_PORT = 554
/** Типовые пути видеопотока у распространённых производителей — чтобы не искать их в инструкции */
export const VENDORS: Record<string, { label: string; path: string }> = {
  hikvision: { label: 'Hikvision / HiWatch', path: '/Streaming/Channels/101' },
  dahua: { label: 'Dahua', path: '/cam/realmonitor?channel=1&subtype=0' },
  axis: { label: 'Axis', path: '/axis-media/media.amp' },
}
const HOST_RE = /^(?:\d{1,3}(?:\.\d{1,3}){3}|\[?[0-9a-fA-F:]+\]?|[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)*)$/

export interface Address { preset: string; host: string; port: string; path: string; username: string; password: string }

export function addressErrors(a: Address): Partial<Record<keyof Address, string>> {
  const errors: Partial<Record<keyof Address, string>> = {}
  if (!a.host.trim()) errors.host = 'Введите IP-адрес камеры'
  else if (!HOST_RE.test(a.host.trim())) errors.host = 'Это не похоже на IP-адрес. Пример: 192.168.1.64'
  if (a.port && !(Number(a.port) >= 1 && Number(a.port) <= 65535)) errors.port = 'Порт — число от 1 до 65535'
  if (!a.path.startsWith('/')) errors.path = 'Путь начинается с «/»'
  return errors
}

export function toConnection(a: Address): Connection {
  return {
    protocol: 'rtsp', host: a.host.trim(), port: a.port ? Number(a.port) : null, path: a.path.trim() || '/',
    username: a.username.trim() || null, password: a.password || null,
  }
}

/** Вставили ссылку целиком (rtsp://логин:пароль@адрес:порт/путь) — раскладываем по полям. Набор по буквам не трогаем. */
export function parseLink(text: string): Partial<Address> | null {
  const value = text.trim()
  if (!/^rtsp:\/\//i.test(value)) return null
  try {
    const url = new URL(value.replace(/^rtsp:/i, 'http:')) // у URL нет разбора rtsp — схема на время подменяется
    return {
      preset: '', host: url.hostname.replace(/^\[|\]$/g, ''), port: url.port || String(RTSP_PORT), path: decodeURI(url.pathname + url.search) || '/',
      username: decodeURIComponent(url.username), password: decodeURIComponent(url.password),
    }
  } catch {
    return null
  }
}

/** Разобрать сохранённый адрес камеры (без пароля) обратно по полям */
export function addressOf(camera: Camera): Address {
  const parsed = camera.address ? parseLink(camera.address) : null
  return { preset: '', host: '', port: String(RTSP_PORT), path: '/', username: '', password: '', ...parsed }
}
