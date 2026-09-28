import { describe, expect, it } from 'vitest'
import { lagDays, pace } from './schedule'
import { day, stage, TODAY } from '@/test/fixtures'

/** Работа из n дней, начатая `from` дней от сегодня, сделанная на fact % */
const work = (from: number, n: number, fact: number) => stage({ start: day(from), end: day(from + n - 1), factProgress: fact })

describe('lagDays — отставание в днях', () => {
  it('работа 10 дней, идёт 5-й день', () => {
    expect(lagDays([work(-4, 10, 80)], TODAY)).toBe(-3)
    expect(lagDays([work(-4, 10, 20)], TODAY)).toBe(3)
    expect(lagDays([work(-4, 10, 50)], TODAY)).toBe(0)
  })
  it('срок вышел 11 дней назад, сделано 50%', () => {
    expect(lagDays([work(-20, 10, 50)], TODAY)).toBe(16)
  })
  it('первая работа сделана, вторая идёт 6-й день с нулём', () => {
    expect(lagDays([work(-15, 10, 100), work(-5, 10, 0)], TODAY)).toBe(6)
  })
  it('ещё не началась и ничего не сделано — отставания нет', () => {
    expect(lagDays([work(3, 10, 0)], TODAY)).toBe(0)
  })
  it('начнётся через 3 дня, но уже 30% — опережение', () => {
    expect(lagDays([work(3, 10, 30)], TODAY)).toBe(-5)
  })
  it('всё сделано, конец через 4 дня', () => {
    expect(lagDays([work(-5, 10, 100)], TODAY)).toBe(-4)
  })
  it('плана нет', () => {
    expect(lagDays([], TODAY)).toBeNull()
  })
})

describe('pace — словами и цветом', () => {
  it('в пределах дня — по графику', () => {
    expect(pace(1)).toMatchObject({ tone: 'ok', short: 'по графику' })
    expect(pace(-1).tone).toBe('ok')
  })
  it('отставание до рабочей недели — жёлтым, от недели — красным', () => {
    expect(pace(2)).toMatchObject({ tone: 'warn', short: 'отстаёт на 2\u00a0дня' })  // число и «дня» не разрываются переносом
    expect(pace(5).tone).toBe('danger')
  })
  it('опережение', () => {
    expect(pace(-3).text).toBe('опережает график примерно на 3\u00a0дня')
  })
})
