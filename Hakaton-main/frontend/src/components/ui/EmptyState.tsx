import { CheckCircle2 } from 'lucide-react'

export function EmptyState({ title, text }: { title: string; text?: string }) {
  return (
    <div className="py-8 px-5 flex items-start gap-3">
      <CheckCircle2 className="w-6 h-6 text-ok shrink-0 mt-0.5" />
      <div>
        <p className="font-semibold text-[17px]">{title}</p>
        {text && <p className="text-muted-foreground">{text}</p>}
      </div>
    </div>
  )
}
