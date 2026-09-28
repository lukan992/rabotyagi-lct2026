import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@/api'
import type { ProbeResult } from '@/data'

/** Временный поток предпросмотра живёт в шлюзе до закрытия формы — потом убираем, не дожидаясь, пока истечёт сам */
export function useProbe() {
  const [probe, setProbe] = useState<ProbeResult | null>(null)
  const current = useRef<string | null>(null)
  const mounted = useRef(true)
  useEffect(() => {
    const previous = current.current
    current.current = probe?.previewPath ?? null
    if (previous && previous !== current.current) api.closeProbe(previous).catch(() => {})
  }, [probe])
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      if (current.current) api.closeProbe(current.current).catch(() => {})
    }
  }, [])
  // форму закрыли, пока шлюз отвечал: результат уже некому показать, а поток висел бы в шлюзе ещё 10 минут
  const set = useCallback((next: ProbeResult | null) => {
    if (mounted.current) setProbe(next)
    else if (next?.previewPath) api.closeProbe(next.previewPath).catch(() => {})
  }, [])
  return [probe, set] as const
}
