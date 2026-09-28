import { m } from 'framer-motion'
import { Loader2 } from 'lucide-react'

/** Пока подгружается код экрана. Появляется с задержкой: быстрая загрузка проходит без мелькания. */
export function PageLoading() {
  return (
    <m.div
      role="status"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ delay: 0.3, duration: 0.2 }}
      className="py-16 flex flex-col items-center gap-3 text-muted-foreground"
    >
      <Loader2 className="w-8 h-8 text-primary animate-spin" aria-hidden />
      Открываем раздел…
    </m.div>
  )
}
