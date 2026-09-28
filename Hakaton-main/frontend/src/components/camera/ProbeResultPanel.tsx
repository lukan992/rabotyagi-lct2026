import { CheckCircle2, Loader2, XCircle } from 'lucide-react'
import type { ProbeResult } from '@/data'
import { useApp } from '@/store/context'
import { streamSource, useVideo } from '@/lib/video'
import { cn } from '@/lib/utils'

/** Проверка подключения: камера отвечает — показываем её живое видео прямо в форме */
export function ProbeResultPanel({ probe }: { probe: ProbeResult | null }) {
  const { meta } = useApp()
  const { videoRef, state } = useVideo(probe?.previewPath ? streamSource(meta, probe.previewPath) : null)
  if (!probe) return null
  return (
    <div className={cn('rounded-xl p-4', probe.ok ? 'bg-ok-bg text-ok-fg' : 'bg-danger-bg text-danger-fg')}>
      <div className="flex items-start gap-2.5 font-semibold">
        {probe.ok ? <CheckCircle2 className="w-5 h-5 shrink-0 mt-0.5" /> : <XCircle className="w-5 h-5 shrink-0 mt-0.5" />}
        <span>{probe.message}{probe.ok && probe.elapsedMs > 0 && <span className="font-normal"> · ответ за {probe.elapsedMs} мс</span>}</span>
      </div>
      {probe.previewPath && (
        <div className="relative mt-3 w-full max-w-md aspect-video rounded-lg overflow-hidden bg-slate-950">
          <video ref={videoRef} autoPlay muted playsInline aria-label="Видео с камеры" className="absolute inset-0 w-full h-full object-contain" />
          {state !== 'playing' && state !== 'error' && <div className="absolute inset-0 flex items-center justify-center text-white/80"><Loader2 className="w-7 h-7 animate-spin" /></div>}
          {state === 'error' && <div className="absolute inset-0 flex items-center justify-center text-white/80 px-4 text-center">Не удалось открыть предпросмотр видео</div>}
        </div>
      )}
    </div>
  )
}
