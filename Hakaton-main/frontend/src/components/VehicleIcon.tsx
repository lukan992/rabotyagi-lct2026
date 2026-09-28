import type { EquipmentType } from '@/data'

/**
 * Упрощённые силуэты техники для мок-кадров и легенды.
 * Все иконки — в viewBox 0 0 100 60, рисуются в цвете `fill`.
 */
export function VehicleIcon({ type, className, fill = 'currentColor' }: { type: EquipmentType; className?: string; fill?: string }) {
  const common = { className, viewBox: '0 0 100 60', fill, xmlns: 'http://www.w3.org/2000/svg', 'aria-hidden': true as const }
  switch (type) {
    case 'excavator':
      return (
        <svg {...common}>
          <rect x="18" y="44" width="46" height="10" rx="5" />
          <rect x="24" y="28" width="30" height="16" rx="3" />
          <rect x="40" y="18" width="14" height="12" rx="2" />
          <path d="M54 26 L78 10 L82 14 L62 30 Z" />
          <path d="M78 12 L92 30 L96 28 L84 8 Z" />
          <path d="M88 30 L98 30 L96 40 L86 40 Z" />
        </svg>
      )
    case 'dump_truck':
      return (
        <svg {...common}>
          <path d="M30 20 L92 16 L88 40 L30 40 Z" />
          <rect x="8" y="26" width="24" height="16" rx="3" />
          <rect x="12" y="20" width="16" height="8" rx="2" />
          <circle cx="18" cy="46" r="7" />
          <circle cx="56" cy="46" r="7" />
          <circle cx="76" cy="46" r="7" />
        </svg>
      )
    case 'roller':
      return (
        <svg {...common}>
          <rect x="36" y="18" width="34" height="22" rx="4" />
          <rect x="44" y="10" width="14" height="10" rx="2" />
          <circle cx="22" cy="40" r="14" />
          <circle cx="22" cy="40" r="6" fill="#fff" opacity=".6" />
          <circle cx="78" cy="44" r="9" />
        </svg>
      )
    case 'manipulator':
      return (
        <svg {...common}>
          <rect x="30" y="30" width="60" height="12" rx="2" />
          <rect x="6" y="24" width="24" height="18" rx="3" />
          <path d="M40 30 L44 12 L50 12 L48 30 Z" />
          <path d="M44 12 L70 6 L72 11 L46 17 Z" />
          <circle cx="18" cy="46" r="7" />
          <circle cx="50" cy="46" r="7" />
          <circle cx="76" cy="46" r="7" />
        </svg>
      )
    case 'mixer':
      return (
        <svg {...common}>
          <rect x="6" y="26" width="24" height="16" rx="3" />
          <rect x="10" y="20" width="16" height="8" rx="2" />
          <ellipse cx="60" cy="26" rx="28" ry="15" transform="rotate(-12 60 26)" />
          <rect x="30" y="36" width="60" height="8" rx="2" />
          <circle cx="18" cy="46" r="7" />
          <circle cx="52" cy="46" r="7" />
          <circle cx="76" cy="46" r="7" />
        </svg>
      )
    case 'bulldozer':
      return (
        <svg {...common}>
          <rect x="24" y="42" width="48" height="12" rx="6" />
          <rect x="30" y="24" width="36" height="18" rx="3" />
          <rect x="40" y="14" width="18" height="12" rx="2" />
          <path d="M66 34 L86 34 L86 30 L92 30 L92 54 L84 54 L84 42 L66 42 Z" />
        </svg>
      )
    case 'truck':
      return (
        <svg {...common}>
          <rect x="30" y="14" width="62" height="28" rx="2" />
          <rect x="6" y="26" width="24" height="16" rx="3" />
          <rect x="10" y="20" width="16" height="8" rx="2" />
          <circle cx="18" cy="46" r="7" />
          <circle cx="50" cy="46" r="7" />
          <circle cx="78" cy="46" r="7" />
        </svg>
      )
    case 'crane':
      return (
        <svg {...common}>
          <rect x="20" y="34" width="70" height="10" rx="2" />
          <rect x="4" y="28" width="20" height="16" rx="3" />
          <rect x="30" y="24" width="20" height="12" rx="2" />
          <path d="M34 26 L90 2 L94 6 L40 32 Z" />
          <path d="M90 4 L90 22 L88 22 L88 8 Z" />
          <circle cx="14" cy="48" r="7" />
          <circle cx="44" cy="48" r="7" />
          <circle cx="70" cy="48" r="7" />
        </svg>
      )
  }
}
