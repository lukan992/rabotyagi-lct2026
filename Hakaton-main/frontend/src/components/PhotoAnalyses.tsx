import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Clock3, ImagePlus, Loader2, Upload } from 'lucide-react'
import { api, ApiError } from '@/api'
import { SITE_MANAGERS, type PhotoAnalysis, type ServiceAnswer } from '@/data'
import { useApp } from '@/store/context'
import { fmtWhen } from '@/lib/utils'
import { Badge } from './ui/Badge'
import { Button } from './ui/Button'
import { Card, CardBody } from './ui/Card'
import { PageHeader } from './ui/PageHeader'
import { ResourceAssessment } from './ResourceAssessment'

const SERVICE_LABEL: Record<ServiceAnswer['service'], string> = {
  deterministic: 'По плану и CV',
  vlm_llm: 'По снимку (VLM)',
}

/** Загрузка фото — самостоятельный источник, не связанный с камерой или её историей. */
export function PhotoAnalyses({ siteId: fixedSiteId }: { siteId?: string }) {
  const { sites, role } = useApp()
  const [siteId, setSiteId] = useState(fixedSiteId ?? sites[0]?.id ?? '')
  const activeSiteId = fixedSiteId ?? siteId
  const canUpload = !!role && SITE_MANAGERS.includes(role.id)
  const key = ['photo-analyses', activeSiteId]
  const queryClient = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [localPreview, setLocalPreview] = useState<string | null>(null)
  const [useSpider, setUseSpider] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [created, setCreated] = useState<PhotoAnalysis | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const photos = useQuery({
    queryKey: key,
    queryFn: () => api.photoAnalyses(activeSiteId),
    enabled: !!activeSiteId,
    refetchInterval: (query) => query.state.data?.some((photo) => photo.answers.length === 0 || photo.answers.some((answer) => answer.state === 'pending')) ? 3_000 : false,
  })
  const upload = useMutation({
    mutationFn: () => {
      if (!file) throw new Error('Выберите фотографию')
      return api.createPhotoAnalysis(activeSiteId, file, useSpider)
    },
    onSuccess: (photo) => {
      setCreated(photo)
      setSelectedId(photo.id)
      void queryClient.invalidateQueries({ queryKey: key })
    },
  })

  useEffect(() => () => { if (localPreview) URL.revokeObjectURL(localPreview) }, [localPreview])

  const chooseFile = (next?: File) => {
    if (!next || !next.type.startsWith('image/')) return
    if (localPreview) URL.revokeObjectURL(localPreview)
    setFile(next)
    setLocalPreview(URL.createObjectURL(next))
    setCreated(null)
    setSelectedId(null)
  }
  const submit = () => {
    if (!file || !activeSiteId || upload.isPending) return
    upload.mutate()
  }
  const selected = photos.data?.find((photo) => photo.id === selectedId) ?? (created?.id === selectedId ? created : photos.data?.[0] ?? null)
  const activeSite = sites.find((site) => site.id === activeSiteId)

  return (
    <div>
      {!fixedSiteId && <PageHeader title="Проверить фото" info="Загрузите фотографию объекта: она анализируется отдельно от камер и их истории." />}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_380px]">
        <section className="space-y-4">
          <div
            className="rounded-xl border-2 border-dashed border-border-strong bg-card p-5 sm:p-6"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => { event.preventDefault(); chooseFile(event.dataTransfer.files[0]) }}
          >
            {localPreview ? <img src={localPreview} alt="Выбранная фотография" className="mx-auto max-h-[28rem] rounded-lg object-contain" /> : (
              <button type="button" onClick={() => inputRef.current?.click()} className="flex min-h-56 w-full flex-col items-center justify-center text-muted-foreground">
                <ImagePlus className="mb-2 h-12 w-12" />
                <span className="text-lg font-semibold text-foreground">Нажмите или перетащите фото сюда</span>
                <span className="mt-1 text-[14px]">Фотография анализируется без камеры</span>
              </button>
            )}
            <input ref={inputRef} type="file" accept="image/*" className="sr-only" tabIndex={-1} aria-hidden="true" onChange={(event) => { chooseFile(event.target.files?.[0]); event.target.value = '' }} />
          </div>
          {canUpload ? (
            <div className="space-y-3">
              <label className="flex cursor-pointer items-start gap-3 rounded-lg bg-muted px-3 py-3">
                <input type="checkbox" checked={useSpider} onChange={(event) => setUseSpider(event.target.checked)} className="mt-0.5 h-5 w-5 accent-[var(--color-primary)]" />
                <span>
                  <strong className="block">Использовать дополнительный план и ресурсы Spider</strong>
                  <span className="mt-0.5 block text-[14px] text-muted-foreground">Опция независима от камер. Spider дополняет локальный план и не считается фактическим наблюдением на фото.</span>
                </span>
              </label>
              <div className="flex flex-wrap gap-3">
                <Button variant="outline" size="lg" onClick={() => inputRef.current?.click()}><Upload className="h-5 w-5" /> {file ? 'Выбрать другое фото' : 'Выбрать фото'}</Button>
                <Button size="lg" onClick={submit} disabled={!file || !activeSiteId || upload.isPending}>{upload.isPending && <Loader2 className="h-5 w-5 animate-spin" />} Отправить на анализ</Button>
              </div>
              {upload.isError && <p role="alert" className="rounded-lg bg-danger-bg px-3 py-2 text-danger-fg">{upload.error instanceof ApiError ? upload.error.message : 'Не удалось отправить фотографию на анализ.'}</p>}
            </div>
          ) : <p className="rounded-lg bg-muted px-3 py-3 text-muted-foreground">Загружать фотографии могут руководитель объекта и администратор.</p>}
        </section>

        <aside className="space-y-4">
          {!fixedSiteId && <Card><CardBody>
            <label htmlFor="photo-site" className="mb-2 block font-semibold">Объект</label>
            <select id="photo-site" value={activeSiteId} onChange={(event) => { setSiteId(event.target.value); setCreated(null); setSelectedId(null) }} className="w-full min-h-[48px] rounded-lg border border-border-strong bg-card px-3 text-[16px] font-medium">
              {sites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}
            </select>
          </CardBody></Card>}
          <Card><CardBody>
            <h2 className="font-semibold">Последние фото{activeSite ? ` · ${activeSite.name}` : ''}</h2>
            {photos.isPending ? <p className="mt-3 text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />Загружаем…</p> : photos.isError ? <p role="alert" className="mt-3 text-danger-fg">{photos.error instanceof ApiError ? photos.error.message : 'Не удалось загрузить недавние фото.'}</p> : photos.data?.length === 0 ? <p className="mt-3 text-muted-foreground">Пока нет загруженных фотографий.</p> : (
              <div className="mt-3 space-y-2">
                {photos.data?.map((photo) => <button key={photo.id} type="button" onClick={() => setSelectedId(photo.id)} className={`flex min-h-[44px] w-full items-center justify-between rounded-lg border px-3 text-left text-[14px] transition-colors ${selected?.id === photo.id ? 'border-primary bg-info-bg' : 'border-border hover:border-primary/60'}`}>
                  <span>{fmtWhen(photo.at)}</span><PhotoStatus photo={photo} />
                </button>)}
              </div>
            )}
          </CardBody></Card>
        </aside>
      </div>
      {selected && <PhotoResult photo={selected} siteId={activeSiteId} preview={selected.id === created?.id ? localPreview : null} />}
    </div>
  )
}

function PhotoStatus({ photo }: { photo: PhotoAnalysis }) {
  if (photo.answers.length === 0) return <Badge tone="info"><Clock3 className="h-3.5 w-3.5" /> Подготавливаем</Badge>
  if (photo.answers.some((answer) => answer.state === 'pending')) return <Badge tone="info"><Clock3 className="h-3.5 w-3.5" /> Анализируем</Badge>
  if (photo.answers.some((answer) => answer.state === 'error')) return <Badge tone="danger">Ошибка</Badge>
  if (photo.answers.some((answer) => answer.state === 'done')) return <Badge tone="ok"><CheckCircle2 className="h-3.5 w-3.5" /> Готово</Badge>
  return <Badge tone="neutral">Нет результата</Badge>
}

function PhotoResult({ photo, siteId, preview }: { photo: PhotoAnalysis; siteId: string; preview: string | null }) {
  return (
    <section className="mt-5 grid gap-5 rounded-xl border border-border bg-card p-4 sm:p-5 lg:grid-cols-[minmax(0,1fr)_380px]">
      <ProtectedPhoto siteId={siteId} photo={photo} preview={preview} />
      <div>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h2 className="text-[18px] font-semibold">Результат анализа</h2><PhotoStatus photo={photo} /></div>
        {photo.llmWaiting > 0 && (
          <p role="status" className="mb-3 rounded-lg bg-warn-bg px-3 py-2 text-[14px] text-warn-fg">
            Нейросетевые анализы стоят в очереди: ожидают {photo.llmWaiting}. Дождитесь завершения предыдущих анализов.
          </p>
        )}
        {photo.answers.length === 0 ? <p className="text-muted-foreground">Ожидаем постановку анализа в очередь…</p> : <div className="space-y-3">{photo.answers.map((answer) => <PhotoAnswer key={answer.service} answer={answer} />)}</div>}
      </div>
    </section>
  )
}

function ProtectedPhoto({ siteId, photo, preview }: { siteId: string; photo: PhotoAnalysis; preview: string | null }) {
  const [url, setUrl] = useState<string | null>(preview)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (preview) { setUrl(preview); setError(null); return }
    let active = true
    let objectUrl: string | null = null
    setUrl(null)
    setError(null)
    void api.photoAnalysisImage(siteId, photo.id).then((blob) => {
      objectUrl = URL.createObjectURL(blob)
      if (active) setUrl(objectUrl)
      else URL.revokeObjectURL(objectUrl)
    }).catch((reason: unknown) => { if (active) setError(reason instanceof ApiError ? reason.message : 'Не удалось загрузить защищённую фотографию.') })
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [siteId, photo.id, preview])
  if (error) return <p role="alert" className="text-danger-fg">{error}</p>
  if (!url) return <div className="flex min-h-56 items-center justify-center rounded-lg bg-muted text-muted-foreground"><Loader2 className="mr-2 h-5 w-5 animate-spin" />Загружаем защищённое фото…</div>
  return <img src={url} alt={`Загруженная фотография от ${fmtWhen(photo.at)}`} className="max-h-[32rem] w-full rounded-lg bg-slate-900 object-contain" />
}

function PhotoAnswer({ answer }: { answer: ServiceAnswer }) {
  const groups = answer.groups.filter((group) => group.works.length > 0)
  return (
    <article className="rounded-lg border border-border p-3 text-[14px]">
      <div className="flex flex-wrap items-center justify-between gap-2"><strong>{SERVICE_LABEL[answer.service]}</strong><span className="text-muted-foreground">{answer.state === 'pending' ? 'Выполняется' : answer.state === 'done' ? 'Готово' : answer.state === 'error' ? 'Ошибка' : 'Нет результата'}</span></div>
      {answer.error && <p role="alert" className="mt-2 text-danger-fg">{answer.error}</p>}
      {groups.length > 0 && <ul className="mt-2 space-y-2">{groups.map((group, index) => <li key={`${group.match}-${index}`}><div className="font-medium">{group.works.map((work) => work.name).join(' / ')}</div>{group.explanation && <p className="text-muted-foreground">{group.explanation}</p>}</li>)}</ul>}
      {answer.resourceAssessment ? (
        <ResourceAssessment
          assessment={answer.resourceAssessment}
          analysisMode={answer.analysisMode}
          limitations={answer.limitations}
          evidence={answer.resourceEvidence}
        />
      ) : answer.limitations.length > 0 ? (
        <div className="mt-3 rounded-md bg-warn-bg px-2.5 py-2 text-warn-fg"><strong>Ограничения:</strong><ul className="mt-1 list-disc pl-4">{answer.limitations.map((limitation, index) => <li key={`${limitation}-${index}`}>{limitation}</li>)}</ul></div>
      ) : null}
    </article>
  )
}
