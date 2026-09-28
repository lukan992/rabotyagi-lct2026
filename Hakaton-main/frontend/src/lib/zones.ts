import type { ZoneKind } from '@/data'

/** Виды зон объекта: от вида зависит, как считается техника на её камерах */
export const ZONE_KINDS: { id: ZoneKind; label: string; hint: string }[] = [
  { id: 'work', label: 'Рабочая зона', hint: 'техника здесь считается работающей' },
  { id: 'gate', label: 'Въезд', hint: 'техника считается подъезжающей' },
  { id: 'storage', label: 'Склад', hint: '' },
]
