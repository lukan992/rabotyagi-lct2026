import { useState } from 'react'
import { Truck, CheckCircle2, XCircle, FileWarning, Wrench, ClipboardCheck, Video, Maximize2 } from 'lucide-react'
import { EQUIPMENT, type Alert, type AlertStatus } from '@/data'
import { useApp } from '@/store/context'
import { isOpen } from '@/store/selectors'
import { fmtDate, fmtDateShort, fmtWhen, plural } from '@/lib/utils'
import { KIND, SEVERITY, STATUS } from '@/lib/labels'
import { Modal } from './ui/Modal'
import { Badge } from './ui/Badge'
import { Disclosure } from './ui/Disclosure'
import { InfoTip } from './ui/InfoTip'
import { Button } from './ui/Button'
import { CameraFrame } from './CameraFrame'
import { FrameViewer } from './FrameViewer'
import { LiveCameraViewer } from './video/LiveCameraViewer'
import { PrescribeDialog } from './PrescribeDialog'
import { adviceFor, confidenceNote } from '@/lib/alertAdvice'
import { cn } from '@/lib/utils'

interface Props {
  alert: Alert | null
  onClose: () => void
}

/** Подробности отклонения: что случилось, доказательства, почему система так решила, что делать */
export function AlertDetail({ alert, onClose }: Props) {
  const { alerts } = useApp()
  // карточка открыта долго, а данные обновляются — показываем свежую версию предупреждения
  const current = alert ? alerts.find((a) => a.id === alert.id) ?? alert : null
  return (
    <Modal open={!!current} onClose={onClose} title={current?.title ?? ''} wide>
      {current && <Body key={current.id} alert={current} onClose={onClose} />}
    </Modal>
  )
}

function Body({ alert, onClose }: { alert: Alert; onClose: () => void }) {
  const { role, rules, updateAlert, notify, bySite, byZone, byStage, byCamera, cameraOf } = useApp()
  const [comment, setComment] = useState('')
  const [saving, setSaving] = useState(false)
  const [watching, setWatching] = useState(false)
  const [zoomed, setZoomed] = useState(false)  // кадр-доказательство на всю вкладку
  const [prescribing, setPrescribing] = useState(false)
  const snaps = alert.evidenceSnapshots
  const [picked, setPicked] = useState<string | null>(null)
  const snap = snaps.find((s) => s.id === picked) ?? snaps[snaps.length - 1]
  const site = bySite(alert.siteId)
  const zone = byZone(alert.zoneId)
  const stage = byStage(alert.stageId)
  const rule = stage?.ruleKey ? rules[stage.ruleKey] : undefined
  const cam = snap ? cameraOf(snap) : undefined
  // живое видео той камеры, что дала доказательства (если она ещё есть и включена)
  const liveCamera = byCamera(alert.cameraId ?? snap?.cameraId ?? null)
  const eq = alert.equipment ? EQUIPMENT[alert.equipment] : undefined
  const highlight = alert.equipment && (alert.kind === 'unexpected' || alert.kind === 'idle') ? [alert.equipment] : undefined
  const sev = SEVERITY[alert.severity]
  const st = STATUS[alert.status]
  // по отклонению с предписанием решение принимает инспектор (и администратор — он может всё)
  const locked = alert.status === 'prescribed' && role?.id !== 'inspector' && role?.id !== 'admin'

  const act = async (status: AlertStatus, defaultText: string) => {
    setSaving(true)
    const ok = await updateAlert(alert.id, status, comment.trim() || defaultText)
    setSaving(false)
    if (ok) {
      notify(`Ответ по № ${alert.code} сохранён`)
      onClose()
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={sev.tone}>{sev.label}</Badge>
        <Badge tone={st.tone}>{st.label}</Badge>
        <Badge tone="neutral">{KIND[alert.kind]}</Badge>
        {alert.prescriptionNo && <Badge tone="info">Предписание № {alert.prescriptionNo}{alert.prescriptionDue && <>, срок до {fmtDate(alert.prescriptionDue)}</>}</Badge>}
        <span className="ml-auto text-[14px] text-muted-foreground font-mono">№ {alert.code} · {fmtDateShort(alert.startedAt)}</span>
      </div>

      <p className="text-[15px] text-muted-foreground">
        <b className="text-foreground font-semibold">{site?.name ?? 'Объект удалён'}</b> · {zone?.name ?? '—'}{stage && <> · этап «{stage.name}»</>}
      </p>

      {/* Доказательства */}
      {cam && snap && (
        <section>
          <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
            <h3 className="font-semibold">Кадры-доказательства</h3>
            {liveCamera?.enabled && (
              <Button variant="outline" size="sm" onClick={() => setWatching(true)}><Video className="w-4 h-4" /> Смотреть камеру сейчас</Button>
            )}
          </div>
          <button type="button" onClick={() => setZoomed(true)} aria-label="Развернуть кадр на всю вкладку" className="relative block w-full rounded-lg cursor-zoom-in group">
            <CameraFrame camera={cam} snapshot={snap} highlight={highlight} offline={alert.kind === 'camera_offline'} />
            <span className="absolute left-2 bottom-2 w-9 h-9 rounded-md bg-black/60 text-white flex items-center justify-center transition-colors group-hover:bg-black/80" aria-hidden>
              <Maximize2 className="w-4 h-4" />
            </span>
          </button>
          {snaps.length > 1 && (
            <div className="flex flex-wrap gap-2 mt-3">
              {snaps.map((s) => (
                <button
                  key={s.id} type="button" onClick={() => setPicked(s.id)}
                  className={cn(
                    'min-h-[44px] px-4 rounded-lg font-semibold border cursor-pointer transition-colors',
                    s.id === snap.id ? 'border-primary bg-info-bg text-info-fg' : 'border-border bg-card hover:border-primary/60',
                  )}
                >
                  {fmtWhen(s.takenAt)}
                  {eq && <span className="ml-2 text-muted-foreground font-normal">{eq.genitivePlural}: {countOf(s.detections.map((d) => d.type), eq.type)}</span>}
                </button>
              ))}
            </div>
          )}
        </section>
      )}

      <section>
        <div className="flex items-center gap-2 mb-1">
          <h3 className="font-semibold">Что случилось</h3>
          {alert.consequence && <InfoTip label="Чем это грозит"><b>Чем это грозит.</b> {alert.consequence}</InfoTip>}
        </div>
        <p className="text-[16px] leading-relaxed">{alert.summary}</p>
      </section>

      {/* Объяснение — ключевое требование ТЗ: явная и проверяемая связь. Свёрнуто, чтобы не шуметь, — раскрывается одним нажатием */}
      <Disclosure title="Почему система так решила">
        <ol className="space-y-3">
          <Step n={1} title="Смотрим в план">
{stage
              ? <>По календарному плану {fmtDate(alert.startedAt)} на объекте идёт этап <b>«{stage.name}»</b> ({fmtDate(stage.start)} — {fmtDate(stage.end)}).</>
              : <>Для объекта не найден этап календарного плана на {fmtDate(alert.startedAt)}.</>}
          </Step>
          {rule && alert.kind !== 'camera_offline' && alert.kind !== 'idle' && (
            <Step n={2} title="Берём правило для этапа">
              {alert.kind === 'unexpected' && eq ? (
                <>На этом этапе <b>{eq.name.toLowerCase()}</b> не нужен: {rule.unexpected.find((u) => u.type === eq.type)?.why.toLowerCase() ?? 'не входит в перечень техники этапа'}.</>
              ) : (
                <>Для этапа нужно: {rule.required.map((r, i) => (
                  <span key={r.type}>{i > 0 && ', '}<b>{EQUIPMENT[r.type].name.toLowerCase()} — не меньше {r.min}</b></span>
                ))}.</>
              )}
            </Step>
          )}
          {alert.kind === 'idle' && (
            <Step n={2} title="Берём правило">Если техника стоит на одном месте 2 часа и дольше (сравниваем её положение на кадрах), считаем это простоем.</Step>
          )}
          {alert.kind === 'camera_offline' && (
            <Step n={2} title="Берём правило">Если видео с камеры нет дольше 10 минут, зона считается «слепой».</Step>
          )}
          <Step n={3} title="Смотрим на кадры">
            {alert.kind === 'camera_offline'
              ? <>Последний кадр: {snap ? fmtWhen(snap.takenAt) : '—'}. С тех пор видео нет.</>
              : alert.kind === 'idle' && eq
                ? <>{eq.name} стоит в одном и том же месте на всех кадрах {snaps.length > 1 ? `с ${fmtWhen(snaps[0].takenAt)} по ${fmtWhen(snaps[snaps.length - 1].takenAt)}` : fmtWhen(snaps[0]?.takenAt ?? alert.startedAt)}.</>
                : eq && (
                  <>На {plural(snaps.length, 'кадре', 'кадрах', 'кадрах')} ({snaps.map((s) => fmtWhen(s.takenAt)).join('; ')}) видим: <b>{eq.genitivePlural} — {alert.observed ?? 0}</b>{alert.expected != null && alert.kind !== 'unexpected' && <>, а нужно не меньше {alert.expected}</>}.</>
                )}
          </Step>
          <Step n={4} title="Вывод">
            <span className="font-semibold">{alert.title}.</span> {confidenceNote(alert, snaps)}
          </Step>
        </ol>
      </Disclosure>

      {alert.advice && isOpen(alert.status) && (
        <p className="rounded-xl bg-info-bg text-info-fg px-4 py-3 text-[16px] leading-relaxed">
          <b>Что делать:</b> {adviceFor(alert, role?.id, locked)}
        </p>
      )}

      {/* Действия по роли */}
      {isOpen(alert.status) && role && locked && (
        <p className="border-t border-border pt-5 text-muted-foreground">
          По этому отклонению выдано предписание{alert.prescriptionNo ? ` № ${alert.prescriptionNo}` : ''}. Закрыть его может инспектор.
        </p>
      )}
      {isOpen(alert.status) && role && !locked && (
        <section className="border-t border-border pt-5">
          <h3 className="font-semibold mb-2">Ваш ответ</h3>
          <textarea
            value={comment} onChange={(e) => setComment(e.target.value)} maxLength={1000} aria-label="Комментарий к ответу"
            placeholder={`Комментарий (необязательно), например: «${role.id === 'foreman' ? 'самосвалы будут к 14:00' : role.id === 'inspector' ? 'проверено на месте, нарушение подтверждается' : 'подрядчик обещал технику к вечеру'}»`}
            className="w-full min-h-[80px] rounded-lg border border-input bg-card p-3 text-[16px] outline-none transition-shadow focus:border-primary focus:ring-4 focus:ring-primary/15"
          />
          <fieldset disabled={saving} className="flex flex-wrap gap-3 mt-3 disabled:opacity-60">
            {role.id === 'foreman' && (
              <>
                {alert.status === 'new' && alert.kind !== 'camera_offline' && (
                  <Button size="lg" onClick={() => act('acknowledged', 'Техника уже едет, проблема будет решена.')}><Truck className="w-5 h-5" /> Техника едет</Button>
                )}
                {alert.status === 'new' && (
                  <Button size="lg" variant="outline" onClick={() => act('confirmed', 'Подтверждаю: проблема есть, разбираемся.')}><Wrench className="w-5 h-5" /> Подтверждаю проблему</Button>
                )}
                {alert.status !== 'new' && (
                  <Button size="lg" variant="success" onClick={() => act('resolved', 'Проблема устранена.')}><CheckCircle2 className="w-5 h-5" /> Устранено</Button>
                )}
                <Button size="lg" variant="ghost" onClick={() => act('false_positive', 'Система ошиблась, на месте всё в порядке.')}><XCircle className="w-5 h-5" /> Это ошибка</Button>
              </>
            )}
            {role.id === 'manager' && (
              <>
                {alert.status === 'new' && <Button size="lg" onClick={() => act('confirmed', 'Проблема подтверждена руководителем проекта.')}><ClipboardCheck className="w-5 h-5" /> Подтвердить</Button>}
                <Button size="lg" variant="success" onClick={() => act('resolved', 'Проблема устранена.')}><CheckCircle2 className="w-5 h-5" /> Устранено</Button>
                <Button size="lg" variant="ghost" onClick={() => act('false_positive', 'Ложное срабатывание.')}><XCircle className="w-5 h-5" /> Ошибка системы</Button>
              </>
            )}
            {role.id === 'inspector' && (
              <>
                {alert.status !== 'prescribed' && <Button size="lg" variant="danger" onClick={() => setPrescribing(true)}><FileWarning className="w-5 h-5" /> Выдать предписание…</Button>}
                <Button size="lg" variant="success" onClick={() => act('resolved', 'Нарушение устранено, закрыто инспектором.')}><CheckCircle2 className="w-5 h-5" /> Закрыть</Button>
                <Button size="lg" variant="ghost" onClick={() => act('false_positive', 'Ложное срабатывание.')}><XCircle className="w-5 h-5" /> Ошибка системы</Button>
              </>
            )}
            {role.id === 'admin' && (
              <>
                {alert.status === 'new' && <Button size="lg" onClick={() => act('confirmed', 'Проблема подтверждена администратором.')}><ClipboardCheck className="w-5 h-5" /> Подтвердить</Button>}
                {alert.status === 'new' && alert.kind !== 'camera_offline' && (
                  <Button size="lg" variant="outline" onClick={() => act('acknowledged', 'Техника уже едет, проблема будет решена.')}><Truck className="w-5 h-5" /> Техника едет</Button>
                )}
                {alert.status !== 'prescribed' && <Button size="lg" variant="danger" onClick={() => setPrescribing(true)}><FileWarning className="w-5 h-5" /> Выдать предписание…</Button>}
                <Button size="lg" variant="success" onClick={() => act('resolved', 'Проблема устранена, закрыто администратором.')}><CheckCircle2 className="w-5 h-5" /> {alert.status === 'prescribed' ? 'Закрыть' : 'Устранено'}</Button>
                <Button size="lg" variant="ghost" onClick={() => act('false_positive', 'Ложное срабатывание.')}><XCircle className="w-5 h-5" /> Ошибка системы</Button>
              </>
            )}
          </fieldset>
        </section>
      )}

      <Disclosure title={<>История <span className="font-normal text-muted-foreground">· {alert.history.length + 1}</span></>}>
        <ul className="space-y-2">
          {[{ at: alert.startedAt, who: 'Система', text: 'Отклонение впервые замечено.' }, ...alert.history].map((h, i) => (
            <li key={i} className="flex gap-3 text-[15px]">
              <span className="text-muted-foreground tabular shrink-0 w-[92px]">{fmtWhen(h.at)}</span>
              <span><b>{h.who}:</b> {h.text}</span>
            </li>
          ))}
        </ul>
      </Disclosure>
      <PrescribeDialog alert={prescribing ? alert : null} onClose={() => setPrescribing(false)} onDone={() => { setPrescribing(false); onClose() }} />
      <LiveCameraViewer camera={watching && liveCamera ? liveCamera : null} onClose={() => setWatching(false)} />
      <FrameViewer
        snapshots={snaps} index={zoomed && snap ? snaps.indexOf(snap) : null} onIndex={(i) => setPicked(snaps[i].id)}
        onClose={() => setZoomed(false)} highlight={highlight} offline={alert.kind === 'camera_offline'}
      />
    </div>
  )
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="shrink-0 w-6 font-semibold text-muted-foreground tabular">{n}.</span>
      <div>
        <div className="font-semibold">{title}</div>
        <div className="text-[15px] leading-relaxed">{children}</div>
      </div>
    </li>
  )
}

function countOf(types: string[], t: string) {
  return types.filter((x) => x === t).length
}
