import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Image, Loader2, RefreshCw, Upload } from 'lucide-react'
import { api, ApiError } from '@/api'
import type { SpiderObservationAsset, SpiderSnapshot, SpiderStageResource } from '@/data'
import { fmtDate, fmtWhen } from '@/lib/utils'
import { Badge } from './ui/Badge'
import { Button } from './ui/Button'
import { Disclosure } from './ui/Disclosure'
import { Modal } from './ui/Modal'
import { SpiderConnection } from './SpiderConnection'

/**
 * Не смешивает сохранённые данные Camera Stage Monitor с локальным календарным планом и CV:
 * а подготовка фото всегда начинается только по явному действию пользователя.
 */
export function SpiderResources({ siteId, siteName, canImport }: { siteId: string; siteName?: string; canImport: boolean }) {
  const queryClient = useQueryClient()
  const key = ['spider', siteId]
  const [confirmImport, setConfirmImport] = useState(false)
  const [prepared, setPrepared] = useState<{ siteId: string; importId: string | null; asset: SpiderObservationAsset } | null>(null)
  const { data, isPending, isError, error } = useQuery({
    queryKey: key,
    queryFn: () => api.spider(siteId),
    refetchInterval: 60_000,
  })
  const snapshotId = data?.snapshot?.id
  const importId = data?.lastImport?.id ?? null
  const asset = prepared?.siteId === siteId && prepared.importId === importId && prepared.asset.snapshotId === snapshotId ? prepared.asset : null
  const importSource = useMutation({
    mutationFn: () => api.importSpider(siteId),
    onSuccess: () => {
      setConfirmImport(false)
      setPrepared(null)
      void queryClient.invalidateQueries({ queryKey: key })
    },
  })
  const prepare = useMutation({
    mutationFn: ({ observationId, snapshotId }: { observationId: string; snapshotId: string }) =>
      api.prepareSpiderObservation(siteId, observationId, snapshotId),
    onSuccess: (result) => setPrepared({ siteId, importId, asset: result }),
  })

  if (isPending) return <div className="bg-card rounded-xl border border-border h-44 animate-pulse" aria-busy />
  if (isError || !data) return (
    <section className="bg-card rounded-xl border border-border shadow-[var(--shadow-card)] px-4 sm:px-5 py-4">
      <h2 className="text-[18px] font-semibold">Источник Camera Stage Monitor</h2>
      <p role="alert" className="mt-3 rounded-lg bg-danger-bg text-danger-fg px-3 py-2 text-[14px]">
        {error instanceof ApiError ? error.message : 'Не удалось загрузить данные источника.'}
      </p>
    </section>
  )

  const snapshot = data.snapshot
  return (
    <section className="bg-card rounded-xl border border-border shadow-[var(--shadow-card)] px-4 sm:px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-[18px] font-semibold">Источник Camera Stage Monitor</h2>
          <p className="mt-0.5 text-[14px] text-muted-foreground">Данные внешнего источника только для просмотра: локальный план объекта не изменяется.</p>
        </div>
        {canImport && <Button size="sm" onClick={() => setConfirmImport(true)} disabled={importSource.isPending}>
          {importSource.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
          Загрузить источник — 5 запросов
        </Button>}
      </div>

      {importSource.isError && <p role="alert" className="mt-3 rounded-lg bg-danger-bg text-danger-fg px-3 py-2 text-[14px]">
        {importSource.error instanceof ApiError ? importSource.error.message : 'Не удалось загрузить источник.'}
      </p>}
      <p className="mt-3 text-[14px] text-muted-foreground">План и происхождение данных показаны здесь; ресурсное сопоставление отображается в результате анализа конкретного кадра.</p>
      {data.stale && data.lastImport?.errorMessage && (
        <p role="alert" className="mt-3 rounded-lg bg-warn-bg text-warn-fg px-3 py-2 text-[14px]">Последнее обновление источника не удалось: {data.lastImport.errorMessage}</p>
      )}
      {!snapshot ? <EmptySource limitations={data.limitations} /> : <SourceSnapshot snapshot={snapshot} stale={data.stale} lastSuccessAt={data.lastSuccessAt} asset={asset} prepareError={prepare.isError ? prepare.error instanceof ApiError ? prepare.error.message : 'Не удалось подготовить фото источника.' : null} preparing={prepare.isPending} onPrepare={(observationId) => prepare.mutate({ observationId, snapshotId: snapshot.id })} />}
      {canImport && <SpiderConnection siteId={siteId} />}

      <Modal open={confirmImport} onClose={() => !importSource.isPending && setConfirmImport(false)} title="Загрузить внешний источник">
        <div className="space-y-4">
          <p>Будет выполнено ровно 5 запросов к Camera Stage Monitor для объекта{siteName ? ` «${siteName}»` : ''}.</p>
          <p className="rounded-lg bg-warn-bg text-warn-fg px-3 py-2 text-[14px]">Данные источника загружаются только для просмотра. План объекта и фактический прогресс не будут заменены.</p>
          {importSource.isError && <p role="alert" className="rounded-lg bg-danger-bg text-danger-fg px-3 py-2 text-[14px]">{importSource.error instanceof ApiError ? importSource.error.message : 'Не удалось загрузить источник.'}</p>}
          <div className="flex flex-wrap gap-3">
            <Button onClick={() => importSource.mutate()} disabled={importSource.isPending}>
              {importSource.isPending && <Loader2 className="w-5 h-5 animate-spin" />} Подтвердить загрузку — 5 запросов
            </Button>
            <Button variant="outline" onClick={() => setConfirmImport(false)} disabled={importSource.isPending}>Отмена</Button>
          </div>
        </div>
      </Modal>
    </section>
  )
}

function EmptySource({ limitations }: { limitations: string[] }) {
  return (
    <div className="mt-4 rounded-lg bg-muted px-3 py-3 text-[15px] text-muted-foreground">
      Источник ещё не загружен.{limitations.includes('source_not_configured') && ' Источник не настроен на сервере.'}
    </div>
  )
}

function SourceSnapshot({ snapshot, stale, lastSuccessAt, asset, prepareError, preparing, onPrepare }: {
  snapshot: SpiderSnapshot
  stale: boolean
  lastSuccessAt: string | null
  asset: SpiderObservationAsset | null
  prepareError: string | null
  preparing: boolean
  onPrepare: (observationId: string) => void
}) {
  return (
    <div className="mt-4 space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-[14px] text-muted-foreground">
        <Badge tone="warn">{snapshot.dataType || 'unknown'}</Badge>
        {stale && <Badge tone="warn">Данные устарели</Badge>}
        <span>Получено: {lastSuccessAt ? fmtWhen(lastSuccessAt) : fmtDate(snapshot.createdAt)}</span>
      </div>
      {snapshot.warning && <p className="rounded-lg bg-warn-bg text-warn-fg px-3 py-2 text-[14px]">{snapshot.warning}</p>}
      <p className="rounded-lg bg-warn-bg px-3 py-2 text-[14px] text-warn-fg">Spider не передаёт ID объекта. Принадлежность этого плана объекту не подтверждена источником.</p>
      <div className="space-y-3">
        {snapshot.resources.stages.map((stage) => <ResourceStage key={stage.code} stage={stage} />)}
      </div>
      {prepareError && <p role="alert" className="rounded-lg bg-danger-bg text-danger-fg px-3 py-2 text-[14px]">{prepareError}</p>}
      {snapshot.resources.limitations.length > 0 && <p className="text-[14px] text-muted-foreground">Ограничения источника: {snapshot.resources.limitations.join(', ')}</p>}
      <Disclosure title="Данные источника">
        <div className="space-y-4 text-[14px]">
          <p className="text-muted-foreground">Наблюдения, ручные оценки и сравнения остаются исходными данными; они не суммируются с CV и не являются фактическим прогрессом.</p>
          {snapshot.sourceObservations.length === 0 ? <p className="text-muted-foreground">Наблюдений нет.</p> : snapshot.sourceObservations.map((observation, index) => {
            const observationId = text(observation, 'observation')
            return <SourceObservation key={observationId || index} observation={observation} asset={asset?.snapshotId === snapshot.id && asset.observationId === observationId ? asset : null} preparing={preparing} onPrepare={() => observationId && onPrepare(observationId)} />
          })}
          <ManualAnnotations
            rows={snapshot.manualAnnotations}
            annotationType={snapshot.manualAnnotationType}
            requiresValidation={snapshot.manualRequiresValidation}
          />
          <SourceRows title="Исходные сравнения" rows={snapshot.sourceComparisons} />
        </div>
      </Disclosure>
    </div>
  )
}

function ResourceStage({ stage }: { stage: SpiderStageResource }) {
  const volume = stage.plannedVolume.value == null ? 'Нет данных' : `${stage.plannedVolume.value}${stage.plannedVolume.unit ? ` ${stage.plannedVolume.unit}` : ''}`
  const productivity = stage.plannedProductivity.value == null ? 'Нет данных' : `${stage.plannedProductivity.value}${stage.plannedProductivity.unit ? ` ${stage.plannedProductivity.unit}` : ''}`
  return (
    <article className="rounded-lg border border-border px-3 py-3">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1"><strong>{stage.code}</strong><span className="font-semibold">{stage.name}</span></div>
      <p className="mt-1 text-[14px] text-muted-foreground">{fmtDate(stage.start)} — {fmtDate(stage.finish)} · смен: {stage.plannedWorkShifts == null ? 'Нет данных' : stage.plannedWorkShifts}</p>
      <dl className="mt-2 grid gap-x-4 gap-y-1 text-[14px] sm:grid-cols-2">
        <div><dt className="text-muted-foreground">Плановый объём</dt><dd>{volume}</dd></div>
        <div><dt className="text-muted-foreground">Производительность этапа</dt><dd>{productivity}</dd></div>
      </dl>
      <div className="mt-3 border-t border-border pt-2 text-[14px]">
        <p className="font-medium">Требуемая техника</p>
        {stage.equipment.length === 0 ? <p className="text-muted-foreground">Нет данных</p> : <ul className="mt-1 space-y-1">{stage.equipment.map((item, index) => <li key={`${item.sourceName}-${index}`}>{item.sourceName} ×{item.plannedQuantity == null ? 'Нет данных' : item.plannedQuantity} <span className="text-muted-foreground">· На единицу техники — нет данных</span></li>)}</ul>}
      </div>
    </article>
  )
}

function SourceObservation({ observation, asset, preparing, onPrepare }: {
  observation: Record<string, unknown>
  asset: SpiderObservationAsset | null
  preparing: boolean
  onPrepare: () => void
}) {
  const id = text(observation, 'observation') || 'Наблюдение'
  const at = text(observation, 'timestamp_iso')
  const observedStage = text(observation, 'observed_stage')
  return (
    <article className="rounded-lg border border-border px-3 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2"><strong>{id}</strong>{at && <span className="text-muted-foreground">{fmtWhen(at)}</span>}</div>
      {observedStage && <p className="mt-1 text-muted-foreground">Этап по источнику: {observedStage}</p>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={onPrepare} disabled={!text(observation, 'observation') || preparing}>
          {preparing ? <Loader2 className="w-4 h-4 animate-spin" /> : asset?.targetError ? <RefreshCw className="w-4 h-4" /> : <Image className="w-4 h-4" />}
          {asset?.targetError ? 'Повторить выбор этапа — 1 запрос' : 'Подготовить фото'}
        </Button>
        {asset?.target && <Badge tone="ok">Этап по времени: {[text(asset.target, 'code'), text(asset.target, 'name')].filter(Boolean).join(' · ') || 'получен'}</Badge>}
      </div>
      {asset?.targetError && <p className="mt-2 rounded-lg bg-warn-bg text-warn-fg px-3 py-2">Не удалось определить этап: {asset.targetError}</p>}
      {asset && <p className="mt-2 text-[13px] text-muted-foreground">Фото получено отдельно {fmtWhen(asset.fetchedAt)}. Spider не подтверждает неизменность изображения с момента загрузки плана.</p>}
      {asset && <ProtectedSpiderImage asset={asset} />}
    </article>
  )
}

function ProtectedSpiderImage({ asset }: { asset: SpiderObservationAsset }) {
  const [url, setUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    let objectUrl: string | null = null
    setUrl(null)
    setError(null)
    void api.spiderImage(asset.imageUrl).then((blob) => {
      objectUrl = URL.createObjectURL(blob)
      if (active) setUrl(objectUrl)
      else URL.revokeObjectURL(objectUrl)
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof ApiError ? reason.message : 'Не удалось загрузить защищённое изображение.')
    })
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [asset.id, asset.imageUrl])
  if (error) return <p role="alert" className="mt-3 text-danger-fg">{error}</p>
  if (!url) return <p className="mt-3 text-muted-foreground">Загружаем защищённое изображение…</p>
  return <img className="mt-3 max-h-80 w-auto rounded-lg border border-border" src={url} alt={`Источник: ${asset.observationId}`} />
}

function ManualAnnotations({ rows, annotationType, requiresValidation }: {
  rows: Record<string, unknown>[]
  annotationType: string | null
  requiresValidation: boolean | null
}) {
  if (rows.length === 0) return null
  const validation = requiresValidation === true
    ? 'требует проверки'
    : requiresValidation === false
      ? 'проверка не требуется'
      : 'требование проверки не указано'
  return (
    <div>
      <h4 className="font-semibold">Ручные оценки техники</h4>
      <p className="mt-1 text-muted-foreground">
        Тип ручной оценки: {annotationType || 'не указан'} · {validation}
      </p>
      <div className="mt-1 space-y-2">
        {rows.map((annotation, annotationIndex) => {
          const equipment = Array.isArray(annotation.equipment)
            ? annotation.equipment.filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null && !Array.isArray(item))
            : []
          return (
            <article key={`${text(annotation, 'observation') || 'annotation'}-${annotationIndex}`} className="rounded-lg border border-border px-3 py-2">
              <p className="text-muted-foreground">{text(annotation, 'observation') || 'Наблюдение'}</p>
              {equipment.length === 0 ? <p className="mt-1 text-muted-foreground">Ручные количества не указаны.</p> : (
                <ul className="mt-1 space-y-1">
                  {equipment.map((item, equipmentIndex) => (
                    <li key={`${text(item, 'class_code') || text(item, 'class_name_ru') || 'equipment'}-${equipmentIndex}`}>
                      {text(item, 'class_name_ru') || text(item, 'class_code') || 'Техника'} ×{text(item, 'count') || 'Нет данных'}
                      {text(item, 'confidence') && <span className="text-muted-foreground"> · достоверность источника: {text(item, 'confidence')}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </article>
          )
        })}
      </div>
    </div>
  )
}

function SourceRows({ title, rows }: { title: string; rows: Record<string, unknown>[] }) {
  if (rows.length === 0) return null
  return <div><h4 className="font-semibold">{title}</h4><ul className="mt-1 space-y-1 text-muted-foreground">{rows.map((row, index) => <li key={index}>{sourceSummary(row)}</li>)}</ul></div>
}

function sourceSummary(row: Record<string, unknown>) {
  const keys = ['observation', 'class_name_ru', 'count', 'planned_stage', 'observed_stage', 'status']
  const parts = keys.map((key) => text(row, key)).filter(Boolean)
  return parts.length ? parts.join(' · ') : 'Исходная запись'
}
function text(value: Record<string, unknown>, key: string) {
  const item = value[key]
  return typeof item === 'string' || typeof item === 'number' ? String(item) : ''
}
