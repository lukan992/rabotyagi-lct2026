import { useId, type ReactNode } from 'react'
import { InfoTip } from './InfoTip'

/** Оформление полей ввода (как в образцовом диалоге «Добавить камеру») */
export const inputCls = 'w-full min-h-[46px] rounded-lg border border-input bg-card px-3 text-[16px] outline-none transition-shadow focus:border-primary focus:ring-4 focus:ring-primary/15 aria-[invalid=true]:border-danger disabled:opacity-60'

/**
 * Поле формы: подпись над полем, пояснение — за значком «?» рядом с подписью (без лишнего текста на экране),
 * ошибка — под полем. Пояснение и ошибка связаны с полем через aria-describedby: экранный диктор прочтёт их сразу.
 */
export function Field({ label, hint, error, className, children }: {
  label: string; hint?: string; error?: string; className?: string
  children: (id: string, describedBy: string | undefined) => ReactNode
}) {
  const id = useId()
  const hintId = `${id}-hint`
  const errorId = `${id}-error`
  const describedBy = [error && errorId, hint && hintId].filter(Boolean).join(' ') || undefined
  return (
    <div className={className}>
      <div className="flex items-center gap-1.5 mb-1.5">
        <label htmlFor={id} className="text-[15px] font-medium">{label}</label>
        {hint && <InfoTip label={`Пояснение: ${label}`}>{hint}</InfoTip>}
      </div>
      {hint && <span id={hintId} className="sr-only">{hint}</span>}
      {children(id, describedBy)}
      {error && <p id={errorId} role="alert" className="text-danger text-[14px] mt-1">{error}</p>}
    </div>
  )
}

/** Итог действия словами: зелёная или красная панель (aria-live объявляет её читалке экрана) */
export function FormError({ message }: { message: string | null }) {
  return (
    <div aria-live="polite">
      {message && <p role="alert" className="rounded-xl bg-danger-bg text-danger-fg px-4 py-3 font-medium">{message}</p>}
    </div>
  )
}
