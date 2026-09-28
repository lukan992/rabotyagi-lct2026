import { PhotoAnalyses } from '@/components/PhotoAnalyses'

/** Маршрут сохранён для старых ссылок, но использует новый site-scoped анализ фото, а не Rule API. */
export function CheckSnapshot() {
  return <PhotoAnalyses />
}
