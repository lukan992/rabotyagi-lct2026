import { useEffect, useRef, type RefObject } from 'react'

/** Открытые окна, верхнее — последнее. Escape и Tab обслуживает только оно: видео поверх истории камеры закрывается одно. */
const stack: object[] = []
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * Поведение диалога: фокус переходит в окно, по Tab не уходит из него и возвращается туда, откуда окно открыли;
 * Escape закрывает только верхнее окно; страница под окном не прокручивается.
 */
export function useDialog(open: boolean, panel: RefObject<HTMLElement | null>, onClose: () => void, onKey?: (e: KeyboardEvent) => void) {
  const close = useRef(onClose)
  const extra = useRef(onKey)
  useEffect(() => {
    close.current = onClose
    extra.current = onKey
  })

  // зависимость только open: иначе при каждом рендере фокус прыгал бы из поля ввода на окно
  useEffect(() => {
    if (!open) return
    const self = {}
    stack.push(self)
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    panel.current?.focus()
    const handle = (e: KeyboardEvent) => {
      if (stack[stack.length - 1] !== self || !panel.current) return
      if (e.key === 'Escape') close.current()
      else if (e.key === 'Tab') keepFocusInside(e, panel.current)
      else extra.current?.(e)
    }
    window.addEventListener('keydown', handle)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', handle)
      stack.splice(stack.indexOf(self), 1)
      if (!stack.length) document.body.style.overflow = ''
      // без прокрутки: кнопка, открывшая окно, и так на виду, а если страница после действия прокрутилась к его
      // результату (новое правило в конце списка) — пусть там и остаётся
      opener?.focus({ preventScroll: true })
    }
  }, [open, panel])
}

/** Tab с последнего элемента окна ведёт на первый, Shift+Tab с первого — на последний */
function keepFocusInside(e: KeyboardEvent, root: HTMLElement) {
  const items = [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.getClientRects().length > 0)
  if (!items.length) {
    e.preventDefault()
    return
  }
  const first = items[0], last = items[items.length - 1], active = document.activeElement
  const target = !root.contains(active) ? (e.shiftKey ? last : first)
    : e.shiftKey && (active === first || active === root) ? last
    : !e.shiftKey && active === last ? first
    : null
  if (target) {
    e.preventDefault()
    target.focus()
  }
}
