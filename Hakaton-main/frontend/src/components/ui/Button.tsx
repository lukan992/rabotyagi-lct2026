import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

type Variant = 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger' | 'success'
type Size = 'md' | 'lg' | 'sm'

const variants: Record<Variant, string> = {
  primary: 'bg-primary text-on-primary hover:bg-primary-hover shadow-[var(--shadow-card)]',
  secondary: 'bg-muted text-foreground hover:bg-border',
  outline: 'bg-card text-foreground border border-border-strong hover:bg-muted',
  ghost: 'bg-transparent text-muted-foreground hover:bg-muted hover:text-foreground',
  danger: 'bg-danger-solid text-white hover:bg-danger-solid/90',
  success: 'bg-ok-solid text-white hover:bg-ok-solid/90',
}
const sizes: Record<Size, string> = {
  sm: 'min-h-[44px] px-3 text-[14px] gap-1.5',
  md: 'min-h-[44px] px-4 text-[15px] gap-2',
  lg: 'min-h-[50px] px-5 text-[16px] gap-2',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  full?: boolean
}

/** Кнопка: высота не меньше 44px, плавный hover */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'primary', size = 'md', full, type = 'button', ...props }, ref) => (
    <button
      ref={ref}
      type={type}
      className={cn(
        'inline-flex items-center justify-center rounded-lg font-semibold cursor-pointer select-none',
        'transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed',
        variants[variant], sizes[size], full && 'w-full', className,
      )}
      {...props}
    />
  ),
)
Button.displayName = 'Button'
