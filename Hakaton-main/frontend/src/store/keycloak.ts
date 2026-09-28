/**
 * Вход через Keycloak (OpenID Connect). Включается, когда бэкенд в /meta сообщает authMode=keycloak.
 *
 * keycloak-js сам ведёт поток авторизации (redirect + PKCE) и обновляет токен; мы кладём свежий access-token
 * туда же, откуда его берёт API-клиент (setToken), поэтому остальной код запросов не меняется.
 */
import type Keycloak from 'keycloak-js'
import { setToken, setTokenRefresher } from '@/api'

export interface KeycloakConfig {
  url: string
  realm: string
  clientId: string
}

let instance: Keycloak | null = null
let ready: Promise<boolean> | null = null
let unavailable = false  // проверка сессии не удалась: Keycloak не отвечает или браузер не может войти
// Тихая проверка сессии идёт через скрытый iframe; если Keycloak его заблокирует (чужой адрес возврата, запрет фреймов),
// keycloak-js ждёт ответа бесконечно — а приложение ждёт проверку, прежде чем спросить сервер «кто я»
const INIT_TIMEOUT_MS = 8_000

/** Инициализировать один раз: тихо проверить активную сессию и подключить продление токена */
export function initKeycloak(cfg: KeycloakConfig): Promise<boolean> {
  if (ready) return ready
  // библиотека грузится, только когда вход идёт через Keycloak
  ready = import('keycloak-js')
    .then(async ({ default: KeycloakClient }) => {
      const kc = new KeycloakClient({ url: cfg.url, realm: cfg.realm, clientId: cfg.clientId })
      instance = kc
      const authenticated = await Promise.race([
        kc.init({
          onLoad: 'check-sso',
          pkceMethod: 'S256',
          silentCheckSsoRedirectUri: `${window.location.origin}/silent-check-sso.html`,
          checkLoginIframe: false,
        }),
        // не дождались — считаем, что сессии нет; кнопка входа при этом работает: переход на страницу Keycloak не нужен iframe
        new Promise<boolean>((resolve) => window.setTimeout(() => resolve(false), INIT_TIMEOUT_MS)),
      ])
      if (authenticated && kc.token) {
        setToken(kc.token)
        // Перед каждым запросом: осталось меньше 30 с — обновляем. Обновление ровно в момент истечения опаздывало:
        // опрос, ушедший в эту долю секунды, получал 401 и выкидывал из системы.
        setTokenRefresher(async () => {
          if ((await kc.updateToken(30)) && kc.token) setToken(kc.token)
        })
      }
      return authenticated
    })
    .catch(() => {
      unavailable = true
      return false
    })
  return ready
}

/** Перейти на страницу входа Keycloak. Если отсюда войти нельзя — понятная ошибка вместо молчащей кнопки. */
export async function keycloakLogin(): Promise<void> {
  // PKCE требует Web Crypto, а он есть только на https и localhost: по http://192.168.… с телефона вход не заработает
  if (!window.isSecureContext) throw new Error('Вход через Keycloak работает только по https или прямо на сервере (localhost).')
  await ready  // библиотека и проверка сессии могут ещё загружаться
  if (!instance || unavailable) throw new Error('Сервер входа Keycloak не отвечает. Проверьте, что он запущен, и обновите страницу.')
  await instance.login()
}

/** Выход по кнопке: завершаем и сессию Keycloak, иначе следующий вход прошёл бы без пароля */
export function keycloakLogout() {
  setTokenRefresher(null)
  if (instance?.authenticated) instance.logout({ redirectUri: window.location.origin })
}
