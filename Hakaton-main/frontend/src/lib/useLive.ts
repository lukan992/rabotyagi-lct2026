import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api'
import type { LiveCamera } from '@/data'

/**
 * Что видит анализ на камерах прямо сейчас: сервер разбирает кадр из видео каждые 2 секунды.
 * Опрашиваем, только пока открыт экран с камерами (и пока вкладка на виду — так делает TanStack Query).
 */
export function useLive(enabled = true) {
  const query = useQuery({
    queryKey: ['live'],
    queryFn: () => api.live(),
    enabled,
    refetchInterval: 2000,
    refetchIntervalInBackground: false,
    retry: false,
  })
  return useMemo(() => new Map<string, LiveCamera>((query.data ?? []).map((c) => [c.cameraId, c])), [query.data])
}
