import { useMemo, useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { KeyRound, Loader2, Pencil, Phone, Plus, Power, Search, Trash2 } from 'lucide-react'
import { api } from '@/api'
import { ROLES, type RoleId, type User } from '@/data'
import { useApp } from '@/store/context'
import { PageHeader } from '@/components/ui/PageHeader'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { Field, inputCls } from '@/components/ui/Field'
import { ActionMenu } from '@/components/ui/ActionMenu'
import { cn } from '@/lib/utils'

/** Сотрудники: завести, изменить роль и объекты, сменить пароль, отключить, удалить (администратор) */
export function AdminUsers() {
  const { user: me, bySite, authMode, run } = useApp()
  const users = useQuery({ queryKey: ['users'], queryFn: api.users })
  const [editing, setEditing] = useState<User | 'new' | null>(null)
  const [password, setPassword] = useState<User | null>(null)
  const [removing, setRemoving] = useState<User | null>(null)
  // кого сейчас включаем, отключаем или удаляем — повторное нажатие по нему не шлёт второй запрос, другие сотрудники доступны
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set())
  const mark = (id: string, on: boolean) => setBusy((prev) => {
    const next = new Set(prev)
    if (on) next.add(id)
    else next.delete(id)
    return next
  })
  const [text, setText] = useState('')
  const [roleFilter, setRoleFilter] = useState<RoleId | 'all'>('all')
  const list = useMemo(() => {
    const words = text.trim().toLowerCase().split(/\s+/).filter(Boolean)
    return (users.data ?? [])
      .filter((u) => roleFilter === 'all' || u.role === roleFilter)
      .filter((u) => {
        const hay = [u.name, u.login, u.phone, ...u.siteIds.map((id) => bySite(id)?.name)].join(' ').toLowerCase()
        return words.every((w) => hay.includes(w))
      })
  }, [users.data, text, roleFilter, bySite])
  const toggle = async (u: User) => {
    if (busy.has(u.id)) return
    mark(u.id, true)
    await run(() => api.updateUser(u.id, { isActive: !u.isActive }), `${u.name}: ${u.isActive ? 'доступ отключён' : 'доступ включён'}`)
    mark(u.id, false)
  }

  return (
    <div>
      <PageHeader
        title="Сотрудники"
        info={<>
          Кто работает в системе, с какой ролью и к каким объектам у него доступ. Здесь же меняют пароли.
          {authMode === 'keycloak' && <> Вход идёт через Keycloak: сотрудники, роли и пароли, которые вы меняете здесь, сразу меняются и в Keycloak.</>}
        </>}
        action={<Button size="lg" onClick={() => setEditing('new')}><Plus className="w-5 h-5" /> Добавить сотрудника</Button>}
      />
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_220px] mb-4">
        <label className="relative block">
          <span className="sr-only">Поиск сотрудника</span>
          <Search className="w-5 h-5 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <input type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder="Фамилия, логин, телефон, объект…" className={cn(inputCls, 'pl-10')} />
        </label>
        <label>
          <span className="sr-only">Роль</span>
          <select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value as RoleId | 'all')} className={inputCls}>
            <option value="all">Все роли</option>
            {ROLES.map((r) => <option key={r.id} value={r.id}>{r.title}</option>)}
          </select>
        </label>
      </div>
      {users.isPending && <p className="text-muted-foreground">Загружаем список…</p>}
      {users.isError && <p role="alert" className="text-danger">Не удалось загрузить сотрудников.</p>}
      {users.isSuccess && list.length === 0 && <p className="text-muted-foreground">Никого не нашли. Измените поиск или роль.</p>}
      <ul className="space-y-3">
        {list.map((u) => {
          const self = u.id === me?.id
          return (
            <li key={u.id} className={cn('bg-card rounded-xl border border-border shadow-[var(--shadow-card)] p-4 flex flex-wrap items-center gap-x-5 gap-y-3', !u.isActive && 'opacity-70')}>
              <div className="min-w-[220px] flex-1">
                <div className="font-semibold text-[17px] flex flex-wrap items-center gap-2">
                  {u.name}
                  <Badge tone="info">{ROLES.find((r) => r.id === u.role)?.title}</Badge>
                  {!u.isActive && <Badge tone="neutral">Отключён</Badge>}
                  {self && <Badge tone="neutral">Это вы</Badge>}
                </div>
                <div className="text-[14px] text-muted-foreground mt-0.5">
                  <span className="font-mono">{u.login}</span> ·{' '}
                  {u.role !== 'foreman' ? 'все объекты' : u.siteIds.length === 0 ? 'объект не назначен' : u.siteIds.map((id) => bySite(id)?.name).join(', ')}
                </div>
                {u.phone && (
                  <a href={`tel:${u.phone}`} className="inline-flex items-center gap-1.5 text-primary font-medium min-h-[44px] text-[15px]">
                    <Phone className="w-4 h-4" />{u.phone}
                  </a>
                )}
              </div>
              {/* главное действие — на виду, редкие — в меню «⋯» */}
              <div className="flex items-center gap-1">
                <Button variant="outline" size="sm" onClick={() => setEditing(u)}><Pencil className="w-4 h-4" /> Изменить</Button>
                <ActionMenu
                  label={`Ещё действия: ${u.name}`}
                  actions={[
                    { label: 'Сменить пароль', Icon: KeyRound, onSelect: () => setPassword(u) },
                    {
                      label: u.isActive ? 'Отключить доступ' : 'Включить доступ', Icon: Power, onSelect: () => void toggle(u),
                      disabled: self || busy.has(u.id), hint: self ? 'Себя отключить нельзя' : undefined,
                    },
                    { label: 'Удалить', Icon: Trash2, danger: true, onSelect: () => setRemoving(u), disabled: self, hint: 'Себя удалить нельзя' },
                  ]}
                />
              </div>
            </li>
          )
        })}
      </ul>

      <section className="mt-8">
        <h2 className="text-[18px] font-semibold mb-3">Что видит каждая роль</h2>
        <div className="grid sm:grid-cols-2 gap-3">
          {ROLES.map((r) => (
            <div key={r.id} className="bg-card rounded-xl border border-border p-4">
              <div className="font-semibold">{r.title}</div>
              <div className="text-muted-foreground text-[14px]">{r.description}</div>
            </div>
          ))}
        </div>
      </section>

      <UserDialog user={editing} onClose={() => setEditing(null)} />
      <PasswordDialog user={password} onClose={() => setPassword(null)} />
      <Modal open={!!removing} onClose={() => setRemoving(null)} title="Удалить сотрудника?">
        {removing && (
          <div className="space-y-5">
            <p>«{removing.name}» больше не сможет войти в систему. Его ответы на отклонения и записи в журнале действий сохранятся.</p>
            <p className="text-muted-foreground text-[15px]">Если сотрудник может вернуться, лучше отключите его — удалять не обязательно.</p>
            <div className="flex flex-wrap gap-3">
              <Button
                variant="danger" size="lg" disabled={busy.has(removing.id)}
                onClick={async () => {
                  mark(removing.id, true)
                  const ok = await run(() => api.deleteUser(removing.id), `${removing.name} удалён`)
                  mark(removing.id, false)
                  if (ok) setRemoving(null)
                }}
              >
                {busy.has(removing.id) && <Loader2 className="w-5 h-5 animate-spin" />} Удалить
              </Button>
              <Button variant="outline" size="lg" onClick={() => setRemoving(null)}>Отмена</Button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}

function UserDialog({ user, onClose }: { user: User | 'new' | null; onClose: () => void }) {
  return (
    <Modal open={user !== null} onClose={onClose} title={user === 'new' ? 'Новый сотрудник' : user ? `Изменить: ${user.name}` : ''}>
      {user !== null && <UserForm key={user === 'new' ? 'new' : user.id} user={user === 'new' ? null : user} onClose={onClose} />}
    </Modal>
  )
}

function UserForm({ user, onClose }: { user: User | null; onClose: () => void }) {
  const { user: me, sites, run } = useApp()
  const [login, setLogin] = useState(user?.login ?? '')
  const [name, setName] = useState(user?.name ?? '')
  const [role, setRole] = useState<RoleId>(user?.role ?? 'foreman')
  const [phone, setPhone] = useState(user?.phone ?? '')
  const [siteIds, setSiteIds] = useState<string[]>(user?.siteIds ?? [])
  const [pass, setPass] = useState('')
  const [tried, setTried] = useState(false)
  const [busy, setBusy] = useState(false)
  const self = user?.id === me?.id

  const errors = {
    login: !user && !/^[A-Za-z0-9._-]{2,64}$/.test(login.trim()) ? 'Латинские буквы, цифры, точка, дефис — от 2 символов' : undefined,
    name: name.trim().length < 2 ? 'Введите фамилию и имя' : undefined,
    pass: !user && pass.length < 6 ? 'Не короче 6 символов' : undefined,
  }
  const shown = (key: keyof typeof errors) => (tried ? errors[key] : undefined)
  const toggleSite = (id: string) => setSiteIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]))

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setTried(true)
    if (Object.values(errors).some(Boolean)) return
    setBusy(true)
    const ids = role === 'foreman' ? siteIds : [] // руководителю, инспектору и администратору объекты не нужны: видят все
    const ok = await run(
      () => (user
        ? api.updateUser(user.id, { name: name.trim(), role, phone: phone.trim(), siteIds: ids })
        : api.createUser({ login: login.trim().toLowerCase(), name: name.trim(), role, phone: phone.trim(), siteIds: ids, password: pass })),
      user ? 'Сотрудник сохранён' : `${name.trim()} добавлен — теперь может войти`,
    )
    setBusy(false)
    if (ok) onClose()
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      {!user && (
        <Field label="Логин для входа" error={shown('login')}>
          {(id, d) => <input id={id} value={login} maxLength={64} autoComplete="off" spellCheck={false} onChange={(e) => setLogin(e.target.value)} aria-invalid={!!shown('login')} aria-describedby={d} className={cn(inputCls, 'font-mono')} placeholder="prorab5" />}
        </Field>
      )}
      <Field label="Фамилия и имя" error={shown('name')}>
        {(id, d) => <input id={id} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} aria-invalid={!!shown('name')} aria-describedby={d} className={inputCls} placeholder="Лебедев Олег" />}
      </Field>
      <fieldset>
        <legend className="block text-[15px] font-medium mb-1.5">Роль</legend>
        <div className="grid sm:grid-cols-2 gap-2">
          {ROLES.map((r) => (
            <label key={r.id} className={cn('flex items-start gap-3 rounded-lg border px-3 py-2.5 cursor-pointer transition-colors', role === r.id ? 'border-primary bg-info-bg' : 'border-border-strong hover:bg-muted', self && r.id !== 'admin' && 'opacity-50 cursor-not-allowed')}>
              <input type="radio" name="role" value={r.id} checked={role === r.id} disabled={self && r.id !== 'admin'} onChange={() => setRole(r.id)} className="mt-1 accent-[var(--color-primary)]" />
              <span><span className={cn('block font-semibold', role === r.id && 'text-info-fg')}>{r.title}</span><span className="block text-[13px] text-muted-foreground">{r.subtitle}</span></span>
            </label>
          ))}
        </div>
        {self && <p className="text-[13px] text-muted-foreground mt-1">Свою роль администратора снять нельзя.</p>}
      </fieldset>
      {role === 'foreman' && (
        <fieldset>
          <legend className="block text-[15px] font-medium mb-1.5">Объекты прораба</legend>
          <div className="space-y-1.5">
            {sites.map((s) => (
              <label key={s.id} className="flex items-center gap-3 min-h-[44px] cursor-pointer">
                <input type="checkbox" checked={siteIds.includes(s.id)} onChange={() => toggleSite(s.id)} className="w-5 h-5 accent-[var(--color-primary)]" />
                {s.name}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      <Field label="Телефон">
        {(id) => <input id={id} value={phone} maxLength={32} inputMode="tel" onChange={(e) => setPhone(e.target.value)} className={inputCls} placeholder="+7 900 000-00-00" />}
      </Field>
      {!user && (
        <Field label="Пароль" error={shown('pass')} hint="Сообщите его сотруднику лично — потом его можно сменить">
          {(id, d) => <input id={id} type="password" value={pass} maxLength={200} autoComplete="new-password" onChange={(e) => setPass(e.target.value)} aria-invalid={!!shown('pass')} aria-describedby={d} className={inputCls} />}
        </Field>
      )}
      <div className="flex flex-wrap gap-3 pt-2">
        <Button type="submit" size="lg" disabled={busy}>{busy && <Loader2 className="w-5 h-5 animate-spin" />} {user ? 'Сохранить' : 'Добавить сотрудника'}</Button>
        <Button type="button" variant="outline" size="lg" onClick={onClose}>Отмена</Button>
      </div>
    </form>
  )
}

function PasswordDialog({ user, onClose }: { user: User | null; onClose: () => void }) {
  return (
    <Modal open={!!user} onClose={onClose} title={user ? `Пароль: ${user.name}` : ''}>
      {user && <PasswordForm key={user.id} user={user} onClose={onClose} />}
    </Modal>
  )
}

function PasswordForm({ user, onClose }: { user: User; onClose: () => void }) {
  const { run } = useApp()
  const [pass, setPass] = useState('')
  const [again, setAgain] = useState('')
  const [tried, setTried] = useState(false)
  const [busy, setBusy] = useState(false)
  const error = pass.length < 6 ? 'Не короче 6 символов' : pass !== again ? 'Пароли не совпадают' : undefined
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setTried(true)
    if (error) return
    setBusy(true)
    const ok = await run(() => api.setPassword(user.id, pass), `Пароль для ${user.name} изменён`)
    setBusy(false)
    if (ok) onClose()
  }
  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <p className="text-muted-foreground text-[15px]">Новый пароль заработает сразу. Сообщите его сотруднику лично — в журнал действий он не попадает.</p>
      <Field label="Новый пароль">
        {(id) => <input id={id} type="password" value={pass} maxLength={200} autoComplete="new-password" onChange={(e) => setPass(e.target.value)} className={inputCls} />}
      </Field>
      <Field label="Повторите пароль" error={tried ? error : undefined}>
        {(id, d) => <input id={id} type="password" value={again} maxLength={200} autoComplete="new-password" onChange={(e) => setAgain(e.target.value)} aria-invalid={tried && !!error} aria-describedby={d} className={inputCls} />}
      </Field>
      <div className="flex flex-wrap gap-3 pt-2">
        <Button type="submit" size="lg" disabled={busy}>{busy && <Loader2 className="w-5 h-5 animate-spin" />} Сменить пароль</Button>
        <Button type="button" variant="outline" size="lg" onClick={onClose}>Отмена</Button>
      </div>
    </form>
  )
}
