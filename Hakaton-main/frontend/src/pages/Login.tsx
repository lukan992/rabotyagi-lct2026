import { useState } from 'react'
import type { FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Eye, EyeOff, Loader2, LogIn } from 'lucide-react'
import { api, ApiError } from '@/api'
import { useApp } from '@/store/context'
import { Button } from '@/components/ui/Button'
import { Logo } from '@/components/layout/AppShell'
import { ThemePicker } from '@/components/ThemePicker'

/** Вход. Локально — форма с учётными данными; в режиме Keycloak — кнопка перехода к Keycloak. */
export function Login() {
  const { login, keycloakLogin } = useApp()
  const meta = useQuery({ queryKey: ['meta'], queryFn: api.meta, retry: 1 })
  const keycloak = meta.data?.authMode === 'keycloak'
  const [busy, setBusy] = useState<'form' | null>(null)
  const [name, setName] = useState('')
  const [pass, setPass] = useState('')
  const [show, setShow] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const enter = async (action: () => Promise<void>) => {
    setBusy('form')
    setError(null)
    try {
      await action()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Не удалось войти. Попробуйте ещё раз.')
      setBusy(null)
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !pass) return setError('Введите логин и пароль')
    void enter(() => login(name, pass))
  }

  const input = 'w-full min-h-[48px] rounded-lg border border-border-strong bg-card px-3.5 text-[16px] outline-none transition-shadow focus:border-primary focus:ring-4 focus:ring-primary/15'

  return (
    <div className="min-h-dvh flex flex-col items-center px-4 py-10 sm:py-16">
      <Logo className="text-xl mb-8" />

      {meta.isPending ? (
        // режим входа ещё не известен — не показываем форму, которая через миг может смениться кнопкой Keycloak
        <div role="status" className="w-full max-w-[420px] min-h-[200px] bg-card border border-border rounded-2xl shadow-[var(--shadow-card)] flex items-center justify-center">
          <Loader2 className="w-7 h-7 text-muted-foreground animate-spin" />
          <span className="sr-only">Загружаем…</span>
        </div>
      ) : keycloak ? (
        <div className="w-full max-w-[420px] bg-card border border-border rounded-2xl shadow-[var(--shadow-card)] p-6 sm:p-8 text-center">
          <h1 className="text-2xl font-semibold">Вход в систему</h1>
          <p className="text-muted-foreground mt-1">Единый вход организации через Keycloak</p>
          <Button size="lg" full className="mt-6" onClick={() => { setError(null); keycloakLogin().catch((e) => setError(e instanceof Error ? e.message : 'Не удалось перейти ко входу')) }}>
            <LogIn className="w-5 h-5" /> Войти через Keycloak
          </Button>
          {error && <p role="alert" className="text-danger text-[15px] mt-3">{error}</p>}
        </div>
      ) : (
        <div className="w-full max-w-[420px] bg-card border border-border rounded-2xl shadow-[var(--shadow-card)] p-6 sm:p-8">
          <h1 className="text-2xl font-semibold">Вход в систему</h1>
          <p className="text-muted-foreground mt-1">Контроль строительных площадок по камерам</p>

          <form onSubmit={submit} className="mt-6 space-y-4" noValidate>
            <div>
              <label htmlFor="login" className="block text-[15px] font-medium mb-1.5">Логин</label>
              <input id="login" autoComplete="username" value={name} onChange={(e) => { setName(e.target.value); setError(null) }} className={input} />
            </div>
            <div>
              <label htmlFor="pass" className="block text-[15px] font-medium mb-1.5">Пароль</label>
              <div className="relative">
                <input id="pass" type={show ? 'text' : 'password'} autoComplete="current-password" value={pass} onChange={(e) => { setPass(e.target.value); setError(null) }}
                  aria-describedby={error ? 'login-error' : undefined} aria-invalid={!!error} className={input + ' pr-12'} />
                <button type="button" onClick={() => setShow((s) => !s)} aria-label={show ? 'Скрыть пароль' : 'Показать пароль'} className="absolute right-0.5 top-0.5 w-11 h-11 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground cursor-pointer">
                  {show ? <EyeOff className="w-5 h-5" /> : <Eye className="w-5 h-5" />}
                </button>
              </div>
              {error && <p id="login-error" role="alert" className="text-danger text-[15px] mt-2">{error}</p>}
            </div>
            <Button type="submit" size="lg" full disabled={busy !== null}>
              {busy === 'form' && <Loader2 className="w-5 h-5 animate-spin" />} Войти
            </Button>
          </form>
        </div>
      )}

      {meta.isError && (
        <p role="alert" className="w-full max-w-[420px] mt-4 rounded-xl bg-warn-bg text-warn-fg px-4 py-3 text-[15px]">
          Нет связи с сервером. Проверьте, что бэкенд запущен, и обновите страницу.
        </p>
      )}

      <div className="mt-8 flex flex-col items-center gap-2">
        <span className="text-[13px] text-muted-foreground">Оформление</span>
        <ThemePicker />
      </div>

      <footer className="mt-auto pt-8 flex items-center gap-2.5 text-[13px] text-muted-foreground">
        <img src="/team-logo.jpg" alt="" className="w-7 h-7 rounded-md object-cover" />
        {meta.data && <>Версия {meta.data.version} · </>}команда «Работяги» · ЛЦТ 2026
      </footer>
    </div>
  )
}
