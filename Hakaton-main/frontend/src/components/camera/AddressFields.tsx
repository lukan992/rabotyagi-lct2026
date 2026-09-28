import type { ClipboardEvent } from 'react'
import { cn } from '@/lib/utils'
import { Field, inputCls } from '../ui/Field'
import { addressErrors, parseLink, RTSP_PORT, VENDORS } from './address'
import type { Address } from './address'

/** IP, порт и путь — моноширинным: так проще сверить с наклейкой на камере */
const mono = cn(inputCls, 'font-mono text-[15px]')

/** Поля адреса видеопотока: быстрая настройка, IP, порт, путь, логин и пароль */
export function AddressFields({ value, onChange, touched, onTouch, passwordHint }: {
  value: Address; onChange: (patch: Partial<Address>) => void; touched: Partial<Record<keyof Address, boolean>>
  onTouch: (key: keyof Address) => void; passwordHint: string
}) {
  const errors = addressErrors(value)
  const shown = (key: keyof Address) => (touched[key] ? errors[key] : undefined)
  const pickPreset = (preset: string) => {
    onChange({ preset, ...(VENDORS[preset] ? { path: VENDORS[preset].path, port: value.port || String(RTSP_PORT) } : {}) })
  }
  const onPaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const parsed = parseLink(e.clipboardData.getData('text'))
    if (parsed) {
      e.preventDefault()
      onChange(parsed)
    }
  }
  return (
    <>
      <Field label="Быстрая настройка" hint="Подставит типовой путь видеопотока для камеры этого производителя">
        {(id, describedBy) => (
          <select id={id} aria-describedby={describedBy} value={value.preset} onChange={(e) => pickPreset(e.target.value)} className={inputCls}>
            <option value="">Свой адрес</option>
            {Object.entries(VENDORS).map(([key, vendor]) => <option key={key} value={key}>{vendor.label}</option>)}
          </select>
        )}
      </Field>
      <div className="grid grid-cols-[1fr_110px] gap-3">
        <Field label="IP-адрес камеры" error={shown('host')} hint="Можно вставить ссылку rtsp://… целиком — поля заполнятся сами">
          {(id, describedBy) => (
            <input
              id={id} value={value.host} inputMode="url" autoComplete="off" spellCheck={false} maxLength={255}
              onPaste={onPaste} onChange={(e) => onChange({ host: e.target.value.trim() })}
              onBlur={() => { const parsed = parseLink(value.host); if (parsed) onChange(parsed); onTouch('host') }}
              aria-invalid={!!shown('host')} aria-describedby={describedBy} className={mono} placeholder="192.168.1.64"
            />
          )}
        </Field>
        <Field label="Порт" error={shown('port')}>
          {(id, describedBy) => (
            <input id={id} value={value.port} inputMode="numeric" onChange={(e) => onChange({ port: e.target.value.replace(/\D/g, '') })} onBlur={() => onTouch('port')}
              aria-invalid={!!shown('port')} aria-describedby={describedBy} className={mono} placeholder={String(RTSP_PORT)} />
          )}
        </Field>
      </div>
      <Field label="Путь к видеопотоку" error={shown('path')}>
        {(id, describedBy) => (
          <input id={id} value={value.path} maxLength={500} autoComplete="off" spellCheck={false} onChange={(e) => onChange({ path: e.target.value, preset: '' })}
            onBlur={() => onTouch('path')} aria-invalid={!!shown('path')} aria-describedby={describedBy} className={mono} />
        )}
      </Field>
      <div className="grid sm:grid-cols-2 gap-4">
        <Field label="Логин камеры" hint="Если камера без пароля — оставьте пустым">
          {(id, describedBy) => <input id={id} value={value.username} maxLength={120} autoComplete="off" onChange={(e) => onChange({ username: e.target.value })} aria-describedby={describedBy} className={inputCls} />}
        </Field>
        <Field label="Пароль камеры" hint={passwordHint}>
          {(id, describedBy) => <input id={id} type="password" value={value.password} maxLength={200} autoComplete="new-password" onChange={(e) => onChange({ password: e.target.value })} aria-describedby={describedBy} className={inputCls} />}
        </Field>
      </div>
    </>
  )
}
