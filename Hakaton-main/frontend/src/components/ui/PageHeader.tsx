import type { ReactNode } from 'react'
import { InfoTip } from './InfoTip'

/** Заголовок экрана. Пояснение к экрану — за значком «?» рядом с заголовком, подзаголовок — только короткие данные (дата, период). */
export function PageHeader({ title, subtitle, info, action }: { title: string; subtitle?: string; info?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 mb-6">
      <div>
        <div className="flex items-center gap-2">
          <h1 className="text-2xl sm:text-[28px] font-semibold leading-tight">{title}</h1>
          {info && <InfoTip label={`Что на экране «${title}»`}>{info}</InfoTip>}
        </div>
        {subtitle && <p className="text-muted-foreground mt-1">{subtitle}</p>}
      </div>
      {action}
    </div>
  )
}
