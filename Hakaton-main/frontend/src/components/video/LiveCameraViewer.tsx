import { useState } from 'react'
import type { Camera } from '@/data'
import { useLive } from '@/lib/useLive'
import { CameraViewer } from './CameraViewer'

/** Одна камера на всю вкладку — например, из карточки отклонения: «что там сейчас» */
export function LiveCameraViewer({ camera, onClose }: { camera: Camera | null; onClose: () => void }) {
  const live = useLive(!!camera)
  const [showBoxes, setShowBoxes] = useState(true)
  return (
    <CameraViewer
      cameras={camera ? [camera] : []} index={camera ? 0 : null} onIndex={() => {}} onClose={onClose}
      live={live} showBoxes={showBoxes} onShowBoxes={setShowBoxes}
    />
  )
}
