import type { ResourceAssessment as Assessment, ResourceEvidence } from '@/data'

const STATUS: Record<Assessment['status'], string> = {
  compared: 'Сопоставлено с планом',
  demonstration: 'Демонстрационный результат',
  insufficient_evidence: 'Недостаточно подтверждений для сопоставления',
  not_evaluated: 'Ресурсный расчёт не выполнялся',
}

const MODE: Record<'demonstration' | 'operational', string> = {
  demonstration: 'Демонстрационный режим',
  operational: 'Операционный режим',
}

const BASIS: Record<Assessment['basis'], string> = {
  cv_detections: 'распознавание техники на кадре',
  manual_visual_estimate: 'визуальная оценка источника',
  unavailable: 'наблюдение недоступно',
}

const COVERAGE: Record<Assessment['coverage'], string> = {
  full_scope: 'полный охват',
  partial_scope: 'частичный охват',
  unknown: 'охват неизвестен',
}

const SELECTION: Record<NonNullable<Assessment['selectionBasis']>, string> = {
  planned_at_frame_time: 'этап по плану на момент кадра',
  confirmed_current: 'подтверждённый текущий этап',
  explicit: 'явно выбранный этап',
}

const REASON: Record<Assessment['reasonCodes'][number], string> = {
  no_resource_plan: 'ресурсный план не передан',
  no_resource_target: 'не выбран ресурсный этап',
  scope_unknown: 'область сравнения неизвестна',
  resource_scope_unknown: 'область ресурсного плана неизвестна',
  plan_class_unmapped: 'класс техники плана не сопоставлен',
  duplicate_planned_class: 'класс техники повторён в плане',
  planned_quantity_unknown: 'плановое количество неизвестно',
  observation_unavailable: 'наблюдение по кадру недоступно',
  partial_coverage: 'кадр покрывает объект частично',
  unknown_coverage: 'охват кадра не подтверждён',
  duplicate_observed_class: 'класс техники повторён в наблюдении',
  observation_class_missing: 'в наблюдении не указан класс техники',
  observation_class_unmapped: 'класс техники кадра не сопоставлен',
  non_production_source: 'источник не является производственным',
  timestamp_unverified: 'время наблюдения не подтверждено',
  observation_requires_validation: 'наблюдение требует проверки',
  demonstration_mode: 'демонстрационный режим',
  no_independent_measurements: 'нет независимых фактических измерений',
  no_planned_equipment: 'для этапа не задана техника',
  vlm_resources_not_evaluated: 'VLM использовал контекст без числового расчёта',
}

const EQUIPMENT_STATUS: Record<Assessment['equipmentItems'][number]['status'], string> = {
  visible_below_plan: 'ниже плана',
  visible_equal_plan: 'соответствует плану',
  visible_above_plan: 'выше плана',
  unknown: 'сравнение неизвестно',
}



/** Компактная проекция v2-оценки, привязанная к одному уже выбранному ответу сервиса. */
export function ResourceAssessment({ assessment, analysisMode, limitations, evidence }: {
  assessment: Assessment
  analysisMode: 'demonstration' | 'operational' | null
  limitations: string[]
  evidence: ResourceEvidence[]
}) {
  const comparisonAllowed = assessment.coverage === 'full_scope' && (
    (assessment.status === 'compared' && analysisMode === 'operational') ||
    (assessment.status === 'demonstration' && analysisMode === 'demonstration')
  )
  const deltaLabel = assessment.status === 'demonstration' && analysisMode === 'demonstration' ? 'Разница (демо)' : 'Разница'
  const allLimitations = [...new Set([...assessment.limitations, ...limitations])]

  return (
    <section className="mt-3 rounded-md border border-border bg-muted/40 px-3 py-2.5 text-[14px]" aria-label="Ресурсное сопоставление">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <strong>{STATUS[assessment.status]}</strong>
        {analysisMode && <span className={analysisMode === 'demonstration' ? 'text-warn-fg' : 'text-muted-foreground'}>{MODE[analysisMode]}</span>}
      </div>
      {assessment.status === 'not_evaluated' ? (
        <p className="mt-1 text-muted-foreground">VLM получил контекст кадра, но не выполняет числовое ресурсное сопоставление.</p>
      ) : (
        <p className="mt-1 text-muted-foreground">
          Основание: {BASIS[assessment.basis]}; {COVERAGE[assessment.coverage]}
          {assessment.stageCode && <>; этап: {assessment.stageCode}</>}
          {assessment.selectionBasis && <>; {SELECTION[assessment.selectionBasis]}</>}.
        </p>
      )}

      {assessment.status === 'demonstration' && <p className="mt-2 rounded bg-warn-bg px-2 py-1.5 text-warn-fg">Демонстрационные данные не подтверждают производственное сравнение.</p>}
      {assessment.reasonCodes.length > 0 && <p className="mt-2 text-muted-foreground">Причины: {assessment.reasonCodes.map((code) => REASON[code]).join('; ')}.</p>}

      {assessment.equipmentItems.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          {!comparisonAllowed && <p className="mb-2 text-muted-foreground">План и наблюдение показаны отдельно; числовое сопоставление для этого кадра не подтверждено.</p>}
          <table className="w-full text-left">
            <thead className="text-[13px] text-muted-foreground"><tr><th className="pr-3 font-medium">Техника</th><th className="pr-3 font-medium">План</th><th className="pr-3 font-medium">На кадре</th><th className="font-medium">{deltaLabel}</th></tr></thead>
            <tbody>
              {assessment.equipmentItems.map((item) => {
                const delta = item.visibleCountDelta
                const canShowDelta = comparisonAllowed && item.status !== 'unknown' && delta !== null
                return (
                  <tr key={item.itemId} className="border-t border-border">
                    <td className="py-1.5 pr-3">{item.classCode ?? item.itemId} <span className="text-muted-foreground">· {EQUIPMENT_STATUS[item.status]}</span></td>
                    <td className="py-1.5 pr-3">{item.plannedQuantity ?? '—'}</td>
                    <td className="py-1.5 pr-3">{item.visibleCount ?? '—'}</td>
                    <td className="py-1.5">{canShowDelta ? <>{delta > 0 ? '+' : ''}{delta}</> : '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {allLimitations.length > 0 && <div className="mt-2 rounded bg-warn-bg px-2 py-1.5 text-warn-fg"><strong>Ограничения:</strong><ul className="mt-1 list-disc pl-4">{allLimitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul></div>}
      {evidence.length > 0 && <div className="mt-2"><strong>Доказательства кадра:</strong><ul className="mt-1 list-disc pl-4 text-muted-foreground">{evidence.map((item) => <li key={item.pointer}>{item.description}{!item.available && <span className="text-warn-fg"> · исходные данные недоступны</span>}</li>)}</ul></div>}
    </section>
  )
}
