import { useState, type ReactNode } from 'react'
import { ScanSearch } from 'lucide-react'
import type { Camera } from '@/data'
import { useLive } from '@/lib/useLive'
import { cn, pluralWord } from '@/lib/utils'
import { VideoTile } from './VideoTile'
import { CameraViewer } from './CameraViewer'

const BOXES_KEY = 'sk-boxes'

function readBoxes() {
  try { return localStorage.getItem(BOXES_KEY) !== 'off' } catch { return true }
}

/**
 * Видеостена: живое видео всех камер сразу. Нажатие на камеру — просмотр на всю вкладку, там же стрелками — к соседним.
 * sections — камеры разложены по объектам (у руководителя, инспектора, администратора).
 */
export function VideoWall({ cameras, sections, empty = 'Камер пока нет.', actions }: {
  cameras: Camera[]
  sections?: { id: string; title: string; cameras: Camera[]; extra?: ReactNode }[]
  empty?: string
  /** Кнопки под видео каждой камеры (управление у администратора) */
  actions?: (camera: Camera) => ReactNode
}) {
  const live = useLive(cameras.length > 0)
  const [opened, setOpened] = useState<number | null>(null)
  const [showBoxes, setShowBoxesState] = useState(readBoxes)
  const setShowBoxes = (on: boolean) => {
    setShowBoxesState(on)
    try { localStorage.setItem(BOXES_KEY, on ? 'on' : 'off') } catch { /* приватный режим */ }
  }
  const onAir = cameras.filter((c) => c.enabled && live.get(c.id)?.online).length
  const groups = sections ?? [{ id: 'all', title: '', cameras }]
  const order = groups.flatMap((g) => g.cameras) // листаем в просмотре в том же порядке, что на экране

  if (!cameras.length && !sections?.length) return <p className="text-muted-foreground">{empty}</p>
  return (
    <div>
      <div className={cn('flex flex-wrap items-center justify-between gap-3 mb-4', !cameras.length && 'hidden')}>
        <p className="text-muted-foreground" role="status">
          В эфире {onAir} из {cameras.length} {pluralWord(cameras.length, 'камеры', 'камер', 'камер')}
        </p>
        <button
          type="button" onClick={() => setShowBoxes(!showBoxes)} aria-pressed={showBoxes}
          className={cn(
            'inline-flex items-center gap-2 min-h-[44px] px-4 rounded-lg border font-medium cursor-pointer transition-colors',
            showBoxes ? 'border-primary bg-info-bg text-info-fg' : 'border-border-strong bg-card hover:bg-muted',
          )}
        >
          <ScanSearch className="w-5 h-5" /> Рамки техники
        </button>
      </div>

      <div className="space-y-8">
        {groups.map((group) => (
          <section key={group.id} aria-label={group.title || undefined}>
            {(group.title || group.extra) && (
              <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                {group.title && <h2 className="text-[18px] font-semibold">{group.title}</h2>}
                {group.extra}
              </div>
            )}
            {group.cameras.length === 0 ? (
              <p className="text-muted-foreground">На объекте пока нет камер.</p>
            ) : (
              <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">
                {group.cameras.map((camera) => (
                  <VideoTile
                    key={camera.id} camera={camera} live={live.get(camera.id)} showBoxes={showBoxes}
                    paused={opened !== null} onOpen={() => setOpened(order.indexOf(camera))} actions={actions?.(camera)}
                  />
                ))}
              </div>
            )}
          </section>
        ))}
      </div>

      <CameraViewer
        cameras={order} index={opened} onIndex={setOpened} onClose={() => setOpened(null)}
        live={live} showBoxes={showBoxes} onShowBoxes={setShowBoxes}
      />
    </div>
  )
}
