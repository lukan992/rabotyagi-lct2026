import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Все даты показываем по Москве независимо от настроек устройства */
const TZ = 'Europe/Moscow'

/** Форматирует время «ЧЧ:ММ» из ISO-строки */
export function fmtTime(iso: string) {
  const d = new Date(iso)
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: TZ })
}

/** «12:30:07» — как на экранной надписи камеры */
export function fmtTimeSec(iso: string) {
  return new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: TZ })
}

/** Форматирует дату «15 сентября» */
export function fmtDate(iso: string) {
  const d = new Date(iso)
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', timeZone: TZ })
}

/** «15.09.2026» */
export function fmtDateShort(iso: string) {
  const d = new Date(iso)
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: TZ })
}

/** Человекочитаемая давность: «2 ч назад», «15 мин назад» */
export function ago(iso: string, now = new Date()) {
  const diff = Math.max(0, (now.getTime() - new Date(iso).getTime()) / 60000)
  if (diff < 1) return 'только что'
  if (diff < 60) return `${Math.round(diff)} мин назад`
  const h = Math.floor(diff / 60)
  if (h < 24) return `${h} ч назад`
  const d = Math.floor(h / 24)
  return d === 1 ? 'вчера' : `${d} дн. назад`
}

/** «Вторник, 15 сентября» — сегодняшняя дата по Москве */
export function todayLabel(now = new Date()) {
  const text = now.toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long', timeZone: TZ })
  return text[0].toUpperCase() + text.slice(1)
}

/** Один и тот же календарный день по Москве? */
export function isSameDay(a: string | Date, b: string | Date = new Date()) {
  const day = (d: string | Date) => new Date(d).toLocaleDateString('ru-RU', { timeZone: TZ })
  return day(a) === day(b)
}

/** Сегодняшний день по Москве в виде «2026-09-24» — так же записаны даты плана */
export function todayISO(now = new Date()) {
  return now.toLocaleDateString('sv-SE', { timeZone: TZ })
}

/** Сколько дней от одной даты плана до другой («2026-09-24» → «2026-10-02» = 8) */
export function daysBetween(from: string, to: string) {
  return Math.round((Date.parse(to) - Date.parse(from)) / 86_400_000)
}

/** Время, а если день не сегодняшний — ещё и дата: «12:30» или «19 сент., 15:00» */
export function fmtWhen(iso: string) {
  if (isSameDay(iso)) return fmtTime(iso)
  return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', timeZone: TZ }) + ', ' + fmtTime(iso)
}

/** Цвет текста на плашке цвета `bg` (#rrggbb): чёрный или белый — где контраст по WCAG выше. Белый на жёлтом и салатовом не читался. */
export function inkOn(bg: string): '#000' | '#fff' {
  const lin = (i: number) => {
    const c = parseInt(bg.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }
  const luminance = 0.2126 * lin(1) + 0.7152 * lin(3) + 0.0722 * lin(5)
  return (luminance + 0.05) / 0.05 >= 1.05 / (luminance + 0.05) ? '#000' : '#fff'
}

export function plural(n: number, one: string, few: string, many: string) {
  const m10 = n % 10, m100 = n % 100
  if (m10 === 1 && m100 !== 11) return `${n} ${one}`
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return `${n} ${few}`
  return `${n} ${many}`
}

/** Только слово в нужной форме: pluralWord(2, 'объект', 'объекта', 'объектов') → «объекта» */
export function pluralWord(n: number, one: string, few: string, many: string) {
  return plural(n, one, few, many).replace(/^\d+\s/, '')
}

/** «Кузнецов Андрей» → «Кузнецов А.» */
export function shortName(full: string) {
  const [last, first] = full.split(' ')
  return first ? `${last} ${first[0]}.` : last
}
