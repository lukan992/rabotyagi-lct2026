import { describe, expect, it } from 'vitest'
import { ago, daysBetween, inkOn, plural, pluralWord, shortName } from './utils'

describe('plural — числительные по-русски', () => {
  it.each([
    [1, '1 день'], [2, '2 дня'], [5, '5 дней'], [11, '11 дней'], [12, '12 дней'], [21, '21 день'], [22, '22 дня'], [111, '111 дней'],
  ])('%i', (n, text) => {
    expect(plural(n, 'день', 'дня', 'дней')).toBe(text)
  })
  it('только слово', () => {
    expect(pluralWord(3, 'объект', 'объекта', 'объектов')).toBe('объекта')
  })
})

describe('даты', () => {
  it('дни между датами плана', () => {
    expect(daysBetween('2026-09-24', '2026-10-02')).toBe(8)
    expect(daysBetween('2026-10-02', '2026-09-24')).toBe(-8)
  })
  it('давность словами', () => {
    const now = new Date('2026-09-25T12:00:00Z')
    expect(ago('2026-09-25T11:59:40Z', now)).toBe('только что')
    expect(ago('2026-09-25T11:45:00Z', now)).toBe('15 мин назад')
    expect(ago('2026-09-25T09:00:00Z', now)).toBe('3 ч назад')
    expect(ago('2026-09-24T09:00:00Z', now)).toBe('вчера')
    expect(ago('2026-09-20T09:00:00Z', now)).toBe('5 дн. назад')
  })
})

describe('прочее', () => {
  it('цвет подписи на цветной плашке — контрастный', () => {
    expect(inkOn('#F59E0B')).toBe('#000')  // жёлтый экскаватор
    expect(inkOn('#1F5FD1')).toBe('#fff')
  })
  it('короткое имя', () => {
    expect(shortName('Кузнецов Андрей')).toBe('Кузнецов А.')
  })
})
