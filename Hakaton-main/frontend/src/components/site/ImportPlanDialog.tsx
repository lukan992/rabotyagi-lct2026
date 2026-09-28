import { useRef, useState } from 'react'
import { AlertTriangle, Download, FileSpreadsheet, Loader2, Upload } from 'lucide-react'
import { api, ApiError } from '@/api'
import type { PlanImport, PlanImportWork, Site } from '@/data'
import { useApp } from '@/store/context'
import { cn, fmtDateShort, plural } from '@/lib/utils'
import { Button } from '../ui/Button'
import { Modal } from '../ui/Modal'

/**
 * План работ из Excel или CSV: скачать шаблон → выбрать файл → посмотреть, что получится (ошибки — по строкам файла)
 * → загрузить. Пока в файле есть ошибки, план не меняется.
 */
export function ImportPlanDialog({ site, onClose }: { site: Site | null; onClose: () => void }) {
  return (
    <Modal open={!!site} onClose={onClose} title={site ? `План из Excel: ${site.name}` : ''} wide>
      {site && <ImportPlan key={site.id} site={site} onClose={onClose} />}
    </Modal>
  )
}

function ImportPlan({ site, onClose }: { site: Site; onClose: () => void }) {
  const { run } = useApp()
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<PlanImport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<'template' | 'preview' | 'apply' | null>(null)
  const [replace, setReplace] = useState(false)
  const request = useRef(0)  // ответ на прежний файл не показываем под новым

  const pick = async (next: File | undefined) => {
    if (!next) return
    const id = ++request.current
    setFile(next)
    setPreview(null)
    setError(null)
    setBusy('preview')
    try {
      const result = await api.importPlan(site.id, next)
      if (id === request.current) setPreview(result)
    } catch (e) {
      if (id === request.current) setError(e instanceof ApiError ? e.message : 'Не удалось прочитать файл')
    } finally {
      if (id === request.current) setBusy(null)
      if (input.current) input.current.value = ''  // тот же файл после правки в Excel можно выбрать снова
    }
  }

  const template = async () => {
    setBusy('template')
    setError(null)
    try {
      const blob = await api.planTemplate(site.id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `План работ — ${site.name}.xlsx`
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Не удалось скачать шаблон')
    } finally {
      setBusy(null)
    }
  }

  const apply = async () => {
    if (!file || !preview) return
    setBusy('apply')
    const ok = await run(
      () => api.importPlan(site.id, file, { apply: true, replace }),
      `План загружен: ${plural(preview.phases.length, 'этап', 'этапа', 'этапов')}, ${plural(preview.works, 'работа', 'работы', 'работ')}`,
    )
    setBusy(null)
    if (ok) onClose()
  }

  return (
    <div className="space-y-5">
      <div className="space-y-3">
        <p>
          Одна строка — одна работа: этап, работа, даты начала и окончания. Правило «этап → техника» выбирается из списка,
          а если его не указать — подойдёт правило с таким же названием, как у работы.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="outline" onClick={template} disabled={busy === 'template'}>
            {busy === 'template' ? <Loader2 className="w-5 h-5 animate-spin" /> : <Download className="w-5 h-5" />} Скачать шаблон
          </Button>
          <span className="text-muted-foreground text-[14px]">Excel, со списками правил и видов работ для этого объекта</span>
        </div>
      </div>

      {/* поле выбора файла — внутри подписи: по Tab фокус на нём, а рамка фокуса видна у всей подписи */}
      <label className="flex items-center gap-3 min-h-[64px] rounded-xl border border-dashed border-border-strong px-4 py-3 cursor-pointer transition-colors hover:border-primary hover:bg-muted/40 has-[:focus-visible]:ring-4 has-[:focus-visible]:ring-primary/15 has-[:focus-visible]:border-primary">
        <input
          ref={input} type="file" accept=".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv"
          className="sr-only" onChange={(e) => void pick(e.target.files?.[0])}
        />
        {busy === 'preview' ? <Loader2 className="w-6 h-6 animate-spin text-muted-foreground shrink-0" /> : <FileSpreadsheet className="w-6 h-6 text-muted-foreground shrink-0" />}
        <span className="min-w-0">
          <span className="block font-semibold truncate">{file ? file.name : 'Выберите заполненный файл'}</span>
          <span className="block text-muted-foreground text-[14px]">{file ? 'Нажмите, чтобы выбрать другой' : '.xlsx или .csv — план проверим и покажем, что получится'}</span>
        </span>
      </label>

      <div aria-live="polite">
        {error && <p role="alert" className="rounded-xl bg-danger-bg text-danger-fg px-4 py-3 font-medium">{error}</p>}
        {preview && <Preview preview={preview} />}
      </div>

      {preview && preview.errors === 0 && preview.existing > 0 && (
        <fieldset>
          <legend className="font-semibold mb-2">В плане объекта уже есть этапы и работы ({preview.existing})</legend>
          <div className="grid sm:grid-cols-2 gap-2">
            {([
              [false, 'Добавить к плану', 'Новые этапы встанут после прежних'],
              [true, 'Заменить план', 'Прежние этапы и работы удалятся; отклонения останутся в истории'],
            ] as const).map(([value, title, text]) => (
              <label key={title} className={cn('flex items-start gap-3 rounded-lg border px-3 py-2.5 cursor-pointer transition-colors', replace === value ? 'border-primary bg-info-bg' : 'border-border-strong hover:bg-muted')}>
                <input type="radio" name="plan-mode" checked={replace === value} onChange={() => setReplace(value)} className="mt-1 accent-[var(--color-primary)]" />
                <span><span className={cn('block font-semibold', replace === value && 'text-info-fg')}>{title}</span><span className="block text-[13px] text-muted-foreground">{text}</span></span>
              </label>
            ))}
          </div>
        </fieldset>
      )}

      <div className="flex flex-wrap gap-3 pt-1">
        <Button size="lg" disabled={!preview || preview.errors > 0 || busy !== null} onClick={apply}>
          {busy === 'apply' ? <Loader2 className="w-5 h-5 animate-spin" /> : <Upload className="w-5 h-5" />}
          {preview && preview.errors === 0 ? `Загрузить ${plural(preview.works, 'работу', 'работы', 'работ')}` : 'Загрузить план'}
        </Button>
        <Button size="lg" variant="outline" onClick={onClose}>Отмена</Button>
      </div>
    </div>
  )
}

/** Что получится: этапы с работами; ошибки — сверху сводкой и у своих строк */
function Preview({ preview }: { preview: PlanImport }) {
  const { rules } = useApp()
  const empty = preview.phases.length === 0
  return (
    <div className="space-y-3">
      {preview.errors > 0 ? (
        <div role="alert" className="rounded-xl bg-danger-bg text-danger-fg px-4 py-3">
          <p className="font-semibold flex items-center gap-2"><AlertTriangle className="w-5 h-5 shrink-0" /> {plural(preview.errors, 'ошибка', 'ошибки', 'ошибок')} — план пока не загрузится</p>
          <p className="text-[15px] mt-0.5">Исправьте отмеченные строки в файле, сохраните его и выберите снова.</p>
        </div>
      ) : (
        <p className="rounded-xl bg-ok-bg text-ok-fg px-4 py-3 font-medium">
          {empty ? 'В файле нет работ.' : `Ошибок нет: ${plural(preview.phases.length, 'этап', 'этапа', 'этапов')}, ${plural(preview.works, 'работа', 'работы', 'работ')}.`}
        </p>
      )}
      <ul className="space-y-3">
        {preview.phases.map((phase) => (
          <li key={`${phase.line}-${phase.name}`} className="rounded-xl border border-border overflow-hidden">
            <div className="bg-muted/50 px-4 py-2.5 flex flex-wrap items-baseline justify-between gap-x-3">
              <span className="font-semibold">{phase.name || 'Этап не указан'}</span>
              <span className="text-[14px] text-muted-foreground">{phase.start && phase.end ? `${fmtDateShort(phase.start)} — ${fmtDateShort(phase.end)}` : 'без дат'}</span>
              {phase.errors.map((e) => <p key={e} className="basis-full text-danger text-[14px]">{e}</p>)}
            </div>
            {phase.works.length > 0 && (
              <ul className="divide-y divide-border">
                {phase.works.map((work) => <WorkRow key={work.line} work={work} ruleName={work.ruleKey ? rules[work.ruleKey]?.stageName ?? work.ruleKey : null} />)}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

function WorkRow({ work, ruleName }: { work: PlanImportWork; ruleName: string | null }) {
  const bad = work.errors.length > 0
  return (
    <li className={cn('px-4 py-2.5', bad && 'bg-danger-bg/40')}>
      <div className="flex flex-wrap items-baseline gap-x-3">
        <span className="text-[13px] text-muted-foreground tabular-nums">строка {work.line}</span>
        <span className="font-semibold">{work.name}</span>
        <span className="text-[14px] text-muted-foreground">
          {work.start && work.end ? `${fmtDateShort(work.start)} — ${fmtDateShort(work.end)}` : ''}
          {ruleName && <> · правило «{ruleName}»</>}
          {work.catalogStageId !== null && <> · вид работ {work.catalogStageId}</>}
        </span>
      </div>
      {work.errors.map((e) => <p key={e} className="text-danger text-[14px]">{e}</p>)}
      {/* правило по названию уже видно в строке — повторяем только то, чего в ней нет */}
      {work.notes.filter((n) => !n.endsWith('по названию работы')).map((n) => <p key={n} className="text-muted-foreground text-[14px]">{n}</p>)}
    </li>
  )
}
