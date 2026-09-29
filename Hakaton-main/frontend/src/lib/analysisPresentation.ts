import type { ServiceAnswer, SpiderStageLinks, WorkGroup } from '@/data'

/** Только подпись: ручная связь не делает гипотезу модели подтверждённым этапом. */
export function manualStageCodes(answer: ServiceAnswer, group: WorkGroup, links: SpiderStageLinks | null): string[] {
  if (!links?.snapshotId || answer.spiderSnapshotId !== links.snapshotId) return []
  const keys = new Set(group.works.map((work) => work.stepKey))
  return links.stages.filter((stage) => stage.stepKey && keys.has(stage.stepKey)).map((stage) => stage.stageCode)
}

export function workGroupPrefix(answer: ServiceAnswer, group: WorkGroup): string {
  if (group.match === 'ambiguous') return 'Возможные работы: '
  return answer.service === 'vlm_llm' ? 'Предположение по фото: ' : 'Оценка по технике и плану: '
}

export function analysisErrorMessage(answer: ServiceAnswer): string {
  if (answer.state === 'unknown' || answer.errorCode === 'execution_uncertain') {
    return 'Неизвестно, завершился ли анализ. Дождитесь проверки статуса сервиса.'
  }
  if (answer.errorCode === 'model_invalid_response' || answer.errorCode === 'model_failure') {
    return 'Анализ фото не удался. Попробуйте другой кадр.'
  }
  if (answer.errorCode === 'analysis_timeout' || answer.errorCode === 'busy') {
    return 'Анализ фото пока недоступен. Повторите попытку позже.'
  }
  if (answer.errorCode === 'catalog_version_mismatch') {
    return 'Справочник сервиса обновился. Руководителю нужно проверить виды работ в плане.'
  }
  if (answer.errorCode === 'dependency_unavailable' || answer.errorCode === 'not_ready') {
    return 'Сервис анализа пока недоступен. Повторите попытку позже.'
  }
  if (answer.errorCode === 'unauthorized' || answer.errorCode === 'forbidden') {
    return 'Сервис анализа не принял доступ. Руководителю нужно проверить настройки.'
  }
  if (answer.errorCode === 'unreachable') return 'Нет связи с сервисом анализа. Повторите попытку позже.'
  if (answer.errorCode === 'invalid_result') return 'Сервис вернул результат не для этого кадра. Нужна проверка.'
  return 'Не удалось получить результат анализа. Попробуйте ещё раз.'
}
