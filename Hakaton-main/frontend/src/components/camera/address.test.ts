import { describe, expect, it } from 'vitest'
import { addressErrors, addressOf, parseLink, toConnection, type Address } from './address'
import { camera } from '@/test/fixtures'

const blank: Address = { preset: '', host: '', port: '554', path: '/', username: '', password: '' }

describe('parseLink — вставили ссылку целиком', () => {
  it('раскладывает логин, пароль, адрес, порт и путь', () => {
    expect(parseLink('rtsp://admin:p%40ss@192.168.1.64:8554/Streaming/Channels/101')).toEqual({
      preset: '', host: '192.168.1.64', port: '8554', path: '/Streaming/Channels/101', username: 'admin', password: 'p@ss',
    })
  })
  it('без порта — стандартный 554, параметры пути сохраняются', () => {
    expect(parseLink('rtsp://cam.local/cam/realmonitor?channel=1&subtype=0')).toMatchObject({ port: '554', path: '/cam/realmonitor?channel=1&subtype=0' })
  })
  it('IPv6 — без квадратных скобок', () => {
    expect(parseLink('rtsp://[fe80::1]/live')?.host).toBe('fe80::1')
  })
  it('не ссылка rtsp — не трогаем (человек просто набирает адрес)', () => {
    expect(parseLink('192.168.1.64')).toBeNull()
    expect(parseLink('http://192.168.1.64/')).toBeNull()
  })
})

describe('addressErrors — проверка полей', () => {
  it('пустой и неправильный адрес', () => {
    expect(addressErrors(blank).host).toBe('Введите IP-адрес камеры')
    expect(addressErrors({ ...blank, host: 'камера на въезде' }).host).toMatch(/не похоже на IP-адрес/)
  })
  it('порт и путь', () => {
    expect(addressErrors({ ...blank, host: '10.0.0.5', port: '70000' }).port).toBeDefined()
    expect(addressErrors({ ...blank, host: '10.0.0.5', path: 'live' }).path).toBe('Путь начинается с «/»')
    expect(addressErrors({ ...blank, host: '10.0.0.5' })).toEqual({})
  })
})

describe('toConnection / addressOf', () => {
  it('пустые логин и пароль уходят на сервер как null', () => {
    expect(toConnection({ ...blank, host: ' 10.0.0.5 ', port: '' })).toEqual({ protocol: 'rtsp', host: '10.0.0.5', port: null, path: '/', username: null, password: null })
  })
  it('сохранённый адрес камеры раскладывается обратно по полям (пароль сервер не отдаёт)', () => {
    expect(addressOf(camera({ address: 'rtsp://admin@10.0.0.5:554/live' }))).toMatchObject({ host: '10.0.0.5', username: 'admin', password: '', path: '/live' })
    expect(addressOf(camera({ address: null }))).toEqual(blank)
  })
})
