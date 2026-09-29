import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Loader2, RotateCw, Save, Unplug } from 'lucide-react'
import { api, ApiError } from '@/api'
import { Button } from './ui/Button'
import { Field, FormError, inputCls } from './ui/Field'

/** Настройка происхождения Spider хранится отдельно для каждого объекта. Секрет не приходит в браузер. */
export function SpiderConnection({ siteId }: { siteId: string }) {
  const queryClient = useQueryClient()
  const key = ['spider-connection', siteId]
  const { data, isPending, isError, error } = useQuery({ queryKey: key, queryFn: () => api.spiderConnection(siteId) })
  const [url, setUrl] = useState<string | null>(null)
  const [token, setToken] = useState('')
  const [touched, setTouched] = useState(false)
  const save = useMutation({
    mutationFn: (next: { url: string; token?: string | null }) => api.saveSpiderConnection(siteId, next),
    onSuccess: (connection) => {
      setUrl(connection.url ?? '')
      setToken('')
      setTouched(false)
      void queryClient.invalidateQueries({ queryKey: key })
      void queryClient.invalidateQueries({ queryKey: ['spider', siteId] })
      void queryClient.invalidateQueries({ queryKey: ['spider-stage-links', siteId] })
    },
  })

  if (isPending) return <div className="h-32 animate-pulse rounded-lg bg-muted" aria-busy />
  if (isError || !data) return <p role="alert" className="rounded-lg bg-danger-bg px-3 py-2 text-[14px] text-danger-fg">{error instanceof ApiError ? error.message : 'Не удалось загрузить подключение Spider.'}</p>

  const currentUrl = url ?? data.url ?? ''
  let errorText: string | undefined
  if (touched) {
    try {
      const parsed = new URL(currentUrl.trim())
      if (!/^https?:$/.test(parsed.protocol) || parsed.pathname !== '/' || parsed.search || parsed.hash || parsed.username || parsed.password) {
        errorText = 'Укажите только origin источника: http(s)://host[:port]'
      }
    } catch {
      errorText = 'Укажите origin источника: http(s)://host[:port]'
    }
  }
  const retainsToken = data.custom && data.hasToken && currentUrl.trim() === data.url
  const submit = (event: FormEvent) => {
    event.preventDefault()
    setTouched(true)
    if (save.isPending || errorText) return
    const body: { url: string; token?: string | null } = { url: currentUrl.trim() }
    if (token) body.token = token
    save.mutate(body)
  }
  const removeToken = () => {
    if (!data.url || save.isPending) return
    save.mutate({ url: data.url, token: null })
  }

  return (
    <section className="mt-5 rounded-lg border border-border bg-muted/40 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 font-semibold"><KeyRound className="h-4 w-4" /> Подключение Spider</h3>
          <p className="mt-1 text-[14px] text-muted-foreground">URL и токен действуют только для этого объекта. Токен шифруется на сервере и никогда не показывается в форме.</p>
        </div>
        <span className="rounded-sm bg-card px-2 py-1 text-[13px] font-medium text-muted-foreground">{data.configured ? (data.custom ? 'Настроено для объекта' : 'Используется серверное подключение') : 'Не настроено'}</span>
      </div>
      <form onSubmit={submit} noValidate className="mt-4 space-y-4">
        <Field label="Origin Spider" error={errorText} hint="Только http(s)://host[:port], без пути и учётных данных.">
          {(id, describedBy) => <input id={id} value={currentUrl} onChange={(event) => setUrl(event.target.value)} onBlur={() => setTouched(true)} className={inputCls} aria-invalid={!!errorText} aria-describedby={describedBy} placeholder="https://spider.example" inputMode="url" autoComplete="url" />}
        </Field>
        <Field label={retainsToken ? 'Новый токен Spider (необязательно)' : 'Токен Spider'} hint={retainsToken ? 'Оставьте пустым, чтобы сохранить текущий токен; введите новый, чтобы заменить его.' : data.hasToken && !data.custom ? 'Серверный токен не копируется в настройку объекта. Введите новый токен, если он нужен этому origin.' : 'При смене origin пустой токен очищает старый секрет. Токен сохраняется зашифрованно и не возвращается в браузер.'}>
          {(id, describedBy) => <input id={id} value={token} onChange={(event) => setToken(event.target.value)} className={inputCls} aria-describedby={describedBy} type="password" autoComplete="new-password" placeholder={retainsToken ? 'Токен сохранён' : 'Введите токен (если требуется)'} />}
        </Field>
        <FormError message={save.isError ? save.error instanceof ApiError ? save.error.message : 'Не удалось сохранить подключение Spider.' : null} />
        <div className="flex flex-wrap gap-3">
          <Button type="submit" disabled={save.isPending}>{save.isPending ? <Loader2 className="h-5 w-5 animate-spin" /> : token ? <RotateCw className="h-5 w-5" /> : <Save className="h-5 w-5" />}{token ? 'Сохранить и заменить токен' : 'Сохранить подключение'}</Button>
          {data.custom && data.hasToken && data.url && <Button type="button" variant="outline" disabled={save.isPending} onClick={removeToken}><Unplug className="h-5 w-5" /> Удалить токен</Button>}
        </div>
      </form>
    </section>
  )
}
