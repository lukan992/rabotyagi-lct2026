import { EQUIPMENT, type Alert, type RoleId } from '@/data'
import { plural } from './utils'

/**
 * Уверенность — только по той технике, о которой отклонение. Раньше бралась средняя по всему кадру:
 * «самосвалов нет, уверенность 96%» на деле означало, что система уверена в экскаваторе.
 */
export function confidenceNote(alert: Alert, snaps: Alert['evidenceSnapshots']) {
  const frames = plural(snaps.length, 'кадре', 'кадрах', 'кадрах')
  if (alert.kind === 'camera_offline') return ''
  if (alert.kind === 'missing') return snaps.length ? `Нужной техники нет ни на одном из ${snaps.length} проверенных кадров.` : ''
  const found = snaps.flatMap((s) => s.detections).filter((d) => d.type === alert.equipment)
  if (!found.length) return ''
  const conf = Math.round((found.reduce((n, d) => n + d.confidence, 0) / found.length) * 100)
  return `Уверенность распознавания ${EQUIPMENT[alert.equipment!].genitivePlural} на ${frames}: ${conf}%.`
}

/**
 * «Что делать»: общий совет сервера + подсказка по кнопкам именно этой роли.
 * Старые советы с названиями чужих кнопок («нажмите «Техника едет»» у инспектора) обрезаем.
 */
export function adviceFor(alert: Alert, role: RoleId | undefined, locked: boolean) {
  const base = alert.advice.split(/(?<=\.)\s+/).filter((s) => !/нажмите «/i.test(s)).join(' ').trim()
  const violation = alert.kind !== 'camera_offline'
  const hint = locked ? ''
    : role === 'foreman' ? (
      alert.status !== 'new' ? 'Когда исправите — нажмите «Устранено».'
        : alert.kind === 'missing' || alert.kind === 'count_below' ? 'Если техника уже едет — нажмите «Техника едет».'
          : alert.kind === 'unexpected' ? 'Если заезд согласован — нажмите «Это ошибка» и напишите в комментарии, кем согласован.'
            : 'Нажмите «Подтверждаю проблему», а когда исправите — «Устранено».')
      : role === 'manager' ? 'Когда проблема решена — нажмите «Устранено».'
        : role === 'inspector' && violation ? 'Если нарушение подтверждается — выдайте предписание; если система ошиблась — нажмите «Ошибка системы».'
          : ''
  return [base, hint].filter(Boolean).join(' ')
}
