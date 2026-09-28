import { describe, expect, it } from 'vitest'
import { adviceFor, confidenceNote } from './alertAdvice'
import { alert, snapshot } from '@/test/fixtures'

describe('adviceFor — «что делать» со своими кнопками у каждой роли', () => {
  it('прорабу — «Техника едет»', () => {
    expect(adviceFor(alert(), 'foreman', false)).toMatch(/нажмите «Техника едет»/)
  })
  it('инспектору — предписание, а не чужая кнопка', () => {
    const text = adviceFor(alert(), 'inspector', false)
    expect(text).toMatch(/выдайте предписание/)
    expect(text).not.toMatch(/Техника едет/)
  })
  it('старый совет с названием чужой кнопки обрезается', () => {
    const old = alert({ advice: 'Уточните у подрядчика, где самосвалы. Если техника уже едет — нажмите «Техника едет».' })
    expect(adviceFor(old, 'inspector', false)).toBe('Уточните у подрядчика, где самосвалы. Если нарушение подтверждается — выдайте предписание; если система ошиблась — нажмите «Ошибка системы».')
  })
  it('у отклонения с предписанием прорабу кнопки не подсказываем', () => {
    expect(adviceFor(alert(), 'foreman', true)).toBe(alert().advice)
  })
})

describe('confidenceNote — уверенность по той технике, о которой отклонение', () => {
  const excavator = { id: 'd1', type: 'excavator' as const, confidence: 0.96, box: { x: 0, y: 0, w: 10, h: 10 } }
  const truck = { id: 'd2', type: 'dump_truck' as const, confidence: 0.8, box: { x: 0, y: 0, w: 10, h: 10 } }
  it('«техники нет» — не выдаём уверенность в соседнем экскаваторе', () => {
    const note = confidenceNote(alert({ kind: 'missing' }), [snapshot([excavator]), snapshot([excavator])])
    expect(note).toBe('Нужной техники нет ни на одном из 2 проверенных кадров.')
  })
  it('«мало техники» — средняя уверенность по самосвалам', () => {
    const note = confidenceNote(alert({ kind: 'count_below' }), [snapshot([excavator, truck])])
    expect(note).toBe('Уверенность распознавания самосвалов на 1 кадре: 80%.')
  })
})
