import { useEffect, type RefObject } from 'react'

const ITEMS = '[role^="menuitem"]:not([disabled])'

/**
 * Клавиатура во всплывающем меню (как у системных меню): при открытии фокус — на первом пункте,
 * стрелки ↑↓ ходят по пунктам по кругу, Home/End — к первому и последнему, Escape закрывает и возвращает фокус
 * на кнопку меню, Tab закрывает меню и отпускает фокус дальше по странице.
 */
export function useMenuKeys(menu: RefObject<HTMLElement | null>, trigger: RefObject<HTMLElement | null>, open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return
    const items = () => [...(menu.current?.querySelectorAll<HTMLElement>(ITEMS) ?? [])]
    const checked = menu.current?.querySelector<HTMLElement>('[aria-checked="true"]:not([disabled])')
    // меню появляется с анимацией — фокус ставим, когда пункты уже в документе
    const frame = requestAnimationFrame(() => (checked ?? items()[0])?.focus())
    const onKey = (e: KeyboardEvent) => {
      const list = items()
      const at = list.indexOf(document.activeElement as HTMLElement)
      const go = (i: number) => { e.preventDefault(); list[(i + list.length) % list.length]?.focus() }
      if (e.key === 'ArrowDown') go(at + 1)
      else if (e.key === 'ArrowUp') go(at - 1)
      else if (e.key === 'Home') go(0)
      else if (e.key === 'End') go(list.length - 1)
      else if (e.key === 'Escape') { e.preventDefault(); onClose(); trigger.current?.focus() }
      else if (e.key === 'Tab') onClose()
    }
    const el = menu.current
    el?.addEventListener('keydown', onKey)
    return () => { cancelAnimationFrame(frame); el?.removeEventListener('keydown', onKey) }
  }, [menu, trigger, open, onClose])
}
