import { describe, expect, it } from 'vitest'
import type { LiveCamera, Meta } from '@/data'
import { tileStatus, trackerDelay } from './video'
import { camera } from '@/test/fixtures'

const live = (online: boolean) => ({ cameraId: 'c1', online }) as LiveCamera

describe('tileStatus — что показать на плитке камеры', () => {
  it('выключенная камера — «выключена», что бы ни было с видео', () => {
    expect(tileStatus(camera({ enabled: false }), live(true), 'playing')).toBe('disabled')
  })
  it('видео идёт — «в эфире»', () => {
    expect(tileStatus(camera(), live(false), 'playing')).toBe('live')
  })
  it('ошибка подключения или сервер не получает видео — «нет сигнала»', () => {
    expect(tileStatus(camera(), live(true), 'error')).toBe('offline')
    expect(tileStatus(camera(), live(false), 'idle')).toBe('offline')
  })
  it('пока подключаемся — не пугаем «нет сигнала», даже если сервер ещё не видел видео', () => {
    expect(tileStatus(camera(), live(false), 'connecting')).toBe('connecting')
    expect(tileStatus(camera(), undefined, 'idle')).toBe('connecting')
  })
})

describe('trackerDelay', () => {
  it('видео придерживаем, только когда идут рамки в реальном времени', () => {
    const tracker = { connected: true, videoDelayMs: 150, lastMessageAt: null }
    expect(trackerDelay({ tracker: { ...tracker, enabled: true } } as Meta)).toBe(150)
    expect(trackerDelay({ tracker: { ...tracker, enabled: false } } as Meta)).toBe(0)
    expect(trackerDelay(undefined)).toBe(0)
    expect(trackerDelay({ tracker: { ...tracker, enabled: true, videoDelayMs: 90_000 } } as Meta)).toBe(4000)  // браузер больше не примет
  })
})
