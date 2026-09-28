import type { Stage } from '@/data'
import { daysBetween, plural, todayISO } from './utils'

export type Tone = 'ok' | 'warn' | 'danger'

/** Из чего складывается выполнение объекта: работы и укрупнённые этапы без работ — так же считает сервер */
export function planUnits(stages: Stage[]): Stage[] {
  const withWorks = new Set(stages.filter((s) => s.level === 2).map((s) => s.parentId))
  return stages.filter((s) => s.level === 2 || !withWorks.has(s.id))
}

/**
 * На сколько дней отстали от графика (больше нуля) или опередили его (меньше нуля): сколько сделано по факту,
 * столько по графику должно было быть сделано N дней назад. Работы весят по длительности и по графику идут
 * равномерно по дням — поэтому у работы, этапа и всего объекта дни считаются одинаково. null — плана нет.
 */
export function lagDays(units: Stage[], today = todayISO()): number | null {
  // Дни — относительно сегодняшнего: 0 — сегодня, −1 — вчера
  const spans = units.map((u) => ({ start: daysBetween(today, u.start), days: Math.max(1, daysBetween(u.start, u.end) + 1) }))
  if (!spans.length) return null
  // Объём — в днях работы: сделано по факту и сколько должно быть сделано по графику к концу дня t
  const total = spans.reduce((n, s) => n + s.days, 0)
  const done = Math.min(total, units.reduce((n, u, i) => n + (u.factProgress * spans[i].days) / 100, 0))
  const due = (t: number) => spans.reduce((n, s) => n + Math.min(Math.max(t - s.start + 1, 0), s.days), 0)
  let t = 0
  if (due(0) > done) {
    while (due(t) > done) t -= 1  // последний день, к концу которого по графику было сделано не больше, чем сейчас
    return Math.round(-(t + (done - due(t)) / (due(t + 1) - due(t)))) || 0
  }
  if (due(0) < done) {
    while (due(t) < done) t += 1  // первый день, к концу которого по графику будет сделано столько, сколько уже есть
    return -Math.round(t - 1 + (done - due(t - 1)) / (due(t) - due(t - 1))) || 0
  }
  return 0
}

/**
 * Словами и цветом: успевают ли по графику. День — в пределах точности отметок (их ставят шагом 5%),
 * от пяти дней — рабочая неделя — красным. short — для тесных мест вроде карточки объекта.
 */
export function pace(lag: number): { tone: Tone; text: string; short: string } {
  const days = (n: number) => plural(n, 'день', 'дня', 'дней').replace(' ', ' ')  // «4 дня» не рвётся переносом
  if (lag <= -2) return { tone: 'ok', text: `опережает график примерно на ${days(-lag)}`, short: `опережает на ${days(-lag)}` }
  if (lag <= 1) return { tone: 'ok', text: 'идёт по графику', short: 'по графику' }
  return { tone: lag >= 5 ? 'danger' : 'warn', text: `отстаёт примерно на ${days(lag)}`, short: `отстаёт на ${days(lag)}` }
}
