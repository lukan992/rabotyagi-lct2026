import { useState } from 'react'
import { Loader2, Pencil, PlugZap, Trash2 } from 'lucide-react'
import { api, ApiError } from '@/api'
import type { Camera } from '@/data'
import { useApp } from '@/store/context'
import { Modal } from '../ui/Modal'
import { Button } from '../ui/Button'
import { EditCameraDialog } from './EditCameraDialog'

// =====================================================================================
//  Управление камерой (администратор): кнопки под видео камеры и их диалоги
// =====================================================================================
/** Адрес камеры и кнопки «Изменить», «Проверить связь», «Удалить» — прямо у камеры в списке. Выключать камеры нельзя */
export function CameraAdminActions({ camera }: { camera: Camera }) {
  const { deleteCamera, notify, refresh } = useApp()
  const [editing, setEditing] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [deleting, setDeleting] = useState(false) // двойное нажатие не шлёт второй DELETE
  const [testing, setTesting] = useState(false)

  const test = async () => {
    setTesting(true)
    try {
      const result = await api.testCamera(camera.id)
      notify(`${camera.name}: ${result.message}`, result.ok ? 'ok' : 'error')
    } catch (e) {
      notify(e instanceof ApiError ? e.message : 'Не удалось проверить камеру', 'error')
    } finally {
      setTesting(false)
      void refresh()
    }
  }

  return (
    <>
      <div className="w-full text-[13px] text-muted-foreground break-all">
        {camera.demo ? 'Демо-ролик' : <span className="font-mono">{camera.address}</span>}{camera.hasCredentials && ' · с паролем'}
      </div>
      <Button variant="outline" onClick={() => setEditing(true)}><Pencil className="w-4 h-4" /> Изменить</Button>
      <Button variant="outline" disabled={testing} onClick={() => void test()}>
        {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <PlugZap className="w-4 h-4" />} Проверить связь
      </Button>
      <Button variant="ghost" aria-label={`Удалить ${camera.name}`} onClick={() => setRemoving(true)}><Trash2 className="w-4 h-4" /></Button>

      <EditCameraDialog camera={editing ? camera : null} onClose={() => setEditing(false)} />
      <Modal open={removing} onClose={() => setRemoving(false)} title="Удалить камеру?">
        <div className="space-y-5">
          <p>«{camera.name}» перестанет показывать видео и исчезнет из списков. Кадры, которые служат доказательствами в предупреждениях, сохранятся.</p>
          <div className="flex flex-wrap gap-3">
            <Button variant="danger" size="lg" disabled={deleting} onClick={async () => { setDeleting(true); await deleteCamera(camera); setDeleting(false); setRemoving(false) }}>
              {deleting && <Loader2 className="w-5 h-5 animate-spin" />} Удалить
            </Button>
            <Button variant="outline" size="lg" onClick={() => setRemoving(false)}>Отмена</Button>
          </div>
        </div>
      </Modal>
    </>
  )
}
