import { ROLES, type Alert, type AlertStatus, type RoleId } from '@/data'

/** Открытые (нерешённые) статусы */
export function isOpen(status: AlertStatus) {
  return status === 'new' || status === 'acknowledged' || status === 'confirmed' || status === 'prescribed'
}

export function roleTitle(id: RoleId) {
  return ROLES.find((r) => r.id === id)?.title ?? id
}

const order = { high: 0, medium: 1, low: 2 }
/** Сортировка: сначала срочные, внутри — свежие */
export function bySeverity(a: Alert, b: Alert) {
  return order[a.severity] - order[b.severity] || b.startedAt.localeCompare(a.startedAt)
}

/** Инициалы для аватара */
export function initials(name: string) {
  return name.split(' ').slice(0, 2).map((w) => w[0]).join('').toUpperCase()
}
