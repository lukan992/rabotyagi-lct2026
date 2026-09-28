import { useCallback } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import type { Alert } from '@/data'
import { useApp } from '@/store/context'

/**
 * Значение фильтра в адресе страницы (?site=s1): переживает обновление, «Назад» и пересылку ссылки.
 * Смена фильтра не плодит записи в истории — «Назад» уводит со страницы, а не перебирает фильтры.
 */
export function useSearchParam<T extends string>(name: string, fallback: T, allowed?: readonly T[]): [T, (value: T) => void] {
  const [params, setParams] = useSearchParams()
  const raw = params.get(name)
  const value = raw !== null && (!allowed || allowed.includes(raw as T)) ? raw as T : fallback
  const set = useCallback((next: T) => {
    setParams((prev) => {
      const p = new URLSearchParams(prev)
      if (next === fallback) p.delete(name)
      else p.set(name, next)
      return p
    }, { replace: true })
  }, [name, fallback, setParams])
  return [value, set]
}

type Opened = { alertOpened?: boolean } | null

/**
 * Открытое отклонение — в адресе (?alert=<id>): ссылку можно переслать, а «Назад» закрывает карточку,
 * а не уводит со страницы.
 */
export function useOpenAlert() {
  const { alerts } = useApp()
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const id = params.get('alert')
  const alert = id ? alerts.find((a) => a.id === id) ?? null : null

  const open = useCallback((a: Alert) => {
    setParams((prev) => {
      const p = new URLSearchParams(prev)
      p.set('alert', a.id)
      return p
    }, { state: { alertOpened: true } })
  }, [setParams])

  const close = useCallback(() => {
    // открыли здесь же — возвращаемся на шаг назад; пришли по ссылке — просто убираем отклонение из адреса
    if ((location.state as Opened)?.alertOpened) navigate(-1)
    else setParams((prev) => {
      const p = new URLSearchParams(prev)
      p.delete('alert')
      return p
    }, { replace: true })
  }, [location.state, navigate, setParams])

  return { alert, open, close }
}
