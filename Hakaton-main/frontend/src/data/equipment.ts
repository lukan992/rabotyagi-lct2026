import type { EquipmentInfo, EquipmentType } from './types'

/** Восемь типов техники из ТЗ (п. 6.2) */
export const EQUIPMENT: Record<EquipmentType, EquipmentInfo> = {
  excavator:   { type: 'excavator',   name: 'Экскаватор',        namePlural: 'экскаваторы',        genitivePlural: 'экскаваторов',        color: '#F59E0B' },
  dump_truck:  { type: 'dump_truck',  name: 'Самосвал',          namePlural: 'самосвалы',          genitivePlural: 'самосвалов',          color: '#3B82F6' },
  roller:      { type: 'roller',      name: 'Каток',             namePlural: 'катки',              genitivePlural: 'катков',              color: '#8B5CF6' },
  manipulator: { type: 'manipulator', name: 'Кран-манипулятор',  namePlural: 'краны-манипуляторы', genitivePlural: 'кранов-манипуляторов', color: '#14B8A6' },
  mixer:       { type: 'mixer',       name: 'Бетоносмеситель',   namePlural: 'бетоносмесители',    genitivePlural: 'бетоносмесителей',    color: '#EC4899' },
  bulldozer:   { type: 'bulldozer',   name: 'Бульдозер',         namePlural: 'бульдозеры',         genitivePlural: 'бульдозеров',         color: '#84CC16' },
  truck:       { type: 'truck',       name: 'Грузовик',          namePlural: 'грузовики',          genitivePlural: 'грузовиков',          color: '#64748B' },
  crane:       { type: 'crane',       name: 'Автокран',          namePlural: 'автокраны',          genitivePlural: 'автокранов',          color: '#EF4444' },
}

export const EQUIPMENT_LIST = Object.values(EQUIPMENT)
