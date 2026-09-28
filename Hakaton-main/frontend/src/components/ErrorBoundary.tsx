import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle, RotateCw } from 'lucide-react'
import { Button } from './ui/Button'

/** После обновления сервера старые куски кода удаляются — вкладка, открытая до этого, не может их подгрузить */
function isStaleBundle(error: Error) {
  return /dynamically imported module|Importing a module script failed|error loading dynamically imported module/i.test(error.message)
}

const RELOAD_KEY = 'sk-reloaded-for-bundle'

/**
 * Ошибка при отрисовке не должна превращать приложение в белый экран: показываем, что случилось,
 * и даём повторить. Устаревший код после обновления сервера лечим одной автоматической перезагрузкой.
 */
export class ErrorBoundary extends Component<{ children: ReactNode; full?: boolean }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Ошибка интерфейса', error, info.componentStack)
    if (isStaleBundle(error)) {
      let reloaded = false
      try { reloaded = sessionStorage.getItem(RELOAD_KEY) === '1'; sessionStorage.setItem(RELOAD_KEY, '1') } catch { /* хранилище недоступно */ }
      if (!reloaded) window.location.reload()
    }
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    const stale = isStaleBundle(error)
    return (
      <div role="alert" className={this.props.full ? 'min-h-dvh flex items-center justify-center p-6' : 'py-16'}>
        <div className="max-w-md mx-auto text-center">
          <AlertTriangle className="w-10 h-10 text-warn mx-auto" aria-hidden />
          <h1 className="text-[20px] font-semibold mt-3">{stale ? 'Вышла новая версия' : 'Этот раздел не открылся'}</h1>
          <p className="text-muted-foreground mt-2">
            {stale ? 'Обновите страницу, чтобы продолжить работу.' : 'Что-то пошло не так. Попробуйте ещё раз — если ошибка повторится, сообщите администратору.'}
          </p>
          <div className="flex flex-wrap justify-center gap-3 mt-5">
            <Button size="lg" onClick={() => window.location.reload()}><RotateCw className="w-5 h-5" /> Обновить страницу</Button>
            {!stale && !this.props.full && <Button size="lg" variant="outline" onClick={() => this.setState({ error: null })}>Повторить</Button>}
          </div>
        </div>
      </div>
    )
  }
}
